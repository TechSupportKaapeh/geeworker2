"""Registro de estadísticas (M.1.3, ``DECISIONS #32``).

Cada estadística es una entrada de :data:`ESTADISTICAS`:

- el nombre con que se guarda en ``measurements.estadisticas`` (jsonb);
- una fábrica del reductor de GEE;
- el sufijo con que GEE nombra la salida de ese reductor.

El registro guarda **fábricas**, no reductores: un reductor no se puede armar
antes de ``ee.Initialize()``. Con ``earthengine-api`` 1.7.41 los métodos ya
existen al importar (``ee.Reducer.median`` está), pero **llamarlos** levanta
``EEException: Earth Engine client library not initialized``. Así importar el
módulo no necesita GEE ni toca la red (``DECISIONS #24``). La fábrica es el
método mismo, o una ``lambda`` cuando lleva argumentos, como los percentiles.

Sumar una estadística (``p25``, por ejemplo) es una entrada más y subir la versión
de la receta. Va al jsonb, así que no pide migración.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

import ee

from pipeline.indices import INDICES
from pipeline.registro import registro

# Letras y dígitos, sin `_`: ver `claves_de_salida`.
_SUFIJO = re.compile(r"[A-Za-z][A-Za-z0-9]*")


@dataclass(frozen=True, slots=True)
class Estadistica:
    """Una estadística de la parcela en el mes.

    Attributes:
        nombre: la clave en el jsonb (``mediana``, ``p10``...).
        fabrica: arma el reductor de GEE. Se llama después de ``ee.Initialize()``.
        sufijo: el nombre de la salida del reductor en GEE (``median``, ``p10``,
            ``stdDev``). Tiene que ser exacto: la reducción busca la clave
            ``{indice}_{sufijo}`` y falla si no está. Eso también cuida que la
            fábrica y el sufijo digan lo mismo: si alguien cambia
            ``percentile([10])`` por ``percentile([20])`` sin tocar el sufijo, GEE
            devuelve ``p20`` y la clave ``p10`` no aparece.
    """

    nombre: str
    fabrica: Callable[[], ee.Reducer]
    sufijo: str

    def __post_init__(self) -> None:
        """Valida el sufijo al armar el registro, que es al importar."""
        if _SUFIJO.fullmatch(self.sufijo) is None:
            msg = f"{self.nombre}: sufijo inválido (letras y dígitos): {self.sufijo!r}"
            raise ValueError(msg)


# Receta v1 (DECISIONS #31): las siete.
ESTADISTICAS = registro(
    Estadistica("mediana", ee.Reducer.median, sufijo="median"),
    Estadistica("media", ee.Reducer.mean, sufijo="mean"),
    Estadistica("min", ee.Reducer.min, sufijo="min"),
    Estadistica("max", ee.Reducer.max, sufijo="max"),
    Estadistica("p10", lambda: ee.Reducer.percentile([10]), sufijo="p10"),
    Estadistica("p90", lambda: ee.Reducer.percentile([90]), sufijo="p90"),
    Estadistica("desvio", ee.Reducer.stdDev, sufijo="stdDev"),
)


def _validar_nombres(tipo: str, nombres: Sequence[str], validos: Sequence[str]) -> None:
    if not nombres:
        msg = f"hace falta al menos un {tipo}"
        raise ValueError(msg)
    if len(set(nombres)) != len(nombres):
        msg = f"{tipo} repetido: {list(nombres)}"
        raise ValueError(msg)
    desconocidos = [nombre for nombre in nombres if nombre not in validos]
    if desconocidos:
        msg = f"{tipo} fuera del registro: {desconocidos}"
        raise ValueError(msg)


def claves_de_salida(
    indices: Sequence[str], estadisticas: Sequence[str]
) -> dict[tuple[str, str], str]:
    """La clave con que ``reduceRegion`` devuelve cada par (índice, estadística).

    GEE aplica el reductor a cada banda (``forEachBand``) y nombra las salidas así:

    - si el reductor tiene **una** salida, la clave es el nombre de la banda:
      ``ndvi``;
    - si tiene **varias**, como el reductor combinado de la receta, es
      ``{banda}_{sufijo}``: ``ndvi_median``.

    Depende de cuántas estadísticas hay, no de cuántos índices. La receta v1 lleva
    siete, así que en la práctica siempre es ``{banda}_{sufijo}``. El caso de una
    sola banda con varias salidas se confirma contra GEE en M.2.6.

    Las claves no chocan: ni el índice ni el sufijo llevan ``_``, así que el
    sufijo es lo que va después del único ``_``.

    Returns:
        Un ``dict`` de ``(indice, estadistica)`` a la clave, en el orden en que
        se pidieron.

    Raises:
        ValueError: si una lista está vacía, tiene repetidos o nombra algo que
            no está en su registro.
    """
    _validar_nombres("índice", indices, list(INDICES))
    _validar_nombres("estadística", estadisticas, list(ESTADISTICAS))
    una_sola_salida = len(estadisticas) == 1
    return {
        (indice, nombre): (
            indice if una_sola_salida else f"{indice}_{ESTADISTICAS[nombre].sufijo}"
        )
        for indice in indices
        for nombre in estadisticas
    }
