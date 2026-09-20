"""M.4.2: `con_seguimiento`, el decorador de todos los handlers.

Hasta M.6.2b habia dos decoradores —este y el `_with_job_tracking` de la capa
vieja—, que eran el mismo `envolver_con_estado` con distinta procedencia de
`update_processing_job`. Borrada la capa vieja queda uno solo, asi que los tests
que fijaban el comportamiento se mudaron aca desde `test_inngest_handlers.py`.
Los de la bitacora siguen en `test_avance_job.py`.
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


@pytest.mark.parametrize("attempt", [0, 1, 2])
def test_un_intento_intermedio_no_marca_failed(estados, attempt):
    """E.4: `failed` tiene que significar "no se va a recuperar".

    Antes se marcaba en cada intento y el reintento lo devolvia a `running`, asi
    que con `retries=3` el estado mentia durante toda la ventana: un job que se
    iba a recuperar solo aparecia como fallido, y quien lo mirara diagnosticaba
    un problema inexistente.

    La excepcion **si** se propaga igual: es lo que hace que Inngest reintente.
    """
    with pytest.raises(ValueError, match="revento"):
        _handler_roto(_Ctx({"jobId": "job-2"}, attempt=attempt), _Step())

    assert estados == [("job-2", "running")]


def test_sin_job_id_no_se_toca_la_tabla(estados):
    """Los eventos que no vienen de un pedido de Geocore no tienen job."""
    assert _handler_ok(_Ctx({"tenantId": "t"}), _Step()) == {"tenant": "t"}
    assert estados == []


def test_las_claves_del_payload_se_normalizan_a_camelCase(estados):
    """Geocore serializa en PascalCase; los handlers leen `parcelaId`."""
    visto = {}

    @seguimiento.con_seguimiento
    def handler(ctx, step, payload):
        visto.update(payload)
        return {}

    handler(_Ctx({"ParcelaId": "p-1", "TenantId": "t-1"}), _Step())
    assert visto == {"parcelaId": "p-1", "tenantId": "t-1"}
