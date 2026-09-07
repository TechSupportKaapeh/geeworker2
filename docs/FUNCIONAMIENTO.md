# FUNCIONAMIENTO.md — Cómo opera el ecosistema

> Qué hace cada pieza y cómo se hablan. El diseño *objetivo* está en
> [`ARCHITECTURE_PLAN.md`](ARCHITECTURE_PLAN.md); esto es lo que hay.
> Última revisión: **2026-08-21**.

---

## 1. Las piezas

| Servicio | Stack | Repo | Responsabilidad |
|---|---|---|---|
| **Geocore** | .NET 10 | `Geocore` | Identidad, tenants, ranchos, parcelas. Fuente de verdad de la geometría. Publica eventos. Sirve el catálogo al front. |
| **GeeWorker** | Python / FastAPI | `terra-api` | Consume eventos, calcula índices en GEE, sube COG, escribe métricas. |
| **Tileserver** | Python / TiTiler | `terra-tileserver` | Renderiza tiles leyendo los COG. Solo lectura. |
| **MinIO** | imagen `minio/minio` | — | Object storage S3 de los COG. |
| **Supabase** | gestionado | — | Auth (JWT ES256) + dos Postgres: el principal y el de `geodata`. |
| **Inngest** | por decidir | — | Bus de eventos y orquestación durable. |

**Ninguno comparte base de datos ni código de negocio.** Se hablan por tres
canales y nada más: eventos, REST con JWT, y object storage.

---

## 2. El flujo completo

```
1. TerraStaff sube un KML
   POST /api/kml/ranchos  →  Geocore parsea y crea las entidades

2. Geocore publica `terra/rancho.created` a Inngest
   (payload: ranchoId, tenantId, coordinates, jobId)

3. Inngest invoca a GeeWorker
   a. resuelve escenas Sentinel-2 para el bbox
   b. GEE compone el índice en SUS servidores; el worker baja el resultado
   c. lo convierte a COG      → MinIO   ranchos/{id}/{fecha}_ndvi.tif
   d. emite `terra/raster.ingested`
   e. otra función registra la capa → geodata.layers

   Los pasos b y c van en **un solo step de Inngest** desde el 2026-09-04: entre
   steps solo cruzan referencias durables, nunca rutas del disco local
   (`DECISIONS #26`). El `_original.tif` que había en b se eliminó (E.8).

   Las métricas por parcela **no salen de acá**: llegan por
   `terra/parcela.created`, que es su propio evento.

4. El front pinta la capa
   GET /api/layers/{id}   → Geocore arma la URL de tiles
   GET /api/maps/token    → Geocore firma un JWT de 1 h
   GET /cog/tiles/…?url=…&token=…  → Tileserver lee el COG y renderiza
```

**Geocore nunca toca MinIO.** No tiene cliente de storage. Solo guarda
`layers.storage_key` —texto— y con él compone `s3://{bucket}/{key}` para pasárselo
al tileserver. Quien escribe en el bucket es el worker; quien lee, el tileserver.

---

## 3. Las funciones del worker

Todas son funciones de Inngest con `retries=3`, envueltas en `_with_job_tracking`,
que actualiza `processing_jobs` si el evento trae `jobId`.

| Función | Evento | Qué hace |
|---|---|---|
| `process_parcela` | `terra/parcela.created` | Fechas disponibles (2 años), NDVI de la más reciente, serie de 12 meses |
| `process_rancho` | `terra/rancho.created` | Composite NDVI del rancho, COG crudo y procesado, métricas por parcela |
| `register_layer` | `terra/raster.ingested` | Registra la capa en `layers` |
| `generate_heatmap_on_demand` | `terra/parcela.heatmap.requested` | Heatmap puntual |
| `compute_timeseries` | `terra/parcela.timeseries.requested` | Serie temporal puntual |
| `query_available_dates` | `terra/parcela.dates.requested` | Fechas de escenas |
| `export_data` | `terra/parcela.export.requested` | GeoTIFF, PNG o CSV |
| `compute_parcela_stats` | `terra/parcela.stats.requested` | Estadísticas de varios índices |

La costura entre ingesta y análisis es el evento **`terra/raster.ingested`**:
hoy las dos partes viven en el mismo desplegable, pero se pueden separar sin
cambiar el contrato cuando el perfil de escalado lo pida (IO vs CPU).

---

## 4. Los seis mecanismos de autenticación

Se confunden fácil. Cada uno responde algo distinto:

| Secreto | Entre | Responde |
|---|---|---|
| JWT de Supabase (ES256, JWKS) | usuario ↔ Geocore | ¿Quién es este usuario? |
| `X-Tenant-ID` + `TenantMiddleware` | cliente ↔ Geocore | ¿En qué tenant opera? |
| `INNGEST_SIGNING_KEY` | Inngest ↔ worker | ¿Este request salió de Inngest? |
| `worker-rw` (S3) | worker ↔ MinIO | ¿Quién escribe COG? |
| `tiler-ro` (S3) | tileserver ↔ MinIO | ¿Quién lee COG? |
| `MAP_TOKEN_SECRET` (HS256) | Geocore ↔ tileserver | ¿Geocore autorizó a este visor? |

Los dos últimos son **capas independientes**, y por eso las dos importan: el
token de mapa decide quién puede *pedir* un tile; `tiler-ro` decide qué puede
hacer el tileserver *contra el bucket*. Un usuario con token válido igual no
puede escribir, porque el tileserver tampoco puede.

Los dos secretos compartidos (`MAP_TOKEN_SECRET` y el bucket) **fallan en
silencio** si no coinciden entre repos. Nada los valida al desplegar.

---

## 5. Almacenamiento: qué va dónde

| | Va a | Por qué |
|---|---|---|
| COG crudo y procesado | **MinIO** | Son blobs pesados. El tileserver los lee por Range Requests. |
| Catálogo de capas (`layers`) | **`geodata`** | Consultable, y el front lo pide vía Geocore. |
| Métricas (`measurements`) | **`geodata`** | Series temporales, se consultan por fecha. |
| Estado de jobs | **`geodata`** | Geocore crea la fila, el worker la actualiza. |

Un índice como NDVI existe en **dos formas** y cada una va a su sitio: como
ráster por píxel es un COG y va a MinIO para pintarse en el mapa; como promedio
de una parcela en una fecha es un número y va a la DB para graficarse. El worker
produce las dos del mismo cálculo.

---

## 6. Qué está probado junto y qué no

Al **2026-08-26**, el camino de **lectura** está verificado de punta a punta: un
COG en MinIO, leído por el tileserver desplegado, con un token firmado por
Geocore, devolviendo un PNG en el navegador. Eso cerró la convención de keys, el
secreto compartido, las credenciales de solo lectura y la red privada.

Se comprueba en cualquier momento con `check_prod.py` del repo del tileserver, o
sin token con `GET /health/ready`.

**Lo que sigue sin probarse es el camino de escritura.** El worker nunca corrió
contra el MinIO real: sus escrituras a `geodata` están verificadas con `PREPARE`
y su `storage_key` con un mock, pero nadie lo vio subir un COG de verdad. El
primer `process_parcela` contra el MinIO desplegado es la verificación que falta.

```
front → Geocore → tileserver → MinIO      ✅ verificado
GEE → worker → MinIO → geodata            ❌ sin probar contra infra real
```
