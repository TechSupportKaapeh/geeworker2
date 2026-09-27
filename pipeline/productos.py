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

from collections.abc import Sequence
from typing import Final

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


def estadisticas_de_ventanas(
    roi: ee.Geometry, ventanas: Sequence[Ventana], receta: Receta
) -> ee.List:
    """Los números de una parcela en varias ventanas, en **una** expresión (M.9.7d).

    Es la lista de :func:`estadisticas_de`, una por ventana y en el mismo orden:
    cada elemento es exactamente la expresión que se pedía suelta, así que cada
    número sale igual que antes. Lo que cambia es que se pide todo junto.

    **Por qué junto** (``DECISIONS #74``): con una ventana por pasada, lo que
    costaba un mes no era reducir sino ir y volver. Medido el 2026-09-27, una
    pasada tapada costaba lo mismo que una útil —unos 2 s cada una, casi todo
    latencia— y un mes del Cauca de 19 pasadas, 44 s en serie contra 5 s junto.
    """
    return ee.List([estadisticas_de(roi, ventana, receta) for ventana in ventanas])


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


# Por cuánto se multiplica cada índice para guardarlo como entero (M.9.7b,
# `DECISIONS #73`). Cuatro decimales es más de lo que el sensor sostiene, y un
# índice acotado a [-1, 1] cabe de sobra en un int16 (±32.767). Es lo habitual en
# Sentinel-2, y el mismo número va en la fila de `layers` (`escala`) para que quien
# pinta multiplique su rango.
ESCALA_DEL_COG: Final = 10_000


def mapa_multibanda_de(
    roi: ee.Geometry, ventana: Ventana, receta: Receta, indices: Sequence[str]
) -> ee.Image:
    """Los índices de la ventana en **una** imagen, una banda cada uno, como enteros.

    Es el COG multibanda de M.9.7 (``ARQUITECTURA_PIPELINE.md`` §3.6): un archivo
    por ventana en vez de uno por índice, así que una descarga en vez de cuatro.
    Las bandas salen **en el orden de** ``indices``, y ese orden es el ``bidx`` de
    cada capa: la banda 1 es ``indices[0]``.

    Los valores van **por** ``ESCALA_DEL_COG`` **y redondeados**, en int16: la
    mitad de tamaño que en float32, y el color real (M.9.7e) entra en el mismo
    archivo. Lo enmascarado sigue enmascarado: quien descarga lo rellena con su
    centinela.

    Raises:
        ValueError: si algún índice no es de la receta, o si no se pide ninguno.
    """
    if not indices:
        msg = "el mapa multibanda necesita al menos un índice"
        raise ValueError(msg)
    fuera = [indice for indice in indices if indice not in receta.indices]
    if fuera:
        msg = f"la receta {receta.version} no calcula {fuera}: {list(receta.indices)}"
        raise ValueError(msg)
    return (
        compuesto_de(roi, ventana, receta)
        .select(list(indices))
        .multiply(ESCALA_DEL_COG)
        .round()
        # **El tope del int16, sin el mínimo.** Con \`acotar_indices\` un índice ya
        # vive en [-1, 1] y esto no hace nada. Sin él —una receta futura— un EVI de 4
        # daría 40.000, que en int16 se corrompe sin error; y un píxel válido que
        # cayera en -32.768 se confundiría con el centinela \`NODATA_ENTERO\` y
        # desaparecería del mapa. Acotar a ±32.767 hace imposibles las dos cosas.
        .clamp(-_TOPE_INT16, _TOPE_INT16)
        .toInt16()
        .clip(roi)
    )


# El mayor int16 en valor absoluto que no es el centinela de "sin dato" (-32.768).
_TOPE_INT16: Final = 32_767
