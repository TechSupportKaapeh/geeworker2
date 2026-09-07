import io
import logging
from functools import lru_cache

from minio import Minio
from minio.error import S3Error

from config import (
    AWS_REGION,
    MINIO_ACCESS_KEY,
    MINIO_BUCKET,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
)

logger = logging.getLogger(__name__)


def validate_credentials(access_key, secret_key):
    """Devuelve el motivo por el que las credenciales no sirven, o `None`.

    Funcion pura: recibe los valores en vez de leer la config al importarse,
    para poder probarla sin recargar modulos. Es la leccion que el tileserver
    ya aprendio con `crear_validador_de_token`.
    """
    faltantes = [
        nombre
        for nombre, valor in (("MINIO_ACCESS_KEY", access_key),
                              ("MINIO_SECRET_KEY", secret_key))
        if not valor
    ]
    if faltantes:
        return (
            f"falta {' y '.join(faltantes)}. Fuera de desarrollo no hay default: "
            "el anterior era la credencial root del docker-compose y hacia que "
            "un deploy mal configurado se autenticara como root en vez de fallar"
        )
    return None


def validate_endpoint(endpoint, secure):
    """Devuelve el motivo por el que el endpoint no puede funcionar, o `None`.

    Detecta sin tocar la red las formas de confundir los dos dominios de
    Railway, que es el error mas repetido de este despliegue: el privado lleva
    puerto explicito y va sin TLS, el publico va sin puerto y con TLS. La red
    privada no hace mapeo de puertos, asi que sin `:9000` se asume el 80, donde
    MinIO no escucha.
    """
    if not endpoint:
        return "falta MINIO_ENDPOINT"
    if "://" in endpoint:
        return (
            f"MINIO_ENDPOINT no lleva esquema, es `host:puerto`: '{endpoint}'. "
            "El esquema lo decide MINIO_SECURE"
        )
    if endpoint.endswith("/"):
        return f"MINIO_ENDPOINT no lleva barra final: '{endpoint}'"
    if ".railway.internal" in endpoint:
        if ":" not in endpoint:
            return (
                f"el dominio privado de Railway necesita puerto explicito "
                f"(`{endpoint}:9000`): la red privada no mapea puertos y sin el "
                "se asume el 80, donde MinIO no escucha"
            )
        if secure:
            return (
                "el dominio privado de Railway va sin TLS: MINIO_SECURE=False. "
                "Hablarle TLS a un puerto de texto plano no da un error de SSL, "
                "da un ConnectionReset que no dice cual es el problema"
            )
    elif ".up.railway.app" in endpoint:
        if ":" in endpoint:
            # El edge de Railway escucha en 443 y hace de proxy; el dominio
            # publico **no expone el puerto del servicio**. Un `:9000` ahi manda
            # el trafico a un puerto que el edge no atiende. Es la otra mitad de
            # la confusion entre los dos dominios —el privado SI necesita el
            # puerto— y hasta el 2026-09-07 este validador solo detectaba una.
            host, _, puerto = endpoint.partition(":")
            return (
                f"el dominio publico de Railway va **sin puerto**: '{host}', no "
                f"'{endpoint}'. El edge escucha en 443 y hace de proxy; el "
                f"puerto {puerto} es la convencion del dominio *privado*"
            )
        if not secure:
            return (
                "el dominio publico de Railway va con TLS: MINIO_SECURE=True. "
                "Sin el, la access key y el secret viajan en texto plano"
            )
    return None


class StorageService:
    """Cliente de object storage para los COG.

    **Un solo bucket, y las funciones de subida devuelven la key pelada.**
    Geocore arma la URL de TiTiler concatenando `s3://{bucket}/{StorageKey}`
    (`LayersController.cs`), asi que lo que se guarda en `layers.storage_key`
    tiene que ser solo la key. Devolver la URI completa producia
    `s3://terra-assets/s3://rasters/...` y TiTiler no resolvia nada.

    Lo que antes eran tres buckets (`rasters`, `exports`, `kml-uploads`) ahora
    son prefijos de la key dentro del bucket unico:

        ranchos/{ranchoId}/{fecha}_{indice}.tif     COG procesado
        ranchos/{ranchoId}/{fecha}_original.tif     COG crudo del proveedor
        parcelas/{parcelaId}/{fecha}_{indice}.tif
        heatmaps/{parcelaId}/{fecha}_{indice}.tif   pedidos on-demand
        exports/{parcelaId}/{fecha}_{indice}.{fmt}

    `bucket_name` sigue existiendo como override puntual, pero por defecto todo
    va al bucket configurado: es el que Geocore tiene en `GeoData:MinioBucket`,
    y tienen que coincidir o los tiles apuntan a un bucket que no existe.
    """

    def __init__(self, endpoint=MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                 secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE,
                 region=AWS_REGION, bucket=MINIO_BUCKET):
        """Construye el cliente. **No hace I/O**: ver `get_storage_service`.

        Los parametros tienen la config como default en vez de leerla del
        modulo, para que las pruebas puedan construirlo con otros valores sin
        recargar `config`.
        """
        for motivo in (validate_credentials(access_key, secret_key),
                       validate_endpoint(endpoint, secure)):
            if motivo:
                # Falla cerrado y nombra la variable a corregir. Es seguro
                # levantar aca porque la construccion es perezosa: revienta
                # cuando alguien necesita el storage, no al importar el modulo.
                raise RuntimeError(f"Configuracion de MinIO invalida: {motivo}")

        self.bucket = bucket
        self.client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            # `region` NO es redundante, aunque MinIO no tenga regiones. Sin
            # ella minio-py hace un `GetBucketLocation` antes de la primera
            # operacion sobre el bucket, que pide un permiso que la subida real
            # no usa: el PUT no se emite y el error apunta al bucket, como si
            # faltaran credenciales. Lo cuida `scripts/check_minio_region.py`.
            region=region,
        )

    def ensure_bucket(self):
        """Crea el bucket si no existe. **Tarea de despliegue, no de arranque.**

        Ya no se llama desde `__init__`: `bucket_exists()` pide `s3:ListBucket`,
        un permiso que la subida real no usa y que una policy de solo escritura
        no tiene por que darle a `worker-rw` — el mismo criterio de paridad de
        permisos que `DECISIONS #21`. Crear el bucket se hace una vez, al
        desplegar, no en cada arranque del proceso.
        """
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
            logger.info("Bucket '%s' creado en el object storage.", self.bucket)

    def upload_file(self, object_name: str, file_path: str,
                    content_type: str = "application/octet-stream",
                    bucket_name: str | None = None) -> str:
        """Sube un archivo de disco y devuelve la **key**, no una URI.

        El valor que devuelve es exactamente lo que va a `layers.storage_key`.
        """
        self.client.fput_object(bucket_name or self.bucket, object_name, file_path,
                                content_type=content_type)
        return object_name

    def upload_bytes(self, object_name: str, data: bytes,
                     content_type: str = "application/octet-stream",
                     bucket_name: str | None = None) -> str:
        """Sube bytes en memoria y devuelve la **key**, no una URI."""
        self.client.put_object(
            bucket_name or self.bucket,
            object_name,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )
        return object_name

    def download_file(self, object_name: str, file_path: str, bucket_name: str | None = None):
        """Descarga un objeto a una ruta local."""
        self.client.fget_object(bucket_name or self.bucket, object_name, file_path)

    def get_object_bytes(self, object_name: str, bucket_name: str | None = None) -> bytes:
        """Lee un objeto directamente a memoria."""
        response = None
        try:
            response = self.client.get_object(bucket_name or self.bucket, object_name)
            return response.read()
        finally:
            if response:
                response.close()
                response.release_conn()

    def object_exists(self, object_name: str, bucket_name: str | None = None) -> bool:
        """`True` si el objeto esta, `False` si no esta, y **levanta si no se pudo
        averiguar**.

        La version anterior era `except Exception: return False`, que mezclaba
        tres cosas distintas en una sola respuesta: el objeto no existe, no
        tenemos permiso para mirar, y la red esta caida. Las dos ultimas no son
        "no existe": son "no se pudo concluir", y devolver `False` ahi convierte
        un fallo de infraestructura en un dato falso.

        Importa para `PLAN.md` E.9, que quiere saltear el calculo si la capa ya
        existe. Apoyado en la version vieja, un error transitorio hacia que E.9
        concluyera "no existe" y disparara un recalculo completo en GEE —cuota
        gastada— o una sobrescritura decidida sobre un falso negativo.

        Es la regla de `DECISIONS #21`: no haber podido concluir no es haber
        concluido. Levantar deja que Inngest reintente, que es lo correcto para
        un fallo transitorio.
        """
        try:
            self.client.stat_object(bucket_name or self.bucket, object_name)
            return True
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                return False
            # `AccessDenied` incluido: MinIO puede devolverlo en lugar de 404
            # para no filtrar qué keys existen, asi que tampoco significa "no
            # existe". Se propaga para que el llamador no confunda una policy
            # incompleta con un bucket vacio.
            raise

    # `get_presigned_url()` se borro el 2026-09-04 (OWASP `W-7`). Nadie la
    # llamaba —era el unico metodo de esta clase sin call sites— y fallaba
    # abierto: si `presigned_get_object` levantaba, devolvia
    # `{protocolo}://{endpoint}/{bucket}/{key}`, o sea una URL **sin firma y sin
    # vencimiento**, en lugar de una firmada de 24 h. En el mejor caso da 403 y
    # confunde; si el bucket fuera publico, entrega un enlace permanente y
    # anonimo.
    #
    # Se borro en vez de arreglarse, con el criterio de `DECISIONS #23`: codigo
    # sin llamadas no se arregla, se elimina — y ademas quien vaya a necesitar
    # URLs firmadas tiene que decidir a proposito quien las emite y con que
    # vencimiento, no heredar un fallback que devuelve algo que parece una URL
    # firmada y no lo es.


@lru_cache(maxsize=1)
def get_storage_service() -> StorageService:
    """Devuelve el `StorageService` compartido, construyendolo al primer uso.

    **Uno solo, y perezoso.** El motivo de que sea uno solo es compartir el pool
    de conexiones de urllib3 que vive dentro del cliente de minio-py: crear un
    cliente por subida tira el pool y vuelve a pagar el saludo TCP (y el TLS)
    en cada archivo.

    El motivo de que sea perezoso es que antes era una instancia a nivel de
    modulo, con `ensure_bucket()` en el constructor: `import app` abria un
    socket y reintentaba si no habia nadie del otro lado. Eso ataba el import a
    que el storage estuviera vivo, congelaba la config al importar, y hacia que
    la suite tardara ~33 s para 4 tests que no tocan la red.
    `repositories/db_repository.get_connection()` ya tenia la forma correcta.
    """
    return StorageService()
