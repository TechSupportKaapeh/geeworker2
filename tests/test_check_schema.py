"""Tests del verificador del contrato con el esquema de Geocore (M.3.4).

Lo que se prueba es la comparación, no la conexión: `revisar_*` recibe lo que
devolvió la base, así que la suite no necesita Postgres y el CI la corre igual
(`CI.md`). Que el contrato coincida con la base real es la otra mitad, y esa se
verifica corriendo `python check_schema.py` contra una base con las migraciones
aplicadas.

El caso que más importa es el que le pasó a este repo: una columna **existe** pero
con otra forma —`valor` que vuelve a ser NOT NULL— y el INSERT falla recién en
producción, adentro de un step, con el cálculo de GEE ya gastado.
"""

import check_schema
from check_schema import (
    CONTRATO,
    DISTINTO,
    FALTA,
    OK,
    revisar_columnas,
    revisar_constraints,
    revisar_indices_parciales,
)


def _fila(tabla, columna):
    """La fila de `information_schema.columns` que el contrato espera para esa columna."""
    for esperada in CONTRATO[tabla]:
        if esperada.nombre == columna:
            nulo = "YES" if esperada.nullable in (True, None) else "NO"
            return (tabla, esperada.nombre, esperada.tipo, nulo)
    raise AssertionError(f"{tabla}.{columna} no está en el contrato")


def esquema_completo():
    """Una base que cumple el contrato entero."""
    return [_fila(tabla, columna.nombre)
            for tabla, columnas in CONTRATO.items()
            for columna in columnas]


def constraints_completas():
    return [(tabla, tipo, f"pk_{tabla}", columnas)
            for tabla, tipo, columnas in check_schema.CONSTRAINTS]


def indices_completos():
    return [
        ("processing_jobs", "ux_processing_jobs_tipo_periodo_parcela",
         "CREATE UNIQUE INDEX ux_processing_jobs_tipo_periodo_parcela ON geodata.processing_jobs "
         "USING btree (request_type, periodo, parcela_id) "
         "WHERE ((periodo IS NOT NULL) AND (parcela_id IS NOT NULL))"),
        ("processing_jobs", "ux_processing_jobs_tipo_periodo_rancho",
         "CREATE UNIQUE INDEX ux_processing_jobs_tipo_periodo_rancho ON geodata.processing_jobs "
         "USING btree (request_type, periodo, rancho_id) "
         "WHERE ((periodo IS NOT NULL) AND (rancho_id IS NOT NULL))"),
    ]


# ---------------------------------------------------------------------------
# Columnas
# ---------------------------------------------------------------------------

def test_un_esquema_completo_pasa_entero():
    hallazgos = revisar_columnas(esquema_completo())

    assert hallazgos, "el contrato no puede estar vacío"
    assert all(h.paso for h in hallazgos)


def test_el_contrato_cubre_las_columnas_de_la_fase_m():
    # Control de que el contrato no se vació: son las que trajo `MedicionesMensuales`.
    nombres = {(tabla, c.nombre) for tabla, columnas in CONTRATO.items() for c in columnas}

    assert ("measurements", "estadisticas") in nombres
    assert ("measurements", "cobertura") in nombres
    assert ("measurements", "observaciones") in nombres
    assert ("measurements", "receta") in nombres
    assert ("layers", "estadisticas") in nombres
    assert ("layers", "receta") in nombres
    assert ("processing_jobs", "periodo") in nombres


def test_una_columna_que_falta_se_nombra():
    filas = [f for f in esquema_completo() if f[:2] != ("measurements", "estadisticas")]

    hallazgos = revisar_columnas(filas)

    fallaron = [h for h in hallazgos if not h.paso]
    assert [h.estado for h in fallaron] == [FALTA]
    assert fallaron[0].que == "measurements.estadisticas"


def test_una_columna_con_otro_tipo_no_pasa():
    # `estadisticas` como text en vez de jsonb: el INSERT no falla, pero Geocore
    # devolvería un string y el CHECK de la base no existiría.
    filas = [("measurements", "estadisticas", "text", "YES") if f[:2] == ("measurements", "estadisticas") else f
             for f in esquema_completo()]

    hallazgos = revisar_columnas(filas)

    fallo = next(h for h in hallazgos if h.que == "measurements.estadisticas")
    assert fallo.estado == DISTINTO
    assert "jsonb" in fallo.detalle and "text" in fallo.detalle


def test_valor_que_vuelve_a_ser_not_null_no_pasa():
    # Es el caso que rompe la FASE M: sin `valor` nullable, un mes con cobertura bajo
    # el mínimo no se puede escribir y el mes queda como "nunca procesado".
    filas = [("measurements", "valor", "double precision", "NO") if f[:2] == ("measurements", "valor") else f
             for f in esquema_completo()]

    hallazgos = revisar_columnas(filas)

    fallo = next(h for h in hallazgos if h.que == "measurements.valor")
    assert fallo.estado == DISTINTO
    assert "nullable" in fallo.detalle


def test_una_base_vacia_falla_entera():
    hallazgos = revisar_columnas([])

    assert all(h.estado == FALTA for h in hallazgos)


def test_una_columna_de_mas_no_molesta():
    # El contrato es lo que el worker usa, no el esquema entero: una columna nueva de
    # Geocore no puede poner en rojo al worker.
    filas = [*esquema_completo(), ("measurements", "columna_nueva_de_geocore", "text", "YES")]

    assert all(h.paso for h in revisar_columnas(filas))


# ---------------------------------------------------------------------------
# Constraints e índices
# ---------------------------------------------------------------------------

def test_las_constraints_completas_pasan():
    assert all(h.paso for h in revisar_constraints(constraints_completas()))


def test_una_pk_con_otras_columnas_no_pasa():
    # La PK de `measurements` sin `fecha`: el ON CONFLICT (parcela_id, indice, fecha)
    # falla con "no unique or exclusion constraint matching".
    filas = [(t, tipo, nombre, "parcela_id, indice" if t == "measurements" else columnas)
             for t, tipo, nombre, columnas in constraints_completas()]

    fallaron = [h for h in revisar_constraints(filas) if not h.paso]

    assert len(fallaron) == 1
    assert "measurements" in fallaron[0].que


def test_los_dos_indices_parciales_se_reconocen_por_sus_columnas():
    hallazgos = revisar_indices_parciales(indices_completos())

    assert len(hallazgos) == 2
    assert all(h.paso for h in hallazgos)


def test_un_indice_unico_sin_filtro_no_cuenta():
    # Sin el WHERE, Postgres cuenta los NULL como distintos y dos jobs del mismo
    # rancho y mes no chocarían: es el bug que los dos índices parciales evitan.
    sin_filtro = [(t, nombre, definicion.split(" WHERE ")[0])
                  for t, nombre, definicion in indices_completos()]

    assert all(h.estado == FALTA for h in revisar_indices_parciales(sin_filtro))


def test_un_indice_que_no_es_unico_no_cuenta():
    solo_btree = [(t, nombre, definicion.replace("UNIQUE INDEX", "INDEX"))
                  for t, nombre, definicion in indices_completos()]

    assert all(h.estado == FALTA for h in revisar_indices_parciales(solo_btree))


def test_el_nombre_del_indice_no_es_contrato():
    renombrados = [(t, "otro_nombre", definicion) for t, _nombre, definicion in indices_completos()]

    assert all(h.paso for h in revisar_indices_parciales(renombrados))


# ---------------------------------------------------------------------------
# La salida
# ---------------------------------------------------------------------------

def test_el_hallazgo_se_lee():
    hallazgos = revisar_columnas([])

    linea = str(hallazgos[0])
    assert FALTA in linea and hallazgos[0].que in linea


def test_ok_es_el_unico_estado_que_pasa():
    assert check_schema.Hallazgo(OK, "x").paso
    assert not check_schema.Hallazgo(FALTA, "x").paso
    assert not check_schema.Hallazgo(DISTINTO, "x").paso
