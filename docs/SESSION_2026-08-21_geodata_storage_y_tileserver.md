# Sesión 2026-08-19/21 — Alineación con `geodata`, storage y tileserver

> Qué pasó y por qué. Estado permanente en [`HANDOFF.md`](HANDOFF.md).
> Cómo funciona todo junto, en [`FUNCIONAMIENTO.md`](FUNCIONAMIENTO.md).

## Dónde quedó

Tres incrementos en el worker, uno completo en el tileserver, y la
infraestructura de storage levantada. **Nada commiteado todavía** en
`terra-api`; el tileserver ya tiene repo propio.

---

## 1. El worker no escribía nada, y hacía cuatro días que no

**El hallazgo.** `db_repository.py` emitía SQL con identificadores PascalCase
entrecomillados (`"Status"`, `"StorageKey"`, `"ParcelaId"`) contra columnas que
la migración `GeoDataSnakeCase` del 2026-08-17 había pasado a snake_case. Con
comillas dobles Postgres toma el identificador literal, así que **toda escritura
fallaba**.

Es exactamente el modo de fallo que `DECISIONS #15` anticipó por escrito:
*"Cambiar un nombre de columna acá rompe al worker en silencio: no hay
compilador que lo agarre."*

**Cómo se confirmó, en vez de suponerlo.** Se arregló `check_schema.py` —no
llamaba a `load_dotenv()`, así que se conectaba a `localhost` y parecía que la
DB estaba caída— y se consultó el esquema real. Después, todas las filas de
`layers`, `measurements` y `processing_jobs` resultaron ser del **2026-08-10**:
cero escrituras desde la migración.

**Un hallazgo lateral que importa más:** `rancho_id` está NULL en las tres filas
de `layers`. `process_rancho` **nunca completó**, ni antes de la migración.

**Qué se hizo.** Las tres funciones de escritura alineadas al esquema real, con
tres correcciones que el rename no cubría:

- `insert_asset` → `insert_layer`. El nombre venía del diseño SQLite con tabla
  `assets`, y esa confusión es la que produjo que las escrituras fueran a
  `layers` y las lecturas a una tabla inexistente.
- **Capas de rancho a `rancho_id`.** `register_layer` metía un UUID de rancho en
  `parcela_id`.
- **Se quitaron siete parámetros fantasma** (`sensor`, `epsg`, `resolution_m`,
  `mean_val`, `stddev_val`, `cog_ok`, `footprint`): no tienen columna y se
  aceptaban y tiraban en silencio. Ahora la pérdida es visible en el call site.
- `insert_measurement` saltea `valor is None`: la columna es NOT NULL y GEE
  devuelve `None` en fechas nubladas. Sin el guard, una fecha sin dato abortaba
  el step entero.

**Verificación.** `PREPARE` contra la DB real —hace parse y análisis completo,
valida tablas, columnas y el target del `ON CONFLICT`, sin escribir nada— más un
**control negativo**: el SQL viejo tiene que fallar. Si hubiera pasado, la
verificación no probaría nada.

```
OK    update_processing_job
OK    insert_layer
OK    insert_measurement
control negativo OK: column "Id" does not exist
```

---

## 2. `storage_key` guardaba una URI completa

**El hallazgo salió del código de Geocore**, no de una suposición:

```csharp
var bucket = config["GeoData:MinioBucket"] ?? "terra-assets";
var s3Url = $"s3://{bucket}/{layer.StorageKey}";
```

Geocore **antepone** `s3://{bucket}/`. El worker guardaba `s3://exports/…`, así
que la concatenación daba `s3://terra-assets/s3://exports/…` y el tileserver no
resolvía nada. Las tres filas reales lo confirmaban.

**Qué se hizo.** `upload_file` y `upload_bytes` devuelven la **key pelada**. Un
solo bucket desde `MINIO_BUCKET`; los tres buckets viejos (`rasters`, `exports`,
`kml-uploads`) pasan a prefijos de la key. 14 call sites actualizados.

**Un bug latente que apareció al hacerlo:** `routes/kml.py` escribía
`{kml_id}.geojson` y `utils_pkg/roi.py` leía la misma key. Al agregar el prefijo
`kml/` hubo que tocar los dos — cambiar solo el escritor habría dejado ese camino
resolviendo a nada, en silencio.

---

## 3. El tileserver: de "existe en una laptop" a desplegable

**Estado inicial:** `git ls-files` daba **0**. Nunca se había commiteado.
`verify_map_token` —la mitad del contrato de `DECISIONS #16`— existía en una
sola copia sin historial.

Ahora vive en `terra-tileserver`, repo propio.

### El fallo de seguridad

```python
MAP_TOKEN_SECRET = os.getenv("MAP_TOKEN_SECRET", "default_terra_map_token_secret_…")
```

Es el mismo defecto que `DECISIONS #16` documenta haber corregido **del lado de
Geocore** (quitaron `?? DevMapTokenSecret`). De este lado el fallback seguía vivo,
y con una asimetría peligrosa: Geocore **falla cerrado** (503, no firma nada);
el tileserver **fallaba abierto** — sin la variable validaba contra un secreto
que está en el repo, y cualquiera podía forjar un token para cualquier COG.

Un deploy mal configurado no daba error: daba acceso.

Ahora sin secreto devuelve **503 `MAP_TOKEN_UNAVAILABLE`** —el mismo código que
Geocore— y loguea un ERROR al arrancar.

### El SSRF

`TilerFactory()` abría con GDAL cualquier cosa que le pasaran en `?url=`. Con un
token válido —o sea, cualquier usuario legítimo del front— se podía pedir
`http://minio.railway.internal:9000/…` y usar el tileserver como proxy hacia la
red privada. Es **OWASP A10**.

Se agregó un `path_dependency` que exige el prefijo `s3://{MINIO_BUCKET}/` y
rechaza `..`. Es la segunda capa: la policy de `tiler-ro` ya acota el bucket del
lado de MinIO.

### El bloqueante de deploy

El Dockerfile **nunca copiaba el código**. Funcionaba en local solo por el bind
mount del compose; en Railway la imagen habría quedado sin `main.py`. También
llevaba `--reload` y puerto fijo en vez de `$PORT`.

### Reestructuración y tests

Las validaciones leían globales resueltas al importar: imposible testearlas sin
recargar módulos. Se pasaron a fábricas que reciben la configuración
(`crear_validador_de_token(secreto)`), y la lógica se separó en `terra_tiles/`,
que **no importa TiTiler** — así los tests corren sin la pila geoespacial, y se
garantiza que `configure_gdal()` pueda correr antes de que GDAL se cargue.

**47 tests.** Uno encontró un bug real en código escrito ese mismo día:

```
FAILED _sin_esquema["https://bucket-x.up.railway.app/"]
  assert 'bucket-x.up.railway.app/' == 'bucket-x.up.railway.app'
```

Solo se quitaba la barra final en la rama sin esquema. Es el error que ya se
había cometido a mano al configurar `mc`, y habría aparecido como tiles rotos en
producción.

El mismo bloque estaba **duplicado** en `scripts/check_titiler_minio.py`, con el
bug incluido: un script de diagnóstico configurado distinto que el servicio no
diagnostica nada. Ahora los dos usan `configure_gdal()`.

### Caché de tiles

Se agregó `Cache-Control: public, max-age=1y, immutable` sobre `/cog/tiles`.
Los COG de una fecha son inmutables, así que el navegador puede quedárselos —
esto elimina la mayoría de las peticiones repetidas durante pan y zoom, sin
infraestructura.

Dos restricciones con test: **solo 200** (cachear un 5xx congelaría una caída
momentánea de MinIO por un año) y **solo bajo `/cog/tiles`**.

> **Se descartó Redis.** Es el paso caro antes del barato. Con pocos usuarios, el
> caché del navegador resuelve el caso dominante. Consecuencia a tener presente:
> como el token va en el query string, **cada token nuevo invalida la caché**.
> Una vez por hora es tolerable; si molesta, la salida es mover el token a un
> header, que es código en el front.

---

## 4. Infraestructura

MinIO levantado en Railway desde un template. Bucket `terra-assets`, dos
usuarios: `worker-rw` (escritura) y `tiler-ro` (lectura acotada al bucket).

**El template exponía dos dominios**, uno por puerto. El indicador para saber
cuál es cuál no es `/minio/health/live` —responde en los dos— sino la raíz:
`text/html` es la consola, `403 application/xml` es la API S3.

**Se decidió dejar la consola pública** para monitoreo, con el riesgo aceptado a
conciencia: es la superficie de administración root. La alternativa sin costo es
`mc admin info` / `mc admin trace` desde la terminal.

---

## 5. Decisiones tomadas

**El worker no expone API de lectura.** El front lee `measurements` a través de
Geocore, que aplica aislamiento de tenant desde el middleware. Que el worker
sirva los mismos datos sin auth crea dos fuentes para el mismo dato, una segura
y otra no. Su única superficie HTTP será `/health` y `/api/inngest`.

Eso convierte la Fase 2 de "arreglar las lecturas" en "borrarlas", y hace
desaparecer sin arreglarlos: el `get_current_user_optional` que se ignoraba, el
`tenant_id` tomado de un query param, el `CORS *`, y el `/upload-kml` que parsea
XML de usuario duplicando algo que Geocore ya hace con defensa XXE testeada.

**MinIO self-host en vez de Supabase Storage.** Revierte
`DEPLOYMENT_DECISION.md` §1, que hay que actualizar formalmente.

**Tileserver en repo y servicio propios**, mismo proyecto de Railway que MinIO
—la red privada no cruza proyectos.

---

## 6. Correcciones a lo que se afirmó en esta sesión

- **Se dijo que `git add .` metería 6422 archivos** de `tileserver-titiler/.venv`.
  Falso: el `.gitignore` de la raíz tiene `.venv` sin barra, y ese patrón matchea
  a cualquier profundidad. Serían 6 archivos.
- **Se identificó mal cuál dominio de MinIO era la API**, usando
  `/minio/health/live`, que responde en los dos puertos.

---

## 7. Lo que sigue

**Nada de las tres piezas se ha hablado entre sí todavía.** Cada una está
verificada en aislamiento; falta el primer tile real.

1. Commitear los dos repos.
2. Desplegar el tileserver.
3. Subir un COG a mano y pedir un tile con un token real de Geocore. Eso cierra
   de una vez la convención de keys, el secreto compartido, las credenciales de
   solo lectura y la red privada — verificaciones que llevan desde el 2026-08-17
   en papel.
4. Recién ahí, `get_ranch_parcels` (deuda bloqueante de `process_rancho`).
