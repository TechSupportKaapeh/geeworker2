# HANDOFF.md — Estado permanente de GeeWorker

> Estado del repo, no crónica. Lo que pasó en cada sesión va en los
> `SESSION_*.md`. Cómo funciona el servicio, en [`FUNCIONAMIENTO.md`](FUNCIONAMIENTO.md).
> Última revisión: **2026-09-12**, dos sesiones. Crónicas:
> [`SESSION_2026-09-12_la_bitacora_del_worker.md`](SESSION_2026-09-12_la_bitacora_del_worker.md)
> y [`SESSION_2026-09-12_primera_corrida_y_el_pipeline_mensual.md`](SESSION_2026-09-12_primera_corrida_y_el_pipeline_mensual.md).
> Para retomar: `geocore/docs/PROXIMA_SESION.md`.
>
> **🎯 Hacia dónde va (2026-09-12, tarde).**
> - El histórico pasa a ser **mensual**, con el compuesto armado en GEE.
> - La capa de satélite se rehace como un pipeline: receta, registros de índices y
>   estadísticas, etapas y un solo borde con GEE.
>
> Diseño: [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md). **Tablero:
> [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md).**
> - `DECISIONS #31`: ✅ decidida (opción B), reemplaza a #19 y #20.
> - `#32`: aceptada.
> - `#33`: recomendada. Se reescribe la capa de satélite, no el servicio.
>
> **La primera corrida real falló** con `CardinalityViolation` en el mes 1 de la
> serie: dos imágenes del mismo día en un lote. Está arreglado (`DECISIONS #30`).
>
> **Novedades del 2026-09-12:** cada job escribe su bitácora
> (`processing_job_events`, `DECISIONS #29`) y su `progress`. El histórico de una
> parcela se procesa por ventanas: 8 trimestres de fechas y 12 meses de serie.
> Salieron y se arreglaron tres bugs:
>
> - los fallos de un step eran invisibles para el wrapper;
> - un `NonRetriableError` dejaba el job en `running` para siempre;
> - la serie anual se truncaba en 30 imágenes.
>
> **Necesita la migración `ProcessingJobEvents` de Geocore aplicada.** Sin ella
> pausa la bitácora, pero no rompe nada.
>
> **Novedades desde el 2026-09-07:** el flujo corre de punta a punta (Inngest
> registrado, firma verificada, COG subido y servido). La contraseña de
> `geodata` está resuelta, así que §5b-bis quedó histórica. El worker ahora
> reporta su configuración y verifica sus conexiones al arrancar
> (`DECISIONS #27`). Variables de Railway, versión final: sesión del 09-11, §8.

---

## 1. Qué es este servicio

Worker de rásters del ecosistema Terra. **Se dispara por eventos de Inngest**,
no por HTTP de negocio. Consume geometría de Geocore, calcula índices sobre
Sentinel-2 en Google Earth Engine, sube los COG a MinIO y escribe métricas y
catálogo en la base `geodata`.

Su única superficie HTTP es `/health` y `/api/inngest`. No expone API de lectura
— eso lo sirve Geocore, que ya aplica aislamiento de tenant.

---

## 2. Estado por pieza

| Pieza | Estado |
|---|---|
| Escrituras a `geodata` (`layers`, `measurements`, `processing_jobs`) | ✅ SQL alineado con el esquema real, verificado con `PREPARE` |
| `storage_key` como key pelada, bucket único | ✅ Verificado contra el bucket real el 2026-08-26 |
| **Cadena de tiles completa** | ✅ **Primer tile real el 2026-08-26** — ver §2b |
| Composición por MosaicJSON | ✅ Spike verificado el 2026-08-27 (`DECISIONS #20`) |
| **Escrituras del worker contra MinIO real** | ✅ **2026-09-07 — probadas de verdad.** `check_write_path.py` y `check_ingest_real.py` |
| COG del worker validado por `rio-cogeo` | ✅ 2026-09-07 — cerró la verificación que `DECISIONS #22` dejó pendiente |
| Escrituras a `geodata` contra la DB real | ❌ **`password authentication failed`** — ver §5b-bis |
| Paridad de permisos en la subida | ✅ 2026-09-01 — la subida emite solo el `PUT`; lo fija `scripts/check_minio_region.py` |
| Paridad de permisos en el arranque | ✅ 2026-09-02 — `ensure_bucket()` salió del constructor (`DECISIONS #24`) |
| Config de MinIO ausente o incoherente | ✅ Falla cerrado nombrando la variable (`DECISIONS #24`) |
| `process_parcela` | 🟡 Debería funcionar; nunca corrió contra MinIO real |
| `process_rancho` | 🟡 Desbloqueado el 2026-08-30 (E.1). Igual que `process_parcela`: nunca corrió contra MinIO real |
| Handlers on-demand (heatmap, timeseries, dates, export, stats) | 🟡 Igual que `process_parcela` |
| `process_kml` | ❌ Handler muerto: su evento fue eliminado en Geocore |
| Superficie HTTP de lectura (`routes/`, `schemas/`, `auth.py`) | ✅ **Borrada** el 2026-08-30 (`DECISIONS #23`) |
| Firma de Inngest | ✅ 2026-09-04 — se verifica en modo cloud; 401 sin firma (`DECISIONS #25`, `W-8`) |
| Criterio de entorno | ✅ Uno solo (`config.IS_PRODUCTION`); lo desconocido cuenta como producción |
| Correlación en los logs | ✅ 2026-09-07 — `run_id`, `attempt`, `job_id` e ids de entidad en cada línea (F.18) |
| Despliegue del worker | 🟡 `Dockerfile` escrito el 2026-09-07, **sin construir** (FASE H) |
| TLS contra MinIO | ✅ 2026-09-07 — el default se deduce del host; lo desconocido asume TLS (`W-2`) |
| Commits del worker | ✅ Commiteado desde el 2026-08-30, sin pushear |
| **Bitácora de jobs** (`processing_job_events` + `progress`) | 🟡 2026-09-12 — migración aplicada; **corrió contra Inngest y la base real** y mostró cada intento. Falta una corrida que termine bien (`DECISIONS #29`) |
| **Pipeline mensual** | 🟡 2026-09-12 — diseñado ([`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md)), sin empezar. `PLAN.md` FASE M |
| Entorno ejecutable + `pytest` | ✅ `.venv` sobre Python 3.13 (`DECISIONS #22`); **201 tests con `pytest tests`**. La raíz también junta los scripts de `scratch/`, que piden GEE |
| `.venv` == los requirements | ✅ 2026-09-02 — `requirements-dev.txt` con `pytest`, `httpx`, `ruff` y `pip-audit` (F.15) |

## 2b. La cadena de tiles, verificada

Al **2026-08-26** las tres piezas se hablaron por primera vez. Un COG en MinIO,
leído por el tileserver desplegado, con un token firmado por Geocore.

| | |
|---|---|
| Tileserver desplegado | ✅ `terra-tileserver-production.up.railway.app` |
| Convención de `storage_key` contra un bucket real | ✅ `ranchos/{id}/pasadas/{fecha}/ndvi.tif` |
| `MAP_TOKEN_SECRET` idéntico de los dos lados | ✅ 401 sin token, 200 con token |
| `tiler-ro` alcanza para las lecturas de GDAL | ✅ |
| `minio.railway.internal:9000` resuelve | ✅ **con puerto explícito** — ver abajo |
| `Cache-Control` sobre un tile real | ✅ `public, max-age=31536000, immutable` |
| PNG transparente en los bordes | ✅ 68 bytes |

**Se verifica con un comando**, desde el repo del tileserver:

```powershell
python scripts\check_prod.py --base https://<titiler> --token $TOKEN --key <storage_key>
```

Y el estado de un deploy, sin token, con `GET /health/ready`. Los dos están
documentados en el README de `terra-tileserver`.

**El dominio privado de Railway lleva puerto explícito (`:9000`) y va sin TLS;
el público va sin puerto y con TLS.** Confundirlos es el error más repetido de
este despliegue y hoy lo detecta `/health/ready` sin tocar la red.

---

## 3. Contratos con Geocore

Cambiarlos deja de ser trabajo local: no hay compilador que agarre el error.

**Esquema de `geodata` — columnas en snake_case.** Definitivo desde la migración
`20260817165517_GeoDataSnakeCase`. Verificado contra la DB real el 2026-08-19.

```
layers            id, tenant_id, parcela_id, rancho_id, storage_key,
                  product, acquired_ts, bbox, created_at, source
measurements      parcela_id, indice, fecha, tenant_id, valor, min_val, max_val
                  PK: (parcela_id, indice, fecha)
processing_jobs   id, tenant_id, parcela_id, rancho_id, request_type, status,
                  progress, error_message, created_by, created_at,
                  started_at, finished_at
processing_job_events
                  id, job_id, created_at, attempt, stage, level, message, detail
                  (2026-09-12, migracion ProcessingJobEvents; el worker solo inserta)
sentinel2_dates   PascalCase — tabla del worker, no la administra EF Core
```

**La bitácora (2026-09-12).** Cómo se llenan las columnas:

| Columna | Qué lleva |
|---|---|
| `attempt` | Desde 1. |
| `level` | `info`, `warning` o `error`. |
| `stage` | El id del step, o `inicio` / `fin` / `reintento`. |
| `detail` | JSON con `desde`, `hasta`, `imagenes`, `escritas`, `megas`, `ms` y `error`. |

La escribe `registrar_evento_job`. El panel traduce los niveles y los `request_type`: si se cambia algo, avisar en `terra-admin/src/lib/procesos.ts`.

**`storage_key` guarda la key pelada.** Geocore compone
`s3://{GeoData:MinioBucket}/{storage_key}` (`LayersController.cs`). Guardar la
URI completa produce `s3://terra-assets/s3://…` y TiTiler no resuelve nada.

Convención de keys, bucket único `terra-assets`:

```
ranchos/{ranchoId}/{periodo}_{indice}.tif     process_rancho
parcelas/{parcelaId}/{periodo}_{indice}.tif   process_parcela y los on-demand
exports/{parcelaId}/{fecha}_{indice}.{fmt}    export_data (.tif, .png o .csv)
```

`{fecha}` es `YYYY-MM-DD`. **Los cuatro prefijos son planos**: `{entidad}/{id}/`
y el archivo. No hay subcarpeta por fecha.

⚠️ Dos cosas que no coinciden con esto y conviene tener presentes:

- **`ranchos/{id}/{fecha}_original.tif` ya no existe** — se borró con E.8 el
  2026-09-04 (`DECISIONS #19` y `#26`). Si aparece una key así en el bucket, es
  de antes de esa fecha.
- **`heatmaps/` tampoco existe** — se unificó con `parcelas/` el 2026-09-07
  (E.9). Estaba separado por *por qué se pidió* en vez de por *qué es*, y su
  `natural_key` **colisionaba** con la de la capa sistemática, dejando un objeto
  huérfano en el bucket.
- **`{periodo}` es `{fecha}` para una sola pasada y `{inicio}_{fin}` para un
  rango.** Antes la key usaba solo el inicio, así que dos rangos distintos
  escribían el mismo objeto.
- **Las dos claves de una capa salen de `claves_de_capa()`**, no se arman a
  mano: la del bucket y la `natural_key` que siembra el UUIDv5 de `layers`
  tienen que identificar la misma capa.
- **El COG de prueba del 2026-08-26 está en otro layout**:
  `ranchos/{id}/pasadas/{fecha}/ndvi.tif`. Se subió a mano para el primer tile y
  **el worker nunca produce esa forma**. Es el que sigue en el bucket bajo el
  rancho inventado `00000000-…-0001`.

**Eventos consumidos.** `terra/rancho.created` y `terra/parcela.created` disparan
el procesamiento; los 5 `terra/parcela.*.requested` son pedidos puntuales.
Coordenadas como `CoordinateDto`, nunca ValueTuples.

**Desde el 2026-09-12 las altas traen `JobId`**, con `request_type`
`ParcelaInicial` o `RanchoInicial`. Puede venir `null` si Geocore no pudo crear
el job: en ese caso el worker procesa igual y no reporta.

**Idempotencia obligatoria.** Inngest reintenta y el backoff de Geocore puede
publicar dos veces. `layers` usa un UUIDv5 determinista; `measurements`, un
`ON CONFLICT` sobre su PK.

**Tres variables tienen que coincidir entre repos.** Nada las valida al
desplegar, pero desde el 2026-08-26 `GET /health/ready` del tileserver detecta
las dos primeras sin necesidad de un tile:

| GeeWorker | Geocore | TiTiler | Si no coinciden |
|---|---|---|---|
| `MINIO_BUCKET` | `GeoData__MinioBucket` | `MINIO_BUCKET` | 400 `URL_NO_PERMITIDA` por tile |
| — | `GeoData__MapTokenSecret` | `MAP_TOKEN_SECRET` | 401 en todos los tiles |
| — | `GeoData__TiTilerUrl` | — | El front pide tiles a la nada |

**Geocore necesita las dos cadenas de conexión, en formato Npgsql.** `geodata`
no es otra base del mismo servidor: es **otro proyecto de Supabase**.

| Variable | Apunta a | Si falta o está mal |
|---|---|---|
| `ConnectionStrings__Default` | Proyecto principal | **Toda** request autenticada falla: `TenantMiddleware` va a la base antes de cualquier controller |
| `ConnectionStrings__GeoData` | Proyecto de `geodata` | No falla al arrancar: cae a `Default` (`DependencyInjection.cs:25`) y busca `layers` donde no existe |

El formato URI (`postgresql://…`) que dan Supabase y Railway **no lo acepta
Npgsql**: hay que traducirlo a `Host=…;Port=…;Database=…;Username=…;Password=…;SSL Mode=Require`.
Supabase lo ofrece ya convertido en la pestaña `.NET` del diálogo de conexión.

---

## 4. Deuda abierta

**Abierta el 2026-09-12** (sesión del día):

- **`get_sentinel2_dates` hace un `getInfo()` por imagen**: cientos en el
  histórico de dos años. Ahora se ve el avance por trimestre, pero sigue siendo
  lo lento. `aggregate_array` lo haría en una sola llamada.
- **`compute_timeseries` (a demanda) conserva el tope de 30 imágenes**, ordenadas
  de la más vieja: un rango largo pierde el final. `process_parcela` ya no lo
  sufre porque va mes por mes.
- ✅ **Verificado en la primera corrida real: `ctx.attempt` vale 0 en el request
  que recibe un `StepError`.** La línea `fin` queda con `attempt` 1 aunque el step
  haya fallado cuatro veces. El panel ya no la usa para separar intentos.
- **La capa de satélite tiene deuda que el pipeline mensual reemplaza** en vez de
  arreglar:
  - fórmulas duplicadas;
  - `cloud_pct` que no se usa;
  - una SCS+C incompleta;
  - stats y export a CSV con el rango vacío;
  - `try/except` que no pueden disparar.

  Detalle en la sesión del 2026-09-12 (tarde) §2, y en `ARQUITECTURA_PIPELINE.md`
  §8 y §9.

**Cerrado el 2026-08-30** — se deja el registro porque explica qué mirar si algo
de esto reaparece:

- **`get_ranch_parcels` era un simulacro** que inventaba parcelas `-A`/`-B`
  contra una columna `uuid`. Borrado junto con su step y todo
  `services/geocore_client.py` (E.1). `process_rancho` queda con una sola
  responsabilidad: el ráster a nivel rancho.
- **La superficie HTTP de lectura ya no existe** (`DECISIONS #23`). Lo fija
  `tests/test_http_surface.py` como igualdad exacta, no como "contiene".

**Nuevo, salido de esos borrados:**

- **`sentinel2_dates` es un caché de solo escritura.** Se inserta en cada
  pedido y nadie lee la tabla. `PREGUNTAS_ABIERTAS` A-4.
- ✅ **El cliente de MinIO se construía sin `region`** — cerrado el 2026-09-01
  (F.12). Era `DECISIONS #21` otra vez, del lado de escritura: sin ese
  parámetro, la primera operación contra el bucket ejecuta un
  `GetBucketLocation` (`minio/api.py:481`) que exige un permiso **que la subida
  real no usa**, y contra una policy de solo escritura salía como un
  `AccessDenied` sobre el bucket que se lee como un problema de credenciales de
  `worker-rw` que no existe. Hoy la región viene de `AWS_REGION` en `config.py`,
  con el mismo nombre y default que el tileserver, y lo cuida
  `scripts/check_minio_region.py`. **Si A-3 falla, esto ya no es la causa** — se
  comprueba corriendo el script.
- ✅ **`storage_service` le pegaba a MinIO al importarse** — cerrado el
  2026-09-02 (F.13, `DECISIONS #24`). Era un singleton de módulo con
  `ensure_bucket()` en el constructor, así que `import app` abría una conexión.
  Hoy es `get_storage_service()` con `lru_cache`: uno solo —comparte el pool de
  urllib3, que es el motivo correcto— pero construido al primer uso, como
  `db_repository.get_connection()` ya hacía. **La suite pasó de ~33 s con 4
  tests a ~6 s con 24.** Lo fija un test que corre el import en un proceso
  aparte con el socket saboteado.
- ✅ **`ensure_bucket()` ya no corre en cada arranque** (misma decisión). Pedía
  `s3:ListBucket`, otro permiso que la subida real no usa. Sigue existiendo como
  método para el despliegue, y ya no se traga la excepción con un `print`.
- ✅ **Las credenciales de MinIO ya no caen a `minioadmin`** (misma decisión).
  Era la credencial root del `docker-compose`: un deploy sin las variables se
  autenticaba como root en vez de fallar. Es el patrón de default silencioso que
  `DECISIONS #16` eliminó en Geocore y el tileserver en `MAP_TOKEN_SECRET`; este
  era el tercero. **Ojo: `ENVIRONMENT=development` reabre el default.**

**La auditoría del 2026-09-02 y su cierre.** Los ocho hallazgos del worker viven
en [`OWASP_TOP10.md`](OWASP_TOP10.md) como `W-1` a `W-8`, con su estado. Siete
están cerrados; queda uno:

- ✅ **`W-6` `object_exists()` mezclaba "no existe" con "no pude averiguar"** —
  cerrado el 2026-09-04. Hoy devuelve `False` **solo** ante `NoSuchKey`; todo lo
  demás se propaga y lo reintenta Inngest. Importaba por **E.9**, que quiere
  saltear el recálculo si la capa ya existe: con el `False` viejo, un error de
  permisos o de red disparaba un recálculo completo en GEE o una sobrescritura
  decidida sobre un falso negativo.
- ✅ **`W-7` `get_presigned_url()` fallaba abierto** — **borrado** el 2026-09-04,
  no arreglado. Era el único método de la clase sin call sites, y devolvía una
  URL sin firma y sin vencimiento cuando el firmado fallaba. Criterio de
  `DECISIONS #23`. Un test lo fija para que no vuelva por costumbre.
- ✅ **`W-5` `print()` en vez del logger** — cerrado el 2026-09-04. Los 6 de
  código de producción (`app.py`, `db_repository.py`, `ee_indices.py`) pasaron a
  `logging.getLogger(__name__)`. Los de `scratch/` quedan: es material
  gitignoreado, no código del servicio.
- ✅ **`W-8` `/api/inngest` sin verificar la firma** — cerrado el 2026-09-04
  (`DECISIONS #25`). Era el único hallazgo bloqueante del despliegue.
- ✅ **`W-2` cerrado el 2026-09-07.** El default de `MINIO_SECURE` **se deduce
  del host**: solo `localhost`, `127.0.0.1`, `minio`, `host.docker.internal` y
  `*.railway.internal` arrancan sin TLS; **cualquier otro host se asume
  público**. Misma dirección que `IS_PRODUCTION`: lo desconocido se trata como lo
  más seguro. Antes era `False` fijo, así que un deploy que se olvidara la
  variable mandaba la access key, el secret y el COG **en texto plano** — y no
  fallaba, funcionaba.

**Durabilidad — cerrado el 2026-09-04** (`DECISIONS #26`):

- ✅ **Rutas temporales cruzando steps.** Era el reintento envenenado: un step
  memoiza lo que devuelve, así que el reintento recibía la ruta de un archivo
  que el intento anterior ya había borrado. **Ningún reintento podía funcionar.**
  Descarga, COG y subida van en un solo step, y lo que cruza es la
  `storage_key`. Regla: entre steps solo **referencias durables**.
- ✅ **Trabajo bloqueante en el event loop.** Los 8 handlers son `def` con
  `inngest.StepSync`. Lo fija un test que pregunta `is_handler_async` por cada
  función registrada.
- ✅ **`failed` en el primer intento.** Solo lo marca el último, comparando
  `ctx.attempt` contra un `RETRIES` compartido con los decoradores.
- ✅ **La grilla de descarga estaba duplicada** entre `inngest_handlers.py` y
  `export_service.py`, con `scale` y `crs` a mano en los dos lados: cumplían
  `DECISIONS #19` por casualidad. Ahora en `services/ee/gee_download.py`.
  Aparecieron dos cosas al unificar: **ninguna descarga tenía `timeout`**, y un
  GeoTIFF de 0 bytes pasaba el `raise_for_status` para reventar después en
  `rasterio` con un error que no mencionaba la descarga.

**Corrección:**

- **Fechas como string contra `timestamptz`.** El cast implícito usa el
  `TimeZone` de la sesión: si el server no está en UTC, se corren un día.
- **`insert_measurement` abre una conexión por fila** (~70 por serie anual).

**Higiene:**

- `DOCUMENTACION_TECNICA.md` duplicado en raíz y `docs/`, describe SQLite.
- `test.tif`, `test_file.txt` y `scratch/` siguen en disco, ya ignorados por
  `.gitignore`. `rewrite_handlers*.py` y `tests/test_api.py` se borraron el
  2026-08-30.
- `docker-compose.yml` levanta TimescaleDB; la DB real no tiene la extensión.
- 7 jobs colgados en `running` desde el 2026-08-10.
- Las 3 filas de `layers` del 2026-08-10 tienen `storage_key` en formato viejo.

---

## 5. Decisiones abiertas

Se mudaron a [`PREGUNTAS_ABIERTAS.md`](PREGUNTAS_ABIERTAS.md), donde cada una es
una tarea ejecutable por el pipeline de [`WORKFLOW.md`](WORKFLOW.md).

<details>
<summary>La tabla anterior, ya cubierta por ese documento</summary>

| | Estado |
|---|---|
| **Inngest Cloud vs self-hosted** | Sin decidir. Define si el worker necesita URL pública o queda privado. |
| **Estadísticas de rásters** | `layers` no tiene columnas para min/max/mean/stddev. Decidir si se pide migración a Geocore. |
| **Backup del volumen de MinIO** | Railway no trae snapshots. Aceptar el riesgo por escrito o resolverlo. |
| **Consola de MinIO pública** | Decidido dejarla abierta para monitoreo. Registrar el riesgo aceptado. |

---

</details>

---

## 5b. Lo que hay que decidir sobre el flujo mismo

Además de las de `PREGUNTAS_ABIERTAS`, dos que salieron de mirar el flujo real:

- **A-5 ✅ contestada por la UX prevista.** El mapa pinta los tiles del
  **rancho** y las parcelas van encima como **polígonos vectoriales**, con las
  `coordinates` que ya trae `ParcelaDto`. Las métricas por parcela son números
  de un `reduceRegion`. **Conclusión: no hacen falta COG por parcela** — el
  diseño barato no es recortar el rancho, es no producir los N ráster.
- ✅ **La contención de las parcelas dentro del rancho.** El 2026-09-12 se
  verificó que Geocore **no** la validaba, aunque se había dicho que sí. Desde ese
  día la valida al crear, al editar la geometría y en el import de KML, con una
  tolerancia del 1 % de la superficie (Geocore `DECISIONS #21`). Las parcelas
  creadas antes no se revisaron.
- 🔴 **A-6 — esa UX necesita métricas de rancho, y no hay dónde guardarlas.**
  `measurements` es `PK (parcela_id, indice, fecha)`, sin `rancho_id`, y
  `process_rancho` **no escribe ninguna medición**. Hay que elegir entre pedir
  la columna a Geocore o derivarlas promediando parcelas — que **no es lo
  mismo**: excluye la superficie del rancho que no pertenece a ninguna parcela.
  Y la UX pone **B-1** (mediana en el mapa vs promedio en el gráfico) en la
  misma pantalla, donde deja de ser teórico.
- ✅ **E.6 cerrada el 2026-09-07, y el bloqueo era falso.** El tipo estaba en el
  código de Geocore, que es quien define el esquema: `Measurement.Fecha` y
  `Layer.AcquiredTs`/`CreatedAt` son `DateTimeOffset`, o sea **`timestamptz`**.
  No hacía falta la DB. Y el problema era más grave de lo que E.6 decía: **`fecha`
  está en la PK**, así que el mismo día escrito desde dos husos daba dos filas y
  el `ON CONFLICT` no colapsaba ninguna — rompía la idempotencia, no solo corría
  la fecha. Lo normaliza `a_timestamptz()` en el borde con la DB.

---

## 5b-bis. 🔴 Las credenciales de la DB del `.env` están muertas

Al levantar el worker el 2026-09-07:

Primero fue el project ref:

```
FATAL: (ENOTFOUND) tenant/user postgres.<ref> not found
```

y después de cambiar las credenciales, el 2026-09-07:

```
FATAL: password authentication failed for user "postgres"
```

**El formato de `DB_USER` es correcto** —`postgres.<ref>`, que es lo que el
pooler de Supabase exige— y el ref ahora **resuelve**: ya no dice `ENOTFOUND`.
Lo que falla es la **contraseña**.

El worker arranca igual —el fallo de `init_db` se degrada a un ERROR en el log,
a propósito— pero `insert_layer` e `insert_measurement` van a fallar. **Es lo
único que queda de A-3.**

---

## 5c. El pedido a Geocore

[`PEDIDO_GEOCORE_GEODATA.md`](PEDIDO_GEOCORE_GEODATA.md) — redactado el
2026-09-07. Siete ítems: la tabla `rancho_measurements`, tres cambios de API,
estadísticas y parámetros en `layers`, y la calidad de cada medición.

**Ninguno es destructivo** —todos agregan tabla o columnas nullable—, así que el
worker sigue andando hasta que se actualice para escribirlas.

🔴 **El punto 0 es un bug de hoy y va aparte:** `GET /api/measurements` devuelve
las mediciones **más viejas** (`OrderBy(Fecha).Take(500)`).

---

## 6. Docs faltantes

Referenciados desde `docs/` y ausentes: `DEPLOYMENT_DECISION.md`, `TEAM.md`,
`SECURITY_FIXES.md`, `KML_CASOS_Y_REDUNDANCIA.md`,
`SESSION_2026-08-14_geodata_deploy.md`. El más necesario es **`TEAM.md`**: define
la forma de los payloads que los handlers dan por supuesta y nadie verificó.
