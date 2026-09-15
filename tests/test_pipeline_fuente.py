"""M.2.1: la fuente del pipeline (`pipeline/etapas/fuente.py`).

Dos clases de tests:

- los puros, que corren en el CI: qué bandas pide la receta, el remuestreo y el
  rango del mes;
- los marcados `gee`, que arman la colección y le preguntan a GEE de verdad.
  Corren solo con `pytest --gee` (`tests/conftest.py`). Son la verificación contra
  lo real de esta etapa, y la de M.2.6 la completa con parcelas reales.
"""

import dataclasses
from datetime import UTC, datetime

import pytest

from pipeline import indices
from pipeline.etapas import fuente
from pipeline.indices import Indice
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE
from pipeline.registro import registro


def _receta(**cambios):
    return dataclasses.replace(RECETA_VIGENTE, **cambios)


def _ms(anio, mes, dia=1):
    return int(datetime(anio, mes, dia, tzinfo=UTC).timestamp()) * 1000


# ---- Las bandas ------------------------------------------------------------------


def test_la_receta_v1_pide_cinco_bandas_en_el_orden_de_s2():
    # Ordenar el texto daría B11 antes que B2.
    assert fuente.bandas_espectrales(RECETA_VIGENTE) == ("B2", "B4", "B5", "B8", "B11")


def test_solo_ndvi_pide_el_rojo_y_el_nir():
    assert fuente.bandas_espectrales(_receta(indices=("ndvi",))) == ("B4", "B8")


def test_el_nir_viene_aunque_ningun_indice_lo_use(monkeypatch):
    # Todos los índices de v1 usan el NIR, así que hace falta uno que no lo use:
    # la máscara de sombras lo necesita igual.
    sin_nir = registro(Indice("ndvi", "(RED - BLUE) / (RED + BLUE)", (-1.0, 1.0), "prueba"))
    monkeypatch.setattr(fuente, "INDICES", sin_nir)

    assert fuente.bandas_espectrales(_receta(indices=("ndvi",))) == ("B2", "B4", "B8")


def test_cada_banda_pedida_es_de_s2():
    assert set(fuente.bandas_espectrales(RECETA_VIGENTE)) <= indices.BANDAS_S2


# ---- El remuestreo ---------------------------------------------------------------


def test_nearest_no_llama_a_resample():
    # `ee.Image.resample` no acepta "nearest": es lo que GEE hace sin pedirlo.
    assert fuente.metodo_de_remuestreo(RECETA_VIGENTE) is None


@pytest.mark.parametrize("metodo", ["bilinear", "bicubic"])
def test_los_demas_van_a_resample(metodo):
    assert fuente.metodo_de_remuestreo(_receta(remuestreo=metodo)) == metodo


# ---- El rango del mes ------------------------------------------------------------


def test_el_mes_va_del_primer_instante_al_primero_del_siguiente():
    assert fuente.milisegundos(Mes(2026, 9)) == (_ms(2026, 9), _ms(2026, 10))


def test_diciembre_termina_en_enero():
    assert fuente.milisegundos(Mes(2025, 12)) == (_ms(2025, 12), _ms(2026, 1))


def test_febrero_bisiesto_tiene_29_dias():
    inicio, fin = fuente.milisegundos(Mes(2024, 2))
    assert fin - inicio == 29 * 24 * 3600 * 1000


# ---- Contra GEE de verdad (pytest --gee) -------------------------------------------

# Un cuadrado de unos 500 m en el Bajío (Guanajuato), zona agrícola. No es la
# parcela de un cliente: solo hace falta un lugar con pasadas de S2.
ROI_DE_PRUEBA = [
    [-100.850, 20.550],
    [-100.845, 20.550],
    [-100.845, 20.555],
    [-100.850, 20.555],
    [-100.850, 20.550],
]
# Marzo es la estación seca del Bajío: hay pasadas limpias casi seguro.
MES_SECO = Mes(2026, 3)


@pytest.mark.gee
def test_la_coleccion_del_mes_trae_las_bandas_de_la_receta_en_reflectancia(gee_inicializado):
    import ee

    roi = ee.Geometry.Polygon([ROI_DE_PRUEBA])
    col = fuente.coleccion(roi, MES_SECO, RECETA_VIGENTE)
    primera = ee.Image(col.first())

    # Una sola llamada: todo lo que se quiere saber, en un diccionario.
    info = ee.Dictionary({
        "escenas": col.size(),
        "bandas": primera.bandNames(),
        "azimut": primera.get("MEAN_SOLAR_AZIMUTH_ANGLE"),
        "desde": col.aggregate_min("system:time_start"),
        "hasta": col.aggregate_max("system:time_start"),
        "rango": primera.reduceRegion(
            reducer=ee.Reducer.minMax(), geometry=roi, scale=10,
            bestEffort=False, maxPixels=1e6,
        ),
    }).getInfo()

    assert info["escenas"] > 0, "marzo en el Bajío sin escenas: revisar el filtro de fecha"
    assert info["bandas"] == ["B2", "B4", "B5", "B8", "B11", "SCL", "probability"]
    # La máscara de sombras necesita el azimut: sin él, la aritmética perdió las propiedades.
    assert info["azimut"] is not None
    inicio, fin = fuente.milisegundos(MES_SECO)
    assert inicio <= info["desde"] <= info["hasta"] < fin

    rango = info["rango"]
    for banda in ("B2", "B4", "B5", "B8", "B11"):
        # Reflectancia 0-1: una nube brillante puede pasar de 1 por poco, pero
        # nunca llega a los miles de las bandas crudas.
        assert rango[f"{banda}_min"] >= 0, banda
        assert rango[f"{banda}_max"] < 2, f"{banda} parece sin dividir: {rango[f'{banda}_max']}"
    assert 0 <= rango["probability_min"] <= rango["probability_max"] <= 100
    # SCL no se tocó: sigue siendo una clase entera de 0 a 11.
    assert rango["SCL_max"] <= 11
    assert float(rango["SCL_max"]).is_integer()


@pytest.mark.gee
def test_bilinear_es_un_remuestreo_que_gee_acepta(gee_inicializado):
    import ee

    roi = ee.Geometry.Polygon([ROI_DE_PRUEBA])
    col = fuente.coleccion(roi, MES_SECO, _receta(remuestreo="bilinear"))

    assert ee.Image(col.first()).bandNames().size().getInfo() == 7


@pytest.mark.gee
def test_un_mes_sin_escenas_da_una_coleccion_vacia(gee_inicializado):
    import ee

    roi = ee.Geometry.Polygon([ROI_DE_PRUEBA])

    assert fuente.coleccion(roi, Mes(2035, 1), RECETA_VIGENTE).size().getInfo() == 0
