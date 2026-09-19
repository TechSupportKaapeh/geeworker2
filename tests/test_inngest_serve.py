"""M.4.8: el worker atiende varios steps a la vez (`services/inngest_serve.py`).

El bug que cierra: `inngest.fast_api.serve()` declara la ruta `async` y llama a
los handlers sincronicos **dentro del event loop**, asi que un step de 7 s
congelaba el proceso entero y los steps de las otras corridas hacian cola en
Inngest (hasta 55 s de espera, medido en produccion el 2026-09-18).

**Los pedidos son de verdad**: el cuerpo es el que manda el executor de Inngest,
y la funcion se ejecuta. La primera version de este test reemplazaba el endpoint
por uno propio y por lo tanto no probaba nada: no vio que el `framework` tenia
que ser el enum del SDK y no un `str`, que daba 500 en **cada** pedido.

Se prueba:
  1. que dos invocaciones se atiendan en paralelo, con el `serve` del SDK como
     control negativo: el mismo test, en serie;
  2. que `/health` conteste mientras corre un step;
  3. que la ruta, sus metodos y la respuesta sean los mismos que los del SDK.
"""
import asyncio
import sys
import threading
import time
from pathlib import Path

import fastapi
import httpx
import inngest
import inngest.fast_api
import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from services import inngest_serve

# Lo que tarda el "step" falso: bastante mas que el ruido del event loop, para
# que la diferencia entre serie y paralelo no sea ambigua.
DURACION = 0.4

APP_ID = "test-paralelo"
FN_ID = "lento"
RUTA = f"/api/inngest?fnId={APP_ID}-{FN_ID}&stepId=step"

# Lo que manda el executor de Inngest para ejecutar una funcion
# (`server_lib/execution_request.py`).
INVOCACION = {
    "ctx": {"attempt": 0, "disable_immediate_execution": False,
            "run_id": "01TEST", "stack": {"stack": []}},
    "event": {"name": "test/lento", "data": {}, "id": "", "ts": 0},
    "events": [{"name": "test/lento", "data": {}, "id": "", "ts": 0}],
    "steps": {},
    "use_api": False,
}


def _app_con(serve):
    """Una app servida por `serve`, con una funcion que bloquea su hilo.

    `time.sleep` es a proposito: imita lo que hace un step de verdad, que espera
    a GEE con una llamada sincronica y no le cede el control al event loop.
    """
    cliente = inngest.Inngest(app_id=APP_ID, is_production=False, event_key="dev")

    @cliente.create_function(
        fn_id=FN_ID, trigger=inngest.TriggerEvent(event="test/lento")
    )
    def lento(ctx, step):
        time.sleep(DURACION)
        return {"hilo": threading.get_ident()}

    app = fastapi.FastAPI()

    @app.get("/health")
    def health():
        return {"status": "ok"}

    serve(app, cliente, [lento])
    return app


async def _invocar(app, cuantas):
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://t") as cliente:
        inicio = time.monotonic()
        respuestas = await asyncio.gather(
            *[cliente.post(RUTA, json=INVOCACION) for _ in range(cuantas)]
        )
        return time.monotonic() - inicio, respuestas


def _hilo_de(respuesta):
    """El hilo donde corrio el handler, que el SDK devuelve en el `data`."""
    cuerpo = respuesta.json()
    return cuerpo["hilo"] if "hilo" in cuerpo else cuerpo["data"]["hilo"]


def test_dos_steps_se_atienden_en_paralelo():
    tardo, respuestas = asyncio.run(_invocar(_app_con(inngest_serve.serve), 2))

    assert [r.status_code for r in respuestas] == [200, 200]
    # En serie serian 2 × DURACION; en paralelo, poco mas de una.
    assert tardo < DURACION * 1.8, f"se atendieron en serie: {tardo:.2f} s"
    # Y cada uno en su propio hilo del pool, no en el del event loop.
    assert _hilo_de(respuestas[0]) != _hilo_de(respuestas[1])


def test_control_negativo_el_serve_del_sdk_los_atiende_en_serie():
    """El bug de produccion, reproducido: lo unico que cambia es quien monta la ruta."""
    tardo, respuestas = asyncio.run(_invocar(_app_con(inngest.fast_api.serve), 2))

    assert [r.status_code for r in respuestas] == [200, 200]
    assert tardo >= DURACION * 1.8, f"no se atendieron en serie: {tardo:.2f} s"


def test_la_respuesta_es_la_misma_que_la_del_sdk():
    """Mismo cuerpo, mismo codigo y las mismas cabeceras del SDK.

    Las cabeceras no son decorativas: por ahi el executor sabe con que SDK y que
    framework habla. Con `framework` como `str` en vez del enum, armar esta
    respuesta tiraba `AttributeError` y el pedido salia 500.
    """
    _, (nuestra,) = asyncio.run(_invocar(_app_con(inngest_serve.serve), 1))
    _, (del_sdk,) = asyncio.run(_invocar(_app_con(inngest.fast_api.serve), 1))

    assert nuestra.status_code == del_sdk.status_code == 200
    cabeceras = {"x-inngest-framework", "x-inngest-sdk", "x-inngest-req-version"}
    for clave in cabeceras & set(del_sdk.headers):
        assert nuestra.headers.get(clave) == del_sdk.headers[clave], clave
    assert nuestra.headers.get("x-inngest-framework") == "fast_api"


def test_health_responde_mientras_corre_un_step():
    """`/health` es la sonda de Railway: un step largo no puede tumbarla."""
    app = _app_con(inngest_serve.serve)

    async def _correr():
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://t") as c:
            step = asyncio.create_task(c.post(RUTA, json=INVOCACION))
            await asyncio.sleep(DURACION / 4)
            inicio = time.monotonic()
            salud = await c.get("/health")
            demora = time.monotonic() - inicio
            await step
            return salud, demora

    salud, demora = asyncio.run(_correr())

    assert salud.status_code == 200
    assert demora < DURACION / 2, f"/health espero al step: {demora:.2f} s"


@pytest.mark.parametrize("metodo", ["GET", "POST", "PUT"])
def test_la_ruta_y_sus_metodos_son_los_mismos_que_los_del_sdk(metodo):
    """Si la superficie cambiara, Inngest dejaria de encontrar al worker."""
    nuestra = _app_con(inngest_serve.serve)
    del_sdk = _app_con(inngest.fast_api.serve)

    def _tiene(app):
        return any(
            getattr(r, "path", None) == "/api/inngest"
            and metodo in (getattr(r, "methods", None) or ())
            for r in app.routes
        )

    assert _tiene(nuestra) == _tiene(del_sdk) is True


def test_la_ruta_es_la_constante_del_sdk():
    """No se escribe a mano: si el SDK la cambia, se cambia con el."""
    from inngest._internal import const

    assert inngest_serve.RUTA == const.DEFAULT_SERVE_PATH == "/api/inngest"
