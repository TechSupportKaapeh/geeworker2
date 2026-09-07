import os
import time
import logging
import csv
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List
import ee

from config import BASE_OUTPUT_DIR
from utils_pkg import ensure_outputs_dir, timestamped_base
from utils_pkg.visualization import index_band_and_vis
from services.ee.ee_indices import compute_sentinel2_index
from services.ee.gee_download import SCALE_METROS, descargar_a_archivo, descargar_geotiff
from services.ee_service import prepare_layer_visualization

logger = logging.getLogger("export_service")

def get_classified_palette(index: str) -> List[str]:
    idx = (index or '').lower()
    if idx in ('ndmi', 'svhi', 'nsmi', 'soil_moisture', 'vegetation_health'):
        # Paleta divergente (estrés hídrico y humedad)
        return ['#b30000', '#fdae61', '#ffffbf', '#abd9e9', '#2c7bb6']
    elif idx in ('change_detection'):
        # Paleta divergente para detección de cambios
        return ['#d7191c', '#fdae61', '#ffffbf', '#a6d96a', '#1a9641']
    else:
        # Paleta secuencial (vigor y biomasa)
        return ['#8c2d04', '#feb24c', '#ffffbf', '#74c476', '#006837']

def export_heatmap(
    roi: ee.Geometry,
    roi_bounds: Optional[List[float]],
    index: str,
    start: str,
    end: str,
    cloud_pct: int = 30,
    export_format: str = "geotiff",
    dynamic_range: bool = True,
    vis_type: str = "continuous",
    kml_id: Optional[str] = None
) -> Tuple[str, Dict[str, Any]]:
    """Descarga e inserta como asset un composite de heatmap en formato GeoTIFF o PNG."""
    ensure_outputs_dir()
    
    # Obtener prefijo de archivo temporal/único
    base_filename, _ = timestamped_base(index, start, end)
    
    # Obtener el composite
    img = compute_sentinel2_index(roi, start, end, index, cloud_pct)
    if img is None:
        raise ValueError("No hay imágenes válidas disponibles para exportar composite.")
        
    band, vis_config = index_band_and_vis(index, satellite="sentinel2")
    try:
        layer = img.select(band).clip(roi)
    except Exception:
        layer = img.clip(roi)
        
    # Calcular estadísticas básicas para registrar el asset
    min_val = max_val = mean_val = stddev_val = None
    stats_info = None
    try:
        target_band = band if isinstance(band, str) else (band[0] if band else None)
        bands_to_select = band if isinstance(band, list) else [target_band]
        if target_band:
            reducer = ee.Reducer.mean().combine(ee.Reducer.min(), None, True).combine(ee.Reducer.max(), None, True).combine(ee.Reducer.stdDev(), None, True)
            stats_info = layer.select(bands_to_select).reduceRegion(reducer, geometry=roi, scale=10, maxPixels=1e9, bestEffort=True).getInfo()
            
            mean_val = float(stats_info.get(f"{target_band}_mean") or stats_info.get("mean", 0))
            min_val = float(stats_info.get(f"{target_band}_min") or stats_info.get("min", 0))
            max_val = float(stats_info.get(f"{target_band}_max") or stats_info.get("max", 0))
            stddev_val = float(stats_info.get(f"{target_band}_stdDev") or stats_info.get("stdDev", 0))
    except Exception as e:
        logger.warning(f"No se pudieron calcular estadísticas para exportar: {e}")

    # Preparar visualización continua para el composite (necesario para el frontend o el PNG)
    vis_image, vis_params = prepare_layer_visualization(layer, band, vis_config, "continuous")
    
    # Ajustar min/max dinámicamente si se solicita y hay estadísticas disponibles
    if dynamic_range:
        if index == "rgb" and stats_info:
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
            logger.info(f"Escala dinámica RGB calculada para exportación: min={mins}, max={maxs}")
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
                logger.info(f"Escala dinámica calculada para índice '{index}': min={dyn_min:.4f}, max={dyn_max:.4f}")

    # Reclasificar en 5 clases si vis_type == "classified"
    if vis_type == "classified" and index != "rgb":
        c_min = vis_params.get("min", 0.0)
        c_max = vis_params.get("max", 1.0)
        if isinstance(c_min, list):
            c_min = c_min[0]
        if isinstance(c_max, list):
            c_max = c_max[0]
        
        # Generar 5 intervalos discretos
        step = (c_max - c_min) / 5.0
        classified = ee.Image(0)
        for i in range(1, 5):
            thresh = c_min + i * step
            classified = classified.where(layer.gt(thresh), i)
            
        # Reemplazar la capa original por la clasificada
        layer = classified.rename(band)
        
        # Sobrescribir parámetros de visualización para el mapeo a paleta de 5 colores
        vis_params["min"] = 0
        vis_params["max"] = 4
        vis_params["palette"] = get_classified_palette(index)
        logger.info(f"Capa clasificada en 5 clases: min={c_min}, max={c_max}")

    saved_path = ""
    asset_id = ""

    if export_format == "geotiff":
        saved_path = str(Path(BASE_OUTPUT_DIR) / f"{base_filename}.tif")
        # La descarga vive en `services/ee/gee_download.py`. Estaba duplicada
        # aca y en `inngest_handlers.py`, con la grilla —`scale`, `crs`—
        # repetida a mano en los dos lados: coincidian por casualidad, y
        # `DECISIONS #19` exige que **no puedan** divergir, porque dos grillas
        # distintas no fallan al descargar, fallan al componer el mosaico.
        descargar_geotiff(layer, roi, saved_path)
        asset_id = base_filename + ".tif"
        
    elif export_format == "png":
        saved_path = str(Path(BASE_OUTPUT_DIR) / f"{base_filename}.png")
                
        # Aplicar remuestreo y visualización del lado del servidor
        vis_image = vis_image.resample("bicubic")
        
        # Construir argumentos de visualización para Earth Engine
        vis_arg = {}
        if isinstance(band, list):
            vis_arg['bands'] = band
        else:
            vis_arg['bands'] = [band]
            
        vis_arg['min'] = vis_params.get('min')
        vis_arg['max'] = vis_params.get('max')
        if vis_params.get('palette'):
            vis_arg['palette'] = vis_params.get('palette')
            
        # Convertir a imagen RGB de 8 bits en el servidor y recortar
        visualized_img = vis_image.visualize(**vis_arg).clip(roi)
        
        thumb_params = {
            'region': roi,
            'dimensions': 1024,
            'format': 'png'
        }
        
        try:
            url = visualized_img.getThumbURL(thumb_params)
        except Exception:
            url = vis_image.getDownloadURL(
                {'scale': SCALE_METROS, 'region': roi, 'format': 'PNG'}
            )

        # El PNG arma sus propios parametros de visualizacion, pero la descarga
        # es la misma: el timeout y el chequeo de archivo vacio valen igual.
        descargar_a_archivo(url, saved_path)
        asset_id = base_filename + ".png"
        
    else:
        raise ValueError(f"Formato de exportación no soportado para heatmap: {export_format}")

    return saved_path, {
        "asset_id": asset_id,
        "product": index,
        "format": export_format,
        "min_val": min_val,
        "max_val": max_val,
        "mean_val": mean_val,
        "stddev_val": stddev_val,
        "vis": vis_params
    }


def export_time_series(
    series_pts: List[Dict[str, Any]],
    index: str,
    start: str,
    end: str,
    roi: ee.Geometry,
    roi_bounds: Optional[List[float]],
    kml_id: Optional[str] = None
) -> Tuple[str, str]:
    """Exporta una serie temporal en un archivo CSV y lo registra en base de datos."""
    ensure_outputs_dir()
    base_filename, _ = timestamped_base(index, start, end)
    saved_path = str(Path(BASE_OUTPUT_DIR) / f"{base_filename}.csv")
    
    with open(saved_path, 'w', newline='', encoding='utf-8') as fh:
        writer = csv.writer(fh)
        writer.writerow(['date', 'value'])
        for pt in series_pts:
            writer.writerow([pt.get('date'), pt.get('value') or pt.get('mean')])
            
    asset_id = f"{index}_{int(time.time())}_series"
    
    return saved_path, asset_id
