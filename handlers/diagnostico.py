"""Mide cuánto tarda Inngest en mandar cada step, sin hacer nada más (M.4.10).

**Por qué existe.** En producción, cada mes de un alta esperaba de 40 a 60 s en la
cola de Inngest antes de llegar al worker, que después lo resolvía en 5 a 10 s. El
mismo código, contra el Inngest local, no esperaba nada (``DECISIONS #53`` y
``#55``). La pregunta es si la espera es de Inngest Cloud o si algo de nuestros
steps la provoca: el tamaño de lo que devuelven, lo que tardan, o el worker.

Esta función la contesta sin nada nuestro en el medio: **N steps que no hacen nada**,
ni GEE ni base ni bitácora. Cada uno devuelve la hora a la que el worker lo
ejecutó, y al final la función devuelve los huecos entre steps.

- Si los steps vacíos también esperan decenas de segundos, la espera es de la
  plataforma, y queda un caso mínimo para mandarle al soporte de Inngest.
- Si salen seguidos, la espera la provoca algo de los steps reales.

**Se dispara a mano**, desde el dashboard de Inngest: *Send event* con el nombre
``terra/diagnostico.latencia`` y, opcional, ``{"steps": 5}``. No toca ningún
dato ni ningún job, y cuesta N + 1 ejecuciones del plan.
"""

import logging
import time
from itertools import pairwise
from typing import Any, Final

import inngest
from services.inngest_client import inngest_client

from handlers.seguimiento import RETRIES

logger = logging.getLogger(__name__)

EVENTO: Final = "terra/diagnostico.latencia"
STEPS_POR_DEFECTO: Final = 5
# Tope para que un evento mal escrito no gaste la cuota del plan: cada step es una
# ejecución, y el plan Hobby tiene 50.000 al mes.
STEPS_MAXIMOS: Final = 10


def cuantos_steps(datos: object) -> int:
    """Cuántos steps pidió el evento, entre 1 y :data:`STEPS_MAXIMOS`."""
    pedido = datos.get("steps") if isinstance(datos, dict) else None
    if not isinstance(pedido, int) or isinstance(pedido, bool):
        return STEPS_POR_DEFECTO
    return max(1, min(pedido, STEPS_MAXIMOS))


def huecos(horas: list[float]) -> list[float]:
    """Los segundos entre la ejecución de un step y la del siguiente."""
    return [round(b - a, 3) for a, b in pairwise(horas)]


@inngest_client.create_function(
    fn_id="diagnostico-latencia",
    trigger=inngest.TriggerEvent(event=EVENTO),
    # El mismo número que las demás funciones: lo pide
    # `test_el_numero_de_reintentos_es_uno_solo`. Un step vacío no falla.
    retries=RETRIES,
)
def diagnostico_latencia(
    ctx: inngest.Context, step: inngest.StepSync
) -> dict[str, Any]:
    """N steps vacíos; devuelve cuándo corrió cada uno y los huecos entre ellos."""
    n = cuantos_steps(ctx.event.data)
    horas = []
    for i in range(1, n + 1):
        # La hora se toma **dentro** del step: es cuando el worker lo ejecutó, y
        # queda memoizada. Afuera, se recalcularía en cada replay.
        hora = step.run(f"vacio-{i}", time.time)
        horas.append(hora)
    resultado = {
        "steps": n,
        "huecos_s": huecos(horas),
        "total_s": round(horas[-1] - horas[0], 3) if n > 1 else 0.0,
    }
    logger.info("Diagnostico de latencia (run %s): %s", ctx.run_id, resultado)
    return resultado
