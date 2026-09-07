"""A-3 en un comando: el camino de escritura del worker contra MinIO real.

QUE HACE
--------
Sube objetos de verdad al MinIO **configurado en el entorno**, en cinco
escalones, cada uno agregando exactamente un eslabon — de modo que **el primero
que falla señala la causa**. Es el equivalente de `check_prod.py` del
tileserver, del lado de escritura.

PARA QUE SIRVE
--------------
`PREGUNTAS_ABIERTAS` A-3: la cadena de **lectura** esta verificada de punta a
punta desde el 2026-08-26 —COG en MinIO, tileserver, token de Geocore, tile en
el navegador— y la de **escritura** nunca se probo. Las escrituras del worker
estan validadas con `PREPARE` contra la DB y con un doble para MinIO, que es
exactamente el tipo de verificacion en aislamiento que esta semana demostro no
alcanzar.

Todo lo que se cerro entre el 2026-09-01 y el 09-07 —la region del cliente
(F.12), el constructor sin I/O (F.13), el fallo cerrado con el nombre de la
variable (`DECISIONS #24`), el default de TLS (W-2)— existe para que **este
script falle por el motivo correcto** cuando falle.

QUE ESCRIBE
-----------
Objetos con el prefijo `_diagnostico/`, para que no se confundan con datos.
**No los borra**: `s3:DeleteObject` es un permiso que la ingesta real no usa, y
pedirlo aca seria volver a la trampa de `DECISIONS #21` —un chequeo mas estricto
que el sistema que chequea—. Se limpian a mano con `mc rm` o con una politica de
lifecycle sobre ese prefijo.

USO
---
    .venv\\Scripts\\python.exe scripts\\check_write_path.py

Lee la configuracion del entorno y del `.env`, igual que el worker. Sale 0 si
todos los escalones exigibles pasan.
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PREFIJO = "_diagnostico"
SEIS_MIB = 6 * 1024 * 1024

_ok = "  OK        "
_falla = "  FALLA     "
_aviso = "  AVISO     "


def _enmascarar_usuario(valor):
    """La access key es un nombre de usuario: mostrar el principio y el final
    alcanza para distinguir `worker-rw` de un default silencioso."""
    if not valor:
        return "(vacio)"
    return f"{valor[:3]}…{valor[-2:]}" if len(valor) > 6 else "…"


def _enmascarar_secreto(valor):
    """Del secreto **no se muestra ningun caracter**, solo que esta y cuanto mide.

    La primera version mostraba los primeros 3 y los ultimos 2. Para una access
    key eso es correcto; para un secreto no — y la salida de este script esta
    pensada para pegarse en un chat, que es como el `.env` del worker ya filtro
    una contraseña una vez (SESSION del 2026-08-26, §6).
    """
    return f"(presente, {len(valor)} chars)" if valor else "(vacio)"


def main():
    from config import (
        AWS_REGION,
        ENVIRONMENT,
        MINIO_ACCESS_KEY,
        MINIO_BUCKET,
        MINIO_ENDPOINT,
        MINIO_SECRET_KEY,
        MINIO_SECURE,
    )

    fallos = 0
    print()

    # --- 1. La configuracion resuelta, sin tocar la red ---------------------
    #
    # Se imprime primero porque el 90% de los fallos de este despliegue fueron
    # de configuracion, y verla escrita descarta la mitad de las hipotesis antes
    # de empezar. La access key sale enmascarada: lo justo para distinguir
    # `worker-rw` de un default, sin que la salida sirva para nada si se pega en
    # un chat.
    print(f"{_ok}config      endpoint={MINIO_ENDPOINT} secure={MINIO_SECURE} "
          f"bucket={MINIO_BUCKET} region={AWS_REGION}")
    print(f"{' ' * 12}            access_key={_enmascarar_usuario(MINIO_ACCESS_KEY)} "
          f"secret={_enmascarar_secreto(MINIO_SECRET_KEY)} entorno={ENVIRONMENT}")

    if "localhost" in MINIO_ENDPOINT or "127.0.0.1" in MINIO_ENDPOINT:
        print(f"{_aviso}            el endpoint es local. A-3 pide el dominio "
              f"**publico** de la API de MinIO")

    # --- 2. Coherencia, todavia sin red ------------------------------------
    from services.storage_service import get_storage_service, validate_endpoint

    motivo = validate_endpoint(MINIO_ENDPOINT, MINIO_SECURE)
    if motivo:
        print(f"{_falla}coherencia  {motivo}")
        print("\n  El primer escalon ya falla, asi que no se toca la red: "
              "corregir eso antes de seguir.\n")
        return 1
    print(f"{_ok}coherencia  el endpoint y MINIO_SECURE no se contradicen")

    # --- 3. El cliente se construye ----------------------------------------
    #
    # Aca es donde `DECISIONS #24` hace su trabajo: si falta una credencial o el
    # endpoint no sirve, levanta nombrando la variable en vez de autenticarse
    # con la credencial root del docker-compose.
    try:
        storage = get_storage_service()
    except RuntimeError as e:
        print(f"{_falla}cliente     {e}")
        print()
        return 1
    print(f"{_ok}cliente     construido sin I/O")

    corrida = uuid.uuid4().hex[:8]

    # --- 4. Una subida chica: s3:PutObject ---------------------------------
    key_chica = f"{PREFIJO}/{corrida}/chico.txt"
    chico_paso = False
    try:
        storage.upload_bytes(key_chica, b"diagnostico del camino de escritura",
                             content_type="text/plain")
        chico_paso = True
        print(f"{_ok}put chico   {key_chica}")
    except Exception as e:  # noqa: BLE001 - un diagnostico reporta el fallo, no lo propaga
        fallos += 1
        print(f"{_falla}put chico   {type(e).__name__}: {e}")
        codigo = getattr(e, "code", None)
        if codigo == "AccessDenied":
            print(f"{' ' * 12}            `AccessDenied` en el PUT es la policy de "
                  f"`worker-rw`, no la region:")
            print(f"{' ' * 12}            F.12 ya elimino el GetBucketLocation. "
                  f"Confirmalo con scripts/check_minio_region.py")
        elif codigo == "NoSuchBucket":
            print(f"{' ' * 12}            el bucket '{MINIO_BUCKET}' no existe. "
                  f"MINIO_BUCKET tiene que coincidir con GeoData__MinioBucket")

    # --- 5. Una subida grande: el camino que toma un COG real --------------
    #
    # Arriba de 5 MiB minio-py deja de emitir un PUT unico y pasa a multipart
    # (`POST ?uploads`, `PUT ?partNumber=N`, `POST ?uploadId`). Un COG de rancho
    # pesa mas que eso, asi que **el escalon 4 no prueba el camino de
    # produccion**: este si. Las tres operaciones caen bajo `s3:PutObject` en la
    # semantica de IAM de S3, pero eso estaba modelado segun la especificacion y
    # **sin verificar contra el motor de policies de MinIO** — es justo lo que
    # este escalon verifica.
    key_grande = f"{PREFIJO}/{corrida}/seis-mib.bin"
    try:
        storage.upload_bytes(key_grande, b"\0" * SEIS_MIB)
        print(f"{_ok}put grande  {key_grande} (6 MiB, multipart)")
    except Exception as e:  # noqa: BLE001 - un diagnostico reporta el fallo, no lo propaga
        fallos += 1
        print(f"{_falla}put grande  {type(e).__name__}: {e}")
        # La pista solo vale si el chico paso. Decir "el chico paso y este no"
        # cuando fallaron los dos manda a revisar la policy de multipart en vez
        # de la conexion — que es exactamente el tipo de falso diagnostico que
        # este script existe para evitar.
        if chico_paso:
            print(f"{' ' * 12}            **El chico paso y este no: es "
                  f"multipart.** La policy de `worker-rw`")
            print(f"{' ' * 12}            necesita cubrir CreateMultipartUpload, "
                  f"UploadPart y CompleteMultipartUpload")
        else:
            print(f"{' ' * 12}            Fallaron los dos, asi que no es "
                  f"multipart: la causa es la del escalon anterior")

    # --- 6. Lectura de vuelta: informativo, no exigible --------------------
    #
    # `s3:GetObject` **no es un permiso que la ingesta use**: el worker escribe y
    # el tileserver lee, con credenciales distintas. Que falle aca no es un
    # problema — es una policy de solo escritura funcionando. Se intenta porque
    # cuando funciona confirma el round-trip completo.
    try:
        datos = storage.get_object_bytes(key_chica)
        estado = _ok if datos.startswith(b"diagnostico") else _falla
        print(f"{estado}get chico   round-trip completo, {len(datos)} bytes")
    except Exception as e:  # noqa: BLE001 - idem
        print(f"{_aviso}get chico   {getattr(e, 'code', type(e).__name__)} - "
              f"esperable si `worker-rw` es de solo escritura")

    print()
    if fallos:
        print(f"  {fallos} escalon(es) exigible(s) fallaron. "
              f"El primero en rojo señala la causa.\n")
        return 1

    print(f"  OK: El camino de escritura funciona. Objetos de diagnostico en "
          f"{PREFIJO}/{corrida}/")
    print(f"     Se limpian con: mc rm --recursive --force "
          f"<alias>/{MINIO_BUCKET}/{PREFIJO}/\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
