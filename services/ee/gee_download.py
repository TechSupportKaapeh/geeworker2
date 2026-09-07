"""La descarga de un raster desde Google Earth Engine, en un solo lugar.

**Por que existe este modulo.** El codigo para bajar un GeoTIFF de GEE estaba
duplicado literalmente en dos sitios —`services/inngest_handlers.py` y
`services/export_service.py`—: mismo `getDownloadURL`, mismos parametros, mismo
`requests.get` por chunks. Dos copias que coincidian **por casualidad, no por
construccion**.

Y ahi hay un contrato que no se puede dejar al azar. `DECISIONS #19`:

> Todas las descargas de una entidad tienen que caer en la misma grilla. Mismo
> `region`, `scale` y `crs`, o las pasadas no se pueden apilar. Si se escapa, el
> bug aparece recien al componer.

O sea: si una de las dos copias cambia `scale` de 10 a 20, o el `crs`, los COG
de la misma parcela dejan de alinearse y el mosaico de la FASE C sale mal —sin
que nada falle en el momento de la descarga—. Un unico lugar convierte ese
riesgo en imposible.
"""
import logging

import requests

logger = logging.getLogger(__name__)

# La grilla. **No cambiar sin leer `DECISIONS #19` y sin recalcular el
# historico**: las pasadas ya guardadas quedaron en esta grilla, y mezclar
# grillas no da un error, da un mosaico mal alineado.
SCALE_METROS = 10
CRS = "EPSG:4326"
FORMATO = "GEO_TIFF"

# Sin timeout, `requests` espera indefinidamente. Como este trabajo corre en el
# pool de hilos del worker, una descarga colgada retiene un hilo hasta que
# alguien reinicie el proceso. Holgado para un GeoTIFF de `getDownloadURL`, que
# de por si tiene tope de tamano.
TIMEOUT_SEGUNDOS = 300

_CHUNK = 8192


def parametros_de_descarga(roi):
    """Los parametros de `getDownloadURL` para un ROI, con la grilla fija."""
    return {
        "scale": SCALE_METROS,
        "crs": CRS,
        "format": FORMATO,
        "region": roi,
    }


def descargar_geotiff(layer, roi, destino: str) -> str:
    """Baja `layer` recortado a `roi` como GeoTIFF en `destino`.

    `layer` es una `ee.Image` ya seleccionada y recortada. Devuelve `destino`
    para poder encadenar.
    """
    return descargar_a_archivo(layer.getDownloadURL(parametros_de_descarga(roi)), destino)


def descargar_a_archivo(url: str, destino: str) -> str:
    """Baja una URL de descarga de GEE a un archivo local.

    Existe aparte de `descargar_geotiff` porque la exportacion a PNG arma sus
    propios parametros de visualizacion, pero **el timeout y el chequeo de
    archivo vacio valen igual para las dos**.
    """
    respuesta = requests.get(url, stream=True, timeout=TIMEOUT_SEGUNDOS)
    respuesta.raise_for_status()

    escritos = 0
    with open(destino, "wb") as archivo:
        for chunk in respuesta.iter_content(chunk_size=_CHUNK):
            if chunk:
                archivo.write(chunk)
                escritos += len(chunk)

    if escritos == 0:
        # Un archivo de 0 bytes pasa el `raise_for_status` y revienta despues,
        # en `rasterio` o en `cog_translate`, con un error que no menciona la
        # descarga. Mejor fallar aca, donde se ve la causa.
        raise RuntimeError(
            f"GEE devolvio un GeoTIFF vacio para {destino}. Suele ser un ROI sin "
            "datos en el periodo pedido, o el tope de tamano de getDownloadURL"
        )

    logger.info("GeoTIFF descargado de GEE: %s bytes en %s", escritos, destino)
    return destino
