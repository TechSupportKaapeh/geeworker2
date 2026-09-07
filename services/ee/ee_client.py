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


# Import index computations from ee_indices (keeps compatibility)
from services.ee.ee_indices import compute_sentinel2_index  # noqa: F401 - re-export de services.ee

# El parseo de KML se borro el 2026-09-04. `parse_kml_to_geojson` no tenia
# llamadores desde que se elimino `process_kml` (E.2), y duplicaba algo que
# **Geocore ya hace y el worker decidio no hacer**: `DECISIONS #17` puso el
# parseo en Geocore con `DtdProcessing.Prohibit` y `XmlResolver = null`, con
# tests de XXE; `DECISIONS #23` borro el `/upload-kml` de aca por lo mismo.
# Esta version usaba `ET.fromstring` sin endurecer y caia a un regex sobre el
# XML si fallaba. Un parser de input no confiable, sin llamadores, es la peor
# combinacion posible: nadie lo mira y sigue disponible.

def composite_embedding(roi, start, end, cloud_pct=None):
    """Crea una composición de embeddings para el área y fechas especificadas"""
    col = (
        ee.ImageCollection('GOOGLE/SATELLITE_EMBEDDING/V1/ANNUAL')
        .filterBounds(roi)
        .filterDate(start, end)
    )
    # Para embeddings, tomamos la primera imagen disponible en el rango
    return col.first().clip(roi)


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

def maskS2clouds(image):
    """
    Aplica máscara de nubes básica usando Scene Classification Layer (SCL) como respaldo
    """
    scl = image.select('SCL')
    # Máscara para excluir: sombras(3), nubes med(8), nubes altas(9), cirrus(10), nieve/hielo(11)
    mask = (scl.neq(3)
           .And(scl.neq(8))
           .And(scl.neq(9))
           .And(scl.neq(10))
           .And(scl.neq(11)))
    return image.updateMask(mask)

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

def get_sentinel2_time_series(roi, start, end, index, cloud_pct=70, limit=30):
    """
    Obtiene serie temporal de cada pasada individual de Sentinel-2 filtrada por cobertura del KML
    """
    cloud_thresholds = [min(cloud_pct, 80), 90]
    limit_to_use = limit if limit and limit > 0 else 30

    for threshold in cloud_thresholds:
        # Usamos nuestra colección robusta con s2cloudless
        collection = get_sentinel2_collection(roi, start, end, cloud_pct=threshold, min_coverage=0.5)
        collection = collection.sort('system:time_start').limit(limit_to_use)

        try:
            size = int(collection.size().getInfo())
        except Exception:
            size = 0
            
        if size > 0:
            break
    else:
        return []

    def add_index_band_fast(img):
        idx = index.lower()
        if idx == 'ndvi':
            return img.addBands(img.normalizedDifference(['B8', 'B4']).rename(index))
        elif idx == 'ndwi':
            return img.addBands(img.normalizedDifference(['B3', 'B8']).rename(index))
        elif idx == 'ndmi':
            return img.addBands(img.normalizedDifference(['B8', 'B11']).rename(index))
        elif idx == 'ndre':
            try:
                ndre = img.normalizedDifference(['B8', 'B5']).rename(index)
            except Exception:
                ndre = img.normalizedDifference(['B8', 'B4']).rename(index)
            return img.addBands(ndre)
        elif idx == 'evi':
            evi = img.expression(
                '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1.0))',
                {
                    'NIR': img.select('B8'),
                    'RED': img.select('B4'),
                    'BLUE': img.select('B2')
                }
            ).rename(index)
            return img.addBands(evi)
        elif idx == 'savi':
            savi = img.expression(
                '1.5 * ((NIR - RED) / (NIR + RED + 0.5))',
                {
                    'NIR': img.select('B8'),
                    'RED': img.select('B4')
                }
            ).rename(index)
            return img.addBands(savi)
        elif idx == 'gci':
            try:
                gci = img.select('B8').divide(img.select('B3')).subtract(1.0).rename(index)
            except Exception:
                gci = img.normalizedDifference(['B8', 'B4']).rename(index)
            return img.addBands(gci)
        elif idx == 'vegetation_health':
            try:
                svhi = img.expression(
                    '(4 * NIR - (RED + RED_EDGE + SWIR1 + SWIR2)) / (4 * NIR + (RED + RED_EDGE + SWIR1 + SWIR2))',
                    {
                        'NIR': img.select('B8'),
                        'RED': img.select('B4'),
                        'RED_EDGE': img.select('B5'),
                        'SWIR1': img.select('B11'),
                        'SWIR2': img.select('B12')
                    }
                ).rename(index)
            except Exception:
                svhi = img.normalizedDifference(['B8', 'B4']).rename(index)
            return img.addBands(svhi)
        elif idx == 'water_detection':
            try:
                mndwi = img.normalizedDifference(['B3', 'B11']).rename(index)
            except Exception:
                mndwi = img.normalizedDifference(['B3', 'B8']).rename(index)
            return img.addBands(mndwi)
        elif idx == 'urban_index':
            try:
                ndbi = img.normalizedDifference(['B11', 'B8']).rename(index)
            except Exception:
                ndbi = img.normalizedDifference(['B11', 'B8']).rename(index)
            return img.addBands(ndbi)
        elif idx == 'soil_moisture':
            try:
                nsmi = img.normalizedDifference(['B11', 'B12']).rename(index)
            except Exception:
                nsmi = img.normalizedDifference(['B8', 'B11']).rename(index)
            return img.addBands(nsmi)
        elif idx == 'lai':
            ndvi = img.normalizedDifference(['B8', 'B4'])
            lai = ndvi.multiply(3.618).subtract(0.118).max(0).rename(index)
            return img.addBands(lai)

        else:
            return img.addBands(img.normalizedDifference(['B8', 'B4']).rename(index))

    processed_collection = collection.map(add_index_band_fast)

    try:
        limited_collection = processed_collection.limit(limit_to_use)
        image_count = limited_collection.size().getInfo()
        image_list = limited_collection.toList(image_count)
        time_series = []
        for i in range(image_count):
            try:
                img = ee.Image(image_list.get(i))
                stats = img.select(index).reduceRegion(reducer=ee.Reducer.mean(), geometry=roi, scale=60, maxPixels=1e5, bestEffort=True).getInfo()
                metadata = img.getInfo()
                date_ms = metadata['properties']['system:time_start']
                import datetime
                date_obj = datetime.datetime.fromtimestamp(date_ms / 1000)
                date_str = date_obj.strftime('%Y-%m-%d')
                mean_value = stats.get(index)
                if mean_value is not None:
                    try:
                        from utils_pkg.io import round_sig
                        rounded = round_sig(float(mean_value), sig=2)
                    except Exception:
                        rounded = float(mean_value)
                    time_series.append({'date': date_str, 'datetime': date_str + ' 12:00:00', 'timestamp': date_ms, 'mean': rounded})
            except Exception:
                continue
        time_series.sort(key=lambda x: x.get('timestamp', 0))
        return time_series
    except Exception:
        return []


def get_sentinel2_dates(roi, start, end, cloud_pct=100):
    """
    Obtiene todas las fechas disponibles de imágenes Sentinel-2 para una geometría.
    
    Args:
        roi: ee.Geometry - región de interés
        start: str - fecha inicio (YYYY-MM-DD)
        end: str - fecha fin (YYYY-MM-DD)
        cloud_pct: int - filtro max de cobertura de nubes (0-100), default 100 (todas)
    
    Returns:
        List[dict] - lista de diccionarios con metadata de cada imagen:
            - date: str (YYYY-MM-DD)
            - system_time_start: int (milliseconds)
            - cloud_cover: float (0-100)
            - tile_id: str (MGRS tile)
    """
    try:
        # Obtener colección sin máscara (queremos todas las fechas disponibles)
        collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                     .filterBounds(roi)
                     .filterDate(start, end)
                     .filter(ee.Filter.lte('CLOUDY_PIXEL_PERCENTAGE', cloud_pct))
                     .sort('system:time_start'))
        
        # Obtener el tamaño de la colección
        size = collection.size().getInfo()
        
        if size == 0:
            return []
        
        # Convertir a lista y extraer metadata
        image_list = collection.toList(size)
        dates = []
        
        for i in range(size):
            try:
                img = ee.Image(image_list.get(i))
                props = img.getInfo()['properties']
                
                date_ms = props.get('system:time_start')
                if not date_ms:
                    continue
                
                # Convertir timestamp a fecha ISO
                import datetime
                date_obj = datetime.datetime.utcfromtimestamp(date_ms / 1000)
                date_str = date_obj.strftime('%Y-%m-%d')
                
                # Extraer metadata adicional
                cloud_cover = props.get('CLOUDY_PIXEL_PERCENTAGE')
                tile_id = props.get('MGRS_TILE', props.get('system:index'))
                
                dates.append({
                    'date': date_str,
                    'system_time_start': date_ms,
                    'cloud_cover': float(cloud_cover) if cloud_cover is not None else None,
                    'tile_id': str(tile_id) if tile_id else None
                })
            except Exception:
                # Skip imágenes con errores de metadata
                continue
        
        return dates
    
    except Exception as e:
        raise RuntimeError(f"Error obteniendo fechas de Sentinel-2: {str(e)}")


# rest of file omitted for brevity; original content preserved
