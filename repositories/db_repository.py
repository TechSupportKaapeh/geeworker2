import logging
import os
import uuid
from datetime import date, datetime, timezone
from psycopg2.extras import Json, execute_values
from typing import Optional

logger = logging.getLogger(__name__)

_pool = None

def get_connection():
    global _pool
    if _pool is None:
        db_host = os.getenv("DB_HOST", "localhost")
        db_port = os.getenv("DB_PORT", "5432")
        db_name = os.getenv("DB_NAME", "terra")
        db_user = os.getenv("DB_USER", "postgres")
        db_password = os.getenv("DB_PASSWORD", "postgres")
        
        from psycopg2.pool import SimpleConnectionPool
        _pool = SimpleConnectionPool(
            1, 20,
            host=db_host,
            port=db_port,
            database=db_name,
            user=db_user,
            password=db_password,
            options="-c search_path=geodata,public"
        )
    return _pool.getconn()

def release_connection(conn):
    if _pool and conn:
        _pool.putconn(conn)

def a_timestamptz(valor, campo: str):
    """Normaliza una fecha a `datetime` **aware en UTC**, para columnas `timestamptz`.

    **Por qué existe** (`PLAN.md` E.6). Las tres columnas de fecha que escribe el
    worker son `timestamptz` — verificado en el codigo de Geocore, que es quien
    define el esquema:

        Layer.AcquiredTs   : DateTimeOffset   -> layers.acquired_ts
        Layer.CreatedAt    : DateTimeOffset   -> layers.created_at
        Measurement.Fecha  : DateTimeOffset   -> measurements.fecha

    Mandarles un string como `"2026-01-01"` deja que Postgres haga el cast, y ese
    cast **usa el `TimeZone` de la sesion**. Con un server fuera de UTC la fecha
    se corre.

    🔴 **Y `fecha` esta en la primary key de `measurements`**
    (`HasKey(m => new { m.ParcelaId, m.Indice, m.Fecha })`). Dos escrituras de la
    misma fecha logica desde sesiones con husos distintos producen **dos
    instantes distintos**, o sea **dos filas**, y el `ON CONFLICT` no colapsa
    ninguna: rompe la idempotencia que `ARCHITECTURE_PLAN` §5 exige. No es solo
    que las fechas se corran un dia.

    **Una fecha sin hora se ancla a medianoche UTC, a proposito.** Se podria
    conservar el instante real de la pasada —GEE lo da en `system:time_start`—
    pero justamente porque `fecha` esta en la PK conviene que sea
    **determinista**: si GEE reportara el mismo dia con milisegundos distintos,
    serian dos filas. Anclar al dia mantiene una fila por (parcela, indice, dia),
    que es lo que la serie temporal necesita.

    `sentinel2_dates."Date"` **no pasa por aca**: es `text` en la tabla propia
    del worker, y ahi el string es el tipo correcto.
    """
    if valor is None:
        return None
    if isinstance(valor, datetime):
        # Un datetime naive se interpreta como UTC en vez de rechazarse: todo lo
        # que produce el worker viene de `datetime.now(timezone.utc)` o de GEE,
        # que trabaja en UTC. Asumirlo aca es correcto y evita que un naive caiga
        # al cast de Postgres, que es el bug que esta funcion existe para cerrar.
        return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)
    if isinstance(valor, date):
        return datetime(valor.year, valor.month, valor.day, tzinfo=timezone.utc)
    if isinstance(valor, str):
        try:
            parseado = datetime.fromisoformat(valor.replace("Z", "+00:00"))
        except ValueError as e:
            raise ValueError(
                f"{campo}: '{valor}' no es una fecha ISO. Se espera 'YYYY-MM-DD' "
                f"o un instante ISO-8601"
            ) from e
        return parseado if parseado.tzinfo else parseado.replace(tzinfo=timezone.utc)
    raise TypeError(f"{campo}: no se puede convertir {type(valor).__name__} a timestamptz")


def update_processing_job(job_id: str, status: str, progress: int = None, error_message: str = None,
                          started_at_now: bool = False, finished_at_now: bool = False):
    """Actualiza el estado de un job que creo Geocore.

    `progress` es `integer` en el esquema, no float: se redondea antes de mandarlo
    para que un 33.3 no reviente el INSERT con un error de tipo.
    """
    if not job_id:
        return
    conn = get_connection()
    try:
        cur = conn.cursor()

        updates = ['status = %s']
        params = [status]

        if progress is not None:
            updates.append('progress = %s')
            params.append(int(round(progress)))

        if error_message is not None:
            updates.append('error_message = %s')
            params.append(error_message)

        if started_at_now:
            updates.append('started_at = now()')

        if finished_at_now:
            updates.append('finished_at = now()')

        query = f'UPDATE processing_jobs SET {", ".join(updates)} WHERE id = %s'
        params.append(job_id)
        
        cur.execute(query, tuple(params))
        conn.commit()
    except Exception as e:
        # No se relanza a proposito: el estado del job es telemetria, no el
        # trabajo. Perder la actualizacion no debe abortar un procesamiento que
        # ya corrio. Pero se loguea como ERROR y no con un print, para que se
        # vea en el log estructurado del deploy.
        logger.error("No se pudo actualizar el job %s: %s", job_id, e)
    finally:
        release_connection(conn)


def init_db():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute("SELECT 1")
        # Create sentinel2_dates since EF Core doesn't manage it
        cur.execute('''
        CREATE TABLE IF NOT EXISTS sentinel2_dates (
            "Id" serial PRIMARY KEY,
            "GeometryId" text,
            "UserId" text,
            "Date" text,
            "SystemTimeStart" bigint,
            "CloudCover" double precision,
            "TileId" text,
            "RoiGeojson" jsonb,
            UNIQUE("GeometryId", "Date", "SystemTimeStart")
        )
        ''')
        conn.commit()
    except Exception as e:
        logger.error("No se pudo inicializar la DB: %s", e)
    finally:
        release_connection(conn)


def insert_layer(natural_key: str, product: str, storage_key: str, acquired_ts: str,
                 ingested_ts: str, tenant_id: str, parcela_id: str = None,
                 rancho_id: str = None, bbox: Optional[list] = None,
                 source: str = 'systematic'):
    """Registra una capa raster en `geodata.layers` (tabla que administra EF Core).

    `natural_key` no se persiste: siembra un UUIDv5 determinista que se usa como
    PK. Reprocesar el mismo (indice, entidad, fecha) reescribe la misma fila en
    vez de duplicarla — es la idempotencia que exige ARCHITECTURE_PLAN §5, porque
    Inngest reintenta y Geocore puede publicar el mismo evento dos veces.

    Una capa es de rancho o de parcela, nunca de las dos: la tabla tiene columnas
    separadas y ambas son nullable. Pasar la que corresponda.

    Nota: `layers` no tiene columnas para las estadisticas del raster (min/max/
    mean/stddev) ni para sensor, epsg, resolucion o cog_ok. Persistirlas requiere
    una migracion en Geocore; hasta entonces no hay donde guardarlas.
    """
    if not parcela_id and not rancho_id:
        raise ValueError("insert_layer requiere parcela_id o rancho_id")

    conn = get_connection()
    try:
        cur = conn.cursor()

        # `bbox` llega como [minx, miny, maxx, maxy] (el orden de rasterio.bounds);
        # la columna es geometry, asi que se arma el rectangulo cerrado en WKT.
        bbox_wkt = None
        if bbox and len(bbox) == 4:
            minx, miny, maxx, maxy = bbox
            bbox_wkt = f"POLYGON(({minx} {miny}, {minx} {maxy}, {maxx} {maxy}, {maxx} {miny}, {minx} {miny}))"

        layer_uuid = str(uuid.uuid5(uuid.NAMESPACE_OID, natural_key))

        cur.execute('''
        INSERT INTO layers(id, product, storage_key, acquired_ts, created_at, bbox,
                           tenant_id, parcela_id, rancho_id, source)
        VALUES (%s, %s, %s, %s, %s, ST_GeomFromText(%s, 4326), %s, %s, %s, %s)
        ON CONFLICT (id) DO UPDATE SET
            product = EXCLUDED.product,
            storage_key = EXCLUDED.storage_key,
            acquired_ts = EXCLUDED.acquired_ts,
            created_at = EXCLUDED.created_at,
            bbox = EXCLUDED.bbox,
            tenant_id = EXCLUDED.tenant_id,
            parcela_id = EXCLUDED.parcela_id,
            rancho_id = EXCLUDED.rancho_id,
            source = EXCLUDED.source
        ''', (
            layer_uuid, product, storage_key,
            a_timestamptz(acquired_ts, "acquired_ts"),
            a_timestamptz(ingested_ts, "ingested_ts"),
            bbox_wkt, tenant_id, parcela_id, rancho_id, source
        ))
        conn.commit()
        return layer_uuid
    finally:
        release_connection(conn)


def insert_measurement(parcela_id: str, indice: str, fecha: str, tenant_id: str,
                       valor: float, min_val: float = None, max_val: float = None) -> bool:
    """Persiste una medicion agregada en `geodata.measurements`.

    El ON CONFLICT apunta a la PK real (parcela_id, indice, fecha), asi que
    reprocesar la misma fecha actualiza en vez de duplicar (contrato de
    idempotencia, ARCHITECTURE_PLAN §5).

    `valor` es NOT NULL en el esquema, pero GEE devuelve None para fechas donde
    la nube tapo la parcela. Esas se saltean: un hueco en la serie es correcto,
    y dejarlas pasar aborta el step entero por una fecha sin dato.

    Devuelve True si se escribio la fila.

    Nota: `measurements` no tiene columnas para stddev, quality ni source.
    """
    if valor is None:
        logger.debug("medicion sin valor (parcela=%s indice=%s fecha=%s), se saltea",
                     parcela_id, indice, fecha)
        return False

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
        INSERT INTO measurements(parcela_id, indice, fecha, tenant_id, valor, min_val, max_val)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (parcela_id, indice, fecha) DO UPDATE SET
            tenant_id = EXCLUDED.tenant_id,
            valor = EXCLUDED.valor,
            min_val = EXCLUDED.min_val,
            max_val = EXCLUDED.max_val
        ''', (parcela_id, indice, a_timestamptz(fecha, "fecha"), tenant_id,
              valor, min_val, max_val))
        conn.commit()
        return True
    finally:
        release_connection(conn)


def insert_measurements(mediciones) -> int:
    """Persiste varias mediciones en **una sola conexion y un solo round-trip**.

    `mediciones` es un iterable de dicts con las claves de `insert_measurement`.
    Devuelve cuantas filas se escribieron.

    **Por que existe** (`PLAN.md` E.7): `insert_measurement` pide una conexion
    al pool, hace un INSERT, commitea y la devuelve. Una serie anual son ~70
    fechas, o sea 70 ciclos de eso — y cada `commit` es un `fsync` del lado del
    servidor. Contra una base gestionada, con la latencia de red por medio, es
    la diferencia entre un step de segundos y uno de minutos.

    Ademas es **atomico**: las 70 filas entran o no entra ninguna. Antes, un
    fallo en la fila 40 dejaba media serie escrita y el reintento de Inngest la
    completaba — el `ON CONFLICT` lo salvaba, pero el estado intermedio existia.

    Las filas sin `valor` se saltean, con el mismo criterio que la version de a
    una: la columna es NOT NULL y GEE devuelve `None` cuando la nube tapo la
    parcela. Un hueco en la serie es correcto; abortar el step por una fecha sin
    dato, no.
    """
    filas = [
        (m["parcela_id"], m["indice"], a_timestamptz(m["fecha"], "fecha"),
         m["tenant_id"], m["valor"], m.get("min_val"), m.get("max_val"))
        for m in mediciones
        if m.get("valor") is not None
    ]
    if not filas:
        return 0

    conn = get_connection()
    try:
        cur = conn.cursor()
        execute_values(cur, '''
        INSERT INTO measurements(parcela_id, indice, fecha, tenant_id, valor, min_val, max_val)
        VALUES %s
        ON CONFLICT (parcela_id, indice, fecha) DO UPDATE SET
            tenant_id = EXCLUDED.tenant_id,
            valor = EXCLUDED.valor,
            min_val = EXCLUDED.min_val,
            max_val = EXCLUDED.max_val
        ''', filas)
        conn.commit()
        return len(filas)
    finally:
        release_connection(conn)


def insert_sentinel2_date(geometry_id: str, user_id: str = None, date: str = None,
                           system_time_start: int = None, cloud_cover: float = None,
                           tile_id: str = None, roi_geojson: dict = None):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute('''
        INSERT INTO sentinel2_dates("GeometryId", "UserId", "Date", "SystemTimeStart", "CloudCover", "TileId", "RoiGeojson")
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT ("GeometryId", "Date", "SystemTimeStart") DO NOTHING
        RETURNING "Id"
        ''', (
            geometry_id,
            user_id,
            date,
            system_time_start,
            cloud_cover,
            tile_id,
            Json(roi_geojson) if roi_geojson is not None else None
        ))
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else None
    except Exception:
        return None
    finally:
        release_connection(conn)
