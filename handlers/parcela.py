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
from pipeline.ejecucion import reduccion_de, ventanas_de
from pipeline.filas import filas_de
from pipeline.periodos import Mes
from pipeline.receta import RECETA_VIGENTE, Receta
from pipeline.ventanas import del_mes
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
    """El step ``mes-AAAA-MM``: pedirle el mes a GEE y escribir sus filas.

    Es idempotente: el upsert pisa las filas del mes si el step se repite.

    **El step sigue siendo un mes** (``DECISIONS #63``): lo que cambió en M.9.0b
    es que el mes se parte en las ventanas que diga la receta, y cada ventana
    escribe sus filas. Con ``agrupamiento_estadisticas: entero`` la partición da
    una sola ventana —el mes— y esto hace exactamente lo de antes: un pedido a
    GEE, cuatro filas, la misma bitácora.

    Raises:
        inngest.NonRetriableError: si GEE dice que el pedido no se puede hacer
            así (sin memoria, demasiados píxeles). Reintentarlo da el mismo error
            y gasta cuota: ``pipeline.ejecucion`` lo marca, y acá se traduce a lo
            que Inngest entiende, porque el pipeline no conoce a Inngest.
    """
    t0 = time.monotonic()
    init_ee()
    roi = coords_to_geometry(coordenadas)
    pedido = del_mes(mes)

    # El trabajo de GEE del step entero va bajo el mismo manejador y el mismo
    # conteo: la bitácora sigue diciendo cuántas llamadas costó el mes, sea una
    # ventana o sean ocho.
    filas = []
    coberturas = []
    with errores_de_gee(mes, "esta parcela"), ejecucion.contando() as conteo:
        for ventana in ventanas_de(roi, pedido, receta):
            reduccion = reduccion_de(roi, ventana, receta)
            coberturas.append(reduccion)
            filas.extend(
                filas_de(
                    parcela_id=parcela_id,
                    tenant_id=tenant_id,
                    ventana=ventana,
                    reduccion=reduccion,
                    receta=receta,
                )
            )

    escritas = upsert_mediciones_mensuales(tuple(filas))

    # Las filas de una ventana comparten la cobertura, y con ella si llevan valor.
    con_valor = bool(filas) and all(fila.valor is not None for fila in filas)
    # Con una sola ventana —lo de hoy— estos dos son los de esa ventana, así que
    # la bitácora dice exactamente lo que decía antes de M.9.0b.
    cobertura_media = _promedio([r.cobertura for r in coberturas])
    observaciones = _promedio([
        r.observaciones for r in coberturas if r.observaciones is not None
    ])
    cobertura = porcentaje(cobertura_media) if cobertura_media is not None else "—"
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
        cobertura=cobertura_media,
        observaciones=observaciones,
        escritas=escritas,
        llamadas=conteo.llamadas,
        ms=ms_desde(t0),
    )
    return {
        "mes": str(mes),
        "cobertura": cobertura_media,
        "con_valor": con_valor,
        "escritas": escritas,
    }


def _promedio(numeros: list[float]) -> float | None:
    """El promedio, o ``None`` si la lista está vacía.

    Con una sola ventana devuelve ese número tal cual, que es lo que hace que la
    bitácora de hoy no cambie. Con varias, un promedio simple: la bitácora es
    para mirar un step, no para calcular nada.
    """
    return sum(numeros) / len(numeros) if numeros else None


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
