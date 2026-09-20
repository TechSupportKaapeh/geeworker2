"""Invariantes del registro de funciones y del contrato de coordenadas.

Se llamaba `test_inngest_handlers.py`, por el modulo que M.6.2b borro. Lo que
probaba del estado del job esta en `test_avance_job.py` y
`test_handlers_seguimiento.py`; lo de las claves de una capa, en
`test_pipeline_claves.py`.

Hasta el 2026-09-04 **ningun test tocaba un handler**. Estos no prueban el
procesamiento —para eso hace falta GEE, MinIO y la DB, y esa verificacion es
A-3— sino las tres cosas que los handlers hacen por su cuenta y que se rompen
en silencio:

  1. Los handlers son **sincronicos** (E.5). Declararlos `async def` mientras
     hacen I/O bloqueante congela el event loop entero.
  2. `coords_to_geometry` traduce el payload que manda Geocore en C#. Es un
     contrato entre repos que nadie verifico (`TEAM.md` no existe).
  3. `_with_job_tracking` decide el estado que ve el usuario en
     `processing_jobs`.

Sobre el punto 2: la primera version de estos tests llamaba a
`coords_to_geometry` y a `.getInfo()`, suponiendo que una `ee.Geometry` se
construye del lado del cliente. **Es falso**, y el test lo agarro:
`ee.Geometry.Polygon` levanta `EEException: client library not initialized`,
porque las clases de geometria se generan a partir del catalogo de algoritmos
que GEE publica. La respuesta no fue mockear GEE sino separar las dos
responsabilidades: `normalizar_coordenadas` es la traduccion del payload —el
contrato con Geocore, pura— y `coords_to_geometry` es la unica parte que habla
con GEE.
"""
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from handlers import registro as handlers
from handlers.geometria import coords_to_geometry, normalizar_coordenadas  # noqa: F401
from handlers.seguimiento import RETRIES

# --- 1. Los handlers no son corrutinas (E.5) ------------------------------


def test_ningun_handler_es_corrutina():
    """`async def` con trabajo bloqueante adentro congela uvicorn entero.

    Los handlers llaman a `requests`, `rasterio`, `psycopg2` y `.getInfo()`,
    que son sincronicos. Declarados `async def`, una descarga de 30 s no cede
    el control: bloquea el event loop y con el **todas** las demas funciones,
    incluido `/health`. En inngest-py 0.4 alcanza con declararlas `def` y pedir
    un `StepSync`; el SDK las corre en un pool de hilos.

    Se pregunta con `is_handler_async`, la property del propio SDK, en vez de
    inspeccionar una lista paralela nuestra: asi el test mira exactamente el
    dato que el SDK usa para decidir como ejecutarlas.
    """
    corrutinas = [f.id for f in handlers.all_functions if f.is_handler_async]
    assert corrutinas == []


def test_hay_una_funcion_registrada_por_evento_esperado():
    """Un handler que no entra en `all_functions` no lo sirve nadie."""
    ids = {f.id for f in handlers.all_functions}
    assert ids == {
        "geeworker-process-parcela",
        "geeworker-process-rancho",
        "geeworker-process-parcela-mes",
        "geeworker-process-rancho-mes",
        "geeworker-cerrar-altas-canceladas",
        "geeworker-diagnostico-latencia",
        # M.6.2 borro `compute-timeseries`, `query-available-dates`, `export-data`
        # y `compute-parcela-stats`. Este queda hasta M.6.2b, que lo pasa al
        # pipeline.
        "geeworker-generate-heatmap-on-demand",
    }


def test_process_kml_ya_no_existe():
    """E.2: su evento se elimino en Geocore (`5b5746a`), asi que no se dispara.

    Un handler muerto no es inocuo: se registra en Inngest, aparece en el panel
    y sugiere que hay un camino de KML por el worker que no existe — el KML lo
    parsea Geocore (`DECISIONS #17`).
    """
    assert not hasattr(handlers, "process_kml")


def test_el_numero_de_reintentos_es_uno_solo():
    """Si `RETRIES` y el `retries=` de los decoradores se separan, E.4 se rompe.

    El wrapper decide si marcar `failed` comparando contra `RETRIES`. Con un
    decorador en 5 y la constante en 3, el job se marcaria fallido dos intentos
    antes de que Inngest deje de reintentar — y volveria a mentir.
    """
    assert {f._opts.retries for f in handlers.all_functions} == {RETRIES}


# --- 2. El contrato de coordenadas con Geocore ----------------------------


def test_coordenadas_como_lista_de_diccionarios_de_csharp():
    """Geocore manda `CoordinateDto`, o sea `[{lat, lng}]`, nunca ValueTuples."""
    coords = [{"lat": 24.75, "lng": -107.45}, {"lat": 24.85, "lng": -107.45},
              {"lat": 24.85, "lng": -107.35}]
    (anillo,), es_multi = normalizar_coordenadas(coords)

    # **El orden se invierte a (lng, lat).** Es el detalle que no falla: da un
    # ROI en otro lugar del planeta, y GEE lo procesa sin quejarse.
    assert anillo[0] == [-107.45, 24.75]
    # Y el anillo se cierra solo: GEE rechaza un poligono abierto.
    assert anillo[0] == anillo[-1]
    assert es_multi is False


def test_coordenadas_con_mayuscula_inicial():
    """C# serializa en PascalCase si no se configura lo contrario."""
    coords = [{"Lat": 24.75, "Lng": -107.45}, {"Lat": 24.85, "Lng": -107.45},
              {"Lat": 24.85, "Lng": -107.35}]
    (anillo,), _ = normalizar_coordenadas(coords)
    assert anillo[0] == [-107.45, 24.75]


def test_una_coordenada_en_cero_no_se_pierde():
    """`c.get("lng") or c.get("Lng")` trataba el 0.0 como ausente.

    El meridiano de Greenwich y el ecuador son coordenadas validas. Con el `or`,
    un `lng` de 0.0 caia al `Lng` en PascalCase, que no existe, y quedaba `None`
    — un ROI corrupto sin ningun error.
    """
    (anillo,), _ = normalizar_coordenadas(
        [{"lat": 0.0, "lng": 0.0}, {"lat": 0.0, "lng": 1.0}, {"lat": 1.0, "lng": 1.0}]
    )
    assert anillo[0] == [0.0, 0.0]
    assert None not in anillo[0]


def test_un_anillo_ya_cerrado_no_se_duplica():
    coords = [{"lat": 24.75, "lng": -107.45}, {"lat": 24.85, "lng": -107.45},
              {"lat": 24.85, "lng": -107.35}, {"lat": 24.75, "lng": -107.45}]
    (anillo,), _ = normalizar_coordenadas(coords)
    assert len(anillo) == 4


def test_poligono_y_multipoligono_se_distinguen_por_anidamiento():
    poligono = [[[-107.45, 24.75], [-107.45, 24.85], [-107.35, 24.85], [-107.45, 24.75]]]
    coords, es_multi = normalizar_coordenadas(poligono)
    assert es_multi is False
    assert coords is poligono

    multi = [poligono, [[[-107.25, 24.75], [-107.25, 24.85], [-107.15, 24.85], [-107.25, 24.75]]]]
    _, es_multi = normalizar_coordenadas(multi)
    assert es_multi is True


# --- 4. (borrada en M.6.2) ------------------------------------------------
#
# Aca vivian los cinco tests de `insert_measurements` y `una_por_dia`, que se
# fueron con las funciones (`DECISIONS #60`). Eran la escritura **por pasada**.
#
# Lo que cuidaban no se perdio, lo cuida el camino mensual en
# `test_escritura_mensual.py`: que el lote sea **una sola** llamada a
# `execute_values`, que las filas repetidas no lleguen a la base (ahi es un
# `ValueError`, porque `filas_del_mes` no puede producirlas), que un lote vacio
# no abra conexion, y que la fecha viaje como `datetime` con zona y no como
# string (`test_upsert_escribe_las_columnas_mensuales`).
#
# Y `una_por_dia` juntaba las dos imagenes de una pasada en el borde de dos
# teselas MGRS promediando dos medias espaciales parciales. El pipeline lo
# resuelve antes: `pipeline/etapas/compuesto.py` mosaica por
# `DATATAKE_IDENTIFIER` en el espacio de la imagen, y su test lo fija.


# --- 5. Las fechas van como timestamptz, no como string (E.6) -------------


def test_una_fecha_sin_hora_se_ancla_a_medianoche_utc():
    """`measurements.fecha` es `timestamptz` — verificado en el codigo de Geocore
    (`Measurement.Fecha : DateTimeOffset`). Un string deja que Postgres haga el
    cast usando el TimeZone de la sesion.

    Y **`fecha` esta en la primary key**, asi que el mismo dia escrito desde dos
    husos distintos daria dos instantes, o sea dos filas, y el ON CONFLICT no
    colapsaria ninguna: rompe la idempotencia, no solo corre la fecha.
    """
    from datetime import datetime, timezone

    from repositories.db_repository import a_timestamptz

    convertida = a_timestamptz("2026-01-01", "fecha")
    assert convertida == datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert convertida.tzinfo is not None


def test_el_anclaje_es_determinista():
    """Si no lo fuera, cada reprocesamiento crearia una fila nueva.

    Es la razon de anclar al dia en vez de conservar el instante real de la
    pasada: `fecha` esta en la PK, y dos milisegundos de diferencia serian dos
    filas para la misma medicion.
    """
    from repositories.db_repository import a_timestamptz

    assert a_timestamptz("2026-01-01", "f") == a_timestamptz("2026-01-01", "f")


def test_un_instante_con_zona_se_respeta():
    from datetime import datetime, timezone

    from repositories.db_repository import a_timestamptz

    assert a_timestamptz("2026-01-01T12:30:00Z", "f") == datetime(
        2026, 1, 1, 12, 30, tzinfo=timezone.utc
    )


def test_un_datetime_naive_se_interpreta_como_utc():
    """Todo lo que produce el worker viene de `datetime.now(timezone.utc)` o de
    GEE, que trabaja en UTC. Asumirlo evita que un naive caiga al cast de
    Postgres, que es el bug que esta funcion cierra."""
    from datetime import datetime, timezone

    from repositories.db_repository import a_timestamptz

    naive = datetime(2026, 1, 1, 8, 0)  # noqa: DTZ001 - el naive ES el caso de prueba
    assert a_timestamptz(naive, "f") == datetime(
        2026, 1, 1, 8, 0, tzinfo=timezone.utc
    )


def test_una_fecha_ilegible_falla_nombrando_el_campo():
    """Mejor reventar aca que escribir una fecha corrida en la PK."""
    import pytest as _pytest

    from repositories.db_repository import a_timestamptz

    with _pytest.raises(ValueError, match="acquired_ts"):
        a_timestamptz("el martes", "acquired_ts")


