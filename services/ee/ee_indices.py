import ee
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

def compute_sentinel2_index(roi, start, end, index, cloud_pct=30, use_first=False):
    """Compute various Sentinel-2 based indices for heatmaps.

    Returns an ee.Image clipped to the roi, with a single band named after the index.
    """
    from services.ee.ee_client import get_sentinel2_collection

    idx = (index or '').lower()

    # Ventana Temporal de Curación: Si el rango es menor a 3 días, expandir ±5 días
    try:
        s_dt = datetime.strptime(start, "%Y-%m-%d")
        e_dt = datetime.strptime(end, "%Y-%m-%d")
        if (e_dt - s_dt).days < 3:
            s_dt = s_dt - timedelta(days=5)
            e_dt = e_dt + timedelta(days=5)
            start = s_dt.strftime("%Y-%m-%d")
            end = e_dt.strftime("%Y-%m-%d")
            logger.info("Ventana de curacion expandida: %s a %s", start, end)
    except Exception as e:
        logger.warning("No se pudo parsear el rango de fechas: %s", e)

    # Special handling for change_detection (which compares two sub-periods)
    if idx == 'change_detection':
        try:
            s_dt = datetime.strptime(start, "%Y-%m-%d")
            e_dt = datetime.strptime(end, "%Y-%m-%d")
            mid_dt = s_dt + (e_dt - s_dt) / 2
            middle = mid_dt.strftime("%Y-%m-%d")
            
            # Compute NDVI for first and second half of the period
            img_t1 = compute_sentinel2_index(roi, start, middle, 'ndvi', cloud_pct, use_first=use_first)
            img_t2 = compute_sentinel2_index(roi, middle, end, 'ndvi', cloud_pct, use_first=use_first)
            
            if img_t1 is None or img_t2 is None:
                return None
                
            diff = img_t2.select('ndvi').subtract(img_t1.select('ndvi')).rename('change_detection')
            return diff.clip(roi)
        except Exception:
            return None

    if idx == 'rgb':
        # Para True Color (RGB), queremos la imagen sólida sin huecos transparentes por máscara de nubes o corrección topográfica.
        # Obtenemos la colección cruda (solo con filtro de nubosidad general) y hacemos el composite median.
        collection = (ee.ImageCollection('COPERNICUS/S2_SR_HARMONIZED')
                      .filterBounds(roi)
                      .filterDate(start, end)
                      .filter(ee.Filter.lt('CLOUDY_PIXEL_PERCENTAGE', cloud_pct)))
    else:
        collection = get_sentinel2_collection(roi, start, end, cloud_pct)

    # If no images, return None
    try:
        size = int(collection.size().getInfo())
    except Exception:
        size = 0
    logger.info("Sentinel-2: %s imagenes para componer (cloud_pct<%s)", size, cloud_pct)
    if size == 0:
        return None

    # Build a robust composite (median) or select the first (sorted descending for most recent)
    if use_first:
        try:
            composite = collection.sort('system:time_start', False).first()
        except Exception:
            return None
    else:
        try:
            composite = collection.median()
        except Exception:
            try:
                composite = collection.first()
            except Exception:
                return None

    # RGB (true color)
    if idx == 'rgb':
        try:
            return composite.select(['B4', 'B3', 'B2'])
        except Exception:
            return composite

    # NDVI
    if idx == 'ndvi':
        ndvi = composite.normalizedDifference(['B8', 'B4']).rename('ndvi')
        return ndvi

    # NDWI (water)
    if idx == 'ndwi':
        ndwi = composite.normalizedDifference(['B3', 'B8']).rename('ndwi')
        return ndwi

    # NDMI (moisture)
    if idx == 'ndmi':
        ndmi = composite.normalizedDifference(['B8', 'B11']).rename('ndmi')
        try:
            ndmi = ndmi.clamp(-0.6, 0.6)
        except Exception:
            pass
        return ndmi

    # NDRE (red edge)
    if idx == 'ndre':
        try:
            ndre = composite.normalizedDifference(['B8', 'B5']).rename('ndre')
        except Exception:
            ndre = composite.normalizedDifference(['B8', 'B4']).rename('ndre')
        try:
            ndre = ndre.clamp(-0.5, 0.6)
        except Exception:
            pass
        return ndre

    # EVI
    if idx == 'evi':
        evi = composite.expression(
            '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))',
            {
                'NIR': composite.select('B8'),
                'RED': composite.select('B4'),
                'BLUE': composite.select('B2')
            }
        ).rename('evi')
        try:
            evi = evi.clamp(-0.2, 0.6)
        except Exception:
            pass
        return evi

    # SAVI (soil-adjusted vegetation index)
    if idx == 'savi':
        savi = composite.expression(
            '(1.5) * ((NIR - RED) / (NIR + RED + 0.5))',
            {
                'NIR': composite.select('B8'),
                'RED': composite.select('B4')
            }
        ).rename('savi')
        try:
            savi = savi.clamp(-0.5, 1.0)
        except Exception:
            pass
        return savi

    # LAI: empirical from EVI
    if idx == 'lai':
        try:
            evi = composite.expression(
                '2.5 * ((NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1))',
                {
                    'NIR': composite.select('B8'),
                    'RED': composite.select('B4'),
                    'BLUE': composite.select('B2')
                }
            )
            lai = evi.multiply(3.618).subtract(0.118)
            lai = lai.max(0)
        except Exception:
            ndvi = composite.normalizedDifference(['B8', 'B4'])
            lai = ndvi.multiply(3.618).subtract(0.118)
            lai = lai.max(0)
        lai = lai.rename('lai')
        return lai

    # GNDVI (Green Normalized Difference Vegetation Index)
    if idx == 'gndvi':
        gndvi = composite.normalizedDifference(['B8', 'B3']).rename('gndvi')
        return gndvi

    # RECI (Red Edge Chlorophyll Index)
    if idx == 'reci':
        try:
            reci = composite.select('B8').divide(composite.select('B5')).subtract(1).rename('reci')
            return reci
        except Exception:
            return composite.normalizedDifference(['B8', 'B4']).rename('reci')

    # GCI (Green Chlorophyll Index)
    if idx == 'gci':
        try:
            gci = composite.select('B8').divide(composite.select('B3')).subtract(1).rename('gci')
            return gci
        except Exception:
            return composite.normalizedDifference(['B8', 'B4']).rename('gci')

    # Vegetation Health (SVHI - Sentinel-2 Vegetation Health Index)
    if idx in ('svhi', 'vegetation_health'):
        try:
            svhi = composite.expression(
                '(4 * NIR - (RED + RED_EDGE + SWIR1 + SWIR2)) / (4 * NIR + (RED + RED_EDGE + SWIR1 + SWIR2))',
                {
                    'NIR': composite.select('B8'),
                    'RED': composite.select('B4'),
                    'RED_EDGE': composite.select('B5'),
                    'SWIR1': composite.select('B11'),
                    'SWIR2': composite.select('B12')
                }
            ).rename(idx)
            return svhi
        except Exception:
            return composite.normalizedDifference(['B8', 'B4']).rename(idx)

    # Water Detection (MNDWI / water_detection)
    if idx in ('mndwi', 'water_detection'):
        try:
            mndwi = composite.normalizedDifference(['B3', 'B11']).rename(idx)
            return mndwi
        except Exception:
            return composite.normalizedDifference(['B3', 'B8']).rename(idx)

    # Urban Index / Normalized Difference Built-Up Index (NDBI / urban_index)
    if idx in ('ndbi', 'urban_index'):
        try:
            ndbi = composite.normalizedDifference(['B11', 'B8']).rename(idx)
            return ndbi
        except Exception:
            return composite.normalizedDifference(['B11', 'B8']).rename(idx)

    # Soil Moisture / Normalized Soil Moisture Index (NSMI / soil_moisture)
    if idx in ('nsmi', 'soil_moisture'):
        try:
            nsmi = composite.normalizedDifference(['B11', 'B12']).rename(idx)
            return nsmi
        except Exception:
            return composite.normalizedDifference(['B8', 'B11']).rename(idx)

    # Default fallback: normalizedDifference(NIR, RED)
    try:
        fallback = composite.normalizedDifference(['B8', 'B4']).rename(idx)
        return fallback
    except Exception:
        return composite
