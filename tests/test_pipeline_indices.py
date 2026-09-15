"""M.1.2: el registro de indices y el lenguaje de sus formulas.

La aceptacion del tablero: un evaluador de Python calcula la misma formula contra
valores de referencia; nombres unicos; bandas que existen en S2; rangos
coherentes.

Los valores de referencia salen de la definicion publicada de cada indice,
escrita aca aparte y a mano (`ndvi_rouse`, `evi_huete`...). Lo que se prueba es
que el TEXTO del registro diga lo mismo que la definicion: un parentesis de menos
o un coeficiente cambiado da otro numero y el test sale rojo. Los espectros son
tipicos y redondeados, no mediciones.
"""
import sys
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from pipeline.formulas import bandas_de, evaluar
from pipeline.indices import BANDAS, BANDAS_S2, INDICES, Indice
from pipeline.registro import registro

# Reflectancia de superficie 0-1, valores tipicos redondeados.
ESPECTROS = {
    "vegetacion densa": {"BLUE": 0.03, "RED": 0.04, "RE1": 0.10, "NIR": 0.45, "SWIR1": 0.20},
    "suelo desnudo": {"BLUE": 0.10, "RED": 0.20, "RE1": 0.23, "NIR": 0.28, "SWIR1": 0.35},
    "agua": {"BLUE": 0.06, "RED": 0.04, "RE1": 0.03, "NIR": 0.02, "SWIR1": 0.01},
}


# --- Las definiciones publicadas, escritas aparte -------------------------


def diferencia_normalizada(a, b):
    return (a - b) / (a + b)


def evi_huete(nir, red, blue, g=2.5, c1=6.0, c2=7.5, fondo=1.0):
    """Huete et al. (2002), con sus coeficientes para reflectancia 0-1."""
    return g * (nir - red) / (nir + c1 * red - c2 * blue + fondo)


DEFINICIONES = {
    "ndvi": lambda b: diferencia_normalizada(b["NIR"], b["RED"]),  # Rouse et al. (1974)
    "evi": lambda b: evi_huete(b["NIR"], b["RED"], b["BLUE"]),
    "ndre": lambda b: diferencia_normalizada(b["NIR"], b["RE1"]),  # Barnes et al. (2000)
    "ndmi": lambda b: diferencia_normalizada(b["NIR"], b["SWIR1"]),  # Wilson y Sader (2002)
}


# --- El registro ----------------------------------------------------------


def test_la_receta_v1_trae_los_cuatro_indices():
    assert list(INDICES) == ["ndvi", "evi", "ndre", "ndmi"]
    assert set(DEFINICIONES) == set(INDICES)
    for nombre, indice in INDICES.items():
        assert indice.nombre == nombre


def test_las_bandas_existen_en_s2_sr():
    assert set(BANDAS.values()) <= BANDAS_S2
    assert "B10" not in BANDAS_S2  # cirros: no esta en el producto de superficie
    for indice in INDICES.values():
        assert indice.bandas <= BANDAS.keys(), indice.nombre


def test_el_registro_es_inmutable():
    with pytest.raises(TypeError):
        INDICES["ndvi"] = INDICES["evi"]
    with pytest.raises(AttributeError):
        INDICES["ndvi"].formula = "NIR"


# --- Valores de referencia ------------------------------------------------


@pytest.mark.parametrize("espectro", ESPECTROS)
@pytest.mark.parametrize("nombre", ["ndvi", "evi", "ndre", "ndmi"])
def test_la_formula_de_texto_dice_lo_mismo_que_la_definicion(nombre, espectro):
    bandas = ESPECTROS[espectro]
    esperado = DEFINICIONES[nombre](bandas)
    assert evaluar(INDICES[nombre].formula, bandas) == pytest.approx(esperado, rel=1e-12)


@pytest.mark.parametrize("nombre,bandas,esperado", [
    ("ndvi", {"NIR": 0.5, "RED": 0.1}, 0.4 / 0.6),
    ("evi", {"NIR": 0.5, "RED": 0.1, "BLUE": 0.05}, 1.0 / 1.725),
    ("ndre", {"NIR": 0.45, "RE1": 0.2}, 0.25 / 0.65),
    ("ndmi", {"NIR": 0.4, "SWIR1": 0.2}, 0.2 / 0.6),
])
def test_valores_calculados_a_mano(nombre, bandas, esperado):
    assert evaluar(INDICES[nombre].formula, bandas) == pytest.approx(esperado)


def test_evi_depende_de_la_escala_y_ndvi_no():
    """ARQUITECTURA §8.6: por esto la fuente divide por 10.000.

    Con las bandas crudas de S2 SR (reflectancia x 10.000) el `+ 1` de EVI se
    vuelve despreciable, y el indice de la vegetacion densa sale en 2,2: fuera de
    rango. NDVI da lo mismo en las dos escalas porque el cociente la cancela.
    """
    reflectancia = ESPECTROS["vegetacion densa"]
    crudas = {banda: valor * 10_000 for banda, valor in reflectancia.items()}
    ndvi, evi = INDICES["ndvi"].formula, INDICES["evi"].formula

    assert evaluar(ndvi, crudas) == pytest.approx(evaluar(ndvi, reflectancia))
    assert evaluar(evi, reflectancia) == pytest.approx(0.6997, abs=1e-4)
    assert evaluar(evi, crudas) == pytest.approx(2.2038, abs=1e-4)
    minimo, maximo = INDICES["evi"].rango
    assert not minimo <= evaluar(evi, crudas) <= maximo


# --- Rangos coherentes ----------------------------------------------------


@pytest.mark.parametrize("espectro", ESPECTROS)
def test_los_espectros_tipicos_caen_dentro_del_rango(espectro):
    for indice in INDICES.values():
        minimo, maximo = indice.rango
        assert minimo <= evaluar(indice.formula, ESPECTROS[espectro]) <= maximo, indice.nombre


@pytest.mark.parametrize("nombre", ["ndvi", "ndre", "ndmi"])
def test_una_diferencia_normalizada_nunca_sale_de_menos_uno_a_uno(nombre):
    grilla = [0.001, 0.01, 0.1, 0.3, 0.5, 1.0]
    formula = INDICES[nombre].formula
    (a, b) = sorted(INDICES[nombre].bandas)
    for x in grilla:
        for y in grilla:
            assert -1.0 <= evaluar(formula, {a: x, b: y}) <= 1.0


def test_el_orden_fisico_se_respeta():
    ndvi = {nombre: evaluar(INDICES["ndvi"].formula, b) for nombre, b in ESPECTROS.items()}
    assert ndvi["vegetacion densa"] > ndvi["suelo desnudo"] > 0 > ndvi["agua"]
    ndmi = {nombre: evaluar(INDICES["ndmi"].formula, b) for nombre, b in ESPECTROS.items()}
    assert ndmi["vegetacion densa"] > ndmi["suelo desnudo"]


# --- Lo que un Indice y un registro rechazan ------------------------------


def test_un_indice_con_una_banda_que_no_existe_se_rechaza():
    with pytest.raises(ValueError, match="GREEN"):
        Indice("gndvi", "(NIR - GREEN) / (NIR + GREEN)", rango=(-1.0, 1.0), tema="x")


@pytest.mark.parametrize("rango", [(1.0, -1.0), (0.5, 0.5)])
def test_un_indice_con_el_rango_invertido_se_rechaza(rango):
    with pytest.raises(ValueError, match="rango"):
        Indice("ndvi", "(NIR - RED) / (NIR + RED)", rango=rango, tema="x")


@pytest.mark.parametrize("nombre", ["NDVI", "nd_vi", "1ndvi", "", "nd/vi", "ndvi "])
def test_el_registro_rechaza_nombres_invalidos(nombre):
    with pytest.raises(ValueError, match="nombre"):
        registro(Indice(nombre, "NIR", rango=(-1.0, 1.0), tema="x"))


def test_el_registro_rechaza_nombres_repetidos_y_el_vacio():
    ndvi = INDICES["ndvi"]
    with pytest.raises(ValueError, match="repetido"):
        registro(ndvi, INDICES["evi"], ndvi)
    with pytest.raises(ValueError):
        registro()


# --- El lenguaje de las formulas ------------------------------------------


@pytest.mark.parametrize("formula", [
    "NIR ** 2", "NIR % 2", "NIR // 2", "abs(NIR)", "NIR.real", "NIR > RED",
    "NIR if RED else BLUE", "True", "'NIR'", "[NIR]", "1j", "", "NIR +",
    "__import__('os').system('echo hola')",
])
def test_lo_que_no_significa_lo_mismo_en_gee_y_en_python_se_rechaza(formula):
    with pytest.raises(ValueError):
        bandas_de(formula)


def test_lo_permitido():
    assert bandas_de("-(NIR - RED) / (+NIR + 2.5 * RED)") == {"NIR", "RED"}
    assert bandas_de("1") == frozenset()
    assert evaluar("-NIR + 1", {"NIR": 0.25}) == pytest.approx(0.75)


def test_evaluar_nombra_la_banda_que_falta():
    with pytest.raises(ValueError, match="RED"):
        evaluar("(NIR - RED) / (NIR + RED)", {"NIR": 0.5})


def test_evaluar_no_esconde_una_division_por_cero():
    # GEE devuelve 0 segun su documentacion; el evaluador de referencia prefiere
    # fallar a inventar un numero (ver el docstring de `evaluar`).
    with pytest.raises(ZeroDivisionError):
        evaluar("(NIR - RED) / (NIR + RED)", {"NIR": 0.0, "RED": 0.0})
