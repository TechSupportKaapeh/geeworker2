import os

from dotenv import load_dotenv

load_dotenv()

# Output directory
BASE_OUTPUT_DIR = os.getenv("BASE_OUTPUT_DIR", "./outputs")
os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)

# Database configuration (GeeWorker's own PostgreSQL/TimescaleDB)
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_NAME = os.getenv("DB_NAME", "terra")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASSWORD = os.getenv("DB_PASSWORD", "postgres")

# Entorno. **Un solo criterio para todo el proceso**, y se define aca arriba
# porque decide dos cosas de seguridad: si las credenciales de MinIO pueden caer
# a un default, y si se verifica la firma de Inngest.
#
# Hasta el 2026-09-02 habia dos criterios que no coincidian: este modulo trataba
# como desarrollo cualquier cosa distinta de development/dev/local, y
# `inngest_client.py` trataba como produccion **solo** el string exacto
# "production". Con `ENVIRONMENT=prod` el proceso quedaba en el peor de los dos
# mundos: sin credencial de desarrollo para MinIO (bien) y sin verificacion de
# firma de Inngest (grave).
#
# **Un valor desconocido cuenta como produccion**, no como desarrollo. Un typo en
# la variable tiene que apretar los controles, no soltarlos.
_DEV_ENVIRONMENTS = ("development", "dev", "local")
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
IS_DEVELOPMENT = ENVIRONMENT.strip().lower() in _DEV_ENVIRONMENTS
IS_PRODUCTION = not IS_DEVELOPMENT

# MinIO S3-compatible storage
MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")

# **Las credenciales no caen a un default fuera de desarrollo.** El default
# `minioadmin` es la credencial root del `docker-compose`, asi que un deploy al
# que le falte la variable no fallaba: se autenticaba como root contra el
# endpoint configurado, y en local funcionaba igual. Es el mismo patron de
# default silencioso que `DECISIONS #16` elimino en Geocore y que el tileserver
# elimino en `MAP_TOKEN_SECRET`: los dos fallaban abiertos.
#
# Fuera de desarrollo quedan en `None`, y `StorageService` falla cerrado
# nombrando la variable que falta. No se valida al importar a proposito: el
# worker tiene que poder arrancar y servir `/health` aunque el storage no este
# configurado (mismo criterio que `DECISIONS #16`).
_DEV_MINIO_CREDENTIAL = "minioadmin" if IS_DEVELOPMENT else None
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY") or _DEV_MINIO_CREDENTIAL
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY") or _DEV_MINIO_CREDENTIAL

_HOSTS_SIN_TLS = ("localhost", "127.0.0.1", "::1", "minio", "host.docker.internal")


def tls_por_defecto(endpoint: str) -> bool:
    """Decide si hablarle TLS a `endpoint` cuando `MINIO_SECURE` no esta puesta.

    **El default era `False` fijo, y eso fallaba abierto** (OWASP `W-2`): un
    deploy que se olvidara la variable mandaba la access key, el secret y el COG
    **en texto plano** contra el dominio publico. Y no fallaba: funcionaba.

    La direccion del default es la misma que la de `IS_PRODUCTION`: **lo
    desconocido se trata como lo mas seguro**. Solo los hosts que sabemos que
    sirven en texto plano —el `docker-compose` local y la red privada de
    Railway, que no hace TLS— arrancan sin TLS. Cualquier otro host se asume
    publico.

    Esto **elige un default**; que el endpoint y el modo no se contradigan lo
    verifica aparte `storage_service.validate_endpoint()`. Son dos trabajos
    distintos: uno adivina bien, el otro atrapa la contradiccion.
    """
    host = (endpoint or "").split(":")[0].strip().lower()
    if host in _HOSTS_SIN_TLS or host.endswith(".railway.internal"):
        return False
    return True


# TLS contra MinIO. El dominio publico de Railway va **sin puerto y con TLS**; el
# privado, **con `:9000` y sin TLS**, porque la red privada no hace TLS ni mapea
# puertos. Confundirlos es el error mas repetido de este despliegue, y
# equivocarse no da un error de SSL: da un `ConnectionReset`.
MINIO_SECURE = (
    os.getenv("MINIO_SECURE").lower() in ("true", "1", "yes")
    if os.getenv("MINIO_SECURE") is not None
    else tls_por_defecto(MINIO_ENDPOINT)
)

# Region del cliente de S3. **No es opcional aunque MinIO no tenga regiones.**
# Sin ella, minio-py resuelve la region llamando a `GetBucketLocation` antes de
# la primera operacion sobre el bucket (`minio/api.py::_get_region`), y eso
# exige `s3:GetBucketLocation`, un permiso que la subida real no usa: contra una
# policy de solo escritura el PUT ni siquiera se emite y el fallo sale como un
# `AccessDenied` sobre el bucket, que se lee como un problema de credenciales de
# `worker-rw` que no existe. Es `DECISIONS #21` del lado de escritura.
#
# Mismo nombre y mismo default que el tileserver (`terra_tiles/settings.py`),
# para que los dos lados del bucket no puedan divergir.
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")

# Bucket unico para los COG. Tiene que coincidir con `GeoData:MinioBucket` de
# Geocore: el arma la URL de tiles como `s3://{bucket}/{storage_key}` y si los
# dos lados no usan el mismo nombre, TiTiler apunta a un bucket inexistente.
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "terra-assets")

# Google Earth Engine
EE_SERVICE_ACCOUNT_EMAIL = os.getenv("EE_SERVICE_ACCOUNT_EMAIL")
EE_SERVICE_ACCOUNT_KEY_JSON = os.getenv("EE_SERVICE_ACCOUNT_KEY_JSON")

# Inngest configuration
INNGEST_EVENT_KEY = os.getenv("INNGEST_EVENT_KEY", "dev-local-key")
INNGEST_BASE_URL = os.getenv("INNGEST_BASE_URL", "http://localhost:8288")
INNGEST_SIGNING_KEY = os.getenv("INNGEST_SIGNING_KEY", "")

# Supported vegetation indices (Soil_pH removed)
SUPPORTED_INDICES = [
    "ndvi", "ndwi", "ndmi", "ndre", "evi", "savi", "lai",
    "gndvi", "reci", "gci", "svhi", "vegetation_health",
    "mndwi", "water_detection", "ndbi", "urban_index",
    "nsmi", "soil_moisture", "rgb", "change_detection"
]
