"""Lo que comparten los dos handlers que suben un ráster (M.6.2b).

Nació en ``handlers/rancho.py``, cuando el único que bajaba imágenes era el mapa
mensual del rancho. Desde M.6.2b el mapa a demanda (``handlers/mapa.py``) hace lo
mismo —misma grilla, mismo nodata, mismo COG—, así que vive acá en vez de
importarse de un handler a otro.

Que sea uno solo no es orden por el orden: **el nodata y la escala son un
contrato con el tileserver**, y tenerlos en dos lados es cómo se desincronizan.
El bug ya pasó una vez con la grilla de descarga, duplicada entre
``inngest_handlers.py`` y ``export_service.py``: cumplían ``DECISIONS #19`` por
casualidad.
"""

import os
import tempfile
from typing import Any, Final

import rasterio
from pipeline.receta import Receta
from services.cog_converter import convert_to_cog
from services.ee.gee_download import descargar_a_archivo, parametros_de_descarga
from services.storage_service import get_storage_service

from handlers.utilidades import borrar_temporales

# Lo que en el COG significa "sin dato". **El GeoTIFF de GEE no declara nodata**:
# lo enmascarado llega como 0, que en NDVI es suelo desnudo (medido el 2026-09-18:
# 3.929 de 10.325 píxeles de un mes con nubes). Se rellena con un valor que ningún
# índice normalizado puede dar, y el COG lo convierte en su máscara.
NODATA_COG: Final = -9999.0

_BYTES_POR_MEGA: Final = 1_000_000


def parametros_del_mapa(roi: object, receta: Receta) -> dict[str, Any]:
    """Los de ``gee_download``, con la escala de la receta.

    La grilla (``crs``, formato) es la de ``DECISIONS #19``. La escala sale de la
    receta porque el mapa tiene que ser de los mismos píxeles que las estadísticas
    (``ARQUITECTURA`` §8.5); hoy las dos valen 10 m.
    """
    return {**parametros_de_descarga(roi), "scale": receta.escala_m}


def subir_cog(url: str, storage_key: str) -> tuple[list[float], float]:
    """Baja el GeoTIFF, lo pasa a COG y lo sube. Devuelve el bbox y los megas.

    Los temporales se borran en ``finally``: el proceso es de larga vida y los
    reintentos se acumulan.
    """
    crudo = cog = None
    try:
        fd, crudo = tempfile.mkstemp(suffix=".tif")
        os.close(fd)
        descargar_a_archivo(url, crudo)
        megas = round(os.path.getsize(crudo) / _BYTES_POR_MEGA, 2)  # noqa: PTH202 - la ruta es str de tempfile
        with rasterio.open(crudo) as fuente:
            bbox = list(fuente.bounds)
        cog = convert_to_cog(crudo, nodata=NODATA_COG)
        get_storage_service().upload_file(storage_key, cog, "image/tiff")
    finally:
        borrar_temporales(crudo, cog)
    return bbox, megas
