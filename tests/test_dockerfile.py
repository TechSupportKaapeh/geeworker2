"""La imagen trae todo el codigo que `app` importa (M.4.2).

El CI no construye la imagen: la construye Railway al desplegar (`docs/CI.md`,
"Lo que el CI no cubre"). Y el Dockerfile copia el codigo directorio por
directorio, a proposito, para que un olvido sea visible. Visible para quien lo
lee, no para el CI: un paquete nuevo que nadie suma al Dockerfile pasa todos los
tests, se despliega, y el contenedor muere al arrancar con `ModuleNotFoundError`.
Le paso al tileserver, y le iba a pasar al worker con `pipeline/` y `handlers/`.

Este test arma en un directorio temporal **solo** lo que copian los `COPY` del
Dockerfile, e importa `app` ahi, en un proceso aparte con el socket saboteado.
No reemplaza construir la imagen, que ademas prueba las dependencias; cubre la
parte que se olvida.
"""
import re
import shutil
import subprocess
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]

# `COPY origen... destino`, sin `--from` ni `--chown`: el Dockerfile no los usa,
# y si algun dia los usa, este parser tiene que enterarse (ver el test de abajo).
_COPY = re.compile(r"^COPY\s+(?P<args>[^-].*)$", re.MULTILINE)

SABOTAJE = (
    "import socket\n"
    "def prohibido(*a, **k):\n"
    "    raise AssertionError('se intento abrir una conexion')\n"
    "socket.socket.connect = prohibido\n"
    "socket.create_connection = prohibido\n"
    "socket.getaddrinfo = prohibido\n"
)


def _origenes_copiados():
    texto = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    origenes = []
    for coincidencia in _COPY.finditer(texto):
        *fuentes, _destino = coincidencia["args"].split()
        origenes.extend(fuentes)
    return origenes


def _armar_imagen(destino: Path):
    for origen in _origenes_copiados():
        fuente = RAIZ / origen.rstrip("/")
        if fuente.is_dir():
            shutil.copytree(fuente, destino / fuente.name,
                            ignore=shutil.ignore_patterns("__pycache__"))
        else:
            shutil.copy2(fuente, destino / fuente.name)


def test_el_parser_ve_todos_los_copy_del_dockerfile():
    """Control del parser: si no viera un COPY, el test de abajo mentiria."""
    texto = (RAIZ / "Dockerfile").read_text(encoding="utf-8")
    assert len(_COPY.findall(texto)) == len(re.findall(r"^COPY\s", texto, re.MULTILINE))
    assert {"app.py", "services/", "pipeline/", "handlers/"} <= set(_origenes_copiados())


def test_app_se_importa_con_solo_lo_que_copia_el_dockerfile(tmp_path):
    _armar_imagen(tmp_path)
    resultado = subprocess.run(
        [sys.executable, "-c", SABOTAJE + "import app\nprint(len(app.all_functions))"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=120, check=False,
        # Sin el directorio del repo en el path: que `import handlers` lo
        # encuentre en el repo haria pasar el test aunque el COPY faltara.
        env={"PATH": "", "SYSTEMROOT": _systemroot(), "PYTHONPATH": ""},
    )
    assert resultado.returncode == 0, resultado.stderr[-3000:]
    # 7 desde M.4.5: `register_layer` se borro con el `process_rancho` viejo.
    assert resultado.stdout.split()[-1] == "7"


def test_sin_el_copy_de_handlers_app_no_se_importa(tmp_path):
    """Control negativo: la imagen sin `handlers/` es justo el bug que se cuida."""
    _armar_imagen(tmp_path)
    shutil.rmtree(tmp_path / "handlers")
    resultado = subprocess.run(
        [sys.executable, "-c", SABOTAJE + "import app"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=120, check=False,
        env={"PATH": "", "SYSTEMROOT": _systemroot(), "PYTHONPATH": ""},
    )
    assert resultado.returncode != 0
    assert "No module named 'handlers'" in resultado.stderr


def _systemroot():
    # En Windows, sin SYSTEMROOT, el interprete no puede inicializar `random`
    # ni los sockets. En Linux no existe y no hace falta.
    import os
    return os.environ.get("SYSTEMROOT", "")
