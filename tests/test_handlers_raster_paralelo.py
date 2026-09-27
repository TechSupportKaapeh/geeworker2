"""M.9.7e2: `raster.en_paralelo`, el paso del rancho con las pasadas en hilos."""
import contextvars
import sys
import threading
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from handlers.raster import en_paralelo

_JOB = contextvars.ContextVar("job", default=None)


def test_los_resultados_salen_en_el_orden_de_los_elementos():
    assert en_paralelo(lambda n: n * 2, list(range(10)), hilos=4) == [n * 2 for n in range(10)]


def test_corre_en_hilos():
    vistos = set()
    barrera = threading.Barrier(2, timeout=5)

    def tarea(_):
        # Si corrieran en serie, la barrera no se abriria nunca.
        barrera.wait()
        vistos.add(threading.get_ident())

    en_paralelo(tarea, [1, 2], hilos=2)
    assert len(vistos) == 2


def test_cada_tarea_ve_el_contexto_del_que_llama():
    """El job de la bitacora y el conteo de GEE viven en ContextVar."""
    _JOB.set("job-r")
    assert en_paralelo(lambda _: _JOB.get(), [1, 2, 3], hilos=3) == ["job-r"] * 3


def test_un_error_sale_y_no_se_traga():
    def tarea(n):
        if n == 2:
            raise OSError("se corto la descarga")
        return n

    with pytest.raises(OSError, match="se corto"):
        en_paralelo(tarea, [1, 2, 3], hilos=2)


def test_con_un_solo_elemento_no_abre_hilos():
    assert en_paralelo(lambda _: threading.get_ident(), [1]) == [threading.get_ident()]
