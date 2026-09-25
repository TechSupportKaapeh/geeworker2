"""El agrupamiento: la ventana de observación como dato, no como supuesto (M.9.0b).

``ARQUITECTURA_PIPELINE.md`` §3.5 y ``DECISIONS #63``. Hasta acá "el mes" vivía
repartido en cinco lugares —:mod:`pipeline.periodos`, el ``median()`` de
:func:`pipeline.etapas.compuesto.compuesto`, la ``fecha`` de
:mod:`pipeline.filas`, el ``{AAAA-MM}`` de :mod:`pipeline.claves` y el ``periodo``
de los jobs— y cambiar la cadencia era tocar los cinco esperando no olvidarse de
ninguno. Es la misma forma de problema que M.7.1 y que ``DECISIONS #44`` de
Geocore: **una regla repartida en instancias**.

Acá hay una sola abstracción, que es la que el compuesto ya era sin decirlo: **de
un pedido a una lista de ventanas**. Cada :class:`Ventana` es una observación, y
el resto del pipeline ya no sabe qué es un mes: sabe que le dan ventanas.

**Lo que NO cambia** (``DECISIONS #63``):

- **el pedido sigue siendo un mes.** Los jobs y el cierre de mes no se tocan: un
  job sigue diciendo "procesá agosto de esta parcela". Lo que cambia es **cuántas
  filas escribe**;
- **el ráster sigue siendo mensual**, por los motivos de ``#31``.

**El agrupamiento es por producto, no por receta.** El ráster y las estadísticas
tienen costos distintos —el ráster son descargas y COG, los números son un
``reduceRegion``— y ``#31`` eligió mensual con la cuenta del ráster. Por eso la
receta lleva **dos** campos y no uno, y puede decir ``estadisticas: por_pasada``
con ``raster: entero`` al mismo tiempo.

Partir es puro
--------------
:attr:`Agrupamiento.partir` es una función de Python sobre datos: recibe el
pedido y las fechas de las pasadas, y devuelve ventanas. **No toca GEE**, así que
se prueba sin credenciales y el borde de ``#32`` queda intacto.

Lo que sí necesita GEE es *saber cuáles son esas fechas*, y sólo para los
agrupamientos que dependen de qué pasadas existen. Eso lo resuelve
:func:`pipeline.ejecucion.fechas_de`, la llamada declarada que ``#63`` le suma al
borde: quien orquesta pregunta las fechas **si** :attr:`Agrupamiento.necesita_fechas`,
y se las pasa a ``partir``. Era la primera decisión que M.9.0b tenía que tomar
—descripción perezosa contra una llamada más—, y se eligió la segunda porque
``#32`` dice «un solo borde», no «una sola llamada».

``entero`` y ``rango_libre`` son la misma función
-------------------------------------------------
El diseño de §3.5 listaba cuatro agrupamientos, y dos de ellos —``mensual`` y
``rango_libre``— resultaron ser **el mismo**: "una imagen con todo el pedido
adentro". Lo que los distinguía era el pedido, no el agrupamiento. Por eso acá
hay uno solo, :data:`ENTERO`, y sobre un pedido mensual produce exactamente lo de
hoy. Que la abstracción colapse dos casos en uno es señal de que está en el lugar
correcto.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final, NamedTuple

from pipeline.periodos import Mes, rango

# Los agrupamientos por nombre. El nombre es lo que va en la receta y, por la
# receta, en la huella.
ENTERO: Final = "entero"
POR_PASADA: Final = "por_pasada"

# Lo que se le suma al último instante de una pasada para cerrar su ventana.
# El rango de `filterDate` es semiabierto, así que sin esto la última tesela de
# la pasada quedaría afuera. Un segundo alcanza y sobra: dos pasadas del mismo
# punto están a días, no a segundos.
_CIERRE: Final = timedelta(seconds=1)


class Ventana(NamedTuple):
    """Una unidad de observación: el intervalo ``[inicio, fin)`` y cómo se llama.

    Attributes:
        etiqueta: el nombre con el que la ventana viaja fuera del pipeline — la
            ``fecha`` de la fila no, pero sí el ``{AAAA-MM}`` de la key del COG y
            el ``periodo`` del job. **Es la única pieza del agrupamiento que
            llega a todos lados**: con ella, los otros cuatro lugares dejan de
            saber qué es un mes.
        inicio: el primer instante, incluido. Es también la ``fecha`` de la fila
            (``ARQUITECTURA_PIPELINE.md`` §6).
        fin: el primer instante de afuera, **excluido**. Es la convención de
            ``filterDate`` y la de :func:`pipeline.periodos.rango`; un rango
            cerrado pierde el último día o lo cuenta dos veces.

    Es un ``NamedTuple`` y no un dataclass por lo mismo que
    :class:`pipeline.claves.ClavesDeCapa`: es un valor chico, inmutable, que se
    arma y se lee, y no tiene invariantes propias que validar.
    """

    etiqueta: str
    inicio: datetime
    fin: datetime


def del_mes(mes: Mes) -> Ventana:
    """La ventana de un mes calendario: el pedido que hace hoy todo el sistema.

    Es el puente entre :class:`~pipeline.periodos.Mes` —que sigue siendo la
    unidad del job y de la bitácora— y la ventana, que es la unidad del pipeline.
    La etiqueta es ``AAAA-MM``, así que la key del COG y el ``periodo`` del job
    siguen escribiéndose exactamente igual que antes.
    """
    inicio, fin = rango(mes)
    return Ventana(etiqueta=str(mes), inicio=inicio, fin=fin)


def _entero(pedido: Ventana, fechas: Sequence[datetime]) -> tuple[Ventana, ...]:
    """Una sola ventana: el pedido tal cual.

    Es lo de hoy. ``fechas`` no se mira: no hace falta saber qué pasadas hay para
    decir "todas juntas", y por eso :attr:`Agrupamiento.necesita_fechas` es falso
    y no se le pide nada a GEE.
    """
    del fechas
    return (pedido,)


def _por_pasada(pedido: Ventana, fechas: Sequence[datetime]) -> tuple[Ventana, ...]:
    """Una ventana por pasada, cada una cerrada sobre su propio instante.

    ``fechas`` son los instantes de las pasadas del pedido, ya agrupadas por
    ``DATATAKE_IDENTIFIER`` —o sea, una por pasada y no una por tesela—, que es
    lo que devuelve :func:`pipeline.ejecucion.fechas_de`.

    La ventana de una pasada es ``[instante, instante + 1 s)``. El segundo está
    sólo para cerrar el rango semiabierto: el instante ya es el de la pasada
    entera, porque :func:`pipeline.etapas.compuesto.por_pasada` junta sus teselas
    antes.

    .. warning::

       **Esta ventana todavía no sirve para seleccionar sus escenas**, y por eso
       ninguna receta usa este agrupamiento. Lo encontró M.9.0b contra GEE real:
       ``S2_SR`` y ``S2_CLOUD_PROBABILITY`` comparten el ``system:index`` —que es
       por donde las une :func:`pipeline.etapas.fuente.coleccion`— pero **no el
       ``system:time_start``**. El de SR va después, y por minutos: de 129 a 260 s
       sobre una parcela de los Llanos, y de 668 a 1169 s sobre el cuadrado del
       Bajío. Depende de dónde caiga el ROI en la pasada, así que **no hay un
       margen chico que sirva para todos**.

       Con una ventana de un segundo alrededor del instante de SR, la imagen de
       nubes queda fuera del ``filterDate``, el join no encuentra par y la
       colección sale vacía.

       **Lo que M.9.0c tiene que hacer**: filtrar la colección de nubes por un
       superconjunto del pedido y dejar que el join por ``system:index`` —que es
       exacto— haga el resto. El filtro de fecha sobre las nubes es una
       optimización, no un criterio. Eso **cambia el borde del mes** —una escena
       de los primeros minutos tiene su imagen de nubes en el mes anterior, y hoy
       se descarta—, así que es un cambio de números y no podía entrar en M.9.0b.
       Está fijado en ``test_las_dos_colecciones_fechan_la_misma_escena_con_
       minutos_de_diferencia``.

    Las fechas se ordenan y se sacan los repetidos: dos teselas de la misma
    pasada darían dos ventanas idénticas, y con ellas dos filas iguales para la
    misma observación.

    Raises:
        ValueError: si una fecha cae fuera del pedido. Sería una pasada que el
            filtro no debería haber dejado pasar, y una ventana afuera escribiría
            una fila con la fecha de otro período.
    """
    afuera = [f for f in fechas if not pedido.inicio <= f < pedido.fin]
    if afuera:
        msg = (
            f"{len(afuera)} pasada(s) fuera del pedido {pedido.etiqueta} "
            f"[{pedido.inicio}, {pedido.fin}): {sorted(afuera)[:3]}"
        )
        raise ValueError(msg)
    return tuple(
        Ventana(etiqueta=etiqueta_de_instante(f), inicio=f, fin=f + _CIERRE)
        for f in sorted(set(fechas))
    )


def etiqueta_de_instante(instante: datetime) -> str:
    """``AAAA-MM-DDTHH:MMZ``: la etiqueta de una pasada.

    Minutos y no segundos: alcanza para distinguir dos pasadas —que están a
    días— y no promete una precisión que el instante de la pasada no tiene, que
    sale de la primera de sus teselas.
    """
    return instante.astimezone(UTC).strftime("%Y-%m-%dT%H:%MZ")


@dataclass(frozen=True, slots=True)
class Agrupamiento:
    """Cómo se parte un pedido en observaciones.

    Attributes:
        nombre: el que va en la receta, y por ella en la huella.
        necesita_fechas: si ``partir`` mira las fechas de las pasadas. Cuando es
            falso, **nadie le pregunta nada a GEE** para armar las ventanas: es
            lo que hace que el agrupamiento de hoy no cueste una llamada más.
        partir: del pedido y las fechas, a las ventanas. Puro.
    """

    nombre: str
    necesita_fechas: bool
    partir: Callable[[Ventana, Sequence[datetime]], tuple[Ventana, ...]]


AGRUPAMIENTOS: Mapping[str, Agrupamiento] = MappingProxyType(
    {
        ENTERO: Agrupamiento(nombre=ENTERO, necesita_fechas=False, partir=_entero),
        POR_PASADA: Agrupamiento(
            nombre=POR_PASADA, necesita_fechas=True, partir=_por_pasada
        ),
    }
)


def agrupamiento(nombre: str) -> Agrupamiento:
    """El agrupamiento que se llama así.

    Raises:
        ValueError: si no existe. La receta lo valida al armarse, que es al
            importar, así que un nombre mal escrito rompe el arranque y no un mes
            suelto en producción.
    """
    if nombre not in AGRUPAMIENTOS:
        msg = f"agrupamiento desconocido: {nombre!r} ({sorted(AGRUPAMIENTOS)})"
        raise ValueError(msg)
    return AGRUPAMIENTOS[nombre]
