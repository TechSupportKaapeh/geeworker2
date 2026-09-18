"""M.1.5: importar `pipeline` no toca la red.

Es la regla del paquete (`pipeline/__init__.py`, `DECISIONS #24`): el nucleo arma
cosas, no las pide. El test importa CADA modulo de `pipeline/` en un proceso
aparte con el socket saboteado: si algo abriera una conexion al importarse, el
import fallaria con nuestro mensaje. Los modulos se recorren con `pkgutil`, asi
que los que se sumen en M.2 (las etapas, `ejecucion.py`) entran solos.

Proceso aparte por dos motivos: el import se cachea por interprete, y dentro de
la suite otro test ya inicializo GEE de verdad cuando hay un `.env` local (ver
`test_pipeline_estadisticas.py`).
"""
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

SABOTAJE = (
    "import socket\n"
    "def prohibido(*a, **k):\n"
    "    raise AssertionError('se intento abrir una conexion')\n"
    "socket.socket.connect = prohibido\n"
    "socket.socket.connect_ex = prohibido\n"
    "socket.create_connection = prohibido\n"
    "socket.getaddrinfo = prohibido\n"
)


def _correr_saboteado(codigo):
    return subprocess.run(
        [sys.executable, "-c", SABOTAJE + codigo],
        cwd=str(RAIZ), capture_output=True, text=True, timeout=120, check=False,
    )


def test_importar_cada_modulo_de_pipeline_no_abre_conexiones():
    resultado = _correr_saboteado(
        "import importlib, pkgutil\n"
        "import pipeline\n"
        "for modulo in pkgutil.walk_packages(pipeline.__path__, 'pipeline.'):\n"
        "    importlib.import_module(modulo.name)\n"
        "    print(modulo.name)\n"
    )
    assert resultado.returncode == 0, resultado.stderr
    # Que el recorrido de verdad encuentre los modulos: vacio, pasaria igual.
    assert {
        "pipeline.periodos", "pipeline.formulas", "pipeline.registro",
        "pipeline.indices", "pipeline.estadisticas", "pipeline.receta",
        # M.2: la primera etapa que importa `ee`. `import ee` no abre conexiones.
        "pipeline.etapas", "pipeline.etapas.fuente",
        # M.4.1: la key del COG mensual.
        "pipeline.claves",
    } <= set(resultado.stdout.split())


def test_el_sabotaje_de_verdad_frena_una_conexion():
    """Control negativo: un sabotaje mal hecho haria pasar el test de arriba siempre."""
    resultado = _correr_saboteado(
        "import socket\n"
        "socket.create_connection(('example.com', 80), timeout=1)\n"
    )
    assert resultado.returncode != 0
    assert "se intento abrir una conexion" in resultado.stderr
