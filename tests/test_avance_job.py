"""La bitacora de jobs (`services/avance_job.py`) y como la usan los handlers.

Se prueba lo que se rompe en silencio:

  1. Que el fallo de un step quede en la bitacora **aunque el wrapper no lo
     vea**. inngest-py convierte la excepcion de un step en un
     `ResponseInterrupt`, que es `BaseException`. `_StepComoElSdk` reproduce eso;
     el `_StepFalso` de `test_inngest_handlers` no (propaga la excepcion tal
     cual), y con el este bug no se podia ver.
  2. Que `NonRetriableError` y `StepError` marquen `failed` en cualquier intento.
  3. (Las altas, con sus meses y su barra, estan en `test_handlers_parcela.py`
     y `test_handlers_rancho.py`.)
  4. Que el mensaje de error no filtre secretos: termina en `error_message`, que
     ve el usuario del tenant.
  5. Que escribir la bitacora nunca tumbe un procesamiento.
"""
import sys
from pathlib import Path

import inngest
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from repositories import db_repository
from services import avance_job
from services import inngest_handlers as handlers


class _Interrupcion(BaseException):
    """Lo que hace el SDK con el resultado de un step: lo levanta como BaseException."""


class _StepComoElSdk:
    """Como `StepSync.run` de inngest-py 0.4 (`step_lib/step_sync.py`).

    `NonRetriableError` y `RetryAfterError` suben tal cual; cualquier otro error
    del step sale envuelto en un `BaseException`, que un `except Exception` no
    atrapa.
    """

    def __init__(self):
        self.ejecutados = []

    def run(self, nombre, funcion, *args):
        self.ejecutados.append(nombre)
        try:
            return funcion(*args)
        except (inngest.NonRetriableError, inngest.RetryAfterError):
            raise
        except Exception as e:
            raise _Interrupcion(e) from e

    def send_event(self, nombre, evento):
        self.ejecutados.append(nombre)


class _CtxFalso:
    def __init__(self, data, attempt=0):
        self.event = type("Evento", (), {"data": data})()
        self.attempt = attempt


@pytest.fixture
def bitacora(monkeypatch):
    lineas = []

    def _registrar(job_id, attempt, stage, level, message, detail=None, progress=None):
        lineas.append({"job": job_id, "intento": attempt, "etapa": stage, "nivel": level,
                       "mensaje": message, "detalle": detail or {}, "progreso": progress})

    monkeypatch.setattr(avance_job, "registrar_evento_job", _registrar)
    return lineas


@pytest.fixture
def estados(monkeypatch):
    llamadas = []
    monkeypatch.setattr(handlers, "update_processing_job",
                        lambda job_id, status, **kw: llamadas.append((status, kw)))
    return llamadas


def _revienta(error):
    def _f():
        raise error
    return _f


# --- 1. La bitacora ------------------------------------------------------


def test_sin_job_no_se_escribe_nada(bitacora):
    """Los eventos que no vienen de un pedido de Geocore no tienen job."""
    avance_job.reportar("x", "fuera de todo seguimiento")
    with avance_job.seguimiento(None, 0, 3):
        avance_job.reportar("x", "con seguimiento pero sin job")
    assert bitacora == []


def test_el_intento_se_escribe_desde_uno(bitacora):
    """`ctx.attempt` es 0-indexado; la persona que mira el panel cuenta desde 1."""
    with avance_job.seguimiento("job-1", 0, 3):
        avance_job.reportar("plan", "arranca", progreso=5, desde="2026-01-01")

    assert bitacora == [{"job": "job-1", "intento": 1, "etapa": "plan", "nivel": "info",
                         "mensaje": "arranca", "detalle": {"desde": "2026-01-01"},
                         "progreso": 5}]


def test_el_seguimiento_no_se_filtra_fuera_del_bloque(bitacora):
    with avance_job.seguimiento("job-1", 0, 3):
        pass
    avance_job.reportar("x", "despues")
    assert bitacora == []


def test_el_fallo_de_un_step_queda_aunque_el_wrapper_no_lo_vea(bitacora):
    """El bug que motivo `paso()`: sin el, este fallo no dejaba rastro en ningun lado."""
    step = _StepComoElSdk()
    with avance_job.seguimiento("job-1", 1, 3), pytest.raises(_Interrupcion):
        avance_job.paso(step, "descarga", _revienta(ValueError("timeout de GEE")))

    (linea,) = bitacora
    assert linea["etapa"] == "descarga"
    assert linea["nivel"] == "warning"
    assert "intento 2 de 4" in linea["mensaje"]
    assert "reintentar" in linea["mensaje"]
    assert linea["detalle"]["error"] == "ValueError: timeout de GEE"


def test_el_ultimo_intento_de_un_step_es_error(bitacora):
    with avance_job.seguimiento("job-1", 3, 3), pytest.raises(_Interrupcion):
        avance_job.paso(_StepComoElSdk(), "descarga", _revienta(ValueError("x")))

    assert bitacora[0]["nivel"] == "error"
    assert "intento 4 de 4" in bitacora[0]["mensaje"]


def test_un_non_retriable_es_error_desde_el_primer_intento(bitacora):
    with avance_job.seguimiento("job-1", 0, 3), pytest.raises(inngest.NonRetriableError):
        avance_job.paso(_StepComoElSdk(), "ingesta",
                        _revienta(inngest.NonRetriableError("sin imagenes")))

    assert bitacora[0]["nivel"] == "error"
    assert "no se reintenta" in bitacora[0]["mensaje"]


def test_un_step_que_sale_bien_no_escribe_fallos(bitacora):
    with avance_job.seguimiento("job-1", 0, 3):
        assert avance_job.paso(_StepComoElSdk(), "ok", lambda: {"n": 1}) == {"n": 1}
    assert bitacora == []


@pytest.mark.parametrize("error,attempt,esperado", [
    (ValueError("x"), 0, False),
    (ValueError("x"), 2, False),
    (ValueError("x"), 3, True),
    (inngest.NonRetriableError("x"), 0, True),
    (inngest.StepError(message="x", name="EEException", stack=None), 0, True),
])
def test_es_definitivo(error, attempt, esperado):
    assert avance_job.es_definitivo(error, attempt, retries=3) is esperado


# --- 2. El wrapper de jobs ------------------------------------------------


def test_inicio_y_fin_quedan_en_la_bitacora(bitacora, estados):
    @handlers._with_job_tracking
    def handler_ok(ctx, step, payload):
        return {"ok": True}

    handler_ok(_CtxFalso({"jobId": "job-1"}), _StepComoElSdk())

    assert [(l["etapa"], l["progreso"]) for l in bitacora] == [("inicio", 1), ("fin", 100)]
    assert estados[-1] == ("completed", {"progress": 100, "finished_at_now": True})


def test_un_non_retriable_marca_failed_en_el_primer_intento(bitacora, estados):
    """Antes el wrapper solo miraba `attempt`: el job quedaba en `running` para siempre."""
    @handlers._with_job_tracking
    def handler(ctx, step, payload):
        raise inngest.NonRetriableError("No se encontraron imágenes útiles")

    with pytest.raises(inngest.NonRetriableError):
        handler(_CtxFalso({"jobId": "job-1"}, attempt=0), _StepComoElSdk())

    assert [s for s, _ in estados] == ["running", "failed"]
    assert bitacora[-1]["etapa"] == "fin" and bitacora[-1]["nivel"] == "error"


def test_un_step_agotado_marca_failed_en_cualquier_intento(bitacora, estados):
    """El SDK entrega el error memoizado de un step sin reintentos como `StepError`."""
    @handlers._with_job_tracking
    def handler(ctx, step, payload):
        raise inngest.StepError(message="Computation timed out.", name="EEException", stack=None)

    with pytest.raises(inngest.StepError):
        handler(_CtxFalso({"jobId": "job-1"}, attempt=0), _StepComoElSdk())

    (_, kw), = [e for e in estados if e[0] == "failed"]
    assert kw["error_message"] == "EEException: Computation timed out."


def test_error_message_no_filtra_la_url_firmada_ni_el_host_privado(bitacora, estados):
    @handlers._with_job_tracking
    def handler(ctx, step, payload):
        raise RuntimeError("fallo subiendo a http://terra-minio.railway.internal:9000/"
                           "terra/x.tif?X-Amz-Signature=abc123&X-Amz-Credential=AKIA")

    with pytest.raises(RuntimeError):
        handler(_CtxFalso({"jobId": "job-1"}, attempt=handlers.RETRIES), _StepComoElSdk())

    (_, kw), = [e for e in estados if e[0] == "failed"]
    assert "abc123" not in kw["error_message"]
    assert "AKIA" not in kw["error_message"]
    assert "railway.internal" not in kw["error_message"]


# --- 3. Mensajes de error ---------------------------------------------------


@pytest.mark.parametrize("crudo,no_debe_quedar", [
    ("could not connect: postgresql://terra:s3cr3t@db:5432/terra", "s3cr3t"),
    ("GET https://bucket.s3.amazonaws.com/a.tif?X-Amz-Signature=deadbeef", "deadbeef"),
    ("auth failed password=hunter2 user=terra", "hunter2"),
    ("token: eyJhbGciOi.payload.firma", "eyJhbGciOi"),
    ("connection refused to postgres.railway.internal:5432", "postgres.railway.internal"),
])
def test_resumir_error_saca_secretos(crudo, no_debe_quedar):
    assert no_debe_quedar not in avance_job.resumir_error(RuntimeError(crudo))


def test_resumir_error_es_una_linea_acotada():
    resumen = avance_job.resumir_error(ValueError("a\nb\n" + "x" * 2000))
    assert "\n" not in resumen
    assert len(resumen) <= avance_job.LARGO_MAXIMO
    assert resumen.startswith("ValueError: a b")


# --- 4. Las altas ------------------------------------------------------------
#
# Las altas de parcela y de rancho son del pipeline mensual (M.4.4 y M.4.5): sus
# tests estan en `test_handlers_parcela.py` y `test_handlers_rancho.py`.


# --- 5. Escribir la bitacora nunca tumba el procesamiento ------------------


class _Cursor:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        verbo = sql.split()[0]
        self.conn.sentencias.append(verbo)
        if verbo == "INSERT" and self.conn.error_insert:
            raise self.conn.error_insert
        if verbo == "UPDATE" and self.conn.falla_update:
            raise RuntimeError("column does not exist")


class _SinTabla(Exception):
    """Como `psycopg2.errors.UndefinedTable`: lo que importa es el `pgcode`."""
    pgcode = "42P01"


class _Conexion:
    def __init__(self, error_insert=None, falla_update=False):
        self.sentencias, self.commits, self.rollbacks = [], 0, 0
        self.error_insert, self.falla_update = error_insert, falla_update

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _con_conexion(monkeypatch, conn):
    monkeypatch.setattr(db_repository, "get_connection", lambda: conn)
    monkeypatch.setattr(db_repository, "release_connection", lambda c: None)


def test_sin_la_migracion_el_avance_se_escribe_igual(monkeypatch):
    """El worker puede desplegarse antes de que se aplique `ProcessingJobEvents`."""
    monkeypatch.setattr(db_repository, "_sin_tabla_de_eventos_hasta", 0.0)
    conn = _Conexion(error_insert=RuntimeError("fallo cualquiera del INSERT"))
    _con_conexion(monkeypatch, conn)

    db_repository.registrar_evento_job("j", 1, "x", "info", "m", {"a": 1}, progress=40)

    assert conn.sentencias == ["UPDATE", "INSERT"]
    assert conn.commits == 1       # el avance quedo
    assert conn.rollbacks == 1     # y la conexion vuelve limpia al pool


def test_sin_la_tabla_la_bitacora_se_pausa_en_vez_de_llenar_el_log(monkeypatch):
    """Una parcela escribe ~50 eventos: sin tabla serian ~50 errores iguales."""
    monkeypatch.setattr(db_repository, "_sin_tabla_de_eventos_hasta", 0.0)
    conn = _Conexion(error_insert=_SinTabla('relation "processing_job_events" does not exist'))
    _con_conexion(monkeypatch, conn)

    db_repository.registrar_evento_job("j", 1, "a", "info", "m", progress=10)
    db_repository.registrar_evento_job("j", 1, "b", "info", "m", progress=20)

    # El segundo ni intenta el INSERT, pero el avance se sigue escribiendo.
    assert conn.sentencias == ["UPDATE", "INSERT", "UPDATE"]


def test_sin_base_no_levanta(monkeypatch):
    def _sin_base():
        raise RuntimeError("could not connect to server")

    monkeypatch.setattr(db_repository, "get_connection", _sin_base)
    db_repository.registrar_evento_job("j", 1, "x", "info", "m")
    db_repository.update_processing_job("j", "running")


def test_un_update_fallido_devuelve_la_conexion_limpia(monkeypatch):
    """Sin rollback, el siguiente que tomara la conexion del pool recibia
    `current transaction is aborted` en una consulta sana."""
    conn = _Conexion(falla_update=True)
    _con_conexion(monkeypatch, conn)

    db_repository.update_processing_job("j", "running")

    assert conn.rollbacks == 1
