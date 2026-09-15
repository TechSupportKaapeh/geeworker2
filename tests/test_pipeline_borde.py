"""El borde con GEE es uno solo (`ARQUITECTURA_PIPELINE.md` §3.3).

Las etapas arman expresiones; `pipeline/ejecucion.py` (M.2.5) es el único módulo
que le pide algo a GEE. Ahí viven el deadline, la traducción de errores y el
conteo de llamadas. Un `getInfo()` suelto en una etapa se saltearía las tres, y
nada fallaría: el número saldría igual.

El test lee el código de `pipeline/` y busca las llamadas que piden un cálculo.
Es texto, no un análisis: un alias como `pedir = img.getInfo` se le escapa. Pero
agarra el caso normal, que es el que llega por costumbre.
"""

import re
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1] / "pipeline"
BORDE = PIPELINE / "ejecucion.py"

PIDE_UN_CALCULO = re.compile(
    r"\.(getInfo|getDownloadURL|getThumbURL|getMapId|computePixels|computeFeatures"
    r"|evaluate)\s*\(|\bee\.data\."
)


def _infractores():
    encontrados = []
    for archivo in sorted(PIPELINE.rglob("*.py")):
        if archivo == BORDE:
            continue
        for numero, linea in enumerate(archivo.read_text(encoding="utf-8").splitlines(), 1):
            if PIDE_UN_CALCULO.search(linea):
                encontrados.append(f"{archivo.relative_to(PIPELINE)}:{numero}: {linea.strip()}")
    return encontrados


def test_fuera_de_ejecucion_nada_le_pide_un_calculo_a_gee():
    assert _infractores() == []


def test_el_patron_agarra_las_llamadas_que_tiene_que_agarrar():
    """Control negativo: un patrón roto haría pasar el test de arriba siempre."""
    for linea in (
        "n = coleccion.size().getInfo()",
        "url = img.getDownloadURL(params)",
        "ee.data.setDeadline(30_000)",
        "img.evaluate(callback)",
    ):
        assert PIDE_UN_CALCULO.search(linea), linea


def test_el_patron_deja_pasar_la_documentacion():
    # Los docstrings nombran estas funciones: "nada acá llama a ``getInfo()``".
    assert not PIDE_UN_CALCULO.search("Arman, no calculan: ninguna llama a ``getInfo()``.")


def test_el_recorrido_de_verdad_lee_las_etapas():
    # Vacío, el primer test pasaría igual.
    leidos = {p.relative_to(PIPELINE).as_posix() for p in PIPELINE.rglob("*.py")}
    assert "etapas/fuente.py" in leidos
