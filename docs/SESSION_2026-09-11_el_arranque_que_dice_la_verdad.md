# Sesión 2026-09-08 → 09-11 — El arranque que dice la verdad, y el primer flujo corriendo

> Qué pasó y por qué. Estado permanente en [`HANDOFF.md`](HANDOFF.md).
> Qué sigue: [`PLAN.md`](PLAN.md). Sesión anterior:
> [`SESSION_2026-09-07_logs_pedido_y_las_dos_claves.md`](SESSION_2026-09-07_logs_pedido_y_las_dos_claves.md).
> Del lado del tileserver, la misma semana:
> `tileserver-titiler/docs/SESSION_2026-09-11_logging_piloto_y_el_png_blanco.md`.

## Dónde quedó

**El flujo corre.** Inngest está registrado y invoca al worker, la firma se
verifica, Earth Engine compone (`Sentinel-2: 7 imagenes para componer`), el COG
se sube a MinIO y el tileserver lo sirve. La contraseña de `geodata` —lo único
que quedaba de A-3 del lado del worker— está resuelta.

**169 tests.** Todo pusheado a `TechSupportKaapeh/geeworker2`:

| Commit | Qué |
|---|---|
| `84b5586` | `scripts/check_db.py`: separa "mala contraseña" de las otras cuatro causas |
| `55a2ca9` | el worker reporta su modo y su configuración al arrancar |
| `8a5f148` | verifica que las conexiones se hayan hecho de verdad |
| `37f0b61` | `serve()` de Inngest tumbaba el proceso al importar |
| `a70359e` | verifica que la carpeta de outputs sea escribible |

El hilo de toda la semana es uno solo, y apareció tres veces con la misma forma:
**la variable está puesta, el reporte la muestra como "definida", y no sirve.**
`DB_PASSWORD` rechazada por Supabase, `INNGEST_SIGNING_KEY` vacía, y
`BASE_OUTPUT_DIR` apuntando afuera del contenedor. Un reporte de configuración
dice **qué hay**; sólo conectar, escribir o consultar dice **si funciona**. Eso
terminó en `DECISIONS #27`.

---

## 1. La contraseña de `geodata`, y un diagnóstico mío que estaba mal

El síntoma era `password authentication failed for user "postgres"`. Primero lo
atribuí a Railway —un valor pegado con espacios, o una variable que no se
aplicó—. **Estaba mal.** `scripts/check_db.py` lo desmintió en un minuto: fallaba
igual desde la máquina local.

Lo que resolvió el caso fue un discriminador. Contra el pooler de Supabase, el
mismo mensaje sale por cinco causas. Pero un ref de proyecto inventado da otra
cosa:

| Usuario enviado | Respuesta del pooler |
|---|---|
| `postgres.reeeefinexistente` | `(ENOTFOUND) tenant/user` |
| `postgres.ubddxlfxmdzqfwazsdle` | `password authentication failed` |

Que el segundo mensaje sea **distinto** prueba que el pooler encontró el
proyecto: host, región, puerto, formato de usuario y ref estaban bien. Sólo
quedaba la contraseña. De paso: `db.<ref>.supabase.co` ni resuelve —el proyecto
sólo tiene pooler— y `aws-1-us-east-1` no es su región.

Un hallazgo lateral que vale para siempre: el `.env` local tiene un **espacio
después del `=`** en `DB_PASSWORD`. `python-dotenv` lo recorta y por eso anda en
local. **Railway no recorta nada**: el mismo valor pegado ahí llega con un
carácter de más. El script, y después el reporte de arranque, marcan bordes
sucios sin mostrar el valor.

## 2. El reporte de arranque (`55a2ca9`)

`utils_pkg/arranque.py`. Una línea por variable, agrupadas por subsistema,
encabezadas por el modo efectivo, y al final **sólo lo que va a fallar y por
qué**. La consecuencia escrita es lo que lo vuelve accionable: sin ella hay que
ir al código a averiguar si la variable importa.

- **Nunca un carácter de un secreto.** Sólo el largo. Seis tests existen para
  verificar que ningún secreto de ejemplo aparece, ni entero ni en prefijos de 8.
- Marca `INNGEST_EVENT_KEY` con el valor de desarrollo (`dev-local-key`): que la
  variable exista no alcanza, y es un caso que hay que poder ver sin ver el valor.
- **No recalcula el modo**: recibe el `IS_PRODUCTION` de `config`. Dos criterios
  para lo mismo es el bug que dejó al worker sin verificación de firma con
  `ENVIRONMENT=prod`.

Los tests encontraron un defecto del primer diseño: `MINIO_SECURE` y `PORT` no
tienen default y **está bien que falten**. El reporte las contaba como faltantes.
Se agregó el estado `opcional`. Un reporte con falsos positivos deja de leerse.

## 3. Que las conexiones se hayan hecho de verdad (`8a5f148`)

`utils_pkg/conexiones.py`. La razón concreta: **el arranque no podía distinguir
una base caída de una que anda.** `init_db()` atrapa su propia excepción y la
loguea, así que el `try/except` de `app.py` nunca la veía.

Cada chequeo **dice hasta dónde llegó**, porque `DECISIONS #21` enseñó que un
chequeo que pide más que el trabajo real inventa fallas:

| Chequeo | Qué prueba | Qué no |
|---|---|---|
| `outputs` | crea la carpeta y escribe un archivo | — |
| `minio` | DNS, TCP y el handshake TLS real | **credenciales**: cualquier operación barata pide un permiso que la subida no usa. Eso lo prueba `check_write_path.py` |
| `geodata` | conecta, autentica, consulta; informa usuario, base y si están las 3 tablas | — |
| `gee` | `init_ee()` **y** un round-trip (`ee.Number(1).getInfo()`) | — |
| `inngest` | modo y alcance de salida | que la app esté registrada: esa conexión la abre Inngest |

Dos defectos aparecieron **sólo contra infraestructura real**, no en los tests:
el error de psycopg2 trae saltos de línea y viene repetido (la primera copia con
el nombre de la excepción adelante), y el de socket lo emite Windows con
acentos. `a_una_linea()` los sanea en el constructor de `Resultado`, así
cualquier chequeo nuevo hereda la garantía.

## 4. `serve()` mataba el proceso al importar (`37f0b61`)

Con `ENVIRONMENT=production` puesto y sin `INNGEST_SIGNING_KEY`, el deploy entró
en bucle. **El SDK no rechaza peticiones: se niega a construirse**
(`inngest/_internal/comm_lib/handler.py:62`, `SigningKeyMissingError`). Como
`inngest.fast_api.serve()` se llama a nivel de módulo, se lleva uvicorn.

**Yo había dicho lo contrario**, y estaba escrito así en un comentario de
`inngest_client.py`: "el fallo cerrado ya lo garantiza el SDK: rechaza la
petición". Corregido en el código con la fecha.

Y esta vez **también falló el diagnóstico**: el reporte de arranque vivía en
`@app.on_event("startup")`, que nunca se dispara si el módulo no termina de
importarse. Salió el traceback pelado.

- El reporte pasó a nivel de módulo, antes de `serve()`. Es seguro ahí: sólo lee
  `os.environ` y loguea.
- `serve()` va en `try/except`. Sin él la ruta `/api/inngest` no existe y todo
  POST se va con 404: se sigue fallando cerrado, pero el worker queda arriba
  para decir por qué.
- `/health` responde **200 con `status: "degradado"`** en el cuerpo. Con 503,
  Railway reiniciaría en bucle.

Cuarta vez que algo a nivel de módulo tumba este repo, después de F.13,
`init_ee` fuera del try y `os.makedirs` en `config`.

Un detalle que explica por qué no se veía en local: una `INNGEST_SIGNING_KEY`
**definida pero vacía** no dispara el error, porque `""` no es `None`. Monta, y
después contesta 401 en cada petición. El `.env` local la tiene vacía; Railway,
ausente.

## 5. `BASE_OUTPUT_DIR` apuntaba afuera del contenedor (`a70359e`)

Con los eventos ya llegando, cada job fallaba en el intento 1 de 4 con
`[Errno 13] Permission denied: '../outputs'`. El valor venía heredado del `.env`
local; desde `/app` resuelve a **`/outputs`**, fuera del árbol que el Dockerfile
le da al usuario `worker` (`mkdir -p /app/outputs` + `chown`).

El reporte tenía el dato delante y no lo vio: imprimía `definida ../outputs`, que
no parece nada. **Lo que delata el problema es la ruta resuelta.** El chequeo
nuevo la informa siempre en absoluto. El arreglo en Railway fue borrar la
variable: el default `./outputs` es exactamente lo que el Dockerfile prepara.

## 6. MinIO por la red privada, y un error de OpenSSL que engaña

El worker hablaba con MinIO por el dominio público: salía a internet para
llegarle a un servicio que tiene al lado, pagando egress. Se pasó a
`bucket.railway.internal:9000`.

Al hacerlo apareció `[SSL: WRONG_VERSION_NUMBER]`, que **no es un problema de
versiones**: significa "mandé un ClientHello de TLS y del otro lado contestaron
algo que no es TLS". OpenSSL lee texto plano de HTTP como si fuera la cabecera
de un registro TLS y reporta el campo de versión. Había quedado `MINIO_SECURE=true`
del dominio público. **Se borra la variable**: `tls_por_defecto()` reconoce
`.railway.internal` y decide sin TLS solo, hoy y si mañana se vuelve al público.

El chequeo de `minio` lo mostró con el diagnóstico entero en una línea: *"TCP
abre pero TLS falla"*. TCP abre → host y puerto bien; TLS falla → sólo el modo.

Los `TCP_INVALID_SYN` que aparecieron en el log de flujos de Railway eran de
TiTiler leyendo COGs: muchas conexiones cortas de range requests, y una ventana
de captura de 45 ms que ve paquetes cuyo handshake nunca presenció.

## 7. Inngest: las dos claves, y para qué sirve cada una

- **`INNGEST_SIGNING_KEY`** protege `/api/inngest` y firma el sync de la app.
- **`INNGEST_EVENT_KEY`** hace falta porque el worker **también emite**:
  `process_rancho` termina con `step.send_event("terra/raster.ingested")`, y ese
  evento lo consume `register_layer`, que es quien escribe en `layers`. Sin ella,
  el COG se sube y la capa nunca se registra — sin ningún error.
- Tiene que ser **la misma** que `Inngest__EventKey` de Geocore, o los eventos
  caen en otro environment y nadie los recibe.

## 8. Variables de entorno en Railway, versión final

| Variable | Valor |
|---|---|
| `ENVIRONMENT` | `production` |
| `INNGEST_SIGNING_KEY` | `signkey-prod-…` |
| `INNGEST_EVENT_KEY` | la misma que `Inngest__EventKey` de Geocore |
| `MINIO_ENDPOINT` | `bucket.railway.internal:9000` |
| `MINIO_ACCESS_KEY` / `MINIO_SECRET_KEY` | `worker-rw` |
| `DB_HOST` | `aws-0-us-east-1.pooler.supabase.com` |
| `DB_PORT` / `DB_NAME` | `5432` / `postgres` |
| `DB_USER` | `postgres.ubddxlfxmdzqfwazsdle` |
| `DB_PASSWORD` | sin espacios en los bordes |
| `EE_SERVICE_ACCOUNT_EMAIL` / `EE_SERVICE_ACCOUNT_KEY_JSON` | la service account |

**No poner:** `MINIO_SECURE` (se deduce del host), `INNGEST_BASE_URL` (en
producción el SDK va a Cloud), `BASE_OUTPUT_DIR` (el default es el correcto),
`PORT` (lo inyecta Railway).

## 9. El primer heatmap real, visto desde el tileserver

`parcelas/3ef66cde-625b-44bb-91b9-f38384575965/2024-05-01_2024-07-31_ndvi.tif`:
`float32`, una banda, máscara interna, **256×256 px alineado exacto al tile de
zoom 14** `3626/6981`. Tiene dato en **35 píxeles de 65.536 (0,05 %)**, con NDVI
entre −0,03 y 0,18 — zona urbana, en Monterrey.

Mostró dos cosas. Del lado del tileserver, un bug que no era del worker (ver la
sesión del tileserver). Del lado del worker, una pregunta abierta: ¿la parcela
es chica de verdad, o el recorte salió mal? Ver abajo.

---

## Lo que esta sesión NO arregló

- 🔴 **El token de mapa no está atado al tenant.** `MapsController` firma sólo
  `sub` y `type`; TiTiler sólo mira que la ruta esté en el bucket; y las claves
  del worker son `parcelas/{uuid}/…`, sin tenant. Cualquier usuario activo de
  cualquier tenant puede leer cualquier COG cuya ruta conozca. La única barrera
  es el UUID. El arreglo pasa por la forma de la key (`PREGUNTAS_ABIERTAS` A-7):
  tenant en la ruta, tenant en el token, y TiTiler comparando.
- ⏳ **El heatmap con 35 píxeles.** Puede ser una parcela de prueba chica
  dibujada sobre la ciudad, o un recorte mal hecho. Hay que mirar la geometría
  de esa parcela contra el tile que exportó `generate-heatmap-on-demand`.
- **`register_layer` no se verificó explícitamente.** El COG existe y se sirve;
  que la fila de `layers` esté bien no se miró con una consulta.
- **Los chequeos suman ~10 s** antes de que `/health` conteste. Acotados por
  timeouts cortos; si el healthcheck de Railway se pone estricto, es lo primero
  a mirar.
