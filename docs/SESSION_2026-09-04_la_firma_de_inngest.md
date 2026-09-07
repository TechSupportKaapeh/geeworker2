# Sesión 2026-09-04 — La firma de Inngest (`W-8`, F.1 y F.3)

> Qué pasó y por qué. Estado permanente en [`HANDOFF.md`](HANDOFF.md).
> Decisión: `DECISIONS #25`. Qué sigue: [`PLAN.md`](PLAN.md). Sesión anterior:
> [`SESSION_2026-09-02_auditoria_del_constructor.md`](SESSION_2026-09-02_auditoria_del_constructor.md).

## Dónde quedó

**Se cerró `W-8`, el único hallazgo marcado como bloqueante del despliegue**, y
con él **F.1** y **F.3**. El worker verifica la firma HMAC de cada petición a
`/api/inngest`, y el entorno tiene **un solo criterio** en todo el proceso.

Y después, sin nada bloqueante de por medio, **el resto de la deuda de la
auditoría**: `W-5`, `W-6`, `W-7` (F.14) y el hueco del multipart que había
quedado abierto el 2026-09-01.

Y al final la **FASE E**: E.2, E.3, E.4, E.5 y E.8, más los primeros tests que
tocan un handler (`DECISIONS #26`).

Y al final E.7 y el rastro del KML, que salió de una pregunta: *¿las geometrías
no las parsea Geocore?* Sí — y quedaba código en el worker de cuando no era así.

**72 tests en ~6 s.** Nada pusheado.

---

## 1. El agujero no era el que decía el plan

`PLAN.md` F.1 decía *"cablear `INNGEST_SIGNING_KEY` al cliente y a `serve()`"*.
Al ir a hacerlo, **dos partes de esa frase resultaron falsas**:

- **`serve()` no acepta `signing_key`.** Su firma es
  `serve(app, client, functions, *, serve_origin, serve_path)`.
- **El SDK ya leía la variable solo:** `client.py:101` hace
  `signing_key or os.getenv("INNGEST_SIGNING_KEY")`.

O sea que la tarea, tal como estaba escrita, no tenía nada que hacer. Si se
hubiera ejecutado al pie de la letra —pasarle la key al cliente— se habría
marcado como cerrada **sin cerrar nada**, porque el agujero estaba un nivel más
abajo.

### Dónde estaba de verdad

En `inngest/_internal/net.py::_validate_sig`, del paquete instalado:

```python
if mode == server_lib.ServerKind.DEV_SERVER:
    return None      # <- no valida NADA
```

**La verificación está enteramente condicionada al modo.** Y el modo se
calculaba así:

```python
is_production=os.getenv("ENVIRONMENT", "development") == "production"
```

Cualquier valor distinto de ese string exacto —`prod`, `Production`, un typo, la
variable ausente— ponía el worker en modo dev y **apagaba la verificación**.
`/api/inngest` es la única superficie pública del worker: sin firma, cualquiera
puede invocar `process_parcela` con ids de otro tenant, geometrías arbitrarias y
gasto de cuota de GEE. **No hay segunda capa detrás.**

Y lo peor: **el default del propio SDK es el seguro.** `_get_mode` devuelve
CLOUD cuando no se le pasa `is_production`. Nuestro código era **más permisivo
que no configurar nada**.

---

## 2. Los dos criterios de entorno que no coincidían

Esto lo introdujo la sesión anterior, y conviene registrarlo porque es el modo
de fallo más difícil de ver: **dos definiciones de "producción" en el mismo
proceso.**

`DECISIONS #24` agregó a `config.py` un `IS_DEVELOPMENT` con lista explícita
(`development`, `dev`, `local`). `inngest_client.py` seguía con su
`== "production"`. Resultado:

| `ENVIRONMENT` | Credenciales de MinIO | Firma de Inngest |
|---|---|---|
| `production` | sin default ✅ | se verifica ✅ |
| `development` | default de dev ✅ | no se verifica ✅ |
| **`prod`** | **sin default** ✅ | **no se verifica** 🔴 |

`prod` caía en el peor de los dos mundos y nada lo avisaba. Ahora hay una sola
función de verdad, `config.IS_PRODUCTION`, y **la dirección del default está
elegida a conciencia: lo desconocido es producción.** Un typo en la variable
tiene que apretar los controles, no soltarlos.

---

## 3. Verificado con un cliente HTTP, no con la config

Los tests del criterio están bien, pero un endpoint protegido no se prueba
mirando la configuración: se prueba pidiéndole algo sin credenciales.

La misma invocación sin firma, en los dos modos:

| Modo | Respuesta |
|---|---|
| **cloud** | **401 `header_missing`**, *antes* de parsear el cuerpo |
| dev | pasa el control de acceso y llega a parsear el cuerpo |

Que el 401 llegue **antes** del parseo importa: un atacante no puede ni sondear
el esquema del payload.

Y el caso dev es el **control negativo**: si empezara a dar 401, el otro test
dejaría de probar algo —querría decir que el rechazo viene de otra parte y no de
la verificación de firma—.

---

## 4. Por qué acá se loguea y no se levanta

`StorageService` **levanta** si su config no sirve; `inngest_client` **loguea un
ERROR y sigue**. No es inconsistencia, y la distinción vale para la próxima vez:

- En storage, sin la validación había una **ventana insegura real**: el cliente
  se construía con la credencial root del `docker-compose` y funcionaba.
- Acá el fallo cerrado **ya lo garantiza el SDK**: en modo cloud sin firma
  válida rechaza con 401. No hay ventana que cerrar, solo un diagnóstico que
  dar — y tumbar el arranque impediría darlo por `/health`, que es el criterio
  de `DECISIONS #16`.

Verificado: con `ENVIRONMENT=production` y sin llaves, el proceso **arranca**,
`/health` responde, y salen dos ERROR nombrando `INNGEST_SIGNING_KEY` y
`INNGEST_EVENT_KEY`.

---

## 5. F.3, de paso

`api_base_url` y `event_api_base_url` estaban fijados **siempre** a
`INNGEST_BASE_URL`, cuyo default es `http://localhost:8288`. Un deploy sin esa
variable buscaba Inngest **en su propio contenedor**. Ahora solo se fijan en
desarrollo; en cloud el SDK usa sus URLs.

Y `INNGEST_EVENT_KEY` con el valor de desarrollo (`dev-local-key`) ya no se
manda en producción, con aviso: sin una key real el worker sube los COG y
**nunca emite `terra/raster.ingested`**, así que la capa no se registra en
`layers` — un fallo silencioso a mitad del pipeline.

---

## 6. Una lección de log, chica y concreta

El mensaje de ERROR tenía un em-dash y en la consola salió `?`. Los logs los lee
una terminal de Railway, que puede estar en cp1252. **Los mensajes de log van en
ASCII**, y no por purismo: un carácter roto en el único mensaje que explica por
qué el endpoint devuelve 401 es un mensaje peor.

---

## 7. Y después, el resto de la deuda (F.14)

Cerrado el mismo día, ya sin nada bloqueante de por medio.

### `W-6` — el error que se leía como "no existe"

```python
except Exception:
    return False
```

Mezclaba tres respuestas distintas en una: *el objeto no está*, *no tengo
permiso para mirar* y *la red está caída*. Las dos últimas no son "no existe":
son **"no se pudo concluir"**, y devolver `False` ahí convierte un fallo de
infraestructura en un dato falso.

Importaba por **E.9**, que quiere saltear el cálculo si la capa ya existe.
Apoyado en ese `False`, un error transitorio disparaba un recálculo completo en
GEE —cuota gastada— o una sobrescritura decidida sobre un falso negativo.

Ahora devuelve `False` **solo** ante `NoSuchKey`; el resto se propaga y lo
reintenta Inngest. Y el `AccessDenied` se propaga a propósito: MinIO puede
devolverlo en lugar de 404 para no filtrar qué keys existen, así que tampoco
significa "no existe".

### `W-7` — se borró, no se arregló

`get_presigned_url()` era **el único método de la clase sin call sites**, y
fallaba abierto: si el firmado reventaba, devolvía
`{protocolo}://{endpoint}/{bucket}/{key}` — una URL **sin firma y sin
vencimiento** en lugar de una firmada de 24 h.

Criterio de `DECISIONS #23`: el código sin llamadas se elimina, no se arregla. Y
hay una razón de fondo además de la higiene: quien vaya a necesitar URLs
firmadas tiene que decidir **a propósito** quién las emite y con qué
vencimiento, no heredar un fallback que devuelve algo que parece firmado y no lo
es. Un test fija que el método no vuelva por costumbre.

### `W-5` — los `print`

Los 6 de código de producción pasaron al logger de módulo. Los de `scratch/`
quedan: es material gitignoreado, no código del servicio.

El de `db_repository.update_processing_job` se dejó **sin relanzar**, con el
motivo escrito al lado: el estado del job es telemetría, no el trabajo. Perder
la actualización no debe abortar un procesamiento que ya corrió. Lo que estaba
mal no era tragarse el error, era hacerlo con un `print` invisible en el log del
deploy.

### El hueco del multipart, cerrado

La sesión del 2026-09-01 dejó una afirmación más ancha de lo probado: *"la
subida emite exactamente una petición"*. Vale para 14 bytes, no para un COG.

Ahora hay un test que sube **6 MiB** y verifica el camino real:

```
POST /terra-assets/...tif?uploads=
PUT  /terra-assets/...tif?partNumber=2&uploadId=...
PUT  /terra-assets/...tif?partNumber=1&uploadId=...
POST /terra-assets/...tif?uploadId=...
```

Fijate que las partes salen **en paralelo y fuera de orden**. Y el invariante
que se fija ya no es "una sola petición" —eso era cierto solo del caso chico—
sino **ninguna petición pidiendo la región**, que es lo que sobrevive a los dos
caminos.

⚠️ Que las tres operaciones de multipart caigan bajo `s3:PutObject` está
modelado **según la especificación de IAM, no verificado contra el motor de
policies de MinIO**. Esa verificación es parte de A-3, y está dicho en el
docstring del doble.

---

## 8. La FASE E: el reintento que no podía funcionar

`process_rancho` tenía tres steps, y el primero devolvía **una ruta del disco
local**:

```python
return {"temp_raw_path": temp_raw_path, ...}   # /tmp/xyz.tif
```

El segundo la leía y borraba el archivo. Y un step de Inngest **memoiza lo que
devuelve**: si el segundo fallaba, el reintento no re-ejecutaba el primero,
recibía el JSON guardado y buscaba un archivo que el intento anterior ya había
borrado. `FileNotFoundError`, en los tres reintentos.

O sea: **`retries=3` está puesto para recuperarse de fallos transitorios, y este
diseño garantizaba que ningún reintento pudiera funcionar.**

La causa no es el borrado, es la frontera. Un step es un punto de recuperación,
y un punto de recuperación que depende del disco local de la ejecución anterior
no es un punto de recuperación — da igual si el archivo lo borra el código, el
reinicio del contenedor, o Railway moviendo el proceso de máquina.

Los tres steps se fusionan en uno, y lo que cruza es la `storage_key`. La regla
que queda: **entre steps solo cruzan referencias durables.**

**Y eso cerró E.8 sin trabajo extra.** La alternativa a fusionar era subir el
crudo a MinIO para pasarlo entre steps — que es exactamente lo que hacía el
`_original.tif` que `DECISIONS #19` ya había descartado. La decisión de
arquitectura y la de durabilidad apuntaban al mismo lado.

### E.5, y el hallazgo que estaba ahí desde antes

Los 8 handlers eran `async def` con `requests`, `rasterio`, `psycopg2` y
`.getInfo()` adentro. Una descarga de 30 s **congelaba el event loop y con él
todo el proceso**, incluido `/health` — o sea que Railway podía reiniciar un
worker que estaba trabajando bien.

Lo notable: **`ruff` lo venía marcando** como `ASYNC210
blocking-http-call-in-async-function`. Estaba en la lista de 36 hallazgos de
este archivo que nadie miró hasta que se pinneó el linter (F.15). El hallazgo
estaba disponible **antes** que el diagnóstico.

Ese archivo pasó de 36 hallazgos de ruff a 5.

### La grilla de descarga estaba duplicada

Mismo `getDownloadURL`, mismos `scale` y `crs` escritos a mano, mismo
`requests.get` por chunks, en `inngest_handlers.py` y en `export_service.py`.
`DECISIONS #19` exige que no puedan divergir —dos grillas distintas no fallan al
descargar, fallan al componer el mosaico— y con dos copias eso se cumplía **por
casualidad**. Ahora vive en `services/ee/gee_download.py`.

Dos cosas aparecieron al unificar:

- **Ninguna de las dos descargas tenía `timeout`.** Con el trabajo en un pool de
  hilos, una descarga colgada retiene un hilo hasta que alguien reinicie el
  proceso.
- **Un GeoTIFF de 0 bytes pasaba el `raise_for_status`** y reventaba más
  adelante, en `rasterio` o en `cog_translate`, con un error que no mencionaba
  la descarga.

### Los primeros tests de handlers, y el supuesto que rompieron

La primera versión llamaba a `coords_to_geometry` y a `.getInfo()`, dando por
sentado que una `ee.Geometry` se construye del lado del cliente. **Es falso**:
`ee.Geometry.Polygon` levanta `EEException: client library not initialized`,
porque las clases de geometría se generan a partir del catálogo de algoritmos
que publica GEE.

La respuesta correcta no era mockear GEE, era **separar las dos
responsabilidades**: `normalizar_coordenadas` es la traducción del payload de
Geocore —el contrato entre repos, puro— y `coords_to_geometry` es la única parte
que habla con GEE.

Y al escribir ese test apareció un bug:

```python
lng = c.get("lng") or c.get("Lng")   # 0.0 es falsy
```

**El meridiano de Greenwich y el ecuador son coordenadas válidas.** Un `lng` de
0.0 caía al `Lng` en PascalCase, que no existía, y quedaba en `None` — un ROI
corrupto sin ningún error. No nos afecta hoy (Sinaloa está en −107°), pero es
justo el tipo de bug que aparece el día que el sistema se use en otra parte.

---

## 9. ¿Las geometrías no las parsea Geocore?

Sí. Y la pregunta destapó tres pedazos de código muerto que decían lo contrario.

**Hay que separar dos cosas que suenan igual:**

| | Quién | Qué hace |
|---|---|---|
| **Parsear** | Geocore | Toma un KML —un archivo XML no confiable— y extrae la geometría. Con `DtdProcessing.Prohibit` y `XmlResolver = null`, y tests de XXE (`DECISIONS #17`) |
| **Traducir** | el worker | Toma las coordenadas **ya extraídas** que vienen en el evento y las pasa al orden de GeoJSON |

El worker nunca ve un archivo. Recibe `coordinates` en el payload de Inngest,
como `CoordinateDto`. `normalizar_coordenadas` no valida topología, no abre
archivos y no toca input no confiable: invierte `{lat, lng}` a `[lng, lat]` y
cierra el anillo. Es un adaptador de formato entre dos convenciones.

**Pero quedaba el rastro de cuando el worker sí parseaba KML**, y estaba muerto
de tres formas distintas:

- **`parse_kml_to_geojson`** en `ee_client.py`. Su único llamador era
  `process_kml`, borrado hoy en E.2. Usaba `ET.fromstring` sin endurecer y
  **caía a un regex sobre el XML** si el parseo fallaba. Un parser de input no
  confiable, sin llamadores, es la peor combinación: nadie lo mira y sigue
  disponible. Con él se fue el `import xml.etree.ElementTree` del módulo.
- **La rama `kml_id` de `get_roi_from_request`**, que leía
  `kml/{kml_id}.geojson` de MinIO. **Nadie escribía esa key desde la FASE D**:
  el escritor era `routes/kml.py`. Un lector sin escritor — una rama que solo
  podía devolver "no existe", gastando dos viajes a MinIO para averiguarlo.
- **`get_roi_and_bounds`**, sin llamadores.

Lo notable del segundo: **el SESSION del 2026-08-21 ya había avisado de ese
par.** Decía textual que `routes/kml.py` escribía la key y `roi.py` la leía, y
que al agregar el prefijo hubo que tocar los dos. La FASE D tocó uno solo.

Queda `PLAN.md` **F.17**: con `get_roi_and_bounds` borrado,
`get_roi_from_request` se queda sin llamadores —toma un objeto `req` con
atributos, que es la forma de los requests HTTP que la FASE D eliminó—. No se
encadenó el borrado en la misma pasada para no cortar a ciegas.

---

## 10. E.7, y por qué E.6 no se puede cerrar todavía

**E.7** cerrada: `insert_measurements` en lote. Una serie anual son ~70 fechas,
y de a una eran ~70 conexiones al pool, cada una con su `commit` —o sea su
`fsync` del lado del servidor—. Contra una base gestionada, con la latencia por
medio, es la diferencia entre un step de segundos y uno de minutos. Y ahora es
**atómico**: antes, un fallo en la fila 40 dejaba media serie escrita.

**E.6 no se puede hacer bien hoy**, y conviene decirlo antes de que alguien la
intente. El arreglo —mandar `datetime` en vez de string— depende del **tipo real
de cada columna**: `layers.acquired_ts` y `created_at` son timestamps, pero de
`measurements.fecha` no lo sabemos. Y mandar un `datetime` a una columna `date`
reintroduce exactamente el mismo cast dependiente del TimeZone, en la otra
dirección.

La herramienta que contesta la pregunta **ya existe**: `check_schema.py` imprime
el `data_type` de cada columna. Solo hace falta corriéndolo contra la DB real
— el mismo bloqueo que A-3. Arreglarlo a ciegas es cambiar un bug por otro.

---

## 11. Lo que esta sesión NO arregló

- **`W-2`**: `MINIO_SECURE` sigue con default `False`. Es el último hallazgo
  abierto del worker, y es **configuración de despliegue, no código**: se cierra
  al setear el entorno de A-3.
- **El procesamiento sigue sin probarse.** Los 12 tests de handlers cubren el
  contrato de coordenadas, el estado de los jobs y la forma de las funciones —no
  `process_parcela` corriendo de punta a punta, que necesita GEE, MinIO y la DB.
  Eso es A-3.
- **E.6** — bloqueada por el tipo de las columnas, ver §10.
- **F.17** — el resto del camino de KML muerto, ver §9.
- **E.9** quedó más barata: ya se puede apoyar en `object_exists()`, que desde
  hoy no confunde `AccessDenied` con "no existe".
- **A-3**: el worker sigue sin escribir en MinIO real. Lo que falta es
  infraestructura, no código.
- **El worker sigue sin Dockerfile ni deploy**, y nada está pusheado. Pero ya no
  hay un hallazgo de seguridad que lo bloquee: `ENVIRONMENT`,
  `INNGEST_SIGNING_KEY` e `INNGEST_EVENT_KEY` son ahora **configuración de
  despliegue**, y el proceso avisa por log si falta alguna.
