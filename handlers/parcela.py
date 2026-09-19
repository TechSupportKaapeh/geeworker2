"""El alta de una parcela sobre el pipeline mensual (M.4.4).

``terra/parcela.created`` trae una parcela nueva, y este handler le escribe sus
meses cerrados en ``measurements``: una fila por índice y mes
(``ARQUITECTURA_PIPELINE.md`` §4 y §6). Con la receta v1 son 24 meses y 96 filas.

Los steps:

- ``plan`` fija "hoy" y la lista de meses. Va en un step porque Inngest vuelve a
  correr el cuerpo del handler en cada request: un reloj leído afuera daría otros
  meses si el run cruza el fin de mes, o si un reintento espera días.
- ``mes-AAAA-MM``, uno por mes, del más viejo al más nuevo: la reducción del mes
  (una llamada a GEE), las filas y el upsert. El id lleva el mes y no un número de
  orden, así que un step memoizado nunca se confunde con otro mes. Lo que cruza
  entre steps es texto (``DECISIONS #26``): el ROI se arma adentro de cada uno.

Reemplaza al ``process_parcela`` de la capa vieja **con el mismo** ``fn_id``: para
Inngest es la misma función con otro código, no dos funciones sobre el mismo
evento. Ya no sube el COG de la parcela ni escribe ``sentinel2_dates`` (A-5 y
``ARQUITECTURA`` §9): el mapa es del rancho (M.4.5), y la parcela es números.

**El primer merge de este módulo congela ``s2-mensual-v1``**: es el primer handler
que escribe filas reales con esa receta. Cambiar un parámetro después es la v2.

No importa ``services/inngest_handlers.py``, que M.6.1 borra.
"""

import time
from typing import Any

import inngest
from pipeline import ejecucion
from pipeline.ejecucion import reduccion_del_mes
from pipeline.filas import filas_del_mes
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE, Receta
from repositories.db_repository import upsert_mediciones_mensuales
from services.avance_job import AVISO, INFO, paso, reportar
from services.ee.ee_client import init_ee
from services.inngest_client import inngest_client

from handlers.altas import (
    CONCURRENCIA_GEE,
    PROGRESO_MESES,
    PROGRESO_PLAN,
    errores_de_gee,
    meses_del_plan,
    planificar,
    porcentaje,
    requerido,
)
from handlers.cierre import cerrar_por_falla
from handlers.geometria import coords_to_geometry
from handlers.seguimiento import RETRIES, con_seguimiento
from handlers.utilidades import entre, ms_desde


def procesar_mes(  # noqa: PLR0913 - lo que necesita un mes, por nombre
    *,
    parcela_id: str,
    tenant_id: str,
    coordenadas: object,
    mes: Mes,
    posicion: int,
    total: int,
    receta: Receta,
) -> dict[str, Any]:
    """El step ``mes-AAAA-MM``: pedir el mes a GEE y escribir sus filas.

    Es idempotente: el upsert pisa las filas del mes si el step se repite.

    Raises:
        inngest.NonRetriableError: si GEE dice que el pedido no se puede hacer
            así (sin memoria, demasiados píxeles). Reintentarlo da el mismo error
            y gasta cuota: ``pipeline.ejecucion`` lo marca, y acá se traduce a lo
            que Inngest entiende, porque el pipeline no conoce a Inngest.
    """
    t0 = time.monotonic()
    init_ee()
    roi = coords_to_geometry(coordenadas)
    with errores_de_gee(mes, "esta parcela"), ejecucion.contando() as conteo:
        reduccion = reduccion_del_mes(roi, mes, receta)

    filas = filas_del_mes(
        parcela_id=parcela_id,
        tenant_id=tenant_id,
        mes=mes,
        reduccion=reduccion,
        receta=receta,
    )
    escritas = upsert_mediciones_mensuales(filas)

    # Todas las filas del mes comparten la cobertura, y con ella si llevan valor.
    con_valor = all(fila.valor is not None for fila in filas)
    cobertura = porcentaje(reduccion.cobertura)
    if con_valor:
        mensaje = f"Mes {posicion} de {total} ({mes}): cobertura {cobertura}"
    else:
        mensaje = (
            f"Mes {posicion} de {total} ({mes}): cobertura {cobertura}, bajo el "
            f"mínimo de {porcentaje(receta.cobertura_minima)}: filas sin valor"
        )
    reportar(
        f"mes-{mes}",
        mensaje,
        progreso=entre(PROGRESO_PLAN, PROGRESO_MESES, posicion, total),
        nivel=INFO if con_valor else AVISO,
        mes=str(mes),
        cobertura=reduccion.cobertura,
        observaciones=reduccion.observaciones,
        escritas=escritas,
        llamadas=conteo.llamadas,
        ms=ms_desde(t0),
    )
    return {
        "mes": str(mes),
        "cobertura": reduccion.cobertura,
        "con_valor": con_valor,
        "escritas": escritas,
    }


@inngest_client.create_function(
    fn_id="process-parcela",
    trigger=inngest.TriggerEvent(event="terra/parcela.created"),
    retries=RETRIES,
    # M.5.3: la misma cola virtual que el cierre de mes, para no pasarse de la
    # cuota de GEE ni del plan de Inngest.
    concurrency=CONCURRENCIA_GEE,
    # M.4.7: si Inngest da la corrida por fallida sin que el handler lo vea (el
    # contenedor murió, el request se cortó), el job no queda en `running`.
    on_failure=cerrar_por_falla,
)
@con_seguimiento
def process_parcela(
    ctx: inngest.Context,  # noqa: ARG001 - la firma que pide con_seguimiento
    step: inngest.StepSync,
    payload: dict,
) -> dict[str, Any]:
    """Los meses cerrados de una parcela nueva, un step por mes."""
    parcela_id = requerido(payload, "parcelaId")
    tenant_id = requerido(payload, "tenantId")
    coordenadas = requerido(payload, "coordinates")
    receta = RECETA_VIGENTE

    plan = paso(step, "plan", lambda: planificar(receta))
    meses = meses_del_plan(plan)

    resultados = []
    for posicion, mes in enumerate(meses, start=1):
        resultados.append(
            paso(
                step,
                f"mes-{mes}",
                # Los valores del bucle entran como defaults: una clausura común
                # vería los de la última vuelta.
                lambda mes=mes, posicion=posicion: procesar_mes(
                    parcela_id=parcela_id,
                    tenant_id=tenant_id,
                    coordenadas=coordenadas,
                    mes=mes,
                    posicion=posicion,
                    total=len(meses),
                    receta=receta,
                ),
            )
        )

    return {
        "status": "success",
        "receta": plan["receta"],
        "meses": len(resultados),
        "con_valor": sum(1 for r in resultados if r["con_valor"]),
        "filas": sum(r["escritas"] for r in resultados),
    }
