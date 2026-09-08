"""Tests de la verificacion de conexiones.

Lo que mas se cuida aca es la regla que hace util a este modulo: **ningun
chequeo puede levantar ni colgar el arranque**. Es la leccion que este repo
aprendio tres veces (el singleton de storage, `init_ee` fuera del try,
`os.makedirs` al importar), y un verificador que tumba el proceso al verificar
seria la cuarta, con el agravante de ser codigo escrito justamente para
diagnosticar.

Nada de esto toca la red: las funciones reciben sus dependencias por parametro.
"""

import socket

import pytest

from utils_pkg.conexiones import (
    FALLA,
    OK,
    Resultado,
    formatear,
    partir_endpoint,
    verificar_gee,
    verificar_geodata,
    verificar_inngest,
    verificar_red,
)


# --------------------------------------------------------------------------
# partir_endpoint: el error mas repetido de este despliegue
# --------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint,secure,esperado", [
    # El dominio privado de Railway va con puerto explicito y sin TLS.
    ("bucket.railway.internal:9000", False, ("bucket.railway.internal", 9000)),
    # El publico va sin puerto y con TLS; el 443 lo pone este parser.
    ("bucket-production.up.railway.app", True, ("bucket-production.up.railway.app", 443)),
    ("localhost:9000", False, ("localhost", 9000)),
    # Sin TLS y sin puerto, 80. No es un caso real pero no debe romper.
    ("minio", False, ("minio", 80)),
    # minio-py no quiere esquema, pero pegarlo es un error frecuente y el
    # chequeo tiene que poder diagnosticarlo en vez de romperse con el.
    ("https://bucket.up.railway.app", True, ("bucket.up.railway.app", 443)),
    ("http://localhost:9000", False, ("localhost", 9000)),
])
def test_partir_endpoint(endpoint, secure, esperado):
    assert partir_endpoint(endpoint, secure) == esperado


def test_partir_endpoint_con_puerto_no_numerico_no_revienta():
    host, puerto = partir_endpoint("host:noesunpuerto", secure=True)
    assert puerto == 443


def test_partir_endpoint_vacio_no_revienta():
    assert partir_endpoint("", False) == ("", 80)
    assert partir_endpoint(None, False) == ("", 80)


# --------------------------------------------------------------------------
# verificar_red: falla, pero devolviendo un Resultado
# --------------------------------------------------------------------------

def test_dns_que_no_resuelve_devuelve_falla_no_excepcion(monkeypatch):
    def explota(_host):
        raise socket.gaierror("no such host")
    monkeypatch.setattr(socket, "gethostbyname", explota)

    r = verificar_red("minio", "no.existe.invalido", 443, tls=True)
    assert r.estado == FALLA
    assert "DNS" in r.detalle


def test_tcp_rechazado_devuelve_falla_no_excepcion(monkeypatch):
    monkeypatch.setattr(socket, "gethostbyname", lambda h: "10.0.0.1")

    def rechaza(*a, **k):
        raise ConnectionResetError("connection reset by peer")
    monkeypatch.setattr(socket, "create_connection", rechaza)

    # Este es el sintoma exacto de apuntarle al dominio publico con puerto.
    r = verificar_red("minio", "bucket.up.railway.app", 9000, tls=True)
    assert r.estado == FALLA
    assert "no acepta TCP" in r.detalle
    assert "9000" in r.detalle


def test_sin_host_no_intenta_la_red():
    r = verificar_red("minio", "", 443, tls=True)
    assert r.estado == FALLA
    assert "no hay host" in r.detalle


def test_el_resultado_trae_el_tiempo(monkeypatch):
    monkeypatch.setattr(socket, "gethostbyname", lambda h: "10.0.0.1")
    monkeypatch.setattr(socket, "create_connection",
                        lambda *a, **k: _SocketFalso())
    r = verificar_red("minio", "host", 9000, tls=False)
    assert r.estado == OK
    assert r.ms is not None and r.ms >= 0
    assert "sin TLS" in r.detalle


class _SocketFalso:
    def close(self):
        pass


# --------------------------------------------------------------------------
# geodata: la unica que prueba la cadena entera
# --------------------------------------------------------------------------

class _CursorFalso:
    def __init__(self, usuario="postgres", base="postgres", tablas=()):
        self._usuario, self._base, self._tablas = usuario, base, set(tablas)
        self._ultimo = None

    def execute(self, sql, params=None):
        self._ultimo = (sql, params)

    def fetchone(self):
        sql, params = self._ultimo
        if "current_user" in sql:
            return (self._usuario, self._base)
        nombre = params[0].split(".", 1)[1]
        return ("existe" if nombre in self._tablas else None,)

    def close(self):
        pass


class _ConexionFalsa:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


TODAS = ("layers", "measurements", "sentinel2_dates")


def test_geodata_ok_informa_usuario_y_base():
    conn = _ConexionFalsa(_CursorFalso("postgres", "postgres", TODAS))
    r = verificar_geodata(lambda: conn, lambda c: None)
    assert r.estado == OK
    # Que diga *que* encontro del otro lado es el punto: prueba que la conexion
    # se hizo, no que la configuracion parecia correcta.
    assert "postgres" in r.detalle
    assert "3 tablas" in r.detalle


def test_geodata_conecta_pero_le_faltan_tablas_es_falla_distinta():
    conn = _ConexionFalsa(_CursorFalso(tablas=("sentinel2_dates",)))
    r = verificar_geodata(lambda: conn, lambda c: None)
    assert r.estado == FALLA
    assert "layers" in r.detalle and "measurements" in r.detalle
    # Hay que poder distinguirlo de un fallo de credenciales de un vistazo.
    assert "EF Core" in r.detalle


def test_geodata_que_no_autentica_devuelve_falla_no_excepcion():
    def explota():
        raise RuntimeError('password authentication failed for user "postgres"')
    r = verificar_geodata(explota, lambda c: None)
    assert r.estado == FALLA
    assert "password authentication failed" in r.detalle


def test_geodata_devuelve_la_conexion_aunque_falle():
    devueltas = []
    conn = _ConexionFalsa(_CursorFalso(tablas=TODAS))

    def consulta_rota():
        raise ValueError("boom")

    # Con la conexion ya tomada y la consulta rota, igual se devuelve al pool.
    class _ConexionRota(_ConexionFalsa):
        def cursor(self):
            consulta_rota()

    r = verificar_geodata(lambda: _ConexionRota(None), devueltas.append)
    assert r.estado == FALLA
    assert len(devueltas) == 1


def test_geodata_no_revienta_si_devolver_la_conexion_falla():
    # Un pool roto no puede convertir un diagnostico en una caida.
    def devolver_roto(_c):
        raise RuntimeError("pool cerrado")
    conn = _ConexionFalsa(_CursorFalso(tablas=TODAS))
    r = verificar_geodata(lambda: conn, devolver_roto)
    assert r.estado == OK


# --------------------------------------------------------------------------
# gee: init_ee sola no alcanza
# --------------------------------------------------------------------------

def test_gee_ok_requiere_el_round_trip():
    r = verificar_gee(lambda: None, lambda: 1)
    assert r.estado == OK


def test_gee_que_inicializa_pero_no_responde_se_distingue():
    # Credenciales bien formadas y sin acceso al proyecto: `Initialize` pasa y
    # la primera llamada real falla. Sin round-trip esto se veria como OK.
    def api_muerta():
        raise RuntimeError("Caller does not have required permission")
    r = verificar_gee(lambda: None, api_muerta)
    assert r.estado == FALLA
    assert "inicializa pero la API no responde" in r.detalle


def test_gee_que_no_inicializa_devuelve_falla_no_excepcion():
    def explota():
        raise RuntimeError("Faltan EE_SERVICE_ACCOUNT_EMAIL")
    r = verificar_gee(explota, lambda: 1)
    assert r.estado == FALLA
    assert "no inicializa" in r.detalle


# --------------------------------------------------------------------------
# inngest
# --------------------------------------------------------------------------

def test_inngest_en_produccion_sin_signing_key_es_falla_sin_tocar_la_red():
    r = verificar_inngest(True, "", hay_signing_key=False)
    assert r.estado == FALLA
    assert "SIGNING_KEY" in r.detalle
    # No llego a medir tiempo porque no salio a la red: la falta de clave se
    # decide antes.
    assert r.ms is None


def test_inngest_en_desarrollo_avisa_que_no_verifica_firma(monkeypatch):
    monkeypatch.setattr(socket, "gethostbyname", lambda h: "127.0.0.1")
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: _SocketFalso())
    r = verificar_inngest(False, "http://localhost:8288", hay_signing_key=False)
    assert "SIN verificacion de firma" in r.detalle


# --------------------------------------------------------------------------
# el formato
# --------------------------------------------------------------------------

def test_formatear_lista_los_que_fallan():
    salida = "\n".join(formatear([
        Resultado("minio", OK, "alcanzable", 12, "alcance"),
        Resultado("geodata", FALLA, "no autentica", 340, "todo"),
    ]))
    assert "NO CONECTAN: geodata" in salida
    assert "minio" in salida


def test_formatear_sin_fallas_lo_dice():
    salida = "\n".join(formatear([Resultado("minio", OK, "alcanzable", 12)]))
    assert "Todas las conexiones verificadas responden" in salida
    assert "NO CONECTAN" not in salida


def test_el_alcance_de_cada_prueba_queda_impreso():
    # Un OK de minio no puede leerse como "las credenciales sirven": es la
    # trampa que costo una sesion en el tileserver (DECISIONS #21), al reves.
    salida = "\n".join(formatear([
        Resultado("minio", OK, "alcanzable", 12, "alcance, no credenciales"),
    ]))
    assert "alcance, no credenciales" in salida


def test_la_salida_es_ascii():
    salida = "\n".join(formatear([
        Resultado("geodata", FALLA, "password authentication failed", 340, "todo"),
    ]))
    salida.encode("ascii")


def test_ninguna_linea_trae_saltos_internos():
    for linea in formatear([Resultado("minio", OK, "x", 1, "y")]):
        assert "\n" not in linea


# --------------------------------------------------------------------------
# a_una_linea: los detalles los escribe otro, no nosotros
# --------------------------------------------------------------------------

def test_el_error_de_psycopg2_queda_en_una_linea_y_sin_repetir():
    # Textual de un fallo real: dos lineas iguales y un salto al final.
    # El prefijo del nombre de la excepcion lo agrega `verificar_geodata`, asi
    # que las dos copias NO son iguales: el descarte tiene que ser por
    # contencion, no por igualdad.
    crudo = (
        'OperationalError: connection to server at "aws-0-us-east-1.pooler.supabase.com" '
        '(52.45.94.125), port 5432 failed: FATAL:  password authentication '
        'failed for user "postgres"\n'
        'connection to server at "aws-0-us-east-1.pooler.supabase.com" '
        '(52.45.94.125), port 5432 failed: FATAL:  password authentication '
        'failed for user "postgres"\n'
    )
    r = Resultado("geodata", FALLA, crudo)
    assert "\n" not in r.detalle
    assert r.detalle.count("password authentication failed") == 1


def test_el_error_del_sistema_operativo_se_pasa_a_ascii():
    # Windows emite los errores de socket en el idioma del sistema.
    crudo = "no se puede establecer una conexion porque el equipo rechazo"
    r = Resultado("minio", FALLA, crudo.replace("conexion", "conexi\u00f3n"))
    r.detalle.encode("ascii")
    assert "conexi" in r.detalle


def test_a_una_linea_no_pierde_informacion_util():
    r = Resultado("x", FALLA, "primera linea\nsegunda linea distinta")
    assert "primera linea" in r.detalle
    assert "segunda linea distinta" in r.detalle
