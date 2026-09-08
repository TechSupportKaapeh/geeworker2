"""Reporte de configuracion que el worker escribe al arrancar.

POR QUE EXISTE
--------------
Los tres deploys que fallaron en este servicio fallaron por configuracion, no
por codigo, y en los tres el log no alcanzaba para saberlo:

  1. `init_ee()` fuera del try tumbaba el proceso: los tracebacks de varios
     reinicios salian entrelazados y no se veia que faltaban las credenciales
     de GEE.
  2. `os.makedirs` a nivel de modulo reventaba **antes** de `setup_logging()`,
     asi que el error salio crudo y sin nombrar la variable culpable.
  3. `DB_PASSWORD` mala: el unico sintoma era un `password authentication
     failed` en medio del log, que ademas tiene cinco causas distintas.

El patron es siempre el mismo: **el proceso sabia perfectamente que le faltaba
y no lo dijo.** Este modulo lo dice, de una sola vez y arriba de todo.

QUE IMPRIME
-----------
Una linea por variable con su estado, agrupadas por subsistema, encabezadas por
el modo efectivo del proceso. Cuatro estados posibles:

    definida        el entorno la trae
    por defecto     no esta y el codigo cae a un valor; se muestra cual
    sin definir     no esta y esta bien que no este (`opcional`)
    AUSENTE         no esta y no hay default util; se explica que se rompe

Y una seccion final que lista solo lo que va a fallar, para no tener que leer
el resto cuando todo esta bien.

QUE NO IMPRIME
--------------
**Ningun caracter de ningun secreto.** De las claves solo sale el largo, y de
`INNGEST_EVENT_KEY` ademas si quedo con el valor de desarrollo, que es un caso
que hay que poder ver sin ver el valor. Mismo criterio que `check_write_path.py`
y `check_db.py`.

Tampoco imprime nada en color ni fuera de ASCII: lo lee la consola de Railway,
pero tambien la de Windows, que es cp1252 y convierte un caracter fuera de tabla
en un `UnicodeEncodeError`.

COMO SE PRUEBA
--------------
`reporte_de_arranque()` es pura: recibe un diccionario y devuelve lineas. No
lee el entorno ni loguea. `registrar_arranque()` es la capa fina que arma ese
diccionario desde `config` y `os.environ` y manda las lineas al logger.
"""

import os

# El dibujo se guarda como lista de lineas, no como un bloque con saltos: cada
# linea sale en su propio registro de log (ver `registrar_arranque`), asi que
# nunca hay un salto que el formateador JSON tenga que escapar.
#
# La primera linea llevaba dos espacios menos cuando se paso; se restauraron
# para que la G quede alineada con la barra de la linea de abajo.
ARTE = (
    '  ___  ____  ____  _  _   __  ____  __ _  ____  ____ ',
    ' / __)(  __)(  __)/ )( \\ /  \\(  _ \\(  / )(  __)(  _ \\',
    '( (_ \\ ) _)  ) _) \\ /\\ /(  O ))   / )  (  ) _)  )   /',
    ' \\___/(____)(____)(_/\\_) \\__/(__\\_)(__\\_)(____)(__\\_)',
    ' ____  _  _  _     __   __    _  _  __               ',
    '(  _ \\( \\/ )(_)   / _\\ (  )  / )( \\(  )              ',
    ' ) _ ( )  /  _   /    \\/ (_/\\\\ \\/ / )(               ',
    '(____/(__/  (_)  \\_/\\_/\\____/ \\__/ (__)              ',
)

# Valor de desarrollo de `INNGEST_EVENT_KEY`. Que la variable exista no alcanza:
# `resolve_client_config` compara contra este string y, si coincide, no se lo
# pasa al cliente. Ver `services/inngest_client.py`.
EVENT_KEY_DE_DESARROLLO = "dev-local-key"

# Como se describe cada variable en el reporte.
#
#   secreto : nunca se muestra el valor, solo el largo
#   publico : se muestra el valor; no es sensible
#
# `consecuencia` es lo que se rompe si falta **en produccion**. Es la parte que
# convierte el reporte en algo accionable: sin ella hay que ir al codigo a
# averiguar si la variable importa.
SECRETO = "secreto"
PUBLICO = "publico"


class Variable:
    """Una variable de entorno y como se la reporta.

    `opcional` es un tercer estado que no estaba en el primer diseno y que
    aparecio al correr los tests: hay variables que no tienen default y que
    igual esta bien que falten. `MINIO_SECURE` se deduce del host cuando no
    esta, y `PORT` lo inyecta Railway. Sin esta distincion el reporte gritaba
    por dos cosas que estaban perfectas, y un reporte que da falsos positivos
    deja de leerse — que es justo el problema que vino a resolver.
    """

    def __init__(self, nombre, clase, defecto=None, consecuencia=None,
                 solo_produccion=False, solo_desarrollo=False, opcional=False):
        self.nombre = nombre
        self.clase = clase
        self.defecto = defecto
        self.consecuencia = consecuencia
        self.solo_produccion = solo_produccion
        self.solo_desarrollo = solo_desarrollo
        self.opcional = opcional


INVENTARIO = (
    ("ENTORNO", (
        Variable("ENVIRONMENT", PUBLICO, defecto="development",
                 consecuencia="un valor desconocido cuenta como produccion, "
                              "que es la direccion segura"),
        Variable("PORT", PUBLICO, opcional=True,
                 consecuencia="lo inyecta Railway; en local lo pone uvicorn"),
        Variable("BASE_OUTPUT_DIR", PUBLICO, defecto="./outputs"),
    )),
    ("INNGEST", (
        Variable("INNGEST_SIGNING_KEY", SECRETO, solo_produccion=True,
                 consecuencia="sin firma el SDK rechaza toda peticion a "
                              "/api/inngest: no entra ni un evento"),
        Variable("INNGEST_EVENT_KEY", SECRETO, solo_produccion=True,
                 consecuencia="no se puede emitir terra/raster.ingested: los "
                              "COG se suben y la capa nunca se registra"),
        Variable("INNGEST_BASE_URL", PUBLICO, defecto="http://localhost:8288",
                 solo_desarrollo=True,
                 consecuencia="en produccion no se usa: el SDK apunta solo a "
                              "Inngest Cloud"),
    )),
    ("MINIO", (
        Variable("MINIO_ENDPOINT", PUBLICO, defecto="localhost:9000"),
        Variable("MINIO_ACCESS_KEY", SECRETO, solo_produccion=True,
                 consecuencia="StorageService falla cerrado: no se sube ningun COG"),
        Variable("MINIO_SECRET_KEY", SECRETO, solo_produccion=True,
                 consecuencia="StorageService falla cerrado: no se sube ningun COG"),
        Variable("MINIO_SECURE", PUBLICO, opcional=True,
                 consecuencia="si no esta, se deduce del host"),
        Variable("MINIO_BUCKET", PUBLICO, defecto="terra-assets",
                 consecuencia="tiene que coincidir con GeoData:MinioBucket de Geocore"),
        Variable("AWS_REGION", PUBLICO, defecto="us-east-1",
                 consecuencia="sin ella minio-py pide s3:GetBucketLocation, "
                              "un permiso que la subida no usa"),
    )),
    ("BASE GEODATA", (
        Variable("DB_HOST", PUBLICO, defecto="localhost"),
        Variable("DB_PORT", PUBLICO, defecto="5432"),
        Variable("DB_NAME", PUBLICO, defecto="terra"),
        Variable("DB_USER", PUBLICO, defecto="postgres",
                 consecuencia="contra el pooler de Supabase va "
                              "postgres.<project-ref>, no postgres a secas"),
        Variable("DB_PASSWORD", SECRETO, solo_produccion=True,
                 consecuencia="no se registra ninguna capa ni ninguna medicion"),
    )),
    ("GOOGLE EARTH ENGINE", (
        Variable("EE_SERVICE_ACCOUNT_EMAIL", PUBLICO, solo_produccion=True,
                 consecuencia="init_ee falla y no se puede bajar ninguna imagen"),
        Variable("EE_SERVICE_ACCOUNT_KEY_JSON", SECRETO, solo_produccion=True,
                 consecuencia="init_ee falla y no se puede bajar ninguna imagen"),
    )),
)


def describir_valor(variable, crudo):
    """Describe el valor de `variable` sin filtrar secretos.

    Devuelve `(texto, es_problema)`. `crudo` es lo que trae el entorno, o `None`
    si la variable no esta definida.
    """
    if crudo is None:
        if variable.defecto is not None:
            return "por defecto  %s" % variable.defecto, False
        if variable.opcional:
            return "sin definir  (opcional)", False
        return "AUSENTE", True

    notas = []
    # Los bordes sucios son el modo de fallo que costo la sesion de la
    # contrasena: python-dotenv los recorta al leer el .env y Railway no recorta
    # nada, asi que el mismo valor anda en local y falla desplegado.
    if crudo != crudo.strip():
        notas.append("OJO: ESPACIOS EN LOS BORDES")
    if crudo[:1] in ('"', chr(39)) or crudo[-1:] in ('"', chr(39)):
        notas.append("OJO: COMILLAS EN LOS BORDES")

    if variable.clase == SECRETO:
        if not crudo.strip():
            return "AUSENTE (definida pero vacia)", True
        texto = "definida     (%d chars)" % len(crudo)
        if crudo.strip() == EVENT_KEY_DE_DESARROLLO:
            return texto + "  ES EL VALOR DE DESARROLLO", True
    else:
        texto = "definida     %s" % crudo

    if notas:
        return texto + "  " + "  ".join(notas), True
    return texto, False


def reporte_de_arranque(entorno, es_produccion):
    """Arma el reporte. **Funcion pura**: no lee el entorno ni loguea.

    `entorno` es un mapeo tipo `os.environ`; `es_produccion` es el modo ya
    resuelto por `config`, que no se recalcula aca a proposito — un segundo
    criterio para lo mismo es exactamente el bug que `config.ENVIRONMENT`
    elimino.
    """
    lineas = list(ARTE)
    modo = "PRODUCCION" if es_produccion else "DESARROLLO"
    crudo = entorno.get("ENVIRONMENT")

    lineas.append("=" * 66)
    lineas.append("MODO: %s" % modo)
    if crudo is None:
        lineas.append("  ENVIRONMENT no esta definida; se asume 'development'.")
    elif es_produccion and crudo.strip().lower() not in (
            "production", "prod", "produccion"):
        # Un valor que nadie reconoce cuenta como produccion. Es la direccion
        # correcta, pero casi siempre es un typo y conviene decirlo.
        lineas.append("  ENVIRONMENT='%s' no es un valor conocido. Un valor "
                      "desconocido cuenta" % crudo)
        lineas.append("  como produccion a proposito, pero revisa que no sea "
                      "un typo.")
    if not es_produccion:
        lineas.append("  ATENCION: en este modo **no se verifica la firma** de "
                      "/api/inngest.")
        lineas.append("  Correcto en local. En un servicio alcanzable desde "
                      "internet significa")
        lineas.append("  que cualquiera puede invocar los handlers.")
    lineas.append("=" * 66)

    problemas = []
    for grupo, variables in INVENTARIO:
        lineas.append("")
        lineas.append(grupo)
        for v in variables:
            # Una variable que solo aplica al otro modo se muestra igual, pero
            # no cuenta como problema: en desarrollo no falta INNGEST_SIGNING_KEY.
            aplica = not (
                (v.solo_produccion and not es_produccion)
                or (v.solo_desarrollo and es_produccion)
            )
            texto, hay_problema = describir_valor(v, entorno.get(v.nombre))
            marca = " " if (aplica or not hay_problema) else "."
            lineas.append("  %s %-28s %s" % (marca, v.nombre, texto))
            if hay_problema and aplica:
                problemas.append((v, texto))

    lineas.append("")
    lineas.append("=" * 66)
    if problemas:
        lineas.append("HAY %d COSA(S) QUE VAN A FALLAR:" % len(problemas))
        for v, _ in problemas:
            lineas.append("  - %s: %s" % (
                v.nombre, v.consecuencia or "requerida en este modo"))
    else:
        lineas.append("Configuracion completa para el modo %s." % modo)
    lineas.append("=" * 66)
    return lineas


def registrar_arranque(logger, es_produccion, entorno=None):
    """Manda el reporte al logger, una linea por registro.

    Una linea por registro y no un bloque de una sola: el formateador JSON de
    produccion escapa los saltos de linea, y un bloque entero quedaria como un
    solo campo ilegible con `\\n` literales.
    """
    for linea in reporte_de_arranque(
            os.environ if entorno is None else entorno, es_produccion):
        logger.info("%s", linea)
