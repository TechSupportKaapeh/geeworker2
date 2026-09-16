"""M.2.2: la máscara de nubes y sombras (`pipeline/etapas/nubes.py`).

Casi todo se prueba contra GEE (`pytest --gee`, `DECISIONS #38`): la máscara es
una expresión, y lo que importa es qué píxeles descarta. Los tests usan una
escena del 4 de julio de 2026 con un 30 % de nubes sobre un cuadrado de 2 km en
el Bajío. Se eligió con un sondeo del 2026-09-15: tiene nubes, y por lo tanto
sombras, sin estar tapada.
"""

import dataclasses

import pytest

from pipeline.etapas import fuente, nubes
from pipeline.indices import INDICES
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE


def test_las_capas_de_la_mascara_no_chocan_con_otras_bandas():
    # El compuesto (M.2.3) suma bandas con los nombres de los índices: una capa de
    # la máscara con el mismo nombre la pisaría sin avisar.
    otras = {*fuente.bandas_espectrales(RECETA_VIGENTE), fuente.BANDA_CLASIFICACION,
             fuente.BANDA_PROBABILIDAD, *INDICES}
    assert len(set(nubes.COMPONENTES)) == len(nubes.COMPONENTES)
    assert not set(nubes.COMPONENTES) & otras


# ---- Contra GEE de verdad (pytest --gee) -------------------------------------------

ROI_2KM = [-100.86, 20.54, -100.84, 20.56]
ESCENA_NUBLADA = "20260704T171721_20260704T171846_T14QKH"
MES = Mes(2026, 7)


def _escena(ee):
    roi = ee.Geometry.Rectangle(ROI_2KM)
    escena = (
        fuente.coleccion(roi, MES, RECETA_VIGENTE)
        .filter(ee.Filter.eq("system:index", ESCENA_NUBLADA))
        .first()
    )
    return roi, ee.Image(escena)


def _fracciones(ee, imagen, roi, escala):
    return imagen.reduceRegion(
        reducer=ee.Reducer.mean(), geometry=roi, scale=escala,
        bestEffort=False, maxPixels=1e7,
    )


@pytest.mark.gee
def test_la_mascara_de_una_escena_parcialmente_nublada(gee_inicializado):
    import ee

    roi, escena = _escena(ee)
    capas = nubes.componentes(escena, RECETA_VIGENTE)
    enmascarada = nubes.enmascarar(escena, RECETA_VIGENTE)

    info = ee.Dictionary({
        "fracciones": _fracciones(ee, capas, roi, 10),
        # La fracción de B8 que queda con dato después de enmascarar.
        "b8_con_dato": _fracciones(ee, enmascarada.select("B8").mask(), roi, 10),
        "bandas": enmascarada.bandNames(),
        "azimut": enmascarada.get("MEAN_SOLAR_AZIMUTH_ANGLE"),
        "crs": capas.projection().crs(),
        "escala": capas.projection().nominalScale(),
    }).getInfo()

    f = info["fracciones"]
    assert 0.1 < f["nube"] < 0.6, f"el sondeo dio ~0.30 de nubes: {f}"
    assert f["sombra"] > 0, "una escena con 30 % de nubes sin ninguna sombra: la proyección no anda"
    # El descarte es nube o sombra, dilatadas: contiene a las dos.
    assert f["descarte"] >= max(f["nube"], f["sombra"])
    assert f["descarte"] < 1
    # Lo que queda con dato es exactamente lo que no se descartó.
    assert info["b8_con_dato"]["B8"] == pytest.approx(1 - f["descarte"], abs=0.01)
    # La máscara no se lleva las bandas ni las propiedades de la escena.
    assert info["bandas"] == ["B2", "B4", "B5", "B8", "B11", "SCL", "probability"]
    assert info["azimut"] is not None
    assert info["crs"].startswith("EPSG:326")
    assert info["escala"] == RECETA_VIGENTE.escala_m


@pytest.mark.gee
def test_la_erosion_de_la_receta_achica_el_descarte(gee_inicializado):
    # La alternativa que M.2.6 tiene que medir (DECISIONS #39). v1 usa 0, que es
    # lo que hace la capa vieja; con 2 px, los píxeles de nube sueltos dejan de
    # convertirse en círculos de 50 m.
    import ee

    roi, escena = _escena(ee)
    con_erosion = dataclasses.replace(RECETA_VIGENTE, nubes_erosion_px=2)

    info = ee.Dictionary({
        "sin": _fracciones(ee, nubes.componentes(escena, RECETA_VIGENTE), roi, 10),
        "con": _fracciones(ee, nubes.componentes(escena, con_erosion), roi, 10),
    }).getInfo()

    # La erosión solo puede sacar descarte, nunca sumar.
    assert info["con"]["descarte"] < info["sin"]["descarte"]
    # Y no toca las nubes en sí: lo que cambia es lo que se dilata.
    assert info["con"]["nube"] == info["sin"]["nube"]


@pytest.mark.gee
def test_la_mascara_no_depende_de_la_escala_del_pedido(gee_inicializado):
    # Es el porqué de la proyección fija (DECISIONS #36): pedida a 60 m, la
    # máscara se muestrea, no se recalcula con sombras seis veces más largas.
    import ee

    roi, escena = _escena(ee)
    descarte = nubes.componentes(escena, RECETA_VIGENTE).select("descarte")

    info = ee.Dictionary({
        "a_10": _fracciones(ee, descarte, roi, 10),
        "a_60": _fracciones(ee, descarte, roi, 60),
    }).getInfo()

    assert info["a_60"]["descarte"] == pytest.approx(info["a_10"]["descarte"], abs=0.03)
