"""Monta `/api/inngest` de modo que el worker atienda varios steps a la vez (M.4.8).

**El problema.** `inngest.fast_api.serve()` declara la ruta como `async` y, por
dentro, llama a nuestros handlers **sincronicos dentro del event loop**
(`_internal/execution_lib/v0.py`: `output = handler(...)`, sin hilo ni
`to_thread`). Un step que tarda 7 s en GEE congela el proceso entero: los steps
de las otras corridas esperan, y `/health` tambien. Con tres altas a la vez,
Inngest mostraba esperas de hasta 55 s por step, con el worker trabajando 7
(medido el 2026-09-18, `DECISIONS #53`).

`DECISIONS #26` decia que "el SDK las corre en un pool de hilos". Era falso para
esta integracion; lo que el SDK ofrece es **la variante sincronica del mismo
manejador** (`post_sync`, `get_sync`, `put_sync`), pensada para frameworks
sincronicos. Este modulo la usa y la manda al pool de hilos de Starlette, que es
lo que FastAPI hace con cualquier endpoint `def`.

**Por que no declarar las rutas `def` y listo:** hay que leer el cuerpo del
request, y eso es `await request.body()`. Asi que la ruta sigue siendo `async`,
lee el cuerpo, y **solo el trabajo pesado** va al pool con `run_in_threadpool`.

Todo lo que corre en un hilo tiene que ser seguro entre hilos. Lo que se reviso
al hacerlo (`DECISIONS #53`):

- el pool de conexiones pasa a `ThreadedConnectionPool` (el simple no se puede
  compartir entre hilos) y se crea bajo candado;
- `pipeline/ejecucion.py:plazo()` cuenta los hilos que lo usan: el primero pone
  el plazo de GEE y el ultimo lo restaura;
- el cliente de MinIO y los `ContextVar` de la bitacora ya eran seguros: el
  cliente comparte el pool de urllib3, y cada hilo tiene su propio contexto.

**Los handlers tienen que ser sincronicos**, que es lo que ya exige E.5 y fija
`test_ningun_handler_es_corrutina`. `post_sync` levanta si alguno es `async`.
"""

import json
import logging

import fastapi
from inngest import Inngest

# Internos del SDK: son los mismos que usa `inngest.fast_api`, y la version va
# pinneada (`inngest==0.4.16`). Si un bump los mueve, lo agarra
# `test_inngest_serve.py`, que invoca la ruta de verdad.
from inngest._internal import comm_lib, const, server_lib, transforms
from inngest._internal.function import Function
from starlette.concurrency import run_in_threadpool

logger = logging.getLogger(__name__)

# El framework que el SDK reporta en sus cabeceras. **Es el enum, no el texto**:
# con un `str`, `net.create_headers` revienta con `AttributeError` al armar la
# respuesta, o sea un 500 en cada pedido. Lo agarro la prueba que invoca la ruta
# de verdad; la primera version del test, que reemplazaba el endpoint, no lo veia.
FRAMEWORK = server_lib.Framework.FAST_API

RUTA = const.DEFAULT_SERVE_PATH


def serve(app: fastapi.FastAPI, client: Inngest, functions: list[Function]) -> None:
    """Monta la ruta de Inngest atendiendo cada pedido en un hilo.

    Reemplaza a `inngest.fast_api.serve`. Misma ruta y mismos metodos, asi que
    `test_http_surface` no cambia.
    """
    manejador = comm_lib.CommHandler(
        client=client, framework=FRAMEWORK, functions=functions
    )

    def _pedido(request: fastapi.Request, cuerpo: bytes) -> comm_lib.CommRequest:
        return comm_lib.CommRequest(
            body=cuerpo,
            headers=dict(request.headers.items()),
            query_params=dict(request.query_params.items()),
            raw_request=request,
            request_url=str(request.url),
            serve_origin=None,
            serve_path=None,
        )

    @app.get(RUTA)
    async def inspeccionar(request: fastapi.Request) -> fastapi.Response:
        cuerpo = await request.body()
        respuesta = await run_in_threadpool(manejador.get_sync, _pedido(request, cuerpo))
        return _respuesta(client, respuesta)

    @app.post(RUTA)
    async def ejecutar(request: fastapi.Request) -> fastapi.Response:
        cuerpo = await request.body()
        # **La linea que arregla M.4.8**: el step corre en un hilo del pool, no
        # en el event loop, asi que otro step puede entrar mientras este espera
        # a GEE.
        respuesta = await run_in_threadpool(manejador.post_sync, _pedido(request, cuerpo))
        return _respuesta(client, respuesta)

    @app.put(RUTA)
    async def sincronizar(request: fastapi.Request) -> fastapi.Response:
        cuerpo = await request.body()
        respuesta = await run_in_threadpool(manejador.put_sync, _pedido(request, cuerpo))
        return _respuesta(client, respuesta)


def _respuesta(client: Inngest, comm_res: comm_lib.CommResponse) -> fastapi.Response:
    """La `CommResponse` del SDK como respuesta de FastAPI.

    Igual que `inngest.fast_api._to_response`: si el cuerpo no se puede
    serializar, se contesta el error del SDK en vez de reventar la ruta.
    """
    cuerpo = transforms.dump_json(comm_res.body)
    if isinstance(cuerpo, Exception):
        comm_res = comm_lib.CommResponse.from_error(client.logger, cuerpo)
        cuerpo = json.dumps(comm_res.body)

    return fastapi.responses.Response(
        content=cuerpo.encode("utf-8"),
        headers=comm_res.headers,
        status_code=comm_res.status_code,
    )
