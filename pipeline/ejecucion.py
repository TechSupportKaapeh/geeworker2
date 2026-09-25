"""El borde con GEE: el único lugar que pide un cálculo (M.2.5).

``ARQUITECTURA_PIPELINE.md`` §3.3. Las etapas arman expresiones; acá se las
manda a calcular. Lo fija ``tests/test_pipeline_borde.py``: fuera de este módulo,
nada en ``pipeline/`` llama a ``getInfo`` ni a ``getDownloadURL``.

Concentra tres cosas:

1. **El tiempo máximo de cada pedido.** ``ee.data.setDeadline`` es estado global
   del cliente, así que :func:`plazo` lo pone y lo deja como estaba. Sin plazo, un
   pedido pesado puede colgar el hilo hasta que alguien reinicie el proceso, y el
   worker corre los handlers en un pool de hilos (``DECISIONS #26``).
2. **La traducción de errores.** Inngest reintenta lo que levanta. Un
   "sin memoria" reintentado tres veces da tres veces el mismo error y gasta
   cuota; un "demasiados pedidos concurrentes" se resuelve solo. Por eso
   :class:`ErrorDeGEE` dice si conviene reintentar, y el handler lo traduce a lo
   que Inngest entiende. **Este paquete no importa Inngest**: el pipeline no sabe
   quién lo orquesta.
3. **El conteo de llamadas**, para la bitácora: cuántas veces se le habló a GEE
   en un step. Va en un ``ContextVar``, como el job actual de
   ``services/avance_job.py``, así que dos handlers en paralelo no se pisan.
"""

import contextlib
import logging
import threading
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Final

import ee

from pipeline.etapas import compuesto as etapa_compuesto
from pipeline.etapas import fuente
from pipeline.etapas.reduccion import Reduccion, leer
from pipeline.productos import estadisticas_de
from pipeline.receta import Receta
from pipeline.ventanas import Ventana, agrupamiento

logger = logging.getLogger(__name__)

# Dos minutos por pedido. El mes de una parcela tarda segundos; la compuerta antes
# de M.4 pide menos de 60 s. El plazo está para que un pedido patológico falle,
# no para recortar uno normal.
PLAZO_MS: Final = 120_000

# `setDeadline(0)` es "sin plazo", que es el default del cliente.
_SIN_PLAZO: Final = 0

# GEE devuelve `system:time_start` en milisegundos desde la época.
_MS_POR_SEGUNDO: Final = 1000

# Lo que GEE contesta cuando el pedido no se puede hacer **así**: reintentarlo da
# exactamente el mismo error, y encima gasta cuota.
_DEFINITIVOS: Final = (
    "user memory limit exceeded",
    "too many pixels",
    "exceeded max pixels",
    "output of image computation is too large",
    # El tope de `getDownloadURL` (unos 48 MB por pedido): un rancho más grande
    # no entra nunca, se reintente lo que se reintente (M.4.5, `DECISIONS #51`).
    "total request size",
    "must be a descendant",
    "parameter 'element' is required",
    # La grilla de salida no puede pasar de 32768 px de lado. Lo levanta una
    # geometría desproporcionada —el 2026-09-20, en producción, un rancho de
    # 13 x 111332 px, o sea 130 m de ancho por 1113 km de largo— y la misma
    # geometría da el mismo error siempre: reintentarlo cuatro veces gastó
    # 144 s de GEE en el primer intento y no cambió nada.
    "pixel grid dimensions",
)

# Lo que se resuelve solo: capacidad, concurrencia o un corte de red.
_PASAJEROS: Final = (
    "too many concurrent aggregations",
    "earth engine capacity exceeded",
    "computation timed out",
    "deadline exceeded",
    "backend error",
    "internal error",
    "service unavailable",
    "connection reset",
    "connection aborted",
)


class ErrorDeGEE(RuntimeError):
    """Un pedido a GEE que falló, con la pista de si conviene reintentarlo.

    Attributes:
        reintentable: si el mismo pedido puede salir bien más tarde.
    """

    def __init__(self, mensaje: str, *, reintentable: bool) -> None:
        """Guarda el mensaje de GEE y si conviene reintentar el pedido."""
        super().__init__(mensaje)
        self.reintentable = reintentable


@dataclass(slots=True)
class Conteo:
    """Cuántos pedidos se le hicieron a GEE dentro de un :func:`contando`."""

    llamadas: int = field(default=0)


_conteo: ContextVar[Conteo | None] = ContextVar("conteo_de_llamadas", default=None)


class _PlazoCompartido:
    """El plazo de GEE, que es del cliente y no del pedido (M.4.8).

    Como es estado de todo el proceso, con varios hilos no alcanza con poner y
    restaurar: se cuenta cuántos lo están usando, el primero lo pone y el último
    lo deja como estaba. No va en un ``ContextVar``, que es justo lo contrario:
    uno por contexto.
    """

    def __init__(self) -> None:
        """Arranca sin hilos adentro."""
        self._candado = threading.Lock()
        self._hilos = 0
        self._anterior: int | None = None

    def entrar(self, milisegundos: int) -> None:
        """Pone el plazo si es el primer hilo que entra."""
        with self._candado:
            if self._hilos == 0:
                self._anterior = ee.data._get_state().deadline_ms  # noqa: SLF001 - el cliente no lo expone
                ee.data.setDeadline(milisegundos)
            self._hilos += 1

    def salir(self) -> None:
        """Restaura el plazo si es el último hilo que sale."""
        with self._candado:
            self._hilos -= 1
            if self._hilos == 0:
                ee.data.setDeadline(self._anterior or _SIN_PLAZO)


_plazo_compartido = _PlazoCompartido()


def es_reintentable(error: BaseException) -> bool:
    """Si conviene volver a intentar el pedido que levantó este error.

    Se decide por el texto, que es lo único que GEE da: la API no trae un código.
    Lo desconocido se trata como reintentable, porque las familias que no se
    recuperan están enumeradas y equivocarse hacia el reintento cuesta una
    llamada, mientras que equivocarse hacia el descarte pierde el mes.
    """
    # `_PASAJEROS` no entra en la cuenta, porque lo desconocido también se
    # reintenta: preguntarle sería una rama que siempre da lo mismo. Está para
    # dejar escrito qué familias se sabe que se recuperan, y un test la recorre.
    texto = str(error).lower()
    return not any(frase in texto for frase in _DEFINITIVOS)


def traducir(error: BaseException) -> ErrorDeGEE:
    """El error de GEE, envuelto con la pista de reintento."""
    return ErrorDeGEE(str(error), reintentable=es_reintentable(error))


def _hay_cliente() -> bool:
    """Si ``ee.Initialize()`` ya corrió en este proceso.

    ``setDeadline`` no guarda un número y se va: reconstruye el cliente HTTP, y
    sin sesión revienta con un ``AssertionError`` del propio ``ee``
    (``ee/data.py``, ``_install_cloud_api_resource``). Verificado el 2026-09-16.
    """
    return ee.data._get_state().requests_session is not None  # noqa: SLF001 - el cliente no lo expone


@contextlib.contextmanager
def plazo(milisegundos: int = PLAZO_MS) -> Iterator[None]:
    """Pone el tiempo máximo de los pedidos y lo deja como estaba al salir.

    ``ee.data.setDeadline`` es estado global del cliente de ``ee``, no un
    parámetro del pedido: sin restaurarlo, el plazo de un handler se le aplicaría
    a todo lo que venga después en ese proceso.

    **Sin cliente no hace nada.** Si nadie inicializó GEE no hay pedido que
    limitar, y el ``getInfo()`` que venga después va a fallar por su cuenta con
    un error que dice justamente eso. Poner el plazo ahí solo cambiaría ese error
    por un ``AssertionError`` del cliente, que no explica nada.

    **Con varios hilos, el primero lo pone y el último lo restaura** (M.4.8).
    Desde que el worker atiende steps en paralelo, dos hilos entran acá a la vez,
    y sin contarlos el que salía primero le sacaba el plazo al que seguía
    trabajando. Peor todavía: ``setDeadline`` **reconstruye el cliente HTTP**, así
    que llamarlo mientras otro hilo tiene un pedido en vuelo es cambiarle el
    piso. El contador evita las dos cosas, porque todos piden el mismo plazo.
    """
    if not _hay_cliente():
        yield
        return
    _plazo_compartido.entrar(milisegundos)
    try:
        yield
    finally:
        _plazo_compartido.salir()


@contextlib.contextmanager
def contando() -> Iterator[Conteo]:
    """Cuenta los pedidos a GEE que se hagan adentro.

    Lo usa el handler para dejar en la bitácora cuántas llamadas costó un step.
    """
    conteo = Conteo()
    testigo = _conteo.set(conteo)
    try:
        yield conteo
    finally:
        _conteo.reset(testigo)


def traer(expresion: Any, *, milisegundos: int = PLAZO_MS) -> Any:  # noqa: ANN401 - cualquier expresión de ee
    """Calcula una expresión de GEE y devuelve el resultado.

    Es **el** punto donde el pipeline deja de armar y empieza a pedir.

    Raises:
        ErrorDeGEE: siempre que el pedido falle, con ``reintentable`` puesto.
    """
    conteo = _conteo.get()
    if conteo is not None:
        conteo.llamadas += 1
    with plazo(milisegundos):
        try:
            return expresion.getInfo()
        except Exception as error:
            traducido = traducir(error)
            logger.warning(
                "Pedido a GEE fallido (reintentable=%s): %s",
                traducido.reintentable,
                error,
            )
            raise traducido from error


def url_de_descarga(
    imagen: ee.Image, parametros: dict[str, Any], *, milisegundos: int = PLAZO_MS
) -> str:
    """La URL de descarga de una imagen, con la misma política que :func:`traer`.

    Va acá y no en el servicio de descarga porque también es pedirle a GEE que
    calcule: el mismo plazo, la misma traducción de errores y el mismo conteo.
    """
    conteo = _conteo.get()
    if conteo is not None:
        conteo.llamadas += 1
    with plazo(milisegundos):
        try:
            return imagen.getDownloadURL(parametros)
        except Exception as error:
            traducido = traducir(error)
            logger.warning(
                "URL de descarga fallida (reintentable=%s): %s",
                traducido.reintentable,
                error,
            )
            raise traducido from error


def reduccion_de(roi: ee.Geometry, ventana: Ventana, receta: Receta) -> Reduccion:
    """Los números de una parcela en una ventana, pedidos y validados.

    Es una sola llamada a GEE: :func:`estadisticas_de` arma todo junto.
    """
    return leer(traer(estadisticas_de(roi, ventana, receta)), receta)


def fechas_de(
    roi: ee.Geometry, pedido: Ventana, receta: Receta
) -> tuple[datetime, ...]:
    """Los instantes de las pasadas que hay en el pedido, una por pasada.

    **Es la llamada que ``DECISIONS #63`` le suma al borde**, y la razón por la
    que existe está en ``ARQUITECTURA_PIPELINE.md`` §3.5: ``entero`` sale del
    calendario, pero ``por_pasada`` **depende de qué pasadas existen**, y eso sólo
    lo sabe GEE. Las dos salidas eran una descripción perezosa que resolviera
    :func:`traer`, o una llamada más declarada acá. Se eligió la segunda: ``#32``
    dice «un solo borde», no «una sola llamada», y una llamada con nombre se
    sigue mejor que una indirección que existe sólo para no agregarla.

    Una por **pasada** y no una por escena: las teselas se juntan antes con
    :func:`pipeline.etapas.compuesto.por_pasada`, que agrupa por
    ``DATATAKE_IDENTIFIER``. Sin eso, un ROI en la franja de solape daría dos
    fechas casi iguales para la misma toma y con ellas dos filas de la misma
    observación (sondeo del 2026-09-15: 16 imágenes que eran 8 pasadas).

    **No se enmascara para contar.** Una pasada enteramente nublada sigue siendo
    una pasada, y su ventana produce una fila con cobertura baja; descartarla acá
    sería volver a descartar al escribir, que es justo lo que ``#63`` saca.

    Returns:
        Los instantes en UTC, ordenados y sin repetidos. Una ventana sin pasadas
        devuelve la tupla vacía, que no es un error.
    """
    escenas = fuente.coleccion(roi, pedido, receta)
    pasadas = etapa_compuesto.por_pasada(escenas)
    crudas = traer(pasadas.aggregate_array("system:time_start"))
    return tuple(
        sorted({datetime.fromtimestamp(ms / _MS_POR_SEGUNDO, tz=UTC) for ms in crudas})
    )


def ventanas_de(
    roi: ee.Geometry, pedido: Ventana, receta: Receta, *, para_raster: bool = False
) -> tuple[Ventana, ...]:
    """En qué observaciones se parte el pedido, según la receta.

    ``para_raster`` elige cuál de los dos agrupamientos de la receta se aplica.
    Son dos y no uno porque el ráster y los números tienen costos distintos
    (``ARQUITECTURA`` §3.5): la receta puede pedir ``por_pasada`` para las filas y
    ``entero`` para el COG, que es lo que ``#63`` deja previsto para ``v2``.

    **Cuesta una llamada a GEE sólo si el agrupamiento la necesita.** Con
    ``entero``, que es lo de hoy, no se le pregunta nada: el pedido es la única
    ventana. Por eso M.9.0b no cambia cuántas llamadas hace un mes.
    """
    nombre = (
        receta.agrupamiento_raster if para_raster else receta.agrupamiento_estadisticas
    )
    modo = agrupamiento(nombre)
    fechas = fechas_de(roi, pedido, receta) if modo.necesita_fechas else ()
    return modo.partir(pedido, fechas)
