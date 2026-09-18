"""M.4.2: `con_seguimiento`, el decorador de los handlers del pipeline mensual.

El comportamiento del wrapper lo fijan los tests de `_with_job_tracking`
(`test_inngest_handlers.py` y `test_avance_job.py`): los dos decoradores son el
mismo `envolver_con_estado`. Lo unico propio de `con_seguimiento` es de donde
sale `update_processing_job`, y eso es lo que se prueba aca.
"""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from handlers import seguimiento
from repositories import db_repository
from services import avance_job


class _Step:
    def run(self, nombre, funcion, *args, **kwargs):
        return funcion(*args, **kwargs)


class _Ctx:
    def __init__(self, data, attempt=0):
        self.event = type("Evento", (), {"data": data})()
        self.attempt = attempt


@pytest.fixture
def estados(monkeypatch):
    """Reemplaza el repositorio **despues** de decorar: tiene que alcanzarlo igual."""
    llamadas = []
    monkeypatch.setattr(avance_job, "registrar_evento_job", lambda *a, **k: None)
    monkeypatch.setattr(
        db_repository, "update_processing_job",
        lambda job_id, status, **kw: llamadas.append((job_id, status)),
    )
    return llamadas


@seguimiento.con_seguimiento
def _handler_ok(ctx, step, payload):
    return {"tenant": payload["tenantId"]}


@seguimiento.con_seguimiento
def _handler_roto(ctx, step, payload):
    raise ValueError("revento")


def test_escribe_el_estado_en_el_repositorio(estados):
    # `TenantId` en PascalCase: el wrapper normaliza la primera letra.
    resultado = _handler_ok(_Ctx({"JobId": "job-1", "TenantId": "t"}), _Step())
    assert resultado == {"tenant": "t"}
    assert estados == [("job-1", "running"), ("job-1", "completed")]


def test_el_ultimo_intento_marca_failed(estados):
    with pytest.raises(ValueError, match="revento"):
        _handler_roto(_Ctx({"jobId": "job-2"}, attempt=seguimiento.RETRIES), _Step())
    assert estados == [("job-2", "running"), ("job-2", "failed")]


def test_conserva_el_nombre_del_handler():
    """Inngest y el contexto de logging lo identifican por el nombre."""
    assert _handler_ok.__name__ == "_handler_ok"


def test_importar_handlers_no_abre_conexiones():
    """La regla de `pipeline` (M.1.5), tambien para `handlers`."""
    import subprocess

    codigo = (
        "import socket\n"
        "def prohibido(*a, **k):\n"
        "    raise AssertionError('se intento abrir una conexion')\n"
        "socket.socket.connect = prohibido\n"
        "socket.create_connection = prohibido\n"
        "socket.getaddrinfo = prohibido\n"
        "import handlers.seguimiento, handlers.geometria, handlers.utilidades\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo], cwd=str(RAIZ),
        capture_output=True, text=True, timeout=120, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
