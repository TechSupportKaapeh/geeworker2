"""El mapa a demanda, sobre el pipeline mensual (M.6.2b).

``terra/parcela.heatmap.requested`` pide el ráster de **un índice y un mes** de
una parcela, o de un polígono suelto. Es el último handler que quedaba de la
capa vieja (``ARQUITECTURA_PIPELINE`` §9: «el mapa a demanda pasa al pipeline»),
y con él se va todo lo que la sostenía.

**Qué cambia respecto del anterior**, y por qué cada cosa:

1. **El período es un mes, no un rango libre** (decisión del usuario, 2026-09-20).
   El pipeline está tipado sobre :class:`~pipeline.periodos.Mes`; aceptar un rango
   arbitrario es generalizar las etapas, que es otra tarea. El evento trae
   ``periodo`` en ``AAAA-MM``.
2. **La key lleva el tenant y la receta**:
   ``tenants/{t}/parcelas/{p}/{receta}/{indice}/{AAAA-MM}.tif``. Antes era
   ``parcelas/{id}/{periodo}_{indice}.tif``, en la raíz del bucket — y eso es lo
   que **impedía cerrar A01** (M.8.1), que compara el prefijo contra el
   ``tenant_id`` del token de mapa.
3. **El COG declara nodata.** El GeoTIFF de GEE no lo declara, así que lo
   enmascarado llegaba como 0 y **una nube se pintaba como NDVI 0**, o sea suelo
   desnudo. Es el mismo defecto que ``DECISIONS #51`` arregló para el rancho y que
   quedó abierto en el on-demand hasta hoy.
4. **La fila de ``layers`` trae ``receta`` y ``estadisticas``.** Antes iban en
   nulo, y el panel no tenía con qué fijar la escala de color sin abrir el ráster
   (``PREGUNTAS_ABIERTAS`` D-2).
5. **Un mes sin un píxel limpio no tiene mapa**, igual que el del rancho
   (``DECISIONS #51``): no se descarga ni se sube nada, y queda el aviso en la
   bitácora. Antes se subía un ráster entero de ceros.

**Lo que se conserva del anterior:** reutilizar el objeto si ya está. La key
codifica entidad, receta, índice y mes, así que «ya está» significa «es el mismo
compuesto», y saltear el cálculo ahorra una llamada a GEE (E.9). Desde ``W-6``,
``object_exists`` levanta en vez de contestar ``False`` cuando no pudo
averiguarlo, así que un fallo de permisos no se lee como «no existe».

**El polígono libre** (``heatmap-on-the-fly``) manda ``parcelaId`` en el uuid
nulo. Su identidad es el job: ver :func:`pipeline.claves.claves_cog_adhoc`.
"""

import time
from datetime import UTC, datetime
from typing import Any, Final

import inngest
from pipeline import ejecucion
from pipeline.claves import claves_cog_adhoc, claves_cog_parcela_a_demanda
from pipeline.ejecucion import reduccion_de, url_de_descarga
from pipeline.periodos import Mes
from pipeline.productos import mapa_de
from pipeline.receta import RECETA_VIGENTE, Receta
from pipeline.ventanas import Ventana, del_mes
from repositories.db_repository import insert_layer
from services.avance_job import AVISO, paso, reportar
from services.ee.ee_client import init_ee
from services.inngest_client import inngest_client
from services.storage_service import get_storage_service

from handlers.altas import CONCURRENCIA_GEE, errores_de_gee, porcentaje, requerido
from handlers.cierre import cerrar_por_falla
from handlers.geometria import coords_to_geometry
from handlers.raster import NODATA_COG, parametros_del_mapa, subir_cog
from handlers.seguimiento import RETRIES, con_seguimiento
from handlers.utilidades import ms_desde

ETAPA: Final = "mapa"
# `Guid.Empty` de C#: lo que manda `heatmap-on-the-fly`, que no tiene parcela.
UUID_NULO: Final = "00000000-0000-0000-0000-000000000000"


def _mes_pedido(payload: dict) -> Mes:
    """El mes del evento, o un error que no se arregla reintentando.

    Un ``periodo`` mal formado es un pedido mal hecho: los cuatro reintentos de
    Inngest darían el mismo resultado y gastarían cuota.
    """
    crudo = requerido(payload, "periodo")
    try:
        return Mes.desde_texto(str(crudo))
    except (TypeError, ValueError) as error:
        msg = f"`periodo` tiene que ser AAAA-MM: {crudo!r} ({error})"
        raise inngest.NonRetriableError(msg) from error


def _indice_pedido(payload: dict, receta: Receta) -> str:
    """El índice del evento, validado contra la receta antes de tocar GEE."""
    indice = str(requerido(payload, "indice")).lower()
    if indice not in receta.indices:
        msg = (
            f"la receta {receta.version} no calcula {indice!r}. "
            f"Los que sí: {', '.join(receta.indices)}"
        )
        raise inngest.NonRetriableError(msg)
    return indice


def _claves_del_pedido(
    *, payload: dict, tenant_id: str, receta: Receta, indice: str, ventana: Ventana
) -> tuple[Any, str | None]:
    """Las claves de la capa y el ``parcela_id`` de la fila (``None`` si es libre).

    Raises:
        inngest.NonRetriableError: si un id del evento no sirve para armar la key.
    """
    parcela_id = str(payload.get("parcelaId") or "").strip()
    es_de_una_parcela = parcela_id and parcela_id != UUID_NULO
    try:
        if es_de_una_parcela:
            claves = claves_cog_parcela_a_demanda(
                tenant_id=tenant_id,
                parcela_id=parcela_id,
                receta=receta,
                indice=indice,
                ventana=ventana,
            )
            return claves, parcela_id
        claves = claves_cog_adhoc(
            tenant_id=tenant_id,
            job_id=requerido(payload, "jobId"),
            receta=receta,
            indice=indice,
            ventana=ventana,
        )
    except (TypeError, ValueError) as error:
        msg = f"el evento no trae ids válidos para la key del mapa: {error}"
        raise inngest.NonRetriableError(msg) from error
    return claves, None


def _registrar(  # noqa: PLR0913 - una fila de `layers`, todo por nombre
    *,
    claves: Any,  # noqa: ANN401 - una ClavesDeCapa
    indice: str,
    ventana: Ventana,
    tenant_id: str,
    parcela_id: str | None,
    bbox: list[float] | None,
    receta: Receta,
    estadisticas: dict[str, float | None],
) -> None:
    """La fila de ``layers``, idempotente por el UUIDv5 de la ``natural_key``.

    Corre también cuando el objeto ya estaba en el bucket: si existía sin su fila
    —o la fila apuntaba a otro lado— hay que registrarla igual.
    """
    insert_layer(
        natural_key=claves.natural_key,
        product=indice,
        storage_key=claves.storage_key,
        acquired_ts=ventana.inicio,
        ingested_ts=datetime.now(UTC),
        tenant_id=tenant_id,
        parcela_id=parcela_id,
        bbox=bbox,
        source="on_demand",
        receta=receta.version,
        estadisticas=estadisticas,
    )


def generar_mapa(payload: dict) -> dict[str, Any]:
    """El step ``mapa``: un índice, un mes, un objeto.

    Es idempotente: la key sale de la entidad, la receta, el índice y el mes, así
    que repetir el step sobrescribe el mismo objeto y la misma fila.
    """
    t0 = time.monotonic()
    receta = RECETA_VIGENTE
    tenant_id = requerido(payload, "tenantId")
    mes = _mes_pedido(payload)
    # El mapa a demanda es de UN mes, por decisión del usuario, así que su ventana
    # es la del mes y no la que diga `agrupamiento_raster`: que la receta parta el
    # mes para el ráster sistemático no cambia lo que alguien pide a mano.
    ventana = del_mes(mes)
    indice = _indice_pedido(payload, receta)
    claves, parcela_id = _claves_del_pedido(
        payload=payload,
        tenant_id=tenant_id,
        receta=receta,
        indice=indice,
        ventana=ventana,
    )

    reportar(
        ETAPA,
        f"Mapa de {indice.upper()} para {mes}",
        progreso=5,
        indice=indice,
        mes=str(mes),
    )

    init_ee()
    roi = coords_to_geometry(requerido(payload, "coordinates"))

    with errores_de_gee(mes, "este polígono"), ejecucion.contando() as conteo:
        reduccion = reduccion_de(roi, ventana, receta)
        if reduccion.cobertura == 0:
            # Igual que el mapa del rancho (`DECISIONS #51`). Antes se subía un
            # ráster entero de ceros, que se dibujaba como suelo desnudo.
            reportar(
                ETAPA,
                f"{mes}: sin un píxel limpio, sin mapa",
                progreso=95,
                nivel=AVISO,
                mes=str(mes),
                cobertura=0.0,
                llamadas=conteo.llamadas,
                ms=ms_desde(t0),
            )
            return {"storage_key": None, "cobertura": 0.0, "mes": str(mes)}

        estadisticas = {
            **reduccion.estadisticas[indice],
            "cobertura": reduccion.cobertura,
            "observaciones": reduccion.observaciones,
        }

        # E.9: si el objeto ya está, no se le pide la imagen a GEE. Las
        # estadísticas ya se calcularon —son la misma llamada que la cobertura— y
        # la fila se escribe igual.
        if get_storage_service().object_exists(claves.storage_key):
            reportar(
                ETAPA,
                "El mapa ya estaba en el almacenamiento: se reutiliza sin bajarlo",
                progreso=90,
                mes=str(mes),
                cobertura=reduccion.cobertura,
                llamadas=conteo.llamadas,
            )
            _registrar(
                claves=claves,
                indice=indice,
                ventana=ventana,
                tenant_id=tenant_id,
                parcela_id=parcela_id,
                bbox=None,
                receta=receta,
                estadisticas=estadisticas,
            )
            return {
                "storage_key": claves.storage_key,
                "cobertura": reduccion.cobertura,
                "mes": str(mes),
                "reutilizado": True,
            }

        reportar(ETAPA, "Calculando la imagen en GEE", progreso=20, mes=str(mes))
        url = url_de_descarga(
            mapa_de(roi, ventana, receta, indice).unmask(
                NODATA_COG, sameFootprint=False
            ),
            parametros_del_mapa(roi, receta),
        )

    reportar(ETAPA, "Bajando el GeoTIFF y convirtiéndolo a COG", progreso=60)
    bbox, megas = subir_cog(url, claves.storage_key)
    _registrar(
        claves=claves,
        indice=indice,
        ventana=ventana,
        tenant_id=tenant_id,
        parcela_id=parcela_id,
        bbox=bbox,
        receta=receta,
        estadisticas=estadisticas,
    )
    reportar(
        ETAPA,
        f"Mapa de {indice.upper()} de {mes} registrado, "
        f"cobertura {porcentaje(reduccion.cobertura)}",
        progreso=95,
        mes=str(mes),
        cobertura=reduccion.cobertura,
        megas=megas,
        llamadas=conteo.llamadas,
        ms=ms_desde(t0),
    )
    return {
        "storage_key": claves.storage_key,
        "cobertura": reduccion.cobertura,
        "mes": str(mes),
    }


@inngest_client.create_function(
    fn_id="generate-heatmap-on-demand",
    trigger=inngest.TriggerEvent(event="terra/parcela.heatmap.requested"),
    retries=RETRIES,
    # La misma cola virtual que las altas y el cierre: este handler también le
    # pide a GEE, y la cuota es de la cuenta entera (M.5.3).
    concurrency=CONCURRENCIA_GEE,
    on_failure=cerrar_por_falla,
)
@con_seguimiento
def generate_heatmap_on_demand(
    ctx: inngest.Context,  # noqa: ARG001 - la firma que pide con_seguimiento
    step: inngest.StepSync,
    payload: dict,
) -> dict[str, Any]:
    """El mapa de un índice y un mes, pedido a mano."""
    return paso(step, ETAPA, lambda: generar_mapa(payload))


# Mantiene el `fn_id` del handler viejo a propósito: Inngest identifica las
# funciones por ahí, así que cambiarlo dejaría la vieja registrada como
# "archivada" y los eventos en vuelo sin quién los atienda.
