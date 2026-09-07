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
