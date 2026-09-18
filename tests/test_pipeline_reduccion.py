"""M.2.4: la reducción (`pipeline/etapas/reduccion.py`).

`leer()` es pura, así que su parte corre en el CI: es la que distingue una clave
que falta (un error) de una clave en `None` (un mes sin cobertura). El reductor y
los pedidos van contra GEE (`pytest --gee`, `DECISIONS #38`), sobre el mismo
cuadrado de 2 km en el Bajío de M.2.2 y M.2.3.
"""

import dataclasses

import pytest

from pipeline.estadisticas import Reductor, claves_de_salida
from pipeline.etapas import compuesto, fuente, nubes, reduccion
from pipeline.indices import INDICES
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE


def _receta(**cambios):
    return dataclasses.replace(RECETA_VIGENTE, **cambios)


def _respuesta(receta=RECETA_VIGENTE, valor=0.5, cobertura=0.9, observaciones=4.0):
    """Una respuesta de GEE completa, como la que devuelve `valores()`."""
    respuesta = dict.fromkeys(claves_de_salida(receta.indices, receta.estadisticas).values(), valor)
    respuesta[reduccion.CLAVE_COBERTURA] = cobertura
    respuesta[reduccion.CLAVE_OBSERVACIONES] = observaciones
    return respuesta


# ---- leer(): lo que corre en el CI -------------------------------------------------


def test_la_respuesta_se_lee_con_los_nombres_del_registro():
    leida = reduccion.leer(_respuesta(), RECETA_VIGENTE)

    # La clave de GEE es `ndvi_p50`; la del jsonb es `mediana`.
    assert leida.estadisticas["ndvi"]["mediana"] == 0.5
    assert set(leida.estadisticas) == set(RECETA_VIGENTE.indices)
    assert set(leida.estadisticas["ndvi"]) == set(RECETA_VIGENTE.estadisticas)
    assert leida.cobertura == 0.9
    assert leida.observaciones == 4.0


def test_una_clave_que_falta_es_un_error():
    # Es el caso que importa: si se tomara por un nulo, se guardaría un mes vacío
    # sin que nadie se entere.
    respuesta = _respuesta()
    del respuesta["ndvi_p50"]

    with pytest.raises(ValueError, match="ndvi_p50"):
        reduccion.leer(respuesta, RECETA_VIGENTE)


@pytest.mark.parametrize("clave", [reduccion.CLAVE_COBERTURA, reduccion.CLAVE_OBSERVACIONES])
def test_tambien_falla_si_falta_la_cobertura_o_las_observaciones(clave):
    respuesta = _respuesta()
    del respuesta[clave]

    with pytest.raises(ValueError, match=clave):
        reduccion.leer(respuesta, RECETA_VIGENTE)


def test_un_mes_sin_un_solo_pixel_con_dato_es_valido():
    # Cobertura cero con las claves en None. No es un error: es la fila con
    # `valor` nulo de ARQUITECTURA §6.
    leida = reduccion.leer(_respuesta(valor=None, cobertura=0.0, observaciones=None), RECETA_VIGENTE)

    assert leida.cobertura == 0.0
    assert leida.observaciones is None
    assert leida.estadisticas["ndvi"]["mediana"] is None


def test_con_cobertura_cero_gee_omite_las_claves_y_se_leen_como_nulo():
    # Lo que contesta GEE de verdad (M.4.4, DECISIONS #50): sin un píxel con dato,
    # la respuesta trae solo la cobertura. Antes esto era un error, y el step del
    # mes fallaba en cada reintento.
    leida = reduccion.leer({reduccion.CLAVE_COBERTURA: 0}, RECETA_VIGENTE)

    assert leida.cobertura == 0.0
    assert leida.observaciones is None
    assert set(leida.estadisticas) == set(RECETA_VIGENTE.indices)
    assert all(v is None for e in leida.estadisticas.values() for v in e.values())


def test_con_cobertura_la_clave_que_falta_sigue_siendo_un_error():
    # La excepción de arriba vale solo para cobertura cero: con un píxel con dato,
    # una clave que falta es un pedido que no calculó lo que se le pidió.
    respuesta = {reduccion.CLAVE_COBERTURA: 0.01}

    with pytest.raises(ValueError, match="ndvi_p50"):
        reduccion.leer(respuesta, RECETA_VIGENTE)


@pytest.mark.parametrize("cobertura", [-0.1, 1.5])
def test_una_cobertura_fuera_de_cero_a_uno_se_rechaza(cobertura):
    with pytest.raises(ValueError, match="cobertura"):
        reduccion.leer(_respuesta(cobertura=cobertura), RECETA_VIGENTE)


@pytest.mark.parametrize("cobertura", ["0.9", None, True])
def test_una_cobertura_que_no_es_un_numero_se_rechaza(cobertura):
    # `True` incluido: para Python es un `int`, y pasaría por una cobertura de 1.
    with pytest.raises(TypeError, match="cobertura"):
        reduccion.leer(_respuesta(cobertura=cobertura), RECETA_VIGENTE)


def test_una_receta_con_un_solo_indice_lee_su_unica_clave():
    receta = _receta(indices=("ndvi",))

    leida = reduccion.leer(_respuesta(receta), receta)

    assert set(leida.estadisticas) == {"ndvi"}


def test_las_claves_del_mes_no_chocan_con_las_de_un_indice():
    # `cobertura` y `observaciones` viajan en el mismo diccionario que las claves
    # `{indice}_{sufijo}`: un índice con ese nombre las pisaría.
    assert reduccion.CLAVE_COBERTURA not in INDICES
    assert reduccion.CLAVE_OBSERVACIONES not in INDICES
    claves = set(claves_de_salida(RECETA_VIGENTE.indices, RECETA_VIGENTE.estadisticas).values())
    assert not claves & {reduccion.CLAVE_COBERTURA, reduccion.CLAVE_OBSERVACIONES}


def test_un_metodo_de_reduccion_desconocido_se_rechaza():
    # El nombre del método sale de un dato. Si algún día el plan trae uno que no
    # está en la lista blanca, esto falla en vez de llamarlo a ciegas.
    with pytest.raises(ValueError, match="desconocido"):
        reduccion._reductor_de(Reductor("system", salidas=("x",)))


# ---- Contra GEE de verdad (pytest --gee) -------------------------------------------

ROI_2KM = [-100.86, 20.54, -100.84, 20.56]
MES = Mes(2026, 7)


def _compuesto_del_mes(ee):
    roi = ee.Geometry.Rectangle(ROI_2KM)
    coleccion = fuente.coleccion(roi, MES, RECETA_VIGENTE)
    enmascarada = coleccion.map(lambda img: nubes.enmascarar(ee.Image(img), RECETA_VIGENTE))
    return roi, compuesto.compuesto(enmascarada, RECETA_VIGENTE)


@pytest.mark.gee
def test_los_numeros_del_mes_de_una_parcela(gee_inicializado):
    import ee

    roi, mes = _compuesto_del_mes(ee)

    leida = reduccion.leer(reduccion.valores(mes, roi, RECETA_VIGENTE).getInfo(), RECETA_VIGENTE)

    assert 0 < leida.cobertura <= 1
    assert leida.observaciones >= 1
    for nombre in RECETA_VIGENTE.indices:
        valores = leida.estadisticas[nombre]
        minimo, maximo = INDICES[nombre].rango
        # El rango del registro se le pide a la **mediana**, que es lo que se guarda
        # en `valor`. Los extremos de EVI se salen por construcción: su denominador
        # puede acercarse a cero en píxeles raros (agua, borde de nube) y dispara el
        # cociente. Medido acá: EVI llegó a -6,4. Lo mira M.2.6.
        assert minimo <= valores["mediana"] <= maximo, nombre
        # El orden de los percentiles es la prueba de que cada salida es la suya:
        # si el reductor combinado mezclara los nombres, esto se rompe.
        assert valores["min"] <= valores["p10"] <= valores["mediana"], nombre
        assert valores["mediana"] <= valores["p90"] <= valores["max"], nombre
        assert valores["desvio"] >= 0, nombre

    # Las diferencias normalizadas sí están acotadas por su fórmula: un extremo
    # fuera de rango en una de ellas sería un error de verdad, como una banda mal
    # elegida.
    for nombre in ("ndvi", "ndre", "ndmi"):
        minimo, maximo = INDICES[nombre].rango
        assert minimo <= leida.estadisticas[nombre]["min"], nombre
        assert leida.estadisticas[nombre]["max"] <= maximo, nombre


@pytest.mark.gee
def test_un_mes_sin_pixeles_en_gee_omite_las_claves(gee_inicializado):
    # DECISIONS #50: el control de que `leer` acepta la respuesta de un mes con la
    # máscara tapándolo todo. Se tapa a mano, porque un mes así depende del clima.
    import ee

    roi, mes = _compuesto_del_mes(ee)
    tapado = mes.updateMask(ee.Image.constant(0))

    respuesta = reduccion.valores(tapado, roi, RECETA_VIGENTE).getInfo()
    leida = reduccion.leer(respuesta, RECETA_VIGENTE)

    # Lo que GEE hace de verdad, y la razón del arreglo: omite, no manda `None`.
    assert reduccion.CLAVE_OBSERVACIONES not in respuesta
    assert leida.cobertura == 0
    assert leida.observaciones is None
    assert leida.estadisticas["ndvi"]["mediana"] is None


@pytest.mark.gee
def test_un_pedido_que_no_entra_falla_en_vez_de_bajar_la_escala(gee_inicializado, monkeypatch):
    # El control de `bestEffort=False` (DECISIONS #36): con un tope de píxeles que
    # el ROI no puede cumplir a 10 m, GEE tiene que levantar. Con `bestEffort=True`
    # devolvería un número calculado a otra escala, sin avisar.
    import ee

    roi, mes = _compuesto_del_mes(ee)
    monkeypatch.setattr(reduccion, "MAX_PIXELES", 10)

    with pytest.raises(ee.EEException, match="Too many pixels|maxPixels"):
        reduccion.valores(mes, roi, RECETA_VIGENTE).getInfo()
