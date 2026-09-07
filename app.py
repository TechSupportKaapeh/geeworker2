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

import logging

logger = logging.getLogger(__name__)

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


@app.get("/health")
def health():
    return {"status": "ok"}


import inngest.fast_api

from services.inngest_client import inngest_client
from services.inngest_handlers import all_functions

inngest.fast_api.serve(
    app,
    inngest_client,
    all_functions,
)
