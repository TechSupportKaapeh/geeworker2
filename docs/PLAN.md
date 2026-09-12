# PLAN.md — Qué sigue, en orden

> Documento para **retomar en frío**. Última actualización: **2026-09-12**.
> Estado del repo: [`HANDOFF.md`](HANDOFF.md) · Cómo funciona:
> [`FUNCIONAMIENTO.md`](FUNCIONAMIENTO.md) · Conceptos:
> [`GUIA_COG_STAC_MOSAICJSON.md`](GUIA_COG_STAC_MOSAICJSON.md)

---

## Cómo retomar

Si venís de cero, leé en este orden y te alcanza:

1. `HANDOFF.md` §1 y §2 — qué es esto y qué está roto
2. [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md) — hacia dónde va
   (`DECISIONS #31` y `#32`, que reemplazan a #19 y #20)
3. Este archivo, desde la **FASE M**

Lo que falta decidir, cada cosa como tarea:
[`PREGUNTAS_ABIERTAS.md`](PREGUNTAS_ABIERTAS.md). Cómo se trabaja cada
incremento: [`WORKFLOW.md`](WORKFLOW.md).

Contexto de las últimas sesiones, si hace falta —de la más reciente para atrás:
[`SESSION_2026-09-07_logs_pedido_y_las_dos_claves.md`](SESSION_2026-09-07_logs_pedido_y_las_dos_claves.md),
[`SESSION_2026-09-04_la_firma_de_inngest.md`](SESSION_2026-09-04_la_firma_de_inngest.md),
[`SESSION_2026-09-02_auditoria_del_constructor.md`](SESSION_2026-09-02_auditoria_del_constructor.md),
[`SESSION_2026-09-01_la_region_del_lado_de_escritura.md`](SESSION_2026-09-01_la_region_del_lado_de_escritura.md),
[`SESSION_2026-08-30_entorno_ejecutable_y_limpieza.md`](SESSION_2026-08-30_entorno_ejecutable_y_limpieza.md),
[`SESSION_2026-08-27_spike_mosaicjson.md`](SESSION_2026-08-27_spike_mosaicjson.md)
y [`SESSION_2026-08-26_primer_tile_real.md`](SESSION_2026-08-26_primer_tile_real.md).

---

## FASE M — El pipeline mensual 🎯 lo que sigue (2026-09-12)

> Diseño: [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md). Lo de Geocore:
> sus `DECISIONS #22` y `#23`.
>
> **El backlog detallado, tarea por tarea, con criterios de aceptación y estado,
> está en [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md).** La tabla de abajo es el
> resumen. Desde el 2026-09-12 hay además un sprint de seguridad (M.8), y el futuro
> pasa a M.9.
>
> **Cada incremento pasa por las seis etapas de [`WORKFLOW.md`](WORKFLOW.md)**
> (PLAN → BUILD → EXPLAIN → AUDIT → DOC → VERIFY), y no se empieza uno sin cerrar
> el anterior. **El código nuevo se escribe al lado del viejo**, los handlers se
> pasan de a uno, y lo viejo se borra al final (M.6). Nunca hay un momento con las
> dos mitades rotas.

| | Qué | Repo | Termina cuando |
|---|---|---|---|
| **M.0** | Red de seguridad: CI con tests, lint y auditoría de dependencias; `main` protegida; Railway espera el check | worker, Geocore, panel | un PR con un test rojo no se puede mergear |
| **M.1** | Núcleo sin GEE: `receta`, `periodos` y los registros de índices y estadísticas, con tests | worker | tests verdes; cambiar un parámetro sin subir la versión rompe un test |
| **M.2** | Etapas y borde: fuente, nubes, compuesto, reducción y `ejecucion.py`. Además `scripts/check_pipeline_real.py` contra GEE real: 3 parcelas × 3 meses, lado a lado con lo de hoy | worker | números y tiempos por mes anotados en la sesión |
| **M.3** | Migración en Geocore (`ARQUITECTURA` §6). `GET /api/measurements` devuelve estadísticas y cobertura. Endpoint de la métrica de rancho ponderada | Geocore | `dotnet test`; la migración la aplica el equipo |
| **M.4** | Las altas sobre el pipeline: `process_parcela` (24 meses) y `process_rancho` (24 COG). Se borran las filas por pasada de prueba | worker | una parcela y un rancho reales de punta a punta, vistos en Procesos |
| **M.5** | El cierre de mes: reconciliador en Geocore y handlers `*.mes.requested` con límite de concurrencia. Reproceso de las parcelas que ya existen | Geocore, worker | el mes se procesa solo; reiniciar Geocore no lo pierde ni lo duplica |
| **M.6** | Borrar lo viejo (`ARQUITECTURA` §9) y decidir los handlers a demanda | worker, Geocore | ruff sin hallazgos nuevos; el worker con menos líneas que al empezar |
| **M.7** | Panel: la serie mensual (mediana con banda p10–p90 y cobertura), el mapa del rancho por mes y el editor de geometría | panel | build y prueba en `vite dev` |
| **M.8** | Seguridad: token de mapa con tenant (A01), tests de la API, rate limiting, registro de auditoría | Geocore, tileserver | un token de otro tenant da 403 |
| **M.9** | Futuro: el cultivo en la parcela y las métricas por cultivo; analítica de series (anomalía, tendencia); más índices; Sentinel-1 para los meses de lluvia | todos | — |

**Qué pasa con las fases de abajo:**
- **C.1 a C.6** (por pasada y MosaicJSON) caducan si se confirma `#31`.
- **G.1** se rehace como M.3, sin `rancho_measurements`.
- **G.2** está decidida: el promedio ponderado por área de las parcelas (Geocore
  `#22`).
- **G.3** se cierra por construcción.
- **G.4** caduca: la métrica del rancho la calcula Geocore.

---

## Arrancar en frío: los cuatro comandos

```powershell
cd "C:\Users\aayal\Downloads\geework 2.0"
.venv\Scripts\python.exe -m pytest tests -q                  # 203 tests, ~35 s
.venv\Scripts\python.exe -m ruff check .                     # lint
.venv\Scripts\python.exe -m pip_audit -r requirements.txt    # CVEs
.venv\Scripts\python.exe scripts\check_minio_region.py       # los 3 en OK
```

Si el `.venv` no existe o falta algo, el entorno se arma con **los dos**
archivos de requirements (ver `WORKFLOW.md`):

```powershell
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt -r requirements-dev.txt
```

**El trabajo del 2026-09-01 al 09-04 está en la rama `fix/region-minio-f12`, sin
pushear.** `main` no se movió, así que entra con `git merge --ff-only`.

### Lo que cambió en esas cuatro sesiones, una línea cada una

| | |
|---|---|
| **F.12** | `region` en el cliente de MinIO — la subida dejó de pedir un permiso que no usa |
| **F.13 · `DECISIONS #24`** | Constructor sin I/O, singleton perezoso, falla cerrado si la config no sirve. La suite pasó de 33 s a 6 |
| **F.15 · F.16** | `requirements-dev.txt` pinneado; `OWASP_TOP10.md` cubre los cuatro repos, con `W-n` y `T-n` |
| **`W-8` · `DECISIONS #25`** | `/api/inngest` verifica la firma. Era el único hallazgo bloqueante del despliegue |
| **FASE E · `DECISIONS #26`** | E.2 a E.5, E.7 y E.8: el reintento envenenado, los handlers sincrónicos, `failed` solo en el último intento |

### Las tres cosas que hay que saber antes de tocar nada

1. **`ENVIRONMENT` es una variable de seguridad.** En `development` apaga la
   verificación de firma de Inngest **y** reactiva el default de credenciales de
   MinIO. Cualquier otro valor cuenta como producción, a propósito.
2. **Entre steps de Inngest solo cruzan referencias durables** —una key de
   MinIO, un id—, nunca rutas del disco local (`DECISIONS #26`).
3. **Los handlers son `def`, no `async def`.** Un test lo fija; declararlos
   corrutinas congela el event loop entero.

**Los dos repos son hermanos y viven fuera del working dir del worker.** Para
tocar el tileserver desde Claude Code hay que sumarlo:

```
/add-dir C:\Users\aayal\Downloads\tileserver-titiler
```

---

## Estado en una tabla

| | Estado |
|---|---|
| SQL del worker alineado con `geodata` | ✅ Verificado con `PREPARE` |
| `storage_key` como key pelada | ✅ Verificado contra el bucket real |
| Tileserver: seguridad, tests, imagen | ✅ 115 tests |
| MinIO en Railway | ✅ Bucket + `worker-rw` + `tiler-ro` |
| Tileserver desplegado | ✅ 2026-08-26 |
| **Primer tile real** | ✅ **2026-08-26 — la cadena de lectura funciona** |
| **Primera escritura real** | ✅ **2026-09-07 — GEE → COG → MinIO, con el COG validado** |
| Geocore: cadenas de conexión y tokens de mapa | ✅ 2026-08-26 |
| Commits del tileserver | ✅ 6 commits, pusheados |
| **Commits del worker** | ✅ Commiteado el 2026-08-30, sin pushear |
| `process_rancho` | 🟡 Desbloqueado (E.1). Nunca corrió contra MinIO real |
| MosaicJSON | ✅ Spike verificado 2026-08-27 |
| Worker desplegado | 🟡 **Dockerfile escrito el 2026-09-07**, sin construir: el daemon de Docker no corre en esta máquina |
| Entorno ejecutable del worker | ✅ 2026-08-30, Python 3.13 (`DECISIONS #22`) |
| Compuerta VERIFY (`pytest`) | ✅ 102 tests en ~9 s (eran 4 en 33 s) |
| **Firma de Inngest verificada** (`W-8`, F.1) | ✅ **2026-09-04 — se cerró el bloqueante del despliegue** |
| `region` en el cliente de MinIO (F.12) | ✅ 2026-09-01, con compuerta propia |
| Singleton perezoso de `storage_service` (F.13) | ✅ 2026-09-02, con test que lo fija |
| Fallo cerrado si falta la config de MinIO | ✅ 2026-09-02 (`DECISIONS #24`) |

**La línea que importa, actualizada el 2026-09-07:** la cadena de **escritura**
también está verificada. El worker bajó datos reales de GEE, produjo un COG que
`rio-cogeo` acepta y lo subió al MinIO desplegado — incluido el camino multipart,
que es el que un COG de verdad toma. Lo que queda de A-3 es la contraseña de
`geodata` y leer el COG de vuelta por el tileserver, que necesita un token.

---

## FASE A — Cerrada el 2026-08-26 ✅

> Se deja el registro porque explica cómo verificar el sistema, no solo que se
> hizo. Detalle completo en el SESSION del 2026-08-26.

**A.1 Commits** — el tileserver pasó de 1 a 6 commits. El worker sigue sin
commitear a propósito.

**A.2 Deploy** — `terra-tileserver-production.up.railway.app`, mismo proyecto de
Railway que MinIO.

**A.3 El primer tile real** — cerró las seis verificaciones que llevaban desde el
2026-08-17 en papel: convención de `storage_key`, `MAP_TOKEN_SECRET` compartido,
credenciales de `tiler-ro`, red privada, `Cache-Control` y PNG transparente en
los bordes.

### Cómo verificar que sigue funcionando

Sin token, el estado de la configuración y de MinIO:

```powershell
curl https://<titiler>/health/ready
```

Con token, la cadena entera en siete escalones:

```powershell
cd C:\Users\aayal\Downloads\tileserver-titiler
python scripts\check_prod.py --base https://<titiler> --token $TOKEN --key <storage_key>
```

Corre con cualquier Python 3.9+, sin venv. **No uses el `.venv` del repo: está
muerto**, se creó en otra máquina.

### Las tres trampas que costaron esta fase

1. **El dominio privado de Railway lleva puerto explícito (`:9000`) y va sin
   TLS.** El público va sin puerto y con TLS. La red privada no mapea puertos.
2. **Npgsql no acepta cadenas de conexión en formato URI.** Geocore necesita
   `Host=…;Port=…;Database=…;Username=…;Password=…;SSL Mode=Require`, y **las
   dos** variables: `Default` y `GeoData`, que apuntan a proyectos de Supabase
   distintos.
3. **Un chequeo de salud no debe exigir permisos que el camino real no usa.**
   Ver `DECISIONS #21`.

---

## FASE B — Cerrada el 2026-08-27 ✅

> Decidía si la arquitectura de `DECISIONS #19` y `#20` era viable.
> **Resultado: se sostiene.** `rio-tiler` compone por mediana y respeta el nodata.

Verificado con datos deliberados —cuatro cuadrantes con agujeros complementarios,
incluido uno que debe salir vacío como control negativo. Se reproduce con
`scripts/check_mosaic_median.py` del tileserver. Detalle en el SESSION del
2026-08-27.

`titiler.mosaic==0.18.0` y `boto3` ya están en `requirements.txt`, y
`MosaicTilerFactory` está montado en `/mosaic` con los mismos dos controles de
acceso que `/cog`.

**Lo que B NO cerró:** el corte de rendimiento entre componer al vuelo y
precalcular (`PREGUNTAS_ABIERTAS` A-2) y quién arma el MosaicJSON (A-1).

<details>
<summary>Los pasos originales, para referencia</summary>

> Ya hay un COG de prueba en el bucket, en
> `ranchos/00000000-0000-0000-0000-000000000001/pasadas/2026-08-26/ndvi.tif`
> (512×512, float32, con nodata y overviews). Sirve como primera pasada del
> mosaico; **borrarlo cuando el spike termine** o queda como capa fantasma.

- **B.1** Agregar `titiler.mosaic==0.18.0` (misma versión que `titiler.core`)
- **B.2** Subir 2-3 COGs de prueba de la misma zona y distinta fecha
- **B.3** Armar un MosaicJSON a mano que los liste
- **B.4** Montar `MosaicTilerFactory` y pedir un tile con selección por **mediana**
- **B.5** Verificar que el nodata se respeta: una zona enmascarada en una pasada
  y con dato en otra tiene que salir con el dato, no con un agujero

⚠️ La advertencia sobre la API de extensiones resultó cierta: hubo que leer la
firma de `MosaicTilerFactory` en el paquete instalado. La alternativa de
precalcular con `rasterio` + `numpy` queda descartada: no hizo falta.

</details>

---

## FASE C — Migrar a ingesta por pasada 🎯 lo que sigue

> B ya confirmó que la arquitectura se sostiene, así que C dejó de ser una
> apuesta. **Antes de empezar, resolver `PREGUNTAS_ABIERTAS` A-3**: el worker
> nunca escribió en el MinIO real, y todo C se apoya en que eso funcione.

- **C.1** Cambiar la descarga: una pasada por vez en vez del composite de GEE
- **C.2** Fijar `region`, `scale` y `crs` **idénticos** en todas las descargas de
  una misma entidad, o las pasadas no se apilan
- **C.3** Preservar el nodata de la máscara de nubes en la descarga
- **C.4** `measurements` por pasada — ya funciona así
  (`get_sentinel2_time_series`), verificar que el camino sistemático lo use
- **C.5** Generar el MosaicJSON por período. Decidir antes `PREGUNTAS_ABIERTAS`
  **A-1** (quién lo arma y dónde vive) y **A-2** (el corte de rendimiento)
- **C.6** Evaluar `ee.batch.Export` para el backfill: `getDownloadURL` tiene tope
  de tamaño y no va a alcanzar
- **C.7** Medir con un rancho real: peso por pasada, tiempo de descarga, límites
  de cuota

⚠️ **Los valores van a cambiar.** Se pasa de `mediana(bandas) → índice` a
`mediana(índices)`. Como el NDVI es un cociente, no son equivalentes. Comparar
contra el esquema actual antes de migrar el histórico, y documentar el escalón.

---

## FASE D — Borrar la superficie HTTP ✅ Cerrada el 2026-08-30

> Ejecutada entera. Motivo y hallazgos en `DECISIONS #23`. La superficie
> quedó fijada por `tests/test_http_surface.py`, que compara por igualdad
> exacta: agregar un `@app.get` ahora rompe el test.

Decidido en el SESSION del 2026-08-21: el worker recibe eventos, no sirve
lecturas. El front lee vía Geocore, que sí aplica aislamiento de tenant.

- **D.1** Borrar `routes/` (10 archivos, 8 sin montar)
- **D.2** Borrar `schemas/`
- **D.3** Borrar `auth.py` y `services/auth/` (incluye un Keycloak sin usar)
- **D.4** Sacar los routers y `/upload-kml` de `app.py`
- **D.5** Quitar las lecturas de `db_repository.py` — consultan una tabla `assets`
  que no existe
- **D.6** Ajustar `services/db.py` y quitar el import muerto `get_cached_dates`
- **D.7** Sacar `python-jose` de `requirements.txt`
- **D.8** Verificar que `ee_service` y `export_service` no importen `routes` ni
  `schemas`
- **D.9** Registrar en `DECISIONS.md`: el worker no expone API de lectura

Hace desaparecer sin arreglarlos: el `get_current_user_optional` que se ignoraba,
el `tenant_id` desde query param, el `CORS *`, y un `/upload-kml` que parsea XML
duplicando algo que Geocore ya hace con defensa XXE testeada.

---

## FASE E — Corrección de los handlers

- **E.1** ✅ **Cerrada el 2026-08-30.** Se borró el step `_calculate_measurements`
  y todo `services/geocore_client.py`. No se reemplazó por una llamada HTTP real:
  `terra/parcela.created` ya cubre el caso con ids y geometrías de verdad
- **E.2** ✅ **Cerrada el 2026-09-04.** `process_kml` borrado: su evento se
  eliminó en Geocore (`5b5746a`), así que no se disparaba nunca. Un handler
  muerto no es inocuo — se registra en Inngest, aparece en el panel y sugiere un
  camino de KML por el worker que no existe (`DECISIONS #17`)
- **E.3** ✅ **Cerrada el 2026-09-04** (`DECISIONS #26`). Descarga, conversión a
  COG y subida van en **un solo step**. La regla que queda: entre steps solo
  cruzan **referencias durables** —una key de MinIO, un id—, nunca estado del
  sistema de archivos local
- **E.4** ✅ **Cerrada el 2026-09-04.** Solo el último intento marca `failed`,
  comparando `ctx.attempt` contra un `RETRIES` que es **el mismo** que usan los
  decoradores. Un test lo fija: si los dos números se separan, el estado del job
  vuelve a mentir sin que nada falle
- **E.5** ✅ **Cerrada el 2026-09-04.** Los 8 handlers pasan de `async def` a
  `def` con `inngest.StepSync`; el SDK los corre en un pool de hilos. Lo fija un
  test que pregunta `is_handler_async` —la property del propio SDK— por cada
  función registrada. **`ruff` venía marcando esto con `ASYNC210
  blocking-http-call-in-async-function` y nadie lo había mirado**
- **E.6** ✅ **Desbloqueada el 2026-09-04 — y no hacía falta la DB.** La
  respuesta estaba en el código de Geocore, que es quien define el esquema:

  ```csharp
  public DateTimeOffset Fecha { get; private set; }          // Measurement.cs
  builder.HasKey(m => new { m.ParcelaId, m.Indice, m.Fecha }); // MeasurementConfiguration.cs
  ```

  `DateTimeOffset` sobre Npgsql mapea a **`timestamp with time zone`**. Así que
  `measurements.fecha` **es `timestamptz`**, y el worker le manda strings
  `"YYYY-MM-DD"`: el cast implícito usa el `TimeZone` de la sesión.

  🔴 **Y es peor de lo que E.6 decía, porque `fecha` está en la PK.** Dos
  escrituras de la misma fecha lógica con sesiones en husos distintos producen
  **dos instantes distintos**, o sea **dos filas** — y el `ON CONFLICT` no
  colapsa ninguna. No es solo que las fechas se corran un día: **rompe la
  idempotencia** que `ARCHITECTURE_PLAN` §5 exige.

  ✅ **Cerrada el 2026-09-07.** `db_repository.a_timestamptz()` normaliza en el
  **borde con la DB**, así que los call sites siguen pasando lo que tengan
  —string, `date` o `datetime`— y ninguno puede olvidarse. Una fecha sin hora se
  ancla a **medianoche UTC a propósito**: `fecha` está en la PK, y conservar el
  instante real de la pasada haría que dos milisegundos de diferencia fueran dos
  filas. Y desapareció un round-trip absurdo: los handlers formateaban
  `datetime.now(timezone.utc)` a string para que Postgres lo volviera a parsear.
  6 tests, y **los dos que ya existían detectaron el cambio** porque esperaban
  el string
- **E.7** ✅ **Cerrada el 2026-09-04.** `insert_measurements` (plural) con
  `execute_values`: una conexión y un round-trip en lugar de ~70 —cada uno con
  su `commit`, o sea su `fsync`—. Y pasa a ser **atómico**: antes, un fallo en
  la fila 40 dejaba media serie escrita. Tres tests fijan el filtrado de fechas
  sin valor y que una serie entera nublada **no pida ni una conexión**
- **E.8** ✅ **Cerrada el 2026-09-04.** El `_original.tif` desapareció junto con
  E.3: existía para pasar el crudo entre steps, que es justo lo que dejó de
  hacerse. `DECISIONS #19` ya lo había descartado —no era dato crudo sino el
  mismo índice antes del COG, nunca se registró en `layers`—
- **E.9** ✅ **Cerrada el 2026-09-07, y era una colisión, no una duplicación.**
  Las dos claves de una capa —la del bucket y la `natural_key` que siembra el
  UUIDv5— se armaban a mano en sitios distintos:

  ```
  process_parcela              generate_heatmap_on_demand
  natural_key  ndvi_{id}_{fecha}   natural_key  {indice}_{id}_{fechaInicio}
  storage_key  parcelas/{id}/…     storage_key  heatmaps/{id}/…
  ```

  🔴 Para NDVI, la misma parcela y la misma fecha, **las dos `natural_key` eran
  idénticas** → mismo UUIDv5 → **la misma fila de `layers`**, pero con
  `storage_key` distinta. El pedido on-demand **pisaba la fila de la capa
  sistemática** apuntándola a `heatmaps/`, y el objeto de `parcelas/` quedaba
  **huérfano en el bucket, referenciado por nadie.** Y al revés.

  Y un segundo choque dentro del on-demand: la key usaba solo `fechaInicio`, así
  que un heatmap de ene1–ene31 y otro de ene1–feb28 escribían **el mismo
  objeto**.

  Ahora `claves_de_capa()` devuelve las dos de **una sola fuente**, con el
  período completo cuando es un rango. El prefijo `heatmaps/` desapareció: un
  heatmap NDVI de una parcela y el ráster sistemático NDVI de esa parcela son el
  mismo tipo de objeto, y estaban separados por *por qué se pidió* en vez de por
  *qué son*. Con eso, el salteo por `object_exists()` es correcto — y desde
  `W-6` esa función levanta en vez de contestar `False` cuando no pudo
  averiguarlo, así que un fallo de permisos ya no dispara un recálculo.

  **El par del rancho era el más peligroso:** `process_rancho` armaba la
  `storage_key` y `register_layer` la `natural_key`, **en otro handler,
  separados por un evento** — el peor lugar para que una convención se
  desincronice. Las dos salen ahora de la misma función.

  De paso, un test destapó que la `natural_key` no llevaba la entidad: un rancho
  y una parcela con el mismo id, índice y fecha habrían dado el mismo UUIDv5.
  Hoy es imposible —los ids son uuid— pero cerrarlo era gratis y más adelante no
  lo sería.

  ⚠️ **La forma de la key no se rediseñó**: eso es A-7 y va con FASE C.

**De paso, C.2 dejó de depender de la casualidad.** La descarga de GEE estaba
duplicada literalmente en `inngest_handlers.py` y `export_service.py`, con la
grilla —`scale`, `crs`— escrita a mano en los dos lados. Ahora vive en
`services/ee/gee_download.py`. `DECISIONS #19` exige que **no puedan** divergir:
dos grillas distintas no fallan al descargar, fallan al componer el mosaico, y
el bug aparece semanas después. De yapa, las dos descargas ganaron `timeout`
—no lo tenían, así que una descarga colgada retenía un hilo para siempre— y un
chequeo de archivo vacío, que antes reventaba más adelante en `rasterio` con un
error que no mencionaba la descarga.

---

## FASE G — La vista de mapa con métricas 🎯 el objetivo de producto

> Definida el 2026-09-04 a partir de la UX prevista: **un mapa con los tiles del
> rancho, las parcelas dibujadas encima con su nombre o id, métricas por parcela
> al seleccionarla, y métricas del rancho aparte.**
>
> `PREGUNTAS_ABIERTAS` **A-5** quedó contestada por esta UX: las parcelas son
> **polígonos vectoriales** sobre el ráster del rancho, así que **no hacen falta
> COG por parcela**. El detalle en A-5 y A-6.

**El orden importa y no es el obvio.** Lo que bloquea no es el front: es que hoy
**no existe ningún lugar donde guardar una métrica de rancho**, y que las dos
cifras que la vista pone lado a lado se calculan distinto.

### G.1 — Un solo pedido de migración a Geocore 🔴 lo primero

> ✅ **Redactado el 2026-09-07:**
> [`PEDIDO_GEOCORE_GEODATA.md`](PEDIDO_GEOCORE_GEODATA.md). Está escrito para que
> lo ejecute quien trabaja Geocore, con el porqué de cada ítem y por qué el DDL
> le corresponde a ese repo (`DECISIONS #15`).
>
> **El punto 0 de ese documento es un bug de hoy y se puede desplegar solo:**
> `GET /api/measurements` hace `OrderBy(Fecha).Take(500)`, o sea que devuelve las
> mediciones **más viejas**. En cuanto una parcela pase ese techo, el gráfico
> deja de mostrar datos actuales.

Junta lo que hoy está repartido en cuatro preguntas abiertas, porque **cambiar el
esquema cuesta coordinación entre dos repos y hacerlo cuatro veces cuesta el
cuádruple**:

| De | Qué | Tabla |
|---|---|---|
| **A-6** | `rancho_id` nullable, `parcela_id` nullable, PK sustituta y dos índices únicos parciales | `measurements` |
| **B-4** | cobertura real del punto (`roi_coverage`) y umbral usado | `measurements` |
| **D-2** | `min`/`max`/`mean`/`stddev` del ráster — que `export_heatmap` **ya calcula y tira** | `layers` |
| **D-1** | parámetros de procesamiento (`max_prob`, `cloud_pct`, `min_coverage`) | `layers` |

⚠️ **No es "agregar columnas": cambia la primary key de `measurements`**, y con
ella el `ON CONFLICT` del que depende la idempotencia del worker
(`ARCHITECTURE_PLAN` §5). Ver A-6 §0 para el detalle y las tres opciones.

### G.2 — Decidir qué es "la métrica del rancho"

Dos candidatas que **no dan lo mismo**, y hay que elegir a conciencia porque la
vista las muestra al lado de las de parcela:

- **`reduceRegion` sobre el polígono del rancho entero** — incluye caminos,
  construcciones y monte. Responde *"¿cómo está mi campo?"*.
- **Promedio ponderado de las parcelas** — excluye todo lo no parcelado.
  Responde *"¿cómo está lo que tengo sembrado?"*.

Para un rancho parcialmente parcelado van a diferir, y el usuario va a preguntar
por qué.

### G.3 — Unificar el reductor (B-1), sabiendo que no van a dar igual

**Decidido: mediana en los dos.** Pero la decisión viene con una advertencia que
hay que poner en la UI, no esconder: **no van a coincidir exactamente**, porque
el mapa agrega en el tiempo por píxel y el gráfico agrega en el espacio y después
en el tiempo. El orden de las operaciones no conmuta.

Y **hay un tercer eje que apareció al revisar el código**: el ráster se descarga
a `scale=10` y la serie reduce a **`scale=60`**. Probablemente sea la mitad de la
discrepancia. Ver B-1.

### G.4 — El worker escribe la métrica del rancho

`process_rancho` hoy **no escribe ninguna medición**: produce el ráster y emite
el evento. Con G.1 y G.2 resueltos, agregar el `reduceRegion` y el
`insert_measurement` correspondiente es la parte fácil.

### G.5 — El contrato con el front, verificado contra el código de Geocore

**La jerarquía `rancho → parcela` vive en la base principal**, no en `geodata`
(`DECISIONS #15`). El front la resuelve **antes** de pedir mediciones, así que
cuando llega a `measurements` ya tiene el `parcela_id` en la mano. Ese orden es
lo que hace innecesario un `rancho_id` en las filas de parcela.

| Paso | Endpoint | Estado |
|---|---|---|
| 1. El rancho y su polígono | `GET /api/ranchos/{id}` → `RanchoDto.Coordinates` | ✅ existe |
| 2. Sus parcelas | `GET /api/parcelas?ranchoId=…` → `GetByRanchoAsync` | ✅ existe. `ParcelaDto` trae `Name`, `Coordinates`, `CentroideLat/Lng` y **`AreaHa`** |
| 3. Tiles del rancho | `GET /api/layers/{id}` + `GET /api/maps/token` | ✅ existe |
| 4. Serie de una parcela | `GET /api/measurements?parcelaId=…&indice=…` | ⚠️ existe, con dos problemas |
| 5. Métrica del rancho | — | ❌ **no existe** (G.1) |
| 6. Escala de color | — | ❌ `layers` no tiene min/max/mean/stddev (G.1) |

⚠️ **`RanchoDto` no incluye las parcelas**, así que los pasos 1 y 2 son dos
llamadas. Está bien: son dos recursos distintos y el mapa necesita los dos.

### G.5b — Los dos problemas de `GET /api/measurements`, verificados en el código

```csharp
public async Task<IActionResult> GetMeasurements(
    [FromQuery] Guid? parcelaId, [FromQuery] string? indice, [FromQuery] int limit = 500, …)
…
    .OrderBy(m => m.Fecha)
    .Take(limit)
```

1. 🔴 **`OrderBy(Fecha).Take(limit)` devuelve las MÁS VIEJAS, no las más
   recientes.** Para un gráfico eso es al revés de lo que se quiere: en cuanto
   una parcela pase las 500 mediciones, **el front deja de ver los datos
   actuales para siempre**. Sin `parcelaId` es peor — 500 filas del tenant
   entero, elegidas por antigüedad. Con FASE C (~73 pasadas por año por índice)
   el techo se toca solo.
2. **`parcelaId` es un `Guid?` único, no una lista, y no hay filtro por
   rancho.** Para pintar los N polígonos del rancho según su valor actual, el
   front hace **N llamadas**: un N+1 a nivel de API.

**Los dos son de Geocore y van en el mismo pedido que G.1.** El primero es un
bug de hoy, no una feature futura.

### Lo que NO bloquea a G

- **A-7** (la convención de keys de MinIO). Va con FASE C, que es la que
  multiplica los objetos. La vista no lista el bucket: consulta `layers`.
- **E.9** (unificar `heatmaps/` con `parcelas/`). Independiente.
- **A-3** sigue bloqueando que todo esto se vea con datos reales, pero no impide
  decidir ni pedir la migración.

---

## FASE H — Desplegar el worker

> Abierta el 2026-09-07. Es lo unico que queda del lado del worker, y **A-3 es
> el paso que valida todo lo demas**.

### H.1 — Imagen 🟡 escrita, sin construir

`Dockerfile` y `.dockerignore` estan escritos y respetan el contrato de
`DECISIONS #22`: base `python:3.13` y `pip install --only-binary=:all:`, o sea
que un pin sin wheel falla el build en vez de ponerse a compilar GDAL.

⚠️ **No se construyo la imagen**: el daemon de Docker no corre en esta maquina
(el CLI esta, el engine no). Lo que si se verifico:

- los siete paths que el `COPY` menciona existen, y **ningun modulo propio queda
  fuera** — comprobado recorriendo el arbol, porque el Dockerfile del tileserver
  ya se olvido de copiar el codigo una vez y la imagen quedo sin `main.py`;
- `uvicorn app:app` arranca de verdad y sirve la superficie correcta:
  `/health` 200, `/docs` **404**, `/api/inngest` 200;
- `pip install --only-binary=:all:` pasa sobre `requirements.txt` sin compilar.

**Lo primero al construir**: `docker build .` y `docker run` con las variables,
y confirmar que `/health` responde dentro del contenedor.

Detalles que el archivo explica y conviene no deshacer: `libexpat1` es la unica
dependencia de sistema —`rasterio` trae GDAL en su wheel pero GDAL la necesita—,
`requirements-dev.txt` **no** se instala, y `/app/outputs` se crea antes de
bajar a usuario no-root porque `config.py` hace `os.makedirs` **al importarse**.

### H.2 — Las variables del despliegue

| Variable | Por que importa |
|---|---|
| `ENVIRONMENT` | **Es una variable de seguridad.** En `development` apaga la verificacion de firma de Inngest **y** reactiva el default de credenciales de MinIO |
| `INNGEST_SIGNING_KEY` | Sin ella, en modo cloud toda invocacion se rechaza — correcto, pero hay que saberlo |
| `INNGEST_EVENT_KEY` | Sin ella los COG se suben y **la capa nunca se registra** en `layers` |
| `MINIO_ENDPOINT` | El **publico**, sin puerto. `MINIO_SECURE` ya se deduce de ahi (W-2) |
| `MINIO_ACCESS_KEY` / `SECRET_KEY` | De `worker-rw`. Ya no hay default fuera de desarrollo |
| `DB_*` | 🔴 **Las del `.env` de esta maquina ya no sirven** — ver H.3 |
| `LOG_FORMAT` | Se deduce del entorno; `json` en produccion |

Las cuatro primeras se anuncian por log al arrancar si estan mal.

### H.3 — 🔴 Las credenciales de la DB del `.env` estan muertas

Al levantar el worker el 2026-09-07 apareció:

```
FATAL: (ENOTFOUND) tenant/user postgres.ubddxlfxmdzqfwazsdle not found
```

O sea que **A-3 esta bloqueada por dos cosas, no una**: las credenciales de
`worker-rw` para MinIO **y** las de la base `geodata`. El worker arranca igual
—el fallo de `init_db` se degrada a un ERROR en el log, a proposito— pero
`insert_layer` y `insert_measurement` van a fallar.

### H.4 — A-3, que ahora es un comando

```powershell
.venv\Scripts\python.exe scripts\check_write_path.py
```

Seis escalones, cada uno agregando un eslabon, de modo que **el primero que
falla señala la causa** — el mismo criterio que `check_prod.py` del tileserver,
del lado de escritura. Incluye la subida de **6 MiB**, que es el camino
multipart que un COG real toma de verdad y que hasta ahora solo estaba modelado
segun la especificacion, sin verificar contra el motor de policies de MinIO.

Verificado que los seis pasan contra un servidor que responde, y que falla con
el diagnostico correcto cuando no hay nadie del otro lado.

**No borra lo que sube**: `s3:DeleteObject` es un permiso que la ingesta real no
usa, y pedirlo seria repetir la trampa de `DECISIONS #21`. Los objetos van a
`_diagnostico/` y se limpian con `mc rm`.

### H.4 corrió el 2026-09-07 y pasó

```
OK  gee         autenticado, ROI armado
OK  descarga    408.203 bytes de GEE (2026-08-08 a 2026-09-07)
    stats calculadas y descartadas: min=-0.3637 max=0.9357 mean=0.2581 stddev=0.2553
OK  cog         428.674 bytes
OK  cog valido  rio-cogeo lo acepta
OK  subida      _diagnostico/2b1a736a/2026-09-07_ndvi.tif
```

Esas cuatro estadísticas son **reales, y `layers` no tiene dónde guardarlas**: es
el ítem 3 del pedido a Geocore, con números en vez de un argumento.

**El único fallo de configuración lo detectó `validate_endpoint` sin tocar la
red.** El `.env` tenía `…up.railway.app:9000` con `MINIO_SECURE=False` —las dos
mitades de la confusión entre los dominios de Railway a la vez— y el mensaje
nombró las dos correcciones. De paso apareció que el validador solo implementaba
tres de las cuatro formas documentadas: **le faltaba justo "sobra el puerto en el
público"**, y se cerró con dos tests.

### H.5 — Lo que falta de A-3

- 🔴 **La contraseña de `geodata`.** `password authentication failed for user
  "postgres"`. El formato de `DB_USER` es correcto (`postgres.<ref>`, que es lo
  que el pooler de Supabase exige) y el ref **resuelve** — ya no dice
  `ENOTFOUND`. Falla la contraseña. Se comprueba con `check_schema.py`.
- ⏳ **Leer el COG por el tileserver desplegado**, que cierra el círculo.
  Necesita un token de `GET /api/maps/token`, y `MAP_TOKEN_SECRET` **no está en
  ninguno de los dos repos** —vive solo en Railway (`DECISIONS #16`)—, así que
  hay que sacarlo del panel:

  ```powershell
  cd C:\Users\aayal\Downloads\tileserver-titiler
  python scripts\check_prod.py --base https://terra-tileserver-production.up.railway.app `
      --token $TOKEN --key _diagnostico/2b1a736a/2026-09-07_ndvi.tif
  ```

  El tileserver desplegado **ya está sano**: sus cuatro chequeos de
  `/health/ready` en verde, leyendo del mismo bucket `terra-assets`.
- **Limpiar los objetos de diagnóstico** cuando ya no sirvan:
  `mc rm --recursive --force <alias>/terra-assets/_diagnostico/`

---

### H.7 — El primer deploy falló, y encontró un bug real (2026-09-07)

**El build pasó** —la imagen se construyó en Railway sin tocar el
`--only-binary`— pero el contenedor entró en **bucle de reinicios**:

```
RuntimeError: Faltan EE_SERVICE_ACCOUNT_EMAIL o EE_SERVICE_ACCOUNT_KEY_JSON
ERROR in uvicorn.error: Application startup failed. Exiting.
```

La causa inmediata eran las variables de GEE sin setear. **La causa real era
una inconsistencia en `app.py`:** `init_db()` estaba dentro de un `try` —con un
comentario explicando que no es fatal— y `init_ee()` estaba **fuera**. Así que
una credencial faltante tumbaba el proceso, Railway reiniciaba, y el ciclo se
repetía: los logs de varios procesos entrelazados y `/health` sin responder
nunca.

**Es exactamente la patología que `DECISIONS #21` describe** para los chequeos
de salud —*"un deploy mal configurado entraría en un bucle de reinicios sin
llegar nunca a mostrar el motivo"*— y el criterio de `DECISIONS #16`: un secreto
faltante degrada una funcionalidad, no tumba el servicio.

Y sostenerlo cuesta poco, porque **los handlers llaman a `init_ee()` por su
cuenta, una vez por step**: la del arranque era un precalentamiento, no un
requisito. Sin credenciales, cada invocación falla por separado y la reintenta
Inngest.

Arreglado, reproducido en local antes y después, y fijado por un test.

**El segundo arranque falló por lo mismo, un nivel más abajo:**

```
PermissionError: [Errno 13] Permission denied: '../outputs'
```

`config.py` hacía `os.makedirs(BASE_OUTPUT_DIR)` **a nivel de módulo**. Con
`BASE_OUTPUT_DIR=../outputs` heredado del `.env` local, el contenedor intentaba
crear `/outputs` —fuera de `/app`— como usuario no-root.

Y lo grave no es el error sino **dónde ocurre**: `config` se importa antes de
`setup_logging()`, así que el fallo sale como un traceback crudo, sin contexto y
sin nombrar la variable. **Un `import` no debería poder matar el proceso.**

Es la **tercera vez** que el mismo patrón —I/O al importar— tumba algo acá: el
singleton de `storage_service` (F.13), el `init_ee()` fuera del `try`, y esto.

No se perdió nada al sacarlo: `utils_pkg.io.ensure_outputs_dir()` **ya** crea la
carpeta y la llaman los tres sitios que escriben ahí. Era código duplicado, y de
los dos el único que podía tumbar el arranque.

⚠️ **Y en Railway hay que sacar `BASE_OUTPUT_DIR`**: el default `./outputs` con
`WORKDIR=/app` da `/app/outputs`, que el Dockerfile crea y le da al usuario
`worker`. El valor `../outputs` es del entorno local y no aplica al contenedor.

---

### H.6 — La secuencia de despliegue, en orden

**El repo remoto ya existe** (`Kaapeh-Mexico/terra-api`) y `origin/main` está
**30 commits atrás**. `.env` no está trackeado y el diff saliente no contiene
ningún secreto — verificado buscando claves privadas, JSON de service account y
el propio `.env`. Lo único que aparece son los defaults del `docker-compose`
local (`postgres`/`minioadmin`), que son públicos por definición y que son
justamente los que hacían peligroso a `W-1`.

Pero **desplegar no es solo pushear**, y el orden importa:

#### 1. 🔴 Decidir `C-6` primero: Inngest Cloud o self-hosted

Está **sin decidir** en `PREGUNTAS_ABIERTAS`, y no es un detalle de infra: de ahí
salen `INNGEST_EVENT_KEY` e `INNGEST_SIGNING_KEY`, que son dos de las variables
del deploy. La recomendación escrita es **Cloud para la demo** — self-hosted
agrega un servicio más que mantener para resolver un problema que todavía no
existe.

#### 2. Crear la app en Inngest y sacar las dos keys

#### 3. Merge y push

```powershell
git checkout main
git merge --ff-only fix/region-minio-f12
git push origin main
```

Es fast-forward: `main` no se movió, así que el historial queda igual que si se
hubiera commiteado ahí directamente.

#### 4. Desplegar en Railway, con las variables de H.2

**En el mismo proyecto que MinIO**, o el dominio privado no resuelve.

#### 5. ⚠️ Registrar la app en Inngest — el paso que no está en ningún doc

**Desplegar el worker no lo conecta a Inngest.** Railway le da una URL pública,
pero Inngest no la conoce: hay que registrar/sincronizar la app apuntando a

```
https://<worker>.up.railway.app/api/inngest
```

Sin ese paso el worker queda desplegado, con `/health` en 200, **y sin recibir
un solo evento** — que es exactamente el síntoma que uno tarda en diagnosticar,
porque nada falla.

Al sincronizar, Inngest lee las 8 funciones de `all_functions`. Un test las fija
por id, así que si el panel muestra menos, faltó registrar alguna.

#### 6. La contraseña de `geodata`

Sin eso, el worker arranca y **cada handler falla en el paso de la DB**. El COG
se sube a MinIO y la capa nunca se registra en `layers`.

### Lo que conviene hacer antes del primer deploy

**Construir la imagen en local.** El `Dockerfile` está escrito y verificado en
lo que se puede sin daemon, pero **nunca se construyó**: si Railway es el primer
build, un error se descubre allá, con un ciclo de iteración mucho más lento.

```powershell
docker build -t geeworker .
docker run --rm -p 8000:8000 --env-file .env geeworker
curl http://localhost:8000/health
```

---

## FASE F — Infra y limpieza

- **F.1** ✅ **Cerrada el 2026-09-04** (`DECISIONS #25`, OWASP `W-8`). Y estaba
  mal planteada: **no hay nada que cablear a `serve()`** —su firma no acepta
  `signing_key`, todo sale del cliente— y el SDK **ya leía la variable solo**
  (`client.py:101`). El agujero real era otro: la verificación de firma está
  enteramente condicionada al modo (`net.py::_validate_sig` devuelve `None` en
  dev sin mirar nada), y el modo se calculaba como
  `os.getenv("ENVIRONMENT", "development") == "production"`. **Cualquier valor
  distinto de ese string exacto —`prod`, un typo, la variable ausente— apagaba
  la verificación**, dejando `/api/inngest` abierto a cualquiera. Era más
  permisivo que no configurar nada: el default del propio SDK es cloud.
  Verificado contra un cliente HTTP: en modo cloud una invocación sin firma da
  **401 antes de parsear el cuerpo**; en dev, la misma pasa el control de acceso
- **F.2** `inngest dev -u http://geeworker:8000/api/inngest --no-discovery`
- **F.3** ✅ **Cerrada el 2026-09-04.** `api_base_url` y `event_api_base_url`
  solo se fijan en desarrollo. Forzarlas apuntaba el worker a
  `INNGEST_BASE_URL`, cuyo default es `http://localhost:8288`: un deploy sin esa
  variable buscaba Inngest **en su propio contenedor**
- **F.4** Limpiar los 7 jobs colgados en `running` desde el 2026-08-10
- **F.5** Borrar las 3 filas de `layers` con `storage_key` en formato viejo
- **F.6** ✅ Cerrada el 2026-08-30
- **F.7** ✅ Cerrada el 2026-08-30
- **F.8** ✅ Cerrada el 2026-08-30 — 4 tests. `test_api.py` probaba las rutas
  borradas; lo reemplaza `tests/test_http_surface.py`
- **F.9** Reescribir o borrar `DOCUMENTACION_TECNICA.md` (duplicado, describe SQLite)
- **F.10** `docker-compose.yml`: `postgis/postgis` en vez de TimescaleDB
- **F.11** ✅ Cerrada el 2026-08-30 — `schemas/` se borró entero en FASE D y
  `models/` ya no existía
- **F.12** ✅ **Cerrada el 2026-09-01.** `region` va al cliente de MinIO, desde
  `AWS_REGION` en `config.py` —mismo nombre y default que el tileserver—. Se
  reprodujo el fallo antes de arreglarlo y quedó `scripts/check_minio_region.py`
  como compuerta. Ya no bloquea A-3. Detalle en `DECISIONS #21`, revisión del
  2026-09-01
- **F.13** ✅ **Cerrada el 2026-09-02.** `get_storage_service()` con
  `lru_cache`, `ensure_bucket()` fuera del constructor, y el test que verifica
  que importar el módulo no abre conexiones —en un proceso aparte, con el socket
  saboteado—. **La suite pasó de ~33 s con 4 tests a ~6 s con 24.** Salió de la
  auditoría del 2026-09-02, junto con dos hallazgos OWASP del mismo constructor
  (`DECISIONS #24`)
- **F.14** ✅ **Cerrada el 2026-09-04.** Los tres hallazgos 🟡 de la auditoría
  (`W-5`, `W-6`, `W-7`):
  - **`W-6`** `object_exists()` ahora devuelve `False` **solo** ante
    `NoSuchKey`; `AccessDenied`, red caída y cualquier otro `S3Error` se
    propagan. `DECISIONS #21`: no haber podido concluir no es haber concluido.
    Importaba por **E.9**, que quiere saltear el cálculo si la capa ya existe:
    apoyado en el `False` viejo, un error transitorio disparaba un recálculo
    completo en GEE o una sobrescritura sobre un falso negativo
  - **`W-7`** `get_presigned_url()` **se borró**, no se arregló. Era el único
    método de la clase sin call sites y fallaba abierto: devolvía una URL sin
    firma y sin vencimiento. Criterio de `DECISIONS #23` — el código sin
    llamadas se elimina. Lo fija un test para que no vuelva por costumbre
  - **`W-5`** los 6 `print()` de código de producción pasaron al logger de
    módulo (`app.py`, `db_repository.py`, `ee_indices.py`). Los de `scratch/`
    quedan: es material gitignoreado, no código del servicio
  - De paso, el **hueco del multipart** que abrió la sesión del 2026-09-01: hay
    un test que sube **6 MiB** y verifica el camino real —`POST ?uploads`, un
    `PUT ?partNumber=N` por trozo, `POST ?uploadId=`— sin ninguna petición
    pidiendo la región. Ese es el invariante que sobrevive a los dos caminos,
    y el que reemplaza al "exactamente una petición" de la primera versión
- **F.15** ✅ **Cerrada el 2026-09-02.** `requirements-dev.txt` con `pytest`,
  `httpx`, `ruff` y `pip-audit` pinneados, y la misma regla de wheel de
  `DECISIONS #22` —verificada con `--dry-run`—. `pytest` y `httpx` salieron de
  `requirements.txt`: no las necesita el servicio corriendo, y el Dockerfile que
  todavía no existe no debería llevárselas. **El comando de instalación cambió**,
  y está en el `WORKFLOW`:
  `pip install --only-binary=:all: -r requirements.txt -r requirements-dev.txt`
- **F.18** ✅ **Cerrada el 2026-09-07.** Los logs llevan **contexto de
  ejecución**: `run_id` (el de Inngest), `attempt`, la función, `job_id` y las
  ids de entidad. Lo inyecta un `logging.Filter` desde un `ContextVar` que fija
  `_with_job_tracking`, así que **aparece también en los logs de
  `storage_service`, `db_repository` y `gee_download`** —los módulos que fallan
  de verdad— sin que ninguno sepa que existe un contexto.

  Tres cosas más que estaban mal y ya no: el `JSONFormatter` **descartaba
  cualquier `extra`**; el default era `text` **siempre**, así que un deploy salía
  sin logs estructurados a menos que alguien se acordara de una variable no
  documentada (ahora es `json` si `IS_PRODUCTION`); y el modo texto no mostraba
  el contexto, con lo cual en local nadie lo habría usado.

  Verificado de punta a punta con un handler que falla:

  ```json
  {"level": "ERROR", "logger": "services.storage_service",
   "message": "AccessDenied al subir el COG", "run_id": "01JC-…",
   "attempt": 3, "funcion": "process_parcela", "job_id": "job-77",
   "tenant_id": "t-1", "parcela_id": "p-9"}
  ```

  9 tests, incluidos los dos que importan: que el contexto **no se filtre** al
  salir —los handlers reusan hilos del pool— y que se restaure aunque haya
  excepción.
- **F.17** ✅ **Cerrada el 2026-09-07.** `utils_pkg/roi.py` se borró **entero**:
  sus siete funciones quedaron sin llamadores al desaparecer el camino de KML por
  el worker. Tomaban un objeto `req` con atributos —la forma de los requests HTTP
  que la FASE D eliminó—, y los handlers arman el ROI con
  `coords_to_geometry(payload["coordinates"])`, directo del evento. Con él se
  fueron los últimos imports de `shapely` y de `ee` en `utils_pkg`.
- **F.16** ✅ **Cerrada el 2026-09-02.** `docs/OWASP_TOP10.md` cubre los cuatro
  repos, con hallazgos **`W-n`** (worker, 8) y **`T-n`** (tileserver, 6), y dos
  correcciones a lo que el documento afirmaba: A10 dejó de ser *"no hay
  superficie de SSRF"* —el `?url=` del tileserver lo es— y A06 pasó de "sin
  proceso" a tenerlo en un repo de cuatro.

  **Lo que el documento hace visible ahora:** `DECISIONS #16` (Geocore), `T-1`
  (tileserver) y `W-1` (worker) son **el mismo defecto** —un default que hace
  que un deploy mal configurado funcione de forma insegura en vez de fallar— y
  se descubrieron uno por uno, en tres sesiones, a lo largo de tres semanas.
  Con el scope limitado a dos de los cuatro repos no había dónde ver el patrón.

---

## Decisiones abiertas

Se mudaron a [`PREGUNTAS_ABIERTAS.md`](PREGUNTAS_ABIERTAS.md), donde cada una es
una **tarea** ejecutable por el pipeline de [`WORKFLOW.md`](WORKFLOW.md), agrupada
por cuándo hay que decidirla y no por tema.

Las tres que bloquean trabajo en curso:

| | Bloquea |
|---|---|
| **A-1** ¿Quién arma el MosaicJSON y dónde vive? | C.5 |
| **A-2** ¿Dónde está el corte entre componer al vuelo y precalcular? | A-1 y C.5 |
| **A-3** El worker nunca escribió en el MinIO real | La confianza en toda la ingesta |
| ~~**A-5** ¿Se recortan las parcelas del ráster del rancho?~~ ✅ **La UX la contesta**: el mapa pinta el ráster del **rancho** y las parcelas van como polígonos encima. **No hacen falta COG por parcela** — las métricas por parcela son números de un `reduceRegion` | — |
| **A-6** La UX necesita **métricas de rancho, y no hay dónde guardarlas** | La vista de mapa. `measurements` es solo por parcela y `process_rancho` no escribe ninguna |

---

## Lo que NO hay que hacer

Para que nadie "simplifique" algo que se decidió a conciencia:

- **No guardar composites en vez de pasadas.** De un mensual no se deriva el
  semanal (`DECISIONS #19`)
- **No escribir código propio de composición.** Alinear grillas y manejar nodata
  ya está resuelto en `rio-tiler` (`DECISIONS #20`)
- **No guardar el composite multibanda.** Los índices son fijos, y las
  correcciones se aplican antes de componer: no ahorra reprocesar
- **No devolver a `storage_key` la URI completa.** Geocore antepone
  `s3://{bucket}/`
- **No volver a poner un default en `MAP_TOKEN_SECRET`.** Falla abierto
- **No relajar el filtro de `?url=` del tileserver.** Es la defensa contra SSRF
- **No exponer lecturas de `geodata` desde el worker.** Eso lo sirve Geocore,
  con aislamiento de tenant

---

## Orden sugerido

```
A.1 commits  →  A.2 deploy  →  A.3 primer tile   ✅ CERRADO 2026-08-26
                                     │
                    ┌────────────────┼────────────────┐
                    ▼                ▼                ▼
            B spike mosaic ✅   E.1 get_ranch ✅  D borrar HTTP ✅
                    │                                (2026-08-30)
                    ▼
              C por pasada  ← bloqueada por A-3
```

**Sesión del 2026-08-30.** Antes de nada hubo que hacer el worker ejecutable:
no había `.venv`, ninguna dependencia instalada y `rasterio==1.3.10` no tiene
wheel para los Pythons de esta máquina, así que `pytest` ni colectaba y la
compuerta VERIFY era inaplicable (`DECISIONS #22`). Con eso resuelto se
cerraron D, E.1 y F.6/F.7/F.8.

**Lo único que queda antes de C es A-3**, y no depende de escribir código:
depende de tener el dominio público de la API de MinIO y las credenciales de
`worker-rw`. El `.env` de esta máquina apunta a `localhost:9000`.

**Sesión del 2026-09-01.** Se cerró **F.12**, que bloqueaba A-3 de hecho: el
cliente de MinIO ya recibe `region`, así que la subida no pide un permiso que no
usa. Se reprodujo el fallo contra un servidor S3 de mentira **antes** de tocar el
código, y esa reproducción quedó como `scripts/check_minio_region.py`.

⚠️ **Cuando A-3 falle, ya no asumir que es F.12.** Ese modo de fallo está
cerrado y hay un comando que lo demuestra en dos segundos:

```powershell
.venv\Scripts\python.exe scripts\check_minio_region.py
```

**Sesión del 2026-09-02.** La auditoría de F.12 —etapa AUDIT del `WORKFLOW`,
más `ruff` y `pip-audit`— encontró tres defectos más en el mismo constructor y
se cerró **F.13** con ellos (`DECISIONS #24`):

- El singleton era eager: `import app` abría un socket. **La suite pasó de
  ~33 s con 4 tests a ~6 s con 24.**
- `ensure_bucket()` pedía `s3:ListBucket` en cada arranque, otro permiso que la
  subida no usa. Salió del constructor.
- Las credenciales caían a `minioadmin` —la root del `docker-compose`—, así que
  un deploy sin variables se autenticaba como root en vez de fallar.

**Antes de disparar A-3, tres cosas que ahora fallan temprano y con el nombre de
la variable**, en vez de tarde y con un `AccessDenied`:

```powershell
.venv\Scripts\python.exe -m pytest tests/ -q          # 24 tests, ~6 s
.venv\Scripts\python.exe scripts\check_minio_region.py # los 3 en OK
```

Y `MINIO_SECURE=True` es **obligatorio** contra el dominio público. Si se
olvida, `validate_endpoint()` lo dice al construir el cliente, sin tocar la red.

De esa auditoría se cerraron **F.15** (`requirements-dev.txt`) y **F.16** (el
scope de `OWASP_TOP10.md`). Queda **F.14**: los tres hallazgos 🟡, que en el
mapeo OWASP son `W-5`, `W-6` y `W-7`.

**Sesión del 2026-09-04.** Se cerró `W-8` —el hallazgo que bloqueaba el
despliegue— y con él **F.1** y **F.3** (`DECISIONS #25`). El worker ya verifica
la firma de cada petición a `/api/inngest`, y el entorno tiene **un solo
criterio** en todo el proceso: antes había dos que no coincidían, y con
`ENVIRONMENT=prod` el proceso quedaba en el peor de los dos mundos.

Lo que queda de la auditoría es **F.14** (`W-5`, `W-6`, `W-7`) y el hueco del
multipart. Ninguno bloquea el despliegue ni A-3.

**A ya no bloquea nada.** B, D y E.1 son independientes entre sí y se pueden
tomar en cualquier orden; F se intercala cuando convenga.

**B ya está cerrada** y confirmó `DECISIONS #19` y `#20`, así que C dejó de ser
una apuesta. Pero antes de invertir en C conviene resolver **A-3** de
`PREGUNTAS_ABIERTAS`: el worker nunca escribió en el MinIO real, y todo C se
apoya en que eso funcione.

Lo que **no** cierra la Fase A: el worker nunca escribió en MinIO real. La cadena
de lectura está probada; la de escritura, no. Eso se resuelve en E.1 y C, o
antes, disparando `process_parcela` a mano contra el MinIO desplegado.
