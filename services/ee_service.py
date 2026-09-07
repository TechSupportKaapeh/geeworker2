import ee
import time
import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, Dict, Any, List

from services.ee.ee_client import get_sentinel2_time_series, init_ee
from services.ee.ee_indices import compute_sentinel2_index
from utils_pkg.visualization import index_band_and_vis

logger = logging.getLogger("ee_service")

# `get_roi_and_bounds` se borro el 2026-09-04: no tenia llamadores. Tomaba un
# objeto `req` con atributos, que era la forma de los requests HTTP que la FASE
# D elimino. Los handlers arman el ROI con `coords_to_geometry(payload
# ["coordinates"])`, directo desde el evento de Inngest.
#
# Con eso `utils_pkg/roi.py` quedo entero sin llamadores, y se borro el
# 2026-09-07 (F.17).

def prepare_layer_visualization(layer: ee.Image, band: Any, vis: Dict[str, Any], vis_type: str = "continuous") -> Tuple[ee.Image, Dict[str, Any]]:
    """Aplica la visualización seleccionada (continua o clasificada) a la capa."""
    is_rgb_band = isinstance(band, (list, tuple))
    
    if is_rgb_band:
        # Para composición RGB (ej. true color B4/B3/B2), usar directo sin paleta
        return layer, vis

    # Si se pide continuo o el índice no tiene discrete=True, usar degradado continuo
    if vis_type == "continuous" or not vis.get("discrete"):
        palette = vis.get("palette")
        min_val = vis.get("min", 0.0)
        max_val = vis.get("max", 1.0)
        
        vis_params = {
            "min": min_val,
            "max": max_val
        }
        if palette:
            vis_params["palette"] = [str(p) for p in palette if p]
            
        return layer, vis_params
    else:
        # Modo clasificado por intervalos discretos
        breaks = vis.get("breaks") or []
        if not breaks:
            return layer, vis
            
        classes = []
        prev = None
        for i, b in enumerate(breaks):
            if prev is None:
                mask = layer.lte(float(b))
            else:
                mask = layer.gt(float(prev)).And(layer.lte(float(b)))
            classes.append(mask.multiply(i))
            prev = b
            
        # Último intervalo (mayor que el último break)
        classes.append(layer.gt(float(prev)).multiply(len(breaks)))
        
        classified = ee.Image(classes[0])
        for c in classes[1:]:
            classified = classified.add(c)
            
        num_classes = len(breaks) + 1
        palette = list(vis.get("palette") or [])
        
        # Ajustar longitud de paleta
        if len(palette) < num_classes:
            if palette:
                while len(palette) < num_classes:
                    palette.append(palette[-1])
            else:
                palette = ["#000000"] * num_classes
        elif len(palette) > num_classes:
            palette = palette[:num_classes]
            
        vis_params = {
            "min": 0,
            "max": num_classes - 1,
            "palette": palette
        }
        return classified.rename("class"), vis_params


def generate_heatmap_tiles(
    roi: ee.Geometry,
    roi_bounds: Optional[List[float]],
    index: str,
    start: str,
    end: str,
    cloud_pct: int = 30,
    vis_type: str = "continuous",
    dynamic_range: bool = True,
    use_first: bool = False,
    source: str = 'on_demand',
    kml_id: Optional[str] = None
) -> Dict[str, Any]:
    """Genera teselas y estadísticas descriptivas de un heatmap para el ROI y periodo especificados."""
    init_ee()
    
    # Obtener definición de bandas y visualización estándar del índice
    band, vis_config = index_band_and_vis(index, satellite="sentinel2")
    
    # Computar el índice utilizando la composición por mediana o imagen individual
    img = compute_sentinel2_index(roi, start, end, index, cloud_pct, use_first=use_first)
    if img is None:
        raise ValueError(f"No se encontraron imágenes Sentinel-2 válidas en el rango {start} a {end} con nubosidad < {cloud_pct}%")
        
    try:
        layer = img.select(band)
    except Exception:
        layer = img

    raw_layer = layer

    # Calcular estadísticas (min, max, mean, stddev) sobre el ROI a resolución nativa (10m)
    min_val = max_val = mean_val = stddev_val = None
    stats_info = None
    try:
        band_names = raw_layer.bandNames().getInfo()
        target_band = band_names[0] if band_names else None
        bands_to_select = band if isinstance(band, list) else [target_band]
        if target_band:
            reducer = ee.Reducer.mean().combine(ee.Reducer.min(), None, True).combine(ee.Reducer.max(), None, True).combine(ee.Reducer.stdDev(), None, True)
            rr = raw_layer.select(bands_to_select).reduceRegion(reducer, geometry=roi, scale=10, maxPixels=1e9, bestEffort=True)
            stats_info = rr.getInfo()
            
            mean_val = stats_info.get(f"{target_band}_mean") or stats_info.get("mean")
            min_val = stats_info.get(f"{target_band}_min") or stats_info.get("min")
            max_val = stats_info.get(f"{target_band}_max") or stats_info.get("max")
            stddev_val = stats_info.get(f"{target_band}_stdDev") or stats_info.get("stdDev")
            
            # Coercer a float
            mean_val = float(mean_val) if mean_val is not None else None
            min_val = float(min_val) if min_val is not None else None
            max_val = float(max_val) if max_val is not None else None
            stddev_val = float(stddev_val) if stddev_val is not None else None
    except Exception as e:
        logger.warning(f"No se pudieron calcular estadísticas detalladas para el composite: {e}")

    # Aplicar remuestreo bicúbico para suavizado óptimo y aspecto realista para la visualización
    layer = raw_layer.resample("bicubic")

    # Preparar visualización
    vis_image, vis_params = prepare_layer_visualization(layer, band, vis_config, vis_type)
    
    # Ajustar min/max dinámicamente si se solicita y no es una clasificación discreta
    is_classified = vis_type == "classified" and vis_config.get("discrete")
    if dynamic_range and not is_classified:
        if index == "rgb" and stats_info:
            # Estiramiento dinámico de 3 bandas para RGB
            mins = []
            maxs = []
            for b in ['B4', 'B3', 'B2']:
                b_mean = stats_info.get(f"{b}_mean")
                b_std = stats_info.get(f"{b}_stdDev")
                if b_mean is not None and b_std is not None:
                    min_b = max(0.0, b_mean - 2.0 * b_std)
                    max_b = min(10000.0, b_mean + 2.0 * b_std)
                    mins.append(min_b)
                    maxs.append(max_b)
                else:
                    mins.append(0.0)
                    maxs.append(3000.0)
            vis_params["min"] = mins
            vis_params["max"] = maxs
            logger.info(f"Escala dinámica RGB aplicada en teselas: min={mins}, max={maxs}")
        elif index != "rgb" and mean_val is not None and stddev_val is not None:
            dyn_min = mean_val - 2.0 * stddev_val
            dyn_max = mean_val + 2.0 * stddev_val
            
            # Límites lógicos según el tipo de índice
            if index in ("ndvi", "savi", "evi", "ndre"):
                dyn_min = max(0.0, dyn_min)
                dyn_max = min(0.9, dyn_max)
            elif index in ("ndwi", "ndmi"):
                dyn_min = max(-0.6, dyn_min)
                dyn_max = min(0.8, dyn_max)
                
            if dyn_max > dyn_min:
                vis_params["min"] = dyn_min
                vis_params["max"] = dyn_max
                logger.info(f"Escala dinámica aplicada en teselas para índice '{index}': min={dyn_min:.4f}, max={dyn_max:.4f}")
            
    # Recortar al ROI exacto
    vis_image = vis_image.clip(roi)
    
    # Obtener MapID de Earth Engine
    try:
        map_id_dict = vis_image.getMapId(vis_params)
        tile_url = map_id_dict["tile_fetcher"].url_format
    except Exception as e:
        raise RuntimeError(f"Error generando el MapID de Earth Engine: {e}")


    return {
        "tileUrlTemplate": tile_url,
        "vis": vis_params,
        "min_val": min_val,
        "max_val": max_val,
        "mean_val": mean_val,
        "stddev_val": stddev_val
    }


def generate_time_series_data(
    roi: ee.Geometry,
    start: str,
    end: str,
    index: str,
    cloud_pct: int = 70,
    limit: int = 30,
    kml_id: Optional[str] = None,
    source: str = 'on_demand'
) -> List[Dict[str, Any]]:
    """Calcula y retorna la serie temporal de valores medios de un índice sobre un ROI, registrando mediciones."""
    init_ee()
    
    # Obtener serie temporal optimizada
    series_data = get_sentinel2_time_series(roi, start, end, index, cloud_pct, limit)
    
            
    return series_data
