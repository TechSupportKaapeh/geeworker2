"""Cliente de Inngest del worker.

**`/api/inngest` es la unica superficie publica del worker**, y lo que la
protege no es un token nuestro: es la verificacion de firma HMAC que hace el
SDK con `INNGEST_SIGNING_KEY`. Por eso este archivo es codigo de seguridad
aunque parezca configuracion.

El detalle que importa, verificado en el paquete instalado
(`inngest/_internal/net.py::_validate_sig`):

    if mode == server_lib.ServerKind.DEV_SERVER:
        return None      # <- no valida NADA

La verificacion esta enteramente condicionada al modo. En modo dev el SDK
acepta cualquier POST sin firma, o sea que cualquiera puede invocar
`process_parcela` con el payload que quiera: ids de otro tenant, geometrias
arbitrarias, gasto de cuota de GEE. No hay una segunda capa detras.

Y el default del SDK ya es el seguro: `_get_mode` devuelve CLOUD cuando no se le
pasa `is_production`. La version anterior de este archivo pasaba
`is_production=os.getenv("ENVIRONMENT", "development") == "production"`, asi que
**cualquier valor distinto del string exacto "production" —incluido "prod", un
typo, o la variable ausente— apagaba la verificacion**. Era mas permisivo que no
configurar nada.
"""
import logging
import os

import inngest

from config import (
    INNGEST_BASE_URL,
    INNGEST_EVENT_KEY,
    INNGEST_SELF_HOSTED_URL,
    INNGEST_SIGNING_KEY,
    IS_PRODUCTION,
)

logger = logging.getLogger(__name__)

APP_ID = "geeworker"

# El default del docker-compose. Sirve para hablarle al `inngest dev` local y no
# vale nada contra Inngest Cloud, asi que en produccion no se manda.
DEV_EVENT_KEY = "dev-local-key"


def resolve_client_config(*, is_production, base_url, event_key, signing_key, self_hosted_url="",
                          serve_origin=""):
    """Arma los kwargs del cliente y lista los problemas de configuracion.

    Funcion pura: recibe los valores en vez de leer la config al importarse,
    para poder probarla sin recargar modulos ni tocar el entorno. Mismo criterio
    que `storage_service.validate_endpoint`.

    Devuelve `(kwargs, problemas)`. Los problemas no impiden construir el
    cliente —ver el comentario de abajo sobre por que no se levanta— pero se
    loguean como ERROR al arrancar.
    """
    kwargs = {"app_id": APP_ID, "is_production": is_production}
    problemas = []

    if signing_key:
        kwargs["signing_key"] = signing_key

    if is_production:
        # `api_base_url` y `event_api_base_url` NO se fijan en produccion
        # (PLAN.md F.3). Forzarlos a INNGEST_BASE_URL, cuyo default es
        # `http://localhost:8288`, hacia que un deploy sin esa variable le
        # hablara a su propio localhost en vez de a Inngest Cloud.
        #
        # **Pero no pasarlos no alcanza** (corregido el 2026-09-19): sin el
        # parametro, el SDK **lee el entorno por su cuenta**
        # (`client_lib/utils.py`: INNGEST_API_BASE_URL, INNGEST_BASE_URL e
        # INNGEST_DEV, en ese orden) y solo si no hay ninguna usa Cloud. Con
        # `INNGEST_BASE_URL=https://inn.gs` en el worker, el sync fallaba con
        # 404. Por eso el reporte de arranque marca esas variables como
        # problema si estan en produccion (`utils_pkg/arranque.py`).
        if not signing_key:
            problemas.append(
                # Solo ASCII en los mensajes de log: los lee una consola de
                # Railway, y un em-dash sale como '?' en una terminal cp1252.
                "falta INNGEST_SIGNING_KEY. En modo cloud el SDK ni siquiera "
                "monta /api/inngest: CommHandler.__init__ levanta "
                "SigningKeyMissingError. app.py lo atrapa para que el worker "
                "quede arriba y se pueda diagnosticar, pero la ruta no existe y "
                "todo POST se va con 404"
            )
        if event_key and event_key != DEV_EVENT_KEY:
            kwargs["event_key"] = event_key
        else:
            problemas.append(
                "falta INNGEST_EVENT_KEY, o quedo con el valor de desarrollo. "
                "El worker no va a poder emitir eventos. Desde M.4.5 no emite "
                "ninguno (el alta del rancho escribe su capa en `layers` sin "
                "pasar por terra/raster.ingested), pero un handler que lo haga "
                "va a fallar"
            )

        # Inngest autohospedado (2026-10-04). El modo sigue siendo CLOUD
        # (`is_production`), y eso es lo que importa: la verificacion de firma
        # depende del modo, no de la URL (`client.py::_get_mode`). El servidor
        # propio firma con la misma INNGEST_SIGNING_KEY que se le configura.
        # Se pasa explicita, y no por las variables que el SDK lee solo, para
        # que apuntar a otro servidor sea una decision a la vista y no un
        # resto de configuracion (el caso de inn.gs del 2026-09-18).
        if self_hosted_url:
            problema = _problema_de_url_propia(self_hosted_url)
            if problema:
                problemas.append(problema)
            else:
                url = self_hosted_url.rstrip("/")
                kwargs["api_base_url"] = url
                kwargs["event_api_base_url"] = url

        # La URL con la que el worker se registra (2026-10-04). Detras del proxy
        # de Railway el pedido de sync llega como http, y el SDK registra esa
        # URL: Inngest le pega a http://, Railway contesta 301 a https y el step
        # no se ejecuta, sin error a la vista. INNGEST_SERVE_ORIGIN la fija (el
        # SDK la lee solo, `config_lib.get_serve_origin`); aca solo se avisa si
        # es http. No cambia que acepta el worker: la firma se verifica igual.
        if serve_origin.startswith("http://"):
            problemas.append(
                "INNGEST_SERVE_ORIGIN es http: Railway contesta 301 a https y "
                "Inngest no ejecuta ningun step. Tiene que ser https://<dominio>"
            )
    else:
        kwargs["api_base_url"] = base_url
        kwargs["event_api_base_url"] = base_url
        kwargs["event_key"] = event_key or DEV_EVENT_KEY

    return kwargs, problemas


def _problema_de_url_propia(url):
    """Por que `INNGEST_SELF_HOSTED_URL` no sirve, o `None` si sirve.

    Una URL que no se puede usar **no se pasa**: el worker sigue con Inngest
    Cloud y el reporte de arranque lo dice. Pasarla a medias haria que el sync
    fuera a parar a cualquier lado.
    """
    if not url.startswith(("http://", "https://")):
        return (f"INNGEST_SELF_HOSTED_URL no es una URL http(s): {url[:80]!r}. Se "
                "sigue con Inngest Cloud")
    host = url.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0].lower()
    if host in ("inn.gs", "api.inngest.com") or host.endswith(".inngest.com"):
        return (f"INNGEST_SELF_HOSTED_URL apunta a Inngest Cloud ({host}): para "
                "Cloud se borra la variable. Se sigue con Inngest Cloud")
    if host in ("localhost", "127.0.0.1"):
        return (f"INNGEST_SELF_HOSTED_URL apunta al propio contenedor ({host}): en "
                "Railway es el nombre del servicio, *.railway.internal. Se sigue "
                "con Inngest Cloud")
    return None


_kwargs, _problemas = resolve_client_config(
    is_production=IS_PRODUCTION,
    base_url=INNGEST_BASE_URL,
    event_key=INNGEST_EVENT_KEY,
    # El SDK tambien leeria la variable por su cuenta (`client.py:101`), pero se
    # pasa explicita: asi el valor sale de la misma config que el resto y no
    # depende de que `load_dotenv()` haya corrido antes de este import.
    signing_key=INNGEST_SIGNING_KEY or None,
    self_hosted_url=INNGEST_SELF_HOSTED_URL,
    serve_origin=os.getenv("INNGEST_SERVE_ORIGIN", ""),
)

for _problema in _problemas:
    # Se loguea y se sigue, no se levanta. Mismo criterio que `DECISIONS #16`:
    # el proceso tiene que poder arrancar y responder /health para que se pueda
    # diagnosticar.
    #
    # **Correccion del 2026-09-08.** Aca decia que no hacia falta levantar
    # porque "el fallo cerrado ya lo garantiza el SDK: en modo cloud sin firma
    # valida rechaza la peticion". Era falso, y costo un deploy: el SDK no
    # rechaza peticiones, se niega a construirse
    # (`comm_lib/handler.py:62`), y como `serve()` se llama a nivel de modulo
    # eso mata el proceso durante el import. El razonamiento de no levantar
    # seguia siendo el correcto; lo que estaba mal era suponer que la
    # dependencia se comportaba igual. Quien garantiza el arranque ahora es el
    # try/except de `app.py`, no una suposicion sobre el SDK.
    logger.error("Configuracion de Inngest: %s", _problema)

inngest_client = inngest.Inngest(**_kwargs)

if IS_PRODUCTION:
    logger.info(
        "Inngest en modo cloud: se verifica la firma de cada peticion a "
        "/api/inngest."
    )
else:
    logger.warning(
        "Inngest en modo dev (ENVIRONMENT=%s): **no se verifica la firma** de "
        "las peticiones a /api/inngest. Correcto en local; en un servicio "
        "alcanzable desde internet significa que cualquiera puede invocar los "
        "handlers.",
        os.getenv("ENVIRONMENT", "development"),
    )
