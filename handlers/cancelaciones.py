"""Las altas canceladas en Inngest cierran su job (M.4.7, ``DECISIONS #52``).

``on_failure`` recibe ``inngest/function.failed``, no las cancelaciones: Inngest
publica ``inngest/function.cancelled`` aparte. Esta función lo escucha para las
dos altas, filtrada con una expresión sobre ``function_id``, y cierra el job con
:func:`handlers.cierre.cerrar_por_cancelacion`.

Está en su propio módulo porque necesita los ids de las dos altas, y
``parcela.py`` y ``rancho.py`` importan ``cierre.py``: ponerla ahí armaría un
ciclo.
"""

from typing import Final

import inngest
from services.inngest_client import inngest_client

from handlers.cierre import cerrar_por_cancelacion
from handlers.parcela import process_parcela
from handlers.rancho import process_rancho
from handlers.seguimiento import RETRIES

EVENTO_CANCELADA: Final = "inngest/function.cancelled"

# Los ids completos (`geeworker-process-parcela`), que es lo que Inngest pone en
# `function_id`. Salen de las funciones, no se escriben a mano: si un `fn_id`
# cambia, la expresión cambia con él.
ALTAS: Final = (process_parcela.id, process_rancho.id)


def expresion_de_cancelacion(ids: tuple[str, ...]) -> str:
    """La expresión CEL que deja pasar solo las cancelaciones de ``ids``."""
    return " || ".join(f"event.data.function_id == '{fn_id}'" for fn_id in ids)


@inngest_client.create_function(
    fn_id="cerrar-altas-canceladas",
    trigger=inngest.TriggerEvent(
        event=EVENTO_CANCELADA, expression=expresion_de_cancelacion(ALTAS)
    ),
    # El mismo número que las demás: el cierre es idempotente, así que reintentar
    # no duplica nada, y `test_el_numero_de_reintentos_es_uno_solo` lo pide.
    retries=RETRIES,
)
def cerrar_altas_canceladas(ctx: inngest.Context, step: inngest.StepSync) -> dict:
    """Cierra el job de un alta que se canceló en Inngest."""
    return cerrar_por_cancelacion(ctx, step)
