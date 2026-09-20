import json
import os

import ee
from dotenv import load_dotenv
from google.oauth2 import service_account

# Cargar variables del archivo .env automáticamente
load_dotenv()

SA_EMAIL = os.getenv("EE_SERVICE_ACCOUNT_EMAIL")
SA_KEY_JSON = os.getenv("EE_SERVICE_ACCOUNT_KEY_JSON")

# Import shared config


def init_ee():
    if not SA_EMAIL or not SA_KEY_JSON:
        raise RuntimeError("Faltan EE_SERVICE_ACCOUNT_EMAIL o EE_SERVICE_ACCOUNT_KEY_JSON en .env")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(SA_KEY_JSON),
        scopes=[
            "https://www.googleapis.com/auth/earthengine.readonly",
            "https://www.googleapis.com/auth/devstorage.read_write",
            "https://www.googleapis.com/auth/drive"
        ],
        subject=SA_EMAIL
    )
    ee.Initialize(creds)


# M.6.2b: se borró todo lo que quedaba de la capa vieja de GEE. Este módulo es
# hoy **sólo las credenciales**: `init_ee()`, que usan el arranque, los handlers
# del pipeline y el chequeo de conexiones.
#
# Lo que se fue, y por qué ninguno se echa de menos (`ARQUITECTURA_PIPELINE` §8
# y §9):
#
# - `get_sentinel2_collection`, el constructor de colecciones. Lo reemplaza
#   `pipeline/etapas/fuente.py`, que divide las bandas por 10.000 —lo que la
#   vieja no hacía, y por eso su EVI y su SAVI estaban mal— y toma sus
#   parámetros de la receta en vez de tenerlos escritos.
# - `apply_scsc`, la "corrección topográfica SCS+C". **No era SCS+C**: le faltaba
#   `cos(pendiente)` en el numerador y usaba un `C` fijo de 0,1 en lugar de uno
#   por banda sacado de una regresión. Para índices normalizados el efecto de la
#   iluminación se cancela casi entero en el cociente, así que la receta v1 no
#   lleva corrección topográfica (§8.4). Si alguna vez hace falta, se implementa
#   bien y se valida.
# - `check_roi_coverage`, que descartaba las pasadas con menos del 50 % del ROI
#   limpio. En un compuesto mensual esa pasada aporta los píxeles que **sí** están
#   limpios: tirarla agrega nulos, que es lo contrario de lo que se busca (§8.2).
#   La calidad se mide al final, con la `cobertura` del mes.
# - `add_cloud_probability` y `mask_s2cloudless_and_shadows`. La máscara vive en
#   `pipeline/etapas/nubes.py`, con los mismos parámetros pero **en una proyección
#   fija a la escala de la receta**: `directionalDistanceTransform` mide en
#   píxeles del pedido, así que sin eso el mapa y las estadísticas podían salir
#   con máscaras distintas (`DECISIONS #39`).
