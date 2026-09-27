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

import contextvars
import os
import tempfile
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
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

# El mismo centinela para el COG multibanda de enteros (M.9.7b): el mínimo de un
# int16. Un índice por 10.000 va de -10.000 a 10.000, así que no puede darlo.
NODATA_ENTERO: Final = -32768

_BYTES_POR_MEGA: Final = 1_000_000


def parametros_del_mapa(roi: object, receta: Receta) -> dict[str, Any]:
    """Los de ``gee_download``, con la escala de la receta.

    La grilla (``crs``, formato) es la de ``DECISIONS #19``. La escala sale de la
    receta porque el mapa tiene que ser de los mismos píxeles que las estadísticas
    (``ARQUITECTURA`` §8.5); hoy las dos valen 10 m.
    """
    return {**parametros_de_descarga(roi), "scale": receta.escala_m}


def subir_cog(
    url: str, storage_key: str, *, nodata: float = NODATA_COG
) -> tuple[list[float], float]:
    """Baja el GeoTIFF, lo pasa a COG y lo sube. Devuelve el bbox y los megas.

    ``nodata`` es el centinela con que se rellenó lo enmascarado antes de bajarlo:
    ``NODATA_COG`` en los de un índice en decimales, ``NODATA_ENTERO`` en los
    multibanda de enteros.

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
        cog = convert_to_cog(crudo, nodata=nodata)
        get_storage_service().upload_file(storage_key, cog, "image/tiff")
    finally:
        borrar_temporales(crudo, cog)
    return bbox, megas


# Cuántas pasadas se bajan a la vez en el paso del rancho (M.9.7e2). Con la cola
# de `CONCURRENCIA_GEE` (5 corridas) son hasta 20 pedidos a GEE en vuelo, que es
# lo que GEE acepta de una cuenta de servicio sin empezar a contestar "too many
# concurrent aggregations" —y si lo contesta, es pasajero y el step se reintenta—.
# En serie, un mes del Cauca con 14 pasadas se iba a ~46 s (ARQUITECTURA §3.6).
DESCARGAS_EN_PARALELO: Final = 4


def en_paralelo[T, R](
    funcion: Callable[[T], R],
    elementos: Sequence[T],
    *,
    hilos: int = DESCARGAS_EN_PARALELO,
) -> list[R]:
    """``funcion`` sobre cada elemento, en hilos, con los resultados en orden.

    **Cada tarea corre en una copia del contexto** del que llama
    (``contextvars.copy_context``): el job de la bitácora y el conteo de llamadas a
    GEE viven en ``ContextVar``, y un hilo del pool arranca sin ellos. Sin esto,
    las URLs pedidas desde un hilo no se contarían y un ``reportar`` no sabría de
    qué job es.

    Si una tarea falla, el error sale acá, de la primera que falló en el orden de
    ``elementos``, y las que no arrancaron se cancelan. El step falla entero y se
    reintenta: lo que ya se subió se vuelve a subir a la misma key.
    """
    if len(elementos) <= 1:
        return [funcion(elemento) for elemento in elementos]
    with ThreadPoolExecutor(max_workers=hilos) as pool:
        futuros = [
            pool.submit(contextvars.copy_context().run, funcion, elemento)
            for elemento in elementos
        ]
        try:
            return [futuro.result() for futuro in futuros]
        except BaseException:
            for futuro in futuros:
                futuro.cancel()
            raise
