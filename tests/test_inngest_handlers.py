"""Invariantes de los handlers de Inngest.

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

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from services import inngest_handlers as handlers

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
        "geeworker-generate-heatmap-on-demand",
        "geeworker-compute-timeseries",
        "geeworker-query-available-dates",
        "geeworker-export-data",
        "geeworker-compute-parcela-stats",
        "geeworker-register-layer",
    }


def test_process_kml_ya_no_existe():
    """E.2: su evento se elimino en Geocore (`5b5746a`), asi que no se dispara.

    Un handler muerto no es inocuo: se registra en Inngest, aparece en el panel
    y sugiere que hay un camino de KML por el worker que no existe — el KML lo
    parsea Geocore (`DECISIONS #17`).
    """
    assert not hasattr(handlers, "process_kml")


# --- 2. El contrato de coordenadas con Geocore ----------------------------


def test_coordenadas_como_lista_de_diccionarios_de_csharp():
    """Geocore manda `CoordinateDto`, o sea `[{lat, lng}]`, nunca ValueTuples."""
    coords = [{"lat": 24.75, "lng": -107.45}, {"lat": 24.85, "lng": -107.45},
              {"lat": 24.85, "lng": -107.35}]
    (anillo,), es_multi = handlers.normalizar_coordenadas(coords)

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
    (anillo,), _ = handlers.normalizar_coordenadas(coords)
    assert anillo[0] == [-107.45, 24.75]


def test_una_coordenada_en_cero_no_se_pierde():
    """`c.get("lng") or c.get("Lng")` trataba el 0.0 como ausente.

    El meridiano de Greenwich y el ecuador son coordenadas validas. Con el `or`,
    un `lng` de 0.0 caia al `Lng` en PascalCase, que no existe, y quedaba `None`
    — un ROI corrupto sin ningun error.
    """
    (anillo,), _ = handlers.normalizar_coordenadas(
        [{"lat": 0.0, "lng": 0.0}, {"lat": 0.0, "lng": 1.0}, {"lat": 1.0, "lng": 1.0}]
    )
    assert anillo[0] == [0.0, 0.0]
    assert None not in anillo[0]


def test_un_anillo_ya_cerrado_no_se_duplica():
    coords = [{"lat": 24.75, "lng": -107.45}, {"lat": 24.85, "lng": -107.45},
              {"lat": 24.85, "lng": -107.35}, {"lat": 24.75, "lng": -107.45}]
    (anillo,), _ = handlers.normalizar_coordenadas(coords)
    assert len(anillo) == 4


def test_poligono_y_multipoligono_se_distinguen_por_anidamiento():
    poligono = [[[-107.45, 24.75], [-107.45, 24.85], [-107.35, 24.85], [-107.45, 24.75]]]
    coords, es_multi = handlers.normalizar_coordenadas(poligono)
    assert es_multi is False
    assert coords is poligono

    multi = [poligono, [[[-107.25, 24.75], [-107.25, 24.85], [-107.15, 24.85], [-107.25, 24.75]]]]
    _, es_multi = handlers.normalizar_coordenadas(multi)
    assert es_multi is True


# --- 3. El estado del job ------------------------------------------------


class _StepFalso:
    """Ejecuta los pasos en el momento y registra sus nombres, como Inngest."""

    def __init__(self):
        self.ejecutados = []

    def run(self, nombre, funcion, *args, **kwargs):
        self.ejecutados.append(nombre)
        return funcion(*args, **kwargs)

    def send_event(self, nombre, evento):
        self.ejecutados.append(nombre)


class _CtxFalso:
    """`attempt` es 0-indexado, igual que en `inngest.Context`."""

    def __init__(self, data, attempt=0):
        self.event = type("Evento", (), {"data": data})()
        self.attempt = attempt


@pytest.fixture
def jobs_registrados(monkeypatch):
    """Captura las llamadas a `update_processing_job` en vez de ir a la DB."""
    llamadas = []
    monkeypatch.setattr(
        handlers, "update_processing_job",
        lambda job_id, status, **kw: llamadas.append((job_id, status)),
    )
    return llamadas


def test_el_job_pasa_por_running_y_completed(jobs_registrados):
    @handlers._with_job_tracking
    def handler_ok(ctx, step, payload):
        return {"ok": True}

    paso = _StepFalso()
    resultado = handler_ok(_CtxFalso({"jobId": "job-1", "tenantId": "t"}), paso)

    assert resultado == {"ok": True}
    assert jobs_registrados == [("job-1", "running"), ("job-1", "completed")]


@pytest.mark.parametrize("attempt", [0, 1, 2])
def test_un_intento_intermedio_no_marca_failed(jobs_registrados, attempt):
    """E.4: `failed` tiene que significar "no se va a recuperar".

    Antes se marcaba en cada intento y el reintento lo devolvia a `running`, asi
    que con `retries=3` el estado mentia durante toda la ventana: un job que se
    iba a recuperar solo aparecia como fallido, y quien lo mirara diagnosticaba
    un problema inexistente.

    La excepcion **si** se propaga igual: es lo que hace que Inngest reintente.
    """
    @handlers._with_job_tracking
    def handler_roto(ctx, step, payload):
        raise ValueError("revento")

    with pytest.raises(ValueError, match="revento"):
        handler_roto(_CtxFalso({"jobId": "job-2"}, attempt=attempt), _StepFalso())

    assert jobs_registrados == [("job-2", "running")]


def test_el_ultimo_intento_si_marca_failed(jobs_registrados):
    """Con `RETRIES=3` y `attempt` 0-indexado, el ultimo es `attempt == 3`."""
    @handlers._with_job_tracking
    def handler_roto(ctx, step, payload):
        raise ValueError("revento")

    with pytest.raises(ValueError, match="revento"):
        handler_roto(
            _CtxFalso({"jobId": "job-3"}, attempt=handlers.RETRIES), _StepFalso()
        )

    assert jobs_registrados == [("job-3", "running"), ("job-3", "failed")]


def test_el_numero_de_reintentos_es_uno_solo():
    """Si `RETRIES` y el `retries=` de los decoradores se separan, E.4 se rompe.

    El wrapper decide si marcar `failed` comparando contra `RETRIES`. Con un
    decorador en 5 y la constante en 3, el job se marcaria fallido dos intentos
    antes de que Inngest deje de reintentar — y volveria a mentir.
    """
    assert {f._opts.retries for f in handlers.all_functions} == {handlers.RETRIES}


def test_sin_job_id_no_se_toca_la_tabla(jobs_registrados):
    """Los eventos que no vienen de un pedido de Geocore no tienen job."""
    @handlers._with_job_tracking
    def handler_ok(ctx, step, payload):
        return {"ok": True}

    handler_ok(_CtxFalso({"tenantId": "t"}), _StepFalso())
    assert jobs_registrados == []


def test_las_claves_del_payload_se_normalizan_a_camelCase():
    """Geocore serializa en PascalCase; los handlers leen `parcelaId`."""
    visto = {}

    @handlers._with_job_tracking
    def handler(ctx, step, payload):
        visto.update(payload)
        return {}

    handler(_CtxFalso({"ParcelaId": "p-1", "TenantId": "t-1"}), _StepFalso())
    assert visto == {"parcelaId": "p-1", "tenantId": "t-1"}


# --- 4. Las mediciones se escriben en lote (E.7) --------------------------


def test_las_mediciones_se_escriben_en_una_sola_operacion(monkeypatch):
    """~70 fechas eran ~70 conexiones al pool, cada una con su commit.

    Se captura lo que llega a `execute_values` para comprobar dos cosas: que es
    **una sola** llamada, y que las filas van en el orden de columnas del INSERT.
    """
    from repositories import db_repository

    llamadas = []
    monkeypatch.setattr(db_repository, "get_connection", lambda: _ConexionFalsa())
    monkeypatch.setattr(db_repository, "release_connection", lambda conn: None)
    monkeypatch.setattr(
        db_repository, "execute_values",
        lambda cur, sql, filas: llamadas.append(filas),
    )

    escritas = db_repository.insert_measurements([
        {"parcela_id": "p", "indice": "ndvi", "fecha": "2026-01-01",
         "tenant_id": "t", "valor": 0.5},
        {"parcela_id": "p", "indice": "ndvi", "fecha": "2026-01-06",
         "tenant_id": "t", "valor": 0.6, "min_val": 0.1, "max_val": 0.9},
    ])

    # Las fechas salen normalizadas a datetime UTC, no como string: la columna es
    # `timestamptz` y esta en la PK (E.6).
    from datetime import datetime, timezone
    ene1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    ene6 = datetime(2026, 1, 6, tzinfo=timezone.utc)

    assert escritas == 2
    assert len(llamadas) == 1
    assert llamadas[0][0] == ("p", "ndvi", ene1, "t", 0.5, None, None)
    assert llamadas[0][1] == ("p", "ndvi", ene6, "t", 0.6, 0.1, 0.9)


def test_las_fechas_sin_valor_no_llegan_a_la_tabla(monkeypatch):
    """`valor` es NOT NULL, y GEE devuelve `None` cuando la nube tapo la parcela.

    Un hueco en la serie es correcto; abortar el step entero por una fecha
    nublada, no.
    """
    from repositories import db_repository

    llamadas = []
    monkeypatch.setattr(db_repository, "get_connection", lambda: _ConexionFalsa())
    monkeypatch.setattr(db_repository, "release_connection", lambda conn: None)
    monkeypatch.setattr(
        db_repository, "execute_values",
        lambda cur, sql, filas: llamadas.append(filas),
    )

    escritas = db_repository.insert_measurements([
        {"parcela_id": "p", "indice": "ndvi", "fecha": "2026-01-01",
         "tenant_id": "t", "valor": None},
        {"parcela_id": "p", "indice": "ndvi", "fecha": "2026-01-06",
         "tenant_id": "t", "valor": 0.6},
    ])

    from datetime import datetime, timezone

    assert escritas == 1
    assert len(llamadas[0]) == 1
    assert llamadas[0][0][2] == datetime(2026, 1, 6, tzinfo=timezone.utc)


def test_una_serie_entera_nublada_no_toca_la_base(monkeypatch):
    """Sin filas que escribir no hay que pedir una conexion siquiera."""
    from repositories import db_repository

    def _no_deberia_conectarse():
        raise AssertionError("pidio una conexion sin filas que escribir")

    monkeypatch.setattr(db_repository, "get_connection", _no_deberia_conectarse)

    assert db_repository.insert_measurements(
        [{"parcela_id": "p", "indice": "ndvi", "fecha": "2026-01-01",
          "tenant_id": "t", "valor": None}]
    ) == 0


def test_dos_filas_del_mismo_dia_no_rompen_el_lote(monkeypatch):
    """La primera corrida real del historico (2026-09-12) fallo asi.

    Postgres rechaza un `INSERT ... ON CONFLICT DO UPDATE` que toque la misma
    fila dos veces, y el lote entero se cae con `CardinalityViolation`. Tiene
    que llegar una sola fila por (parcela, indice, fecha): la ultima.
    """
    from datetime import datetime, timezone

    from repositories import db_repository

    llamadas = []
    monkeypatch.setattr(db_repository, "get_connection", lambda: _ConexionFalsa())
    monkeypatch.setattr(db_repository, "release_connection", lambda conn: None)
    monkeypatch.setattr(
        db_repository, "execute_values",
        lambda cur, sql, filas: llamadas.append(filas),
    )

    escritas = db_repository.insert_measurements([
        {"parcela_id": "p", "indice": "ndvi", "fecha": "2025-09-20",
         "tenant_id": "t", "valor": 0.41},
        {"parcela_id": "p", "indice": "ndvi", "fecha": "2025-09-25",
         "tenant_id": "t", "valor": 0.50},
        {"parcela_id": "p", "indice": "ndvi", "fecha": "2025-09-20",
         "tenant_id": "t", "valor": 0.43},
    ])

    assert escritas == 2
    assert [f[2] for f in llamadas[0]] == [
        datetime(2025, 9, 20, tzinfo=timezone.utc),
        datetime(2025, 9, 25, tzinfo=timezone.utc),
    ]
    assert llamadas[0][0][4] == 0.43


def test_la_serie_trae_una_medicion_por_dia():
    """Una parcela en el borde de dos tiles MGRS recibe dos imagenes de la misma
    pasada. Se promedian: `fecha` esta en la PK de `measurements` (DECISIONS #30).
    """
    from services.ee.ee_client import una_por_dia

    puntos = [
        {"date": "2025-09-20", "timestamp": 2000, "mean": 0.40},
        {"date": "2025-09-15", "timestamp": 1000, "mean": 0.30},
        {"date": "2025-09-20", "timestamp": 2001, "mean": 0.50},
    ]

    juntos = una_por_dia(puntos)

    assert [p["date"] for p in juntos] == ["2025-09-15", "2025-09-20"]
    assert juntos[0]["mean"] == 0.30
    assert abs(juntos[1]["mean"] - 0.45) < 1e-9
    # Conserva el resto de los campos del primero del dia.
    assert juntos[1]["timestamp"] == 2000


class _ConexionFalsa:
    def cursor(self):
        return object()

    def commit(self):
        pass


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


def test_las_mediciones_en_lote_llevan_datetime_no_string(monkeypatch):
    """El lote tambien pasa por el normalizador."""
    from datetime import datetime, timezone

    from repositories import db_repository

    llamadas = []
    monkeypatch.setattr(db_repository, "get_connection", lambda: _ConexionFalsa())
    monkeypatch.setattr(db_repository, "release_connection", lambda conn: None)
    monkeypatch.setattr(
        db_repository, "execute_values",
        lambda cur, sql, filas: llamadas.append(filas),
    )

    db_repository.insert_measurements([
        {"parcela_id": "p", "indice": "ndvi", "fecha": "2026-01-01",
         "tenant_id": "t", "valor": 0.5},
    ])

    assert llamadas[0][0][2] == datetime(2026, 1, 1, tzinfo=timezone.utc)


# --- 6. Las dos claves de una capa salen de una sola fuente (E.9) ----------


def test_la_capa_sistematica_y_la_on_demand_de_la_misma_fecha_son_la_misma():
    """Era una colision real, no una duplicacion teorica.

    `process_parcela` armaba `natural_key = f"ndvi_{id}_{fecha}"` con
    `storage_key = parcelas/{id}/…`, y el on-demand armaba
    `natural_key = f"{indice}_{id}_{fechaInicio}"` con `heatmaps/{id}/…`. Para
    NDVI, la misma parcela y la misma fecha **las dos natural_key eran
    identicas** — o sea el mismo UUIDv5, la misma fila de `layers`— pero con
    `storage_key` distinta.

    El pedido on-demand pisaba la fila de la capa sistematica apuntandola a
    `heatmaps/`, y el objeto de `parcelas/` quedaba huerfano en el bucket,
    referenciado por nadie.
    """
    sistematica = handlers.claves_de_capa("parcela", "p-1", "ndvi", "2026-01-01")
    on_demand = handlers.claves_de_capa("parcela", "p-1", "ndvi", "2026-01-01", "2026-01-01")

    # Misma capa: misma key y misma fila.
    assert sistematica == on_demand
    assert sistematica[0] == "parcelas/p-1/2026-01-01_ndvi.tif"
    assert "heatmaps/" not in sistematica[0]


def test_dos_periodos_distintos_no_comparten_objeto():
    """La key usaba solo `fechaInicio`, asi que un heatmap de ene1–ene31 y otro
    de ene1–feb28 escribian **el mismo objeto**."""
    enero = handlers.claves_de_capa("parcela", "p-1", "ndvi", "2026-01-01", "2026-01-31")
    dos_meses = handlers.claves_de_capa("parcela", "p-1", "ndvi", "2026-01-01", "2026-02-28")

    assert enero != dos_meses
    assert enero[0] == "parcelas/p-1/2026-01-01_2026-01-31_ndvi.tif"


def test_la_storage_key_y_la_natural_key_no_pueden_divergir():
    """El par del rancho es el caso peligroso: `process_rancho` arma la
    `storage_key` y `register_layer` la `natural_key`, **en otro handler,
    separados por un evento**. Que salgan de la misma funcion es lo que impide
    que una convencion se desincronice a traves de esa frontera."""
    storage_key, natural_key = handlers.claves_de_capa("rancho", "r-1", "ndvi", "2026-01-01")

    assert storage_key == "ranchos/r-1/2026-01-01_ndvi.tif"
    assert natural_key == "rancho_ndvi_r-1_2026-01-01"
    # La natural_key identifica la capa sin depender del prefijo del bucket, que
    # A-7 puede rediseñar en FASE C sin cambiar la identidad de las filas.
    assert "/" not in natural_key


def test_cada_indice_es_una_capa_distinta():
    ndvi = handlers.claves_de_capa("parcela", "p-1", "ndvi", "2026-01-01")
    ndwi = handlers.claves_de_capa("parcela", "p-1", "ndwi", "2026-01-01")

    assert ndvi[0] != ndwi[0]
    assert ndvi[1] != ndwi[1]


def test_rancho_y_parcela_con_el_mismo_id_no_colisionan():
    """Lo destapo este mismo test en su primera version.

    Sin la entidad en la `natural_key`, un rancho y una parcela con el mismo id,
    indice y fecha darian el **mismo UUIDv5** y por lo tanto la misma fila de
    `layers`. Hoy es imposible porque los ids son uuid, pero la identidad de una
    capa no deberia depender de eso — y cerrarlo era gratis: el worker todavia
    no escribio contra el MinIO real, asi que no hay UUIDv5 en produccion que
    cambiar.
    """
    rancho = handlers.claves_de_capa("rancho", "x", "ndvi", "2026-01-01")
    parcela = handlers.claves_de_capa("parcela", "x", "ndvi", "2026-01-01")

    assert rancho[0] != parcela[0], "distinto objeto en el bucket"
    assert rancho[1] != parcela[1], "y distinta fila en layers"
