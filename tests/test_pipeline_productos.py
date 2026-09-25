"""M.2.5: los productos del mes (`pipeline/productos.py`).

El módulo encadena las cuatro etapas, así que lo que hay que probar es que las dos
ramas —los números de una parcela y el mapa de un rancho— salen **del mismo
compuesto**. Eso va contra GEE (`pytest --gee`). En el CI corre la validación del
índice pedido, que no necesita GEE porque ocurre antes de armar nada.
"""

import dataclasses

import pytest

from pipeline import productos
from pipeline.etapas import compuesto, reduccion
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE
from pipeline.ventanas import del_mes


def test_un_indice_fuera_de_la_receta_se_rechaza():
    # Sin este chequeo, `select` devolvería una imagen sin bandas y el error
    # aparecería recién al descargar el COG.
    with pytest.raises(ValueError, match="savi"):
        productos.mapa_de(None, del_mes(Mes(2026, 7)), RECETA_VIGENTE, "savi")


def test_se_puede_pedir_cualquier_indice_de_la_receta():
    receta = dataclasses.replace(RECETA_VIGENTE, indices=("ndvi",))

    with pytest.raises(ValueError, match="evi"):
        productos.mapa_de(None, del_mes(Mes(2026, 7)), receta, "evi")


# ---- Contra GEE de verdad (pytest --gee) -------------------------------------------

ROI_2KM = [-100.86, 20.54, -100.84, 20.56]
MES = Mes(2026, 7)


@pytest.mark.gee
def test_el_compuesto_trae_los_indices_y_las_observaciones(gee_inicializado):
    import ee

    roi = ee.Geometry.Rectangle(ROI_2KM)

    bandas = productos.compuesto_de(roi, del_mes(MES), RECETA_VIGENTE).bandNames().getInfo()

    assert bandas == list(compuesto.bandas_de_salida(RECETA_VIGENTE))


@pytest.mark.gee
def test_el_mapa_del_mes_es_una_sola_banda_recortada(gee_inicializado):
    import ee

    roi = ee.Geometry.Rectangle(ROI_2KM)
    mapa = productos.mapa_de(roi, del_mes(MES), RECETA_VIGENTE, "ndvi")

    # Solo las bandas. La proyección por defecto de esta imagen es WGS84 de 1°
    # (111.319 m, medido el 2026-09-16): la aritmética de bandas la pierde, y la
    # escala real la fija la descarga con `scale` y `crs` (DECISIONS #19).
    assert mapa.bandNames().getInfo() == ["ndvi"]


@pytest.mark.gee
def test_el_numero_de_la_parcela_y_el_mapa_salen_de_los_mismos_pixeles(gee_inicializado):
    # Es B-1, cerrado por construcción (ARQUITECTURA §2): las dos ramas parten del
    # mismo compuesto. Si alguna volviera a armar el suyo —con otra máscara u otra
    # escala—, la mediana del mapa y la de las estadísticas dejarían de coincidir.
    import ee

    roi = ee.Geometry.Rectangle(ROI_2KM)
    mapa = productos.mapa_de(roi, del_mes(MES), RECETA_VIGENTE, "ndvi")

    info = ee.Dictionary({
        "estadisticas": productos.estadisticas_de(roi, del_mes(MES), RECETA_VIGENTE),
        "mediana_del_mapa": mapa.reduceRegion(
            reducer=ee.Reducer.percentile([50]),
            geometry=roi,
            scale=RECETA_VIGENTE.escala_m,
            bestEffort=False,
            maxPixels=reduccion.MAX_PIXELES,
        ),
    }).getInfo()

    leida = reduccion.leer(info["estadisticas"], RECETA_VIGENTE)

    assert info["mediana_del_mapa"]["ndvi"] == pytest.approx(
        leida.estadisticas["ndvi"]["mediana"], abs=1e-6
    )
