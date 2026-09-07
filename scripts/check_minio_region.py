"""Verifica que el worker suba a MinIO sin pedir permisos que no necesita.

QUE HACE
--------
Levanta un servidor S3 de mentira en localhost que solo registra que le piden,
apunta el `StorageService` real contra el, y mira **la secuencia exacta de
peticiones** que salen al subir un archivo.

No necesita MinIO, ni credenciales, ni red. Lo que se observa es el orden de las
peticiones, que es donde vive el problema que este script cuida.

PARA QUE SIRVE
--------------
El cliente de minio-py, si no se le pasa `region`, resuelve la region llamando a
`GetBucketLocation` antes de la primera operacion sobre un bucket
(`minio/api.py::_get_region`). Esa llamada exige el permiso
`s3:GetBucketLocation`, que la subida real **no usa**.

Contra una policy de solo escritura eso falla con:

    S3Error: code: AccessDenied, resource: /terra-assets/

que apunta al bucket y se lee como un problema de credenciales de `worker-rw`
que no existe. El PUT ni siquiera llega a emitirse. Es el mismo falso negativo
que costo una sesion entera del lado del tileserver (`DECISIONS #21`), ahora del
lado de escritura.

QUE LOGRAMOS
------------
Que ese modo de fallo no pueda volver en silencio. El parametro `region` parece
redundante —MinIO ni siquiera tiene regiones— y es exactamente el tipo de linea
que alguien borra por "limpieza". Si se borra, este script se pone en rojo antes
de que el fallo aparezca contra la infraestructura real, disfrazado de problema
de permisos.

LAS DOS COMPROBACIONES
----------------------
  1. subida        La subida emite EXACTAMENTE una peticion, y es el PUT.
                   Se corre contra un servidor que niega todo menos el PUT: si
                   el cliente pide algo de mas, no hay forma de que pase.

                   OJO con el alcance: el objeto de prueba pesa 14 bytes. Arriba
                   de 5 MiB (`MIN_PART_SIZE`, minio/helpers.py:50) `put_object`
                   deja de emitir un PUT unico y pasa a multipart: `POST
                   ?uploads`, un `PUT ?partNumber=N` por trozo y `POST
                   ?uploadId`. Un COG real pesa mas que eso, asi que este caso
                   NO es el camino de escritura de produccion. Pendiente subir
                   tambien 6 MiB y exigir que ninguno de los dos caminos pida un
                   `GET ?location=`.
  2. import        Importar el modulo no emite ninguna peticion.
                   Hoy sale PENDIENTE: el singleton es eager y llama a
                   `ensure_bucket()` en el constructor (`PLAN.md` F.13).

Y un **control negativo**: se construye a proposito un cliente sin `region` y se
verifica que falle. Si ese cliente pasara, la comprobacion 1 no estaria probando
nada — querria decir que minio-py cambio de comportamiento y hay que releer
`_get_region` antes de confiar en este script.

USO
---
    .venv\\Scripts\\python.exe scripts\\check_minio_region.py

Sale 0 si todo lo exigible esta en verde, 1 si algo fallo. Lo PENDIENTE no
tumba la corrida: es deuda conocida, no una regresion.
"""
import os
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BUCKET = "terra-assets"
KEY_DE_PRUEBA = "parcelas/00000000-0000-0000-0000-000000000001/2026-01-01_ndvi.tif"

_peticiones = []

_ACCESS_DENIED = (
    b'<?xml version="1.0" encoding="UTF-8"?>'
    b"<Error><Code>AccessDenied</Code><Message>Access Denied.</Message>"
    b"<Resource>/terra-assets/</Resource>"
    b"<RequestId>check</RequestId><HostId>check</HostId></Error>"
)


class SoloAceptaPut(BaseHTTPRequestHandler):
    """Servidor S3 con los permisos justos de una policy de solo escritura.

    Acepta el PUT de un objeto y niega todo lo demas, que es como se comporta
    MinIO con una policy que otorga `s3:PutObject` y nada mas. Cualquier
    peticion de mas que el cliente quiera hacer muere aca, igual que en real.
    """

    protocol_version = "HTTP/1.1"

    def log_message(self, *_):
        pass  # el registro lo llevamos nosotros, ordenado

    def _registrar(self):
        _peticiones.append((self.command, self.path))

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
        self._responder(403, _ACCESS_DENIED)

    def do_HEAD(self):
        self._registrar()
        self._responder(403)

    def do_PUT(self):
        self._registrar()
        largo = int(self.headers.get("Content-Length", 0))
        if largo:
            self.rfile.read(largo)
        self._responder(200, extra={"ETag": '"0000"'})


def _levantar_servidor():
    sonda = socket.socket()
    sonda.bind(("127.0.0.1", 0))
    puerto = sonda.getsockname()[1]
    sonda.close()
    servidor = ThreadingHTTPServer(("127.0.0.1", puerto), SoloAceptaPut)
    threading.Thread(target=servidor.serve_forever, daemon=True).start()
    return servidor, puerto


def _tomar():
    capturadas = list(_peticiones)
    _peticiones.clear()
    return capturadas


def _formatear(peticiones):
    if not peticiones:
        return "(ninguna)"
    return " | ".join(f"{metodo} {path}" for metodo, path in peticiones)


def main():
    servidor, puerto = _levantar_servidor()

    # El entorno se fija ANTES de importar config, que lee os.getenv al
    # importarse. `load_dotenv()` no pisa lo que ya esta en el entorno, asi que
    # el .env de la maquina no interfiere con esta corrida.
    os.environ["MINIO_ENDPOINT"] = f"127.0.0.1:{puerto}"
    os.environ["MINIO_SECURE"] = "False"
    os.environ["MINIO_ACCESS_KEY"] = "worker-rw"
    # El valor es literal a proposito: el servidor de la prueba no valida
    # firmas, asi que no hay ningun secreto real involucrado. Un linter con las
    # reglas de bandit lo marca como S105; es un falso positivo.
    os.environ["MINIO_SECRET_KEY"] = "de-mentira-el-servidor-no-valida"
    os.environ["MINIO_BUCKET"] = BUCKET

    from minio import Minio

    from services.storage_service import StorageService

    durante_el_import = _tomar()

    fallos = 0
    print()

    # -- 1. La subida emite exactamente una peticion -------------------------
    servicio = StorageService()
    _tomar()  # el constructor todavia hace I/O (F.13); no es lo que se mide aca
    try:
        servicio.upload_bytes(KEY_DE_PRUEBA, b"COG de mentira")
        error = None
    except Exception as exc:  # noqa: BLE001 - cualquier fallo es informacion
        error = f"{type(exc).__name__}: {exc}"
    de_la_subida = _tomar()

    puts = [p for p in de_la_subida if p[0] == "PUT"]
    if error is None and len(de_la_subida) == 1 and len(puts) == 1:
        print(f"  OK         subida         {_formatear(de_la_subida)}")
    else:
        fallos += 1
        print(f"  FALLA      subida         {_formatear(de_la_subida)}")
        if error:
            print(f"             {error}")
        print("             La subida tiene que emitir solo el PUT. Si aparece")
        print("             un GET ?location=, falta `region=` en el cliente de")
        print("             MinIO de services/storage_service.py (PLAN.md F.12).")

    # -- 2. Importar el modulo no toca la red --------------------------------
    if not durante_el_import:
        print("  OK         import         (ninguna peticion)")
    else:
        print(f"  PENDIENTE  import         {_formatear(durante_el_import)}")
        print("             `import services.storage_service` abre conexiones:")
        print("             el singleton es eager y llama a ensure_bucket() en")
        print("             el constructor. Deuda conocida (PLAN.md F.13).")

    # -- Control negativo: sin `region` esto TIENE que fallar ----------------
    sin_region = StorageService.__new__(StorageService)
    sin_region.bucket = BUCKET
    sin_region.client = Minio(
        os.environ["MINIO_ENDPOINT"],
        access_key=os.environ["MINIO_ACCESS_KEY"],
        secret_key=os.environ["MINIO_SECRET_KEY"],
        secure=False,
    )
    try:
        sin_region.upload_bytes(KEY_DE_PRUEBA, b"COG de mentira")
        cayo = False
    except Exception:  # noqa: BLE001 - se espera que caiga
        cayo = True
    del_control = _tomar()

    if cayo and any("location" in path for _, path in del_control):
        print(f"  OK         control neg.   {_formatear(del_control)}")
    else:
        fallos += 1
        print(f"  FALLA      control neg.   {_formatear(del_control)}")
        print("             Un cliente SIN `region` tendria que pedir")
        print("             GetBucketLocation y morir ahi. No lo hizo, asi que")
        print("             la comprobacion 1 no esta probando nada: releer")
        print("             minio/api.py::_get_region antes de confiar en esto.")

    servidor.shutdown()
    print()
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
