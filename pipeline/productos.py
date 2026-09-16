"""Los productos del mes: las etapas encadenadas, sin pedirle nada a GEE (M.2.5).

``ARQUITECTURA_PIPELINE.md`` §2: una parcela usa la rama de la **reducción**
—números, sin descargar un píxel— y un rancho la de la **imagen** —el COG del
mes—. **Las dos parten del mismo compuesto**, con la misma receta y a la misma
escala, así que el número de la parcela y el color del mapa salen de los mismos
píxeles. Eso cierra B-1 por construcción.

Este módulo es la única pieza que conoce el orden de las etapas. Sigue siendo
puro: arma expresiones. Quien las calcula es ``ejecucion.py``.
"""

import ee

from pipeline.etapas import compuesto, fuente, nubes, reduccion
from pipeline.periodos import Mes
from pipeline.receta import Receta


def compuesto_del_mes(roi: ee.Geometry, mes: Mes, receta: Receta) -> ee.Image:
    """El compuesto mensual del ROI: fuente, máscara y mediana por píxel.

    Es el tronco común de los dos productos. Se expone porque M.2.6 lo compara
    contra la capa vieja, y porque un handler que quiera las dos cosas del mismo
    mes no tiene que armarlo dos veces.
    """
    escenas = fuente.coleccion(roi, mes, receta)
    limpias = escenas.map(lambda escena: nubes.enmascarar(ee.Image(escena), receta))
    return compuesto.compuesto(limpias, receta)


def estadisticas_del_mes(roi: ee.Geometry, mes: Mes, receta: Receta) -> ee.Dictionary:
    """Los números de una parcela en un mes: la rama de la reducción.

    Lo que devuelve lo lee :func:`pipeline.etapas.reduccion.leer`, que valida que
    estén todas las claves.
    """
    return reduccion.valores(compuesto_del_mes(roi, mes, receta), roi, receta)


def mapa_del_mes(roi: ee.Geometry, mes: Mes, receta: Receta, indice: str) -> ee.Image:
    """La imagen de un índice en el mes, recortada al ROI: la rama del COG.

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
    return compuesto_del_mes(roi, mes, receta).select([indice]).clip(roi)
