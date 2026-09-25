"""Los productos de una ventana: las etapas encadenadas, sin pedirle nada a GEE.

``ARQUITECTURA_PIPELINE.md`` §2: una parcela usa la rama de la **reducción**
—números, sin descargar un píxel— y un rancho la de la **imagen** —el COG—. **Las
dos parten del mismo compuesto**, con la misma receta y a la misma escala, así que
el número de la parcela y el color del mapa salen de los mismos píxeles. Eso
cierra B-1 por construcción.

Este módulo es la única pieza que conoce el orden de las etapas. Sigue siendo
puro: arma expresiones. Quien las calcula es ``ejecucion.py``.

**Desde M.9.0b la unidad es la** :class:`~pipeline.ventanas.Ventana` **y no el
mes** (``DECISIONS #63``). Acá no cambió nada más que el nombre del argumento: el
compuesto siempre fue "todas las pasadas del intervalo, mediana por píxel", y que
ese intervalo sea un mes o una pasada sola es de quien arma las ventanas, no de
acá. Con una ventana de una pasada, la mediana es de una sola imagen y devuelve
esa imagen.
"""

import ee

from pipeline.etapas import compuesto, fuente, nubes, reduccion
from pipeline.receta import Receta
from pipeline.ventanas import Ventana


def compuesto_de(roi: ee.Geometry, ventana: Ventana, receta: Receta) -> ee.Image:
    """El compuesto de la ventana sobre el ROI: fuente, máscara y mediana por píxel.

    Es el tronco común de los dos productos. Se expone porque un handler que
    quiera las dos cosas de la misma ventana no tiene que armarlo dos veces, y
    porque ``scripts/check_pipeline_real.py`` lo compara contra las pasadas.
    """
    escenas = fuente.coleccion(roi, ventana, receta)
    limpias = escenas.map(lambda escena: nubes.enmascarar(ee.Image(escena), receta))
    return compuesto.compuesto(limpias, receta)


def estadisticas_de(
    roi: ee.Geometry, ventana: Ventana, receta: Receta
) -> ee.Dictionary:
    """Los números de una parcela en una ventana: la rama de la reducción.

    Lo que devuelve lo lee :func:`pipeline.etapas.reduccion.leer`, que valida que
    estén todas las claves.
    """
    return reduccion.valores(compuesto_de(roi, ventana, receta), roi, receta)


def mapa_de(
    roi: ee.Geometry, ventana: Ventana, receta: Receta, indice: str
) -> ee.Image:
    """La imagen de un índice en la ventana, recortada al ROI: la rama del COG.

    Una sola banda, la del índice pedido, porque es lo que el tileserver sirve.
    El recorte va acá y no en el compuesto: las estadísticas ya se calculan sobre
    el ROI con ``reduceRegion``, y recortar antes las obligaría a pagar el
    recorte dos veces.

    **La imagen no trae una escala útil.** Su proyección por defecto es WGS84 de
    1° (111.319 m, medido el 2026-09-16): la aritmética de bandas pierde la
    proyección de la escena. Quien la descargue tiene que pasar ``scale`` y
    ``crs``, que es lo que hace ``services/ee/gee_download.py`` desde
    ``DECISIONS #19``. La máscara sí se calculó a ``escala_m`` (M.2.2), así que
    los píxeles son los mismos que los de las estadísticas.

    Raises:
        ValueError: si el índice no es uno de los de la receta. Pedir un índice
            que la receta no calculó daría una imagen sin bandas, y el error
            aparecería recién al descargar.
    """
    if indice not in receta.indices:
        msg = f"el índice {indice!r} no está en la receta: {list(receta.indices)}"
        raise ValueError(msg)
    return compuesto_de(roi, ventana, receta).select([indice]).clip(roi)
