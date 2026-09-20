import json
import os

import ee
from dotenv import load_dotenv
from google.oauth2 import service_account

# Cargar variables del archivo .env automáticamente
load_dotenv()

SA_EMAIL = os.getenv("EE_SERVICE_ACCOUNT_EMAIL")
SA_KEY_JSON = os.getenv("EE_SERVICE_ACCOUNT_KEY_JSON")

# Import shared config


def init_ee():
    if not SA_EMAIL or not SA_KEY_JSON:
        raise RuntimeError("Faltan EE_SERVICE_ACCOUNT_EMAIL o EE_SERVICE_ACCOUNT_KEY_JSON en .env")
    creds = service_account.Credentials.from_service_account_info(
        json.loads(SA_KEY_JSON),
        scopes=[
            "https://www.googleapis.com/auth/earthengine.readonly",
            "https://www.googleapis.com/auth/devstorage.read_write",
            "https://www.googleapis.com/auth/drive"
        ],
        subject=SA_EMAIL
    )
    ee.Initialize(creds)


def apply_scsc(image):
    # 1. Obtener datos de elevación (SRTM)
    dem = ee.Image('USGS/SRTMGL1_003')
    terrain = ee.Algorithms.Terrain(dem)
    pi = 3.141592653589793
    slope = terrain.select('slope').divide(180).multiply(pi).unmask(0)
    aspect = terrain.select('aspect').divide(180).multiply(pi).unmask(0)


    # 2. Obtener posición del sol desde los metadatos de la imagen
    solar_azimuth = ee.Number(image.get('MEAN_SOLAR_AZIMUTH_ANGLE')).divide(180).multiply(pi)
    solar_zenith = ee.Number(image.get('MEAN_SOLAR_ZENITH_ANGLE')).divide(180).multiply(pi)

    # 3. Calcular el ángulo de incidencia (coseno de i)
    cos_i = slope.cos().multiply(solar_zenith.cos()).add(
        slope.sin().multiply(solar_zenith.sin()).multiply(aspect.subtract(solar_azimuth).cos())
    )

    # 4. Aplicar corrección SCS+C
    c_factor = 0.1
    bands_to_correct = ['B2', 'B3', 'B4', 'B5', 'B8', 'B11']
    corrected = image.select(bands_to_correct).multiply(solar_zenith.cos().add(c_factor)).divide(cos_i.add(c_factor))
    return image.addBands(corrected, overwrite=True)


# El parseo de KML se borro el 2026-09-04. `parse_kml_to_geojson` no tenia
# llamadores desde que se elimino `process_kml` (E.2), y duplicaba algo que
# **Geocore ya hace y el worker decidio no hacer**: `DECISIONS #17` puso el
# parseo en Geocore con `DtdProcessing.Prohibit` y `XmlResolver = null`, con
# tests de XXE; `DECISIONS #23` borro el `/upload-kml` de aca por lo mismo.
# Esta version usaba `ET.fromstring` sin endurecer y caia a un regex sobre el
# XML si fallaba. Un parser de input no confiable, sin llamadores, es la peor
# combinacion posible: nadie lo mira y sigue disponible.

# M.6.1 (`DECISIONS #59`): se borraron `composite_embedding`, `maskS2clouds` y
# la re-exportacion de `compute_sentinel2_index`, las tres sin ningun llamador
# (`ARQUITECTURA_PIPELINE` §9). Ojo con la segunda: se lee como "la mascara de
# nubes" y no es la que usa nadie — la de aca es `mask_s2cloudless_and_shadows`
# y la del pipeline vive en `pipeline/etapas/nubes.py`.
#
# Lo que queda —`get_sentinel2_collection`, `get_sentinel2_time_series` y sus
# ayudantes— sigue vivo **solo por los handlers a demanda**. §9 los da por
# borrados; los borra M.6.2, cuando se sepa si el front de los tenants los usa.

# --------- Funciones auxiliares para Sentinel-2 (Heatmaps y Series) ---------
def add_cloud_probability(img):
    cloud_img = ee.Image(img.get('cloud_probability'))
    prob = cloud_img.select('probability')
    return img.addBands(prob)

def mask_s2cloudless_and_shadows(img, max_prob=45, dark_threshold=0.15, shadow_distance=1000):
    """
    Algoritmo científico avanzado de enmascaramiento de nubes s2cloudless
    y proyección geométrica de sombras.
    """
    # 1. Máscara de nubes por probabilidad
    prob = img.select('probability')
    clouds = prob.gt(max_prob)

    # 2. Máscara de píxeles oscuros (candidatos a sombras)
    scl = img.select('SCL')
    not_water = scl.neq(6)  # Excluir agua de SCL
    dark_pixels = img.select('B8').lt(dark_threshold * 10000).And(not_water)

    # 3. Proyección geométrica de sombras
    shadow_azimuth = ee.Number(img.get('MEAN_SOLAR_AZIMUTH_ANGLE')).add(180)
    # Proyectar la sombra desde las nubes usando directionalDistanceTransform (escala 10m -> divide por 10)
    proj_shadows = clouds.directionalDistanceTransform(shadow_azimuth, shadow_distance / 10).select('distance').mask()
    
    # Intersección para obtener sombras reales
    shadows = proj_shadows.And(dark_pixels)

    # 4. Dilatación de máscara combinada (nube + sombra) por 50 metros
    cloud_shadow_mask = clouds.Or(shadows).focalMax(50, 'circle', 'meters')

    return img.updateMask(cloud_shadow_mask.Not())

def check_roi_coverage(img, roi):
    """
    Calcula el porcentaje de píxeles válidos (no enmascarados) dentro del ROI del KML.
    """
    # Contar píxeles válidos en B8
    stats = img.select('B8').reduceRegion(
        reducer=ee.Reducer.count(),
        geometry=roi,
        scale=10,
        maxPixels=1e9
    )
    count_unmasked = ee.Number(stats.get('B8', 0))

    # Contar píxeles totales en B8 (desenmascarados)
    stats_total = img.select('B8').unmask().reduceRegion(
        reducer=ee.Reducer.count(),
        geometry=roi,
        scale=10,
        maxPixels=1e9
    )
    count_total = ee.Number(stats_total.get('B8', 0))

    # Proporción de cobertura
    coverage = ee.Algorithms.If(
        count_total.gt(0),
        count_unmasked.divide(count_total),
        0.0
    )
    return img.set('roi_coverage', coverage)

def get_sentinel2_collection(roi, start, end, cloud_pct=30, min_coverage=0.5):
    """
    Obtiene colección Sentinel-2 utilizando el algoritmo s2cloudless,
    corrección topográfica SCS+C, y filtrado por cobertura útil mínima en el ROI (KML).
    """
    s2_collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                     .filterBounds(roi)
                     .filterDate(start, end))
                     
    s2_clouds = (ee.ImageCollection('COPERNICUS/S2_CLOUD_PROBABILITY')
                 .filterBounds(roi)
                 .filterDate(start, end))

    # Cruzar colecciones por system:index
    join = ee.Join.saveFirst(matchKey='cloud_probability')
    filter = ee.Filter.equals(leftField='system:index', rightField='system:index')
    joined = ee.ImageCollection(join.apply(s2_collection, s2_clouds, filter))

    # Aplicar máscara avanzada de nubes y sombras s2cloudless + corrección SCS+C
    processed = (joined
                 .map(add_cloud_probability)
                 .map(lambda img: mask_s2cloudless_and_shadows(img, max_prob=45))
                 .map(apply_scsc))

    # Filtrar por cobertura mínima del polígono KML
    filtered = (processed
                .map(lambda img: check_roi_coverage(img, roi))
                .filter(ee.Filter.gte('roi_coverage', min_coverage)))

    return filtered


# M.6.2 (`DECISIONS #60`): se borraron `get_sentinel2_time_series` —con su
# `add_index_band_fast`—, `una_por_dia`, `_redondear` y `get_sentinel2_dates`,
# las cuatro con los handlers a demanda que las llamaban.
#
# `add_index_band_fast` era **la segunda copia de las formulas** de indices, y
# no coincidia con la primera: EVI y SAVI usaban constantes pensadas para
# reflectancia 0-1 sobre bandas en miles, asi que el SAVI que devolvia era, en
# la practica, 1,5 x NDVI. La copia unica vive en `pipeline/indices.py`, donde
# las formulas son texto y hay tests que las evaluan contra valores de
# referencia.
#
# `una_por_dia` juntaba las dos imagenes de una misma pasada en el borde de dos
# teselas MGRS. El pipeline lo resuelve antes y mejor: `pipeline/etapas/
# compuesto.py` mosaica por `DATATAKE_IDENTIFIER` en el espacio de la imagen, en
# vez de promediar dos medias espaciales parciales.
#
# Lo que queda en este modulo —`get_sentinel2_collection` y sus ayudantes— lo
# sostiene **solo** `generate_heatmap_on_demand`, via
# `ee_indices.compute_sentinel2_index`. Se va con M.6.2b, cuando el mapa a
# demanda pase al pipeline.
