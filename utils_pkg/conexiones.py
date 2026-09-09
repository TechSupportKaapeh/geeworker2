"""Verificacion de que las conexiones del worker **se hicieron de verdad**.

QUE PROBLEMA RESUELVE
---------------------
`utils_pkg/arranque.py` dice que variables tiene el proceso. Eso no es lo
mismo que decir que funcionan, y la diferencia ya nos costo tiempo: la
`DB_PASSWORD` estaba puesta —el reporte la habria mostrado como "definida
(16 chars)"— y no servia.

Peor todavia: hoy el arranque **no puede distinguir** una base que conecta de
una que no. `init_db()` atrapa su propia excepcion y la loguea, asi que el
try/except de `app.py` nunca la ve y el precalentamiento termina sin quejarse.
Un log que dice "todo bien" cuando no lo esta es peor que no tener log.

QUE PRUEBA CADA CHEQUEO, Y QUE NO
---------------------------------
Esto es la parte importante del diseno. `DECISIONS #21` del tileserver costo
una sesion entera por un chequeo de salud que exigia `s3:GetBucketLocation`, un
permiso que el trabajo real no usa: el chequeo fallaba, el servicio andaba, y el
diagnostico apuntaba al lugar equivocado. **Un chequeo que pide mas de lo que
pide el trabajo real inventa fallas.**

Asi que cada resultado dice explicitamente hasta donde llego:

  geodata   PRUEBA TODO. Abre conexion, autentica y consulta. Ademas informa
            que usuario y que base quedaron del otro lado, y si estan las tres
            tablas que el worker escribe.

  gee       PRUEBA TODO. Inicializa y hace un round-trip minimo contra la API
            (`ee.Number(1).getInfo()`), que es lo unico que distingue unas
            credenciales validas de unas que solo estan bien formadas.

  minio     PRUEBA ALCANCE, NO CREDENCIALES. Resuelve el DNS, abre TCP y, si
            corresponde, negocia TLS. No firma ninguna peticion a proposito:
            cualquier operacion barata que se me ocurra —`bucket_exists`,
            `list_buckets`— pide un permiso que la subida no usa, y contra la
            policy de solo escritura daria un `AccessDenied` que se leeria como
            un problema de credenciales inexistente. La prueba real de
            credenciales es `scripts/check_write_path.py`, que escribe.

            Lo que si atrapa es el error mas repetido de este despliegue:
            confundir el dominio publico con el privado. Equivocarse ahi no da
            un error de SSL, da un `ConnectionReset`, y eso este chequeo lo ve.

  inngest   PRUEBA ALCANCE Y MODO. Que la app este registrada no se puede
            verificar desde aca: la conexion la abre Inngest hacia nosotros.

REGLAS
------
**Ningun chequeo puede tumbar el arranque.** Es la leccion que este repo
aprendio tres veces (F.13, `init_ee` fuera del try, `os.makedirs` al importar).
Cada funcion atrapa todo y devuelve un `Resultado`; ninguna levanta.

**Ningun chequeo puede colgar el arranque.** Todos tienen timeout explicito y
corto. El total esta acotado por `TIMEOUT_*`, porque hasta que el startup no
termina, `/health` no responde.

**Ningun chequeo imprime un secreto.**
"""

import logging
import os
import socket
import ssl
import tempfile
import time
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

# Cortos a proposito: esto corre antes de que `/health` conteste. Un chequeo que
# tarda es un deploy que Railway marca como caido.
TIMEOUT_RED = 5
TIMEOUT_DB = 8
TIMEOUT_GEE = 15

OK = "OK"
FALLA = "FALLA"
OMITIDO = "-"


def a_una_linea(texto):
    """Aplana un mensaje ajeno a una sola linea ASCII.

    Los detalles de este reporte no los escribimos nosotros: vienen de
    psycopg2, de `ee`, del sistema operativo. Traen dos cosas que rompen el
    formato y que solo se ven corriendo contra infraestructura real:

      - **Saltos de linea.** El error de psycopg2 tiene varios, y el
        formateador JSON de produccion los escapa: el registro queda con `\\n`
        literales adentro e ilegible.
      - **Acentos.** El error del socket lo emite Windows en el idioma del
        sistema ("no se puede establecer una conexion..."). Fuera de ASCII, una
        consola cp1252 tira `UnicodeEncodeError` y un chequeo que corrio entero
        termina reportando fallo.

    Ademas se descartan las lineas repetidas: psycopg2 repite el mismo mensaje
    dos veces y duplicar un texto ya largo no agrega nada.
    """
    # Se descarta por contencion y no por igualdad: psycopg2 repite el mensaje,
    # y la primera copia viene con el nombre de la excepcion adelante
    # ("OperationalError: connection to server..."), asi que las dos lineas no
    # son iguales pero la segunda no aporta nada.
    acumulado = ""
    for linea in str(texto).splitlines():
        linea = " ".join(linea.split())
        if linea and linea not in acumulado:
            acumulado = (acumulado + " " + linea).strip()
    return acumulado.encode("ascii", "replace").decode("ascii")


class Resultado:
    """El desenlace de un chequeo. Nunca es una excepcion."""

    def __init__(self, nombre, estado, detalle, ms=None, prueba=""):
        self.nombre = nombre
        self.estado = estado
        # Se sanea aca y no en cada sitio de construccion: cualquier chequeo
        # nuevo hereda la garantia sin tener que acordarse.
        self.detalle = a_una_linea(detalle)
        self.ms = ms
        # Que alcance tuvo el chequeo. Va impreso al lado del resultado para
        # que un OK no se lea como mas garantia de la que da.
        self.prueba = prueba

    @property
    def fallo(self):
        return self.estado == FALLA


def _ms(inicio):
    return int((time.monotonic() - inicio) * 1000)


def partir_endpoint(endpoint, secure):
    """Saca (host, puerto) de un endpoint estilo MinIO.

    El endpoint de minio-py es `host[:puerto]`, sin esquema. Igual se tolera uno
    pegado adelante porque es un error de configuracion frecuente y conviene que
    el chequeo lo diagnostique en vez de romperse con el.
    """
    texto = (endpoint or "").strip()
    if "//" in texto:
        texto = urlsplit(texto).netloc or texto.split("//", 1)[1]
    if ":" in texto:
        host, _, puerto = texto.rpartition(":")
        try:
            return host, int(puerto)
        except ValueError:
            return texto, 443 if secure else 80
    return texto, 443 if secure else 80


def verificar_red(nombre, host, puerto, tls, timeout=TIMEOUT_RED, prueba=""):
    """DNS + TCP, y el handshake de TLS si corresponde.

    El handshake se hace de verdad y no se da por sentado: hablarle TLS a un
    puerto que sirve texto plano —el caso de apuntar al dominio privado con
    `MINIO_SECURE=True`— falla aca y no despues, en medio de una subida.
    """
    inicio = time.monotonic()
    if not host:
        return Resultado(nombre, FALLA, "no hay host configurado", prueba=prueba)
    try:
        ip = socket.gethostbyname(host)
    except OSError as e:
        return Resultado(nombre, FALLA, "el DNS no resuelve %s (%s)" % (host, e),
                         _ms(inicio), prueba)
    try:
        sock = socket.create_connection((host, puerto), timeout=timeout)
    except OSError as e:
        return Resultado(
            nombre, FALLA,
            "no acepta TCP en %s:%s [%s] (%s)" % (host, puerto, ip, e),
            _ms(inicio), prueba)

    try:
        if tls:
            contexto = ssl.create_default_context()
            with contexto.wrap_socket(sock, server_hostname=host) as tunel:
                version = tunel.version()
            detalle = "%s:%s [%s] %s" % (host, puerto, ip, version)
        else:
            detalle = "%s:%s [%s] sin TLS" % (host, puerto, ip)
    except (ssl.SSLError, OSError) as e:
        return Resultado(nombre, FALLA,
                         "TCP abre pero TLS falla contra %s:%s (%s)" % (host, puerto, e),
                         _ms(inicio), prueba)
    finally:
        try:
            sock.close()
        except OSError:
            pass

    return Resultado(nombre, OK, detalle, _ms(inicio), prueba)


def verificar_minio(endpoint, secure):
    host, puerto = partir_endpoint(endpoint, secure)
    return verificar_red("minio", host, puerto, secure,
                         prueba="alcance, no credenciales")


def verificar_geodata(get_connection, release_connection):
    """Conecta de verdad, consulta, y cuenta que encontro del otro lado.

    Las dependencias entran por parametro para poder probar esta funcion sin
    base: `db_repository` abre un pool al importarse la primera conexion.
    """
    inicio = time.monotonic()
    conn = None
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT current_user, current_database()")
        usuario, base = cur.fetchone()

        faltantes = []
        for tabla in ("layers", "measurements", "sentinel2_dates"):
            cur.execute("SELECT to_regclass(%s)", ("geodata." + tabla,))
            if cur.fetchone()[0] is None:
                faltantes.append(tabla)
        cur.close()

        detalle = "conectado como %s a %s" % (usuario, base)
        if faltantes:
            # Conecta pero le faltan tablas: es un fallo distinto del de
            # credenciales y hay que poder distinguirlo de un vistazo. `layers`
            # y `measurements` las crea EF Core desde Geocore.
            return Resultado(
                "geodata", FALLA,
                "%s, pero NO existen: %s. Las crea EF Core desde Geocore"
                % (detalle, ", ".join(faltantes)),
                _ms(inicio), "conexion, auth y consulta")
        return Resultado("geodata", OK, detalle + ", con las 3 tablas",
                         _ms(inicio), "conexion, auth y consulta")
    except Exception as e:  # noqa: BLE001 - un chequeo no puede levantar
        return Resultado("geodata", FALLA, "%s: %s" % (type(e).__name__, e),
                         _ms(inicio), "conexion, auth y consulta")
    finally:
        if conn is not None:
            try:
                release_connection(conn)
            except Exception:  # noqa: BLE001
                pass


def verificar_gee(init_ee, round_trip):
    """Inicializa y ademas hace una llamada minima contra la API.

    `ee.Initialize()` sola no prueba gran cosa: arma credenciales y puede pasar
    con una service account que despues no tiene acceso al proyecto. El
    round-trip es lo que separa "las credenciales estan bien formadas" de "las
    credenciales sirven".
    """
    inicio = time.monotonic()
    try:
        init_ee()
    except Exception as e:  # noqa: BLE001
        return Resultado("gee", FALLA, "no inicializa: %s" % e, _ms(inicio),
                         "credenciales y round-trip")
    try:
        valor = round_trip()
    except Exception as e:  # noqa: BLE001
        return Resultado(
            "gee", FALLA,
            "inicializa pero la API no responde: %s: %s" % (type(e).__name__, e),
            _ms(inicio), "credenciales y round-trip")
    return Resultado("gee", OK, "la API responde (round-trip devolvio %r)" % (valor,),
                     _ms(inicio), "credenciales y round-trip")


def verificar_outputs(base_output_dir):
    """Crea la carpeta de trabajo y escribe un archivo de prueba.

    **Por que existe.** Con `BASE_OUTPUT_DIR=../outputs` heredado del `.env`
    local, el worker fallo en produccion con:

        [Errno 13] Permission denied: '../outputs'

    Y fallo **por job**, en el intento 1 de 4 de cada invocacion, no al
    arrancar. El reporte de configuracion tenia el dato delante y no lo vio:
    imprimia `BASE_OUTPUT_DIR definida ../outputs`, que no parece nada. Lo que
    delata el problema es la ruta **resuelta**: desde `/app`, `../outputs` es
    `/outputs`, fuera del arbol que el Dockerfile le dio al usuario `worker`.

    Por eso este chequeo informa siempre la ruta absoluta. Una ruta relativa se
    lee inofensiva; la absoluta muestra de inmediato si esta donde tiene que
    estar.

    Es el mismo caso que la contrasena: la variable estaba puesta y no servia.
    Un reporte de configuracion dice que hay; solo escribir dice si funciona.
    """
    inicio = time.monotonic()
    resuelta = os.path.abspath(base_output_dir or "")
    prueba = "escritura real"
    try:
        os.makedirs(resuelta, exist_ok=True)
    except OSError as e:
        return Resultado(
            "outputs", FALLA,
            "no se puede crear %s (de BASE_OUTPUT_DIR=%s): %s"
            % (resuelta, base_output_dir, e),
            _ms(inicio), prueba)

    # Crear la carpeta no alcanza: puede existir y no ser escribible.
    try:
        with tempfile.NamedTemporaryFile(dir=resuelta, prefix=".chequeo-",
                                         suffix=".tmp", delete=True):
            pass
    except OSError as e:
        return Resultado(
            "outputs", FALLA,
            "%s existe pero no se puede escribir: %s" % (resuelta, e),
            _ms(inicio), prueba)

    return Resultado("outputs", OK,
                     "%s (de BASE_OUTPUT_DIR=%s)" % (resuelta, base_output_dir),
                     _ms(inicio), prueba)


def verificar_inngest(es_produccion, base_url, hay_signing_key):
    """En produccion, alcance del API de eventos. En desarrollo, del dev server.

    No prueba que la app este registrada: esa conexion la abre Inngest hacia
    nosotros, y desde aca no hay forma de verla. Se dice explicito para que un
    OK no se lea como "ya llegan eventos".
    """
    if es_produccion:
        if not hay_signing_key:
            return Resultado(
                "inngest", FALLA,
                "modo cloud sin INNGEST_SIGNING_KEY: el SDK va a rechazar toda "
                "peticion a /api/inngest", prueba="modo y alcance de salida")
        # En produccion el SDK no usa `base_url`: habla con Inngest Cloud.
        r = verificar_red("inngest", "api.inngest.com", 443, True,
                          prueba="modo y alcance de salida")
        if r.estado == OK:
            r.detalle = "modo cloud, con signing key. " + r.detalle
        return r

    host, puerto = partir_endpoint(base_url, secure=base_url.startswith("https"))
    r = verificar_red("inngest", host, puerto, base_url.startswith("https"),
                      prueba="modo y alcance de salida")
    r.detalle = "modo dev (SIN verificacion de firma). " + r.detalle
    return r


def formatear(resultados):
    """Arma las lineas del reporte. **Funcion pura.**"""
    lineas = ["", "=" * 66, "CONEXIONES", "=" * 66]
    for r in resultados:
        tiempo = "%5d ms" % r.ms if r.ms is not None else "        "
        lineas.append("  %-5s %-9s %s  %s" % (r.estado, r.nombre, tiempo, r.detalle))
        if r.prueba:
            lineas.append("        %s(prueba: %s)" % (" " * 9, r.prueba))

    fallidos = [r for r in resultados if r.fallo]
    lineas.append("=" * 66)
    if fallidos:
        lineas.append("NO CONECTAN: %s" % ", ".join(r.nombre for r in fallidos))
        lineas.append("El worker igual arranca; cada invocacion que dependa de "
                      "esto va a fallar")
        lineas.append("por separado y la va a reintentar Inngest.")
    else:
        lineas.append("Todas las conexiones verificadas responden.")
    lineas.append("=" * 66)
    return lineas


def verificar_conexiones():
    """Corre los cuatro chequeos. Nunca levanta.

    Los imports son locales a proposito: este modulo se importa desde `app.py`
    y no tiene por que arrastrar `ee`, `psycopg2` ni el cliente de MinIO al
    importarse. Es la misma leccion de las tres veces que un import mato el
    proceso.
    """
    resultados = []

    try:
        from config import (
            BASE_OUTPUT_DIR,
            INNGEST_BASE_URL,
            INNGEST_SIGNING_KEY,
            IS_PRODUCTION,
            MINIO_ENDPOINT,
            MINIO_SECURE,
        )
        # Primero el disco: es el chequeo mas barato y el unico que no depende
        # de la red, asi que si falla el resto del reporte se lee con la
        # sospecha correcta encima.
        resultados.append(verificar_outputs(BASE_OUTPUT_DIR))
        resultados.append(verificar_minio(MINIO_ENDPOINT, MINIO_SECURE))
    except Exception as e:  # noqa: BLE001
        resultados.append(Resultado("minio", FALLA, "no se pudo chequear: %s" % e))
        INNGEST_BASE_URL = INNGEST_SIGNING_KEY = ""
        IS_PRODUCTION = True

    try:
        from repositories.db_repository import get_connection, release_connection
        resultados.append(verificar_geodata(get_connection, release_connection))
    except Exception as e:  # noqa: BLE001
        resultados.append(Resultado("geodata", FALLA, "no se pudo chequear: %s" % e))

    try:
        import ee

        from services.ee.ee_client import init_ee
        resultados.append(verificar_gee(init_ee, lambda: ee.Number(1).getInfo()))
    except Exception as e:  # noqa: BLE001
        resultados.append(Resultado("gee", FALLA, "no se pudo chequear: %s" % e))

    resultados.append(
        verificar_inngest(IS_PRODUCTION, INNGEST_BASE_URL, bool(INNGEST_SIGNING_KEY)))
    return resultados


def registrar_conexiones(log):
    """Loguea el reporte y devuelve los resultados.

    El bloque va como INFO para que se lea entero y en orden; ademas cada fallo
    sale como ERROR por separado, para que sobreviva a cualquier nivel de log y
    para que Railway lo destaque. Mismo criterio que el banner de Geocore.
    """
    resultados = verificar_conexiones()
    for linea in formatear(resultados):
        log.info("%s", linea)
    for r in resultados:
        if r.fallo:
            log.error("Conexion %s: %s", r.nombre, r.detalle)
    return resultados
