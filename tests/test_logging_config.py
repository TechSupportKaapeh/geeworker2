"""Invariantes del logging con contexto (PLAN.md F.18).

Lo que se fija aca es lo que hacia falta cuando un handler agotaba sus reintentos
y quedaban lineas de ERROR sueltas en Railway: **saber a que ejecucion y a que
entidad pertenece cada linea**.
"""
import json
import logging
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from utils_pkg.logging_config import (
    JSONFormatter,
    TextFormatter,
    _FiltroDeContexto,
    contexto_de_ejecucion,
    formato_por_defecto,
)


def _emitir(formatter, nombre_logger="modulo.cualquiera", nivel=logging.ERROR):
    """Emite un registro por un logger real y devuelve la linea formateada."""
    registro = logging.LogRecord(
        name=nombre_logger, level=nivel, pathname=__file__, lineno=1,
        msg="algo fallo", args=(), exc_info=None,
    )
    _FiltroDeContexto().filter(registro)
    return formatter.format(registro)


def test_el_json_incluye_el_contexto_activo():
    with contexto_de_ejecucion(run_id="run-abc", parcela_id="p-1"):
        salida = json.loads(_emitir(JSONFormatter()))

    assert salida["run_id"] == "run-abc"
    assert salida["parcela_id"] == "p-1"
    assert salida["message"] == "algo fallo"


def test_el_contexto_llega_a_los_logs_de_cualquier_modulo():
    """Es el punto de F.18: la correlacion tiene que aparecer en los logs de
    `storage_service`, `db_repository` y `gee_download` —los que fallan de
    verdad— sin que ninguno sepa que existe un contexto."""
    with contexto_de_ejecucion(run_id="run-abc"):
        salida = json.loads(_emitir(JSONFormatter(), "services.storage_service"))

    assert salida["logger"] == "services.storage_service"
    assert salida["run_id"] == "run-abc"


def test_el_contexto_no_se_filtra_al_salir():
    """Los handlers reusan hilos del pool: un contexto que se filtra atribuye
    los logs de una ejecucion a otra."""
    with contexto_de_ejecucion(run_id="run-abc"):
        pass

    assert "run_id" not in json.loads(_emitir(JSONFormatter()))


def test_el_contexto_se_restaura_aunque_haya_excepcion():
    try:
        with contexto_de_ejecucion(run_id="run-abc"):
            raise ValueError("revento")
    except ValueError:
        pass

    assert "run_id" not in json.loads(_emitir(JSONFormatter()))


def test_los_contextos_anidados_se_acumulan():
    with contexto_de_ejecucion(run_id="run-abc"), contexto_de_ejecucion(paso="descarga"):
        salida = json.loads(_emitir(JSONFormatter()))

    assert salida["run_id"] == "run-abc"
    assert salida["paso"] == "descarga"


def test_los_campos_vacios_no_ensucian_el_log():
    """`job_id` es `None` en los eventos que no vienen de un pedido de Geocore."""
    with contexto_de_ejecucion(run_id="run-abc", job_id=None, tenant_id=""):
        salida = json.loads(_emitir(JSONFormatter()))

    assert "job_id" not in salida
    assert "tenant_id" not in salida


def test_el_modo_texto_tambien_muestra_el_contexto():
    """Si en local no se viera, nadie lo usaria y solo aparecería en produccion
    — donde no se puede iterar."""
    with contexto_de_ejecucion(run_id="run-abc"):
        salida = _emitir(TextFormatter())

    assert "run_id=run-abc" in salida
    assert "algo fallo" in salida


def test_el_formato_por_defecto_depende_del_entorno():
    """Antes era `text` siempre, asi que un deploy salia sin logs estructurados
    a menos que alguien se acordara de una variable no documentada."""
    assert formato_por_defecto(is_production=True) == "json"
    assert formato_por_defecto(is_production=False) == "text"


def test_el_json_es_una_sola_linea():
    """El log estructurado de Railway parsea por linea."""
    with contexto_de_ejecucion(run_id="run-abc"):
        salida = _emitir(JSONFormatter())

    assert "\n" not in salida
