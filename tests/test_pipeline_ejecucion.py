"""M.2.5: el borde con GEE (`pipeline/ejecucion.py`).

Casi todo corre en el CI: `traer()` solo le pide `getInfo()` a lo que recibe, así
que una expresión de mentira alcanza para probar el plazo, el conteo y la
traducción de errores. Contra GEE va un solo test, el que comprueba que el mes de
una parcela cuesta **una** llamada.
"""

import threading
import time

import pytest

from pipeline import ejecucion
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE


class _Expresion:
    """Una expresión de mentira. `traer()` solo le pide `getInfo()`."""

    def __init__(self, resultado=None, error=None):
        self.resultado = resultado
        self.error = error
        self.veces = 0
        self.plazo_visto = None

    def getInfo(self):
        import ee

        self.veces += 1
        self.plazo_visto = ee.data._get_state().deadline_ms
        if self.error is not None:
            raise self.error
        return self.resultado

    def getDownloadURL(self, parametros):
        return self.getInfo()


def _plazo_actual():
    import ee

    return ee.data._get_state().deadline_ms


# ---- La traducción de errores ------------------------------------------------------


@pytest.mark.parametrize("frase", ejecucion._DEFINITIVOS)
def test_lo_que_no_se_arregla_reintentando(frase):
    # Reintentar esto da el mismo error tres veces, y gasta cuota. Se recorre la
    # lista del módulo: sumar una familia sin probarla no debería ser posible.
    assert ejecucion.es_reintentable(RuntimeError(f"GEE dijo: {frase.title()}.")) is False


@pytest.mark.parametrize("frase", ejecucion._PASAJEROS)
def test_lo_que_se_resuelve_solo(frase):
    assert ejecucion.es_reintentable(RuntimeError(f"GEE dijo: {frase.title()}.")) is True


@pytest.mark.parametrize(
    "mensaje",
    [
        "Image.reduceRegion: Too many pixels in the region.",
        "User memory limit exceeded.",
    ],
)
def test_los_mensajes_tal_como_llegan_de_gee(mensaje):
    # Los de arriba son frases sueltas; estos son mensajes completos, con el
    # prefijo del método, como los devuelve GEE de verdad.
    assert ejecucion.es_reintentable(RuntimeError(mensaje)) is False


def test_un_error_desconocido_se_reintenta():
    # Equivocarse hacia el reintento cuesta una llamada; hacia el descarte, el mes.
    assert ejecucion.es_reintentable(RuntimeError("algo raro")) is True


def test_traer_envuelve_el_error_y_conserva_la_causa():
    original = RuntimeError("User memory limit exceeded.")

    with pytest.raises(ejecucion.ErrorDeGEE) as caso:
        ejecucion.traer(_Expresion(error=original))

    assert caso.value.reintentable is False
    assert caso.value.__cause__ is original


def test_la_url_de_descarga_tambien_traduce():
    with pytest.raises(ejecucion.ErrorDeGEE) as caso:
        ejecucion.url_de_descarga(_Expresion(error=RuntimeError("Too many concurrent aggregations.")), {})

    assert caso.value.reintentable is True


# ---- El plazo ----------------------------------------------------------------------


def test_traer_devuelve_lo_calculado():
    assert ejecucion.traer(_Expresion({"ndvi_p50": 0.5})) == {"ndvi_p50": 0.5}


def test_sin_cliente_de_gee_el_plazo_no_se_pone_ni_rompe():
    # `setDeadline` reconstruye el cliente HTTP y sin sesión levanta un
    # `AssertionError` del propio `ee` (verificado el 2026-09-16). Sin cliente no
    # hay pedido que limitar: el plazo se saltea y el `getInfo()` sigue su curso.
    expresion = _Expresion("ok")

    assert ejecucion.traer(expresion, milisegundos=5000) == "ok"
    assert expresion.plazo_visto == 0


@pytest.mark.gee
def test_el_pedido_se_hace_con_el_plazo_puesto(gee_inicializado):
    # Pide cliente: es lo que `setDeadline` exige.
    expresion = _Expresion({})

    ejecucion.traer(expresion, milisegundos=5000)

    assert expresion.plazo_visto == 5000


@pytest.mark.gee
def test_el_plazo_vuelve_a_lo_que_estaba(gee_inicializado):
    antes = _plazo_actual()

    with ejecucion.plazo(7000):
        assert _plazo_actual() == 7000

    assert _plazo_actual() == antes


@pytest.mark.gee
def test_el_plazo_se_restaura_aunque_el_pedido_falle(gee_inicializado):
    antes = _plazo_actual()

    with pytest.raises(ejecucion.ErrorDeGEE):
        ejecucion.traer(_Expresion(error=RuntimeError("Backend error.")))

    assert _plazo_actual() == antes


# ---- El plazo con varios hilos (M.4.8) ---------------------------------------------
#
# Desde que el worker atiende steps en paralelo, dos hilos entran a `plazo()` a la
# vez. `setDeadline` es del cliente, no del pedido: el que sale primero no puede
# sacarle el plazo al que sigue trabajando. Se prueba sobre `_PlazoCompartido`,
# que es la pieza pura, con un doble de `ee.data`: el de verdad pide cliente y no
# corre en el CI.


class _ClienteFalso:
    """`ee.data` de mentira: recuerda el plazo puesto."""

    def __init__(self, inicial=0):
        self.deadline_ms = inicial
        self.puestos = []

    def _get_state(self):
        return self

    def setDeadline(self, ms):  # noqa: N802 - el nombre de ee
        self.deadline_ms = ms
        self.puestos.append(ms)


@pytest.fixture
def cliente_falso(monkeypatch):
    falso = _ClienteFalso(inicial=3000)
    monkeypatch.setattr(ejecucion.ee, "data", falso)
    return falso


def test_el_ultimo_hilo_que_sale_restaura_el_plazo(cliente_falso):
    compartido = ejecucion._PlazoCompartido()

    compartido.entrar(9000)
    compartido.entrar(9000)
    compartido.salir()
    # El primero salió, pero el segundo sigue trabajando: el plazo tiene que seguir.
    assert cliente_falso.deadline_ms == 9000

    compartido.salir()
    assert cliente_falso.deadline_ms == 3000


def test_el_plazo_se_pone_una_sola_vez_aunque_entren_muchos(cliente_falso):
    """`setDeadline` reconstruye el cliente HTTP: llamarlo de más es cambiarle
    el piso a un pedido en vuelo de otro hilo."""
    compartido = ejecucion._PlazoCompartido()

    for _ in range(5):
        compartido.entrar(9000)
    for _ in range(5):
        compartido.salir()

    assert cliente_falso.puestos == [9000, 3000]


def test_muchos_hilos_a_la_vez_dejan_el_plazo_como_estaba(cliente_falso):
    compartido = ejecucion._PlazoCompartido()
    arranquen = threading.Event()

    def _usar():
        arranquen.wait()
        compartido.entrar(9000)
        time.sleep(0.01)
        compartido.salir()

    hilos = [threading.Thread(target=_usar) for _ in range(12)]
    for hilo in hilos:
        hilo.start()
    arranquen.set()
    for hilo in hilos:
        hilo.join()

    assert cliente_falso.deadline_ms == 3000
    # Mientras hubo hilos adentro, el plazo nunca volvió al anterior.
    assert cliente_falso.puestos[-1] == 3000
    assert cliente_falso.puestos.count(3000) == 1


# ---- El conteo ---------------------------------------------------------------------


def test_se_cuentan_las_llamadas_del_step():
    with ejecucion.contando() as conteo:
        ejecucion.traer(_Expresion(1))
        ejecucion.traer(_Expresion(2))
        ejecucion.url_de_descarga(_Expresion("url"), {})

    assert conteo.llamadas == 3


def test_un_pedido_que_falla_tambien_cuenta():
    # Si no contara, la bitácora diría que el step no le habló a GEE.
    with ejecucion.contando() as conteo, pytest.raises(ejecucion.ErrorDeGEE):
        ejecucion.traer(_Expresion(error=RuntimeError("Backend error.")))

    assert conteo.llamadas == 1


def test_fuera_de_un_conteo_no_se_rompe_nada():
    assert ejecucion.traer(_Expresion("ok")) == "ok"


def test_el_conteo_no_sobrevive_al_bloque():
    with ejecucion.contando():
        pass

    assert ejecucion._conteo.get() is None


# ---- Contra GEE de verdad (pytest --gee) -------------------------------------------

ROI_2KM = [-100.86, 20.54, -100.84, 20.56]
MES = Mes(2026, 7)


@pytest.mark.gee
def test_un_error_definitivo_de_gee_llega_traducido(gee_inicializado):
    # Cierra el círculo entre la tabla de errores y lo que GEE contesta de verdad.
    # Un tope de píxeles imposible no se arregla reintentando: el mismo pedido da
    # el mismo error las tres veces, y cada una gasta cuota.
    import ee

    from pipeline import productos

    roi = ee.Geometry.Rectangle(ROI_2KM)
    imposible = (
        productos.compuesto_del_mes(roi, MES, RECETA_VIGENTE)
        .select(["ndvi"])
        .reduceRegion(
            reducer=ee.Reducer.mean(), geometry=roi, scale=1,
            bestEffort=False, maxPixels=10,
        )
    )

    with pytest.raises(ejecucion.ErrorDeGEE) as caso:
        ejecucion.traer(imposible)

    assert caso.value.reintentable is False, str(caso.value)


@pytest.mark.gee
def test_el_mes_de_una_parcela_cuesta_una_sola_llamada(gee_inicializado):
    import ee

    roi = ee.Geometry.Rectangle(ROI_2KM)

    with ejecucion.contando() as conteo:
        leida = ejecucion.reduccion_del_mes(roi, MES, RECETA_VIGENTE)

    assert conteo.llamadas == 1, "el mes entero se pide junto: estadísticas, cobertura y n_obs"
    assert 0 < leida.cobertura <= 1
    assert set(leida.estadisticas) == set(RECETA_VIGENTE.indices)
