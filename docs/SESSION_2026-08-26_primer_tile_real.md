# Sesión 2026-08-26 — El primer tile real

> Qué pasó y por qué. Estado permanente en [`HANDOFF.md`](HANDOFF.md).
> Qué sigue, en [`PLAN.md`](PLAN.md).

## Dónde quedó

**FASE A cerrada.** Las tres piezas se hablaron por primera vez: un COG en MinIO,
leído por el tileserver desplegado, con un token firmado por Geocore.

```
1. Liveness              /health — 200
2. Readiness             map_token_secret, minio_endpoint, minio_credenciales, minio — ok
3. Control de acceso     /cog/info sin token — 401
4. Token + lectura       /cog/info con token — dtype=float32 bandas=1
                         bounds: [-107.45, 24.7476, -107.3476, 24.85]
5. Un tile real          200, PNG de 22.058 bytes
6. Caché                 public, max-age=31536000, immutable
7. Borde del raster      200 + PNG de 68 bytes (transparente)
```

Los `bounds` son exactamente los del COG generado en local antes de subirlo: el
archivo viajó a MinIO y GDAL lo abrió por `/vsis3/` sin alterarlo. El tile de 68
bytes es el PNG transparente de 1×1, o sea el handler de `TileOutsideBounds`
funcionando.

**El worker sigue sin commitear**, por decisión explícita. Todo el trabajo de
esta sesión es del tileserver (`terra-tileserver`, 5 commits) y de variables de
entorno de Geocore.

---

## 1. Lo que se desplegó

`terra-tileserver` pasó de 1 commit a 6. El primero (`faa8e70`) subió el trabajo
del 21-ago que estaba solo en disco: la separación en `terra_tiles/`, el cierre
del fallback de `MAP_TOKEN_SECRET`, el filtro anti-SSRF y el Dockerfile que no
copiaba el código.

Los 47 tests pasaron en un Python 3.13 recién armado, no en el venv original de
3.11 — que además está muerto: se creó en otra máquina
(`C:\Users\USUARIO\MiProyectoRasters\`) y su intérprete no existe en esta. Que
pasen en un intérprete distinto es más fuerte que el número: confirma que la
separación de `terra_tiles/` sin TiTiler aguanta el cambio de entorno, que es
justo lo que pasa al construir la imagen.

---

## 2. `/health/ready`: el diagnóstico como código

Se agregó un chequeo de readiness separado del liveness. **`/health` no consulta
MinIO a propósito**: es el que Railway usa para decidir reinicios, y si
dependiera del storage un parpadeo de MinIO reiniciaría un tileserver sano —
donde el reinicio no arregla nada de lo que falló.

`/health/ready` comprueba cuatro cosas y traduce cada fallo a la variable que hay
que corregir, con un `cause` estable para no parsear texto en español:

| check | Qué detecta |
|---|---|
| `map_token_secret` | Falta el secreto: `/health` da 200 y **todos** los tiles dan 503 |
| `minio_endpoint` | La forma del endpoint, sin tocar la red |
| `minio_credenciales` | Si la credencial vino del entorno o del default silencioso |
| `minio` | Sondeo real contra MinIO |

Se justificó solo: **cada uno de los cuatro problemas que aparecieron en el
deploy fue diagnosticado por el chequeo, no adivinado.**

### El endpoint privado necesita puerto explícito

El primer fallo real. `MINIO_ENDPOINT=minio.railway.internal`, sin `:9000`.

La red privada de Railway **no hace mapeo de puertos**: sin puerto se asume el 80,
donde MinIO no escucha. Es exactamente al revés que el dominio público, que va
sin puerto porque el edge escucha en 443 y hace de proxy. Por eso se confunden.

| | Puerto | TLS |
|---|---|---|
| `<servicio>.railway.internal` | `:9000` obligatorio | `MINIO_SECURE=False` |
| `<algo>.up.railway.app` | sin puerto | `MINIO_SECURE=True` |

`comprobar_endpoint` ahora lo detecta **sin tocar la red**, junto con las otras
tres formas de confundir los dos dominios y el `localhost` heredado del
docker-compose.

### Clasificar el fallo de red

El mensaje original juntaba DNS, TLS, puerto cerrado y timeout en un solo texto,
y cada uno manda a revisar algo distinto. Al separarlos aparecieron **dos errores
propios**, encontrados reproduciendo los fallos contra sockets reales en vez de
con excepciones fabricadas:

- **Hablarle TLS a un puerto de texto plano no produce ningún error de SSL.**
  Produce un `ConnectionReset` envuelto en `ProtocolError`. La clasificación
  buscaba `sslerror` y por lo tanto nunca disparaba; el test pasaba porque usaba
  una excepción inventada con ese texto. Ahora el corte de conexión se interpreta
  según `minio_secure`, que es un dato que sí tenemos: con TLS pedido manda a
  apagarlo, sin TLS manda a encenderlo.
- **El texto de los errores de socket viene traducido al idioma del sistema.**
  "connection refused" no aparece en un Windows en español. Las señales se buscan
  sobre los nombres de clase, que son estables.

> La lección general: un test que usa una excepción fabricada por uno mismo
> prueba que el código hace lo que uno cree, no que la realidad se parezca a eso.

---

## 3. El `AccessDenied` que no era de la policy

El fallo más largo de la sesión, y el más instructivo.

Con la red ya funcionando, MinIO devolvía `AccessDenied` tanto al leer el objeto
como al consultar el bucket. Se descartaron, en orden: que la policy no tuviera
el sufijo `/*` en el `Resource` (lo tenía), que `tiler-ro` fuera una service
account y no un usuario (era usuario), que estuviera deshabilitado (`enabled`),
que la policy no estuviera asignada (`PolicyName: tiler-readonly`), que hubiera
un `Deny` heredado de un grupo (`MemberOf: []`), y que el bucket se llamara
distinto (`mc ls` mostró `terra-assets`).

**Todo estaba bien. El bug era del chequeo.**

`minio-py`, cuando no se le pasa `region`, resuelve la región llamando a
`GetBucketLocation` antes de cualquier operación sobre un bucket que no tenga
cacheado (`minio/api.py::_get_region`). Esa llamada previa era la que se denegaba
— por eso fallaban las dos operaciones idénticamente: no fallaba ninguna de las
dos, fallaba el paso común anterior.

Y **GDAL no hace esa llamada**: `/vsis3/` usa `AWS_REGION` directamente. El
chequeo estaba exigiendo un permiso que el camino real no usa, y reportaba roto
un tileserver perfectamente capaz de servir tiles.

> **La regla que faltaba, ahora escrita en el código:** el sondeo tiene que
> ejercitar los mismos permisos que el camino real, ni uno más. Se había evitado
> a conciencia con `s3:ListBucket` —la policy `readonly` de MinIO no lo incluye—
> y se volvió a cometer por otra puerta.

El cliente se construye con `region=settings.aws_region`, la misma que
`configure_gdal()` le pasa a GDAL.

### Lo que sí aportó el camino largo

Que `s3:ListBucket` estuviera otorgado permitió agregar un **segundo sondeo que
desambigua el `AccessDenied`**, que hasta entonces era el único resultado del que
no se podía concluir nada. Preguntar por el bucket usa un permiso distinto del
que acaba de fallar:

- responde que sí → era MinIO devolviendo 403 en vez de 404 para no filtrar qué
  keys existen. Los tiles funcionan.
- responde que no → no hay ningún bucket con ese nombre.
- vuelve a denegar → la policy no llega a ese usuario.

Si el segundo sondeo no se puede hacer, se devuelve el `degradado` original: **no
haber podido concluir no es lo mismo que haber concluido que está roto**. Lo
encontró un test que falló cuando el fallback reportaba un error de red inventado.

---

## 4. Geocore no podía firmar tokens

Con el tileserver listo, `GET /api/maps/token` devolvía:

```
Format of the initialization string does not conform to specification starting at index 0
```

`MapsController` no toca la base —solo lee configuración y firma un JWT—, pero
**`TenantMiddleware` sí, en toda request autenticada**: verifica `IsActive` en
cada una (`[M-1]`), y eso va a Postgres antes de llegar a cualquier controller.

```
TenantMiddleware.cs:29     users.GetByAuthIdAsync(...)
UserRepository.cs:21       db.Users...
GeocoreDbContext.cs:11     get_Users()
  → NpgsqlConnectionStringBuilder..ctor()   ✗
```

Dos problemas, los dos de variables de entorno:

- **`ConnectionStrings__Default` estaba en formato URI.** Npgsql no lo acepta:
  necesita `Host=…;Port=…;Database=…;Username=…;Password=…;SSL Mode=Require`. El
  stack trace lo confirmó y además descartó la otra hipótesis: una cadena vacía
  se parsea como cero pares sin error, así que fallar en `GetKeyValuePair` en el
  índice 0 significa que había contenido con el formato equivocado.
- **`ConnectionStrings__GeoData` no existía.** Y `geodata` no es otra base del
  mismo servidor: es **otro proyecto de Supabase** (se confirmó contra el `.env`
  del worker). Con la variable ausente, `DependencyInjection.cs:25` cae a
  `Default` y Geocore se pone a buscar `layers`, `measurements` y
  `processing_jobs` en el proyecto principal, donde no existen. No falla al
  arrancar: falla la primera vez que alguien pide una capa.

Es el modo de fallo que `DECISIONS #15` anticipó por escrito para este contrato.

---

## 5. `scripts/check_prod.py`

Verifica un tileserver desplegado en siete escalones, cada uno agregando
exactamente un eslabón, de modo que el primero que falla señala la causa. Traduce
cada código a la variable concreta: 400 → `MINIO_BUCKET`, 401 con token → el
secreto compartido o el vencimiento, 500 → la policy de `tiler-ro`.

El tile se elige desde `center` de tilejson, que ya trae el zoom que el ráster
resuelve — pedir un z/x/y a mano es la forma más fácil de depurar un tile vacío
creyendo que es un problema de permisos.

Solo librería estándar, para que corra con cualquier Python 3.9+ sin instalar
nada. El token nunca se imprime: va en el query string y esta salida se pega en
chats.

---

## 6. Correcciones a lo que se afirmó en esta sesión

- **Se dijo que `mc mb terra_assets` habría rechazado el guion bajo.** Falso:
  minio-py acepta el guion bajo en su validación no estricta e hizo la petición.
  La duda se cerró de otra forma — `mc ls` mostró que el bucket real es
  `terra-assets`, con guion medio.
- **Se dio como hipótesis principal que a la policy le faltaba el `/*`.** Era
  incorrecta: la policy estuvo bien todo el tiempo (§3).
- **La clasificación de fallos TLS nunca disparaba** por buscar `sslerror` (§2).
- Al inspeccionar el `.env` del worker, un enmascarado incompleto **imprimió la
  contraseña de la base** en formato `CLAVE=valor`. Quedó en el historial de la
  conversación. **Rotarla si ese transcript se comparte.**

---

## 7. Deuda que apareció de paso

- **`requirements.txt` del tileserver pinnea `titiler.core==0.18.0` pero no
  `rasterio` ni `GDAL`**, que entran como dependencias transitivas. Un rebuild
  dentro de unos meses puede traer otro GDAL sin que nadie lo decida.
- **El `.venv` del tileserver está muerto** (creado en otra máquina). No afecta al
  deploy — el Dockerfile instala desde `requirements.txt` — pero rompe cualquier
  intento de correr algo en local con un error que no menciona la causa.
- **El COG de prueba quedó en el bucket** bajo un rancho inventado
  (`ranchos/00000000-0000-0000-0000-000000000001/`). Borrarlo cuando ya no sirva
  de dato de prueba para el spike de MosaicJSON.
- **El `.env` del worker tiene `DB_PASSWORD= <clave>`, con un espacio después del
  `=`.** `python-dotenv` lo recorta, pero copiado tal cual a Railway el espacio
  viaja y produce un error de autenticación que no sugiere que sobra un carácter.
