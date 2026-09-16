"""La reducción: del compuesto del mes a los números de la parcela (M.2.4).

La última etapa de ``ARQUITECTURA_PIPELINE.md`` §2. Arma la expresión y no la
calcula (§3.3): el pedido lo hace ``ejecucion.py`` (M.2.5).

De la imagen del mes salen tres cosas:

- **las estadísticas de cada índice**, con el reductor que arma
  :func:`plan_de_reduccion` (``DECISIONS #36``): los percentiles en un solo
  histograma y el mínimo con el máximo en un ``minMax``;
- **la cobertura**, la fracción de la parcela con al menos una observación limpia
  en el mes. Debajo del mínimo de la receta, la fila se escribe con ``valor``
  nulo (``ARQUITECTURA`` §6);
- **las observaciones**, la mediana de ``n_obs``: cuántas pasadas limpias tuvo un
  píxel típico.

**Ningún pedido cambia la escala solo** (``DECISIONS #36``). Todos van con
``bestEffort=False`` y un ``maxPixels`` explícito: si GEE no puede a ``escala_m``,
el pedido falla y M.2.5 traduce el error. Con ``bestEffort=True``, que es lo que
usaba la serie vieja, GEE devuelve un número calculado a otra escala sin avisar.

**Una clave que falta es un error, no un nulo.** Lo distingue :func:`leer`:
- que la respuesta no traiga la clave significa que el pedido no calculó lo que
  se le pidió, y eso se levanta;
- que la traiga en ``None`` significa que no hubo un solo píxel con dato, y eso
  es un mes sin cobertura, que es un resultado válido.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

import ee

from pipeline.estadisticas import Reductor, claves_de_salida, plan_de_reduccion
from pipeline.etapas.compuesto import BANDA_OBSERVACIONES
from pipeline.receta import Receta

CLAVE_COBERTURA: Final = "cobertura"
CLAVE_OBSERVACIONES: Final = "observaciones"

# El tope de píxeles de cada pedido. No sale de la receta porque no cambia ningún
# número: es el límite de lo que se está dispuesto a calcular. A 10 m son 10.000
# km², mil veces el rancho más grande que se espera. Si un pedido lo pasa, falla,
# y eso es lo que se quiere: un pedido sin tope consume hasta que GEE lo corta.
MAX_PIXELES: Final = 100_000_000

# Los métodos de `ee.Reducer` que el plan puede pedir. El nombre viene de un dato
# (`Reductor.metodo`), así que se valida contra esta lista antes de usarlo: llamar
# a ciegas un método cuyo nombre sale de datos es el patrón que hay que evitar,
# aunque hoy esos datos sean nuestros.
_METODOS_SIN_ARGUMENTOS: Final = frozenset({"minMax", "mean", "stdDev", "min", "max"})
_METODO_PERCENTIL: Final = "percentile"


@dataclass(frozen=True, slots=True)
class Reduccion:
    """Los números de una parcela en un mes, ya leídos de la respuesta de GEE.

    Attributes:
        estadisticas: por índice, el valor de cada estadística de la receta, con
            los nombres del registro (``mediana``, ``p10``…). Un valor en ``None``
            es un mes sin píxeles con dato.
        cobertura: de 0 a 1.
        observaciones: la mediana de ``n_obs``, o ``None`` si no hubo dato.
    """

    estadisticas: Mapping[str, Mapping[str, float | None]]
    cobertura: float
    observaciones: float | None


def _reductor_de(paso: Reductor) -> ee.Reducer:
    """El ``ee.Reducer`` de un paso del plan."""
    if paso.metodo == _METODO_PERCENTIL:
        # Los nombres de salida los pone el pipeline, no GEE: así `p10` es `p10` y
        # no `p10_p10` al combinarlo.
        return ee.Reducer.percentile(
            list(paso.percentiles), outputNames=list(paso.salidas)
        )
    if paso.metodo not in _METODOS_SIN_ARGUMENTOS:
        msg = f"método de reducción desconocido: {paso.metodo!r}"
        raise ValueError(msg)
    return getattr(ee.Reducer, paso.metodo)()


def reductor(receta: Receta) -> ee.Reducer:
    """Un solo reductor con las estadísticas de la receta, fusionadas.

    ``sharedInputs=True`` porque todos miran los mismos píxeles: GEE los recorre
    una vez. Las salidas conservan su nombre, así que las claves de la respuesta
    son las de :func:`claves_de_salida`.
    """
    combinado: ee.Reducer | None = None
    for paso in plan_de_reduccion(receta.estadisticas):
        siguiente = _reductor_de(paso)
        combinado = (
            siguiente
            if combinado is None
            else combinado.combine(siguiente, sharedInputs=True)
        )
    if combinado is None:  # pragma: no cover - la receta valida que haya al menos una
        msg = "la receta no pidió ninguna estadística"
        raise ValueError(msg)
    return combinado


def _reducir(
    imagen: ee.Image, roi: ee.Geometry, receta: Receta, reduce: ee.Reducer
) -> ee.Dictionary:
    """Un ``reduceRegion`` con la política de pedidos de ``DECISIONS #36``."""
    return imagen.reduceRegion(
        reducer=reduce,
        geometry=roi,
        scale=receta.escala_m,
        bestEffort=False,
        maxPixels=MAX_PIXELES,
    )


def cobertura(compuesto: ee.Image, roi: ee.Geometry, receta: Receta) -> ee.Dictionary:
    """La fracción de la parcela con al menos una observación limpia en el mes.

    Se mide sobre la máscara del primer índice: ``mask()`` vale 1 donde hay dato y
    0 donde no, y no deja píxeles enmascarados, así que el promedio sobre el ROI
    es la fracción cubierta. Todos los índices comparten la máscara de su pasada
    (M.2.2), así que cualquiera sirve.
    """
    presente = compuesto.select([receta.indices[0]]).mask().rename(CLAVE_COBERTURA)
    return _reducir(presente, roi, receta, ee.Reducer.mean())


def observaciones(
    compuesto: ee.Image, roi: ee.Geometry, receta: Receta
) -> ee.Dictionary:
    """La mediana de ``n_obs`` sobre la parcela.

    Donde ningún píxel tuvo observaciones, ``n_obs`` viene enmascarada y no entra
    en la mediana. Si no hubo ninguna en toda la parcela, la clave viene en
    ``None``, que es lo mismo que decir cobertura cero.
    """
    banda = compuesto.select([BANDA_OBSERVACIONES]).rename(CLAVE_OBSERVACIONES)
    return _reducir(banda, roi, receta, ee.Reducer.median())


def valores(compuesto: ee.Image, roi: ee.Geometry, receta: Receta) -> ee.Dictionary:
    """Todo lo que hay que pedirle a GEE para una parcela y un mes, en un diccionario.

    Junta las estadísticas de los índices, la cobertura y las observaciones. Es
    una sola expresión: M.2.5 la pide de una vez, y cuenta una llamada.
    """
    estadisticas = _reducir(
        compuesto.select(list(receta.indices)), roi, receta, reductor(receta)
    )
    return (
        ee.Dictionary(estadisticas)
        .combine(cobertura(compuesto, roi, receta))
        .combine(observaciones(compuesto, roi, receta))
    )


def leer(respuesta: Mapping[str, object], receta: Receta) -> Reduccion:
    """Lee la respuesta de :func:`valores` y la valida.

    Args:
        respuesta: lo que devolvió GEE, ya traído (un ``dict`` de Python).
        receta: la que armó el pedido.

    Returns:
        La :class:`Reduccion` con los nombres del registro, no con los sufijos de
        GEE: la clave ``ndvi_p50`` entra como ``estadisticas["ndvi"]["mediana"]``.

    Raises:
        ValueError: si falta una clave, o si la cobertura es un número fuera de
            [0, 1]. Faltar significa que el pedido no calculó lo que se le pidió;
            tomarlo por un nulo guardaría un mes vacío sin que nadie se entere.
        TypeError: si la cobertura no es un número.
    """
    claves = claves_de_salida(receta.indices, receta.estadisticas)
    esperadas = {*claves.values(), CLAVE_COBERTURA, CLAVE_OBSERVACIONES}
    faltan = esperadas - respuesta.keys()
    if faltan:
        msg = f"la respuesta de GEE no trae {sorted(faltan)}"
        raise ValueError(msg)

    cubierto = respuesta[CLAVE_COBERTURA]
    # `bool` es un `int` para Python: `True` pasaría por una cobertura de 1.
    if not isinstance(cubierto, int | float) or isinstance(cubierto, bool):
        msg = f"cobertura no numérica: {cubierto!r}"
        raise TypeError(msg)
    if not 0 <= cubierto <= 1:
        msg = f"cobertura fuera de [0, 1]: {cubierto}"
        raise ValueError(msg)

    estadisticas: dict[str, dict[str, float | None]] = {}
    for (indice, estadistica), clave in claves.items():
        estadisticas.setdefault(indice, {})[estadistica] = respuesta[clave]  # type: ignore[assignment]
    return Reduccion(
        estadisticas=estadisticas,
        cobertura=float(cubierto),
        observaciones=respuesta[CLAVE_OBSERVACIONES],  # type: ignore[arg-type]
    )
