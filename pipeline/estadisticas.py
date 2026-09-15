"""Registro de estadísticas y el plan de su reducción (M.1.3 y M.1.6).

Cada estadística es una entrada de :data:`ESTADISTICAS`: el nombre con que se
guarda en ``measurements.estadisticas`` (jsonb), su tipo y, si es un percentil,
cuál. El registro **describe** la estadística; no guarda un reductor de GEE
(``DECISIONS #36``):

- el reductor lo arma la etapa de reducción (M.2.4), después de
  ``ee.Initialize()``, siguiendo :func:`plan_de_reduccion`. Así este módulo no
  importa ``ee``;
- la huella de la receta ve la definición completa de cada estadística. Con una
  fábrica (``lambda: ee.Reducer.percentile([10])``) solo veía el nombre.

:func:`plan_de_reduccion` junta lo que GEE calcula de una vez. Mediana, p10 y p90
son **un** ``ee.Reducer.percentile([10, 50, 90])``: como reductores separados,
cada uno arma su propio histograma de los píxeles, según la documentación de
``ee.Reducer.median`` y de ``ee.Reducer.percentile``. Mín y máx son un
``ee.Reducer.minMax()``.

Sumar una estadística (``p25``, por ejemplo) es una entrada más y otra versión de
la receta. Va al jsonb, así que no pide migración.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from pipeline.indices import INDICES
from pipeline.registro import registro

_PERCENTIL_MAXIMO = 100


class Tipo(StrEnum):
    """Qué calcula una estadística. El valor entra en la huella de la receta."""

    PERCENTIL = "percentil"
    MEDIA = "media"
    DESVIO = "desvio"
    MINIMO = "min"
    MAXIMO = "max"


# El nombre de la salida de cada tipo en GEE, que para estos cuatro es también el
# nombre del método de `ee.Reducer` (`ee.Reducer.mean()` sale como `mean`). Los
# percentiles se nombran `p{n}`: el plan le pasa esos nombres a
# `ee.Reducer.percentile` como `outputNames`, así que los pone el pipeline.
_SALIDA = MappingProxyType(
    {
        Tipo.MEDIA: "mean",
        Tipo.DESVIO: "stdDev",
        Tipo.MINIMO: "min",
        Tipo.MAXIMO: "max",
    }
)


@dataclass(frozen=True, slots=True)
class Estadistica:
    """Una estadística de la parcela en el mes.

    Attributes:
        nombre: la clave en el jsonb (``mediana``, ``p10``...).
        tipo: qué calcula.
        percentil: de 0 a 100, solo si ``tipo`` es ``Tipo.PERCENTIL``.
    """

    nombre: str
    tipo: Tipo
    percentil: int | None = None

    def __post_init__(self) -> None:
        """Valida el tipo, y que el percentil vaya solo con un percentil."""
        if not isinstance(self.tipo, Tipo):
            msg = f"{self.nombre}: el tipo es un Tipo, no {self.tipo!r}"
            raise TypeError(msg)
        if self.tipo is Tipo.PERCENTIL:
            # `bool` es un `int` para Python: `True` pasaría por el percentil 1.
            valido = (
                isinstance(self.percentil, int)
                and not isinstance(self.percentil, bool)
                and 0 <= self.percentil <= _PERCENTIL_MAXIMO
            )
            if not valido:
                msg = f"{self.nombre}: percentil entero de 0 a 100: {self.percentil!r}"
                raise ValueError(msg)
        elif self.percentil is not None:
            msg = f"{self.nombre}: solo un percentil lleva percentil ({self.tipo})"
            raise ValueError(msg)

    @property
    def sufijo(self) -> str:
        """El nombre de la salida en GEE: el final de la clave ``{indice}_{sufijo}``.

        Sale del tipo, no se escribe a mano, así que no puede contradecir al
        reductor. Nunca lleva ``_``: ver :func:`claves_de_salida`.
        """
        if self.tipo is Tipo.PERCENTIL:
            return f"p{self.percentil}"
        return _SALIDA[self.tipo]


@dataclass(frozen=True, slots=True)
class Reductor:
    """Un reductor de GEE, descripto sin armarlo. Lo arma M.2.4.

    Attributes:
        metodo: el método de ``ee.Reducer``: ``percentile``, ``minMax``,
            ``min``, ``max``, ``mean`` o ``stdDev``.
        salidas: los nombres de sus salidas, en el orden en que las da.
        percentiles: solo para ``percentile``. Van con ``outputNames=salidas``.
    """

    metodo: str
    salidas: tuple[str, ...]
    percentiles: tuple[int, ...] = ()


def _estadisticas_pedidas(nombres: Sequence[str]) -> tuple[Estadistica, ...]:
    """Valida una lista de nombres contra el registro y devuelve sus entradas."""
    if not nombres:
        msg = "hace falta al menos una estadística"
        raise ValueError(msg)
    if len(set(nombres)) != len(nombres):
        msg = f"estadística repetida: {list(nombres)}"
        raise ValueError(msg)
    desconocidas = [nombre for nombre in nombres if nombre not in ESTADISTICAS]
    if desconocidas:
        msg = f"estadística fuera del registro: {desconocidas}"
        raise ValueError(msg)
    pedidas = tuple(ESTADISTICAS[nombre] for nombre in nombres)
    # Dos estadísticas con la misma salida (una "mediana" y una "p50") le darían a
    # GEE dos salidas con el mismo nombre, y la misma clave a las dos.
    sufijos = [estadistica.sufijo for estadistica in pedidas]
    if len(set(sufijos)) != len(sufijos):
        msg = f"dos estadísticas calculan lo mismo: {sufijos}"
        raise ValueError(msg)
    return pedidas


def plan_de_reduccion(estadisticas: Sequence[str]) -> tuple[Reductor, ...]:
    """Los reductores de GEE que calculan estas estadísticas, fusionados.

    - Todos los percentiles van en **un** ``percentile``, ordenados: un solo
      histograma en vez de uno por percentil.
    - Mín y máx juntos van en un ``minMax``; si se pide uno solo, va solo.
    - Media y desvío van cada uno en su reductor. Son acumulados, no
      histogramas, y no hay nada que fusionar.

    M.2.4 los combina con ``sharedInputs=True``. Las salidas del plan son
    exactamente los sufijos de lo pedido, así que :func:`claves_de_salida` vale
    igual.

    Raises:
        ValueError: si la lista está vacía, tiene repetidos, nombra algo que no
            está en el registro o pide dos veces la misma salida.
    """
    pedidas = _estadisticas_pedidas(estadisticas)
    por_tipo = {tipo: [e for e in pedidas if e.tipo is tipo] for tipo in Tipo}
    plan: list[Reductor] = []

    percentiles = sorted(por_tipo[Tipo.PERCENTIL], key=lambda e: e.percentil or 0)
    if percentiles:
        plan.append(
            Reductor(
                "percentile",
                salidas=tuple(e.sufijo for e in percentiles),
                percentiles=tuple(e.percentil or 0 for e in percentiles),
            )
        )
    if por_tipo[Tipo.MINIMO] and por_tipo[Tipo.MAXIMO]:
        plan.append(Reductor("minMax", salidas=("min", "max")))
        sueltos = (Tipo.MEDIA, Tipo.DESVIO)
    else:
        sueltos = (Tipo.MINIMO, Tipo.MAXIMO, Tipo.MEDIA, Tipo.DESVIO)
    plan.extend(
        Reductor(_SALIDA[tipo], salidas=(_SALIDA[tipo],))
        for tipo in sueltos
        if por_tipo[tipo]
    )
    return tuple(plan)


def claves_de_salida(
    indices: Sequence[str], estadisticas: Sequence[str]
) -> dict[tuple[str, str], str]:
    """La clave con que ``reduceRegion`` devuelve cada par (índice, estadística).

    GEE aplica el reductor a cada banda (``forEachBand``) y nombra las salidas así:

    - si el reductor tiene **una** salida, la clave es el nombre de la banda:
      ``ndvi``;
    - si tiene **varias**, como el combinado de la receta, es
      ``{banda}_{sufijo}``: ``ndvi_p50``.

    Depende de cuántas estadísticas hay, no de cuántos índices: cada estadística
    es exactamente una salida del plan. La receta v1 lleva siete, así que en la
    práctica siempre es ``{banda}_{sufijo}``. El caso de una sola banda con
    varias salidas se confirma contra GEE en M.2.6.

    Las claves no chocan: ni el índice ni el sufijo llevan ``_``, así que el
    sufijo es lo que va después del único ``_``.

    Returns:
        Un ``dict`` de ``(indice, estadistica)`` a la clave, en el orden en que
        se pidieron.

    Raises:
        ValueError: si una lista está vacía, tiene repetidos o nombra algo que
            no está en su registro.
    """
    if not indices:
        msg = "hace falta al menos un índice"
        raise ValueError(msg)
    if len(set(indices)) != len(indices):
        msg = f"índice repetido: {list(indices)}"
        raise ValueError(msg)
    desconocidos = [indice for indice in indices if indice not in INDICES]
    if desconocidos:
        msg = f"índice fuera del registro: {desconocidos}"
        raise ValueError(msg)
    pedidas = _estadisticas_pedidas(estadisticas)
    una_sola_salida = len(pedidas) == 1
    return {
        (indice, estadistica.nombre): (
            indice if una_sola_salida else f"{indice}_{estadistica.sufijo}"
        )
        for indice in indices
        for estadistica in pedidas
    }


# Receta v1 (DECISIONS #31): las siete. La mediana es el percentil 50, para que
# comparta el histograma con p10 y p90.
ESTADISTICAS = registro(
    Estadistica("mediana", Tipo.PERCENTIL, percentil=50),
    Estadistica("media", Tipo.MEDIA),
    Estadistica("min", Tipo.MINIMO),
    Estadistica("max", Tipo.MAXIMO),
    Estadistica("p10", Tipo.PERCENTIL, percentil=10),
    Estadistica("p90", Tipo.PERCENTIL, percentil=90),
    Estadistica("desvio", Tipo.DESVIO),
)

# El registro entero tiene que poder pedirse junto: sin dos entradas con la
# misma salida. Falla al importar, no a mitad de un alta.
_estadisticas_pedidas(tuple(ESTADISTICAS))
