import logging
import time
from datetime import datetime, timezone

import inngest

from services.inngest_client import inngest_client
from services.ee.ee_client import init_ee, get_sentinel2_dates
from services.ee_service import generate_heatmap_tiles, generate_time_series_data
from services.export_service import export_heatmap, export_time_series
from services.storage_service import get_storage_service
from services.cog_converter import convert_to_cog
# OJO: `get_sentinel2_dates` se importa arriba desde `services.ee.ee_client` y
# consulta GEE. El repositorio tiene otra funcion con el MISMO nombre que leia
# la tabla `sentinel2_dates`. Se importaba aliasada como `get_cached_dates` y no
# la llamaba nadie: si alguien quitaba el alias, la del repositorio pisaba a la
# de GEE y las dos llamadas de abajo le pasaban un ROI donde espera un
# geometry_id. La del repositorio se elimino en FASE D; el alias, aca.
from repositories.db_repository import (update_processing_job, insert_layer,
                                       insert_measurement, insert_measurements)
from services.avance_job import paso, reportar
# M.4.2: el wrapper de jobs, el ROI y las utilidades viven en `handlers/`, para que
# los handlers del pipeline mensual no importen este modulo, que es la capa vieja
# y se borra en M.6.1. Se importan con los nombres de siempre: los handlers de
# abajo los buscan aca, y los tests los reemplazan aca.
from handlers.geometria import coords_to_geometry, normalizar_coordenadas  # noqa: F401 - la usan los tests
from handlers.seguimiento import RETRIES, envolver_con_estado
from handlers.utilidades import borrar_temporales as _borrar_temporales
from handlers.utilidades import ms_desde as _ms_desde
# M.4.4 y M.4.5: las altas ya son del pipeline mensual. Se registran desde
# `all_functions`, abajo, hasta que M.6.1 borre este modulo y mude la lista.
from handlers.parcela import process_parcela
from handlers.rancho import process_rancho
# M.5.3: el cierre de mes, un mes por evento.
from handlers.mes import process_parcela_mes, process_rancho_mes
# M.4.7: cierra el job de un alta cancelada en Inngest.
from handlers.cancelaciones import cerrar_altas_canceladas
# M.4.10: mide la espera de Inngest entre steps vacios. Se dispara a mano.
from handlers.diagnostico import diagnostico_latencia

logger = logging.getLogger("inngest_handlers")

def claves_de_capa(entidad: str, entidad_id: str, indice: str, inicio: str,
                   fin: str | None = None):
    """Devuelve `(storage_key, natural_key)` de **una sola fuente** (E.9).

    Las dos identifican la misma capa y por eso no pueden calcularse por
    separado. Antes se armaban en dos sitios distintos, y **colisionaban**:

        process_parcela              generate_heatmap_on_demand
        natural_key ndvi_{id}_{fecha}   natural_key {indice}_{id}_{fechaInicio}
        storage_key parcelas/{id}/…     storage_key heatmaps/{id}/…

    Para `indice="ndvi"`, la misma parcela y la misma fecha, **las dos
    `natural_key` son identicas**. `insert_layer` las convierte en el mismo
    UUIDv5, o sea **la misma fila de `layers`** — pero con `storage_key`
    distinta. Resultado: el pedido on-demand pisaba la fila de la capa
    sistematica apuntandola a `heatmaps/`, y el objeto de `parcelas/` quedaba
    **huerfano en el bucket, referenciado por nadie**. Y al reves.

    Y habia un segundo choque dentro del propio on-demand: la key usaba solo
    `fechaInicio`, asi que un heatmap de ene1–ene31 y otro de ene1–feb28
    escribian **el mismo objeto**. Por eso el periodo entra completo cuando es un
    rango.

    **El prefijo `heatmaps/` desaparece.** Un heatmap NDVI de una parcela y el
    raster sistematico NDVI de esa parcela son el mismo tipo de objeto; estaban
    separados por *por que se pidio*, no por *que son*. Con el prefijo unificado,
    el mismo contenido cae en el mismo lugar lo haya pedido quien lo haya pedido,
    y `object_exists` puede saltear el recalculo.

    ⚠️ La forma de la key —`{entidad}s/{id}/{periodo}_{indice}.tif`— **no se
    rediseña aca**: eso es `PREGUNTAS_ABIERTAS` A-7, y va con la FASE C, que es
    la que multiplica los objetos. Lo que se arregla hoy es la colision.
    """
    periodo = inicio if not fin or fin == inicio else f"{inicio}_{fin}"
    # La `natural_key` lleva la entidad. Sin ella, un rancho y una parcela con el
    # mismo id, indice y fecha darian el mismo UUIDv5 y por lo tanto la misma
    # fila. Hoy es imposible —los ids son uuid— pero la identidad de una capa no
    # deberia depender de eso, y cerrarlo es gratis: el worker todavia no
    # escribio contra el MinIO real, asi que no hay UUIDv5 en produccion que
    # cambiar. Mas adelante dejaria de ser gratis.
    return (f"{entidad}s/{entidad_id}/{periodo}_{indice}.tif",
            f"{entidad}_{indice}_{entidad_id}_{periodo}")


def _with_job_tracking(func):
    """El wrapper de jobs de la capa vieja (`handlers/seguimiento.py`).

    `update_processing_job` se busca en **este** modulo al llamarla: los tests
    de los handlers viejos la reemplazan aca. Se va con la capa vieja (M.6.1);
    los handlers nuevos usan `con_seguimiento`.
    """
    return envolver_con_estado(func, lambda *a, **k: update_processing_job(*a, **k))


# M.4.4: `process_parcela` vive en `handlers/parcela.py`, sobre el pipeline
# mensual, con el mismo `fn_id`. El de aca procesaba el alta por ventanas de
# fechas, subia el COG de la parcela y llenaba `sentinel2_dates`; se borro junto
# con sus ayudantes (`ventanas`, `_escribir_serie`). `all_functions` registra el
# nuevo.


# M.4.5: `process_rancho` vive en `handlers/rancho.py`, sobre el pipeline
# mensual, con el mismo `fn_id`. El de aca bajaba un compuesto de 30 dias y
# emitia `terra/raster.ingested` para que `register_layer` escribiera la fila de
# `layers`; el nuevo la escribe en el mismo step, y los dos se borraron.

@inngest_client.create_function(
    fn_id="generate-heatmap-on-demand",
    trigger=inngest.TriggerEvent(event="terra/parcela.heatmap.requested"),
    retries=RETRIES,
)
@_with_job_tracking
def generate_heatmap_on_demand(ctx: inngest.Context, step: inngest.StepSync, payload: dict) -> dict:
    def _generate():
        etapa = "generate-heatmap"
        indice, inicio, fin = payload["indice"], payload["fechaInicio"], payload["fechaFin"]
        reportar(etapa, f"Mapa de {indice} para {inicio} → {fin}",
                 progreso=5, indice=indice, desde=inicio, hasta=fin)
        t0 = time.monotonic()
        init_ee()
        roi = coords_to_geometry(payload["coordinates"])
        tiles = generate_heatmap_tiles(roi, None, indice, inicio, fin, payload.get("cloudPct", 30))

        obj_name, natural_key = claves_de_capa(
            "parcela", payload["parcelaId"], indice, inicio, fin,
        )

        # E.9: si el objeto ya esta, no se vuelve a pedir a GEE. La key encodea
        # entidad, periodo e indice, asi que "ya esta" significa "es el mismo
        # composite" — y desde `W-6` `object_exists` levanta en vez de contestar
        # `False` cuando no pudo averiguarlo, con lo cual un fallo de permisos o
        # de red ya no se lee como "no existe" y no dispara un recalculo.
        if get_storage_service().object_exists(obj_name):
            logger.info("La capa %s ya existe; se saltea el calculo en GEE", obj_name)
            reportar(etapa, "La capa ya estaba en el almacenamiento: se reutiliza sin calcular en GEE",
                     progreso=80)
            storage_key = obj_name
        else:
            reportar(etapa, "Calculando y exportando el GeoTIFF en GEE", progreso=20)
            path, stats = export_heatmap(roi, None, indice, inicio, fin, payload.get("cloudPct", 30), "geotiff")
            reportar(etapa, "GeoTIFF exportado: convirtiendo a COG y subiendo", progreso=60)
            cog_path = convert_to_cog(path)
            storage_key = get_storage_service().upload_file(obj_name, cog_path, "image/tiff")
            _borrar_temporales(path, cog_path)

        # `insert_layer` corre igual: es idempotente por el UUIDv5, y si la capa
        # existia en el bucket pero su fila no —o llegaba de otro tenant— hay que
        # registrarla.
        #
        # `stats` no se persiste: `layers` no tiene columnas para min/max/mean/stddev.
        insert_layer(
            natural_key=natural_key,
            product=indice, storage_key=storage_key,
            acquired_ts=inicio,
            ingested_ts=datetime.now(timezone.utc),
            tenant_id=payload['tenantId'], parcela_id=payload['parcelaId'],
            bbox=None, source='on_demand'
        )
        reportar(etapa, f"Capa de {indice} registrada ({inicio} → {fin})",
                 progreso=95, ms=_ms_desde(t0))
        return {"tiles": tiles, "storage_key": storage_key}

    res = paso(step, "generate-heatmap", _generate)
    return res

@inngest_client.create_function(
    fn_id="compute-timeseries",
    trigger=inngest.TriggerEvent(event="terra/parcela.timeseries.requested"),
    retries=RETRIES,
)
@_with_job_tracking
def compute_timeseries(ctx: inngest.Context, step: inngest.StepSync, payload: dict) -> dict:
    def _compute_ts():
        etapa = "compute-ts"
        indice, inicio, fin = payload["indice"], payload["fechaInicio"], payload["fechaFin"]
        reportar(etapa, f"Serie de {indice}: {inicio} → {fin}",
                 progreso=10, indice=indice, desde=inicio, hasta=fin)
        t0 = time.monotonic()
        init_ee()
        roi = coords_to_geometry(payload["coordinates"])
        ts_data = generate_time_series_data(roi, inicio, fin, indice, payload.get("cloudPct", 30))
        escritas = insert_measurements(
            {"parcela_id": payload['parcelaId'], "indice": indice,
             "fecha": p['date'], "tenant_id": payload['tenantId'],
             "valor": p.get('mean')}
            for p in ts_data
        )
        reportar(etapa, f"Serie de {indice}: {escritas} fechas con valor",
                 progreso=95, escritas=escritas, ms=_ms_desde(t0))
        return {"data": ts_data}
    res = paso(step, "compute-ts", _compute_ts)
    return res

@inngest_client.create_function(
    fn_id="query-available-dates",
    trigger=inngest.TriggerEvent(event="terra/parcela.dates.requested"),
    retries=RETRIES,
)
@_with_job_tracking
def query_available_dates(ctx: inngest.Context, step: inngest.StepSync, payload: dict) -> dict:
    def _query_dates():
        init_ee()
        roi = coords_to_geometry(payload["coordinates"])
        # M.6.1: las fechas se devuelven en el resultado del job y **no se
        # persisten**. Antes cada una se escribia en `sentinel2_dates`, una
        # tabla que no tenia una sola consulta de lectura (`§9`, A-4). El
        # llamador siempre las leyo de aca, no de la tabla.
        return {"dates": get_sentinel2_dates(
            roi, payload["fechaInicio"], payload["fechaFin"],
            payload.get("cloudPct", 30),
        )}
    res = step.run("query-dates", _query_dates)
    return res

@inngest_client.create_function(
    fn_id="export-data",
    trigger=inngest.TriggerEvent(event="terra/parcela.export.requested"),
    retries=RETRIES,
)
@_with_job_tracking
def export_data(ctx: inngest.Context, step: inngest.StepSync, payload: dict) -> dict:
    def _export():
        init_ee()
        roi = coords_to_geometry(payload["coordinates"])
        fmt = payload["formato"]
        
        if fmt in ["geotiff", "png"]:
            path, stats = export_heatmap(roi, None, payload["indice"], payload["fecha"], payload["fecha"], 30, fmt)
            obj = f"exports/{payload['parcelaId']}/{payload['fecha']}_{payload['indice']}.{fmt}"
            ct = "image/tiff" if fmt == "geotiff" else "image/png"
            storage_key = get_storage_service().upload_file(obj, path, ct)
            _borrar_temporales(path)
        else: # csv
            # We need series pts
            ts_data = generate_time_series_data(roi, payload["fecha"], payload["fecha"], payload["indice"])
            path, _ = export_time_series(ts_data, payload["indice"], payload["fecha"], payload["fecha"], roi, None)
            obj = f"exports/{payload['parcelaId']}/{payload['fecha']}_{payload['indice']}.csv"
            storage_key = get_storage_service().upload_file(obj, path, "text/csv")
            _borrar_temporales(path)
            
        return {"storage_key": storage_key}
    res = step.run("export", _export)
    return res

@inngest_client.create_function(
    fn_id="compute-parcela-stats",
    trigger=inngest.TriggerEvent(event="terra/parcela.stats.requested"),
    retries=RETRIES,
)
@_with_job_tracking
def compute_parcela_stats(ctx: inngest.Context, step: inngest.StepSync, payload: dict) -> dict:
    def _stats():
        init_ee()
        roi = coords_to_geometry(payload["coordinates"])
        results = {}
        for idx in payload["indices"]:
            ts_data = generate_time_series_data(roi, payload["fecha"], payload["fecha"], idx)
            if ts_data:
                p = ts_data[0]
                insert_measurement(
                    parcela_id=payload['parcelaId'], indice=idx, fecha=payload['fecha'],
                    tenant_id=payload['tenantId'], valor=p.get('mean'),
                )
                results[idx] = p.get('mean')
        return results
    res = step.run("stats", _stats)
    return res

all_functions = [
    process_parcela,
    process_rancho,
    process_parcela_mes,
    process_rancho_mes,
    cerrar_altas_canceladas,
    diagnostico_latencia,
    generate_heatmap_on_demand,
    compute_timeseries,
    query_available_dates,
    export_data,
    compute_parcela_stats
]
