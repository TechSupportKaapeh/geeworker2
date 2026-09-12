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

## 33. Se reescribe la capa de satélite, no el servicio (2026-09-12) — propuesta, recomendada

> **Estado: propuesta y recomendada**, a confirmar al empezar M.1. El usuario
> preguntó "¿reescribimos el worker desde el inicio, o al lado del viejo?".

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
