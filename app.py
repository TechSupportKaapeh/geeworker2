"""Punto de entrada del worker de rásters de Terra.

El worker **se dispara por eventos de Inngest, no por HTTP de negocio**. Su
superficie HTTP es deliberadamente mínima:

    GET  /health        sonda de vida para el orquestador
    *    /api/inngest   lo monta `inngest.fast_api.serve`

No expone API de lectura. Las lecturas las sirve Geocore, que es el dueño del
catálogo y el único que aplica aislamiento de tenant. Ver `DECISIONS #23`
antes de agregar un endpoint acá.
"""

from dotenv import load_dotenv
from fastapi import FastAPI

from services.ee.ee_client import init_ee
from repositories.db_repository import init_db

load_dotenv()

from utils_pkg.logging_config import setup_logging

setup_logging()

from config import IS_PRODUCTION
from utils_pkg.arranque import registrar_arranque
from utils_pkg.conexiones import registrar_conexiones

import logging

logger = logging.getLogger(__name__)

# El reporte de configuración va **acá, al importar**, y no en el evento de
# startup donde estaba.
#
# El motivo lo dio un deploy real: `inngest.fast_api.serve()`, al final de este
# archivo, levantó por falta de `INNGEST_SIGNING_KEY`. Como eso ocurre durante
# el import, el evento `startup` nunca se dispara y el reporte —escrito
# justamente para explicar qué variable falta— no llegó a imprimirse. Salió el
# traceback pelado y nada más.
#
# Que esto sea seguro a nivel de módulo no es casualidad: `registrar_arranque`
# solo lee `os.environ` y loguea. Nada de I/O, nada que pueda fallar. Los
# chequeos que sí abren conexiones se quedan en `startup`, donde tienen
# permitido tardar y fallar.
registrar_arranque(logger, IS_PRODUCTION)

# Sin CORSMiddleware a propósito: ningún navegador le habla al worker. El front
# pega a Geocore. Un `allow_origins=["*"]` acá solo servía a los endpoints de
# lectura que se eliminaron.
# `docs_url=None, redoc_url=None, openapi_url=None`: sin endpoints de negocio no
# hay nada que documentar, y si el worker termina con URL pública —Inngest Cloud
# la exige— /docs sería superficie expuesta sin ningún uso.
app = FastAPI(
    title="Terra GeeWorker",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)


@app.on_event("startup")
def _startup():
    """Precalienta las dependencias. **Ningún fallo acá tumba el arranque.**

    `init_db` ya se trataba así; `init_ee` no, y eso produjo exactamente la
    patología que `DECISIONS #21` describe para los chequeos de salud: en el
    primer deploy a Railway faltaban las variables de GEE, `init_ee` levantó,
    uvicorn abortó, Railway reinició, y el ciclo se repitió — con los logs de
    varios procesos entrelazados y `/health` sin llegar a responder nunca.

    **Un deploy mal configurado tiene que poder arrancar para poder
    diagnosticarse.** Es el mismo criterio que `DECISIONS #16` tomó en Geocore:
    un secreto faltante degrada una funcionalidad, no tumba el servicio.

    Y acá cuesta todavía menos: los handlers llaman a `init_ee()` por su cuenta
    —una vez por step— así que esta llamada es un precalentamiento, no un
    requisito. Sin credenciales, cada invocación falla por separado y la
    reintenta Inngest, que es el comportamiento correcto.
    """
    for nombre, arranca in (("Google Earth Engine", init_ee),
                            # Crea `sentinel2_dates`, que es tabla del worker y
                            # EF Core no administra.
                            ("la base geodata", init_db)):
        try:
            arranca()
        except Exception as e:  # noqa: BLE001 - un precalentamiento atrapa todo a proposito
            logger.error(
                "No se pudo inicializar %s al arrancar: %s. El worker sigue "
                "levantando; las funciones que dependan de esto van a fallar "
                "de a una y las va a reintentar Inngest.", nombre, e,
            )

    # Y recién ahora, si las conexiones se hicieron de verdad.
    #
    # Va **después** de los precalentamientos porque no los reemplaza: `init_db`
    # crea `sentinel2_dates` y `init_ee` deja las credenciales listas. Corriendo
    # después, el chequeo de `geodata` puede además confirmar que esa tabla
    # quedó creada.
    #
    # Y hace falta porque el bucle de arriba **no puede distinguir** una
    # dependencia que anda de una que no: `init_db()` atrapa su propia excepción
    # y la loguea, así que este `try` nunca la ve y el precalentamiento termina
    # sin quejarse aunque la base esté caída. Un arranque que no dice nada
    # cuando algo está roto es peor que uno que falla.
    registrar_conexiones(logger)


@app.get("/health")
def health():
    """Sonda de vida. **Responde 200 aunque el worker esté degradado.**

    Es deliberado y va contra el instinto. Si esto devolviera 503 cuando falta
    `/api/inngest`, Railway marcaría el deploy como caído y lo reiniciaría en
    bucle — que es exactamente la patología que este archivo viene esquivando
    desde el primer deploy. Un deploy mal configurado tiene que quedarse
    arriba para poder diagnosticarse.

    El estado degradado se reporta en el cuerpo, no en el código HTTP.
    """
    if _ERROR_AL_MONTAR_INNGEST is None:
        return {"status": "ok", "inngest": "montado"}
    return {
        "status": "degradado",
        "inngest": "NO montado",
        "motivo": _ERROR_AL_MONTAR_INNGEST,
    }


import inngest.fast_api

from services.inngest_client import inngest_client
from services.inngest_handlers import all_functions

# `serve()` puede levantar, y si lo hace se lleva el proceso puesto.
#
# Es la **cuarta** vez que este repo se cae porque algo a nivel de módulo puede
# fallar, después de F.13, `init_ee` fuera del try y `os.makedirs` en `config`.
# Y esta vez el diagnóstico también falló: el reporte de arranque vive en
# `@app.on_event("startup")`, y ese evento nunca llega a dispararse si el
# módulo no termina de importarse. Salía el traceback y nada más.
#
# El caso concreto: en modo cloud sin `INNGEST_SIGNING_KEY`,
# `CommHandler.__init__` hace `raise errors.SigningKeyMissingError()`
# (`comm_lib/handler.py:62`). El comentario de `resolve_client_config` decía
# que no hacía falta levantar porque "el fallo cerrado ya lo garantiza el SDK:
# rechaza la petición". **Eso era falso**: el SDK no rechaza peticiones, se
# niega a construirse.
#
# Atraparlo no abre ninguna ventana insegura: sin `serve()` la ruta
# `/api/inngest` simplemente no existe y cualquier POST se va con 404. Se sigue
# fallando cerrado; lo único que cambia es que ahora el worker queda arriba
# para poder decir por qué.
_ERROR_AL_MONTAR_INNGEST = None
try:
    inngest.fast_api.serve(
        app,
        inngest_client,
        all_functions,
    )
except Exception as e:  # noqa: BLE001 - montar la ruta no puede tumbar el proceso
    _ERROR_AL_MONTAR_INNGEST = "%s: %s" % (type(e).__name__, e)
    logger.error(
        "NO se pudo montar /api/inngest (%s). El worker queda arriba y "
        "/health responde, pero **no recibe ningun evento**: la ruta no "
        "existe y todo POST se va con 404. Revisa el reporte de arranque de "
        "mas arriba, que dice que variables tiene el proceso.",
        _ERROR_AL_MONTAR_INNGEST,
    )
