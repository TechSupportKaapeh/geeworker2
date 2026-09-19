"""El cierre de mes: un mes de una parcela o de un rancho (M.5.3).

El reconciliador de Geocore publica ``terra/parcela.mes.requested`` y
``terra/rancho.mes.requested``, uno por entidad activa, cuando se cierra un mes
(``DECISIONS #23`` y ``#30`` de Geocore). Acá se procesa **ese** mes.

Es el alta con un solo mes, y el trabajo de cada mes es exactamente el mismo:
:func:`handlers.parcela.procesar_mes` escribe las filas y
:func:`handlers.rancho.procesar_mes` sube el mapa. Reusarlas no es sólo ahorrar
código: si el cierre de mes calculara distinto que el alta, el mes 25 de una
parcela no sería comparable con los 24 que trajo su alta, y nadie lo notaría
mirando los números.

Diferencias con el alta:

- **el mes lo manda el evento**, en ``periodo``, en vez de calcularlo un step
  ``plan``. El reconciliador ya decidió qué mes cierra, y con el día 5 de por
  medio (``#30``) no siempre es el anterior a "hoy";
- **un solo step**, así que la barra de avance va del 0 al 99 en un salto;
- **el límite de concurrencia** compartido con las altas
  (:data:`handlers.altas.CONCURRENCIA_GEE`).
"""

from typing import Any

import inngest
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE
from services.avance_job import paso
from services.inngest_client import inngest_client

from handlers import parcela as alta_de_parcela
from handlers import rancho as alta_de_rancho
from handlers.altas import CONCURRENCIA_GEE, requerido
from handlers.cierre import cerrar_por_falla
from handlers.seguimiento import RETRIES, con_seguimiento


def _mes_del_evento(payload: dict) -> Mes:
    """El mes que manda el evento, en ``periodo``.

    Raises:
        inngest.NonRetriableError: si falta o no es ``AAAA-MM``. Un periodo mal
            escrito da el mismo error en cada reintento.
    """
    texto = requerido(payload, "periodo")
    try:
        return Mes.desde_texto(str(texto))
    except (TypeError, ValueError) as error:
        msg = f"el evento trae un periodo que no es un mes AAAA-MM: {texto!r}"
        raise inngest.NonRetriableError(msg) from error


@inngest_client.create_function(
    fn_id="process-parcela-mes",
    trigger=inngest.TriggerEvent(event="terra/parcela.mes.requested"),
    retries=RETRIES,
    concurrency=CONCURRENCIA_GEE,
    on_failure=cerrar_por_falla,
)
@con_seguimiento
def process_parcela_mes(
    ctx: inngest.Context,  # noqa: ARG001 - la firma que pide con_seguimiento
    step: inngest.StepSync,
    payload: dict,
) -> dict[str, Any]:
    """El mes cerrado de una parcela: sus filas en ``measurements``."""
    parcela_id = requerido(payload, "parcelaId")
    tenant_id = requerido(payload, "tenantId")
    coordenadas = requerido(payload, "coordinates")
    mes = _mes_del_evento(payload)

    resultado = paso(
        step,
        f"mes-{mes}",
        lambda: alta_de_parcela.procesar_mes(
            parcela_id=parcela_id,
            tenant_id=tenant_id,
            coordenadas=coordenadas,
            mes=mes,
            posicion=1,
            total=1,
            receta=RECETA_VIGENTE,
        ),
    )

    return {
        "status": "success",
        "receta": RECETA_VIGENTE.version,
        "mes": resultado["mes"],
        "con_valor": resultado["con_valor"],
        "filas": resultado["escritas"],
    }


@inngest_client.create_function(
    fn_id="process-rancho-mes",
    trigger=inngest.TriggerEvent(event="terra/rancho.mes.requested"),
    retries=RETRIES,
    concurrency=CONCURRENCIA_GEE,
    on_failure=cerrar_por_falla,
)
@con_seguimiento
def process_rancho_mes(
    ctx: inngest.Context,  # noqa: ARG001 - la firma que pide con_seguimiento
    step: inngest.StepSync,
    payload: dict,
) -> dict[str, Any]:
    """El mes cerrado de un rancho: su mapa, si hubo un píxel limpio."""
    rancho_id = requerido(payload, "ranchoId")
    tenant_id = requerido(payload, "tenantId")
    coordenadas = requerido(payload, "coordinates")
    mes = _mes_del_evento(payload)

    resultado = paso(
        step,
        f"mes-{mes}",
        lambda: alta_de_rancho.procesar_mes(
            rancho_id=rancho_id,
            tenant_id=tenant_id,
            coordenadas=coordenadas,
            mes=mes,
            posicion=1,
            total=1,
            receta=RECETA_VIGENTE,
        ),
    )

    return {
        "status": "success",
        "receta": RECETA_VIGENTE.version,
        "mes": resultado["mes"],
        "mapas": 1 if resultado["storage_key"] else 0,
    }
