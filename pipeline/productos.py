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
from pipeline.indices import ESCALA_COG_NORMALIZADO, INDICES, TOPE_INT16
from pipeline.receta import COLOR_REAL, PRODUCTO_COLOR_REAL, Receta
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


# Por cuánto se multiplica una banda para guardarla como entero (M.9.7b,
# `DECISIONS #73`). Cuatro decimales es más de lo que el sensor sostiene, y un
# índice acotado a [-1, 1] cabe de sobra en un int16 (±32.767). Es lo habitual en
# Sentinel-2, y el mismo número va en la fila de `layers` (`escala`) para que quien
# pinta multiplique su rango. **Desde M.9.3 es la de las bandas normalizadas y del
# color real**: un índice que no vive en [-1, 1] —el LAI— trae la suya en el
# registro (`Indice.escala_cog`), y `escala_de_banda` es quien decide.
ESCALA_DEL_COG: Final = ESCALA_COG_NORMALIZADO


def escala_de_banda(nombre: str) -> int:
    """Por cuánto se multiplica una banda del COG: la del índice, o la de siempre.

    El color real es reflectancia por 10.000, el número de S2 tal cual.
    """
    return INDICES[nombre].escala_cog if nombre in INDICES else ESCALA_DEL_COG


def escala_de_producto(producto: str) -> int:
    """La ``escala`` de la fila de ``layers`` de un producto: la de su banda.

    El color real son tres bandas con la misma escala, la de la reflectancia.
    """
    if producto == PRODUCTO_COLOR_REAL:
        return ESCALA_DEL_COG
    return escala_de_banda(producto)


def productos_del_cog(receta: Receta) -> tuple[str, ...]:
    """Las filas de ``layers`` que salen de un COG del rancho, en orden.

    Un índice por fila y, si la receta lleva el color real, una fila más,
    ``PRODUCTO_COLOR_REAL``, que pinta las tres bandas del color (M.9.7e2).
    """
    if receta.color_real:
        return (*receta.indices, PRODUCTO_COLOR_REAL)
    return receta.indices


def bandas_del_cog(receta: Receta) -> tuple[str, ...]:
    """Las bandas del COG del rancho, en orden: la ``bidx`` de cada una es su lugar.

    Los índices y, detrás, el rojo, el verde y el azul si la receta lleva el color
    real. Detrás y no delante: la banda de cada índice es la misma que en v2.
    """
    color = tuple(COLOR_REAL) if receta.color_real else ()
    return (*receta.indices, *color)


def bandas_de_producto(receta: Receta, producto: str) -> list[int]:
    """Las ``bandas`` de la fila de ``layers`` de un producto: su ``bidx``, desde 1.

    Un índice es una banda; el color real son tres, en el orden R, G, B, que es
    como TiTiler las pinta cuando recibe tres ``bidx``.

    Raises:
        ValueError: si el producto no sale de esta receta.
    """
    bandas = bandas_del_cog(receta)
    if producto == PRODUCTO_COLOR_REAL and receta.color_real:
        return [bandas.index(nombre) + 1 for nombre in COLOR_REAL]
    if producto in receta.indices:
        return [bandas.index(producto) + 1]
    msg = f"la receta {receta.version} no produce {producto!r}"
    raise ValueError(msg)


def mapa_multibanda_de(
    roi: ee.Geometry, ventana: Ventana, receta: Receta, indices: Sequence[str]
) -> ee.Image:
    """Los índices de la ventana en **una** imagen, una banda cada uno, como enteros.

    Es el COG multibanda de M.9.7 (``ARQUITECTURA_PIPELINE.md`` §3.6): un archivo
    por ventana en vez de uno por índice, así que una descarga en vez de cuatro.
    Las bandas salen **en el orden de** ``indices``, y ese orden es el ``bidx`` de
    cada capa: la banda 1 es ``indices[0]``.

    Los valores van **por su escala** (``escala_de_banda``) **y redondeados**, en
    int16: la
    mitad de tamaño que en float32, y el color real (M.9.7e) entra en el mismo
    archivo. Lo enmascarado sigue enmascarado: quien descarga lo rellena con su
    centinela.

    ``indices`` puede traer también las bandas del color real si la receta lo
    lleva (``bandas_del_cog``): la reflectancia por 10.000 es el número de S2 tal
    cual, y cabe en un int16.

    Raises:
        ValueError: si alguna banda no es de la receta, o si no se pide ninguna.
    """
    if not indices:
        msg = "el mapa multibanda necesita al menos un índice"
        raise ValueError(msg)
    fuera = [indice for indice in indices if indice not in bandas_del_cog(receta)]
    if fuera:
        msg = f"la receta {receta.version} no calcula {fuera}: {bandas_del_cog(receta)}"
        raise ValueError(msg)
    return (
        compuesto_de(roi, ventana, receta)
        .select(list(indices))
        # Una constante por banda, en el orden de `indices`: GEE multiplica banda a
        # banda. Con una sola escala para todo, el LAI se recortaba al tope.
        .multiply(ee.Image.constant([escala_de_banda(nombre) for nombre in indices]))
        .round()
        # **El tope del int16, sin el mínimo.** Con \`acotar_indices\` un índice ya
        # vive en [-1, 1] y esto no hace nada. Sin él —una receta futura— un EVI de 4
        # daría 40.000, que en int16 se corrompe sin error; y un píxel válido que
        # cayera en -32.768 se confundiría con el centinela \`NODATA_ENTERO\` y
        # desaparecería del mapa. Acotar a ±32.767 hace imposibles las dos cosas.
        .clamp(-TOPE_INT16, TOPE_INT16)
        .toInt16()
        .clip(roi)
    )
