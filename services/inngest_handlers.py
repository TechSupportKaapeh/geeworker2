import logging
import time
from datetime import datetime, timezone

import inngest

from services.inngest_client import inngest_client
from services.ee.ee_client import init_ee
from services.ee_service import generate_heatmap_tiles
from services.export_service import export_heatmap
from services.storage_service import get_storage_service
from services.cog_converter import convert_to_cog
from repositories.db_repository import update_processing_job, insert_layer
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

# M.6.2, 2026-09-20 (`DECISIONS #60`): se borraron los cuatro handlers a demanda
# —`compute_timeseries`, `query_available_dates`, `export_data` y
# `compute_parcela_stats`— junto con sus endpoints en Geocore. Decision del
# usuario, con el front avisado.
#
# Lo que el pipeline mensual ya daba mejor:
#   - la serie: 96 filas por parcela (24 meses x 4 indices) con 7 estadisticas,
#     medidas a 10 m y no a 60, sin descartar pasadas y con EVI bien calculado;
#   - las estadisticas de una fecha: un subconjunto de eso;
#   - el CSV: se arma leyendo `measurements`, no yendo a GEE.
#
# Y dos de los cuatro **nunca devolvieron un dato**: `stats` y el CSV de
# `export` le pedian a GEE el rango `fecha -> fecha`, y `filterDate` es
# semiabierto. `compute_timeseries` ademas escribia filas con `receta IS NULL`,
# que son las que hubo que borrar a mano en M.3.5.
#
# Lo unico que se pierde es la ventana arbitraria (pedir del 3 al 20 de marzo).
# Recuperarla es cambiar `Mes` por un rango semiabierto en `pipeline/etapas/`,
# no conservar este codigo: contestaba a 60 m y con formulas equivocadas.
#
# `generate_heatmap_on_demand` sigue vivo: el mapa a demanda **pasa al
# pipeline** (`ARQUITECTURA` §9), y eso es M.6.2b.

all_functions = [
    process_parcela,
    process_rancho,
    process_parcela_mes,
    process_rancho_mes,
    cerrar_altas_canceladas,
    diagnostico_latencia,
    generate_heatmap_on_demand,
]
