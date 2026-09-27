"""M.2.3: el compuesto mensual (`pipeline/etapas/compuesto.py`).

Lo que se puede probar sin GEE son los nombres de las bandas. El resto va contra
GEE (`pytest --gee`, `DECISIONS #38`), sobre el mismo cuadrado de 2 km en el Bajío
que usa M.2.2: julio de 2026 trae 16 imágenes que son 8 pasadas, y ese es
justamente el caso que esta etapa tiene que resolver.
"""

import dataclasses

import pytest

from pipeline.etapas import compuesto, fuente, nubes
from pipeline.formulas import evaluar
from pipeline.indices import BANDAS, INDICES
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE
from pipeline.ventanas import del_mes


def _receta(**cambios):
    return dataclasses.replace(RECETA_VIGENTE, **cambios)


def test_las_bandas_de_salida_son_los_indices_y_las_observaciones():
    assert compuesto.bandas_de_salida(RECETA_VIGENTE) == (
        "ndvi", "evi", "ndre", "ndmi", "n_obs",
    )


def test_el_color_real_va_despues_de_los_indices():
    # Despues y no antes: la banda de cada indice no se mueve, y el `bidx` de las
    # filas de `layers` que ya existen sigue apuntando al indice correcto.
    assert compuesto.bandas_de_salida(_receta(color_real=True)) == (
        "ndvi", "evi", "ndre", "ndmi", "rojo", "verde", "azul", "n_obs",
    )


def test_el_color_real_no_choca_con_un_indice_ni_con_una_banda():
    from pipeline.receta import COLOR_REAL

    assert not set(COLOR_REAL) & set(INDICES)
    assert compuesto.BANDA_OBSERVACIONES not in COLOR_REAL
    assert not set(COLOR_REAL) & set(BANDAS.values())


def test_con_un_solo_indice_tambien_van_las_observaciones():
    assert compuesto.bandas_de_salida(_receta(indices=("ndvi",))) == ("ndvi", "n_obs")


def test_la_banda_de_observaciones_no_choca_con_un_indice():
    # `n_obs` viaja al lado de los índices: si un índice se llamara igual, el
    # `addBands` lo pisaría sin avisar.
    assert compuesto.BANDA_OBSERVACIONES not in INDICES
    assert compuesto.BANDA_OBSERVACIONES not in set(fuente.bandas_espectrales(RECETA_VIGENTE))
    assert compuesto.BANDA_OBSERVACIONES not in set(nubes.COMPONENTES)


# ---- Contra GEE de verdad (pytest --gee) -------------------------------------------

ROI_2KM = [-100.86, 20.54, -100.84, 20.56]
# Desde M.9.0b el pipeline recibe una `Ventana`, no un `Mes`: con el `Mes` suelto
# estos tests fallaban con `AttributeError` y el CI no lo veia (no corre los `gee`).
MES = del_mes(Mes(2026, 7))
# Lo que dio el sondeo del 2026-09-15 sobre ese cuadrado.
IMAGENES = 16
PASADAS = 8


def _coleccion(ee):
    roi = ee.Geometry.Rectangle(ROI_2KM)
    cruda = fuente.coleccion(roi, MES, RECETA_VIGENTE)
    return roi, cruda, cruda.map(lambda img: nubes.enmascarar(ee.Image(img), RECETA_VIGENTE))


@pytest.mark.gee
def test_las_teselas_de_una_pasada_se_juntan_en_una_imagen(gee_inicializado):
    import ee

    _, cruda, enmascarada = _coleccion(ee)

    info = ee.Dictionary({
        "imagenes": cruda.size(),
        "pasadas": compuesto.por_pasada(enmascarada).size(),
        "datatakes": cruda.aggregate_array(compuesto.PROPIEDAD_PASADA).distinct().size(),
        "fechas": compuesto.por_pasada(enmascarada).aggregate_array("system:time_start").size(),
    }).getInfo()

    assert info["imagenes"] == IMAGENES, "el mes de prueba cambió: revisar el sondeo"
    assert info["pasadas"] == PASADAS == info["datatakes"]
    # `mosaic()` pierde las propiedades: si no se copiaran, no habría fechas.
    assert info["fechas"] == PASADAS


@pytest.mark.gee
def test_el_compuesto_del_mes(gee_inicializado):
    import ee

    roi, _, enmascarada = _coleccion(ee)
    mes = compuesto.compuesto(enmascarada, RECETA_VIGENTE)

    info = ee.Dictionary({
        "bandas": mes.bandNames(),
        "valores": mes.reduceRegion(
            reducer=ee.Reducer.median(), geometry=roi, scale=10,
            bestEffort=False, maxPixels=1e7,
        ),
        "max_obs": mes.select(compuesto.BANDA_OBSERVACIONES).reduceRegion(
            reducer=ee.Reducer.max(), geometry=roi, scale=10,
            bestEffort=False, maxPixels=1e7,
        ),
    }).getInfo()

    assert info["bandas"] == list(compuesto.bandas_de_salida(RECETA_VIGENTE))
    for nombre in RECETA_VIGENTE.indices:
        minimo, maximo = INDICES[nombre].rango
        assert minimo <= info["valores"][nombre] <= maximo, nombre
    # Julio en el Bajío es temporada de lluvias: la vegetación está activa.
    assert info["valores"]["ndvi"] > 0.2

    # **El punto de la etapa**: sin juntar las teselas, cada píxel contaría hasta 16.
    assert info["max_obs"][compuesto.BANDA_OBSERVACIONES] <= PASADAS
    assert info["valores"][compuesto.BANDA_OBSERVACIONES] >= 1


@pytest.mark.gee
def test_v1_deja_evi_dentro_de_su_rango(gee_inicializado):
    # v1 acota los índices desde `DECISIONS #45`. EVI se sale de [-1, 1] por
    # construcción —su denominador puede acercarse a cero—, así que sin acotar el
    # mínimo y el máximo que se guardan quedan disparados.
    import ee

    roi, _, enmascarada = _coleccion(ee)
    sin_acotar = dataclasses.replace(RECETA_VIGENTE, acotar_indices=False)

    def extremos(receta):
        return compuesto.compuesto(enmascarada, receta).select(["evi"]).reduceRegion(
            reducer=ee.Reducer.minMax(), geometry=roi, scale=10,
            bestEffort=False, maxPixels=1e7,
        )

    info = ee.Dictionary({"v1": extremos(RECETA_VIGENTE), "sin": extremos(sin_acotar)}).getInfo()

    minimo, maximo = INDICES["evi"].rango
    # v1 nunca guarda un valor fuera del rango del registro.
    assert minimo <= info["v1"]["evi_min"]
    assert info["v1"]["evi_max"] <= maximo
    # Y el recorte no es decorativo: sin él, esta escena se sale.
    assert info["sin"]["evi_min"] < minimo or info["sin"]["evi_max"] > maximo, (
        "la escena de prueba ya no tiene EVI fuera de rango: el test dejó de probar algo"
    )


@pytest.mark.gee
def test_el_indice_lo_calcula_gee_igual_que_el_evaluador_de_python(gee_inicializado):
    # La fórmula es una sola (DECISIONS #35): la de `pipeline.indices`. Acá se
    # comprueba que GEE y el evaluador de los tests la leen igual, sobre los
    # valores de un píxel real.
    import ee

    roi, _, enmascarada = _coleccion(ee)
    pasada = ee.Image(compuesto.por_pasada(enmascarada).first())
    # Las bandas y los índices del **mismo** píxel, y uno que tenga dato:
    # `dropNulls` saltea los que la máscara se llevó. Un punto fijo caía en una
    # nube y devolvía todo en `None`.
    # `numPixels` es aproximado: el muestreo es probabilístico, y pidiendo 1 sobre
    # un cuadrado de 2 km lo normal es que no devuelva ninguno. Se piden muchos y se
    # usa el primero.
    muestra = ee.Image.cat(
        pasada.select(list(fuente.bandas_espectrales(RECETA_VIGENTE))),
        compuesto.indices_de(pasada, RECETA_VIGENTE),
    ).sample(region=roi, scale=10, numPixels=500, dropNulls=True, seed=42)

    # La colección entera, no `first()`: si viniera vacía, `first()` da null y el
    # error sale de GEE, sin decir qué pasó.
    features = muestra.limit(1).getInfo()["features"]

    assert features, "ninguna muestra con dato: la pasada quedó entera enmascarada"
    pixel = features[0]["properties"]
    # Sólo las bandas que se bajaron: desde M.9.7e1 `BANDAS` tiene también el verde,
    # que v2 no pide porque ningún índice lo usa.
    valores = {nombre: pixel[banda] for nombre, banda in BANDAS.items() if banda in pixel}
    for nombre in RECETA_VIGENTE.indices:
        esperado = evaluar(INDICES[nombre].formula, valores)
        assert pixel[nombre] == pytest.approx(esperado, abs=1e-6), nombre


# ---- v3 (M.9.7e1), contra GEE ---------------------------------------------------


@pytest.mark.gee
def test_el_compuesto_de_v3_trae_el_color_real_en_reflectancia(gee_inicializado):
    """El rojo, el verde y el azul en 0-1, despues de los indices (`DECISIONS #76`)."""
    import ee

    from pipeline.receta import RECETA_PASADA_V3

    roi = ee.Geometry.Rectangle(ROI_2KM)
    enmascarada = fuente.coleccion(roi, MES, RECETA_PASADA_V3).map(
        lambda img: nubes.enmascarar(ee.Image(img), RECETA_PASADA_V3)
    )
    mes = compuesto.compuesto(enmascarada, RECETA_PASADA_V3)
    info = ee.Dictionary({
        "bandas": mes.bandNames(),
        "valores": mes.select(["rojo", "verde", "azul"]).reduceRegion(
            reducer=ee.Reducer.median(), geometry=roi, scale=10,
            bestEffort=False, maxPixels=1e7,
        ),
    }).getInfo()

    assert info["bandas"] == list(compuesto.bandas_de_salida(RECETA_PASADA_V3))
    for banda in ("rojo", "verde", "azul"):
        # Reflectancia de superficie de un campo: decimos, no miles (la fuente divide).
        assert 0 < info["valores"][banda] < 0.5, (banda, info["valores"])


@pytest.mark.gee
def test_la_mascara_de_v3_nunca_deja_mas_que_la_de_la_receta(gee_inicializado):
    """Es la interseccion (d36): por pasada, la cobertura de v3 no pasa la de v2."""
    import ee

    from pipeline import ejecucion
    from pipeline.receta import RECETA_PASADA_V3, RECETA_POR_PASADA

    roi = ee.Geometry.Rectangle(ROI_2KM)
    ventanas = ejecucion.ventanas_de(roi, MES, RECETA_POR_PASADA)
    v2 = ejecucion.reducciones_de(roi, ventanas, RECETA_POR_PASADA)
    v3 = ejecucion.reducciones_de(roi, ventanas, RECETA_PASADA_V3)

    assert ventanas
    assert all(b.cobertura <= a.cobertura + 1e-9 for a, b in zip(v2, v3, strict=True))
