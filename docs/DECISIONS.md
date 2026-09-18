# DECISIONS.md — Decisiones de diseño y arquitectura

Registro de las decisiones relevantes tomadas en la construcción de Geocore y Terra Admin. El propósito es dar contexto sobre el *por qué*, no solo el *qué*.

---

## 1. Arquitectura hexagonal (Ports & Adapters)

**Decisión:** El dominio no conoce EF Core, PostGIS, Supabase ni Inngest. Solo conoce interfaces (`IParcelaRepository`, `IGeospatialService`, `IEventPublisher`) definidas en Application.

**Por qué:** El dominio puede probarse sin base de datos ni servicios externos (los tests de Domain son puramente in-memory; conteo vigente: `dotnet test`). Los adaptadores (EF, PostGIS, Inngest) se intercambian sin tocar reglas de negocio.

**Consecuencia:** Los value objects `GeoPolygon` y `GeoPoint` son coordenadas puras; la conversión a tipos NTS/PostGIS vive exclusivamente en `GeoConverter.cs` (Infrastructure).

---

## 2. Supabase Auth, no auth propio

**Decisión:** Geocore no maneja passwords, tokens, lockout ni verificación de email. Todo eso es Supabase Auth.

**Por qué:** Implementar auth seguro requiere tiempo y superficie de ataque enorme. Supabase ofrece OAuth, MFA, magic links, gestión de sesiones — gratis y auditado.

**Consecuencia:** El campo `AuthId` en `User` es el `sub` del JWT de Supabase. Las contraseñas nunca tocan Geocore.

---

## 3. JWT ES256 validado via JWKS, no secret compartido

**Decisión:** La validación usa `MetadataAddress` apuntando al JWKS endpoint de Supabase (`/.well-known/openid-configuration`), no un secreto hardcodeado.

**Por qué:** ES256 usa clave pública/privada. Supabase firma con su clave privada; Geocore verifica con la pública que descarga del JWKS. No hay secreto que rotar ni compartir.

**Consecuencia:** Si Supabase rota claves, Geocore las recoge automáticamente al siguiente request.

---

## 4. `global_role` via Auth Hook, no tabla de roles

**Decisión:** El rol global del usuario (`Client`, `TerraAdmin`, `TerraSupport`) se inyecta en el JWT via un Hook de PostgreSQL (`custom_access_token_hook`), no se consulta en cada request.

**Por qué:** Evita un query adicional a la DB en cada request autenticado. El rol no cambia frecuentemente.

**Consecuencia:** Si se cambia el `GlobalRole` de un usuario en Geocore, el cambio solo tiene efecto en el próximo JWT (próximo login o refresh). Los tokens activos siguen con el rol anterior hasta expirar.

---

## 5. X-Tenant-ID como header, no como JWT claim

**Decisión:** El tenant de contexto viene del header `X-Tenant-ID`, no del JWT.

**Por qué:** Un usuario puede pertenecer a múltiples tenants. El JWT solo prueba identidad (Supabase lo firma); el contexto de operación (qué tenant estás usando ahora) es responsabilidad del cliente. El panel admin guarda el tenantId seleccionado y lo envía en cada request.

**Consecuencia:** `TenantMiddleware` valida que el usuario sea miembro activo del tenant declarado. TerraAdmin/TerraSupport hacen bypass para tener acceso transversal.

---

## 6. Creación de usuarios via Edge Function (operación atómica)

**Decisión:** Crear un usuario requiere pasar por la Edge Function `create-user`, no llamar directamente a Geocore.

**Por qué:** Crear un usuario requiere dos operaciones: crear en Supabase Auth (para credenciales) y crear en Geocore (para el perfil con `AuthId`). Si se hicieran desde el cliente, cualquier fallo dejaría el sistema inconsistente. La Edge Function ejecuta ambas y hace rollback si Geocore falla.

**Consecuencia:** El panel admin (`createUserFull`) llama a la Edge Function, no a `POST /api/users` directamente.

---

## 7. Enums almacenados como string, no como int

**Decisión:** `GlobalRole`, `UserTenantRole`, `UserTenantStatus`, `TenantStatus` se guardan como `text` en PostgreSQL.

**Por qué:** Los integers son opacos en la DB — un `2` en `global_role` no dice nada sin ir al código. Como el soporte de Terra trabaja directamente en el dashboard de Supabase, los strings son legibles directamente.

**Consecuencia:** La migración `EnumsToString` convirtió los datos existentes con CASE statements SQL. EF Core usa `.HasConversion<string>()`.

---

## 8. Paginación offset, con nota sobre cursor-based

**Decisión:** Los endpoints de listado (`GET /api/users`, `GET /api/tenants`) usan offset-based pagination con `page` y `pageSize`.

**Por qué:** Es simple de implementar y suficiente para el volumen actual. El panel admin no necesita saltar a páginas arbitrarias, pero sí se aprovecha de conocer el total.

**Cuándo migrar a cursor-based:** Si el volumen crece (feeds en tiempo real, millones de filas) — `WHERE id > lastId LIMIT n` no desplaza resultados ante inserciones concurrentes.

---

## 9. FuentesValidas como set en el dominio

**Decisión:** `Rancho.FuentesValidas` es un `IReadOnlySet<string>` definido en la entidad: `["manual", "kml", "geojson", "wkt", "gee"]`.

**Por qué:** La validez de una fuente geométrica es una regla de negocio, no de infraestructura. El dominio la posee.

**Consecuencia:** Agregar `"drone"` u otro formato requiere modificar el dominio (correcto) y regenerar tests. `GeometryInput` en el frontend ya setea `fuenteGeom` automáticamente según el tab usado.

---

## 10. GeoConverter invierte coordenadas para NTS

**Decisión:** `GeoConverter.cs` invierte el orden lat/lng al mapear a NetTopologySuite.

**Por qué:** El dominio usa la convención humana (Lat primero, Lng segundo). NTS usa la convención matemática (X=Lng, Y=Lat). La inversión vive en un único lugar (GeoConverter) y nunca toca el dominio ni los controllers.

---

## 11. ngrok como tunnel para desarrollo local

**Decisión:** La Edge Function de Supabase llama a Geocore via un tunnel ngrok, no directamente a `localhost`.

**Por qué:** Las Edge Functions de Supabase corren en Deno Deploy (cloud). No pueden llegar a `localhost` de tu máquina. ngrok expone la API local con una URL HTTPS pública.

**Limitación conocida:** El plan free de ngrok cambia el subdominio en cada reinicio. El secret `GEOCORE_API_URL` en Supabase y `VITE_GEOCORE_URL` en `.env.local` deben actualizarse cuando cambia.

---

## 12. Soft delete obligatorio

**Decisión:** Usuarios, tenants, ranchos y parcelas nunca se eliminan físicamente. Solo se desactivan (`IsActive = false`).

**Por qué:** Los datos geoespaciales y de membresía tienen valor histórico y auditivo. Una parcela inactiva sigue siendo referencia para análisis pasados.

**Consecuencia (2026-06-12):** El soft delete debe ser reversible. Se agregaron `Rancho.Activate()`/`Parcela.Activate()` (dominio, idempotentes), `ActivateAsync` (servicio, con el mismo check de tenant que `Deactivate`) y `POST {id}/activate` (API).

---

## 13. Visor de geometría de solo lectura separado del editor

**Decisión:** La visualización de geometría ya guardada vive en un componente aparte (`GeometryView`), distinto del editor (`GeometryInput`). `GeometryView` es presentacional puro: recibe `Shape[]` (`{ coordinates, color?, label? }`) y dibuja, sin estado ni callbacks.

**Por qué:** Editar y visualizar tienen responsabilidades opuestas (uno parsea/emite, el otro solo renderiza). Una abstracción `Shape[]` dominio-agnóstica permite superponer rancho + sus parcelas en un mapa sin acoplar el visor al modelo. Además, las `coordinates` ya viajan en `RanchoDto`/`ParcelaDto` → no hace falta ningún endpoint nuevo para dibujar.

**Detalle de estabilidad:** En react-leaflet las props `center`/`zoom` de `MapContainer` **no son reactivas** tras el montaje; el encuadre se ajusta imperativamente con `useMap().fitBounds` dentro de un sub-componente `FitBounds`. Ese efecto depende de una **firma string de las coordenadas**, no del array `shapes` (que es nuevo en cada render) — así el mapa solo re-encuadra cuando la geometría cambia, no en cada render.

---

## 14. Librería de componentes UI: Base UI (Opción A, resuelto 2026-06-13)

**Decisión:** El panel usa **Base UI** (`@base-ui/react`). Las páginas se migraron a su API.

**Por qué Base UI y no Radix:** Era el stack ya instalado y completo — `@base-ui/react` es el único primitivo en `package.json`, los 12 componentes de `ui/*` ya estaban escritos sobre él (con `render`/`useRender`), y `shadcn@4` genera sobre Base UI. No había ningún `@radix-ui/*`. Las páginas eran lo único rezagado (escritas con la API vieja de Radix).

**Qué cambió en las páginas:**
- `<DialogTrigger asChild><Button/></DialogTrigger>` → `<DialogTrigger render={<Button/>} />`. (El `asChild` viejo era un bug latente: Base UI lo ignoraba y anidaba `<button>` dentro de `<button>`.)
- `onValueChange` ahora maneja `string | null` (Base UI emite `null` al limpiar): `?? valorPrevio` en formularios, guard (`if (!v) return`) en handlers.
- Se eliminó `baseUrl` de `tsconfig.app.json` (deprecado; innecesario en `moduleResolution: bundler`).

**Consecuencia:** `npm run build` vuelve a pasar (typecheck estricto + vite build). Patrón a seguir para nuevos componentes: API de Base UI, no Radix.

---

## 15. GeoData vive en su propia DB, con su propio DbContext (2026-08-14)

**Decisión:** El subdominio `GeoData` (`layers`, `measurements`, `processing_jobs`) usa un `GeoDataDbContext` separado apuntando a la connection string `GeoData`, distinta de `Default`.

**Por qué:** Es la DB del servicio de rasters/análisis descrito en `ARCHITECTURE_PLAN.md §8` — la escribe el worker, Geocore mayormente la lee. Compartir el `GeocoreDbContext` habría atado el ciclo de vida de las migraciones de los dos servicios y tentado a poner FKs cross-service. Se referencia **por ID, sin FK cross-DB**: `TenantId`/`ParcelaId`/`RanchoId` son `Guid` sueltos.

**Consecuencia:**
- Migraciones separadas: `--context GeoDataDbContext`, salida en `Persistence/Migrations/GeoData/`.
- En deploy hay que setear `ConnectionStrings__GeoData`. Si falta, `DependencyInjection` cae a `Default` — conveniente en local, peligroso en prod (crearía el esquema `geodata` dentro de la DB principal). Verificarlo en el checklist de deploy.
- ~~**Deuda conocida:** las columnas quedaron en PascalCase~~ → **corregido el 2026-08-17** con la migración `GeoDataSnakeCase`: 29 `RenameColumn`, sin drops, reversible. El mapeo pasó de estar inline en `GeoDataDbContext` a clases `IEntityTypeConfiguration` con `HasColumnName` explícito, igual que el contexto principal. Se hizo justo antes de que el worker empezara a escribir contra esas tablas; después habría dejado de ser una migración local para volverse un cambio coordinado entre dos repos.
- **Los nombres de columna de `geodata` son contrato entre repos.** Geocore crea las filas de `processing_jobs`, pero es el **worker** quien escribe `layers` y `measurements` y quien actualiza el estado de los jobs. Cambiar un nombre de columna acá rompe al worker en silencio: no hay compilador que lo agarre.

---

## 16. El token de mapa lo firma Geocore, lo valida TiTiler (2026-08-14)

**Decisión:** `GET /api/maps/token` devuelve un JWT HS256 de 1 hora (`aud: TiTiler`) firmado con `GeoData:MapTokenSecret`. El front lo agrega como `&token=…` a las URLs de tiles que arma `GET /api/layers/{id}`.

**Por qué:** TiTiler sirve los COG directo desde el bucket, sin pasar por Geocore — proxiar cada tile por la API sería un cuello de botella. Un secreto compartido HS256 es suficiente porque ambos lados son nuestros y el token es de vida corta; no hace falta la asimetría de ES256.

**Consecuencia:** `GeoData__MapTokenSecret` es obligatoria fuera de `Development`, y el mismo valor tiene que estar configurado en TiTiler.

### Revisión 2026-08-17 — el arranque ya no aborta

La versión original hacía que `Program.cs` **abortara el arranque** si el secreto faltaba. El motivo era real: `MapsController` tenía un `?? DevMapTokenSecret` que, sin config, firmaba con un secreto que está en el repo. Entre arrancar así y no arrancar, no arrancar era lo correcto.

**Pero el guard estaba compensando un defecto de raíz que estaba una línea más allá.** Se eliminó el fallback: hoy `ResolveSecret()` solo devuelve el secreto de desarrollo **si el entorno es Development**, y fuera de ahí devuelve `null`. Sin fallback, que falte la config ya no puede producir un arranque inseguro — produce un endpoint que no funciona.

Con la causa resuelta, tumbar toda la API por una feature de 1 endpoint entre ~35 es desproporcionado: `ranchos`, `parcelas`, `tenants` y `users` no tienen nada que ver con este secreto. Ahora:

- `GET /api/maps/token` devuelve **503** con código `MAP_TOKEN_UNAVAILABLE` si no hay secreto utilizable. Falla cerrado: no firma nada.
- El resto de la API arranca y funciona normal.
- `Program.cs` **loguea un Error** al arrancar en vez de lanzar.

**El costo aceptado:** un deploy mal configurado ahora **sube** con los mapas rotos, y hay que mirar los logs para enterarse — antes era imposible no notarlo. Se mitiga con el log de Error, pero es un cambio real de ruidoso-y-total a silencioso-y-acotado. El healthcheck de `railway.toml` sigue siendo `/health` (liveness): meter esta validación ahí reinventaría el bloqueo de arranque, más lento.

---

## 17. El KML lo parsea Geocore, y el nivel lo decide el endpoint (2026-08-16)

**Decisión:** `POST /api/kml/ranchos` y `POST /api/kml/ranchos/{id}/parcelas` reciben el archivo, lo parsean **en el servidor** y crean las entidades de forma **síncrona**, reusando `RanchoService.CreateAsync` / `ParcelaService.CreateAsync`. Se reemplazó `POST /api/kml/upload`, que mandaba el KML en base64 dentro del evento.

**Por qué:** el worker no crea ranchos — solo descarga y procesa el histórico satelital, disparado por `terra/rancho.created`. Mandarle el archivo (en base64 o como llave de bucket) era entregarle un trabajo que no hace. Al crear por los servicios existentes, el evento sale por el camino que ya funciona y el pipeline arranca solo.

**Por qué el nivel no se infiere del archivo:** los KML vienen de registros catastrales externos y no hay garantía de que la jerarquía esté codificada de forma consistente. Deducirla por contención geométrica es frágil con polígonos que se tocan o solapan; por carpetas, depende de una convención que nadie garantiza. El operador sabe qué está subiendo, así que lo dice eligiendo el endpoint. `KmlPlacemark.FolderPath` **sí** se captura, para poder revisar archivos reales y reconsiderar esto con datos; `POST /api/kml/preview` existe justamente para inspeccionar sin escribir.

**Consecuencias:**
- Se descartó agregar una librería de KML (`SharpKml.Core`): el archivo es input no confiable y se prefiere controlar explícitamente la configuración del `XmlReader` (DTD prohibido, sin resolver entidades) y cubrirla con tests, en vez de depender de la configuración interna de un tercero y revalidarla en cada bump. Ver `OWASP_TOP10.md` → A03/XXE.
- El backend **ahora sí parsea XML de usuario**, cosa que antes no hacía. Eso cambia la superficie de ataque y está reflejado en `OWASP_TOP10.md`.
- Lo que el parser no entiende del todo (KMZ, `MultiGeometry` con varios polígonos, huecos interiores) **se rechaza con error explícito**. En datos catastrales, importar de menos en silencio es peor que fallar.
- Topes: 10 MB por archivo (`KmlController`) y 1000 polígonos (`KmlParser`). El primero acota el archivo; el segundo, el trabajo que genera — cada polígono es un INSERT y un evento.
- `POST /api/ranchos/{id}/kml` (el que toma `KmlS3Key`) **se borró el 2026-08-17**, junto con `RequestKmlProcessingAsync`, el record `ProcessKmlRequest` y el evento `terra/kml.process.requested`. Nadie lo referenciaba y nunca llegaba a emitir su evento, porque ningún endpoint podía producir la llave que exigía.

> Mapa operativo de los tres caminos que empiezan en un KML, qué acepta y rechaza el parser, y el detalle de por qué ese endpoint es redundante: [`KML_CASOS_Y_REDUNDANCIA.md`](KML_CASOS_Y_REDUNDANCIA.md).

---

## 18. La publicación de eventos no es atómica: el outbox queda diferido a propósito (2026-08-17)

**El problema tiene nombre: *dual-write*.** Al crear un Rancho o una Parcela, Geocore escribe en dos sistemas que no comparten transacción: Postgres y el bus de eventos de Inngest. Hoy el orden es `SaveChanges` → `PublishAsync`. Si la publicación falla, la entidad ya está commiteada y el evento se perdió.

**Qué pasa hoy exactamente**, porque es más sutil que "se pierde en silencio":
- `InngestEventPublisher` relanza el error, así que quien llamó recibe un **500** — hay señal.
- Pero la entidad **ya está creada**. El llamador no sabe si se creó: si reintenta duplica, si no reintenta queda huérfana. Estado inconsistente sin nadie que reconcilie.
- El único caso realmente silencioso es que el proceso muera **entre** el `SaveChanges` y el POST (redeploy, OOM). Ahí no hay ni 500.

**Qué cubre Inngest y qué no.** Inngest es durable **a partir de que recibe el evento**: lo persiste, reintenta la función, mantiene el estado entre pasos. Nada de eso hay que construirlo. Lo que no puede hacer es enterarse de un evento que nunca le llegó — el hueco está *antes* de Inngest, y por eso su durabilidad no lo tapa.

**Decisión:** **no** implementar el Transactional Outbox por ahora. Mitigar el modo de fallo dominante —un hipo transitorio de Inngest— con reintento y backoff dentro de `InngestEventPublisher`. **Implementado el 2026-08-17:** 3 intentos, esperas de 1 s y 2 s.

Detalles que importan de esa implementación:
- **Presupuesto corto a propósito** (~3 s en el peor caso). El import masivo de KML publica un evento por polígono **en secuencia**, así que un presupuesto generoso se multiplica por la cantidad de entidades y cuelga el request hasta el timeout. La base es configurable por `Inngest:RetryBaseDelayMs`.
- **Los 4xx no se reintentan.** Un 401 por event key inválida o un 400 por payload mal formado fallan igual en el segundo intento; reintentarlos solo agrega latencia al error.
- **Cancelar no es fallar.** Si el token del llamador está cancelado, la excepción se propaga sin reintentar.

**Por qué se puede diferir, y por qué eso no aplicaba a otras deudas:** el outbox es **puramente interno**. No cambia ningún contrato, ningún esquema que otro repo consuma, ninguna API. Agregarlo dentro de seis meses cuesta exactamente lo mismo que hoy. Es lo contrario del rename de columnas de #15, que tenía una ventana que se cerraba en cuanto el worker empezara a escribir. Sin ventana que se cierre, y sin worker todavía consumiendo estos eventos, el costo de esperar es cero.

**⚠️ Lo que el reintento NO resuelve — no llamar a esto "resistente a errores":** si Inngest está caído más que los reintentos, o si el contenedor muere en la ventana, **el evento se pierde igual**. Esto es una mitigación parcial de fallos transitorios, no una garantía de entrega. Documentarlo como tolerancia a fallos sería falso y daría una sensación de seguridad que el sistema no tiene.

**Cómo sería el outbox, cuando toque.** Una tabla por base —hacen falta **dos**, porque Rancho/Parcela viven en la DB principal y ProcessingJob en la de GeoData (#15), y un outbox solo sirve en la misma base que la escritura con la que es atómico. Los servicios pasan a registrar el evento **antes** del `SaveChanges`, para que entren en la misma transacción; `IEventPublisher` no cambia de forma, solo se registra otra implementación que escribe la fila en vez de hacer HTTP. Un dispatcher en segundo plano drena las filas pendientes con reintentos, contador de intentos y `LastError` para que un mensaje veneno no bloquee la cola. Consecuencia a tener presente: entrega **at-least-once**, o sea que el worker **debe ser idempotente** — que es lo que `ARCHITECTURE_PLAN.md` §5 ya exige.

**Disparadores para dejar de diferirlo:** que exista el worker consumiendo de verdad; que aparezca tráfico real; o el primer incidente de evento perdido.

---

## 19. Se guarda por pasada, no por composite (2026-08-21)

> ⚠️ **2026-09-12: propuesta para reemplazarla por `#31`**, el histórico mensual
> con el compuesto armado en GEE. Sigue vigente hasta que se confirme.

**Decisión:** el worker descarga y guarda **un COG por pasada de satélite** (con
las correcciones ya aplicadas en GEE). Los composites mensual, semanal o de
cualquier otra ventana **no se guardan**: se derivan al momento de servir.

Lo mismo del lado de los datos: `measurements` guarda **una fila por pasada**, y
la agrupación por mes o semana es un `GROUP BY` al consultar.

**Por qué:** la agregación no se puede deshacer. De un composite mensual no se
deriva el semanal —la información de las pasadas ya se colapsó— pero de las
pasadas se deriva cualquier cadencia. Como el producto contempla que el usuario
elija la ventana, guardar composites obligaría a volver a GEE en cada elección.

Es el mismo principio que ya aplicaba `get_sentinel2_time_series`, que trabaja
pasada por pasada. Se extiende al mapa.

**Consecuencias:**

- **Más consumo de GEE en la ingesta, no menos.** ~73 pasadas al año contra 12
  composites mensuales. El ahorro llega después: una vez descargada la pasada,
  cambiar de cadencia no toca GEE. Es una inversión, no un ahorro directo.
- **El backfill es la parte cara.** Dos años × 73 pasadas × cantidad de ranchos
  concentra mucha operación de GEE. `getDownloadURL` tiene tope de tamaño y no
  va a alcanzar; hay que evaluar `ee.batch.Export`, que es asíncrono y cambia la
  forma del handler.
- **Todas las descargas de una entidad tienen que caer en la misma grilla.**
  Mismo `region`, `scale` y `crs`, o las pasadas no se pueden apilar. Si se
  escapa, el bug aparece recién al componer.
- **Las pasadas van a tener agujeros de nube, y está bien.** Hay que preservar
  el nodata: un cero en NDVI es un valor válido, y confundirlo con "sin dato"
  arruina el composite en silencio.
- ⚠️ **Los valores van a cambiar respecto del esquema actual.** Hoy se hace
  `collection.median()` sobre las **bandas** y después se calcula el índice; con
  pasadas guardadas se hace la mediana sobre el **índice ya calculado**. Como el
  NDVI es un cociente, `mediana(NDVI) ≠ NDVI(mediana(B8), mediana(B4))`. Ninguna
  de las dos es incorrecta —calcular el índice por imagen y reducir después es
  práctica común y más robusta a nubes residuales— pero **no es neutro**: al
  comparar series viejas con nuevas va a aparecer un escalón que no es del
  cultivo.

**Lo que se descartó:** guardar el composite multibanda corregido. Su única
ventaja era derivar índices nuevos sin volver a GEE, y el conjunto de índices
está fijado de antemano. Además las correcciones se aplican **por imagen, antes
de componer**: si cambia el umbral de nubes o SCS+C, el composite también cambia,
así que guardarlo tampoco evita reprocesar.

También se descartó el `_original.tif` que subía `process_rancho`: no era dato
crudo sino el mismo índice antes de convertirse a COG, nunca se registró en
`layers`, y duplicaba almacenamiento sin aportar nada.

---

## 20. Composites con MosaicJSON, y campos con forma de STAC (2026-08-21)

> ⚠️ **2026-09-12: la parte de MosaicJSON caduca si se confirma `#31`.** La
> convención STAC para los campos de `layers` sigue en pie.

**Decisión:** los composites se arman con **MosaicJSON** y los compone
`rio-tiler` al servir el tile, con selección de píxel por mediana. No se escribe
código propio de composición.

Y por separado: **los campos de `layers` se nombran siguiendo la convención
STAC**, sin adoptar STAC como infraestructura todavía.

**Por qué MosaicJSON:** componer rásters parece simple y no lo es —alinear
grillas, manejar nodata, elegir el método de reducción— y los casos borde muerden
en silencio. `rio-tiler` ya lo resuelve y está probado. Además, como el
MosaicJSON es solo una lista de COGs, cambiar de mensual a semanal es armar otra
lista: no se reprocesa nada.

**Límite conocido:** componer al vuelo cuesta lectura. Con las 2-6 pasadas de una
semana o un mes está bien; con las ~73 de un año conviene precalcular. La regla:
ventanas chicas al vuelo, ventanas grandes precalculadas.

**Por qué STAC solo como convención:** `geodata.layers` ya es un catálogo
funcional y a un paso de ser un STAC Item —tiene `id`, `acquired_ts`, `bbox`,
`product`, `storage_key`. Reemplazarlo por STAC de verdad implica cambiar
Geocore, que es quien lo sirve al front: un cambio entre dos repos para resolver
un problema que hoy no existe.

Nombrar los campos como STAC no cuesta nada y deja la migración barata.

**Cuándo dejar de diferirlo:** cuando el catálogo tenga que servir a algo que no
sea nuestro front (QGIS, un cliente que quiere sus datos crudos), o cuando se
quieran mezclar nuestras capas con catálogos públicos —Copernicus, AWS Earth
Search y Microsoft Planetary Computer publican Sentinel-2 L2A como COG con STAC.

### Revisión 2026-08-27 — verificado ✅

La verificación pendiente se hizo y **la decisión se sostiene**. Tres pasadas
sobre la misma grilla, con valores constantes y agujeros complementarios: la
mediana sale correcta y una zona con dato en una sola pasada sale con el dato, no
con un agujero. Un cuarto cuadrante sin dato en ninguna pasada sale vacío, que es
lo que hace que la prueba pruebe algo.

Se reproduce con `scripts/check_mosaic_median.py` del tileserver. **No es un
test**: tarda, y depende de `rio-tiler` y `cogeo-mosaic`, que entran sin pinnear
(`PREGUNTAS_ABIERTAS` C-2). Correrlo después de tocar `requirements.txt`.

Dos cosas que la decisión original no contemplaba:

- **`cogeo-mosaic` lee el MosaicJSON con boto3, no con GDAL.** `S3Backend` crea
  su cliente sin `endpoint_url`, así que toda la configuración de MinIO que ya
  existía para GDAL le es invisible y un `s3://` iría a AWS de verdad. Resuelto
  en `configure_gdal()`, que ahora fija también `AWS_ENDPOINT_URL_S3` desde la
  misma `Settings`.
- **Los assets listados dentro del MosaicJSON no pasan por el filtro anti-SSRF**
  que sí protege a `?url=`. Hoy está contenido porque solo `worker-rw` escribe en
  el bucket. Registrado en `PREGUNTAS_ABIERTAS` C-1, porque deja de estarlo si el
  mosaico pasa a servirse por HTTP.

**Sigue sin decidirse dónde está el corte** entre componer al vuelo y precalcular.
La regla "ventanas chicas al vuelo, ventanas grandes precalculadas" está escrita,
pero el spike usó 3 pasadas y un año son ~73. Es una medición, no una discusión:
`PREGUNTAS_ABIERTAS` A-2.

> Explicación desde cero de COG, MosaicJSON y STAC, para quien no venga del
> mundo geoespacial: [`GUIA_COG_STAC_MOSAICJSON.md`](GUIA_COG_STAC_MOSAICJSON.md).

---

## 21. El chequeo de salud: liveness y readiness separados, y paridad de permisos (2026-08-26)

**Decisión:** el tileserver expone dos endpoints de estado con propósitos
distintos, y el sondeo de readiness **ejercita exactamente los mismos permisos
que el camino real, ni uno más**.

| | Pregunta | Quién lo consulta |
|---|---|---|
| `/health` | ¿El proceso vive? | Railway, para decidir reinicios |
| `/health/ready` | ¿Puede hacer su trabajo? | Una persona, para diagnosticar |

**Por qué separados:** si `/health` dependiera de MinIO, un parpadeo del storage
haría que Railway reinicie un tileserver sano — y el reinicio no arregla nada de
lo que falló. Tampoco puede depender de la configuración: un deploy mal
configurado entraría en un bucle de reinicios sin llegar nunca a mostrar el
motivo. Por eso `/health` responde 200 aunque falte todo.

**Por qué la paridad de permisos.** Esto costó una sesión entera de depuración.

`/health/ready` sondea MinIO con el cliente de minio-py. Sin el parámetro
`region`, ese cliente resuelve la región llamando a `GetBucketLocation` antes de
cada operación (`minio/api.py::_get_region`), lo que exige el permiso
`s3:GetBucketLocation`. **GDAL no hace esa llamada**: `/vsis3/` usa `AWS_REGION`
directamente.

Resultado: una policy de solo lectura perfectamente válida para servir tiles
hacía fallar el chequeo con `AccessDenied` — y en las dos operaciones a la vez,
porque las dos morían en el mismo paso previo. Se leyó como un problema de fondo
con la asignación de la policy y mandó a revisar durante horas algo que estaba
bien.

Un chequeo más estricto que el sistema que chequea **no es más seguro: es una
fuente de falsos negativos**, y los falsos negativos entrenan a la gente a
ignorar el semáforo.

**Consecuencias:**

- El cliente del sondeo se construye con la misma `region` que recibe GDAL.
- El mismo criterio ya había evitado usar `bucket_exists()` como sondeo
  principal: exige `s3:ListBucket`, que la policy `readonly` de MinIO no incluye.
  El sondeo principal es un `stat_object` sobre una key inexistente, que ejercita
  `s3:GetObject` — el permiso que el tileserver realmente usa.
- **Ante la duda, `degradado` con 200, no error con 503.** Hay respuestas de las
  que honestamente no se puede concluir —un `AccessDenied` puede ser falta de
  permiso o MinIO ocultando un `NoSuchKey`— y un chequeo que grita fallo ante la
  duda se vuelve ruido.
- **No haber podido concluir no es haber concluido que está roto.** Si el sondeo
  de desambiguación falla, se devuelve el estado ambiguo original en vez de un
  diagnóstico nuevo.
- Cada fallo trae un `cause` estable y un texto que **nombra la variable a
  corregir**, no solo la operación que falló.
- El endpoint es público: informa el bucket —que ya viaja en cada URL de tile—
  pero nunca el endpoint interno ni las credenciales. La access key sale
  enmascarada, lo justo para distinguir `tiler-ro` de un default.

**Lo que se descartó:** un solo endpoint que sirviera para las dos cosas. Es lo
que uno escribe primero y es exactamente lo que produce el reinicio en cascada
cuando la dependencia parpadea.

### Revisión 2026-09-01 — la regla vale para todo cliente S3, no solo para el sondeo

**El mismo defecto estaba en el worker, en el camino de escritura, y esta
decisión no lo agarró.** `StorageService.__init__` construía el cliente de MinIO
sin `region`, así que la primera subida iba a disparar el mismo
`GetBucketLocation` y a fallar con el mismo `AccessDenied` engañoso —esta vez no
en un chequeo de salud, sino en la operación que el worker existe para hacer.

**Por qué se escapó, que es lo que importa.** Esta entrada se redactó como la
crónica de un bug del tileserver: *el sondeo* tiene que ejercitar los mismos
permisos que el camino real. Redactada así, no aplica a nada más. La regla real
es más ancha y ahora queda escrita como tal:

> **Todo cliente S3 del ecosistema se construye con `region`.** No porque MinIO
> tenga regiones —no las tiene— sino porque sin ese parámetro la librería agrega
> una petición que pide un permiso que el camino real no usa.

Lo cubre `scripts/check_minio_region.py` en el worker, que corre contra un
servidor S3 de mentira que **solo acepta el `PUT`** y exige que la subida emita
esa petición y ninguna otra. Sale en rojo si alguien quita el parámetro por
considerarlo redundante, que es exactamente como se pierde una línea así.

**Reproducido antes de arreglarlo**, con tres escenarios y control negativo. El
que decide es el tercero: contra un servidor que **niega** `GetBucketLocation`,
el mismo código con `region` sube y sin `region` no llega a emitir el `PUT`. Eso
descarta de raíz la hipótesis de credenciales, que es la que costó la sesión del
2026-08-26.

Dos detalles que la entrada original no registraba y conviene tener a mano:

- **El caché de región vive dentro de cada cliente** (`_region_map`), no es
  global. Dos objetos `Minio` en el mismo proceso pagan el peaje una vez cada
  uno.
- **`GetBucketLocation` contra MinIO devuelve un XML vacío**, que minio-py
  interpreta como `us-east-1` (`minio/api.py:505`). Toda la ida y vuelta, el
  permiso extra y el modo de fallo existen para llegar al mismo valor que se
  puede escribir a mano.

Queda abierta la otra mitad: `ensure_bucket()` sigue en el constructor y llama a
`bucket_exists()`, que pide otro permiso que la subida tampoco usa (`PLAN.md`
F.13). El script lo reporta como `PENDIENTE`, no como fallo: es deuda conocida,
no una regresión.

---

## 22. El worker se pinnea contra Python 3.13, y el pin exige wheel (2026-08-30)

**Decisión:** `requirements.txt` fija versión exacta de las 16 dependencias
directas contra **Python 3.13**, y la regla de aceptación es que
`pip install --only-binary=:all: -r requirements.txt` pase sin compilar nada.
`rasterio` sube de `1.3.10` a `1.4.3` y `rio-cogeo` de `5.3.6` a `5.4.2`.

**El problema.** El repo tenía 3 de 16 paquetes pinneados y ningún entorno
reproducible: nunca hubo `.venv` ni dependencias instaladas en esta máquina, así
que la compuerta VERIFY de `WORKFLOW.md` —`pytest` verde— no se podía correr
aunque se quisiera. Y el único pin que importaba estaba mal: `rasterio==1.3.10`
publica wheels hasta cp312, contra los Pythons 3.13 y 3.14 instalados. Instalar
las dependencias no fallaba con un mensaje claro; se ponía a compilar rasterio
contra headers de GDAL en Windows.

**Por qué 3.13 y no instalar 3.12 para respetar el pin viejo.** Porque no había
paridad con producción que preservar: el worker **no está desplegado y no tiene
Dockerfile** (`.devcontainer/` no cuenta, no es la imagen del servicio). El
`1.3.10` no era una versión validada contra nada real — era el estado en que
quedó. Elegir 3.13 ahora, a conciencia, convierte este archivo en el contrato
que el Dockerfile va a tener que respetar, en vez de arrastrar un pin heredado.

**Por qué `--only-binary=:all:` como regla y no solo como comando.** Es lo que
convierte "compila si tiene que compilar" en un error temprano y legible. Un pin
que obliga a compilar `rasterio` contra GDAL falla distinto en cada máquina y en
cada imagen base, y el mensaje nunca dice "la versión que pediste no tiene
wheel". Con la regla, ese pin se rechaza en el momento de proponerlo.

**Lo que este cambio NO valida.** `rio-cogeo` 5.3 → 5.4 puede mover el COG que
produce `services/cog_converter.py`, que llama a `cog_translate` con
`web_optimized=True` y `add_mask=True`. La FASE B verificó la composición por
mediana con COGs armados por el spike, **no por este convertidor**.

> ✅ **La mitad importante, verificada el 2026-09-07.**
> `scripts/check_ingest_real.py` corre la cadena real —GEE → GeoTIFF →
> `convert_to_cog` → MinIO— y pasa el resultado por
> `rio_cogeo.cogeo.cog_validate`, que **lo acepta**: 408 KB bajados de GEE,
> 428 KB de COG válido, subido al bucket desplegado.
>
> Lo que sigue sin probarse es lo otro que decía este párrafo: que **varios** COG
> de este convertidor **se apilen bien en un mosaico**. Eso necesita dos pasadas
> de la misma zona sobre la misma grilla, y es parte de la FASE C — no de A-3.

**Consecuencias:**

- Cierra `PREGUNTAS_ABIERTAS` C-2 en la parte que dependía del worker:
  `rasterio`/GDAL dejan de estar sin pinnear.
- Desbloquea la compuerta VERIFY: `pytest` colecta y corre por primera vez.
  Sale en rojo (4 fallos), y los 4 son endpoints que FASE D borra.
- `python-jose` queda pinneado a propósito aunque FASE D lo elimine: sacarlo de
  `requirements.txt` antes de sacar el `import` de `auth.py` dejaría un clon
  limpio sin poder arrancar.
- Cuando se escriba el Dockerfile, la imagen base es `python:3.13` y el build
  tiene que usar la misma regla de wheel.

---

## 23. El worker no expone API de lectura (2026-08-30)

**Decisión:** la superficie HTTP del worker es **`GET /health` y `/api/inngest`,
y nada más**. Las lecturas las sirve Geocore. Ejecuta la FASE D del `PLAN.md`,
decidida en el SESSION del 2026-08-21 y sin ejecutar desde entonces.

**Qué se borró:** `routes/` (10 archivos, 8 ni siquiera montados), `schemas/`,
`auth.py`, `services/auth/` —con un cliente de Keycloak que no usaba nadie—, las
seis funciones de lectura de `repositories/db_repository.py`, el endpoint
`/upload-kml`, el `CORSMiddleware` con `allow_origins=["*"]`, la dependencia
`python-jose` y los endpoints `/docs`, `/redoc` y `/openapi.json`.

**Por qué, y no "arreglarlo".** Geocore ya es el dueño del catálogo y el único
que aplica aislamiento de tenant vía `TenantMiddleware`. Un segundo servicio
sirviendo las mismas lecturas es una segunda superficie que autorizar, y esa
segunda estaba mal: `/assets` tomaba el `tenant_id` **de un query param** y su
`get_current_user_optional` se ignoraba, así que cualquiera podía pedir los
assets de cualquier tenant escribiendo su id en la URL. Arreglarlo era
reimplementar en Python el aislamiento que Geocore ya tiene testeado en C#.

Además las lecturas apuntaban a una tabla `assets` que **no existe** en
`geodata` desde la migración a `layers`: endpoints que llevaban semanas
devolviendo 500 sin que nadie lo notara. Es la mejor prueba de que nadie los
usaba.

Con esto desaparecen, sin tener que arreglarlos uno por uno: el
`get_current_user_optional` que se ignoraba, el `tenant_id` por query param, el
CORS abierto, y un `/upload-kml` que parseaba XML duplicando algo que Geocore ya
hace con defensa XXE testeada (`DECISIONS #17`).

**Dos cosas que aparecieron al borrar:**

1. **`get_cached_dates` no era solo un import muerto, era una colisión de
   nombres.** `services/inngest_handlers.py` importa `get_sentinel2_dates` de
   `services.ee.ee_client` —que consulta GEE— y también importaba una función
   distinta con el **mismo nombre** del repositorio, aliasada. Nadie llamaba al
   alias. Quien lo quitara sin mirar iba a hacer que la del repositorio pisara a
   la de GEE, y las dos llamadas del archivo le pasarían un ROI donde espera un
   `geometry_id`. Se eliminaron las dos puntas.

2. **`sentinel2_dates` es un caché de solo escritura.** Los handlers insertan una
   fila por fecha en cada pedido y **nunca leen la tabla**: la única función que
   la consultaba era la del punto anterior, que no llamaba nadie. Cada pedido
   pega a GEE completo y además escribe un caché que no ahorra nada. No se toca
   acá —FASE D es borrar la superficie de lectura, no rediseñar handlers— pero
   queda anotado en `PREGUNTAS_ABIERTAS`.

**Cómo se sostiene la decisión.** `tests/test_http_surface.py` fija la superficie
como igualdad exacta, no como "contiene". Una decisión así se erosiona sola:
agregar un `@app.get` es una línea y nada avisa. Ahora avisa el test. Reemplaza a
`tests/test_api.py`, que probaba justamente las rutas borradas y era el único
test del repo.

**Consecuencias:**

- La compuerta VERIFY de este repo pasa a verde por primera vez: 4 tests.
- Si se usa Inngest Cloud, el worker necesita URL pública. Con `/docs` apagado y
  sin endpoints de negocio, lo único expuesto es el endpoint firmado de Inngest
  — pero `INNGEST_SIGNING_KEY` **sigue sin cablearse** (`PLAN.md` F.1). Esa es
  hoy la superficie pública sin autenticar, y hay que cerrarla antes de exponer.

---

## 24. El cliente de storage se construye perezoso, y falla cerrado (2026-09-02)

**Decisión:** `StorageService` **no hace I/O al construirse**, se obtiene por
`get_storage_service()` con `lru_cache`, y si la configuración de MinIO no sirve
**levanta nombrando la variable a corregir** en lugar de arrancar y fallar
después.

Salió de auditar F.12: el mismo constructor tenía tres defectos más, y los tres
son variantes de algo que este proyecto ya había pagado dos veces.

### Uno solo, sí; creado al importar, no

Había un `storage_service = StorageService()` a nivel de módulo con
`ensure_bucket()` dentro del `__init__`. Consecuencia: `import app` abría un
socket y reintentaba si no había nadie del otro lado.

**El singleton en sí estaba bien, y por un motivo que conviene nombrar:**
comparte el pool de conexiones de urllib3 que vive dentro del cliente de
minio-py. Crear un cliente por subida tira ese pool y vuelve a pagar el saludo
TCP —y el TLS— en cada archivo. El motivo correcto es reusar conexiones, no
"garantizar que haya uno solo".

Lo que estaba mal era que fuera *eager*. Eso ataba el `import` a que el storage
estuviera vivo, congelaba la config en el momento de importar, y el fallo queda
tapado por un `try/except` con `print`. **Medido:** la suite tardaba ~33 s para
4 tests que no tocan la red; con el cambio, ~6 s para 24.

`repositories/db_repository.get_connection()` ya tenía la forma correcta —`_pool
= None` y se construye en el primer uso—. `storage_service` era el
inconsistente, no el modelo.

### `ensure_bucket()` es tarea de despliegue

Llama a `bucket_exists()`, que pide `s3:ListBucket`: **otro** permiso que la
subida real no usa, en el mismo constructor donde acabábamos de sacar el
`GetBucketLocation`. Crear el bucket se hace una vez, al desplegar. Sigue
existiendo como método, ya no se llama solo, y ya no se traga la excepción.

### Las credenciales no caen a un default fuera de desarrollo

```python
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
```

`minioadmin` es la credencial **root** del `docker-compose`. Un deploy al que le
faltara la variable no fallaba: se autenticaba como root contra el endpoint
configurado. Y en local funcionaba, así que el problema solo podía aparecer en
producción.

Es exactamente el patrón que `DECISIONS #16` eliminó en Geocore
(`?? DevMapTokenSecret`) y que el tileserver eliminó en `MAP_TOKEN_SECRET`. **Los
dos fallaban abiertos, y este era el tercero.**

### Por qué levantar es correcto acá, y no contradice #16

`DECISIONS #16` decidió que un secreto faltante **degrade un endpoint** en vez de
tumbar el arranque, y eso sigue valiendo: el worker tiene que poder arrancar y
responder `/health` aunque el storage no esté configurado. Por eso la validación
**no corre al importar**.

Corre al construir el cliente, que ahora es perezoso. La diferencia es de
momento, no de criterio: revienta cuando alguien necesita el storage —o sea,
dentro de un handler de Inngest, que reintenta y registra el fallo en
`processing_jobs`— y no cuando el proceso arranca.

### De paso: la coherencia del endpoint, sin tocar la red

`validate_endpoint()` detecta las cuatro formas de confundir los dos dominios de
Railway —el privado lleva `:9000` y va sin TLS, el público va sin puerto y con
TLS— más el esquema de más y la barra final. Es el error más repetido de este
despliegue, y equivocarse **no da un error de SSL**: da un `ConnectionReset`.

Las dos validaciones son **funciones puras que reciben los valores** en vez de
leer la config al importarse. Es la misma lección que el tileserver aprendió con
`crear_validador_de_token`: una validación que lee globales resueltas al importar
no se puede probar sin recargar módulos.

**Consecuencias:**

- Los call sites pasan de `storage_service.upload_file(...)` a
  `get_storage_service().upload_file(...)` — 7 en `inngest_handlers.py`, 3 en
  `utils_pkg/roi.py`.
- `tests/test_storage_service.py` fija los tres invariantes: la subida emite solo
  el `PUT` (con control negativo), importar el módulo no abre conexiones
  —verificado en un proceso aparte, con el socket saboteado, porque el import se
  cachea por intérprete— y una config inválida falla cerrado nombrando la
  variable.
- **`ENVIRONMENT` pasa a tener consecuencias de seguridad.** Antes solo elegía el
  modo de Inngest; ahora decide si hay credencial de desarrollo. Un deploy con
  `ENVIRONMENT=development` reabre el default.

---

## 25. La firma de Inngest se verifica, y el entorno tiene un solo criterio (2026-09-04)

**Decisión:** `is_production` del cliente de Inngest sale de `config.IS_PRODUCTION`,
que es **el único criterio de entorno del proceso**, y **un valor desconocido
cuenta como producción**. Con eso, `/api/inngest` verifica la firma HMAC de cada
petición.

### El agujero no era el que decía el plan

`PLAN.md` F.1 decía *"cablear `INNGEST_SIGNING_KEY` al cliente y a `serve()`"*.
Al ir a hacerlo, dos cosas de esa frase resultaron falsas:

- **`serve()` no acepta `signing_key`.** Su firma es
  `serve(app, client, functions, *, serve_origin, serve_path)`: todo sale del
  cliente.
- **El SDK ya leía la variable solo.** `client.py:101` hace
  `signing_key or os.getenv("INNGEST_SIGNING_KEY")`.

El agujero real estaba un nivel más abajo, en el paquete instalado
(`inngest/_internal/net.py::_validate_sig`):

```python
if mode == server_lib.ServerKind.DEV_SERVER:
    return None      # <- no valida NADA
```

**La verificación está enteramente condicionada al modo.** Y el modo se
calculaba así:

```python
is_production=os.getenv("ENVIRONMENT", "development") == "production"
```

O sea que **cualquier valor distinto del string exacto `"production"`** —`prod`,
`Production`, un typo, o la variable ausente— ponía el worker en modo dev y
**apagaba la verificación de firma**. `/api/inngest` es la única superficie
pública del worker: sin firma, cualquiera puede invocar `process_parcela` con
ids de otro tenant, geometrías arbitrarias y gasto de cuota de GEE. No hay
segunda capa detrás.

**Lo peor del caso:** el default del propio SDK es el seguro. `_get_mode`
devuelve CLOUD cuando no se le pasa `is_production`. Nuestro código era **más
permisivo que no configurar nada**.

### Dos criterios de entorno que no coincidían

El 2026-09-02, `DECISIONS #24` agregó `IS_DEVELOPMENT` a `config.py` con una
lista explícita (`development`, `dev`, `local`). Quedaron entonces **dos
nociones de entorno en el mismo proceso, con reglas distintas**:

| `ENVIRONMENT` | Credenciales de MinIO | Firma de Inngest |
|---|---|---|
| `production` | sin default ✅ | se verifica ✅ |
| `development` | default de dev ✅ | no se verifica ✅ |
| **`prod`** | **sin default** ✅ | **no se verifica** 🔴 |

`prod` caía en el peor de los dos mundos, y nada lo avisaba. Ahora hay una sola
función de verdad, y **la dirección del default está elegida a conciencia: lo
desconocido es producción.** Un typo en la variable tiene que apretar los
controles, no soltarlos.

### Por qué acá se loguea y no se levanta

`StorageService` **levanta** si su config no sirve; este módulo **loguea un
ERROR y sigue**. No es inconsistencia:

- En storage, sin la validación había una ventana insegura real: el cliente se
  construía con la credencial root del `docker-compose` y funcionaba.
- Acá el fallo cerrado **ya lo garantiza el SDK**: en modo cloud sin firma
  válida rechaza la petición con 401. No hay ventana que cerrar, solo un
  diagnóstico que dar — y tumbar el arranque impediría dar ese diagnóstico por
  `/health`, que es el criterio de `DECISIONS #16`.

### Verificado contra un cliente HTTP, no contra la config

La misma invocación sin firma, en los dos modos:

| Modo | Respuesta |
|---|---|
| **cloud** | **401 `header_missing`**, *antes* de parsear el cuerpo |
| dev | pasa el control de acceso y llega a parsear el cuerpo |

Que el 401 llegue **antes** del parseo importa: un atacante no puede ni sondear
el esquema del payload. Y el caso dev es el **control negativo** — si empezara a
dar 401, el otro test dejaría de probar algo.

**Consecuencias:**

- Cierra `OWASP_TOP10.md` **W-8**, que era el único hallazgo marcado como
  bloqueante del despliegue.
- **F.3 de paso:** `api_base_url` y `event_api_base_url` ya no se fijan en
  producción. Forzarlas a `INNGEST_BASE_URL` —default `http://localhost:8288`—
  hacía que un deploy sin esa variable buscara Inngest en su propio contenedor.
- `INNGEST_EVENT_KEY` con el valor de desarrollo (`dev-local-key`) no se manda
  en producción y se avisa: sin ella el worker sube los COG y **nunca emite
  `terra/raster.ingested`**, así que la capa no se registra en `layers`.
- En modo dev se loguea un WARNING que dice que la firma no se verifica. Es
  correcto en local y es exactamente lo que no se quiere ver en un log de
  producción.
- **`ENVIRONMENT` es ahora una variable de seguridad.** Ponerla en
  `development` en un deploy abre el endpoint y reactiva el default de
  credenciales. Tiene que estar en el checklist de despliegue.

---

## 26. Entre steps de Inngest solo cruzan referencias durables (2026-09-04)

**Decisión:** un step de Inngest **nunca** devuelve estado del sistema de
archivos local. Lo que cruza un límite de step es una referencia que sobrevive a
la muerte del proceso: una key de MinIO, un id, una fecha. Y los handlers son
**sincrónicos**, no corrutinas.

### El reintento envenenado

`process_rancho` tenía tres steps: `_download_gee` devolvía
`{"temp_raw_path": "/tmp/xyz.tif", ...}`, `_convert_cog` leía ese archivo y lo
borraba, `_upload_minio` subía el resultado.

**Un step de Inngest memoiza lo que devuelve.** Si `_convert_cog` fallaba y el
reintento volvía a entrar, `_download_gee` no se ejecutaba: contestaba con el
JSON guardado, que apuntaba a un archivo **que el intento anterior ya había
borrado**. `FileNotFoundError`, en los tres reintentos, para siempre.

Es el modo de fallo más incómodo posible: `retries=3` está puesto justamente
para recuperarse de fallos transitorios, y este diseño garantizaba que **ningún
reintento pudiera funcionar**.

**La causa no es el borrado, es la frontera.** Un step es un punto de
recuperación, y un punto de recuperación que depende del disco local de la
ejecución anterior no es un punto de recuperación. Da lo mismo si el archivo lo
borra el código, el reinicio del contenedor, o Railway moviendo el proceso a
otra máquina.

**Los tres steps se fusionan en uno.** Lo que sale es la `storage_key`, que sigue
existiendo después de que el proceso muera. El costo es que un reintento vuelve
a bajar de GEE — aceptable, y es lo que ya pasaba *de hecho*, porque el
reintento no funcionaba en absoluto.

**Y cierra E.8 sin trabajo extra.** La alternativa a fusionar era subir el crudo
a MinIO para pasarlo entre steps, que es exactamente lo que hacía el
`_original.tif` — el archivo que `DECISIONS #19` ya había descartado por no ser
dato crudo sino el mismo índice antes del COG. La decisión de arquitectura y la
de durabilidad apuntaban al mismo lado.

### Los handlers no son corrutinas

Estaban declarados `async def` y adentro llamaban a `requests`, `rasterio`,
`psycopg2` y `.getInfo()`, que son sincrónicos y bloqueantes. Una descarga de
30 s no cedía el control: **congelaba el event loop y con él todo el proceso**,
incluido `/health` —o sea que Railway podía reiniciar un worker que estaba
trabajando bien—.

En inngest-py 0.4 alcanza con declararlas `def` y pedir un `inngest.StepSync`:
el SDK las corre en un pool de hilos.

**Lo notable es que `ruff` lo venía marcando** como `ASYNC210
blocking-http-call-in-async-function`, y estaba en la lista de hallazgos que
nadie miró hasta que se pinneó el linter (`PLAN.md` F.15). El hallazgo estaba
disponible antes que el diagnóstico.

### La grilla de descarga, en un solo lugar

El código de descarga estaba **duplicado literalmente** entre
`services/inngest_handlers.py` y `services/export_service.py`: mismo
`getDownloadURL`, mismos `scale` y `crs` escritos a mano, mismo `requests.get`
por chunks.

`DECISIONS #19` exige que todas las descargas de una entidad caigan en la misma
grilla, *"o las pasadas no se pueden apilar; si se escapa, el bug aparece recién
al componer"*. Con dos copias, eso se cumplía **por casualidad**. Ahora vive en
`services/ee/gee_download.py`, donde no puede divergir.

Dos cosas que aparecieron al unificar:

- **Ninguna de las dos descargas tenía `timeout`.** `requests` sin timeout
  espera indefinidamente, y con el trabajo en un pool de hilos una descarga
  colgada retiene un hilo hasta que alguien reinicie el proceso. Es la clase de
  fallo que no aparece en desarrollo y no se recupera solo.
- **Un GeoTIFF de 0 bytes pasaba el `raise_for_status`** y reventaba después, en
  `rasterio` o en `cog_translate`, con un error que no mencionaba la descarga.
  Ahora falla donde está la causa, y el mensaje nombra los dos motivos probables:
  un ROI sin datos en el período, o el tope de tamaño de `getDownloadURL`.

**Consecuencias:**

- `RETRIES` es una sola constante, usada por los decoradores **y** por el wrapper
  de jobs. E.4 depende de que coincidan, así que un test lo fija: con el
  decorador en 5 y la constante en 3, el job se marcaría fallido dos intentos
  antes de que Inngest deje de reintentar.
- `normalizar_coordenadas` se separó de `coords_to_geometry`. La traducción del
  payload de Geocore —`[{lat, lng}]` de C# a `[lng, lat]` de GeoJSON— es
  **contrato entre repos** y es pura; construir una `ee.Geometry` exige
  `ee.Initialize()`. Separarlas hizo el contrato testeable sin autenticarse.
- Al escribir ese test apareció un bug: `c.get("lng") or c.get("Lng")` trataba
  el **0.0 como ausente**. El meridiano de Greenwich y el ecuador son
  coordenadas válidas, y un `lng` de 0.0 quedaba en `None` — un ROI corrupto sin
  ningún error.

---

## 27. El arranque verifica con I/O real, y cada chequeo dice hasta dónde probó (2026-09-08)

**Decisión:** al arrancar, el worker escribe dos cosas. Primero, su modo y el
estado de cada variable de entorno —**sin un carácter de ningún secreto**, sólo
largos—, con la consecuencia de cada faltante (`utils_pkg/arranque.py`).
Después, el resultado de **conectarse de verdad** a cada dependencia, con el
alcance de cada chequeo impreso al lado del resultado
(`utils_pkg/conexiones.py`).

### Por qué no alcanzaba con el reporte de configuración

Pasó tres veces en la misma semana, con la misma forma: **la variable estaba
puesta, el reporte la mostraba como "definida", y no servía.** `DB_PASSWORD`
rechazada por Supabase, `INNGEST_SIGNING_KEY` vacía, `BASE_OUTPUT_DIR` apuntando
fuera del contenedor. Un reporte de configuración dice qué hay; sólo conectar,
escribir o consultar dice si funciona.

Y había un agravante: `init_db()` atrapa su propia excepción, así que el
precalentamiento de `app.py` **no podía distinguir** una base caída de una que
anda.

### Reglas

- **Ningún chequeo levanta y ninguno cuelga.** Cada uno devuelve un `Resultado`
  y tiene timeout corto, porque hasta que termina el `startup`, `/health` no
  contesta.
- **Ningún chequeo pide más que el trabajo real** (`DECISIONS #21`). Por eso
  `minio` prueba alcance y TLS, **no credenciales**: cualquier operación barata
  exige un permiso que la subida no usa. Y el resultado lo dice, para que un OK
  no se lea como más garantía de la que da.
- **El reporte de configuración va a nivel de módulo**, antes de
  `inngest.fast_api.serve()`. Si algo del import falla, el evento `startup`
  nunca se dispara, y un diagnóstico que sólo aparece cuando todo anda no sirve.
  Es seguro ahí porque sólo lee `os.environ`. Los chequeos con I/O van en
  `startup`.
- **`/health` responde 200 aunque el worker esté degradado**, con el estado en
  el cuerpo. Con 503, Railway reiniciaría en bucle un servicio mal configurado,
  que es justo el que hay que poder mirar.

### Consecuencias

- Unos 10 s de chequeos antes de que `/health` conteste. Acotados; es lo
  primero a revisar si el healthcheck de Railway se pone estricto.
- Geocore (`StartupBanner.cs`) y el tileserver (`terra_tiles/arranque.py`)
  siguen el mismo patrón, cada uno reusando lo que ya tenía: el tileserver
  reusa las comprobaciones de `/health/ready`.

---

## 28. El tileserver no devuelve mensajes crudos de error (2026-09-11)

**Decisión:** las excepciones de rio-tiler se atienden **caso por caso, con
mensaje fijo** —`TileOutsideBounds` → 200 con PNG transparente,
`PointOutsideBounds` → 404—, y **no** se registra
`titiler.core.errors.add_exception_handlers`, que era lo obvio.

### Por qué

Ese registro incluye un manejador para cualquier `Exception` que devuelve
`str(exc)` en la respuesta. Y los errores de GDAL traen el endpoint de S3
adentro. Verificado contra un MinIO inalcanzable:

    CURL error: Failed to connect to <host> port <puerto> ...

Con el MinIO privado de Railway, eso publicaría `bucket.railway.internal:9000`
en una respuesta HTTP, que es exactamente lo que `/health/ready` se cuida de no
mostrar.

### Costo aceptado

Un archivo inexistente responde **500 sin motivo** en el cuerpo (en TiTiler 0.18
eso es 500, no 404). El motivo queda en el log del servicio, que no es público.

### Los bytes fijos se generan con código

El PNG de "fuera del raster" era un literal base64 comentado como transparente.
Decodificado, era **blanco opaco** —píxel `[filtro 1, gris 255, alfa 255]`— y
además con el CRC de su bloque IDAT roto. Pintaba cuadrados blancos junto a los
heatmaps del worker, que están alineados exacto a la grilla de zoom 14. Ahora
lo arma `terra_tiles/png.py` desde los bytes del píxel, y `tests/test_png.py` lo
decodifica y mira el alfa. Un literal opaco a la lectura deja que el comentario
de al lado diga cualquier cosa.

---

## 29. La bitácora de un job se escribe desde adentro de los steps (2026-09-12)

**Decisión:** lo que el worker reporta del avance de un job se escribe **dentro
del callback de un `step.run`**, a través de `services/avance_job.py`: etapas,
ventanas de fechas y fallos por intento. Desde el cuerpo del handler sólo se
escriben los errores que ocurren fuera de todo step.

Los contratos de la tabla (`processing_job_events`, de Geocore) y por qué es una
tabla y no los logs están en `DECISIONS #20` de Geocore.

### Por qué

Inngest vuelve a ejecutar el cuerpo del handler en cada request y sólo memoiza
lo que devuelven los steps. Una línea escrita en el cuerpo se repite una vez por
cada step ya completado. El histórico de una parcela tiene 23 steps: el
"arrancó" aparecería 23 veces. Dentro del step, la línea queda atada a la
ejecución real: se escribe una vez si sale bien, y una vez por intento si falla.
Eso es exactamente lo que tiene que decir.

Y los fallos de un step **sólo se ven desde adentro**. El SDK los convierte en
`ResponseInterrupt`, que es `BaseException`, antes de que salgan de `step.run`.
El `except Exception` del wrapper nunca los vio.

### Cómo se usa

- En todo step que haga trabajo, `paso(step, "id", fn)` en vez de
  `step.run("id", fn)`. Registra el fallo de cada intento como `warning` si se va
  a reintentar, o `error` si no.
- `reportar(etapa, mensaje, progreso=…, **detalle)`, adentro del callback.
  `mensaje` es para una persona; `detalle` lleva los datos crudos.
- Un loop que arma steps pasa los valores de la vuelta como defaults del
  callback: una clausura común vería los de la última vuelta.
- Lo que tiene que ser igual en todos los requests, como la fecha de hoy, se fija
  en un step.
- `failed` se decide con `es_definitivo()`, no sólo con `attempt`. Un
  `NonRetriableError` o un `StepError` son definitivos en cualquier intento.

### Costo aceptado

- Cada línea es un round-trip a la base, unas 50 por parcela. No se agrupan.
- Si la base no está, la línea se pierde: nunca levanta. Si falta la tabla, la
  bitácora se pausa 10 minutos en vez de loguear el mismo error cada vez.
- Los ids de step nuevos (`-1…-8`, `-1…-12`) hacen que un run en vuelo durante
  un deploy rehaga esas etapas. Es idempotente.

---

## 30. Una medición por día: las imágenes del mismo día se promedian (2026-09-12)

**Decisión:** `get_sentinel2_time_series` devuelve un punto por día. Si dos
imágenes comparten fecha, se promedian (`una_por_dia`). Además
`insert_measurements` nunca manda dos filas con la misma PK en un lote: si llegan,
queda la última y se loguea.

**Por qué:** la primera corrida real del histórico falló en el mes 1 de la serie
con `CardinalityViolation`. Una parcela en el borde de dos tiles MGRS recibe dos
imágenes de la misma pasada, con la misma fecha, y Postgres rechaza un
`INSERT … ON CONFLICT DO UPDATE` que toque dos veces la misma fila. De a una fila,
la segunda pisaba a la primera en silencio. En lote (E.7), el lote entero se cae.

**Costo aceptado:** es un promedio simple, que no pondera por cuánta superficie
cubre cada tile.

**Transitoria:** con `#31` el compuesto mensual ya junta todas las pasadas del
mes, y este código se borra (FASE M.6).

---

## 31. El histórico es mensual y el compuesto lo arma GEE (2026-09-12)

> **✅ Decidida por el usuario el 2026-09-12**: eligió la opción B, "GEE arma una
> foto limpia por mes", contra la A, "una foto por pasada que se junta al mirar".
> Reemplaza `#19` y la parte de MosaicJSON de `#20`. Diseño completo:
> [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md). Backlog:
> [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md).
>
> **Receta v1, también decidida:**
> - índices NDVI (vegetación), EVI (vegetación densa), NDRE (clorofila) y NDMI
>   (humedad);
> - mediana, media, mín, máx, p10, p90 y desvío;
> - cobertura mínima de 0,3;
> - 24 meses de historia.
>
> Más índices después, una entrada de registro cada uno (M.9.3).

**Decisión:** por cada entidad y cada mes calendario, GEE arma el compuesto:
- cada pasada se enmascara por nubes y sombras;
- a cada una se le calcula el índice;
- se toma la mediana por píxel.

Del compuesto salen las estadísticas de la parcela (números, sin descarga) y el
COG del rancho (una descarga por mes). No se guardan pasadas sueltas ni se usa
MosaicJSON.

**Por qué.** La necesidad la definió el usuario el 2026-09-12:
- 2 años de historia al dar de alta;
- ventana **mensual** con mediana, para reducir nulos por nubes;
- datos nuevos todos los meses;
- series para análisis estadístico y un mapa por mes.

`#19` guardaba cada pasada para poder elegir **cualquier** ventana sin volver a
GEE. Con la ventana fija en un mes, esa libertad cuesta mucho más de lo que
rinde:

| | Por pasada + MosaicJSON (#19/#20) | Compuesto mensual |
|---|---|---|
| Descargas por rancho, 2 años | ~146, muchas casi vacías por nubes | 24 |
| Mapa de un mes | TiTiler abre 3 a 6 COG por tile | un COG ya compuesto |
| ¿El número coincide con el mapa? | no del todo (B-1: el orden de las operaciones cambia el resultado) | sí: salen de la misma imagen |
| Piezas extra | MosaicJSON, con A-1, A-2 y C-1 abiertas | ninguna |

**Consecuencias:**
- Cambiar a otra cadencia, como la semanal, obliga a volver a GEE. Es
  reprocesable, porque GEE es la fuente de verdad.
- `measurements` pasa a una fila por parcela, índice y mes. Las filas por pasada
  de hoy son de prueba y se borran.
- Caducan A-1, A-2, C-1 y FASE C.1–C.6. B-1 queda cerrada por construcción y B-3
  decidida.
- El tope de `getDownloadURL` (~48 MB) alcanza para ranchos de hasta unas
  120.000 ha a 10 m.

---

## 32. La capa de satélite es un pipeline: receta, registros, etapas y un solo borde (2026-09-12)

> **Estado: aceptada como base del plan.** El usuario pidió armar los sprints
> sobre este diseño. Diseño, ejemplos y estructura de carpetas:
> [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md) §3 a §5.

**Decisión:** el código que habla con GEE se organiza en cuatro piezas:
1. **Etapas** que arman expresiones: fuente, nubes, compuesto y reducción.
2. **Registros** de índices y de estadísticas: datos, no `if/elif`.
3. Una **receta versionada** con todos los parámetros que cambian un número.
4. **Un solo borde**, `pipeline/ejecucion.py`, que es el único lugar que llama a
   `getInfo()` y a `getDownloadURL()`.

Los handlers de Inngest solo ordenan los steps.

**Por qué:**
- Hoy cada fórmula está escrita dos veces, y distinta.
- Hay `try/except` alrededor de expresiones de GEE que nunca pueden fallar ahí,
  porque GEE es perezoso y el error aparece en `getInfo()`.
- Hay parámetros que se reciben y no se usan (`cloud_pct`).
- Los handlers mezclan la orquestación con el cálculo.

Sumar un índice o una estadística toca hoy tres o cuatro archivos. Con los
registros es una entrada.

**Descartado:** un framework de pipelines (Kedro, Dagster, Airflow). Inngest ya
orquesta, reintenta y limita la concurrencia, y la bitácora va encima (`#29`).

**Consecuencias:**
- Las estadísticas se guardan en `jsonb`: agregar una no pide migración. Así lo
  pidió el usuario ("que sean modificables").
- Cada fila registra su receta (D-1). Un test fija la receta vigente para que
  nadie la cambie sin subir la versión.
- Solo el núcleo puro (receta, periodos y registros) se prueba con unit tests.
  Las etapas se verifican contra GEE real con un script (`WORKFLOW` §6: "probar
  contra lo real").
- **Las fórmulas de los índices se escriben como texto**, sobre bandas con nombre
  y en reflectancia 0–1. GEE las evalúa con `Image.expression`, y los tests con
  un evaluador de Python contra valores de referencia. Así una fórmula se prueba
  sin GEE.

---

## 33. Se reescribe la capa de satélite, no el servicio (2026-09-12)

> **✅ Confirmada por el usuario el 2026-09-12:** "tal cual como lo decís,
> reescribí la parte del satélite". Había preguntado "¿reescribimos el worker
> desde el inicio, o al lado del viejo?". La reescritura arranca en M.1, después
> del CI (M.0).

**Decisión:** el código de GEE se escribe **de cero**, en un paquete nuevo
(`pipeline/`), dentro del mismo servicio y del mismo repo. Los handlers se pasan
de a uno al pipeline nuevo, y lo viejo se borra al final (M.6). Es el patrón
*strangler fig*: lo nuevo crece alrededor de lo viejo hasta reemplazarlo.

**Lo que se conserva, porque es lo que está bien y costó más:**

| Pieza | Por qué no se toca |
|---|---|
| El cliente de Inngest y la verificación de firma | `#25`, verificado contra Inngest real |
| El storage: región, permisos, TLS, falla cerrada | `#21` y `#24`, verificado contra el MinIO real con `check_write_path.py` |
| Steps con referencias durables | `#26`: el reintento envenenado ya se pagó una vez |
| La bitácora (`avance_job.py`) | `#29`, verificada en la primera corrida real |
| Las escrituras idempotentes, el arranque y el logging | `#27` y F.18 |
| El Dockerfile, el `.venv` pinneado y los 203 tests | `#22` |

**Por qué no reescribir todo:**
1. La plomería es la parte con más arreglos probados contra la realidad. Reescribirla
   es volver a pagar esos bugs.
2. Un reemplazo de una sola vez, sin CI y sin staging, es la jugada más riesgosa
   posible: todo o nada.
3. **La parte sucia sí se escribe de cero**, no se parchea. `pipeline/` es código
   nuevo con el diseño limpio. Se obtiene lo bueno de reescribir sin el riesgo.

**Por qué no al lado indefinidamente:** convivir es un estado de transición, no
un destino. M.6 tiene criterio de salida: el worker termina con **menos** líneas
que al empezar. Si después los handlers todavía molestan, rehacerlos es barato,
porque quedan finos.

---

## 34. CI en los cuatro repos: la compuerta VERIFY la corre una máquina (2026-09-14)

> Sprint M.0 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). Qué corre cada repo, cómo
> reproducirlo y qué hace el equipo en M.0.6: [`CI.md`](CI.md). Lo propio de Geocore
> y del panel está en `DECISIONS #24` de Geocore.

**Decisión:** cada repo tiene `.github/workflows/ci.yml`, con un solo job llamado
`ci`, que corre en cada PR y en cada push a `main`. El worker corre `pytest`,
`pip-audit` y un ruff estricto solo sobre `pipeline/`. Geocore corre build, tests y
paquetes vulnerables. El panel corre lint, build y `npm audit`. El tileserver corre
`pytest`.

**Por qué ahora, antes que el pipeline:** hasta el 2026-09-13 todo iba directo a
`main`, y Railway desplegaba lo que llegara. La FASE M reescribe la capa de
satélite (`#33`), y el `#33` mismo lo dice: un reemplazo grande sin red de
seguridad es la jugada más riesgosa. La compuerta VERIFY ya existía, pero dependía
de que alguien se acordara de correrla.

**Los criterios, comunes a los cuatro:**
1. **La versión de la imagen, no la de la máquina de quien desarrolla.** Python 3.13
   en el worker y 3.11 en el tileserver, Node 22 en el panel y .NET 10 en Geocore,
   las de cada Dockerfile. Un CI verde con otra versión no dice nada del deploy.
2. **Se instala como la imagen.** En el worker, `--only-binary=:all:` (`#22`), como
   variable de entorno para que alcance también al pip interno de `pip-audit`. El
   Dockerfile del worker nunca se construyó (FASE H), así que el CI es la primera
   vez que esos pins se instalan en Linux.
3. **Sin secretos.** Antes de escribir los workflows se corrió cada suite desde un
   clon limpio, sin `.env`: el worker dio 203, Geocore 243 y el tileserver 150, y
   el panel compiló sin `.env.local`. Si un test empieza a necesitar credenciales,
   el CI lo va a decir.
4. **Un solo job y siempre `ci`.** Es el nombre del check obligatorio de M.0.6.
   Partirlo en varios jobs multiplica los checks que hay que mantener en cuatro
   repos, para ganar unos segundos de paralelismo.
5. **Solo lectura** (`permissions: contents: read`). Un push nuevo a un PR cancela
   la corrida anterior; en `main` no se cancela nada.

**El ruff estricto vive en `pipeline/ruff.toml`.** Ruff usa la configuración más
cercana a cada archivo, así que ese archivo gobierna solo el paquete nuevo y el
resto del repo queda como estaba: 198 hallazgos, que no suben. `pipeline/` nace
vacío en M.0.1 justamente para que la regla exista antes de la primera línea.
- `select = ["ALL"]`, porque es código nuevo: abrir una regla con un motivo es más
  barato que cerrarla después con cien hallazgos encima.
- Tres excepciones, cada una con su porqué en el archivo: `COM812` choca con el
  formateador; `CPY001` pide un aviso de copyright que el repo no tiene; y la
  convención `google` de pydocstyle apaga `D401`, que chequea el imperativo con
  verbos en inglés.
- También `ruff format --check`: en código nuevo el formato no se discute.
- **Control negativo:** una función sin anotaciones da `ANN001` y `ANN202` dentro
  de `pipeline/`, y la misma función pasa limpia en la raíz.

**La auditoría de dependencias es compuerta en tres repos.** Consecuencia buscada:
un advisory nuevo puede poner el CI en rojo sin que nadie haya tocado el código. Se
arregla subiendo la dependencia, no apagando el paso. En Geocore apareció una
trampa: **`dotnet list package --vulnerable` sale con 0 aunque encuentre algo**
(se probó metiendo un paquete vulnerable a propósito), así que el paso busca la
frase de la salida (`DECISIONS #24` de Geocore).

**Lo que se descartó:**
- **Fijar las actions por SHA.** Van por tag de major (`@v7`): son todas de
  `actions/*`, mantenidas por GitHub, y sin Dependabot un SHA se queda viejo en
  silencio. Si se suma una action de terceros, esa sí va por SHA.
- **Construir la imagen Docker en el CI.** Railway ya la construye en cada deploy;
  hacerlo dos veces duplica el costo. Lo que se pierde está anotado abajo.
- **Un workflow reutilizable entre los cuatro repos.** Son cuatro stacks sin un
  paso en común.

**Lo que el CI no cubre, y queda registrado en [`CI.md`](CI.md):**
- Un Dockerfile roto. El caso que viene: **el Dockerfile del worker no copia
  `pipeline/`**, y cuando un handler lo importe (M.4.4) el contenedor va a morir al
  arrancar, que es el bug que ya tuvo el tileserver.
- El tileserver no audita dependencias, y tiene varias sin pin.
- Las 9 vulnerabilidades altas de las herramientas de desarrollo del panel
  (`vite` entre ellas): no llegan al bundle.

**Sin M.0.6 el CI avisa pero no frena.** Proteger `main` y activar "Wait for CI" en
Railway lo hace el equipo. `TechSupportKaapeh` es una cuenta personal: los tres
repos públicos (worker, panel y tileserver) se pueden proteger con el plan gratis,
y Geocore, que es privado, pide GitHub Pro. Se verificó con la API el 2026-09-14.
"Wait for CI" en Railway no depende del plan, y alcanza para que un rojo no se
despliegue.

---

## 35. El núcleo del pipeline: fórmulas que GEE y Python leen igual, registros validados al importar y una huella que cubre los registros (2026-09-15)

> Sprint M.1 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md), PR geeworker2#4 a #8.
> Crónica: [`SESSION_2026-09-15_el_nucleo_del_pipeline.md`](SESSION_2026-09-15_el_nucleo_del_pipeline.md).
> Implementa las piezas puras de `#32` (`ARQUITECTURA_PIPELINE.md` §3.2, §3.4 y §4).

**Decisión**, en seis partes:

1. **Las fórmulas son un lenguaje chico, que se lee con `ast` y nunca con `eval`**
   (`pipeline/formulas.py`). Acepta números, bandas, paréntesis, `+ - * /` y el
   signo. `#32` quería evaluar la misma fórmula en GEE (`Image.expression`) y en
   Python para probarla sin red, y eso vale solo si los dos la leen igual.
   Potencias, `%`, comparaciones, condicionales o funciones pueden leerse
   distinto, o no existir, en uno de los dos. Se suman cuando un índice los pida,
   comprobados antes en GEE.
2. **Un registro es un mapa inmutable por nombre, validado al importar**
   (`pipeline/registro.py`). Un nombre repetido rompe el import, no el mes 17 de
   un alta. Los nombres van en minúsculas y dígitos, **sin `_`**. El nombre es
   banda de GEE, clave del jsonb, segmento de la key del COG y prefijo de la clave
   de `reduceRegion`. Sin `_`, las claves `{indice}_{sufijo}` no pueden chocar, y
   en una key no entra `/` ni `..`.
3. **El `rango` de un índice es la plausibilidad, no la escala de color.** EVI no
   está acotado para espectros raros, así que un valor fuera de rango marca un
   dato roto, como una nube que escapó a la máscara. La paleta del mapa se decide
   cuando haga falta (M.4.5 o M.7).
4. **Las claves de `reduceRegion` se calculan, no se parsean**
   (`claves_de_salida`). Con una estadística, la clave es la banda; con varias,
   `{banda}_{sufijo}`. El sufijo es el nombre de salida de GEE y se busca exacto,
   así que también fija la fábrica: cambiar `percentile([10])` por `[20]` sin
   tocar el sufijo hace fallar la reducción, en lugar de pasar en silencio.
5. **El registro de estadísticas guarda fábricas, y la fábrica puede ser el
   método mismo.** Esto corrige la razón que daba `ARQUITECTURA` §3.2. Con
   `earthengine-api` 1.7.41, `ee.Reducer.median` existe antes de `ee.Initialize()`;
   lo que falla es llamarlo. Lo destapó el ruff estricto (`PLW0108`), se comprobó
   contra la librería, y un test lo fija en un proceso aparte.
6. **La huella de la receta cubre también lo que la receta toma de los
   registros**: la fórmula y las bandas de cada índice, y el sufijo de cada
   estadística. No incluye la versión, y no depende del orden. `HUELLAS` vive en el
   test, una línea por versión. Con solo los parámetros, cambiar la fórmula de
   NDVI en `indices.py` habría cambiado los números sin cambiar la versión, que es
   justo lo que la huella existe para impedir. **No cubre** el código de las
   etapas (M.2): eso se revisa en su PR.

**La receta v1 suma cuatro campos que el tablero no listaba:**
`sombras_nir_oscuro` (0,15, en reflectancia 0-1), `sombras_distancia_m` (1000),
`coleccion` y `coleccion_nubes`. Los dos primeros son parámetros de la máscara de
hoy que cambian un número, y §3.4 y §8.7 ya decían que iban a la receta. Si
quedaban afuera, M.2.2 los escribía como constantes y la huella no los veía.

**Descartado:**
- Evaluar las fórmulas con `eval`, que es un agujero, o con una librería
  (`numexpr`, `simpleeval`), que es una dependencia para cuarenta líneas.
- Guardar la huella en el código, al lado de la receta. Quien cambia un parámetro
  la actualizaría en el mismo archivo sin pensarlo; en el test es un valor fijado
  que el PR muestra.
- Meter las fábricas en la huella hasheando el bytecode de las `lambda`: es frágil
  entre versiones de Python, y el sufijo ya cumple ese papel.

**Consecuencias:**
- M.2 arma expresiones desde los registros y la receta, sin constantes propias en
  las etapas.
- Queda para M.2.6: confirmar que GEE da 0 al dividir por cero, y la clave de
  `reduceRegion` con una banda y varias salidas.
- Un hallazgo fuera de M.1 quedó registrado en el `HANDOFF` §4: en local, la suite
  le habla a GEE, a la base y a MinIO a través de `test_http_surface`.

> **Revisada el 2026-09-15 por `#36`:** el punto 5 queda reemplazado (el registro
> ya no guarda fábricas), y el argumento del punto 4 ("el sufijo fija la fábrica")
> deja de hacer falta, porque el sufijo se deriva del tipo.

---

## 36. Estadísticas declarativas, un solo histograma, y una receta que no deja que GEE cambie la escala (2026-09-15)

> Tareas M.1.6 y M.1.7 (geeworker2#11 y #12). Salieron de revisar el código nuevo
> contra la capa vieja: [`SESSION_2026-09-15_el_nucleo_del_pipeline.md`](SESSION_2026-09-15_el_nucleo_del_pipeline.md)
> §6. Reemplaza el punto 5 de `#35`.

**Decisión**, en cuatro partes:

1. **El registro de estadísticas es declarativo** (tipo y percentil), y
   `plan_de_reduccion()` fusiona:
   - todos los percentiles van en un `ee.Reducer.percentile` con `outputNames`;
   - mín y máx van en un `minMax`;
   - la mediana es el percentil 50.

   **Por qué:** según la documentación de `ee.Reducer.median` y de
   `ee.Reducer.percentile`, cada reductor arma su propio histograma. La receta v1
   armaba tres de los mismos píxeles, en cada índice de cada mes. Además, la
   huella veía cada fábrica solo por su sufijo. Ahora ve la definición entera, y
   `estadisticas.py` deja de importar `ee`.
2. **El sufijo se deriva del tipo** (`p50`, `mean`, `stdDev`, `min`, `max`), así
   que no puede contradecir al reductor.
3. **La receta suma `remuestreo` y `sombras_distancia_px`.**
   - `remuestreo`: v1 usa `nearest`, que es lo que GEE hace si no se le pide otro
     y lo que hacía la capa vieja. `bilinear` se compara en M.2.6.
   - `sombras_distancia_px` sale de `escala_m`. `directionalDistanceTransform`
     mide en píxeles del pedido, y la capa vieja pasaba `1000 / 10` fijo. La
     serie vieja pedía a 60 m, así que proyectaba sombras hasta 6 km. Eso sale de
     la documentación, y se confirma en M.2.6.
4. **Los pedidos a GEE van con `bestEffort=False` y `maxPixels` explícito.** Con
   `bestEffort=True`, GEE usa una escala mayor sin avisar; la serie vieja lo hacía,
   con `maxPixels=1e5`. No es un parámetro de la receta: es la garantía de que
   `escala_m` se respeta. Lo implementan M.2.4 y M.2.5.

**Una versión se congela con su primera fila.** `s2-mensual-v1` se re-fijó dos
veces el 2026-09-15 (M.1.6 y M.1.7) sin pasar a v2. La huella existe para rastrear
qué receta produjo cada fila, y v1 todavía no escribió ninguna. Desde que M.4.3
escriba la primera, cambiar el contenido de v1 exige v2. Las huellas anteriores
quedan anotadas en el test.

**Descartado:**
- **Pasar a v2 en cada ajuste antes de producir datos.** `HUELLAS` mostraría
  versiones que nunca se usaron.
- **`bilinear` en v1 sin medirlo.** Cambia NDRE y NDMI respecto de lo de hoy, y
  M.2.6 compara lado a lado con la capa vieja: con `nearest`, esa comparación es
  entre iguales.
- **Fusionar media y desvío.** Son acumulados, no histogramas: no hay nada que
  ganar.

**Consecuencias:**
- La clave de la mediana en GEE pasa a `ndvi_p50`. La del jsonb sigue siendo
  `mediana`.
- M.2.2 usa `sombras_distancia_px` y fija la proyección de la máscara.
- M.2.4 arma el reductor desde el plan, con `bestEffort=False`.
- M.2.6 compara `nearest` contra `bilinear`, y confirma los nombres de `minMax` y
  la proyección de sombras.

---

## 37. Un test que llama al arranque reemplaza `registrar_conexiones`, y un socket saboteado lo comprueba (2026-09-15)

> geeworker2#14. Era la deuda de `HANDOFF` §4 abierta el mismo día.

**Decisión:** `test_el_worker_arranca_aunque_falten_las_credenciales` reemplaza también
`app.registrar_conexiones` y verifica que se lo llama. Además sabotea
`socket.socket.connect`: anota cada intento y falla si hubo alguno.

**Por qué:** el test reemplazaba `app.init_ee` y `app.init_db`, pero `_startup()` termina en
`registrar_conexiones()`, que importa su propio `init_ee` y verifica el disco, MinIO,
`geodata`, GEE e Inngest. Con el `.env` local, la suite le hablaba de verdad a esos
servicios. En el CI no, porque no hay `.env`, y por eso nadie lo vio.

Reemplazar una función por su nombre en el módulo que la usa no alcanza cuando la función
importa sus propias dependencias. El socket saboteado es la red de seguridad de eso: si
mañana `_startup()` suma otro chequeo, el test lo agarra aunque `_startup()` se trague el
error. Es el mismo patrón de `#24` y M.1.5, en el mismo proceso en vez de uno aparte.

**Control negativo:** el test como estaba, con el mismo socket saboteado (sin tráfico real),
anotó 9 intentos, a los puertos 443 y 8288.

**Lo que el guardia no ve: la base.** psycopg2 se conecta desde libpq, en C, sin pasar por
el `socket` de Python, y en el control no apareció ningún intento a Postgres. A la base no
se llega porque se reemplazan `init_db` y `registrar_conexiones`, no por el guardia. Está
escrito en el docstring, para que nadie crea que el guardia cubre todo.

---

## 38. La fuente del pipeline, y cómo se prueba una etapa contra GEE: `pytest --gee` (2026-09-15)

> Tarea M.2.1, la primera etapa del sprint M.2. Diseño: `ARQUITECTURA_PIPELINE.md` §2,
> §3.3 y §8.

**La fuente** (`pipeline/etapas/fuente.py`) arma la colección del mes sobre el ROI, sin
calcular nada:
- **Pide solo las bandas que la receta usa:** las de sus índices, más el NIR, que la máscara
  de sombras necesita siempre. Para v1 son B2, B4, B5, B8 y B11, en el orden de S2.
- **Divide por 10.000 solo las bandas espectrales.** `SCL` es una clasificación y
  `probability` va de 0 a 100: ninguna de las dos es reflectancia.
- **El `remuestreo` de la receta va solo a las espectrales.** Promediar clases de `SCL` con
  `bilinear` daría clases que no existen. Con `nearest` no se llama a `resample`, porque
  `ee.Image.resample` no lo acepta como argumento.
- **Se arma con `addBands` sobre la escena**, porque la aritmética de GEE no conserva las
  propiedades, y la máscara necesita el azimut solar de la escena.
- **Una escena sin su probabilidad de nube queda afuera.** Es lo que hace el join
  `saveFirst`, igual que en la capa vieja: sin probabilidad no hay máscara.
- **No se descartan escenas por nubes ni por cobertura** (§8.2 y §8.3).
- `filterDate` recibe milisegundos, para no depender de cómo `ee` convierte un `datetime`
  con huso.

**Cómo se prueba una etapa.** Una etapa no se puede ejecutar sin `ee.Initialize()`, y el CI
no tiene credenciales (`#34`). Por eso hay dos clases de tests:
- **Los puros, en el CI.** Cada etapa separa lo que decide (qué bandas, qué remuestreo, qué
  rango) en funciones que no tocan `ee`.
- **Los marcados `gee`, que corren solo con `pytest --gee`** (`tests/conftest.py`). Le
  preguntan a GEE de verdad sobre un cuadrado de unos 500 m en el Bajío, que no es la parcela
  de un cliente. Juntan todo lo que quieren saber en un `ee.Dictionary`, así cada test hace
  una sola llamada. Se corren en local antes de abrir el PR: son la verificación contra lo
  real del `WORKFLOW` §6. M.2.6 la completa con parcelas reales.

**Por qué con una opción y no con el `.env`.** Si tener credenciales bastara para que un
test hable con GEE, sería otra vez el test de `#37`: una suite que sale a la red porque sí.
Con `--gee`, salir a la red es algo que alguien pide.

**Un test de arquitectura para el borde** (`tests/test_pipeline_borde.py`). Fuera de
`pipeline/ejecucion.py`, nada en `pipeline/` puede llamar a `getInfo`, `getDownloadURL`,
`computePixels` ni `ee.data`. Un `getInfo()` suelto en una etapa se saltearía el deadline, la
traducción de errores y el conteo de llamadas, y nada fallaría. El test lee el texto, así que
un alias se le escapa, pero agarra el caso que llega por costumbre. Tiene su control
negativo.

**Controles negativos, contra GEE real:**
- Sin dividir, el máximo de B8 en el ROI de prueba es 3987, así que el test que exige que
  quede por debajo de 2 lo rechaza.
- El azimut solar de una escena es 138,3; después de `divide` es `None`. Sin el `addBands`,
  la máscara de M.2.2 se quedaría sin azimut.

**Consecuencias:**
- M.2.2 a M.2.5 siguen el mismo patrón: decisiones puras en el CI, y la expresión verificada
  con `--gee`.
- M.2.2 recibe `SCL` intacta, a su resolución de 20 m.
- La suite pasa de 386 a 400 tests, más 3 que corren con `--gee`.

---

## 39. La máscara se calcula en la proyección de la escena a `escala_m`, y la de hoy descarta de más (2026-09-15)

> Tarea M.2.2. Cumple lo que `#36` le pedía: la distancia de sombra en píxeles, y la
> máscara en una proyección fija.

**Decisión:** `pipeline/etapas/nubes.py` es la máscara de la capa vieja
(`mask_s2cloudless_and_shadows`), con tres cambios:
- los parámetros salen de la receta;
- el NIR se compara en reflectancia 0-1;
- las tres capas (`nube`, `sombra`, `descarte`) se calculan con un `reproject` a la
  proyección del NIR de la escena, a `escala_m`. En el Bajío es EPSG:32614 a 10 m.

`componentes()` devuelve las tres capas por separado, para que los tests y M.2.6 puedan
medirlas. `enmascarar()` aplica `descarte`.

**Por qué el `reproject`, con números.** `directionalDistanceTransform` mide la sombra en
píxeles, y `focalMax` resuelve los 50 m de dilatación en píxeles, los dos en la proyección
del pedido. Sobre una escena del 4 de julio de 2026 con 32 % de nubes, en un cuadrado de 2
km, la fracción descartada fue:

| | pedida a 10 m | pedida a 60 m |
|---|---|---|
| con la proyección fija | 0,949 | 0,952 |
| sin la proyección fija | 0,949 | **0,353** |

Sin ella, el COG a 10 m y una estadística a 60 m saldrían con máscaras completamente
distintas. Es el control negativo del test `test_la_mascara_no_depende_de_la_escala_del_pedido`.
`reproject` suele desaconsejarse porque fuerza la escala del cálculo, y acá eso es justo lo
que se busca.

**Lo que salió al medir: la máscara de hoy descarta de más.** En esa misma escena, la
máscara descarta el **95 %** con un 32 % de nubes. Separada por capas, a 10 m:

| Capa | Fracción |
|---|---|
| nube (probabilidad > 45) | 0,323 |
| nube dilatada 50 m | **0,898** |
| sombra (proyectada y oscura) | 0,102 |
| descarte | 0,949 |
| descarte, con una erosión de 2 px de la nube antes de dilatar | **0,503** |

La probabilidad de s2cloudless viene salpicada, y dilatar 50 m cada píxel suelto se come casi
todo el cuadrado. El tutorial de s2cloudless de GEE erosiona antes de dilatar (`focalMin`)
justamente por eso. Las sombras están bien acotadas: la proyección cubre el 98 %, pero el
cruce con los píxeles oscuros las baja al 10 %.

**Por qué M.2.2 no lo cambia:**
- `ARQUITECTURA` §8.7 decidió que la máscara se queda igual.
- M.2.6 compara lado a lado con la capa vieja, y con la misma máscara la comparación es entre
  iguales. Es el mismo criterio que `#36` usó con `bilinear`.

La erosión es un parámetro nuevo de la receta, y cambia la huella. Como v1 todavía no escribió
filas, se la puede re-fijar sin pasar a v2 (`#36`).

**Consecuencias:**
- **M.2.6 suma una comparación:** la máscara de hoy contra la de la erosión, en las parcelas
  reales, mirando cobertura y valores. La decide el usuario antes de M.4.3.
- Con la máscara de hoy, un mes con varias escenas nubladas va a tener poca cobertura. La
  compuerta antes de M.4 ("cobertura coherente con la estación") lo va a mostrar.
- El test de la escena pide `descarte < 1`, que es flojo. Queda así a propósito, hasta que
  M.2.6 decida la máscara: fijar hoy 0,949 fijaría el sobre-descarte.

---

## 40. El compuesto junta primero las teselas de cada pasada, y recién después promedia (2026-09-15)

> Tarea M.2.3. La decisión de juntar las teselas la tomó el usuario el mismo día, sobre el
> hallazgo del sondeo de M.2.2.

**Decisión:** `pipeline/etapas/compuesto.py` hace tres cosas, en orden:

1. **Junta las teselas de cada pasada** (`por_pasada`), agrupando por
   `DATATAKE_IDENTIFIER` y mosaicando.
2. **Calcula los índices en cada pasada** (`indices_de`), con la fórmula del registro y
   `Image.expression`.
3. **Reduce por píxel con la mediana**, y suma la banda `n_obs` con las observaciones
   limpias de cada píxel.

**Por qué `DATATAKE_IDENTIFIER`.** Sentinel-2 entrega cada toma partida en teselas de 110 km
que se solapan unos 10 km, y un ROI en esa franja recibe la misma pasada dos veces. Sobre el
cuadrado de prueba, julio de 2026 trae **16 imágenes que son 8 pasadas**: el datatake es
idéntico en las teselas `14QKH` y `14QLH` de una toma, y distinto entre pasadas. Se prefirió
al par fecha + satélite porque es un solo campo y no depende de cómo se redondee la fecha.

**Control negativo, contra GEE real.** La banda `n_obs` sobre el ROI:

| | máximo | mediana |
|---|---|---|
| juntando las teselas | 6 | 2 |
| sin juntarlas | **12** | **4** |

Exactamente el doble, porque en ese ROI todas las pasadas venían duplicadas. Sin esta etapa,
`measurements.observaciones` diría 4 donde hubo 2.

**El orden es índice por pasada y después mediana** (`ARQUITECTURA` §8.1). El mapa viejo hacía
la mediana de las bandas y después el índice: un cociente de medianas no es la mediana de los
cocientes.

**`n_obs` se cuenta sobre el primer índice de la receta.** Todos los índices de una pasada
comparten su máscara, porque se aplica a la imagen entera (M.2.2).

**Un test compara los dos motores de la fórmula.** El mismo texto lo evalúa GEE con
`Image.expression` y `pipeline.formulas.evaluar` en Python, sobre las bandas de un píxel real:
coinciden con 1e-6. Es lo que sostiene que hay una sola fórmula (`#35`).
- **Detalle del test, aprendido a la mala:** un punto fijo caía en un píxel enmascarado y
  devolvía todo en `None`, y `sample` con `numPixels=1` no devuelve una muestra sino
  ninguna, porque el muestreo es probabilístico. Hoy pide 500 y usa la primera.

**Lo que este control deja a la vista.** La mediana de observaciones limpias en julio, que es
mes de lluvias, es **2 de 8 pasadas**. Es consistente con el sobre-descarte de `#39`, y es un
argumento más para la decisión de la erosión que M.2.6 tiene que traer.

**Consecuencias:**
- M.2.4 reduce este compuesto, y la cobertura sale de sus píxeles con dato.
- `measurements.observaciones` es la mediana de `n_obs` sobre la parcela.
- Donde dos teselas se solapan traen los mismos píxeles, así que `mosaic()` elige cualquiera
  de las dos. Si alguna vez difirieran, la diferencia sería del reprocesamiento de ESA.

---

## 41. La reducción: un pedido que no baja la escala solo, y una clave que falta es un error (2026-09-15)

> Tarea M.2.4, la última etapa del pipeline. Cumple lo que `#36` dejó escrito sobre los
> pedidos a GEE.

**Decisión:** `pipeline/etapas/reduccion.py` convierte el compuesto del mes en los números de
la parcela:
- `reductor(receta)` arma **un solo** `ee.Reducer` desde `plan_de_reduccion()`, combinando
  con `sharedInputs=True`: GEE recorre los píxeles una vez;
- `cobertura()` promedia la máscara del primer índice, que vale 1 donde hay dato y 0 donde
  no. Es la fracción de la parcela con al menos una observación limpia en el mes;
- `observaciones()` es la mediana de `n_obs`;
- `valores()` junta todo en un `ee.Dictionary`, para que M.2.5 lo pida en una llamada;
- `leer()` es pura, y es la que valida.

**Todos los pedidos van con `bestEffort=False` y `MAX_PIXELES` explícito.** Con
`bestEffort=True`, GEE devuelve un número calculado a otra escala sin avisar, que es lo que
hacía la serie vieja. **Control negativo:** con el tope bajado a 10 píxeles, el pedido
levanta en vez de responder. `MAX_PIXELES` es 1e8, unos 10.000 km² a 10 m, y no vive en la
receta porque no cambia ningún número: es el límite de lo que se está dispuesto a calcular.

**Una clave que falta es un error; una clave en `None`, no.**
- Que falte significa que el pedido no calculó lo que se le pidió: `leer()` levanta con el
  nombre de las claves que faltan. Tomarlo por un nulo guardaría un mes vacío sin que nadie
  se entere.
- Que venga en `None` significa que no hubo un solo píxel con dato, y eso es un mes sin
  cobertura, que es un resultado válido: la fila se escribe con `valor` nulo
  (`ARQUITECTURA` §6).

**Dos detalles de construcción:**
- El método de `ee.Reducer` sale de un dato (`Reductor.metodo`), así que se valida contra una
  lista blanca antes de llamarlo. Llamar a ciegas un método cuyo nombre viene de datos es el
  patrón que no hay que dejar entrar, aunque hoy esos datos sean nuestros.
- Una cobertura que no es un número levanta `TypeError`, y una fuera de [0, 1], `ValueError`.
  `True` se rechaza: para Python es un `int`, y pasaría por una cobertura de 1.

**Los primeros números reales del pipeline**, sobre el cuadrado de 2 km en el Bajío, julio de
2026:

| índice | min | p10 | mediana | media | p90 | max | desvío |
|---|---|---|---|---|---|---|---|
| ndvi | −0,115 | 0,066 | 0,278 | 0,329 | 0,676 | 0,925 | 0,230 |
| evi | **−6,447** | 0,093 | 0,218 | 0,258 | 0,468 | **1,622** | 0,172 |
| ndre | −0,278 | 0,018 | 0,178 | 0,198 | 0,404 | 0,639 | 0,147 |
| ndmi | −0,462 | −0,080 | 0,037 | 0,051 | 0,209 | 0,496 | 0,113 |

Cobertura del mes: **0,955**. Observaciones: **2**.

**Lo que salió de ahí:**
1. **EVI se sale de su rango declarado, y es por construcción.** Su denominador
   (`NIR + 6·RED − 7,5·BLUE + 1`) puede acercarse a cero en píxeles raros —agua, borde de
   nube, sombra— y el cociente se dispara. Pasa en el **0,012 %** de los píxeles, unos 5 de
   44.000. La mediana y los percentiles están sanos; los que se van son el mínimo y el
   máximo, que también se guardan en `estadisticas`. NDVI, NDRE y NDMI están acotados por su
   fórmula, y ninguno se salió. **No se cambia acá:** lo mide M.2.6 y lo decide el usuario,
   junto con la erosión (`#39`). Las opciones son acotar EVI al armar el compuesto, o
   aceptar que su mínimo y su máximo no son informativos.
2. **La cobertura mensual aguanta el sobre-descarte de la máscara.** Cada escena pierde
   mucho (`#39`), pero con ocho pasadas el 95,5 % de la parcela tuvo al menos una
   observación limpia. Lo que queda golpeado es `observaciones`, con mediana 2. Matiza lo que
   `#40` dejó anotado: el problema de la máscara se ve en cuántas observaciones respaldan
   cada píxel, no en cuánta parcela queda sin dato.

**Consecuencias:**
- El test pide el rango del registro a la **mediana**, que es lo que se guarda en `valor`, y
  a los extremos solo de las diferencias normalizadas. Fijar hoy el mínimo de EVI sería fijar
  el problema.
- La regla "si la cobertura no llega al mínimo, `valor` va nulo" no está acá: es de
  `productos.py` (M.2.5) y de la escritura (M.4.3). Esta etapa devuelve los números.

---

## 42. El borde con GEE: un plazo que necesita cliente, una tabla de errores y un conteo por contexto (2026-09-16)

> Tarea M.2.5, la que cierra el sprint M.2 salvo la validación con parcelas reales.

**Decisión**, en dos módulos:

- **`pipeline/productos.py`** encadena las cuatro etapas y no pide nada:
  `compuesto_del_mes()` es el tronco, `estadisticas_del_mes()` es la rama de la parcela y
  `mapa_del_mes()` la del rancho. **Las dos salen del mismo compuesto**, que es lo que cierra
  B-1 por construcción (`ARQUITECTURA` §2), y un test contra GEE lo comprueba: la mediana del
  mapa y la de las estadísticas coinciden con 1e-6.
- **`pipeline/ejecucion.py`** es el único que le pide a GEE que calcule: `traer()` y
  `url_de_descarga()`. Lo fija el test del borde desde M.2.1.

**El plazo necesita un cliente inicializado.** `ee.data.setDeadline` no guarda un número: 
reconstruye el cliente HTTP, y sin sesión levanta un `AssertionError` del propio `ee`
(`_install_cloud_api_resource`). Verificado el 2026-09-16, y es lo que hizo fallar nueve
tests antes de saberlo. Por eso `plazo()` se saltea si nadie inicializó GEE: ahí no hay
pedido que limitar, y el `getInfo()` que venga va a fallar solo, con un error que explica lo
que pasa. Los tests que miran el plazo pasaron a `gee`, porque necesitan cliente.

**La traducción de errores es una tabla de frases**, porque la API no da un código:
- **no se reintenta** lo que no se arregla repitiendo: memoria, demasiados píxeles, salida
  demasiado grande;
- **lo desconocido se reintenta.** Equivocarse hacia el reintento cuesta una llamada;
  equivocarse hacia el descarte pierde el mes.

`_PASAJEROS` (concurrencia, capacidad, timeout, cortes de red) **no entra en la decisión**,
porque daría lo mismo que el caso por defecto: preguntarle sería una rama muerta. Queda como
documentación de lo que se sabe que se recupera, y un test la recorre entera.

**El control que cierra el círculo entre la tabla y GEE.** Un test `gee` fuerza un tope de
píxeles imposible y comprueba que el error llega como `ErrorDeGEE` con `reintentable=False`.
Sin él, la tabla sería una lista de frases que nadie confrontó con lo que GEE contesta de
verdad.

---

## 43. Las dos alternativas pendientes pasan a ser parámetros de la receta, con el valor de hoy (2026-09-16)

> Prepara M.2.6. No decide nada: deja las dos variantes medibles.

**Decisión:** `nubes_erosion_px` y `acotar_indices` entran en la `Receta`, los dos con el
valor que tiene la capa vieja (`0` y `False`), así que **ningún número cambia hoy**.

- **`nubes_erosion_px`**: cuántos píxeles se encoge la máscara de nubes antes de dilatarla
  (`#39`). Va en píxeles de `escala_m`, como la sombra, porque la máscara se calcula en la
  proyección fija.
- **`acotar_indices`**: si cada índice se recorta a su `rango` del registro (`#41`).

**Por qué parámetros y no arreglos.** M.2.6 compara el pipeline contra la capa vieja sobre
parcelas reales. Cambiar la máscara o los índices antes de esa comparación la volvería una
comparación entre cosas distintas, y ya no diría si el pipeline reproduce lo de hoy. Es el
mismo criterio que `#36` usó con `bilinear`. Como parámetros:

- la comparación se hace cambiando un campo de la receta, no parcheando código en un script;
- las dos variantes salen en la misma corrida, que es lo que M.2.6 tiene que producir;
- la decisión sigue siendo del usuario, con los números de parcelas reales, antes de M.4.3.

**La huella de v1 se re-fijó otra vez**, a
`920554f366bbdc5e45887148702c10a242baa1a0394a29a2830fe78fd2bf751f`. Sumar un campo cambia la
huella aunque el comportamiento sea idéntico, porque la huella cubre la definición completa,
no el resultado. Se puede re-fijar porque v1 todavía no escribió ninguna fila (`#36`); desde
M.4.3 no.

**Lo que fija cada variante**, contra GEE:
- con `nubes_erosion_px=2` el descarte baja, y la capa `nube` no cambia: lo que cambia es lo
  que se dilata;
- con `acotar_indices=True` el mínimo y el máximo de EVI quedan dentro de `[-1, 1]`, y el
  test verifica primero que sin acotar se salen, para no volverse verde el día que la escena
  de prueba deje de tener el caso.

---

## 44. Las tres cosas que el pipeline daba por supuestas de GEE, confirmadas (2026-09-16)

> Primer escalón de M.2.6: `scripts/check_pipeline_real.py`. La tarea sigue abierta, porque
> la compuerta pide parcelas reales.

**Confirmado contra GEE**, y hasta hoy eran supuestos escritos en `#35` y `#36`:

1. **Dividir por cero da 0**, no un error. Las fórmulas del registro no necesitan protegerse:
   `pipeline/formulas.evaluar` levanta `ZeroDivisionError` en Python, y eso está bien, porque
   es el evaluador de referencia de los tests, no el del pipeline.
2. **Una banda con varias salidas sale como `{banda}_{sufijo}`**: `ndvi_min`, `ndvi_mean`,
   `ndvi_max`. Es lo que `claves_de_salida()` asume desde M.1.3, y lo que hacía que la
   receta v1, con siete estadísticas, tuviera claves predecibles.
3. **`minMax` nombra sus salidas `min` y `max`**, que es lo que `_SALIDA` da por sentado en
   `pipeline/estadisticas.py`.

**Los primeros números del pipeline al lado de la capa vieja**, sobre el cuadrado de prueba
del Bajío, julio de 2026:

| | pipeline | capa vieja |
|---|---|---|
| valor | 0,278 (mediana) | 0,310 (media) |
| pasadas que usa | 8 | 2 |
| tiempo | 3,8 s | 17,7 s |

No tienen por qué coincidir, y las razones ya estaban previstas (`ARQUITECTURA` §8): la vieja
promedia en vez de tomar la mediana, reduce a 60 m con `bestEffort=True`, y **descarta** las
pasadas con menos del 50 % del ROI limpio, que es por lo que usa 2 de 8. La compuerta pide
menos de 60 s por mes: 3,8 s deja margen, aunque una parcela real es más grande.

**Dos datos que van a pesar en las decisiones de M.2.6:**
- **La erosión mejora lo que se guarda, no solo el descarte por escena.** Con
  `nubes_erosion_px=2`, la cobertura del mes pasó de 0,955 a 1,000 y las observaciones por
  píxel de 2 a 4. Es el primer dato medido sobre el resultado.
- **El remuestreo cambia el número.** `nearest` y `bilinear` difieren 0,0039 en la mediana de
  NDRE y de NDMI: unas 4000 veces la tolerancia de float32. No es un detalle de precisión,
  es una elección que mueve el dato guardado.

**Lo que falta para cerrar M.2.6:** correr el script sobre 3 parcelas reales y 3 meses, con
uno de lluvia, y el COG de un rancho (`--cog`). Los números de las tablas los lee una
persona: la compuerta pide que sean plausibles, no que coincidan con la capa vieja.

---

## 45. La receta v1 lleva erosión de 2 px y acota los índices (2026-09-17)

> **Decisión del usuario**, con los números de M.2.6. Cierra lo que `#39` y `#41` dejaron
> abierto, y lo que `#43` habia dejado listo para medir.

**Decisión:** `nubes_erosion_px = 2` y `acotar_indices = True` en `s2-mensual-v1`.

**Las parcelas.** Tres cuadrados de 101 ha en la Orinoquía (4,68 N, 69,79 O), los tres
iguales y en fila. **No cumplen lo que la tarea pedía** —tamaños y regiones distintas— así
que la compuerta se cruzó midiendo una sola situación tres veces. Lo que sí aportan: es zona
de mucha nube, que es donde la máscara se pone a prueba. Meses: febrero (seco), abril
(arranque de lluvias) y julio (lluvias).

**La compuerta pasó:**

| Criterio | Pedido | Medido |
|---|---|---|
| NDVI plausible | en [−1, 1] | 0,30–0,35 en seco, 0,55–0,57 en lluvias |
| Cobertura coherente con la estación | — | 1,000 en seco; 0,86–0,98 en julio |
| Un mes de parcela | < 60 s | **2 a 3 s** |

**El resultado que más importa:** en una parcela de julio, **la capa vieja no devolvió nada**
—descartó todas las pasadas por su filtro de cobertura del 50 %— y el pipeline dio 0,553 con
86 % de cobertura. Es exactamente lo que el compuesto mensual venía a resolver (`#31`).

**Por qué la erosión.** No apareció ningún caso donde empeore:
- en los siete meses despejados no cambió nada;
- en los dos nublados subió la cobertura (0,856 → 0,936 y 0,980 → 0,995) y las observaciones
  (1 → 2 y 2 → 3);
- en la escena del Bajío baja el descarte de 0,95 a 0,50, y la cobertura del mes sube de
  0,955 a 1,000 con las observaciones de 2 a 4 (`#39` y `#44`).

**Por qué acotar.** En estas tres parcelas EVI **nunca** se salió del rango: los mínimos
fueron de 0,057 a 0,224, y acotar no cambió un solo número. El caso feo es del Bajío
(−6,447), así que depende del terreno y no es sistemático. Se acota igual porque lo que se
guarda en `estadisticas` se le muestra al usuario, y un mínimo de −6,4 no significa nada para
quien lo lee. **Y las dos decisiones atacan cosas distintas:** con la erosión puesta, la
escena del Bajío **sigue** teniendo EVI fuera de rango, así que una no reemplaza a la otra.

**La huella de v1 se re-fijó otra vez**, a
`75dbb738dd2a69a30c89107b7cb55b1192b5bd4030f368b9f5b185099c42a352`. **Esta vez los números sí
cambian**, no solo la definición. Se re-fija igual porque la regla de `#36` es la primera fila
escrita, y M.4.3 todavía no escribió ninguna. Desde esa primera fila, cualquier cambio exige
v2.

**Los tests cambiaron de lado.** Los que comparaban "v1 contra la variante" ahora comparan la
variante contra v1: sin erosión el descarte es mayor, y sin acotar la escena del Bajío se
sale del rango. El de EVI trae su propio control: si algún día la escena de prueba deja de
tener el caso, falla con ese mensaje en vez de volverse verde por vacío.

**Cómo quedó, corriendo el script otra vez ya con la receta decidida:**

| parcela, julio | cobertura antes | después | observaciones antes | después |
|---|---|---|---|---|
| parcela_1 | 0,999 | 1,000 | 2 | 2 |
| parcela_2 | 0,980 | **0,995** | 2 | **3** |
| parcela_3 | 0,856 | **0,936** | 1 | **2** |

Los meses secos no se movieron: ya estaban en 1,000. **El NDVI de julio bajó un poco**
(0,568 → 0,561 y 0,553 → 0,537), y era de esperar: al dejar de descartar píxeles limpios, la
mediana deja de estar sesgada hacia los que sobrevivían. Los tiempos siguen entre 2 y 8 s.

**El escalón 2 del script se dio vuelta con la decisión.** Comparaba la receta contra las
variantes *prendidas*; desde que v1 las tiene, las dos columnas salían idénticas. Ahora
compara contra las alternativas **apagadas**, y marca como problema que la erosión baje la
cobertura en alguna parcela, que sería el dato que obligaría a revisar esta decisión.

**Lo que esta decisión no cierra:**
- la compuerta se cruzó con tres parcelas iguales de 101 ha. **El tiempo por mes con una
  parcela grande sigue sin medirse**, y es lo que fija el límite de concurrencia en M.5;
- el COG del rancho (`--cog`) todavía no se corrió sobre una parcela real.

**El error viaja como `ErrorDeGEE` con `reintentable`, y el pipeline no importa Inngest.**
Quién orquesta no es asunto del pipeline: el handler traduce esa marca a lo que Inngest
entiende, que es el vocabulario que ya tiene `services/avance_job.py` (`es_definitivo`,
`NonRetriableError`).

**El conteo de llamadas va en un `ContextVar`**, igual que el job actual de `avance_job.py`:
dos handlers en paralelo no se pisan, y un pedido que falla **también** cuenta, porque si no
la bitácora diría que el step no le habló a GEE.

**Un hallazgo para M.4.5.** La imagen de `mapa_del_mes()` no trae una escala útil: su
proyección por defecto es WGS84 de 1° (111.319 m), porque la aritmética de bandas pierde la
proyección de la escena. Quien la descargue tiene que pasar `scale` y `crs`, que es lo que ya
hace `services/ee/gee_download.py` desde `#19`. La máscara sí se calculó a `escala_m`
(`#39`), así que los píxeles son los mismos que los de las estadísticas.

**Consecuencias:**
- M.4.4 y M.4.5 llaman a `ejecucion`, no a las etapas, y envuelven el step con `contando()`
  para dejar el número de llamadas en la bitácora.
- El handler es quien decide qué hacer con `reintentable`: el pipeline solo lo informa.
- `PLAZO_MS` son dos minutos. La compuerta antes de M.4 pide que un mes tarde menos de 60 s,
  así que el plazo está para que un pedido patológico falle, no para recortar uno normal.

---

## 46. `check_schema.py` verifica el contrato con Geocore en vez de imprimirlo (2026-09-17)

> Tarea M.3.4 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). El esquema es de Geocore
> (`DECISIONS #15` de Geocore) y sus columnas nuevas son `#25` de allá.

**Decisión.** El script pasó de **listar** el esquema a **compararlo** contra un contrato
escrito: cada columna que el worker usa, con su tipo y —donde importa— su nulabilidad, más
las constraints que necesitan los `ON CONFLICT` y los dos índices únicos parciales del cierre
de mes. Sale con código 1 si algo no está. El listado de antes quedó bajo `--dump`.

**Por qué ahora.** El esquema es contrato entre repos y no hay compilador que agarre un
desajuste. Imprimirlo servía cuando lo leía una persona en el momento; con las siete columnas
nuevas de la FASE M, lo que hace falta es que **el script diga si la base está lista**, antes
de que M.4.3 escriba la primera fila mensual. El modo de fallo que evita es el feo: el INSERT
revienta en producción, adentro de un step de Inngest, con el cálculo de GEE ya gastado.

**Qué se verifica, y qué no.**
- **Lo que el worker usa**, no el esquema entero. Una columna que Geocore agregue y el worker
  no toque no puede poner esto en rojo: sería un contrato que se rompe solo.
- **La nulabilidad, solo donde cambia el comportamiento.** El caso es `measurements.valor`:
  si volviera a ser `NOT NULL`, un mes con cobertura bajo el mínimo **no se podría escribir**,
  y ese mes quedaría como "nunca procesado" para el cierre de mes.
- **Los índices, por sus columnas y su filtro, no por su nombre.** El nombre lo elige la
  migración; las columnas son el contrato. Un índice único **sin** el `WHERE` no cuenta: sin
  el filtro, Postgres cuenta los NULL como distintos y dos jobs del mismo rancho y mes no
  chocarían, que es justo lo que los dos índices parciales existen para impedir.
- **Los CHECK de rango no se repiten acá**: los verifica el script de M.3.1b, del lado de
  Geocore, que es quien los crea.

**Cómo se probó.** 15 tests sin base sobre las funciones de comparación, que reciben lo que
devolvió la consulta (así el CI los corre sin Postgres), y **la corrida real contra PostGIS 15
en un contenedor**, con las migraciones de Geocore aplicadas con `dotnet ef database update`:
**43 de 43 en ok**.
- **Control negativo:** con la base llevada a la migración anterior (`ProcessingJobEvents`,
  que es el estado de producción hasta que se aplicó M.3.1b), el script sale en **33 de 43**,
  con código 1, y nombra exactamente las siete columnas nuevas, los dos índices y
  `measurements.valor` como `DISTINTO` por haber vuelto a `NOT NULL`.

**Un detalle que salió de correrlo:** lo que se imprime va **sin acentos**. La consola de
Windows no siempre está en UTF-8, y la primera corrida real mostró los títulos con
caracteres rotos. Los comentarios y docstrings siguen con acentos: no se imprimen.

## 47. La key del COG mensual lleva el tenant y la receta (2026-09-17)

> Tarea M.4.1 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). Es la base de M.8.1 (A01) y
> reemplaza, para lo mensual, la forma que proponía `ARQUITECTURA_PIPELINE.md` §6
> (`ranchos/{id}/ndvi/{AAAA-MM}.tif`). Contesta `PREGUNTAS_ABIERTAS` A-7 para lo que
> se escribe desde M.4.

**Decisión.** El COG mensual de un rancho va en

```
tenants/{tenantId}/ranchos/{ranchoId}/{receta}/{indice}/{AAAA-MM}.tif
tenants/7f3c…/ranchos/a1b2…/s2-mensual-v1/ndvi/2025-09.tif
```

y la fila de `layers` se identifica con `rancho_mensual_{indice}_{ranchoId}_{AAAA-MM}`,
**sin la receta**. Las dos salen de `pipeline/claves.py:claves_cog_mensual()`, juntas, como
pide E.9. Decidido por el usuario el 2026-09-17, entre esta forma y la del tablero (la
misma sin `{receta}`).

**Por qué el tenant primero.** TiTiler va a comparar el prefijo `tenants/{tenantId}/`
contra el `tenant_id` del token de mapa (M.8.1). Hoy su validación de ruta es un
`startswith` (`terra_tiles/security.py`), así que alcanza con que el tenant sea el primer
segmento. Fijarlo ahora, antes del primer COG mensual, es lo que evita mover objetos
después. **El tenant de un rancho no cambia:** `Rancho.TenantId` es `private set` y solo se
asigna en `Create`. La key no puede quedar vieja por eso.

**Por qué la receta en la key.** El tileserver sirve los tiles con
`Cache-Control: public, max-age=31536000, immutable` (`terra_tiles/caching.py`). Si un
reproceso con otra receta escribiera sobre la misma key, la URL del tile no cambiaría, y
el navegador (o un CDN) seguiría mostrando el mapa viejo durante un año. Con la receta
adentro, una receta nueva es otra key y, por lo tanto, otra URL: el caché se invalida solo.
Bajar el caché para cubrir el reproceso le cobraría a todos los tiles un caso que pasa una
vez por receta.

**Por qué la receta no va en la natural_key.** La fila de `layers` es una por rancho,
índice y mes, igual que la de `measurements`, cuya PK tampoco lleva la receta. Al
reprocesar, el upsert apunta la fila a la key nueva y actualiza `receta`: el panel
(M.7.4) no tiene que elegir entre dos filas del mismo mes. El objeto de la receta anterior
**queda en el bucket**, bajo `…/ranchos/{r}/{receta vieja}/`. Es un prefijo, así que se
limpia con una regla o con `mc rm --recursive`; hasta entonces, ocupa lugar y no lo lee
nadie.

**El orden va de lo más estable a lo que más varía** (A-7: S3 solo filtra por prefijo):
tenant, rancho, receta, índice y mes. Cada pregunta útil es un prefijo: lo de un tenant (A01),
lo de un rancho, lo de una receta (limpieza) y la serie de un índice.

**Lo que fija el código, y por qué.**
- **Los uuid salen en forma canónica**, en minúsculas y con guiones. Geocore escribe el
  `tenant_id` del token con `Guid.ToString()`, y para un `startswith`, `7F3C…` y `7f3c…`
  son dos tenants distintos: el dueño del COG recibiría 403.
- **Los ids pasan por `uuid.UUID`**, así que un segmento no puede traer `/` ni `..`. El id
  llega en un evento, y uno que armara otra ruta escribiría fuera del prefijo de su tenant.
  **El uuid nulo se rechaza:** es el `Guid.Empty` de un id sin asignar.
- **`prefijo_de_tenant()` devuelve la barra final.** Sin ella, el prefijo de un tenant sería
  también el comienzo de cualquier id que empezara igual. Con uuid de largo fijo no pasa,
  pero la comparación de M.8.1 no debería depender de eso. **M.8.1 compara con la barra.**
- **Los argumentos van por nombre:** `tenant_id` y `rancho_id` son los dos texto, e
  intercambiarlos daría una key válida en el lugar equivocado.
- **El índice tiene que estar en la receta.**

**Lo que no cambia, y lo que queda para después.**
- **Lo que ya existe en el bucket se queda donde está.** Las keys de la capa vieja
  (`ranchos/{id}/{fecha}_{indice}.tif`, `parcelas/…`, `exports/…`) y sus filas de `layers` no
  se migran. Son de prueba o las borra M.6.1.
- **Los on-demand y los exports todavía no llevan tenant.** Cuando M.8.1 active la
  comparación, esas keys darán 403. Se resuelve en M.6.2, que decide si esos handlers se
  borran o se rehacen: si se rehacen, van bajo `tenants/{t}/`.
- **Para M.8.1:** TerraAdmin y TerraSupport no tienen tenant. Su token tiene que poder
  leer cualquier `tenants/…`, o llevar el tenant que eligieron con `X-Tenant-ID`. Se decide
  allá.

**Cómo se probó.** 25 tests en `tests/test_pipeline_claves.py`: la key entera, carácter por
carácter; el prefijo con la barra; cuatro formas de escribir un uuid que salen iguales; seis
ids rechazados, incluidos `../otro-tenant` y el nulo; los argumentos por nombre; que otra
receta cambie la key y no la natural_key; y que la natural_key no choque con la de la capa
vieja. **Control negativo:** con la canonicalización quitada (el id pasa tal cual), cuatro
tests salen en rojo.

## 48. `handlers/`: el wrapper de jobs y las utilidades salen de la capa vieja (2026-09-17)

> Tarea M.4.2 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md) (`ARQUITECTURA_PIPELINE.md` §4).

**Decisión.** Sale de `services/inngest_handlers.py`, sin cambiar comportamiento, un paquete
`handlers/` con tres módulos:
- `seguimiento.py`: `RETRIES` y el wrapper que lleva el estado del job y su bitácora;
- `geometria.py`: `normalizar_coordenadas` y `coords_to_geometry`;
- `utilidades.py`: `borrar_temporales`, `entre` y `ms_desde`.

Los handlers del pipeline mensual (M.4.4 y M.4.5) los usan **sin importar la capa vieja**,
que M.6.1 borra. `inngest_handlers.py` los importa con los nombres de siempre (`_entre`,
`_borrar_temporales`…), así que sus handlers no cambian una línea.

**El wrapper recibe con qué escribe el estado.** `envolver_con_estado(func, actualizar_job)`.
Hay dos decoradores encima:
- `con_seguimiento`, para los handlers nuevos, busca `db_repository.update_processing_job` al
  llamarla;
- `_with_job_tracking`, que se queda en `inngest_handlers.py`, busca `update_processing_job`
  en **ese** módulo, que es donde la reemplazan los tests de los handlers viejos.

El criterio de aceptación era "la suite entera verde sin tocar un test", y dos archivos de
tests parchean `handlers.update_processing_job`. Si el wrapper hubiera importado la función en
su módulo nuevo, esos parches habrían dejado de alcanzarlo, y los tests habrían escrito en la
base de verdad o fallado. Recibirla como parámetro es además lo que corresponde (el wrapper no
tiene por qué saber de la base); el adaptador de la capa vieja se va con ella.

**Me desvié del tablero en una cosa: `claves_de_capa()` se queda en `inngest_handlers.py`.**
El tablero decía "sacar las claves". Desde M.4.1 las claves de lo mensual están en
`pipeline/claves.py` (`#47`). La función vieja arma las keys de la capa vieja, y solo la usan
sus handlers: mudarla metería en el paquete nuevo código que M.6.1 borra.

**El Dockerfile copia `pipeline/` y `handlers/`.** Sin eso, el merge de este PR habría
desplegado un contenedor que muere al arrancar con `ModuleNotFoundError: handlers`. Es el
bug que tuvo el tileserver, y el que `PROXIMA_SESION` tenía anotado como 👥 para `pipeline/`
antes de M.4.4. Iba en el mismo PR o no iba.

**Y un test que lo cuida, porque el CI no construye la imagen.** `tests/test_dockerfile.py`
arma en un directorio temporal **solo** lo que copian los `COPY` del Dockerfile, e importa
`app` ahí, sin el repo en el path y con el socket saboteado.
- **Control negativo:** con el `COPY handlers/` quitado, el test sale en rojo con
  `No module named 'handlers'`.
- Un segundo test controla que el parser vea todos los `COPY`.
- **No cubre `pipeline/` todavía:** `app` no lo importa. Lo va a cubrir solo desde M.4.4,
  cuando un handler lo importe.

**Lo único observable que cambia:** las líneas de log del wrapper (`Job … fallo…`) salen
con el logger `handlers.seguimiento` en vez de `inngest_handlers`.

**`handlers/` tiene el ruff estricto de `pipeline/`** (`handlers/ruff.toml` lo extiende). El
CI no lo corre, porque el CI no se toca por ahora. Se corre en local:
`ruff check pipeline/ handlers/`. En `inngest_handlers.py` quedaron los mismos 5 hallazgos
de la config de la raíz que en `main`: ninguno nuevo.

**Cómo se probó.**
- La suite: 495 verdes. Son 488 sin tocar ninguno, más 7 nuevos: 3 del Dockerfile y 4 de
  `con_seguimiento`, incluido que importar `handlers` no abre conexiones.
- **La imagen construida con Docker y arrancada**, sin credenciales:
  - `/health` responde 200 y las 8 funciones quedan registradas;
  - `handlers` y `pipeline` se importan adentro del contenedor;
  - los únicos `ERROR` son los esperados de GEE y la base sin configurar (`#27`).

## 49. La escritura mensual: una fila por índice, también sin valor (2026-09-17)

> Tarea M.4.3 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). El esquema es la migración
> `MedicionesMensuales` de Geocore (`#25` de allá), y su contrato lo verifica
> `check_schema.py` (`#46`).

**Decisión.** Dos piezas:
- `pipeline/filas.py:filas_del_mes()`, **pura**: de la `Reduccion` de un mes a una
  `FilaMensual` por índice de la receta;
- `db_repository.upsert_mediciones_mensuales()`, que las escribe en una conexión y un
  round-trip.

Y `insert_layer` suma `receta` y `estadisticas`, opcionales.

**Qué lleva cada fila.**
- `fecha` es el primer instante del mes en UTC, igual para todos los índices. Es la PK, así
  que tiene que ser la misma en cada reproceso.
- `valor` es la mediana, **o nulo si la cobertura quedó bajo el mínimo de la receta**
  (0,3 en v1). En el mínimo exacto, va.
- **`estadisticas` va siempre, aunque `valor` sea nulo.** Son números que se calcularon y se
  pagaron, y descartarlos pierde información. Quien los lee filtra por `valor` o por
  `cobertura`: la métrica del rancho ya lo hace (Geocore `#29`). **M.7.3 tiene que hacer lo
  mismo** antes de dibujar la banda p10–p90 de un mes nublado.
- Un mes sin un solo píxel con dato se escribe igual: las estadísticas van con nulos adentro
  y `observaciones` nula.

**Tres diferencias con el `insert_measurements` viejo, y por qué.**
- **Las filas con `valor` nulo se escriben.** El mes existe aunque esté nublado: el front
  dibuja el hueco (B-6), y el cierre de mes (M.5) sabe que ya se procesó. La vieja las
  salteaba porque la columna era `NOT NULL`.
- **En el conflicto, `min_val` y `max_val` pasan a `NULL`.** Lo mensual ya no los escribe
  (`ARQUITECTURA` §6), y una fila vieja por pasada del día 1 cae en la misma PK que la del
  mes. Sin esto, sus `min_val` y `max_val` quedaban colgados en la fila mensual. Pasa hasta
  que el equipo haga M.3.5.
- **Un lote con filas repetidas se rechaza antes de ir a la base.** La vieja se quedaba con
  la última; las filas de un mes no se repiten por construcción, así que acá es un bug.

**Los `NaN` no llegan a la base.** `json.dumps` escribe `NaN` sin quejarse, pero no es JSON,
y Postgres rechaza el `jsonb` con el lote entero, sin decir qué número fue. `filas_del_mes`
los rechaza nombrando el índice y la estadística, y el `Json` del repositorio usa
`allow_nan=False` como segunda defensa. GEE devuelve `None` cuando no hay píxeles: un `NaN`
significaría que algo cambió.

**Un arreglo de paso: `insert_layer` no hacía rollback.** Si el insert fallaba, la conexión
volvía al pool con la transacción abortada, y la siguiente consulta en esa conexión fallaba con
`current transaction is aborted`. Con los CHECK nuevos, un insert rechazado dejó de ser
teórico. Ahora usa el `_deshacer` que ya usaban las demás.

**`estadisticas` de una capa tiene que ser un `dict`.** La base tiene un CHECK que rechaza
cualquier otro JSON; se valida antes para que el error diga qué se pasó. Los llamadores
viejos no pasan ni `receta` ni `estadisticas`, y quedan en `NULL`, como antes.

**Cuándo se congela `s2-mensual-v1`.** Las filas de la verificación de abajo fueron locales y
se borraron. **La primera fila real la escribe el handler de M.4.4 en producción**, así que
la receta v1 queda congelada **al mergear M.4.4**: desde ahí, cambiar un parámetro es una v2.

**Cómo se probó.**
- 23 tests nuevos: 13 de las filas (puros) y 10 de la escritura, con una conexión falsa que
  captura el SQL y los valores. La suite: 518 verdes.
- **La corrida real**, contra PostGIS 15 en un contenedor con las migraciones de Geocore
  aplicadas. Primero `check_schema.py`: **43 de 43 en ok**. Después la escritura:
  - dos escrituras del mismo mes dejan 4 filas, con los valores de la segunda;
  - la fila vieja por pasada del día 1 queda convertida en la mensual, con `min_val` y
    `max_val` en `NULL`;
  - un mes con cobertura 0,1 deja 4 filas y ninguna con `valor`;
  - un mes sin dato deja las estadísticas con nulos adentro y `observaciones` nula;
  - `cobertura = 73` (un porcentaje) lo rechaza `ck_measurements_cobertura`: no entra
    ninguna fila del lote, y el pool sigue sano después del rechazo;
  - la capa mensual, escrita dos veces, es una sola fila con `receta` y `estadisticas`
    (`jsonb_typeof = object`); una capa vieja deja esas dos columnas en `NULL`.

## 50. `process_parcela` sobre el pipeline, y el mes sin píxeles que GEE contesta sin claves (2026-09-18)

> Tarea M.4.4 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). Usa el wrapper de `#48`, la
> escritura de `#49` y el borde de `#42`. **Su merge congela `s2-mensual-v1`**: es el
> primer handler que escribe filas mensuales en producción (confirmado por el usuario al
> abrir la sesión 8).

**Decisión.** `handlers/parcela.py:process_parcela` reemplaza al de la capa vieja sobre
`terra/parcela.created`:
- un step `plan`, que fija "hoy" y devuelve los meses (`meses_cerrados(hoy_utc(), 24)`) y la
  versión de la receta;
- un step `mes-AAAA-MM` por mes, del más viejo al más nuevo: `init_ee` → el ROI →
  `ejecucion.reduccion_del_mes` (una llamada a GEE, contada) → `filas_del_mes` →
  `upsert_mediciones_mensuales`, con una línea de bitácora y el avance de 2 a 99.

Decorado con `handlers.seguimiento.con_seguimiento`, y **no importa `inngest_handlers.py`**:
lo fija un test que importa el módulo en un proceso aparte y mira `sys.modules`.

**El mismo `fn_id` (`process-parcela`).** Para Inngest es la misma función con otro código,
no dos funciones sobre el mismo evento. El `process_parcela` viejo se borró, con sus
ayudantes (`ventanas`, `_escribir_serie`) y sus tres tests, y `all_functions` registra el
nuevo hasta que M.6.1 mude la lista. Un run viejo en vuelo durante el deploy no encuentra
sus steps (tenían otros ids) y corre el alta nueva desde el `plan`: es idempotente.

**Lo que el alta deja de hacer**, a propósito: ya no sube el COG de la parcela (A-5: el
mapa es del rancho, M.4.5) ni escribe `sentinel2_dates` (`ARQUITECTURA` §9).

**Tres reglas del handler.**
- **El reloj se lee dentro del step `plan`.** Inngest vuelve a correr el cuerpo en cada
  request, y un "hoy" leído afuera daría otros meses si el run cruza el fin de mes. Hay un
  test con el step memoizado y "hoy" corrido al mes siguiente; con el reloj afuera, sale
  rojo (control negativo hecho).
- **Un `ErrorDeGEE` no reintentable se traduce a `NonRetriableError`** en el handler, que
  es quien conoce a Inngest (`#42`). "Sin memoria" corta el alta y marca `failed` al primer
  intento; "demasiados pedidos concurrentes" se reintenta y el job sigue `running`.
- **Un evento sin `parcelaId`, `tenantId` o `coordinates` falla sin reintentos.** Antes era
  un `KeyError`, que Inngest reintentaba tres veces para dar lo mismo.

**Lo que salió de probarlo contra lo real: un mes sin píxeles no trae claves.** El alta de
la parcela 1 de `scratch/` se cortó en 2026-05. Ese mes tiene 10 escenas y la máscara las
tapa enteras: cobertura 0. **GEE no devolvió las estadísticas en `None`: las omitió**, y la
respuesta trajo una sola clave, `cobertura`. `reduccion.leer` (`#41`) trataba la falta como
error, así que el step habría fallado en los cuatro intentos y **el alta de esa parcela no
habría terminado nunca**. El supuesto ("sin píxeles, las claves vienen en `None`") estaba
escrito en el módulo y en un test, pero nadie lo había visto: en M.2.6 no salió ningún mes
con cobertura cero.

El arreglo, en `leer`: **con cobertura 0, las estadísticas y `observaciones` que falten se
leen como `None`**; con cobertura mayor que cero, una clave que falta sigue siendo un error.
La cobertura sigue siendo obligatoria. No cambia ningún número ni la huella de la receta, así
que no pide una v2. Lo fija un test `--gee` que tapa el compuesto con `updateMask(0)` sobre
el ROI público de los tests y comprueba que GEE omite la clave.

**Cómo se probó.**
- 17 tests nuevos (15 del handler y 2 de `leer`, más uno `--gee`); 5 viejos del alta por
  ventanas se borraron con ella. El step imita al SDK: envuelve el error en un
  `BaseException` y puede memoizar por id. `estadisticas_del_mes` devuelve una expresión
  falsa, así que `ejecucion.traer`, `leer` y `filas_del_mes` corren de verdad.
- **Contra lo real**: el handler entero, con GEE de verdad y PostGIS 15 local con las
  migraciones de Geocore (`check_schema.py` 43 de 43). Parcela 1: **24 meses en 101 s**
  (3 a 8 s por mes), 96 filas, 92 con valor (2026-05 sin dato), el job en `completed` al
  100 %, 27 líneas de bitácora con un aviso y ningún error. Repetir un mes no suma filas. El
  NDVI sigue la estación: 0,22 a 0,27 en la seca de marzo y abril, 0,56 a 0,58 en lluvias.
  Las parcelas 2 y 3, después del arreglo: 103 s y 95 s, 96 filas cada una, las dos
  `completed` y sin errores en la bitácora. **Las tres tenían un mes con cobertura 0**: sin
  el arreglo de `leer`, ninguna de las tres altas habría terminado.

**Registrado, sin hacer:**
- **Sin límite de concurrencia.** Un KML de 500 parcelas son 500 altas a la vez contra GEE,
  como con el handler viejo. Va con M.5.3, que fija el límite de las funciones mensuales:
  conviene que el alta lo comparta.
- **`init_ee()` corre en cada step**, como en la capa vieja. Cuesta poco al lado del pedido,
  pero se podría saltear si el cliente ya está inicializado.
- **`observaciones` sale con ruido de float** (`2.9999999999999947`): es la mediana de
  `n_obs` que interpola el histograma. Redondearla cambia un número guardado, así que va con
  una receta nueva si se decide.

## 51. `process_rancho` sobre el pipeline: un COG por mes con dato, y lo enmascarado como nodata (2026-09-18)

> Tarea M.4.5 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). Usa la key de `#47`, el wrapper de
> `#48`, `insert_layer` de `#49` y las altas de `#50`.

**Decisión.** `handlers/rancho.py:process_rancho` reemplaza al de la capa vieja sobre
`terra/rancho.created`, con el mismo `fn_id` (`process-rancho`): un step `plan` y un step
`mes-AAAA-MM` por mes. Cada mes, en **un solo step** (E.3, `#26`):
1. las estadísticas del rancho con la misma reducción que la parcela (una llamada);
2. **con cobertura 0 no hay mapa**: nada se baja ni se sube, y queda un aviso en la bitácora
   (decisión del usuario, 2026-09-18). Con cualquier cobertura mayor, aunque quede bajo el
   mínimo de la receta, el mapa va: muestra lo que se vio;
3. `productos.mapa_del_mes(…, "ndvi")` con lo enmascarado relleno con `-9999`, la URL por el
   borde (`ejecucion.url_de_descarga`, contada y con la traducción de errores), la descarga,
   el COG y la subida a la key de `claves_cog_mensual`;
4. `insert_layer(source="mensual", receta=…, estadisticas=…)`. `estadisticas` son las del
   NDVI más `cobertura` y `observaciones` (D-2).

**Lo común de las dos altas pasa a `handlers/altas.py`**: el step `plan`, los campos
obligatorios del evento y la traducción de `ErrorDeGEE` a `NonRetriableError`. `parcela.py`
lo usa sin cambiar de comportamiento: sus 15 tests pasan sin tocar más que dónde se
reemplaza el reloj.

**Se borraron el `process_rancho` viejo y `register_layer`.** El viejo emitía
`terra/raster.ingested` para que `register_layer` escribiera la fila; el nuevo la escribe en
el mismo step, y `register_layer` quedaba escuchando un evento que ya no emite nadie. Desde
M.4.5 **el worker no emite ningún evento**: los avisos de arranque por `INNGEST_EVENT_KEY`
lo dicen. Quedan 7 funciones registradas.

**Lo que salió de probarlo contra lo real: el GeoTIFF de GEE no declara nodata.** Una
descarga real (parcela 1, 2025-06, cobertura 0,62) llegó con `nodata=None` y la máscara
`all_valid`: los 3.929 píxeles enmascarados de 10.325 (nubes, y lo que queda fuera del
polígono) venían como `0.0`. **En NDVI, 0 es suelo desnudo**: el COG habría pintado cada nube
como un lote pelado. La capa vieja tenía el mismo defecto. El arreglo:
- `mapa_del_mes(…).unmask(-9999, sameFootprint=False)`: el centinela también fuera del
  polígono. Ningún índice normalizado puede dar −9999;
- `convert_to_cog(…, nodata=-9999)`: con el `add_mask=True` de siempre, `rio-cogeo` escribe la
  máscara interna del COG, y el tileserver pinta esos píxeles transparentes. Medido: el
  mínimo válido pasó de 0 a 0,21 y el COG valida.

**El tope de `getDownloadURL` es un error definitivo.** GEE contesta `Total request size
(620163765 bytes) must be less than or equal to 50331648 bytes.` (medido con un cuadrado de
unos 100 km). La frase entró en `_DEFINITIVOS` de `ejecucion.py`: antes se reintentaba cuatro
veces. Un rancho de más de unas 120.000 ha no entra en una descarga y su alta falla con un
mensaje claro; partirlo en teselas queda para cuando aparezca uno (`ARQUITECTURA` §10).

**Un id que no es un uuid falla sin reintentos**, antes de pedirle nada a GEE: la key no se
puede armar, y reintentar no cambia el id.

**La escala del COG sale de la receta**, no de `SCALE_METROS`: la grilla (`crs`, formato) es
la de `DECISIONS #19`, y el mapa tiene que ser de los mismos píxeles que las estadísticas
(`ARQUITECTURA` §8.5). Hoy los dos valen 10 m. El índice del mapa (`INDICE_DEL_MAPA`, NDVI) no
es de la receta: no cambia un número, cambia qué se dibuja, y va en la key.

**Cómo se probó.**
- 17 tests nuevos. La descarga falsa escribe un GeoTIFF de verdad con el centinela, y
  `convert_to_cog` corre con `rio-cogeo`: el test ve la máscara del COG que se sube. Control
  negativo: sin el `nodata`, el COG sale sin píxeles enmascarados y el test da rojo. La suite:
  547 verdes.
- **Contra lo real**, con la parcela 1 como polígono del rancho: GEE de verdad, MinIO local y
  PostGIS local. **23 COG en 226 s** (2026-05, sin dato, quedó sin mapa), los 23 validan con
  `rio-cogeo` y tienen máscara sin el centinela visible; 23 filas `mensual` con receta,
  estadísticas y bbox; el job en `completed`; 27 líneas de bitácora con un aviso. Repetir un
  mes no duplica ni la fila ni el objeto. **Las medianas del rancho son las de la parcela
  sobre el mismo polígono** (0,474, 0,447, …): B-1 cerrado por construcción, como pedía el
  diseño.

**Registrado, sin hacer:**
- **Un mes de rancho cuesta de 5 a 12 s**, contra 3 a 8 s de la parcela: son dos llamadas y
  una descarga. Con un rancho grande hay que medirlo (el pendiente de M.2, "el COG del
  rancho").
- **Sin límite de concurrencia**, como el alta de la parcela (`#50`): va con M.5.3.
- **`convert_to_cog` deja su carpeta temporal** (`mkdtemp`); se borra el archivo, no la
  carpeta. Ya pasaba con la capa vieja.
