import logging
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone

import ee
import inngest

from services.inngest_client import inngest_client
from services.ee.ee_client import init_ee, get_sentinel2_dates
from services.ee.gee_download import descargar_geotiff
from services.ee.ee_indices import compute_sentinel2_index
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
                                       insert_measurement, insert_measurements,
                                       insert_sentinel2_date)
from services.avance_job import (AVISO, ERROR, INFO, es_definitivo, paso, reportar,
                                 resumir_error, seguimiento)
from utils_pkg.logging_config import contexto_de_ejecucion
from utils_pkg.visualization import index_band_and_vis

logger = logging.getLogger("inngest_handlers")

# Reintentos de Inngest, en **un solo lugar**. El wrapper de jobs necesita saber
# cual es el ultimo intento para no marcar `failed` antes de tiempo (E.4), y si
# este numero y el de los decoradores se separan, el estado del job vuelve a
# mentir sin que nada falle. `ctx.attempt` es 0-indexado, asi que el ultimo
# intento es `attempt == RETRIES`.
RETRIES = 3

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


def _borrar_temporales(*rutas):
    """Borra archivos temporales sin dejar que un fallo al borrar tape el real."""
    for ruta in rutas:
        if not ruta:
            continue
        try:
            os.remove(ruta)
        except OSError as e:
            # Un temporal que no se pudo borrar es basura en disco, no un fallo
            # del procesamiento. Se registra para que no sea invisible.
            logger.warning("No se pudo borrar el temporal %s: %s", ruta, e)

def normalizar_coordenadas(coordinates):
    """Traduce el payload de Geocore a coordenadas de GeoJSON.

    Devuelve `(coordenadas, es_multipoligono)`. **Funcion pura**: no toca GEE,
    asi que se puede probar sin autenticarse —construir una `ee.Geometry` exige
    `ee.Initialize()`, porque las clases de geometria se generan a partir del
    catalogo de algoritmos del servidor—.

    Esta separada de `coords_to_geometry` porque es **el contrato con Geocore**,
    y es la parte que se rompe en silencio: Geocore manda `CoordinateDto`, o sea
    `[{lat, lng}]`, y GeoJSON quiere `[lng, lat]`. Invertir el orden no produce
    un error, produce un ROI en otro lugar del planeta.
    """
    # Lista de diccionarios: el `CoordinateDto` de C#, que puede venir en
    # camelCase o en PascalCase segun como este configurado el serializador.
    if isinstance(coordinates, list) and len(coordinates) > 0 and isinstance(coordinates[0], dict):
        anillo = []
        for c in coordinates:
            lng = c.get("lng") if c.get("lng") is not None else c.get("Lng")
            lat = c.get("lat") if c.get("lat") is not None else c.get("Lat")
            anillo.append([lng, lat])
        # GEE rechaza un poligono abierto, y Geocore no garantiza cerrarlo.
        if len(anillo) > 0 and anillo[0] != anillo[-1]:
            anillo.append(anillo[0])
        return [anillo], False

    # Un MultiPolygon tiene un nivel de anidamiento mas que un Polygon.
    es_multi = (
        isinstance(coordinates, list) and len(coordinates) > 0
        and isinstance(coordinates[0], list) and len(coordinates[0]) > 0
        and isinstance(coordinates[0][0], list) and len(coordinates[0][0]) > 0
        and isinstance(coordinates[0][0][0], list)
    )
    return coordinates, es_multi


def coords_to_geometry(coordinates) -> ee.Geometry:
    """Arma la `ee.Geometry` del ROI. Requiere `ee.Initialize()` previo."""
    coords, es_multi = normalizar_coordenadas(coordinates)
    if es_multi:
        return ee.Geometry.MultiPolygon(coords)
    return ee.Geometry.Polygon(coords)

def _with_job_tracking(func):
    def wrapper(ctx: inngest.Context, step: inngest.StepSync) -> dict:
        # Normalize payload keys (first letter lowercase) to match Python expectations
        payload = {k[:1].lower() + k[1:] if isinstance(k, str) else k: v for k, v in ctx.event.data.items()}
        job_id = payload.get("jobId")

        # F.18: todo lo que se loguee de aca para adentro lleva con que ejecucion
        # y con que entidad pasó — incluidos los logs de `storage_service`,
        # `db_repository` y `gee_download`, que son los que fallan de verdad y
        # que no tienen forma de conocer este contexto por su cuenta.
        #
        # `run_id` es el identificador que asigna Inngest y el unico que permite
        # juntar los intentos de una misma ejecucion en el panel y en el log.
        #
        # `seguimiento` hace lo mismo para la bitacora del job: `reportar()` y
        # `paso()` saben a que job y en que intento escriben sin que cada
        # handler tenga que pasarlos (`services/avance_job.py`).
        with contexto_de_ejecucion(
            run_id=getattr(ctx, "run_id", None),
            attempt=ctx.attempt,
            funcion=func.__name__,
            job_id=job_id,
            tenant_id=payload.get("tenantId"),
            parcela_id=payload.get("parcelaId"),
            rancho_id=payload.get("ranchoId"),
        ), seguimiento(job_id, ctx.attempt, RETRIES):
            return _correr_con_estado(func, ctx, step, payload, job_id)

    wrapper.__name__ = func.__name__
    return wrapper


def _correr_con_estado(func, ctx, step, payload, job_id):
    """El cuerpo de `_with_job_tracking`, ya dentro del contexto de logging.

    Los eventos `inicio` y `fin` de la bitacora van **dentro** de los steps que
    cambian el estado: quedan memoizados con el, y se escriben una vez por
    ejecucion aunque Inngest vuelva a correr este cuerpo en cada request.
    """
    if job_id:
        def _mark_running():
            update_processing_job(job_id, "running", started_at_now=True)
            reportar("inicio", "El worker tomó el job", progreso=1, funcion=func.__name__)
        step.run("mark-job-running", _mark_running)

    try:
        res = func(ctx, step, payload)
        if job_id:
            def _mark_completed():
                update_processing_job(job_id, "completed", progress=100, finished_at_now=True)
                reportar("fin", "Terminó sin errores", progreso=100)
            step.run("mark-job-completed", _mark_completed)
        return res
    except Exception as e:
        # El mensaje se captura **ahora**, no dentro de la clausura: Python
        # borra `e` al salir del bloque `except`, asi que una clausura que
        # lo referencie funciona solo mientras se llame aca adentro. Hoy es
        # el caso, pero es una trampa que espera a que alguien mueva la
        # linea. Ruff lo marcaba como F821/F841.
        #
        # Y va resumido: termina en `error_message`, que
        # `GET /api/processing/jobs/{id}` le devuelve al usuario del tenant, y
        # el crudo puede traer una URL prefirmada o un host privado. El crudo
        # va al log.
        mensaje = resumir_error(e)

        if job_id and es_definitivo(e, ctx.attempt, RETRIES):
            # E.4: **solo un error definitivo marca `failed`.**
            #
            # Antes se marcaba en cada intento, y el reintento lo volvia a
            # poner en `running`. Con `retries=3` eso significa que el
            # estado que ve el usuario miente durante toda la ventana de
            # reintentos: un job que se va a recuperar solo aparece como
            # fallido, y quien lo mire va a diagnosticar un problema que no
            # existe. `failed` tiene que significar "no se va a recuperar".
            #
            # "Definitivo" no es solo el ultimo intento: ver `es_definitivo`.
            # Mirar solo `attempt` dejaba en `running` para siempre a un
            # `NonRetriableError`, que Inngest no reintenta.
            logger.error("Job %s fallo y no se va a reintentar: %s", job_id, e)

            def _mark_failed():
                update_processing_job(job_id, "failed", error_message=mensaje,
                                      finished_at_now=True)
                reportar("fin", f"Falló y no se va a reintentar: {mensaje}", nivel=ERROR)
            step.run("mark-job-failed", _mark_failed)
        elif job_id:
            logger.warning(
                "Job %s fallo en el intento %s de %s; Inngest va a "
                "reintentar, asi que el estado sigue en `running`: %s",
                job_id, ctx.attempt + 1, RETRIES + 1, e,
            )
            # Aca solo llegan errores del cuerpo del handler, fuera de los
            # steps: los de un step los registra `paso()` desde adentro
            # (`avance_job`, regla 2). Cada request que falla aca es un intento
            # real, asi que la linea no se duplica en los replays.
            reportar("reintento",
                     f"Falló en el intento {ctx.attempt + 1} de {RETRIES + 1}; "
                     f"Inngest lo va a reintentar: {mensaje}",
                     nivel=AVISO)
        raise


# El historico de una parcela nueva se procesa **por ventanas**, cada una en su
# propio step. Antes eran dos llamadas enteras a GEE —730 dias de fechas y 365 de
# serie— y el panel no tenia forma de saber por donde iba. Por ventanas:
#
#   - la bitacora dice que rango de fechas se esta procesando;
#   - un reintento repite una ventana, no los dos años: Inngest memoiza cada step;
#   - la serie ya no se trunca. `get_sentinel2_time_series` corta en 30 imagenes
#     **ordenadas de la mas vieja a la mas nueva**, asi que en un año con mas de
#     30 pasadas utiles se perdian los ultimos meses — justo los que importan.
#     Un mes tiene a lo sumo ~12.
DIAS_HISTORICO = 730
VENTANAS_HISTORICO = 8  # trimestres
DIAS_SERIE = 365
VENTANAS_SERIE = 12  # meses


def ventanas(desde: date, hasta: date, n: int) -> list[tuple[str, str]]:
    """Parte `[desde, hasta)` en `n` ventanas contiguas, como strings ISO.

    Sin huecos ni solapes: el `hasta` de una es el `desde` de la siguiente, y el
    `filterDate` de GEE es semiabierto —incluye el inicio, excluye el fin—, asi
    que ninguna imagen cae en dos ventanas ni entre dos.
    """
    total = (hasta - desde).days
    cortes = [desde + timedelta(days=round(total * i / n)) for i in range(n + 1)]
    return [(cortes[i].isoformat(), cortes[i + 1].isoformat()) for i in range(n)]


def _entre(inicio: float, fin: float, hechas: int, total: int) -> float:
    """Avance de la barra cuando `hechas` de las `total` partes de una etapa terminaron."""
    return inicio + (fin - inicio) * hechas / total


def _ms_desde(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _escribir_serie(parcela_id: str, tenant_id: str, puntos) -> int:
    # E.7: en lote. Una serie anual son ~70 fechas, y de a una eran ~70
    # conexiones al pool con su commit —o sea su fsync— cada una. Ademas es
    # atomico: entran todas o ninguna.
    #
    # Se cuenta lo escrito, no lo recibido: GEE devuelve fechas sin valor
    # cuando la nube tapo la parcela y esas no llegan a la tabla.
    return insert_measurements(
        {"parcela_id": parcela_id, "indice": "ndvi", "fecha": p["date"],
         "tenant_id": tenant_id, "valor": p.get("mean")}
        for p in puntos
    )


@inngest_client.create_function(
    fn_id="process-parcela",
    trigger=inngest.TriggerEvent(event="terra/parcela.created"),
    retries=RETRIES,
)
@_with_job_tracking
def process_parcela(ctx: inngest.Context, step: inngest.StepSync, payload: dict) -> dict:
    parcela_id = payload["parcelaId"]
    # `ranchoId` llega en el evento y no se usa aca a proposito: la capa de una
    # parcela se registra con `parcela_id`, y meter el rancho fue el bug que
    # dejo `rancho_id` en NULL en las tres filas del 2026-08-10.
    tenant_id = payload["tenantId"]
    coordinates = payload["coordinates"]

    roi = coords_to_geometry(coordinates)

    # 0. "Hoy" se fija en un step. El cuerpo del handler corre de nuevo en cada
    # request de Inngest, y un `datetime.now()` suelto daria un "hoy" distinto
    # en cada una: si el run cruza la medianoche —o un reintento espera horas—
    # las ventanas de los steps que faltan se correrian un dia respecto de las
    # ya memoizadas, y quedaria un dia sin procesar o uno procesado dos veces.
    def _planificar():
        hoy = datetime.now(timezone.utc).date()
        desde_hist = hoy - timedelta(days=DIAS_HISTORICO)
        desde_serie = hoy - timedelta(days=DIAS_SERIE)
        reportar(
            "plan-historico",
            f"Histórico de {DIAS_HISTORICO} días ({desde_hist} → {hoy}) en "
            f"{VENTANAS_HISTORICO} trimestres, y serie NDVI de {DIAS_SERIE} días "
            f"({desde_serie} → {hoy}) en {VENTANAS_SERIE} meses",
            progreso=2, desde=desde_hist.isoformat(), hasta=hoy.isoformat(),
        )
        return {"hoy": hoy.isoformat()}
    hoy = date.fromisoformat(paso(step, "plan-historico", _planificar)["hoy"])

    # 1. Fechas con imagen Sentinel-2 de los ultimos dos años, por trimestre.
    fechas = []
    ventanas_hist = ventanas(hoy - timedelta(days=DIAS_HISTORICO), hoy, VENTANAS_HISTORICO)
    for i, (desde, hasta) in enumerate(ventanas_hist, start=1):
        # Los valores del bucle entran como defaults: una clausura comun veria
        # los de la ultima vuelta.
        def _consultar_fechas(i=i, desde=desde, hasta=hasta):
            etapa, n = f"query-sentinel2-dates-{i}", VENTANAS_HISTORICO
            reportar(etapa, f"Buscando imágenes Sentinel-2, trimestre {i} de {n}: {desde} → {hasta}",
                     progreso=_entre(2, 30, i - 1, n), desde=desde, hasta=hasta, ventana=f"{i}/{n}")
            t0 = time.monotonic()
            init_ee()
            encontradas = get_sentinel2_dates(roi, desde, hasta, cloud_pct=100)
            for d in encontradas:
                insert_sentinel2_date(
                    geometry_id=parcela_id,
                    user_id=tenant_id,
                    date=d.get("date"),
                    system_time_start=d.get("system_time_start"),
                    cloud_cover=d.get("cloud_cover"),
                    tile_id=d.get("tile_id")
                )
            reportar(etapa, f"Trimestre {i} de {n}: {len(encontradas)} imágenes ({desde} → {hasta})",
                     progreso=_entre(2, 30, i, n), desde=desde, hasta=hasta, ventana=f"{i}/{n}",
                     imagenes=len(encontradas), ms=_ms_desde(t0))
            return {"dates": encontradas}
        fechas.extend(paso(step, f"query-sentinel2-dates-{i}", _consultar_fechas)["dates"])

    # 2. Mapa NDVI de la fecha mas reciente con imagen.
    def _generate_heatmap():
        etapa = "generate-recent-heatmap"
        # Las ventanas van en orden y cada una sale ordenada por fecha: la
        # ultima fecha de la lista es la mas reciente.
        if fechas:
            recent_date = fechas[-1].get("date") if isinstance(fechas[-1], dict) else str(fechas[-1])
            reportar(etapa, f"Generando el mapa NDVI de la fecha más reciente con imagen: {recent_date}",
                     progreso=31, fecha=recent_date)
        else:
            recent_date = hoy.isoformat()
            reportar(etapa, f"No hubo imágenes en {DIAS_HISTORICO} días: se intenta el mapa "
                            f"con la fecha de hoy ({recent_date})",
                     progreso=31, nivel=AVISO, fecha=recent_date)
        t0 = time.monotonic()
        init_ee()

        # Using export_heatmap to get the geotiff directly
        path, stats = export_heatmap(
            roi=roi,
            roi_bounds=None,
            index="ndvi",
            start=recent_date,
            end=recent_date,
            cloud_pct=30,
            export_format="geotiff"
        )
        cog_path = convert_to_cog(path)

        object_name, natural_key = claves_de_capa("parcela", parcela_id, "ndvi", recent_date)
        storage_key = get_storage_service().upload_file(object_name, cog_path, "image/tiff")

        # `stats` (min/max/mean/stddev) no se persiste: `layers` no tiene esas
        # columnas. Requiere migracion en Geocore.
        insert_layer(
            natural_key=natural_key,
            product="ndvi",
            storage_key=storage_key,
            acquired_ts=recent_date,
            ingested_ts=datetime.now(timezone.utc),
            tenant_id=tenant_id,
            parcela_id=parcela_id,
            bbox=None,
            source='systematic'
        )
        _borrar_temporales(path, cog_path)
        reportar(etapa, f"Mapa NDVI del {recent_date} subido y registrado como capa",
                 progreso=45, fecha=recent_date, ms=_ms_desde(t0))
        return {"storage_key": storage_key, "recent_date": recent_date}
    heatmap_info = paso(step, "generate-recent-heatmap", _generate_heatmap)

    # 3. Serie NDVI de los ultimos 12 meses, mes por mes.
    total_escritas = 0
    ventanas_serie = ventanas(hoy - timedelta(days=DIAS_SERIE), hoy, VENTANAS_SERIE)
    for i, (desde, hasta) in enumerate(ventanas_serie, start=1):
        def _serie_del_mes(i=i, desde=desde, hasta=hasta):
            etapa, n = f"compute-time-series-{i}", VENTANAS_SERIE
            reportar(etapa, f"Serie NDVI, mes {i} de {n}: {desde} → {hasta}",
                     progreso=_entre(45, 98, i - 1, n), desde=desde, hasta=hasta, ventana=f"{i}/{n}")
            t0 = time.monotonic()
            init_ee()
            # `rescate=False`: aceptar nubes hasta 90 % se decide sobre el año
            # entero (paso 4), como antes. Mes por mes, cualquier mes nublado
            # caeria al 90 % y la serie mezclaria dos criterios de calidad.
            puntos = generate_time_series_data(roi, desde, hasta, "ndvi", cloud_pct=30, rescate=False)
            escritas = _escribir_serie(parcela_id, tenant_id, puntos)
            reportar(etapa, f"Mes {i} de {n}: {escritas} fechas con valor ({desde} → {hasta})",
                     progreso=_entre(45, 98, i, n), desde=desde, hasta=hasta, ventana=f"{i}/{n}",
                     escritas=escritas, ms=_ms_desde(t0))
            return {"count": escritas}
        total_escritas += paso(step, f"compute-time-series-{i}", _serie_del_mes)["count"]

    # 4. El rescate de antes, sobre el año entero: si ningun mes dio un valor
    # con menos de 30 % de nubes, se acepta hasta 90 %. `limit=100` porque es
    # un año entero y el tope de 30 lo volveria a truncar.
    if total_escritas == 0:
        def _rescate():
            etapa = "compute-time-series-rescate"
            desde, hasta = ventanas_serie[0][0], ventanas_serie[-1][1]
            reportar(etapa, f"Ningún mes tuvo un valor con menos de 30 % de nubes: se reintenta "
                            f"el año entero ({desde} → {hasta}) aceptando hasta 90 %",
                     progreso=98, nivel=AVISO, desde=desde, hasta=hasta)
            t0 = time.monotonic()
            init_ee()
            puntos = generate_time_series_data(roi, desde, hasta, "ndvi", cloud_pct=30, limit=100)
            escritas = _escribir_serie(parcela_id, tenant_id, puntos)
            reportar(etapa, f"Rescate: {escritas} fechas con valor en el año",
                     progreso=99, nivel=INFO if escritas else AVISO,
                     desde=desde, hasta=hasta, escritas=escritas, ms=_ms_desde(t0))
            return {"count": escritas}
        total_escritas += paso(step, "compute-time-series-rescate", _rescate)["count"]

    return {"status": "success", "heatmap": heatmap_info, "ts_count": total_escritas}

@inngest_client.create_function(
    fn_id="process-rancho",
    trigger=inngest.TriggerEvent(event="terra/rancho.created"),
    retries=RETRIES,
)
@_with_job_tracking
def process_rancho(ctx: inngest.Context, step: inngest.StepSync, payload: dict) -> dict:
    rancho_id = payload["ranchoId"]
    tenant_id = payload["tenantId"]
    coordinates = payload["coordinates"]
    
    # E.3: descarga, conversion y subida van en **un solo step**.
    #
    # Antes eran tres, y `_download_gee` devolvia `temp_raw_path` —una ruta del
    # disco local— que `_convert_cog` consumia y borraba. Un step de Inngest
    # memoiza lo que devuelve: si el segundo reintentaba, el primero contestaba
    # con el JSON guardado y el archivo ya no estaba. Reintento envenenado, y el
    # unico modo de fallo que `retries=3` no puede resolver.
    #
    # La regla que queda: **entre steps solo cruzan referencias durables**
    # —una key de MinIO, un id— nunca estado del sistema de archivos local. Lo
    # que sale de aca es la `storage_key`, que sigue existiendo despues de que
    # el proceso muera.
    #
    # El costo: un reintento vuelve a bajar de GEE. Es aceptable —y es lo que ya
    # pasaba de hecho, porque el reintento no funcionaba— y la alternativa
    # (subir el crudo a MinIO para pasarlo entre steps) es exactamente el
    # `_original.tif` que `DECISIONS #19` descarto: no era dato crudo sino el
    # mismo indice antes de convertirse a COG, nunca se registro en `layers`, y
    # duplicaba almacenamiento sin aportar nada. Eso cierra E.8.
    def _ingest_rancho_raster():
        etapa = "ingest-rancho-raster"
        init_ee()
        roi = coords_to_geometry(coordinates)
        end_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        start_date = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%d")
        reportar(etapa, f"Buscando el compuesto NDVI de los últimos 30 días: {start_date} → {end_date}",
                 progreso=10, desde=start_date, hasta=end_date)
        t0 = time.monotonic()

        index = "ndvi"
        img = compute_sentinel2_index(roi, start_date, end_date, index, cloud_pct=30)
        if img is None:
            raise inngest.NonRetriableError(
                f"No se encontraron imágenes útiles para el rancho {rancho_id} "
                f"entre {start_date} y {end_date}.")

        band, _ = index_band_and_vis(index, satellite="sentinel2")
        layer = img.select(band).clip(roi)

        try:
            acquired_ms = img.get('system:time_start').getInfo()
            fecha_captura = datetime.fromtimestamp(acquired_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
        except Exception:
            fecha_captura = end_date
        reportar(etapa, f"Imagen del {fecha_captura}: descargando el GeoTIFF de GEE",
                 progreso=25, fecha=fecha_captura)

        temp_raw_path = None
        cog_path = None
        try:
            fd, temp_raw_path = tempfile.mkstemp(suffix=".tif")
            os.close(fd)

            descargar_geotiff(layer, roi, temp_raw_path)
            megas = round(os.path.getsize(temp_raw_path) / 1_000_000, 2)
            reportar(etapa, f"GeoTIFF descargado ({megas} MB): convirtiendo a COG y subiendo",
                     progreso=60, megas=megas)

            import rasterio
            with rasterio.open(temp_raw_path) as src:
                bounds = list(src.bounds)
                crs = src.crs.to_string() if src.crs else "EPSG:4326"

            cog_path = convert_to_cog(temp_raw_path)
            object_name, _ = claves_de_capa("rancho", rancho_id, index, fecha_captura)
            storage_key = get_storage_service().upload_file(object_name, cog_path, "image/tiff")
        finally:
            # En `finally` para que un fallo a mitad no deje el temporal en
            # disco: el proceso es de larga vida y los reintentos se acumulan.
            _borrar_temporales(temp_raw_path, cog_path)

        reportar(etapa, f"Ráster del rancho listo: imagen del {fecha_captura}",
                 progreso=90, fecha=fecha_captura, ms=_ms_desde(t0))
        return {"storage_key": storage_key, "fecha_captura": fecha_captura,
                "bbox": bounds, "crs": crs}

    gee_info = paso(step, "ingest-rancho-raster", _ingest_rancho_raster)
    upload_info = gee_info

    # E.1: acá había un step `_calculate_measurements` que calculaba NDVI por
    # parcela. Se borró junto con `services/geocore_client.py`, que era un
    # simulacro: partía el bbox del rancho en dos mitades e inventaba las
    # parcelas con ids `{ranchoId}-parcela-A` y `-B`, contra una columna `uuid`.
    # Reventaba con `invalid input syntax for type uuid` y dejaba a
    # `process_rancho` sin poder terminar nunca.
    #
    # No se reemplazó por la llamada HTTP real a Geocore porque el caso ya está
    # cubierto: Geocore emite `terra/parcela.created` por cada parcela y
    # `process_parcela` la procesa con su id y su geometría de verdad. El step
    # además pedía a GEE un segundo composite del mismo período que el de
    # arriba, gastando cuota para recalcular lo mismo.
    #
    # `process_rancho` queda con una sola responsabilidad: el ráster a nivel
    # rancho. Las mediciones son por parcela y llegan por su propio evento.
    
    step.send_event("emit-raster-ingested", inngest.Event(
        name="terra/raster.ingested",
        data={
            "ranchoId": rancho_id, "tenantId": tenant_id, "cogKey": upload_info["storage_key"],
            "fecha": gee_info["fecha_captura"], "bbox": gee_info["bbox"], "crs": gee_info["crs"], "index": "ndvi"
        }
    ))
    
    return {"status": "success", "ranchoId": rancho_id, "cogKey": upload_info["storage_key"]}

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
        dates = get_sentinel2_dates(roi, payload["fechaInicio"], payload["fechaFin"], payload.get("cloudPct", 30))
        for d in dates:
            insert_sentinel2_date(
                geometry_id=payload['parcelaId'],
                user_id=payload['tenantId'],
                date=d.get("date"),
                system_time_start=d.get("system_time_start"),
                cloud_cover=d.get("cloud_cover"),
                tile_id=d.get("tile_id")
            )
        return {"dates": dates}
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

@inngest_client.create_function(
    fn_id="register-layer",
    trigger=inngest.TriggerEvent(event="terra/raster.ingested"),
    retries=RETRIES,
)
@_with_job_tracking
def register_layer(ctx: inngest.Context, step: inngest.StepSync, payload: dict) -> dict:
    def _register_db():
        # La `natural_key` sale de `claves_de_capa`, la misma funcion que uso
        # `process_rancho` para la `storage_key`. Estaban armadas a mano en los
        # dos handlers: iguales por coincidencia, y separadas por un evento —el
        # peor lugar para que una convencion se desincronice, porque el fallo
        # seria una fila de `layers` que apunta a un objeto y otra huerfana.
        _, asset_id = claves_de_capa(
            "rancho", payload['ranchoId'], payload['index'], payload['fecha'],
        )
        # Capa a nivel rancho: va en `rancho_id`, no en `parcela_id`.
        insert_layer(
            natural_key=asset_id,
            product=payload['index'], storage_key=payload['cogKey'],
            acquired_ts=payload['fecha'],
            ingested_ts=datetime.now(timezone.utc),
            tenant_id=payload['tenantId'], rancho_id=payload['ranchoId'],
            bbox=payload['bbox'], source='systematic'
        )
        return {"asset_id": asset_id}
    res = step.run("register-layer-in-geodata", _register_db)
    return res

all_functions = [
    process_parcela,
    process_rancho,
    generate_heatmap_on_demand,
    compute_timeseries,
    query_available_dates,
    export_data,
    compute_parcela_stats,
    register_layer
]
