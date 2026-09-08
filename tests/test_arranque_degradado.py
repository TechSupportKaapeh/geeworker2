"""El worker tiene que quedar arriba cuando `/api/inngest` no se puede montar.

EL CASO REAL
------------
Con `ENVIRONMENT=production` y sin `INNGEST_SIGNING_KEY`, el SDK no rechaza
peticiones: **se niega a construirse**. En
`inngest/_internal/comm_lib/handler.py:62`:

    signing_key = client.signing_key
    if signing_key is None:
        if self._client.is_production:
            signing_key = os.getenv(const.EnvKey.SIGNING_KEY.value)
            if signing_key is None:
                self._client.logger.error("missing signing key")
                raise errors.SigningKeyMissingError()

Como `inngest.fast_api.serve()` se llama a nivel de modulo, esa excepcion sube
por el import y se lleva el proceso. Fue la cuarta vez que este repo se cayo
por algo que puede fallar al importarse, y la primera en la que **tambien
fallo el diagnostico**: el reporte de arranque vivia en el evento `startup`,
que nunca se dispara si el modulo no termina de importarse.

Este test cubre las dos mitades de la correccion:

  1. que el import sobreviva y el servicio quede en pie para diagnosticarse;
  2. que quedar en pie no abra ninguna ventana: sin `serve()` la ruta no
     existe y todo POST se va con 404.

Y un detalle que la reproduccion manual destapo: una `INNGEST_SIGNING_KEY`
definida pero **vacia** no dispara el error del SDK, porque `""` no es `None`.
Monta igual y falla despues, en cada peticion. Por eso `arranque.py` la reporta
como AUSENTE y no como definida.
"""

import importlib

import inngest.fast_api
import pytest
from fastapi.testclient import TestClient
from inngest._internal import errors


@pytest.fixture
def app_sin_inngest(monkeypatch):
    """Reimporta `app` con un `serve()` que levanta, como en produccion."""
    def serve_que_levanta(*_a, **_k):
        raise errors.SigningKeyMissingError()

    monkeypatch.setattr(inngest.fast_api, "serve", serve_que_levanta)

    import app as modulo
    yield importlib.reload(modulo)

    # Se deja el modulo como estaba para no contaminar los otros tests, que
    # comparten el proceso y verifican la superficie HTTP completa.
    monkeypatch.undo()
    importlib.reload(modulo)


def test_el_import_no_tumba_el_proceso(app_sin_inngest):
    # Si `serve()` levantara sin proteccion, este test ni llegaria a correr:
    # la excepcion saldria del `importlib.reload` del fixture.
    assert app_sin_inngest.app is not None


def test_se_registra_el_motivo(app_sin_inngest):
    motivo = app_sin_inngest._ERROR_AL_MONTAR_INNGEST
    assert motivo is not None
    assert "SigningKeyMissingError" in motivo


def test_health_responde_200_aunque_este_degradado(app_sin_inngest):
    # 200 a proposito: con 503, Railway marca el deploy caido y lo reinicia en
    # bucle, que es justo la patologia que esto viene a evitar. El estado va en
    # el cuerpo, no en el codigo HTTP.
    r = TestClient(app_sin_inngest.app).get("/health")
    assert r.status_code == 200
    cuerpo = r.json()
    assert cuerpo["status"] == "degradado"
    assert cuerpo["inngest"] == "NO montado"
    assert "SigningKeyMissingError" in cuerpo["motivo"]


def test_la_ruta_de_inngest_no_existe_asi_que_sigue_fallando_cerrado(app_sin_inngest):
    # Quedar arriba no puede significar quedar abierto. Sin `serve()` no hay
    # handler montado y no hay forma de invocar nada.
    r = TestClient(app_sin_inngest.app).post("/api/inngest", json={})
    assert r.status_code == 404

    rutas = {ruta.path for ruta in app_sin_inngest.app.routes}
    assert "/api/inngest" not in rutas
    assert "/health" in rutas


def test_cuando_monta_bien_health_lo_dice():
    # El otro lado del contrato, con el modulo normal.
    import app as modulo
    importlib.reload(modulo)
    assert modulo._ERROR_AL_MONTAR_INNGEST is None
    cuerpo = TestClient(modulo.app).get("/health").json()
    assert cuerpo == {"status": "ok", "inngest": "montado"}
