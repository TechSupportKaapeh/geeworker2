"""Opciones comunes de la suite.

``--gee`` corre los tests marcados ``gee``, que le hablan de verdad a Google
Earth Engine con las credenciales del ``.env`` local. **Sin la opción se
saltean, aunque haya credenciales.** Un test no sale a la red porque sí: es la
lección de ``DECISIONS #37``, donde un test le hablaba a GEE, a la base y a MinIO
solo porque había un ``.env``.

El CI no pasa ``--gee`` y no tiene credenciales (``CI.md``). Esos tests son la
verificación contra lo real de las etapas de M.2 (``WORKFLOW`` §6), y se corren
en local antes de abrir el PR:

    .venv\\Scripts\\python.exe -m pytest tests -q --gee -m gee
"""

import pytest


def pytest_addoption(parser):
    parser.addoption(
        "--gee",
        action="store_true",
        default=False,
        help="corre los tests marcados gee, que le hablan a GEE de verdad (pide el .env)",
    )


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "gee: le habla a Google Earth Engine de verdad; corre solo con --gee"
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption("--gee"):
        return
    saltear = pytest.mark.skip(reason="le habla a GEE de verdad: correr con --gee")
    for item in items:
        if item.get_closest_marker("gee") is not None:
            item.add_marker(saltear)


@pytest.fixture(scope="session")
def gee_inicializado():
    """Inicializa GEE una vez por corrida. Solo lo piden los tests ``gee``."""
    from services.ee.ee_client import init_ee

    init_ee()
