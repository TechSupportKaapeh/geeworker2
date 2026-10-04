"""Invariantes de la configuracion de Inngest.

`/api/inngest` es la unica superficie publica del worker, y lo que la protege es
la verificacion de firma HMAC del SDK. Esa verificacion esta **enteramente
condicionada al modo**: en dev, `net.py::_validate_sig` devuelve `None` sin
mirar nada. Un bug que ponga el modo en dev en produccion no rompe nada visible
—los handlers siguen funcionando— y deja el endpoint abierto.

Por eso lo que se fija aca es el criterio de modo, no el resultado de una
peticion. Los tests son sobre una funcion pura para no depender del entorno.
"""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from services.inngest_client import (
    DEV_EVENT_KEY,
    resolve_client_config,
)


def _config(**cambios):
    base = {
        "is_production": True,
        "base_url": "http://localhost:8288",
        "event_key": "clave-de-eventos",
        "signing_key": "signkey-prod-abc123",
    }
    base.update(cambios)
    return resolve_client_config(**base)


# --- El modo, que es lo que decide si se verifica la firma ---------------


def test_produccion_activa_el_modo_cloud():
    kwargs, problemas = _config()
    assert kwargs["is_production"] is True
    assert problemas == []


def test_desarrollo_activa_el_modo_dev():
    kwargs, _ = _config(is_production=False)
    assert kwargs["is_production"] is False


@pytest.mark.parametrize("environment,esperado_produccion", [
    # Los unicos valores que cuentan como desarrollo.
    ("development", False),
    ("dev", False),
    ("local", False),
    ("DEVELOPMENT", False),
    ("  dev  ", False),
    # Todo lo demas es produccion, **incluido lo que parece desarrollo**. Un
    # typo tiene que apretar los controles, no soltarlos. La version anterior
    # trataba como produccion solo el string exacto "production", asi que
    # "prod" apagaba la verificacion de firma.
    ("production", True),
    ("prod", True),
    ("staging", True),
    ("produccion", True),
    ("", True),
    ("developmentt", True),
])
def test_el_criterio_de_entorno_es_uno_solo(environment, esperado_produccion):
    """`config.IS_PRODUCTION` es la unica fuente de verdad del entorno.

    Se reimplementa el criterio en vez de recargar `config`, que lee el entorno
    al importarse. Si esto se desincroniza del modulo, el test de abajo lo
    agarra.
    """
    es_desarrollo = environment.strip().lower() in ("development", "dev", "local")
    assert (not es_desarrollo) is esperado_produccion


def test_el_criterio_del_test_coincide_con_el_de_config():
    """Ata el test de arriba al modulo real, para que no se separen."""
    import config

    assert config.IS_PRODUCTION is not config.IS_DEVELOPMENT
    esperado = config.ENVIRONMENT.strip().lower() in ("development", "dev", "local")
    assert config.IS_DEVELOPMENT is esperado


# --- Fallo cerrado y diagnostico -----------------------------------------


def test_produccion_sin_signing_key_avisa():
    """El SDK ya falla cerrado; lo que falta es que se entienda por que."""
    kwargs, problemas = _config(signing_key=None)
    assert "signing_key" not in kwargs
    assert any("INNGEST_SIGNING_KEY" in p for p in problemas)


def test_produccion_sin_event_key_avisa():
    _, problemas = _config(event_key=None)
    assert any("INNGEST_EVENT_KEY" in p for p in problemas)


def test_produccion_rechaza_la_event_key_de_desarrollo():
    """`dev-local-key` no vale nada contra Cloud: no se manda y se avisa."""
    kwargs, problemas = _config(event_key=DEV_EVENT_KEY)
    assert kwargs.get("event_key") != DEV_EVENT_KEY
    assert any("INNGEST_EVENT_KEY" in p for p in problemas)


def test_desarrollo_no_exige_nada():
    """En local no hay que configurar secretos para trabajar."""
    kwargs, problemas = _config(
        is_production=False, signing_key=None, event_key=None,
    )
    assert problemas == []
    assert kwargs["event_key"] == DEV_EVENT_KEY


# --- F.3: no forzar las URLs en produccion -------------------------------


def test_produccion_no_fija_las_urls_del_servidor():
    """Forzarlas mandaba al worker a hablarle a su propio localhost.

    `INNGEST_BASE_URL` tiene default `http://localhost:8288`, asi que un deploy
    sin esa variable buscaba Inngest en su propio contenedor.
    """
    kwargs, _ = _config()
    assert "api_base_url" not in kwargs
    assert "event_api_base_url" not in kwargs


def test_desarrollo_si_fija_las_urls_del_servidor():
    kwargs, _ = _config(is_production=False, base_url="http://localhost:8288")
    assert kwargs["api_base_url"] == "http://localhost:8288"
    assert kwargs["event_api_base_url"] == "http://localhost:8288"


# --- La firma se pasa cuando existe --------------------------------------


@pytest.mark.parametrize("is_production", [True, False])
def test_la_signing_key_se_pasa_en_los_dos_modos(is_production):
    """En dev no se usa, pero pasarla permite probar el modo cloud en local."""
    kwargs, _ = _config(is_production=is_production, signing_key="signkey-x")
    assert kwargs["signing_key"] == "signkey-x"


# --- El endpoint, contra un cliente HTTP de verdad ----------------------
#
# Los tests de arriba fijan el criterio; estos dos fijan la consecuencia. Un
# endpoint protegido no se prueba mirando la config: se prueba pidiendole algo
# sin credenciales y viendo que lo rechace.


def _montar_endpoint(is_production):
    """Monta /api/inngest en una app nueva, con una funcion que no hace nada."""
    import fastapi
    import inngest
    from fastapi.testclient import TestClient

    from services import inngest_serve

    cliente = inngest.Inngest(
        app_id="test-firma",
        is_production=is_production,
        signing_key="signkey-prod-" + "a" * 32,
        event_key="de-mentira",
    )

    @cliente.create_function(
        fn_id="noop",
        trigger=inngest.TriggerEvent(event="test/x"),
    )
    def noop(ctx):
        return "no deberia ejecutarse en el caso cloud"

    app = fastapi.FastAPI()
    # El `serve` del worker (M.4.8), no el del SDK: la garantia de la firma
    # es de la ruta que montamos nosotros.
    inngest_serve.serve(app, cliente, [noop])
    return TestClient(app, raise_server_exceptions=False)


_INVOCACION_SIN_FIRMA = {
    "url": "/api/inngest?fnId=test-firma-noop&stepId=step",
    "json": {"event": {"name": "test/x", "data": {}}, "ctx": {}, "steps": {}},
}


def test_en_modo_cloud_una_invocacion_sin_firma_se_rechaza():
    """401 **antes** de parsear el cuerpo: no se puede ni sondear el esquema."""
    respuesta = _montar_endpoint(is_production=True).post(
        _INVOCACION_SIN_FIRMA["url"], json=_INVOCACION_SIN_FIRMA["json"],
    )
    assert respuesta.status_code == 401
    assert "header_missing" in respuesta.text


def test_en_modo_dev_la_misma_invocacion_no_se_rechaza():
    """Control negativo, y el motivo por el que W-8 era grave.

    En dev el SDK no mira la firma: la misma peticion pasa el control de acceso
    y sigue hasta parsear el cuerpo —que aca esta incompleto a proposito, asi
    que falla por el payload y no por la firma—. Con un cuerpo bien armado,
    habria ejecutado el handler.

    Si este test empezara a dar 401, el de arriba dejaria de probar algo: querria
    decir que el rechazo viene de otra parte y no de la verificacion de firma.
    """
    respuesta = _montar_endpoint(is_production=False).post(
        _INVOCACION_SIN_FIRMA["url"], json=_INVOCACION_SIN_FIRMA["json"],
    )
    assert respuesta.status_code != 401
    assert "signature" not in respuesta.text.lower()


# --- Inngest autohospedado (2026-10-04) ----------------------------------


def test_produccion_con_servidor_propio_fija_sus_urls():
    kwargs, problemas = _config(self_hosted_url="http://inngest.railway.internal:8288/")
    assert kwargs["api_base_url"] == "http://inngest.railway.internal:8288"
    assert kwargs["event_api_base_url"] == "http://inngest.railway.internal:8288"
    assert problemas == []


def test_el_servidor_propio_no_apaga_la_firma():
    """Lo que protege /api/inngest es el modo, y con servidor propio sigue en produccion."""
    kwargs, _ = _config(self_hosted_url="http://inngest.railway.internal:8288")
    assert kwargs["is_production"] is True
    assert kwargs["signing_key"] == "signkey-prod-abc123"


def test_el_servidor_propio_sin_signing_key_sigue_avisando():
    _, problemas = _config(self_hosted_url="http://inngest.railway.internal:8288", signing_key=None)
    assert any("INNGEST_SIGNING_KEY" in p for p in problemas)


@pytest.mark.parametrize("url", [
    "inngest.railway.internal:8288",          # sin esquema
    "https://inn.gs",                         # es Cloud: se borra la variable
    "https://api.inngest.com",
    "http://localhost:8288",                  # el propio contenedor
])
def test_una_url_propia_que_no_sirve_no_se_pasa_y_avisa(url):
    """Pasarla a medias mandaria el sync a cualquier lado: se sigue con Cloud y se dice."""
    kwargs, problemas = _config(self_hosted_url=url)
    assert "api_base_url" not in kwargs
    assert "event_api_base_url" not in kwargs
    assert any("INNGEST_SELF_HOSTED_URL" in p for p in problemas)


def test_en_desarrollo_la_url_propia_no_cambia_nada():
    """En dev manda INNGEST_BASE_URL, el dev server de siempre."""
    kwargs, _ = _config(is_production=False, base_url="http://localhost:8288",
                        self_hosted_url="http://inngest.railway.internal:8288")
    assert kwargs["api_base_url"] == "http://localhost:8288"


def test_con_servidor_propio_una_invocacion_sin_firma_se_rechaza():
    """La consecuencia, no la config: el endpoint con api_base_url propio sigue cerrado."""
    import fastapi
    import inngest
    from fastapi.testclient import TestClient

    from services import inngest_serve

    cliente = inngest.Inngest(
        app_id="test-firma-propio",
        is_production=True,
        signing_key="signkey-prod-" + "a" * 32,
        event_key="de-mentira",
        api_base_url="http://inngest.railway.internal:8288",
        event_api_base_url="http://inngest.railway.internal:8288",
    )

    @cliente.create_function(fn_id="noop", trigger=inngest.TriggerEvent(event="test/x"))
    def noop(ctx):
        return "no deberia ejecutarse"

    app = fastapi.FastAPI()
    inngest_serve.serve(app, cliente, [noop])
    respuesta = TestClient(app, raise_server_exceptions=False).post(
        "/api/inngest?fnId=test-firma-propio-noop&stepId=step",
        json={"event": {"name": "test/x", "data": {}}, "ctx": {}, "steps": {}},
    )
    assert respuesta.status_code == 401


# --- La URL con la que se registra el worker (2026-10-04) -----------------


def test_un_serve_origin_http_en_produccion_avisa():
    """Detras de Railway, http da 301 y ningun step se ejecuta: paso con el servidor propio."""
    _, problemas = _config(serve_origin="http://geeworker2-production.up.railway.app")
    assert any("INNGEST_SERVE_ORIGIN" in p for p in problemas)


@pytest.mark.parametrize("origen", ["", "https://geeworker2-production.up.railway.app"])
def test_sin_serve_origin_o_con_https_no_avisa(origen):
    _, problemas = _config(serve_origin=origen)
    assert not any("INNGEST_SERVE_ORIGIN" in p for p in problemas)


def test_el_serve_origin_no_toca_las_urls_ni_el_modo():
    """Sólo se avisa: la URL la lee el SDK solo, y la firma depende del modo."""
    kwargs, _ = _config(serve_origin="https://geeworker2-production.up.railway.app")
    assert kwargs["is_production"] is True
    assert "api_base_url" not in kwargs
