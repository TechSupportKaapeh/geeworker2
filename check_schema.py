"""Verifica el esquema `geodata` que administra Geocore (EF Core) contra el contrato.

Los nombres de columna de `geodata` son contrato entre repos: Geocore es dueño del
esquema (`DECISIONS #15` de Geocore), pero el worker escribe `layers` y
`measurements` con SQL a mano. No hay compilador que agarre un desajuste, y el modo
de fallo es feo: el INSERT revienta en producción, dentro de un step de Inngest,
cuando ya se gastó el cálculo en GEE.

Desde la FASE M el esquema tiene columnas nuevas (migración `MedicionesMensuales`,
`DECISIONS #25` de Geocore), así que esto dejó de imprimir el esquema y pasó a
**compararlo**: cada columna que el worker usa se busca por nombre, tipo y, donde
importa, nulabilidad.

Uso:
    python check_schema.py            # verifica y sale 1 si falta algo
    python check_schema.py --dump     # además imprime el esquema entero

La conexión sale del `.env` (`DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`,
`DB_PASSWORD`). Contra un Postgres local se prueba igual: lo que se verifica es el
esquema, no de quién es la base.
"""
from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# El contrato
# ---------------------------------------------------------------------------

#: Nulabilidad que no se verifica: la columna existe y el tipo importa, pero que
#: acepte nulos o no es cosa de Geocore.
DA_IGUAL = None


@dataclass(frozen=True)
class Columna:
    """Una columna que el worker usa, con el tipo que `information_schema` reporta."""

    nombre: str
    tipo: str
    nullable: bool | None = DA_IGUAL
    #: Por qué le importa al worker. Sale impreso cuando falta.
    porque: str = ""


#: Lo que el worker escribe o lee de cada tabla. No es el esquema entero: una columna
#: que el worker no toca no es contrato, y ponerla acá haría fallar esta verificación
#: por un cambio que no lo afecta.
CONTRATO: dict[str, tuple[Columna, ...]] = {
    "measurements": (
        Columna("parcela_id", "uuid", False),
        Columna("indice", "text", False),
        Columna("fecha", "timestamp with time zone", False,
                "el ON CONFLICT va sobre (parcela_id, indice, fecha)"),
        Columna("tenant_id", "uuid", False),
        # La nulabilidad de `valor` es la que cambió en la FASE M y la que rompe si
        # se revierte: un mes con cobertura bajo el mínimo se escribe con valor nulo
        # para que el front dibuje el hueco y el cierre de mes sepa que ya corrió.
        Columna("valor", "double precision", True,
                "un mes sin dato se escribe con valor nulo"),
        Columna("min_val", "double precision", True, "filas por pasada, ya no se escriben"),
        Columna("max_val", "double precision", True, "filas por pasada, ya no se escriben"),
        # FASE M (M.3.1). Las cuatro nullable: las filas por pasada no las tienen.
        Columna("estadisticas", "jsonb", True, "las 7 estadisticas del mes"),
        Columna("cobertura", "double precision", True, "fraccion de la parcela con dato"),
        Columna("observaciones", "double precision", True,
                "mediana de n_obs; double porque puede caer entre dos enteros"),
        Columna("receta", "text", True, "que receta produjo la fila"),
    ),
    "layers": (
        Columna("id", "uuid", False, "UUIDv5 determinista: el ON CONFLICT va sobre la PK"),
        Columna("product", "text", False),
        Columna("storage_key", "text", False, "la key pelada, sin el bucket"),
        Columna("acquired_ts", "timestamp with time zone", False),
        Columna("created_at", "timestamp with time zone", False),
        Columna("bbox", "USER-DEFINED", DA_IGUAL, "geometry(Polygon,4326)"),
        Columna("tenant_id", "uuid", False),
        Columna("parcela_id", "uuid", True, "una capa de rancho la escribe en nulo"),
        Columna("rancho_id", "uuid", True, "una capa de parcela la escribe en nulo"),
        Columna("source", "text", False),
        # FASE M (M.3.1).
        Columna("receta", "text", True),
        Columna("estadisticas", "jsonb", True),
    ),
    "processing_jobs": (
        Columna("id", "uuid", False),
        Columna("request_type", "text", False),
        Columna("status", "text", False),
        Columna("progress", "integer", False),
        Columna("error_message", "text", True),
        Columna("started_at", "timestamp with time zone", True),
        Columna("finished_at", "timestamp with time zone", True),
        # FASE M (M.3.1): la crea Geocore, pero el worker la ve en los jobs mensuales.
        Columna("periodo", "text", True, "AAAA-MM del job mensual"),
    ),
    "processing_job_events": (
        Columna("job_id", "uuid", False),
        Columna("created_at", "timestamp with time zone", False),
        Columna("attempt", "integer", False),
        Columna("stage", "text", False),
        Columna("level", "text", False),
        Columna("message", "text", False),
        Columna("detail", "jsonb", True, "el JSON de la bitacora"),
    ),
}

#: Las constraints que los `ON CONFLICT` del worker necesitan. Sin una constraint que
#: coincida **exactamente** con esas columnas, el INSERT falla con
#: "no unique or exclusion constraint matching the ON CONFLICT specification".
CONSTRAINTS: tuple[tuple[str, str, str], ...] = (
    ("measurements", "PRIMARY KEY", "parcela_id, indice, fecha"),
    ("layers", "PRIMARY KEY", "id"),
    ("processing_jobs", "PRIMARY KEY", "id"),
)

#: Los dos índices únicos parciales del cierre de mes (`DECISIONS #25` de Geocore).
#: Son dos y no uno porque Postgres cuenta los NULL como distintos: con un solo
#: índice, dos jobs del mismo rancho y mes no chocarían. Se buscan por su definición y
#: no por su nombre: el nombre lo elige la migración, las columnas son el contrato.
INDICES_PARCIALES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("processing_jobs", ("request_type", "periodo", "parcela_id")),
    ("processing_jobs", ("request_type", "periodo", "rancho_id")),
)

OK = "ok"
FALTA = "FALTA"
DISTINTO = "DISTINTO"


@dataclass(frozen=True)
class Hallazgo:
    """El resultado de verificar una cosa. `estado` es OK, FALTA o DISTINTO."""

    estado: str
    que: str
    detalle: str = ""

    @property
    def paso(self) -> bool:
        return self.estado == OK

    def __str__(self) -> str:
        detalle = f"  - {self.detalle}" if self.detalle else ""
        return f"  {self.estado:<8} {self.que}{detalle}"


# ---------------------------------------------------------------------------
# Las verificaciones, puras: reciben lo que devolvió la base, no la conexión
# ---------------------------------------------------------------------------

def revisar_columnas(filas: list[tuple[str, str, str, str]]) -> list[Hallazgo]:
    """Compara el contrato contra las filas de `information_schema.columns`.

    `filas` son `(tabla, columna, data_type, is_nullable)` tal cual las devuelve la
    consulta: `is_nullable` es el texto ``'YES'`` o ``'NO'``.
    """
    reales = {(t, c): (tipo, nulo == "YES") for t, c, tipo, nulo in filas}
    hallazgos: list[Hallazgo] = []

    for tabla, columnas in CONTRATO.items():
        for columna in columnas:
            que = f"{tabla}.{columna.nombre}"
            real = reales.get((tabla, columna.nombre))

            if real is None:
                hallazgos.append(Hallazgo(FALTA, que, columna.porque or "no esta en la base"))
                continue

            tipo, nullable = real
            if tipo != columna.tipo:
                hallazgos.append(Hallazgo(
                    DISTINTO, que, f"se esperaba {columna.tipo} y es {tipo}"))
                continue

            if columna.nullable is not DA_IGUAL and nullable != columna.nullable:
                esperado = "nullable" if columna.nullable else "NOT NULL"
                encontrado = "nullable" if nullable else "NOT NULL"
                hallazgos.append(Hallazgo(
                    DISTINTO, que,
                    f"se esperaba {esperado} y es {encontrado}"
                    + (f" ({columna.porque})" if columna.porque else "")))
                continue

            hallazgos.append(Hallazgo(OK, que))

    return hallazgos


def revisar_constraints(filas: list[tuple[str, str, str, str]]) -> list[Hallazgo]:
    """Compara `CONSTRAINTS` contra `(tabla, tipo, nombre, columnas)` de la base."""
    reales = {(tabla, tipo, columnas) for tabla, tipo, _nombre, columnas in filas}

    return [
        Hallazgo(OK, f"{tabla} {tipo} ({columnas})")
        if (tabla, tipo, columnas) in reales
        else Hallazgo(FALTA, f"{tabla} {tipo} ({columnas})", "lo necesita un ON CONFLICT")
        for tabla, tipo, columnas in CONSTRAINTS
    ]


def revisar_indices_parciales(filas: list[tuple[str, str, str]]) -> list[Hallazgo]:
    """Busca los índices únicos parciales en `(tabla, nombre, definicion)` de `pg_indexes`.

    Se comparan las columnas y el `WHERE`, no el nombre: renombrar un índice no rompe
    nada, cambiarle las columnas sí.
    """
    hallazgos: list[Hallazgo] = []

    for tabla, columnas in INDICES_PARCIALES:
        que = f"{tabla} UNIQUE parcial ({', '.join(columnas)})"
        encontrado = any(
            t == tabla
            and "UNIQUE" in definicion.upper()
            and " WHERE " in definicion.upper()
            and all(c in definicion for c in columnas)
            for t, _nombre, definicion in filas
        )
        hallazgos.append(
            Hallazgo(OK, que) if encontrado
            else Hallazgo(FALTA, que, "es lo que impide dos jobs del mismo mes"))

    return hallazgos


# ---------------------------------------------------------------------------
# El borde: la conexión y la salida
# ---------------------------------------------------------------------------

SQL_COLUMNAS = """
    SELECT table_name, column_name, data_type, is_nullable
    FROM information_schema.columns
    WHERE table_schema = 'geodata'
    ORDER BY table_name, ordinal_position;
"""

SQL_CONSTRAINTS = """
    SELECT tc.table_name, tc.constraint_type, tc.constraint_name,
           string_agg(kcu.column_name, ', ' ORDER BY kcu.ordinal_position)
    FROM information_schema.table_constraints tc
    JOIN information_schema.key_column_usage kcu
      ON tc.constraint_name = kcu.constraint_name
     AND tc.table_schema = kcu.table_schema
    WHERE tc.table_schema = 'geodata'
      AND tc.constraint_type IN ('PRIMARY KEY', 'UNIQUE')
    GROUP BY tc.table_name, tc.constraint_type, tc.constraint_name
    ORDER BY tc.table_name, tc.constraint_type;
"""

SQL_INDICES = """
    SELECT tablename, indexname, indexdef
    FROM pg_indexes
    WHERE schemaname = 'geodata'
    ORDER BY tablename, indexname;
"""


def imprimir_esquema(columnas: list[tuple[str, str, str, str]]) -> None:
    """El listado de antes, ahora bajo `--dump`: sirve para mirar lo que no es contrato."""
    print("\n=== ESQUEMA COMPLETO ===")
    if not columnas:
        print("(el esquema 'geodata' no existe o esta vacio)")
        return

    tabla_actual = None
    for tabla, columna, tipo, nullable in columnas:
        if tabla != tabla_actual:
            print(f"\n[{tabla}]")
            tabla_actual = tabla
        marca = "" if nullable == "NO" else " NULL"
        print(f"  {columna:<24} {tipo}{marca}")


def informe(titulo: str, hallazgos: list[Hallazgo]) -> None:
    print(f"\n=== {titulo} ===")
    for hallazgo in hallazgos:
        print(hallazgo)


def main() -> int:
    # Descripcion aparte del docstring, y sin acentos: argparse la imprime en la
    # consola, y la de Windows no siempre esta en UTF-8.
    parser = argparse.ArgumentParser(
        description="Verifica el esquema geodata contra el contrato del worker.")
    parser.add_argument("--dump", action="store_true",
                        help="imprime ademas el esquema entero, como la version vieja")
    args = parser.parse_args()

    import psycopg2
    from dotenv import load_dotenv

    load_dotenv()

    conn = None
    try:
        conn = psycopg2.connect(
            host=os.getenv("DB_HOST", "localhost"),
            port=os.getenv("DB_PORT", "5432"),
            dbname=os.getenv("DB_NAME", "terra"),
            user=os.getenv("DB_USER", "postgres"),
            password=os.getenv("DB_PASSWORD", "postgres"),
            connect_timeout=10,
        )
        cur = conn.cursor()

        cur.execute(SQL_COLUMNAS)
        columnas = cur.fetchall()
        cur.execute(SQL_CONSTRAINTS)
        constraints = cur.fetchall()
        cur.execute(SQL_INDICES)
        indices = cur.fetchall()
    except Exception as e:  # noqa: BLE001 — sin conexión no hay nada que verificar
        print("Error al conectar o consultar:", e)
        return 2
    finally:
        if conn:
            conn.close()

    de_columnas = revisar_columnas(columnas)
    de_constraints = revisar_constraints(constraints)
    de_indices = revisar_indices_parciales(indices)
    hallazgos = de_columnas + de_constraints + de_indices

    informe("COLUMNAS DEL CONTRATO", de_columnas)
    informe("CONSTRAINTS QUE NECESITAN LOS ON CONFLICT", de_constraints)
    informe("INDICES UNICOS PARCIALES (cierre de mes)", de_indices)

    if args.dump:
        imprimir_esquema(columnas)

    fallaron = [h for h in hallazgos if not h.paso]
    print(f"\n{len(hallazgos) - len(fallaron)} de {len(hallazgos)} verificaciones en ok.")

    if fallaron:
        print("\nFalta aplicar una migracion de Geocore, o el contrato cambio sin avisar:")
        for hallazgo in fallaron:
            print(hallazgo)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
