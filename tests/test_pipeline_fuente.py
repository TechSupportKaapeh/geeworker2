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
# Clava v2: estos tests fijan lo que da una receta SIN el color real ni Cloud
# Score+ (las bandas que se piden, las de salida, una fila por indice), y eso
# se fija contra una receta concreta. Hasta M.9.7g la vigente era v2; con v3
# vigente, seguir a `RECETA_VIGENTE` les cambiaria el sentido sin tocarlos. Lo
# propio de v3 tiene sus tests aparte.
from pipeline.receta import RECETA_POR_PASADA as RECETA_VIGENTE
from pipeline.registro import registro
from pipeline.ventanas import del_mes


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


def test_el_color_real_suma_el_verde_y_respeta_el_orden_de_s2():
    # M.9.7e1: el verde (B3) no lo usa ningun indice; sin `color_real` no se baja.
    from pipeline.receta import RECETA_PASADA_V3

    assert fuente.bandas_espectrales(RECETA_PASADA_V3) == (
        "B2", "B3", "B4", "B5", "B8", "B11",
    )
    assert "B3" not in fuente.bandas_espectrales(RECETA_VIGENTE)


# ---- El remuestreo ---------------------------------------------------------------


def test_nearest_no_llama_a_resample():
    # `ee.Image.resample` no acepta "nearest": es lo que GEE hace sin pedirlo.
    assert fuente.metodo_de_remuestreo(RECETA_VIGENTE) is None


@pytest.mark.parametrize("metodo", ["bilinear", "bicubic"])
def test_los_demas_van_a_resample(metodo):
    assert fuente.metodo_de_remuestreo(_receta(remuestreo=metodo)) == metodo


# ---- El rango del mes ------------------------------------------------------------


def test_el_mes_va_del_primer_instante_al_primero_del_siguiente():
    assert fuente.milisegundos(del_mes(Mes(2026, 9))) == (_ms(2026, 9), _ms(2026, 10))


def test_diciembre_termina_en_enero():
    assert fuente.milisegundos(del_mes(Mes(2025, 12))) == (_ms(2025, 12), _ms(2026, 1))


def test_febrero_bisiesto_tiene_29_dias():
    inicio, fin = fuente.milisegundos(del_mes(Mes(2024, 2)))
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
    col = fuente.coleccion(roi, del_mes(MES_SECO), RECETA_VIGENTE)
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
    inicio, fin = fuente.milisegundos(del_mes(MES_SECO))
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
    col = fuente.coleccion(roi, del_mes(MES_SECO), _receta(remuestreo="bilinear"))

    assert ee.Image(col.first()).bandNames().size().getInfo() == 7


@pytest.mark.gee
def test_un_mes_sin_escenas_da_una_coleccion_vacia(gee_inicializado):
    import ee

    roi = ee.Geometry.Polygon([ROI_DE_PRUEBA])

    assert fuente.coleccion(roi, del_mes(Mes(2035, 1)), RECETA_VIGENTE).size().getInfo() == 0


# El tándem de Sentinel-2C (`DECISIONS #79`): un rancho del Valle del Yaqui, de los
# que se usaron para medir v3, y el día en que el 2C sacó su toma de prueba 27 s
# después de la del 2A sobre la tesela 12RWR.
ROI_YAQUI = [
    [-110.0076, 27.293215],
    [-109.9924, 27.293215],
    [-109.9924, 27.306785],
    [-110.0076, 27.306785],
    [-110.0076, 27.293215],
]


@pytest.mark.gee
def test_el_producto_de_prueba_de_s2c_no_entra(gee_inicializado):
    import ee

    from pipeline.ventanas import Ventana

    roi = ee.Geometry.Polygon([ROI_YAQUI])
    dia = Ventana(
        "2024-12-11",
        datetime(2024, 12, 11, tzinfo=UTC),
        datetime(2024, 12, 12, tzinfo=UTC),
    )
    crudas = ee.ImageCollection(RECETA_VIGENTE.coleccion).filterBounds(roi).filterDate(
        "2024-12-11", "2024-12-12"
    )
    info = ee.Dictionary({
        # Control: GEE sigue publicando las dos, la del 2A y la de prueba del 2C.
        "bases_crudas": crudas.aggregate_array("PROCESSING_BASELINE"),
        "bases": fuente.coleccion(roi, dia, RECETA_VIGENTE).aggregate_array(
            "PROCESSING_BASELINE"
        ),
    }).getInfo()

    assert sorted(info["bases_crudas"]) == ["05.11", "99.05"], "cambió el caso de prueba"
    assert info["bases"] == ["05.11"]


@pytest.mark.gee
def test_diciembre_de_2024_se_parte_en_pasadas_sin_error(gee_inicializado):
    """Lo que fallaba en producción: «1 par de pasadas a menos de 0:01:00»."""
    import ee

    from pipeline.ejecucion import ventanas_de

    roi = ee.Geometry.Polygon([ROI_YAQUI])
    # Las estadisticas de v2 ya van por pasada: es el camino que parte el mes.
    ventanas = ventanas_de(roi, del_mes(Mes(2024, 12)), RECETA_VIGENTE)

    assert ventanas, "diciembre en el Yaqui sin pasadas"
