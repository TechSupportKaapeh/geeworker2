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
    init_ee()
    try:
        # Crea `sentinel2_dates`, que es tabla del worker y EF Core no administra.
        # No es fatal: si la DB no responde al arrancar, los handlers fallan de a
        # uno y los reintenta Inngest, en vez de tumbar el proceso entero.
        init_db()
    except Exception as e:
        logger.error("No se pudo inicializar la DB al arrancar: %s", e)


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
