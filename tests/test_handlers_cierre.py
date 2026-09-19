"""M.4.7: el cierre de un job cuando su corrida termina fuera del handler.

Tres cosas:
  1. Los handlers de cierre leen el evento de sistema de Inngest (el original
     viene en `event`) y cierran el job con el motivo, sin secretos.
  2. Lo que se registra en Inngest: el `on_failure` de las dos altas escucha
     `inngest/function.failed`, y la funcion de cancelaciones escucha
     `inngest/function.cancelled` solo para las dos altas. Se mira en
     `get_config`, que es lo que el SDK manda al sincronizar.
  3. `cerrar_job_abierto` solo toca un job `pending` o `running`.
"""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from handlers import cancelaciones, cierre, parcela, rancho
from repositories import db_repository
from services import inngest_handlers

JOB = "3f1b2c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"


class _Step:
    def __init__(self):
        self.ejecutados = []

    def run(self, nombre, funcion, *args):
        self.ejecutados.append(nombre)
        return funcion(*args)


class _Evento:
    def __init__(self, data):
        self.data = data


class _Ctx:
    def __init__(self, data):
        self.event = _Evento(data)
        self.attempt = 0


def _sistema(original_data, error=None):
    """Un `inngest/function.failed` o `.cancelled`: el evento original va en `event`."""
    datos = {"function_id": "geeworker-process-parcela", "run_id": "01J…",
             "event": {"name": "terra/parcela.created", "data": original_data}}
    if error is not None:
        datos["error"] = error
    return datos


@pytest.fixture
def base(monkeypatch):
    estado = {"cierres": [], "bitacora": [], "abierto": True}

    def _cerrar(job_id, mensaje):
        estado["cierres"].append((job_id, mensaje))
        return estado["abierto"]

    def _registrar(job_id, attempt, stage, level, message, detail=None, progress=None):
        estado["bitacora"].append((job_id, stage, level, message))

    monkeypatch.setattr(db_repository, "cerrar_job_abierto", _cerrar)
    monkeypatch.setattr(db_repository, "registrar_evento_job", _registrar)
    return estado


# --- 1. Los handlers de cierre -----------------------------------------------


def test_una_corrida_fallida_cierra_el_job_con_el_error(base):
    ctx = _Ctx(_sistema({"JobId": JOB, "ParcelaId": "p"},
                        error={"name": "Error", "message": "worker unreachable"}))
    step = _Step()

    resultado = cierre.cerrar_por_falla(ctx, step)

    assert resultado == {"job": JOB, "cerrado": True}
    ((job, mensaje),) = base["cierres"]
    assert job == JOB
    assert mensaje == "Inngest dio la corrida por fallida: Error: worker unreachable"
    assert base["bitacora"] == [(JOB, "fin", "error", mensaje)]
    assert step.ejecutados == ["cerrar-job"]  # la escritura va en un step


def test_una_corrida_cancelada_cierra_el_job(base):
    ctx = _Ctx(_sistema({"jobId": JOB}))  # camelCase tambien

    cierre.cerrar_por_cancelacion(ctx, _Step())

    assert base["cierres"] == [(JOB, "Se canceló la corrida en Inngest")]


def test_un_job_ya_cerrado_no_escribe_otra_linea_de_fin(base):
    """El handler llego a marcar su final: el cierre no lo pisa ni lo repite."""
    base["abierto"] = False

    resultado = cierre.cerrar_por_falla(_Ctx(_sistema({"JobId": JOB})), _Step())

    assert resultado["cerrado"] is False
    assert base["bitacora"] == []


def test_una_corrida_sin_job_no_toca_la_base(base):
    """Un evento que no vino de Geocore no tiene job."""
    resultado = cierre.cerrar_por_falla(_Ctx(_sistema({"ParcelaId": "p"})), _Step())

    assert resultado == {"job": None, "cerrado": False}
    assert base["cierres"] == []


def test_el_motivo_no_filtra_secretos(base):
    """`error_message` lo ve el usuario del tenant: pasa por `resumir_error`."""
    error = {"name": "HTTPError",
             "message": "POST https://x.up.railway.app/api/inngest?token=abc123 failed"}

    cierre.cerrar_por_falla(_Ctx(_sistema({"JobId": JOB}, error=error)), _Step())

    ((_, mensaje),) = base["cierres"]
    assert "abc123" not in mensaje


def test_sin_detalle_del_error_el_mensaje_lo_dice(base):
    cierre.cerrar_por_falla(_Ctx(_sistema({"JobId": JOB}, error={})), _Step())

    assert base["cierres"][0][1] == "Inngest dio la corrida por fallida: sin detalle del error"


# --- 2. Lo que se registra en Inngest -------------------------------------------


@pytest.mark.parametrize("funcion", [parcela.process_parcela, rancho.process_rancho])
def test_las_altas_escuchan_su_propia_falla(funcion):
    config = funcion.get_config("https://worker/api/inngest")

    assert config.on_failure is not None
    (trigger,) = config.on_failure.triggers
    assert trigger.event == "inngest/function.failed"
    # Solo las fallas de esta funcion, no las de cualquiera.
    assert trigger.expression == f"event.data.function_id == '{funcion.id}'"


def test_las_cancelaciones_se_escuchan_solo_para_las_altas():
    config = cancelaciones.cerrar_altas_canceladas.get_config("https://worker/api/inngest")

    (trigger,) = config.main.triggers
    assert trigger.event == "inngest/function.cancelled"
    assert trigger.expression == (
        "event.data.function_id == 'geeworker-process-parcela' || "
        "event.data.function_id == 'geeworker-process-rancho'"
    )


def test_la_funcion_de_cancelaciones_esta_registrada():
    assert cancelaciones.cerrar_altas_canceladas in inngest_handlers.all_functions
    assert not cancelaciones.cerrar_altas_canceladas.is_handler_async


# --- 3. `cerrar_job_abierto` ---------------------------------------------------


class _Cursor:
    def __init__(self, conn):
        self.conn, self.rowcount = conn, 0

    def execute(self, sql, params=None):
        self.conn.sql.append((sql, params))
        if self.conn.error:
            raise self.conn.error
        self.rowcount = self.conn.filas


class _Conexion:
    def __init__(self, filas=1, error=None):
        self.sql, self.filas, self.error = [], filas, error
        self.commits = self.rollbacks = 0

    def cursor(self):
        return _Cursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def _con(monkeypatch, conn):
    monkeypatch.setattr(db_repository, "get_connection", lambda: conn)
    monkeypatch.setattr(db_repository, "release_connection", lambda c: None)


def test_cerrar_solo_toca_un_job_abierto(monkeypatch):
    conn = _Conexion(filas=1)
    _con(monkeypatch, conn)

    assert db_repository.cerrar_job_abierto(JOB, "motivo") is True

    ((sql, params),) = conn.sql
    assert "status IN ('pending', 'running')" in sql
    assert "status = 'failed'" in sql and "finished_at = now()" in sql
    assert params == ("motivo", JOB)


def test_cerrar_un_job_ya_cerrado_devuelve_false(monkeypatch):
    _con(monkeypatch, _Conexion(filas=0))
    assert db_repository.cerrar_job_abierto(JOB, "motivo") is False


def test_cerrar_nunca_levanta_y_devuelve_la_conexion_limpia(monkeypatch):
    conn = _Conexion(error=RuntimeError("could not connect"))
    _con(monkeypatch, conn)

    assert db_repository.cerrar_job_abierto(JOB, "motivo") is False
    assert conn.rollbacks == 1


# --- 4. El pool de conexiones con varios hilos (M.4.8) --------------------------
#
# `SimpleConnectionPool` **no se puede compartir entre hilos** (psycopg2), y
# desde M.4.8 los handlers corren en el pool de hilos de FastAPI.


def test_el_pool_es_el_de_varios_hilos(monkeypatch):
    import psycopg2.pool

    creados = []

    class _PoolFalso:
        def __init__(self, *a, **kw):
            creados.append(type(self).__name__)

        def getconn(self):
            return "conexion"

    monkeypatch.setattr(db_repository, "_pool", None)
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", _PoolFalso)
    # Si alguien volviera al simple, este doble lo delata.
    monkeypatch.setattr(psycopg2.pool, "SimpleConnectionPool",
                        lambda *a, **kw: pytest.fail("el pool simple no sirve con hilos"))

    assert db_repository.get_connection() == "conexion"
    assert creados == ["_PoolFalso"]


def test_muchos_hilos_crean_un_solo_pool(monkeypatch):
    """Sin candado, dos hilos que llegan juntos crean dos pools, y las
    conexiones del que queda descartado no vuelven a ningun `putconn`."""
    import threading
    import time

    import psycopg2.pool

    creados = []

    class _PoolLento:
        def __init__(self, *a, **kw):
            time.sleep(0.05)  # la ventana que el candado tiene que cerrar
            creados.append(1)

        def getconn(self):
            return "conexion"

    monkeypatch.setattr(db_repository, "_pool", None)
    monkeypatch.setattr(psycopg2.pool, "ThreadedConnectionPool", _PoolLento)

    arranquen = threading.Event()

    def _pedir():
        arranquen.wait()
        db_repository.get_connection()

    hilos = [threading.Thread(target=_pedir) for _ in range(8)]
    for hilo in hilos:
        hilo.start()
    arranquen.set()
    for hilo in hilos:
        hilo.join()

    assert len(creados) == 1
