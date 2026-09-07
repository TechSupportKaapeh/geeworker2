# Sesión 2026-08-30 — Entorno ejecutable, FASE D y E.1

> Qué pasó y por qué. Estado permanente en [`HANDOFF.md`](HANDOFF.md).
> Lo que quedó sin decidir: [`PREGUNTAS_ABIERTAS.md`](PREGUNTAS_ABIERTAS.md).
> Qué sigue: [`PLAN.md`](PLAN.md).

## Dónde quedó

**El worker es ejecutable por primera vez, está commiteado por primera vez, y
su compuerta VERIFY está en verde por primera vez.** Cinco commits sobre `main`,
sin pushear. FASE D, E.1, F.6, F.7 y F.8 cerradas.

```
af229b4  chore: commitear el estado actual antes de la limpieza
b3d7df7  build: entorno reproducible sobre Python 3.13 y pines que exigen wheel
ae8dae0  refactor!: borrar la superficie HTTP de lectura (FASE D)   -2652 lineas
f884e90  fix: borrar el simulacro get_ranch_parcels y su step (E.1)
9754083  docs: poner HANDOFF y PLAN al dia
```

**Lo único entre hoy y FASE C es A-3**, y no depende de escribir código sino de
tener el dominio público de MinIO y las credenciales de `worker-rw`.

---

## 1. El hallazgo que reordenó la sesión

La sesión empezó armando un roadmap a partir del `PLAN.md`, que pone FASE C como
lo que sigue con la condición *"antes de empezar, resolver A-3"*. Al ir a
ejecutar A-3 apareció que **tiene un prerrequisito que ningún doc escribió**:

| | |
|---|---|
| `.venv` | no existía |
| Dependencias | ninguna instalada |
| `pytest` | no llegaba a colectar — `ModuleNotFoundError: No module named google` |
| `rasterio==1.3.10` | wheels hasta cp312, contra los Pythons 3.13 y 3.14 de la máquina |
| `requirements.txt` | 3 de 16 paquetes pinneados |
| Árbol de git | 47 archivos sin commitear |

El worker **nunca había sido ejecutable en esta máquina**. Con lo cual A-3 —"el
worker nunca escribió en el MinIO real"— no era una tarea pendiente por falta de
prioridad: no se podía ni intentar. Y la compuerta VERIFY del `WORKFLOW`
—`pytest` verde, *"el peso recae entero en los tests"*— era inaplicable para
este repo desde siempre.

El pin no fallaba con un mensaje claro: se ponía a compilar `rasterio` contra
headers de GDAL en Windows.

**Se eligió Python 3.13 en vez de instalar 3.12 para respetar el pin viejo.**
No había paridad con producción que preservar: el worker no está desplegado y no
tiene Dockerfile. `1.3.10` no era una versión validada contra nada real, era el
estado en que había quedado. `rasterio` 1.3.10 → 1.4.3 y `rio-cogeo` 5.3.6 →
5.4.2, y los 16 paquetes pinneados. (`DECISIONS #22`.)

La regla que quedó: **`pip install --only-binary=:all: -r requirements.txt` tiene
que pasar sin compilar nada.** Es lo que convierte "compila si tiene que
compilar" en un error temprano y legible; un pin sin wheel falla distinto en cada
máquina y el mensaje nunca dice cuál es el problema real.

---

## 2. El commit de respaldo, y por qué se revirtió una decisión

FASE D borra `routes/`, `schemas/`, `auth.py` y `services/auth/`. Sobre un árbol
con 47 archivos sin versionar, ese borrado **no tenía vuelta atrás**.

El worker venía sin commitear por decisión explícita —etapa demo, el historial
granular no le paga a nadie todavía—. Se revirtió para este caso: editar sin
commitear es una cosa, un borrado masivo sobre un árbol sin versionar es otra.
Ahí el commit deja de ser burocracia y pasa a ser la red de seguridad.

`af229b4` captura el estado tal cual estaba, **sin arreglar nada**: entra la
deuda conocida —`rewrite_handlers*.py`, el simulacro, el
`DOCUMENTACION_TECNICA.md` duplicado— para que el punto de retorno sea limpio y
los borrados vengan después, uno por commit.

De paso F.6: `.gitignore` cubre `scratch/`, `test.tif`, `test_file.txt` y
`*.tif`. Se verificó antes de commitear que `.env` no estuviera trackeado y que
no hubiera ninguna credencial en lo que se iba a agregar.

---

## 3. FASE D — la superficie HTTP (`DECISIONS #23`)

Decidida el 2026-08-21, sin ejecutar desde entonces. Superficie resultante:
**`GET /health` y `/api/inngest`, y nada más.**

Se borró `routes/` (10 archivos, 8 ni siquiera montados), `schemas/`, `auth.py`,
`services/auth/` —con un Keycloak que no usaba nadie—, las 6 funciones de lectura
de `db_repository.py` (387 → 228 líneas), `services/db.py`, `/upload-kml`, el
`CORSMiddleware` con `allow_origins=["*"]`, `python-jose`, y `/docs`, `/redoc`
y `/openapi.json`.

**Por qué borrar y no arreglar.** `/assets` tomaba el `tenant_id` de un query
param y su `get_current_user_optional` se ignoraba: cualquiera podía leer los
assets de cualquier tenant escribiendo su id en la URL. Arreglarlo era
reimplementar en Python el aislamiento que Geocore ya tiene testeado en C#.
Además esas lecturas apuntaban a una tabla `assets` que **no existe** en
`geodata` desde la migración a `layers`: llevaban semanas devolviendo 500 sin que
nadie lo notara, que es la mejor prueba de que nadie las usaba.

### Dos cosas que aparecieron al borrar

**`get_cached_dates` no era un import muerto, era una colisión de nombres.**
`inngest_handlers.py` importaba **dos funciones distintas llamadas
`get_sentinel2_dates`**: la de `services.ee.ee_client`, que consulta GEE, y la
del repositorio, aliasada. Nadie llamaba al alias. Quien lo hubiera quitado sin
mirar habría hecho que la del repositorio pisara a la de GEE, y las dos llamadas
del archivo le pasarían un ROI donde espera un `geometry_id`. Se eliminaron las
dos puntas.

**`sentinel2_dates` es un caché de solo escritura.** Los handlers insertan una
fila por fecha en **cada** pedido y nadie lee la tabla — la única función que la
consultaba era la del párrafo anterior. Se paga el costo de mantener un caché y
no se cobra ninguno de sus beneficios: cada pedido pega a GEE completo igual.
Quedó como `PREGUNTAS_ABIERTAS` **A-4**, con la recomendación de decidirlo junto
con FASE C, que cambia el patrón de acceso a GEE y por lo tanto qué conviene
cachear.

### La compuerta

`tests/test_api.py` probaba justamente las rutas borradas y era **el único test
del repo**. Lo reemplaza `tests/test_http_surface.py`, que no prueba rutas: fija
la superficie como **igualdad exacta**, no como "contiene".

Una decisión como "el worker no expone API de lectura" se erosiona sola —agregar
un `@app.get` es una línea y nada avisa—. Lo que se borró había llegado por ese
camino. Ahora avisa el test.

---

## 4. E.1 — el simulacro

`process_rancho` no podía terminar nunca. Su step `_calculate_measurements`
llamaba a `services/geocore_client.py`, que a pesar del nombre no hablaba con
Geocore: partía el bbox del rancho por la mitad, inventaba dos parcelas y les
daba ids `{ranchoId}-parcela-A` y `-B`. Esos ids iban a `insert_measurement`
contra `measurements.parcela_id`, que es `uuid`.

**Se borró el step y el módulo entero, en vez de reemplazarlo por la llamada HTTP
real.** El caso ya está cubierto: Geocore emite `terra/parcela.created` por cada
parcela y `process_parcela` la procesa con su id y su geometría de verdad. Poner
una llamada real habría pedido un endpoint nuevo en Geocore, auth
service-to-service y una dirección de acoplamiento worker → Geocore que hoy no
existe, para duplicar algo que ya funciona por evento.

De paso desaparece un gasto de cuota: el step pedía a GEE un **segundo composite
del mismo período** que el que ya había descargado el step de arriba, solo para
reducirlo por parcela.

`process_rancho` queda con una responsabilidad sola: el ráster a nivel rancho.
Descarga, COG, subida, y emisión de `terra/raster.ingested`, que `register_layer`
consume para escribir en `layers`.

---

## 5. Lo que se encontró y NO se arregló

Todo esto es de `services/storage_service.py`, y **los tres se cruzan con el
Paso 3**. Detalle en `HANDOFF.md` §4.

### Falta `region` en el cliente de MinIO — es `DECISIONS #21` otra vez

`StorageService.__init__` construye `Minio(endpoint, access_key, secret_key,
secure)` **sin `region`**. Verificado en el paquete instalado,
`minio/api.py:481`:

```python
def _get_region(self, bucket_name=None):
    if self._base_url.region:
        return self._base_url.region      # el atajo que no se esta tomando
    ...
    response = self._url_open("GET", "us-east-1", bucket_name=bucket_name,
                              query_params={"location": ""})   # GetBucketLocation
```

Sin `region`, la primera operación contra el bucket dispara un
`GetBucketLocation`, que exige un permiso **que la subida real no usa**. Es
exactamente la trampa que costó una sesión entera del lado del tileserver, ahora
del lado de escritura: *un chequeo más estricto que el sistema que chequea no es
más seguro, es una fuente de falsos negativos*.

**En el Paso 3 esto se va a ver como un `AccessDenied` que parece un problema de
credenciales de `worker-rw` y no lo es.** Conviene arreglarlo antes de disparar
A-3, no después de perder la tarde.

### El singleton es *eager* y hace I/O en el constructor

```python
storage_service = StorageService()   # ultima linea del modulo
```

y `__init__` llama a `ensure_bucket()`. Consecuencias: `import app` abre un
socket y reintenta cinco veces si no hay nadie del otro lado —por eso la suite
tarda ~20 s—, la config se lee al importar y no se puede cambiar después, y un
fallo de construcción está tapado por un `try/except` que imprime un warning, con
lo cual una URL mal configurada arranca "bien" y falla recién en la primera
subida.

El singleton en sí **está bien**: el motivo correcto es compartir el pool de
conexiones de urllib3, no garantizar unicidad. Lo que está mal es que sea eager.
La forma idiomática es una factory perezosa con `functools.lru_cache`, que es
exactamente lo que `repositories/db_repository.py:13` ya hace bien para la DB
(`_pool = None` y se construye en el primer `get_connection()`).
`storage_service` es el inconsistente, no el modelo.

### `ensure_bucket()` no debería correr en cada arranque

Crear el bucket es tarea de despliegue, hecha una vez. Y llama a
`bucket_exists()`, que necesita permisos de listado que una policy de solo
escritura no tiene por qué darle a `worker-rw`.

---

## 6. Lo que NO valida nada de esta sesión

- **El worker sigue sin escribir en MinIO real** (`PREGUNTAS_ABIERTAS` A-3). Es
  lo único entre hoy y FASE C, y no depende de código: depende del dominio
  público de la API de MinIO —sin puerto, con TLS, o sea `MINIO_SECURE=True`— y
  de las credenciales de `worker-rw`. El `.env` de esta máquina apunta a
  `localhost:9000` con la access key por defecto del docker-compose.
- **`rio-cogeo` 5.3 → 5.4 puede mover el COG** que produce
  `services/cog_converter.py`, que llama a `cog_translate` con
  `web_optimized=True` y `add_mask=True`. La FASE B verificó la composición por
  mediana con COGs armados por el spike, **no por este convertidor**. Que los
  COGs del worker se apilen bien en el mosaico se prueba en A-3.
- **`INNGEST_SIGNING_KEY` sigue sin cablearse** (`PLAN.md` F.1) y está vacío en
  el `.env`. Con `/docs` apagado y sin endpoints de negocio, el endpoint de
  Inngest es hoy la única superficie pública sin autenticar. Hay que cerrarla
  antes de exponer el worker, y se vuelve obligatoria si se usa Inngest Cloud,
  que exige URL pública.
- **Los tests no prueban ningún handler.** Los 4 que hay fijan la superficie
  HTTP. Ninguno toca `process_parcela`, `process_rancho` ni la conversión a COG.
  La compuerta existe; lo que cubre todavía es poco.
- **Nada está pusheado**, y el worker sigue sin Dockerfile ni deploy.
