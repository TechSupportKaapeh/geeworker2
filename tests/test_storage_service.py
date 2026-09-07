"""Invariantes del cliente de object storage.

Las tres cosas que se fijan aca se rompieron o casi se rompieron de verdad, y
ninguna la agarra un compilador:

  1. La subida no pide permisos que no usa (`region`, PLAN.md F.12).
  2. Importar el modulo no abre conexiones (F.13).
  3. Una config invalida falla cerrado nombrando la variable, en vez de
     autenticarse con la credencial root del docker-compose.

El servidor doble de la prueba **solo acepta el PUT**, que son los permisos
exactos de una policy de solo escritura: si el cliente pide algo de mas, no hay
forma de que la prueba pase. Es el mismo criterio de paridad de permisos que
`DECISIONS #21`.

La version humana y explicada del punto 1 es `scripts/check_minio_region.py`;
esta es la que corre en cada `pytest`.
"""
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest
from minio.error import S3Error

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from services.storage_service import (
    StorageService,
    validate_credentials,
    validate_endpoint,
)

BUCKET = "terra-assets"


def _error_s3(codigo):
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Error><Code>{codigo}</Code><Message>{codigo}</Message>"
        f"<Resource>/{BUCKET}/k</Resource>"
        "<RequestId>test</RequestId><HostId>test</HostId></Error>"
    ).encode()


class _SoloAceptaPut(BaseHTTPRequestHandler):
    """Servidor S3 con los permisos justos de una policy de solo escritura."""

    protocol_version = "HTTP/1.1"
    peticiones: ClassVar[list] = []

    # Que contesta al HEAD de `stat_object`. El default modela una policy de
    # solo escritura —o MinIO devolviendo 403 en vez de 404 para no filtrar qué
    # keys existen—; los tests de `object_exists` lo cambian. La fixture lo
    # restaura, para que un test no arrastre el estado al siguiente.
    respuesta_head: ClassVar[tuple] = (403, "AccessDenied")

    def log_message(self, *_):
        pass

    def _registrar(self):
        type(self).peticiones.append((self.command, self.path))

    def _responder(self, codigo, cuerpo=b"", extra=None):
        self.send_response(codigo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.send_header("Content-Type", "application/xml")
        for clave, valor in (extra or {}).items():
            self.send_header(clave, valor)
        self.end_headers()
        if cuerpo:
            self.wfile.write(cuerpo)

    def do_GET(self):
        self._registrar()
        self._responder(403, _error_s3("AccessDenied"))

    def do_HEAD(self):
        self._registrar()
        codigo, _ = type(self).respuesta_head
        # Un HEAD no lleva cuerpo, y minio-py deduce el codigo S3 del status
        # cuando no hay XML que parsear: 404 -> NoSuchKey, 403 -> AccessDenied.
        # Verificado: los tests de `object_exists` distinguen los dos casos.
        self._responder(codigo)

    def do_PUT(self):
        self._registrar()
        self._leer_cuerpo()
        self._responder(200, extra={"ETag": '"0000"'})

    def do_POST(self):
        """Multipart: `CreateMultipartUpload` y `CompleteMultipartUpload`.

        Arriba de 5 MiB (`MIN_PART_SIZE`) minio-py deja de emitir un PUT unico y
        pasa a multipart. Las tres operaciones —crear, subir cada parte,
        completar— caen bajo `s3:PutObject` en la semantica de IAM de S3, asi que
        una policy de solo escritura las permite y este doble tambien.

        Eso ultimo esta modelado segun la especificacion, **no verificado contra
        el motor de policies de MinIO**: esa verificacion es parte de A-3.
        """
        self._registrar()
        self._leer_cuerpo()
        if "uploads" in self.path:
            cuerpo = (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b"<InitiateMultipartUploadResult>"
                b"<Bucket>terra-assets</Bucket><Key>k</Key>"
                b"<UploadId>upload-de-mentira</UploadId>"
                b"</InitiateMultipartUploadResult>"
            )
        else:
            cuerpo = (
                b'<?xml version="1.0" encoding="UTF-8"?>'
                b"<CompleteMultipartUploadResult>"
                b"<Location>http://x/terra-assets/k</Location>"
                b"<Bucket>terra-assets</Bucket><Key>k</Key>"
                b'<ETag>"0000"</ETag>'
                b"</CompleteMultipartUploadResult>"
            )
        self._responder(200, cuerpo)

    def _leer_cuerpo(self):
        largo = int(self.headers.get("Content-Length", 0))
        if largo:
            self.rfile.read(largo)


@pytest.fixture
def s3_de_mentira():
    """Levanta el doble en un puerto libre y devuelve (endpoint, peticiones)."""
    sonda = socket.socket()
    sonda.bind(("127.0.0.1", 0))
    puerto = sonda.getsockname()[1]
    sonda.close()

    _SoloAceptaPut.peticiones = []
    _SoloAceptaPut.respuesta_head = (403, "AccessDenied")
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), _SoloAceptaPut)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    try:
        yield f"127.0.0.1:{puerto}", _SoloAceptaPut.peticiones
    finally:
        servidor.shutdown()
        servidor.server_close()


def _servicio(endpoint):
    return StorageService(
        endpoint=endpoint,
        access_key="worker-rw",
        secret_key="de-mentira-el-doble-no-valida-firmas",
        secure=False,
        region="us-east-1",
        bucket=BUCKET,
    )


# --- 1. La subida no pide permisos de mas (F.12) --------------------------


def test_la_subida_emite_solo_el_put(s3_de_mentira):
    """Sin `region`, minio-py agrega un `GetBucketLocation` que pide un permiso
    que la subida real no usa: el PUT no llega a emitirse y el error apunta al
    bucket, como si faltaran credenciales de `worker-rw`."""
    endpoint, peticiones = s3_de_mentira
    _servicio(endpoint).upload_bytes("parcelas/x/2026-01-01_ndvi.tif", b"COG")

    assert [metodo for metodo, _ in peticiones] == ["PUT"]
    assert not any("location" in path for _, path in peticiones)


def test_sin_region_la_subida_falla_control_negativo(s3_de_mentira):
    """Control negativo: si esto pasara, el test de arriba no probaria nada."""
    endpoint, peticiones = s3_de_mentira
    servicio = StorageService(
        endpoint=endpoint,
        access_key="worker-rw",
        secret_key="de-mentira-el-doble-no-valida-firmas",
        secure=False,
        region=None,
        bucket=BUCKET,
    )

    with pytest.raises(Exception):  # noqa: B017 - la clase la elige minio-py
        servicio.upload_bytes("parcelas/x/2026-01-01_ndvi.tif", b"COG")

    assert any("location" in path for _, path in peticiones)


def test_construir_el_servicio_no_hace_io(s3_de_mentira):
    """El constructor arma objetos en memoria y nada mas."""
    endpoint, peticiones = s3_de_mentira
    _servicio(endpoint)
    assert peticiones == []


def test_un_cog_grande_va_por_multipart_y_tampoco_pide_la_region(s3_de_mentira):
    """El camino que un COG real va a tomar de verdad.

    Arriba de **5 MiB** (`MIN_PART_SIZE`, minio/helpers.py:50) minio-py deja de
    emitir un PUT unico: hace `POST ?uploads`, un `PUT ?partNumber=N` por trozo
    y `POST ?uploadId=`. Un COG de un rancho pesa mas que eso, asi que la prueba
    de 14 bytes de arriba **no ejercita el camino de produccion**; este si.

    El invariante que sobrevive a los dos caminos no es "una sola peticion",
    es **ninguna peticion pidiendo la region**.
    """
    endpoint, peticiones = s3_de_mentira
    seis_mib = b"\0" * (6 * 1024 * 1024)

    _servicio(endpoint).upload_bytes("parcelas/x/2026-01-01_ndvi.tif", seis_mib)

    metodos = sorted(metodo for metodo, _ in peticiones)
    assert metodos == ["POST", "POST", "PUT", "PUT"], peticiones
    assert not any("location" in path for _, path in peticiones)
    # Y que sea multipart de verdad, no dos PUT sueltos.
    assert any("uploads" in path for _, path in peticiones)
    assert any("partNumber" in path for _, path in peticiones)


# --- 2. Importar el modulo no abre conexiones (F.13) ----------------------


def test_importar_el_modulo_no_abre_conexiones():
    """Corre en un proceso aparte porque el import se cachea por interprete.

    El socket se sabotea antes del import: si algo intentara conectarse, el
    import fallaria con el mensaje que pusimos nosotros. Antes de F.13 esto
    fallaba, porque el singleton de modulo llamaba a `ensure_bucket()`.
    """
    codigo = (
        "import socket, sys\n"
        "def prohibido(*a, **k):\n"
        "    raise AssertionError('el import abrio una conexion')\n"
        "socket.socket.connect = prohibido\n"
        "socket.create_connection = prohibido\n"
        "import services.storage_service\n"
        "print('sin conexiones')\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=str(RAIZ), capture_output=True, text=True, timeout=120, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    assert "sin conexiones" in resultado.stdout


# --- 3. Una config invalida falla cerrado --------------------------------


@pytest.mark.parametrize("access_key,secret_key,esperado", [
    (None, "s", "MINIO_ACCESS_KEY"),
    ("a", None, "MINIO_SECRET_KEY"),
    (None, None, "MINIO_ACCESS_KEY"),
    ("", "", "MINIO_ACCESS_KEY"),
    ("a", "s", None),
])
def test_validate_credentials(access_key, secret_key, esperado):
    motivo = validate_credentials(access_key, secret_key)
    if esperado is None:
        assert motivo is None
    else:
        assert motivo and esperado in motivo


@pytest.mark.parametrize("endpoint,secure,esperado", [
    # Las formas de confundir los dos dominios de Railway. **Son cuatro, no
    # tres**: el privado necesita puerto y va sin TLS; el publico va sin puerto
    # y con TLS. Hasta el 2026-09-07 faltaba justo la del puerto en el publico,
    # y aparecio porque el `.env` real la tenia — `…up.railway.app:9000`.
    ("minio.railway.internal", False, "puerto explicito"),
    ("minio.railway.internal:9000", True, "sin TLS"),
    ("bucket-x.up.railway.app:9000", False, "sin puerto"),
    ("bucket-x.up.railway.app:9000", True, "sin puerto"),
    ("bucket-x.up.railway.app", False, "con TLS"),
    ("http://minio.railway.internal:9000", False, "no lleva esquema"),
    ("minio.railway.internal:9000/", False, "barra final"),
    ("", False, "falta MINIO_ENDPOINT"),
    # Las que si tienen que pasar.
    ("minio.railway.internal:9000", False, None),
    ("bucket-x.up.railway.app", True, None),
    ("localhost:9000", False, None),
])
def test_validate_endpoint(endpoint, secure, esperado):
    motivo = validate_endpoint(endpoint, secure)
    if esperado is None:
        assert motivo is None
    else:
        assert motivo and esperado in motivo


def test_el_constructor_falla_cerrado_sin_credenciales():
    """Nombra la variable a corregir en vez de autenticarse como root."""
    with pytest.raises(RuntimeError, match="MINIO_ACCESS_KEY"):
        StorageService(endpoint="localhost:9000", access_key=None,
                       secret_key=None, secure=False, region="us-east-1",
                       bucket=BUCKET)


def test_el_constructor_falla_cerrado_con_endpoint_incoherente():
    with pytest.raises(RuntimeError, match="MINIO_SECURE=True"):
        StorageService(endpoint="bucket-x.up.railway.app", access_key="a",
                       secret_key="s", secure=False, region="us-east-1",
                       bucket=BUCKET)

# --- 4. `object_exists` no confunde "no puedo saber" con "no existe" (W-6) ---


def test_object_exists_levanta_si_no_pudo_averiguar(s3_de_mentira):
    """`AccessDenied` no es "no existe": es "no se pudo concluir".

    El doble niega el HEAD, que es lo que hace una policy de solo escritura — y
    tambien lo que hace MinIO cuando devuelve 403 en lugar de 404 para no
    filtrar qué keys existen.

    Antes esto devolvia `False`. **E.9 quiere saltear el calculo cuando la capa
    ya existe**; apoyado en ese `False`, un error de permisos o de red disparaba
    un recalculo completo en GEE, o una sobrescritura decidida sobre un falso
    negativo. Es la regla de `DECISIONS #21`: no haber podido concluir no es
    haber concluido.
    """
    endpoint, _ = s3_de_mentira
    with pytest.raises(S3Error) as capturada:
        _servicio(endpoint).object_exists("parcelas/x/2026-01-01_ndvi.tif")
    assert capturada.value.code == "AccessDenied"


def test_object_exists_devuelve_false_solo_ante_nosuchkey(s3_de_mentira):
    """El unico caso en que `False` es la respuesta correcta."""
    endpoint, _ = s3_de_mentira
    _SoloAceptaPut.respuesta_head = (404, "NoSuchKey")

    assert _servicio(endpoint).object_exists("parcelas/x/no-esta.tif") is False


def test_object_exists_devuelve_true_si_el_objeto_esta(s3_de_mentira):
    endpoint, _ = s3_de_mentira
    _SoloAceptaPut.respuesta_head = (200, None)

    assert _servicio(endpoint).object_exists("parcelas/x/esta.tif") is True


# --- 5. La superficie de la clase -----------------------------------------


def test_no_hay_metodo_de_url_firmada():
    """`get_presigned_url()` se borro (W-7): fallaba abierto y no la usaba nadie.

    Devolvia una URL **sin firma y sin vencimiento** cuando el firmado fallaba:
    en el mejor caso un 403 que confunde, y si el bucket fuera publico, un
    enlace permanente y anonimo en lugar de uno de 24 h.

    El test existe para que no vuelva por costumbre. Quien necesite URLs
    firmadas tiene que decidir a proposito quien las emite y con que
    vencimiento, no heredar un fallback que devuelve algo que parece firmado y
    no lo es.
    """
    assert not hasattr(StorageService, "get_presigned_url")


# --- 6. El default de TLS falla cerrado (W-2) -----------------------------


@pytest.mark.parametrize("endpoint,esperado", [
    # Los unicos hosts que sabemos que sirven en texto plano.
    ("localhost:9000", False),
    ("127.0.0.1:9000", False),
    ("minio:9000", False),
    ("host.docker.internal:9000", False),
    ("minio.railway.internal:9000", False),
    # Todo lo demas se asume publico, o sea con TLS. **Es la direccion que
    # importa**: el default anterior era False fijo, asi que un deploy que se
    # olvidara la variable mandaba la access key y el secret en texto plano
    # contra el dominio publico — y no fallaba, funcionaba.
    ("bucket-x.up.railway.app", True),
    ("minio.terra.com", True),
    ("s3.amazonaws.com", True),
    ("", True),
])
def test_el_default_de_tls_se_deduce_del_host(endpoint, esperado):
    from config import tls_por_defecto

    assert tls_por_defecto(endpoint) is esperado


def test_el_default_de_tls_ignora_mayusculas_y_espacios():
    from config import tls_por_defecto

    assert tls_por_defecto("  LOCALHOST:9000  ") is False


# --- 7. Importar `config` no toca el sistema de archivos ------------------


def test_importar_config_no_crea_carpetas(tmp_path):
    """`config` se importa **antes** de que `setup_logging()` corra.

    Antes tenia `os.makedirs(BASE_OUTPUT_DIR)` a nivel de modulo, y reventó en el
    primer deploy a Railway: con `BASE_OUTPUT_DIR=../outputs` heredado del `.env`
    local, el contenedor intentaba crear `/outputs` —fuera de `/app`— como
    usuario no-root, y el fallo salia como un traceback crudo, sin contexto y sin
    el nombre de la variable.

    Es la tercera vez que el mismo patron —I/O al importar— tumba algo en este
    repo: el singleton de `storage_service` (F.13), el `init_ee()` fuera del try
    en `app.py`, y esto. **Un import no deberia poder matar el proceso.**

    No se perdio nada: `utils_pkg.io.ensure_outputs_dir()` ya crea la carpeta, y
    la llaman los tres sitios que escriben ahi.
    """
    destino = tmp_path / "no-deberia-existir"
    codigo = (
        "import os, sys\n"
        f"os.environ['BASE_OUTPUT_DIR'] = r'{destino}'\n"
        "import config\n"
        f"assert not os.path.exists(r'{destino}'), 'el import creo la carpeta'\n"
        "print('sin efectos en disco')\n"
    )
    resultado = subprocess.run(
        [sys.executable, "-c", codigo],
        cwd=str(RAIZ), capture_output=True, text=True, timeout=120, check=False,
    )
    assert resultado.returncode == 0, resultado.stderr
    assert "sin efectos en disco" in resultado.stdout
