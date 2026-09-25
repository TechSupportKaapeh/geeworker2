"""El alta de un rancho sobre el pipeline mensual (M.4.5).

``terra/rancho.created`` trae un rancho nuevo, y este handler le deja un COG de
NDVI por mes cerrado en el bucket, con su fila en ``layers``
(``ARQUITECTURA_PIPELINE.md`` §2 y §6). Con la receta v1 son hasta 24 mapas.

Los steps son los del alta de una parcela (``handlers/altas.py``): ``plan`` y un
``mes-AAAA-MM`` por mes. Cada mes, en **un solo step** (E.3, ``DECISIONS #26``):

1. las estadísticas del rancho en el mes, con la misma reducción que la parcela
   (una llamada a GEE). Van a ``layers.estadisticas`` junto con la cobertura;
2. **si la cobertura es 0, no hay mapa**: no se descarga ni se sube nada, y queda
   un aviso en la bitácora (decisión del usuario, 2026-09-18). Con cualquier
   cobertura mayor, aunque quede bajo el mínimo de la receta, el mapa va: muestra
   lo que se vio;
3. la imagen del mes (``productos.mapa_del_mes``), con lo enmascarado relleno con
   :data:`NODATA_COG`, la URL por el borde (``ejecucion.url_de_descarga``), la
   descarga, el COG y la subida a la key de ``claves_cog_mensual``;
4. ``insert_layer`` con ``source="mensual"``, la receta y las estadísticas.

Los temporales no cruzan steps: lo que sale del step son las ``storage_keys``.

Reemplaza al ``process_rancho`` de la capa vieja con el mismo ``fn_id``, y a su
``register_layer``, que solo escuchaba el evento que emitía el viejo.

No importa ``services/inngest_handlers.py``, que M.6.1 borra.
"""

import time
from datetime import UTC, datetime
from typing import Any

import inngest
from pipeline import ejecucion
from pipeline.claves import claves_cog_mensual
from pipeline.ejecucion import reduccion_de, url_de_descarga
from pipeline.periodos import Mes
from pipeline.productos import mapa_de
from pipeline.receta import RECETA_VIGENTE, Receta
from pipeline.ventanas import Ventana, agrupamiento, del_mes
from repositories.db_repository import insert_layer
from services.avance_job import AVISO, paso, reportar
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
from handlers.raster import NODATA_COG, parametros_del_mapa, subir_cog
from handlers.seguimiento import RETRIES, con_seguimiento
from handlers.utilidades import entre, ms_desde


# **Un mapa por índice de la receta** (decisión del usuario, 2026-09-20). Hasta el
# 2026-09-20 era sólo NDVI, que fue con lo que se probó el pipeline.
#
# No es un parámetro de la receta y por eso **no rompe el congelamiento de
# `s2-mensual-v1`**: no cambia ningún número, cambia qué se dibuja. Los cuatro índices
# ya se calculaban; lo que faltaba era descargarlos. El índice va en la key y en la
# `natural_key`, así que son cuatro objetos y cuatro filas por mes, sin pisarse.
#
# El costo es de descargas: cuatro por mes en vez de una.
def indices_del_mapa(receta: Receta) -> tuple[str, ...]:
    """Los índices que se suben como mapa: todos los de la receta."""
    return receta.indices

# `NODATA_COG`, `_parametros` y `_subir_cog` se mudaron a `handlers/raster.py` en
# M.6.2b: el mapa a demanda hace lo mismo, y el nodata y la escala son un
# contrato con el tileserver que no puede vivir en dos lados.


def _estadisticas_de_la_capa(reduccion: Any, indice: str) -> dict[str, float | None]:  # noqa: ANN401 - una Reduccion
    """Los números del mapa: los de su índice, con cobertura y observaciones (D-2)."""
    return {
        **reduccion.estadisticas[indice],
        "cobertura": reduccion.cobertura,
        "observaciones": reduccion.observaciones,
    }


def _la_unica_ventana(rancho_id: str, mes: Mes, receta: Receta) -> Ventana:
    """La ventana del ráster del mes, y la garantía de que es una sola.

    **El ráster sigue siendo mensual** (``DECISIONS #63``): los motivos de ``#31``
    —146 descargas contra 24, TiTiler abriendo 3 a 6 COG por tile— no cambiaron.
    Por eso acá se lee ``agrupamiento_raster`` de la receta, se arma su ventana, y
    **se exige que sea una**.

    No se generaliza a N ventanas a propósito. Este es el camino más caro y más
    frágil del worker —descarga, conversión a COG y subida—, y un bucle cuyo N es
    siempre 1 sería código que nadie ejecuta hasta que alguien cambie la receta, y
    que fallaría justo ahí. Prefiero que la receta que pida un ráster por pasada
    se encuentre con este error, que dice qué falta hacer, antes que con un camino
    nunca probado. Escribirlo es su propia tarea.

    **No le pregunta nada a GEE**, y por eso se resuelve acá y no con
    ``ventanas_de``: un agrupamiento que necesita las fechas de las pasadas no
    puede dar una sola ventana por mes, así que se rechaza sin gastar una llamada
    —y sin necesitar el ROI, que en este punto todavía no se armó—.

    Raises:
        inngest.NonRetriableError: si la receta no parte el mes en exactamente una
            ventana para el ráster. Reintentarlo daría lo mismo: es la receta.
    """
    modo = agrupamiento(receta.agrupamiento_raster)
    ventanas = () if modo.necesita_fechas else modo.partir(del_mes(mes), ())
    if len(ventanas) != 1:
        msg = (
            f"la receta {receta.version} pide el ráster con agrupamiento "
            f"{receta.agrupamiento_raster!r}, que no da una sola ventana por mes; "
            f"el mapa del rancho {rancho_id} es de una sola "
            f"(DECISIONS #63: el ráster sigue siendo mensual)"
        )
        raise inngest.NonRetriableError(msg)
    return ventanas[0]


def procesar_mes(  # noqa: PLR0913 - lo que necesita un mes, por nombre
    *,
    rancho_id: str,
    tenant_id: str,
    coordenadas: object,
    mes: Mes,
    posicion: int,
    total: int,
    receta: Receta,
) -> dict[str, Any]:
    """El step ``mes-AAAA-MM``: el mapa del mes, si hubo algo que ver.

    Es idempotente: la key y la ``natural_key`` salen del rancho, el índice, la
    receta y el mes, así que repetir el step sobrescribe el mismo objeto y la
    misma fila.

    Raises:
        inngest.NonRetriableError: si un id del evento no es un uuid, o si GEE
            dice que el pedido no se puede hacer así (sin memoria, demasiados
            píxeles, un rancho que no entra en una descarga).
    """
    t0 = time.monotonic()
    indices = indices_del_mapa(receta)
    ventana = _la_unica_ventana(rancho_id, mes, receta)
    # Las claves primero, las de **todos** los índices: validan los uuid antes de
    # pedirle nada a GEE. Un id que no es un uuid no se arregla reintentando.
    try:
        claves_por_indice = {
            indice: claves_cog_mensual(
                tenant_id=tenant_id,
                rancho_id=rancho_id,
                receta=receta,
                indice=indice,
                ventana=ventana,
            )
            for indice in indices
        }
    except (TypeError, ValueError) as error:
        msg = f"el evento no trae ids válidos para la key del mapa: {error}"
        raise inngest.NonRetriableError(msg) from error
    init_ee()
    roi = coords_to_geometry(coordenadas)
    progreso = entre(PROGRESO_PLAN, PROGRESO_MESES, posicion, total)

    with errores_de_gee(mes, "este rancho"), ejecucion.contando() as conteo:
        reduccion = reduccion_de(roi, ventana, receta)
        if reduccion.cobertura == 0:
            reportar(
                f"mes-{mes}",
                f"Mes {posicion} de {total} ({mes}): sin un píxel limpio, sin mapa",
                progreso=progreso,
                nivel=AVISO,
                mes=str(mes),
                cobertura=0.0,
                llamadas=conteo.llamadas,
                ms=ms_desde(t0),
            )
            return {"mes": str(mes), "cobertura": 0.0, "storage_keys": []}

        # Las URL de todos, dentro del mismo bloque: las llamadas a GEE del mes
        # quedan contadas juntas y traducidas por el mismo manejador.
        parametros = parametros_del_mapa(roi, receta)
        urls = {
            indice: url_de_descarga(
                mapa_de(roi, ventana, receta, indice).unmask(
                    NODATA_COG, sameFootprint=False
                ),
                parametros,
            )
            for indice in indices
        }

    inicio = ventana.inicio
    subidos: list[str] = []
    megas_total = 0.0
    # Uno por uno, y cada uno con su fila: si el step se reintenta, las keys son las
    # mismas y se sobrescribe lo mismo. Un fallo a mitad deja los anteriores subidos,
    # que es exactamente lo que el reintento vuelve a pisar.
    for indice in indices:
        claves = claves_por_indice[indice]
        bbox, megas = subir_cog(urls[indice], claves.storage_key)
        megas_total += megas
        insert_layer(
            natural_key=claves.natural_key,
            product=indice,
            storage_key=claves.storage_key,
            acquired_ts=inicio,
            ingested_ts=datetime.now(UTC),
            tenant_id=tenant_id,
            rancho_id=rancho_id,
            bbox=bbox,
            source="mensual",
            receta=receta.version,
            estadisticas=_estadisticas_de_la_capa(reduccion, indice),
        )
        subidos.append(claves.storage_key)

    reportar(
        f"mes-{mes}",
        f"Mes {posicion} de {total} ({mes}): {len(subidos)} mapas subidos "
        f"({', '.join(i.upper() for i in indices)}), "
        f"cobertura {porcentaje(reduccion.cobertura)}",
        progreso=progreso,
        mes=str(mes),
        cobertura=reduccion.cobertura,
        megas=round(megas_total, 2),
        mapas=len(subidos),
        llamadas=conteo.llamadas,
        ms=ms_desde(t0),
    )
    return {
        "mes": str(mes),
        "cobertura": reduccion.cobertura,
        "storage_keys": subidos,
    }


@inngest_client.create_function(
    fn_id="process-rancho",
    trigger=inngest.TriggerEvent(event="terra/rancho.created"),
    retries=RETRIES,
    # M.5.3: la misma cola virtual que el cierre de mes, para no pasarse de la
    # cuota de GEE ni del plan de Inngest.
    concurrency=CONCURRENCIA_GEE,
    # M.4.7: si Inngest da la corrida por fallida sin que el handler lo vea (el
    # contenedor murió, el request se cortó), el job no queda en `running`.
    on_failure=cerrar_por_falla,
)
@con_seguimiento
def process_rancho(
    ctx: inngest.Context,  # noqa: ARG001 - la firma que pide con_seguimiento
    step: inngest.StepSync,
    payload: dict,
) -> dict[str, Any]:
    """Los mapas mensuales de un rancho nuevo, un step por mes."""
    rancho_id = requerido(payload, "ranchoId")
    tenant_id = requerido(payload, "tenantId")
    coordenadas = requerido(payload, "coordinates")
    receta = RECETA_VIGENTE

    plan = paso(step, "plan", lambda: planificar(receta))
    meses = meses_del_plan(plan)

    resultados = [
        paso(
            step,
            f"mes-{mes}",
            # Los valores del bucle entran como defaults: una clausura común
            # vería los de la última vuelta.
            lambda mes=mes, posicion=posicion: procesar_mes(
                rancho_id=rancho_id,
                tenant_id=tenant_id,
                coordenadas=coordenadas,
                mes=mes,
                posicion=posicion,
                total=len(meses),
                receta=receta,
            ),
        )
        for posicion, mes in enumerate(meses, start=1)
    ]

    return {
        "status": "success",
        "receta": plan["receta"],
        "meses": len(resultados),
        "mapas": sum(len(r["storage_keys"]) for r in resultados),
    }
