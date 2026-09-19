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

Los temporales no cruzan steps: lo que sale del step es la ``storage_key``.

Reemplaza al ``process_rancho`` de la capa vieja con el mismo ``fn_id``, y a su
``register_layer``, que solo escuchaba el evento que emitía el viejo.

No importa ``services/inngest_handlers.py``, que M.6.1 borra.
"""

import os
import tempfile
import time
from datetime import UTC, datetime
from typing import Any, Final

import inngest
import rasterio
from pipeline import ejecucion
from pipeline.claves import claves_cog_mensual
from pipeline.ejecucion import reduccion_del_mes, url_de_descarga
from pipeline.periodos import Mes, rango
from pipeline.productos import mapa_del_mes
from pipeline.receta import RECETA_VIGENTE, Receta
from repositories.db_repository import insert_layer
from services.avance_job import AVISO, paso, reportar
from services.cog_converter import convert_to_cog
from services.ee.ee_client import init_ee
from services.ee.gee_download import descargar_a_archivo, parametros_de_descarga
from services.inngest_client import inngest_client
from services.storage_service import get_storage_service

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
from handlers.utilidades import borrar_temporales, entre, ms_desde

# El mapa de la v1 es solo de NDVI (`ARQUITECTURA` §10). No es un parámetro de la
# receta porque no cambia ningún número: cambia qué se dibuja. El índice va en la
# key, así que sumar otro mapa no pisa este.
INDICE_DEL_MAPA: Final = "ndvi"

# Lo que en el COG significa "sin dato". **El GeoTIFF de GEE no declara nodata**:
# lo enmascarado llega como 0, que en NDVI es suelo desnudo (medido el 2026-09-18:
# 3.929 de 10.325 píxeles de un mes con nubes). Se rellena con un valor que ningún
# índice normalizado puede dar, y el COG lo convierte en su máscara.
NODATA_COG: Final = -9999.0

_BYTES_POR_MEGA: Final = 1_000_000


def _parametros(roi: object, receta: Receta) -> dict[str, Any]:
    """Los de ``gee_download``, con la escala de la receta.

    La grilla (``crs``, formato) es la de ``DECISIONS #19``. La escala sale de la
    receta porque el mapa tiene que ser de los mismos píxeles que las estadísticas
    (``ARQUITECTURA`` §8.5); hoy las dos valen 10 m.
    """
    return {**parametros_de_descarga(roi), "scale": receta.escala_m}


def _estadisticas_de_la_capa(reduccion: Any) -> dict[str, float | None]:  # noqa: ANN401 - una Reduccion
    """Los números del mapa: las del índice, más cobertura y observaciones (D-2)."""
    return {
        **reduccion.estadisticas[INDICE_DEL_MAPA],
        "cobertura": reduccion.cobertura,
        "observaciones": reduccion.observaciones,
    }


def _subir_cog(url: str, storage_key: str) -> tuple[list[float], float]:
    """Baja el GeoTIFF, lo pasa a COG y lo sube. Devuelve el bbox y los megas.

    Los temporales se borran en ``finally``: el proceso es de larga vida y los
    reintentos se acumulan.
    """
    crudo = cog = None
    try:
        fd, crudo = tempfile.mkstemp(suffix=".tif")
        os.close(fd)
        descargar_a_archivo(url, crudo)
        megas = round(os.path.getsize(crudo) / _BYTES_POR_MEGA, 2)  # noqa: PTH202 - la ruta es str de tempfile
        with rasterio.open(crudo) as fuente:
            bbox = list(fuente.bounds)
        cog = convert_to_cog(crudo, nodata=NODATA_COG)
        get_storage_service().upload_file(storage_key, cog, "image/tiff")
    finally:
        borrar_temporales(crudo, cog)
    return bbox, megas


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
    # Las claves primero: validan los uuid antes de pedirle nada a GEE. Un id que
    # no es un uuid no se arregla reintentando.
    try:
        claves = claves_cog_mensual(
            tenant_id=tenant_id,
            rancho_id=rancho_id,
            receta=receta,
            indice=INDICE_DEL_MAPA,
            mes=mes,
        )
    except (TypeError, ValueError) as error:
        msg = f"el evento no trae ids válidos para la key del mapa: {error}"
        raise inngest.NonRetriableError(msg) from error
    init_ee()
    roi = coords_to_geometry(coordenadas)
    progreso = entre(PROGRESO_PLAN, PROGRESO_MESES, posicion, total)

    with errores_de_gee(mes, "este rancho"), ejecucion.contando() as conteo:
        reduccion = reduccion_del_mes(roi, mes, receta)
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
            return {"mes": str(mes), "cobertura": 0.0, "storage_key": None}
        imagen = mapa_del_mes(roi, mes, receta, INDICE_DEL_MAPA).unmask(
            NODATA_COG, sameFootprint=False
        )
        url = url_de_descarga(imagen, _parametros(roi, receta))

    bbox, megas = _subir_cog(url, claves.storage_key)
    inicio, _ = rango(mes)
    insert_layer(
        natural_key=claves.natural_key,
        product=INDICE_DEL_MAPA,
        storage_key=claves.storage_key,
        acquired_ts=inicio,
        ingested_ts=datetime.now(UTC),
        tenant_id=tenant_id,
        rancho_id=rancho_id,
        bbox=bbox,
        source="mensual",
        receta=receta.version,
        estadisticas=_estadisticas_de_la_capa(reduccion),
    )
    reportar(
        f"mes-{mes}",
        f"Mes {posicion} de {total} ({mes}): mapa de {INDICE_DEL_MAPA.upper()} "
        f"subido, cobertura {porcentaje(reduccion.cobertura)}",
        progreso=progreso,
        mes=str(mes),
        cobertura=reduccion.cobertura,
        megas=megas,
        llamadas=conteo.llamadas,
        ms=ms_desde(t0),
    )
    return {
        "mes": str(mes),
        "cobertura": reduccion.cobertura,
        "storage_key": claves.storage_key,
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
        "mapas": sum(1 for r in resultados if r["storage_key"]),
    }
