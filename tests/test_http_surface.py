"""La superficie HTTP del worker es un contrato, y este test es su compuerta.

`DECISIONS #23`: el worker se dispara por eventos y **no expone API de lectura**.
Eso lo sirve Geocore, que es el dueño del catálogo y el único que aplica
aislamiento de tenant.

Una decisión así se erosiona sola: agregar un `@app.get` es una línea, y nada
avisa. Lo que se borró en FASE D había llegado por ese camino —diez archivos en
`routes/`, ocho ni siquiera montados, con `tenant_id` llegando por query param.
Este test falla en cuanto aparece una ruta nueva, así que agregar una obliga a
volver acá y decidirlo a propósito.
"""

import app as app_module

# GET/HEAD juntos: FastAPI agrega HEAD solo en cada GET.
SUPERFICIE_ESPERADA = {
    ("/health", "GET"),
    ("/api/inngest", "GET"),
    ("/api/inngest", "POST"),
    ("/api/inngest", "PUT"),
}


def _superficie():
    return {
        (r.path, m)
        for r in app_module.app.routes
        for m in (getattr(r, "methods", None) or ())
        if m != "HEAD"
    }


def test_la_superficie_http_es_exactamente_health_e_inngest():
    assert _superficie() == SUPERFICIE_ESPERADA


def test_no_hay_endpoints_de_lectura():
    """Los que borró FASE D, por si alguien los reintroduce con otro router."""
    borradas = {"/assets", "/measurements", "/compute", "/heatmap", "/time-series",
                "/export", "/dates", "/upload-kml", "/auth/info"}
    paths = {r.path for r in app_module.app.routes}
    assert not (paths & borradas)


def test_no_expone_openapi_ni_docs():
    """Sin endpoints de negocio no hay nada que documentar, y el worker puede
    terminar con URL pública si se usa Inngest Cloud."""
    paths = {r.path for r in app_module.app.routes}
    assert not (paths & {"/docs", "/redoc", "/openapi.json"})


def test_no_hay_cors_abierto():
    """El `allow_origins=["*"]` existía para los endpoints de lectura. Ningún
    navegador le habla al worker: el front pega a Geocore."""
    nombres = [m.cls.__name__ for m in app_module.app.user_middleware]
    assert "CORSMiddleware" not in nombres


def test_el_worker_arranca_aunque_falten_las_credenciales(monkeypatch):
    """Un deploy mal configurado tiene que poder arrancar **para poder
    diagnosticarse**.

    En el primer deploy a Railway faltaban las variables de GEE. `init_ee()`
    estaba fuera del `try` —el `init_db()` de entonces si estaba adentro— asi
    que levanto, uvicorn aborto, Railway reinicio, y el ciclo se repitio: logs de varios
    procesos entrelazados y `/health` sin responder nunca. Es exactamente la
    patologia que `DECISIONS #21` describe para los chequeos de salud, y el
    criterio de `DECISIONS #16`: un secreto faltante degrada una funcionalidad,
    no tumba el servicio.

    Cuesta poco sostenerlo porque los handlers llaman a `init_ee()` por su
    cuenta, una vez por step: la del arranque es un precalentamiento. Sin
    credenciales cada invocacion falla por separado y la reintenta Inngest.

    **Y sin tocar la red** (2026-09-15). `_startup()` termina en
    `registrar_conexiones()`, que importa su propio `init_ee` y verifica el
    disco, MinIO, `geodata`, GEE e Inngest. Reemplazar solo los que arranca
    `_startup()` no alcanzaba: con el `.env` local, este test le hablaba de
    verdad a GEE, a la base y a MinIO, y en el CI pasaba solo porque ahi no
    hay `.env`. El socket saboteado lo vuelve comprobable: una conexion HTTP
    que se escape (GEE, MinIO, Inngest) queda anotada y el test falla, aunque
    `_startup()` se trague el error. Control negativo del 2026-09-15: con el
    test como estaba, anoto 9 intentos, a los puertos 443 y 8288.

    **Lo que el guardia no ve:** la base. psycopg2 se conecta desde libpq, en
    C, sin pasar por el `socket` de Python; en ese control no aparecio ningun
    intento a Postgres. Lo que impide llegar a la base es reemplazar
    `registrar_conexiones`, no el guardia. Hasta M.6.1 habia que reemplazar
    tambien `init_db`, que el arranque ya no llama.
    """
    import socket

    import app as modulo

    def _revienta():
        raise RuntimeError("Faltan EE_SERVICE_ACCOUNT_EMAIL o EE_SERVICE_ACCOUNT_KEY_JSON")

    conexiones = []

    def _sin_red(_sock, direccion, *_args):
        conexiones.append(direccion)
        raise OSError("este test no tiene red")

    reportes = []
    monkeypatch.setattr(socket.socket, "connect", _sin_red)
    monkeypatch.setattr(modulo, "init_ee", _revienta)
    monkeypatch.setattr(modulo, "registrar_conexiones", reportes.append)

    # No debe propagar: si lo hiciera, uvicorn abortaria el arranque.
    modulo._startup()

    assert len(reportes) == 1, "el arranque tiene que seguir terminando en el reporte de conexiones"
    assert conexiones == [], f"el arranque intento conectarse a {conexiones}"
