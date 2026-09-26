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

## 52. El job se cierra aunque su corrida termine fuera del handler (2026-09-18)

> Tarea M.4.7 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md), sumada en la sesión 8 a pedido del
> usuario: canceló en Inngest las altas de M.4.6 y el panel las siguió mostrando "en proceso".

**El problema.** El wrapper (`handlers/seguimiento.py`, E.4) marca `failed` cuando **ve** el
error definitivo. Hay dos casos en que nadie lo ve, y el job quedaba en `running` (o en
`pending`) para siempre:
- **la corrida se cancela** desde el dashboard o por la API;
- **Inngest la da por fallida sin que corra el último intento del handler**: el contenedor
  muere (OOM, reinicio, deploy) o el request se corta.

Es el origen de los 7 jobs colgados desde el 2026-08-10 (HANDOFF §4).

**Decisión.** Inngest avisa las dos cosas con eventos de sistema, y el worker los escucha:
- **`inngest/function.failed`** → el `on_failure` de `process_parcela` y `process_rancho`
  (`handlers/cierre.py:cerrar_por_falla`). El SDK lo registra como una función aparte,
  `…-failure`, filtrada por `function_id`.
- **`inngest/function.cancelled`** → `on_failure` **no** lo recibe. Lo escucha una función
  propia, `cerrar-altas-canceladas` (`handlers/cancelaciones.py`), filtrada con una expresión
  sobre los ids de las dos altas, que salen de las funciones y no se escriben a mano.

Los dos leen el `JobId` del evento original (viene en `event.data.event.data`) y escriben con
**`cerrar_job_abierto`**, que hace `UPDATE … WHERE status IN ('pending', 'running')`:
- **no pisa un final que ya se escribió**: si el wrapper llegó a marcar `completed` o `failed`
  con su motivo, ese queda;
- **es idempotente**: solo el primer cierre escribe su línea `fin` en la bitácora.

El estado es `failed`, con el motivo en `error_message` y en la bitácora ("Se canceló la
corrida en Inngest" o "Inngest dio la corrida por fallida: …"). No se sumó un `cancelled`:
Geocore y el panel conocen cuatro estados, y un quinto los tocaría a los dos. El error que
trae Inngest pasa por `resumir_error`, como el del wrapper, porque lo ve el usuario del tenant.

Quedan **8 funciones** registradas.

**Cómo se probó.**
- 13 tests: los handlers de cierre con el evento de sistema, que no pisan un job cerrado ni
  filtran secretos; lo que se registra en Inngest (mirado en `get_config`, que es lo que manda
  el SDK al sincronizar); y el `UPDATE` condicional. La suite: 560 verdes.
- **Contra un Inngest real** (el dev server del compose del worker, con el worker local y
  PostGIS local):
  - una alta de rancho con un id que no es uuid falla sin reintentos. El wrapper la cierra, llega
    `inngest/function.failed`, el `on_failure` encuentra el job y **no escribe otra línea**;
  - una alta de parcela real, cancelada por la API (`DELETE /v1/runs/{id}`) en el mes 3: **a
    los 5 s el job está `failed` con "Se canceló la corrida en Inngest"**.
  - De paso, un dato para la lentitud de M.4.6: con el dev server orquestando, cada mes tardó
    unos 6 s. **Lo lento en producción no es el código ni GEE.**

**Lo que no arregla: los jobs que ya están colgados.** Sus eventos de cancelación pasaron
antes de este deploy. Los cierra el equipo en GeoData (👥), **después de confirmar en
Inngest que no hay corridas vivas**:

```sql
-- 1. Ver cuáles son
SELECT id, request_type, status, created_at
FROM geodata.processing_jobs
WHERE status IN ('pending', 'running')
ORDER BY created_at;

-- 2. Cerrarlos. Solo si en Inngest → Runs no queda ninguna corrida en Running o
--    Queued: una viva se cerraría acá y después marcaría su propio final encima.
UPDATE geodata.processing_jobs
SET status = 'failed',
    error_message = 'Cerrado a mano: la corrida se canceló o se perdió antes de M.4.7',
    finished_at = now()
WHERE status IN ('pending', 'running');
```

**Registrado, sin hacer:**
- **Una línea de falla sale dos veces** en la bitácora cuando un step levanta
  `NonRetriableError`: la escribe `paso()` en el step, y otra vez cuando el SDK reentrega el
  error. Es ruido, no un dato. Ya pasaba antes de M.4.7.
- **Un job en `pending` cuyo evento nunca llegó a Inngest** no genera ningún evento de sistema:
  esto no lo cierra. Es el hueco del outbox (Geocore `#18`); lo cubren el reconciliador (M.5.2)
  y el reprocesar (M.5.4).

## 53. El worker atiende varios steps a la vez (2026-09-19)

> Tarea M.4.8 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md), sumada al diagnosticar la lentitud de
> M.4.6. **Corrige a `#26`**, que decía que el SDK corría los handlers síncronos en un pool de
> hilos: con `inngest.fast_api` no era cierto.

**El síntoma.** En producción, las altas de M.4.6 tardaban cerca de un minuto por mes. La vista de
Inngest separaba el tiempo en dos: **"Your server" de 4,6 a 9,9 s** (el trabajo real del mes) e
**"Inngest" de 1,6 a 55 s**, variable, de espera antes de llamar al worker. Solo corrían tres
altas: un rancho y dos parcelas.

**La causa.** `inngest.fast_api.serve()` declara `/api/inngest` como `async`, y la ejecución del
SDK llama a los handlers síncronos **directo, dentro del event loop**
(`_internal/execution_lib/v0.py`: `output = handler(...)`, sin hilo). Mientras un step esperaba a
GEE, el proceso entero quedaba congelado: los steps de las otras corridas, y hasta `/health`,
esperaban su turno. **El worker atendía de a un step por vez**, y la espera de cada uno dependía de
cuántos tuviera delante. Con una sola alta no se notaba, y por eso nadie lo vio hasta M.4.6.

**Decisión.** `services/inngest_serve.py` reemplaza a `inngest.fast_api.serve`. Monta la misma ruta
con los mismos métodos, pero usa la **variante síncrona del mismo manejador del SDK**
(`post_sync`, `get_sync`, `put_sync`) y la manda al pool de hilos de Starlette
(`run_in_threadpool`). La ruta sigue siendo `async` solo para leer el cuerpo; el step corre en un
hilo. Todo lo que ya garantizaba el SDK (la firma, el registro, las cabeceras) sigue siendo suyo.

**Lo que dejó de ser seguro al pasar a hilos, y se arregló en la misma tarea:**
- **El pool de conexiones era `SimpleConnectionPool`**, que psycopg2 documenta como **no seguro
  entre hilos**. Pasó a `ThreadedConnectionPool`, creado bajo candado: sin el candado, dos hilos
  que llegan juntos crean dos pools.
- **El plazo de GEE** (`ejecucion.plazo()`) es estado del cliente, no del pedido. Con hilos, el que
  salía primero le sacaba el plazo al que seguía, y `setDeadline` **reconstruye el cliente HTTP**
  mientras otro hilo tiene un pedido en vuelo. Ahora `_PlazoCompartido` cuenta los hilos: el
  primero lo pone y el último lo restaura.
- Ya eran seguros: el cliente de MinIO (comparte el pool de urllib3) y los `ContextVar` de la
  bitácora y del conteo, que son uno por contexto.

**Un bug que agarró la prueba, y que la primera versión del test tapaba.** El `framework` que el
SDK pone en sus cabeceras tiene que ser su enum (`server_lib.Framework.FAST_API`), no el texto
`"fast_api"`: con un `str`, armar la respuesta tira `AttributeError` y **cada pedido sale 500**. La
primera versión de `test_inngest_serve.py` reemplazaba el endpoint por uno propio, así que medía
el reemplazo y no el `serve`: pasaba igual. Se rehizo para **invocar la ruta de verdad**, con el
cuerpo que manda el executor de Inngest, y ahí apareció el 500.

**Cómo se probó.**
- 13 tests: dos invocaciones reales se atienden en paralelo, cada una en su hilo, con el `serve`
  del SDK como **control negativo** (el mismo test, en serie); `/health` contesta mientras corre
  un step; las cabeceras son las del SDK; el plazo compartido con muchos hilos; un solo pool
  aunque lleguen muchos a la vez. El test del 401 sin firma (W-8) pasó a montar esta ruta: la
  garantía ahora es nuestra. La suite: 573 verdes.
- **Contra un Inngest real** (el dev server, con PostGIS local y GEE de verdad), **tres altas de
  parcela a la vez**, de 6 meses cada una:

  | | Total | Cada alta |
  |---|---|---|
  | Sin el arreglo (`main`, como en producción) | **66 s** | 64–65 s |
  | Con el arreglo | **23 s** | 21–22 s |

  Tres veces más rápido con tres corridas: lo que da un worker que atendía de a un step. Las 72
  filas (3 × 6 × 4) quedaron escritas en los dos casos. El registro (`put_sync`) también se probó
  ahí.

**Lo que esto no resuelve.** El techo ahora es cuántos pedidos concurrentes acepta GEE y el pool
de hilos de Starlette (40 por defecto). Con muchas altas a la vez (un KML de cientos de parcelas),
GEE va a empezar a contestar "too many concurrent aggregations": es un error pasajero, y se
reintenta. El límite que lo ordena es el de concurrencia de Inngest, que va con **M.5.3** y ahora
tiene que tener en cuenta que el worker sí atiende en paralelo.

## 54. El arranque avisa si hay URLs de Inngest en producción (2026-09-19)

> Tarea M.4.9 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md), sumada después del sync roto del
> 2026-09-18.

**Qué pasó.** Al arreglar la event key de Geocore, quedó `INNGEST_BASE_URL=https://inn.gs` en el
**worker**. El sync de la app empezó a fallar: `POST https://inn.gs/fn/register` → 404,
`registration_failed`. `inn.gs` es el host de eventos de Inngest; el registro va a
`api.inngest.com`.

**Por qué nadie lo vio venir.** El worker no le pasa URLs al SDK en producción (PLAN F.3), y el
reporte de arranque decía de `INNGEST_BASE_URL`: "en producción no se usa". **Era falso.** Sin el
parámetro, el SDK **lee el entorno por su cuenta** (`client_lib/utils.py`): `INNGEST_API_BASE_URL`,
después `INNGEST_BASE_URL`, después `INNGEST_DEV`, y solo si no hay ninguna usa Cloud. El aviso
que tenía que alertar dejaba tranquilo a quien lo leía.

**Decisión.** `Variable` suma `prohibida_en_produccion`: en producción, una variable así
**definida** es un problema, sea cual sea su valor, y sale en "HAY N COSAS QUE VAN A FALLAR" con
"Borrarla". Las cuatro que lee el SDK: `INNGEST_BASE_URL`, `INNGEST_API_BASE_URL`,
`INNGEST_EVENT_API_BASE_URL` e `INNGEST_DEV`. En desarrollo siguen siendo lo normal. El comentario
de `services/inngest_client.py` quedó corregido.

**Cómo se probó.** 7 tests: cada una de las cuatro, en producción, es un problema con su
consecuencia; sin ellas no hay problema; en desarrollo, la URL del dev server está bien; y ninguna
consecuencia de `INNGEST_*` vuelve a decir "no se usa". Control negativo: con el `arranque.py`
anterior, 5 en rojo. La suite: 580 verdes.

## 55. Una función para medir la espera de Inngest entre steps (2026-09-19)

> Tarea M.4.10 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). Sigue a `#53`.

**Qué queda sin explicar después de M.4.8.** Con el worker atendiendo en paralelo, un alta sola
en producción sigue esperando **de 40 a 70 s en la cola de Inngest** antes de cada mes, que el
worker después resuelve en 5 a 10 s. Contra el Inngest local, el mismo código no espera. Ya se
descartó:
- **el worker**: con una sola alta no hay nada compitiendo;
- **el límite del plan Hobby** (5 steps a la vez en la cuenta): una alta usa uno;
- **una pérdida de datos por ir más rápido.** La parcela 1, corrida el 2026-09-18 y otra vez hoy
  con el código de hoy, dio **las mismas 96 filas y las mismas 672 estadísticas, con diferencia
  máxima 0**. Nada de M.4.8 ni de M.4.9 toca lo que se calcula, y la huella de la receta lo fija.

Queda por saber si la espera es de **la plataforma** o si la provoca **algo de nuestros steps**:
lo que tardan, lo que devuelven, o el modo en que Cloud los despacha (en una captura, la
*Discovery* tardó 34 ms y la ejecución llegó 52 s después).

**Decisión.** `handlers/diagnostico.py:diagnostico_latencia`: una función con N steps que **no
hacen nada** (ni GEE, ni base, ni bitácora). Cada uno devuelve la hora a la que el worker lo
ejecutó, y la función devuelve los huecos entre steps. Se dispara a mano: *Send event*
`terra/diagnostico.latencia`, con `{"steps": N}` opcional, entre 1 y 10 para no gastar la cuota
(cada step es una ejecución del plan). No toca ningún dato ni ningún job.

**Cómo leerla.**
- **Línea de base, contra el Inngest local: huecos de 0,12 a 0,20 s**, y 0,62 s los 5 steps.
- Si en producción los huecos entre steps vacíos son de decenas de segundos, la espera es de
  Inngest Cloud, y esta corrida es el caso mínimo para su soporte.
- Si son de milisegundos, la espera la provoca algo de los steps reales, y se sigue por ahí.

**Cómo se probó.** 12 tests: los huecos, el tope de steps, que no toca GEE ni la base, y el
registro. Y la función corrida de punta a punta contra el Inngest local. La suite: 591 verdes.
Quedan 9 funciones registradas.

**Resultado en producción (2026-09-19): la espera es de Inngest Cloud.** Tres corridas de
`diagnostico-latencia` con 5 steps vacíos:

| Corrida | Antes del primer step | Huecos entre steps vacíos | Finalization |
|---|---|---|---|
| 1 | 75 s | 2,5 · 2,0 · 2,1 · 0,25 s | 16,8 s |
| 2 | 2,1 s | 37,6 · 2,1 · 0,3 · 1,8 s | 0,25 s |
| 3 | 2,3 s | 48,5 · 2,2 · 2,3 · 43,7 s | 2,3 s |

Steps que no hacen nada tienen **esperas al azar de 38 a 75 s**, con el mismo patrón que las
altas: la mayoría sale en ~2 s (localmente, 0,12 a 0,20) y algunos esperan casi un minuto. Queda
descartada la hipótesis de que la espera dependiera de cuánto dura el step. **No hay nada que
arreglar en el worker ni en el pipeline**: la demora la agrega la plataforma al despachar.

**Qué se hace:**
1. 👥 escribirle al soporte de Inngest con los *run id* de estas corridas: ¿es el comportamiento
   del plan Hobby, o un problema de la cuenta o de la región?
2. Seguir con M.4.6 y M.5: la espera no afecta la corrección, y el cierre de mes es un step por
   entidad.
3. Si el soporte no lo resuelve, un piloto de Inngest autohosteado en Railway: el dev server local
   no tuvo esperas. Va con su propia decisión, porque suma un servicio que operar.

---

## 56. El cierre de mes reusa el mes del alta, y las cuatro funciones de GEE comparten una cola de 5 (2026-09-19)

> Tarea M.5.3 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). Del otro lado están `DECISIONS #30` y
> `#31` de Geocore, que publican los eventos.

**`handlers/mes.py`** registra dos funciones: `process-parcela-mes`
(`terra/parcela.mes.requested`) y `process-rancho-mes` (`terra/rancho.mes.requested`). Son 11
funciones registradas.

**Cada una es un solo step, y reusa el mes del alta.** `handlers.parcela.procesar_mes` y
`handlers.rancho.procesar_mes` dejaron de ser privadas y se llaman desde acá tal cual. No es sólo
ahorrar código: **si el cierre de mes calculara distinto que el alta, el mes 25 de una parcela no
sería comparable con los 24 que trajo su alta**, y no habría forma de notarlo mirando los
números. El único cambio es que `posicion` y `total` valen 1, así que la barra va del 0 al 99 de
un salto.

**El mes lo manda el evento, en `periodo`; acá no se lee el reloj.** El reconciliador ya decidió
qué mes cierra, y con el día 5 de por medio no siempre es el anterior a "hoy" (`#30` de Geocore).
Un `periodo` que no es `AAAA-MM` —o que falta— es `NonRetriableError`: reintentarlo da el mismo
error. Un test cambia `hoy_utc` por algo que tira, para que el día que alguien meta un reloj acá,
se entere.

**Las cuatro funciones que le piden a GEE comparten una cola de concurrencia de 5**: las dos
altas y los dos meses, con `scope="account"` y `key="'gee'"` (una expresión, de ahí las comillas
adentro). Sin la key el límite sería de 5 **por función**, o sea 20. El número es el techo del
plan Hobby (`#55`), y el límite se declara igual para que el cierre de mes no se coma la cuota
con la que un alta tiene que terminar, y para que el día que el plan cambie, este número siga
siendo el que manda sobre GEE.

**Cómo se probó.** 17 tests nuevos sin GEE ni base (el mes del evento, un solo step, las filas y
el mapa, el mes sin cobertura, los periodos inválidos, los campos que faltan, la cola compartida
y el registro), y la suite pasó de 591 a 608.

**Y contra un Inngest de verdad** (el dev server local), que es donde se ven las dos cosas que un
test no prueba:

- **la cola es una sola**: sincronizadas las 11 funciones, las cuatro de GEE quedaron con el
  mismo `hash` de concurrencia (`38zepdro2fz7m`) en la config que devuelve el server. Si la key
  faltara, cada una tendría el suyo;
- **la deduplicación por id de evento existe**: `terra/parcela.mes.requested` con
  `id = job-mes-verificacion-1` corrió y escribió sus 4 filas de 2026-06 (NDVI 0,52, cobertura
  0,71, contra una parcela real y GEE real, en una base local con las migraciones). **El mismo
  evento reenviado con el mismo id no disparó ninguna corrida** (`/v1/events/{id}/runs` devuelve
  `[]`). Es el supuesto sobre el que se apoya la republicación de un `pending` de Geocore
  (`#30` y `#31` de Geocore), y ahora está verificado y no supuesto.

**Lo que queda abierto:**
- **La deduplicación se verificó en el dev server, no en Inngest Cloud.** Va con M.5.5, que es
  donde el cierre corre de verdad.
- **Hasta que el equipo prenda `CierreMensual__Habilitado` en Geocore** (M.5.5), estas dos
  funciones están registradas y nunca reciben un evento.

---

## 57. La grilla de salida de GEE es un error definitivo, no uno para reintentar (2026-09-20)

> Salió del primer cierre de mes real en producción, unas horas después de M.5.3.

**El caso.** El cierre de mes de un rancho falló con
`Pixel grid dimensions (13x111332) must be less than or equal to 32768`. A 10 m, eso es una
geometría de **130 m de ancho por 1113 km de largo**: un polígono mal cargado, no un rancho
grande. Como la familia no estaba en `_DEFINITIVOS`, `es_reintentable` la trató como pasajera
—que es el default deliberado— e Inngest la reintentó: **el primer intento gastó 144 s de GEE**
y los siguientes dieron exactamente el mismo error.

**El cambio:** `"pixel grid dimensions"` entra en `_DEFINITIVOS`, así que el job queda `failed`
al primer intento y a la vista en Procesos. El test que recorre la lista lo cubre solo, y además
se sumó **el mensaje tal como llegó de producción** al test de mensajes completos: la frase
suelta y el mensaje real son dos cosas distintas, y el que importa es el segundo.

**Por qué importa más de lo que parece:** con el cierre de mes andando, un rancho así falla
**todos los meses**, y cada mes gastaba cuatro intentos. El default de tratar lo desconocido
como reintentable sigue siendo el correcto —equivocarse hacia el reintento cuesta una llamada y
equivocarse hacia el descarte pierde el mes—, pero cada familia que aparece hay que nombrarla.

**Lo que queda del lado de los datos:** ese rancho tiene una geometría degenerada y ningún
cálculo suyo va a servir mientras siga así. Es de quien carga los datos, no del pipeline. Sigue
abierto, de M.4.5, qué hacer con un rancho legítimamente enorme, que es otro problema: ese no
entra en **una** descarga y pide partirla.

---

## 58. El mapa del rancho es de los cuatro índices, uno por COG (2026-09-20)

> Decisión del usuario, después de ver el primer cierre de mes en producción. Hasta acá el mapa
> era sólo de NDVI, que fue con lo que se probó el pipeline (`ARQUITECTURA` §10).

**Cada mes del rancho sube un COG por índice de la receta** —NDVI, EVI, NDRE y NDMI en la v1—,
cada uno con su fila en `layers`.

**No rompe el congelamiento de `s2-mensual-v1`.** Los cuatro índices ya se calculaban: son los
mismos números, la misma máscara y el mismo compuesto. Lo único que cambia es **qué se descarga**.
`INDICE_DEL_MAPA` era una constante del handler, no un campo de la receta, y por eso la huella no
se toca.

**Un COG por índice y no uno multibanda** (decisión del usuario). La key ya tenía el lugar desde
M.4.1 (`tenants/{t}/ranchos/{r}/{receta}/{indice}/{AAAA-MM}.tif`) y la `natural_key` también, así
que son cuatro objetos y cuatro filas sin pisarse, el selector de índice del panel mapea uno a
uno, y TiTiler sirve un COG de una banda sin parámetros extra. Un multibanda ahorraría descargas,
pero cambiaría la forma de la key y de `layers`, obligaría al panel a pedir la banda con `bidx=`
y habría que reprocesar lo que ya está en producción.

**Cada capa lleva las estadísticas de SU índice**, no las del NDVI: si todas copiaran las mismas,
la ficha del mapa de NDMI mentiría. Hay un test que lo cuida.

**Un step por mes, con las cuatro descargas adentro.** No se parte en un step por índice: con la
espera de Inngest Cloud entre steps (38 a 75 s, `#55`), 96 steps por alta serían más de una hora
de espera pura. Un fallo a mitad deja subidos los anteriores, y el reintento los vuelve a pisar
con la misma key.

**El costo, que es lo que hay que mirar:** de 1 a 4 descargas por mes. El alta de un rancho pasa
de ~24 a ~96 descargas y de ~4 a ~15 minutos; el cierre mensual, de 1 a 4 por rancho. El
almacenamiento es menor (0,62 MB por COG en el rancho de prueba). **Las llamadas a GEE por mes
pasan de 2 a 5**, y hay un test que lo afirma: ese número conviene verlo en la suite y no en la
factura.

**Los colores los decide el panel** (decisión del usuario): cada índice con su `rescale` y su
paleta, en una tabla de `src/lib/`. El tileserver no fija ninguno —la plantilla de tiles sale sin
`rescale` ni `colormap`—, y los rangos útiles son distintos: NDMI suele ser negativo y EVI está
acotado a [-1, 1] desde `#45`. Se escribe cuando M.7.4 construya el mapa; antes sería código sin
uso.

**Cómo se probó.** La suite pasó de 610 a 612, y se reescribieron los seis tests que afirmaban un
mapa por mes: ahora afirman cuatro, con sus keys, sus filas, las estadísticas de cada índice y el
conteo de llamadas. Los dos nuevos fijan la decisión: que los índices del mapa salen de
`RECETA_VIGENTE.indices` —sumar un índice a la receta suma su mapa, no lo deja afuera en
silencio— y que cada capa trae los números de su propio índice.

**Lo que queda abierto:**
- **Lo que ya está en producción sigue teniendo sólo NDVI.** Los meses viejos no se rehacen
  solos: hay que reprocesar el rancho (`#32` de Geocore) para que aparezcan los otros tres.

---

## 59. M.6.1: se borra lo que no tiene llamador, y `sentinel2_dates` deja de crearse (2026-09-20)

> Sprint M.6, primera tarea. `ARQUITECTURA_PIPELINE` §9 lista lo que el pipeline mensual
> reemplaza; esta decisión fija **qué parte de esa lista se puede borrar hoy** y por qué el
> resto no.

**El criterio: se borra lo que hoy no tiene ningún llamador.** §9 se escribió en M.0, antes de
que existieran las altas mensuales, y da por borradas varias funciones que **siguen vivas**
—`get_sentinel2_collection`, `get_sentinel2_time_series`, `compute_sentinel2_index`,
`apply_scsc`, `check_roi_coverage`, `una_por_dia`, `get_sentinel2_dates`— porque las sostienen
los cinco handlers a demanda. Ésos son M.6.2, que espera la confirmación 👥 de si el front de
los tenants los usa. Borrarlas ahora sería cambiar en silencio lo que devuelven endpoints
documentados en `api-frontend.html`.

Lo que se borró:

| Qué | Por qué |
|---|---|
| `ee_client.composite_embedding` | Componía `GOOGLE/SATELLITE_EMBEDDING`. Nada en el worker usa embeddings |
| `ee_client.maskS2clouds` | La máscara por SCL, "el respaldo" de s2cloudless. Nunca se llamó |
| La re-exportación de `compute_sentinel2_index` en `ee_client` | Existía sólo para `services/ee/__init__.py`, y creaba un ciclo de imports con `ee_indices` que sólo se sostenía porque el import de vuelta está dentro de la función |
| Los cinco nombres de `services/ee/__init__.py` | Ningún módulo hace `from services.ee import …`; todos importan del módulo concreto |
| `config.SUPPORTED_INDICES` | 20 nombres que nadie consultaba |
| `db_repository.insert_sentinel2_date` | Escribía en una tabla sin lectores |
| `db_repository.init_db` | Su única función real era crear esa tabla |
| La escritura de fechas en `query_available_dates` | Ídem; el llamador siempre las leyó del resultado del job |
| `sentinel2_dates` en las verificaciones de conexión | `utils_pkg/conexiones.py` y `scripts/check_db.py` |

**`SUPPORTED_INDICES` era peor que no tenerla.** Se lee como un contrato de validación y no lo
era: ninguna función la consultaba, así que un índice de afuera de la lista se procesaba igual
—`add_index_band_fast` cae a NDVI para lo que no reconoce— y uno de adentro podía no estar
implementado. La fuente única es el registro de `pipeline/indices.py`, que sí falla con lo que
no conoce.

**`init_db` se borra entero, no sólo el `CREATE TABLE`.** Lo que quedaba era un `SELECT 1` de
precalentamiento, y ya estaba reemplazado: `registrar_conexiones()` corre inmediatamente
después en el arranque, abre una conexión del mismo pool, consulta y **reporta**. `init_db`
atrapaba su propia excepción y la logueaba, así que el `try` de `_startup()` nunca la veía y un
arranque contra una base caída terminaba sin quejarse — el comentario de `app._startup()` ya lo
decía.

**Dejar de crear la tabla es lo que hace seguro el `DROP TABLE` de 👥 M.6.1b.** Mientras el
worker la creara al arrancar, el primer deploy después del `DROP` la traía de vuelta. Por eso el
test no se conforma con que falten las dos funciones: recorre el repo con `ast` y **falla si
cualquier módulo nombra la tabla en código**. Distingue código de comentario a propósito —el
comentario que explica el borrado es lo que se quiere conservar—, y para identificadores compara
exacto, porque `get_sentinel2_dates` contiene la cadena y no toca la tabla: consulta GEE.

**El mensaje de las conexiones dejó de mentir.** Decía «Las crea EF Core desde Geocore» de las
tres tablas, y `sentinel2_dates` la creaba el worker: cuando faltaba justo ésa, el reporte
mandaba a buscar el problema al repo equivocado. Ahora las dos que quedan sí son de EF Core.

**Cómo se probó.** La suite pasó de 612 a 618. Y contra Postgres de verdad (el contenedor
`terra-geodata`), que es lo que pide el `WORKFLOW`:
- `verificar_geodata` contra la base `geodata`: `OK … con las 2 tablas`;
- el worker arrancado contra una base **vacía y descartable**: `/health` 200, el reporte nombra
  `layers, measurements` como faltantes, y `to_regclass('sentinel2_dates')` sigue en `NO EXISTE`
  después del arranque. Es la verificación de que la tabla no vuelve;
- control negativo del test de `ast`: con un `INSERT INTO sentinel2_dates` agregado a mano en
  `db_repository`, sale rojo nombrando el archivo.

**Las líneas.** El código de producción baja 61 líneas; los tests suben 152, casi todas del
archivo nuevo. **El total del repo sube 91**, así que el criterio de aceptación de M.6.1
—"líneas netas negativas"— se cumple en el código y no en el repo. El borrado grande es M.6.2:
son `ee_service.py`, `export_service.py`, `ee_indices.py`, el constructor de colecciones de
`ee_client.py` e `index_band_and_vis`, más de mil líneas que hoy sostienen cinco handlers.

**Lo que queda abierto:**
- 👥 **M.6.1b**, el `DROP TABLE` en GeoData. Ya es seguro hacerlo:

  ```sql
  DROP TABLE IF EXISTS geodata.sentinel2_dates;
  ```

  **El nombre va calificado con el esquema, y no es un detalle de estilo.** `init_db` la creaba
  sin calificar, o sea en el esquema que dijera el `search_path` de esa conexión, y en la base
  local terminó en `geodata` — pero el `search_path` por defecto de ese servidor es
  `"$user", public, topology, tiger`, que **no incluye `geodata`**. Un `DROP TABLE IF EXISTS
  sentinel2_dates` sin calificar no encuentra nada y sale con éxito sin borrar nada. Antes de
  darlo por hecho conviene confirmar en qué esquema quedó en producción:

  ```sql
  SELECT n.nspname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE c.relname = 'sentinel2_dates';
  ```

  Verificado contra la base local, donde la tabla existe: **0 claves foráneas entrantes, 0
  vistas dependientes y 0 filas**. Ningún lector en ninguno de los cuatro repos.
- 👥 **M.6.2** sigue esperando si el front de los tenants usa `timeseries`, `dates`, `stats` y
  `export`.

---

## 60. M.6.2: se borran los cuatro handlers a demanda, y con ellos media capa vieja (2026-09-20)

> Decisión del usuario, con el equipo del front avisado. Del lado de Geocore es `DECISIONS #35`.
> Es la tarea que trabó el sprint M.6 durante toda la sesión 10.

**Se borran cuatro de los cinco handlers a demanda:** `compute_timeseries`,
`query_available_dates`, `export_data` y `compute_parcela_stats`.
**`generate_heatmap_on_demand` se queda**: el mapa a demanda *pasa al pipeline*
(`ARQUITECTURA §9`), y eso es M.6.2b. El worker baja de 11 funciones registradas a 7.

**Lo que se cayó con ellos**, todo por no tener otro llamador:

| Módulo | Qué se fue |
|---|---|
| `services/ee/ee_client.py` | `get_sentinel2_time_series` (con `add_index_band_fast`), `una_por_dia`, `_redondear`, `get_sentinel2_dates` |
| `services/ee_service.py` | `generate_time_series_data` |
| `services/export_service.py` | `export_time_series` |
| `repositories/db_repository.py` | `insert_measurement`, `insert_measurements` |
| `utils_pkg/io.py` | `round_sig` |
| `scripts/check_pipeline_real.py` | `_mes_viejo` y las columnas del lado a lado |

**`add_index_band_fast` era la segunda copia de las fórmulas**, y no coincidía con la primera.
EVI y SAVI usaban constantes pensadas para reflectancia 0–1 sobre bandas en miles, así que el
SAVI que devolvía era, en la práctica, `1,5 × NDVI`. La copia única vive en `pipeline/indices.py`,
donde las fórmulas son texto y hay tests que las evalúan contra valores de la literatura.

**`insert_measurement` e `insert_measurements` eran la escritura por pasada**, la que producía
filas con `receta` nula — exactamente las que el equipo borró a mano en 👥 M.3.5. Mientras
existieran, la canilla seguía abierta. Lo mensual se escribe con `upsert_mediciones_mensuales`,
que siempre pone `receta`. Con esto, **la única escritora de `min_val` y `max_val` desaparece**:
esas columnas quedan sólo para las filas viejas.

**`una_por_dia` la resuelve mejor el pipeline.** Juntaba las dos imágenes de una misma pasada en
el borde de dos teselas MGRS promediando dos medias espaciales parciales —una aproximación que
no pondera por superficie (`DECISIONS #30`)—. `pipeline/etapas/compuesto.py` mosaica por
`DATATAKE_IDENTIFIER` **en el espacio de la imagen**, antes de reducir, así que el problema no
llega a existir.

**El lado a lado de `check_pipeline_real.py` se retira, no se rompe.** Esa comparación era la
compuerta de M.2.6 y **ya se corrió**, el 2026-09-17 sobre 3 parcelas reales × 3 meses
(`DECISIONS #44` y `#45`); sus números están escritos. La capa vieja ya no existe para
compararse, y un script que no se puede correr es peor que uno que dice por qué.

**Los tests borrados no se perdieron, se mudaron.** Los cinco de `insert_measurements` y
`una_por_dia` cuidaban que el lote fuera una sola llamada a `execute_values`, que las filas
repetidas no llegaran a la base, que un lote vacío no abriera conexión y que la fecha viajara
como `datetime` con zona. **Todo eso lo cuida `test_escritura_mensual.py`** sobre el camino que
de verdad se usa — y ahí las filas repetidas son un `ValueError`, porque `filas_del_mes` no
puede producirlas. Se verificó antes de borrar, no después.

**Un casi-accidente, y el test que quedó de él.** Al revisar qué publica cada controlador de
Geocore apareció que `POST /api/processing/jobs/timeseries-on-the-fly` publica
`terra/parcela.timeseries.requested`, y que el handler borrado era su **único oyente**. No estaba
en la lista de la tarea. Dejarlo vivo habría convertido ese endpoint en una fábrica de jobs
`pending` eternos. Se borró también (Geocore `#35`), y quedó
`test_cada_evento_que_geocore_publica_tiene_oyente`: compara los disparadores registrados contra
la lista escrita de lo que Geocore publica, en las dos direcciones — que no falte un oyente y que
no sobre uno. **No hay compilador que cruce los dos repos.** Control negativo corrido: quitando
un handler de `all_functions`, el test sale rojo.

**El orden de despliegue es el inverso del de M.5.5.** Allá el worker iba primero, para que
hubiera quien escuchara. Acá se está quitando: **primero Geocore** (deja de publicar), **después
el worker**. Al revés, entre un merge y el otro quedan eventos sin oyente.

**Los números.** Código de producción: **−467 líneas** (los tests bajan 98 más). `ruff check .`
en la raíz pasa de 187 a 157 hallazgos, **sin sumar ninguno nuevo** — verificado con un diff de
conjuntos contra `main`, no comparando totales. Suite: 619 → **617** (se borraron 6 tests y
entraron 4).

**Lo que queda abierto:**
- **M.6.2b**: el mapa a demanda al pipeline. Se lleva `ee_service.py`, `export_service.py`,
  `ee_indices.py`, el constructor de colecciones de `ee_client.py` (con `apply_scsc` y el
  descarte por pasada) e `index_band_and_vis`. **Ahí también se resuelve** que los GeoTIFF de los
  on-demand siguen sin nodata y que sus keys no llevan tenant, que es lo que M.8.1 necesita.
- **M.6.3** pierde casi todo su sentido: `RequestHeatmapAsync` queda sola, y sin duplicación no
  hay nada que unificar. Se revisa después de M.6.2b.
- **M.6.4** sigue en pie y ahora es más chica: los `except Exception` que quedaban en los módulos
  borrados se fueron solos.

---

## 61. M.6.2b: el mapa a demanda pasa al pipeline, y la capa vieja desaparece (2026-09-20)

> Decisión del usuario sobre el período. Del lado de Geocore es `DECISIONS #36`.
> **Cierra el sprint M.6**: el worker ya no tiene nada de la capa anterior al pipeline mensual.

**`generate_heatmap_on_demand` se rehízo sobre el pipeline** (`handlers/mapa.py`), con el mismo
`fn_id` y el mismo evento. Es de **un índice y un mes**: el evento trae `periodo` en `AAAA-MM`.

**Qué cambia, y por qué cada cosa importaba:**

| | Antes | Ahora |
|---|---|---|
| El cálculo | la capa vieja: 60 m, descartando pasadas, EVI sin dividir por 10.000 | el mismo pipeline que el histórico: 10 m, sin descartar, fórmulas únicas |
| La key | `parcelas/{id}/{periodo}_{indice}.tif`, en la raíz del bucket | `tenants/{t}/parcelas/{p}/{receta}/{indice}/{AAAA-MM}.tif` |
| El nodata | **no lo declaraba**: una nube se pintaba como NDVI 0 | `NODATA_COG`, convertido en máscara por el COG |
| La fila de `layers` | `receta` y `estadisticas` en nulo | las dos, con cobertura y observaciones |
| Un mes sin píxel limpio | subía un ráster de ceros | no sube nada, y lo dice en la bitácora |

**La key era el motivo real de la tarea.** Mientras el mapa a demanda colgara de `parcelas/{id}/`
—fuera de `tenants/`— **A01 no se podía cerrar**: el token de mapa de M.8.1 autoriza comparando
el prefijo del objeto contra el `tenant_id`, y un objeto fuera de ese prefijo no es autorizable.
Ahora **todo lo que el worker escribe vive bajo `tenants/{t}/`**.

**El mapa a demanda vive al lado del sistemático, no en otra carpeta.** Un mapa NDVI de una
parcela y el ráster NDVI de su rancho son el mismo tipo de objeto; estaban separados por *por qué
se pidió*, no por *qué son*. Lo que los distingue es `layers.source`, que ya existía. La
`natural_key` los mantiene en filas distintas (`parcela_ondemand_…` contra `rancho_mensual_…`).

**El polígono libre se identifica por su job.** `heatmap-on-the-fly` manda `parcelaId` en el uuid
nulo: no hay entidad detrás, así que la key es
`tenants/{t}/adhoc/{jobId}/{receta}/{indice}/{AAAA-MM}.tif`. **Consecuencia que hay que tener
presente: esos objetos no se reutilizan ni se sobrescriben**, porque dos pedidos del mismo
polígono no se pueden reconocer como el mismo recorte sin adivinar. Se acumulan, y su retención
es parte de la decisión que quedó abierta en `PREGUNTAS_ABIERTAS` C-5.

**Se conserva reutilizar el objeto si ya está** (E.9). La key encodea entidad, receta, índice y
mes, así que «ya está» significa «es el mismo compuesto». La fila se escribe igual: si el objeto
estaba sin su fila, o apuntando a otro lado, hay que registrarla.

**Lo que se borró con esto:**

- `services/ee_service.py`, `services/export_service.py`, `services/ee/ee_indices.py` y
  `utils_pkg/visualization.py`, enteros;
- de `ee_client.py`, todo menos `init_ee`: `get_sentinel2_collection`, `apply_scsc`,
  `check_roi_coverage`, `mask_s2cloudless_and_shadows` y `add_cloud_probability`. El módulo pasó
  de 436 a 57 líneas y hoy es **sólo las credenciales**;
- `services/inngest_handlers.py`, que era lo último que quedaba de la capa vieja. Su lista de
  funciones se mudó a `handlers/registro.py`, que **sólo tiene la lista**.

**`apply_scsc` y `check_roi_coverage` merecen su epitafio**, porque explican por qué no se
extrañan (`ARQUITECTURA` §8). La primera decía ser SCS+C y no lo era: le faltaba `cos(pendiente)`
en el numerador y usaba un `C` fijo de 0,1 en lugar de uno por banda sacado de una regresión.
Para índices normalizados el efecto de la iluminación se cancela casi entero en el cociente, así
que la receta v1 no lleva corrección topográfica. La segunda descartaba las pasadas con menos del
50 % del ROI limpio: en un compuesto mensual esa pasada aporta los píxeles que **sí** están
limpios, y tirarla **agrega** nulos.

**`handlers/raster.py`, un módulo nuevo y chico.** El nodata y la escala de descarga son un
contrato con el tileserver, y ahora los usan dos handlers. Tenerlos en dos lados es cómo se
desincronizan: ya pasó con la grilla de descarga, duplicada entre `inngest_handlers.py` y
`export_service.py`, que cumplían `DECISIONS #19` por casualidad.

**Los tests que se movieron, y por qué no se perdió nada.** `test_inngest_handlers.py` probaba el
módulo borrado. Lo suyo se repartió: el wrapper de jobs a `test_avance_job.py` y
`test_handlers_seguimiento.py` —eran el mismo `envolver_con_estado`, así que ahora prueban
`con_seguimiento`, el único que queda—, las claves de una capa a `test_pipeline_claves.py`, y lo
del registro a `test_handlers_registro.py`. El test de colisión con la capa vieja ahora compara
contra **literales**, no contra una función: lo que no se puede pisar son las cadenas que ya están
escritas en `layers`, no lo que devuelva un módulo que se borró.

**Cómo se verificó.** Suite: 613 → **635** (22 nuevos, todos de `handlers/mapa.py`).
`ruff check .` en la raíz pasa de 157 a **92**, verificado con un diff de conjuntos contra `main`:
aparecieron 8 hallazgos nuevos —orden de imports que yo desordené, y un `pytest` sin usar— y están
corregidos. Código de producción: **−622 líneas**.

**Orden de despliegue: primero el worker, después Geocore.** Es el de M.5.5, no el de M.6.2:
acá no se quita un consumidor, se cambia un contrato. En la ventana entre los dos merges un
pedido de mapa falla **al primer intento** (`failed`, no `pending`), porque el payload viejo no
trae `periodo` y eso no se arregla reintentando.

**Lo que queda abierto:**
- **Los rásters a demanda que ya están en el bucket siguen en `parcelas/{id}/`**, sin tenant y sin
  nodata. Son de la capa vieja y sus filas siguen en `layers`. **M.8.1 tiene que decidir qué hacer
  con ellos**: moverlos, borrarlos, o dejar que el token los rechace.
- **La ventana arbitraria** sigue sin existir. Recuperarla es cambiar `Mes` por un período
  semiabierto en `pipeline/`, que es también lo que habilitaría un formato por pasada.

---

## 62. M.6.4: `except Exception` se justifica o se borra, y el invariante queda en un test (2026-09-20)

> Última tarea del sprint M.6. Lo que se hizo no es lo que la tarea suponía, y esa diferencia es
> la decisión.

**La tarea decía «excepciones explícitas en lugar de `except Exception` en lo que queda».
Quedaban 35, y angostar los 35 habría sido una regresión.**

Lo que resolvió el alcance fue correr la regla en vez de contar a ojo:

```bash
.venv/Scripts/python.exe -m ruff check . --select BLE
```

**Ruff marcaba 8 de los 35.** No marca un `except Exception` que **relanza**, y ahí está la
distinción que importa: atrapar ancho para anotar el fallo y dejar que suba es el patrón
correcto, y angostarlo sería peor —dejaría pasar sin registrar lo que no estuviera en la lista—.
Lo que ruff marca son los que **absorben**, que son los que tapan bugs. Esos 27 que no marca son
`pipeline/ejecucion.py` traduciendo errores de GEE, `avance_job.paso` y `handlers/seguimiento`
anotando y relanzando, los `rollback`-y-relanzo de las escrituras, y los 19 que ya llevaban un
`noqa` con su motivo.

**De los 8: tres se borraron y cinco se justificaron.**

`utils_pkg/cache.py` y `utils_pkg/io.py` **no tenían un solo llamador**. Guardaban mapids de GEE
y estadísticas de cálculo en `BASE_OUTPUT_DIR`; lo último que escribía ahí era
`export_service.py`, que se fue en M.6.2b. Sus tres `except Exception: pass / return None` eran
justo la clase que ruff marca. Se borraron los dos módulos enteros, y `utils_pkg/__init__.py`
quedó sin exportar nada.

Los cinco de `db_repository.py` se quedan, con `# noqa: BLE001` y el motivo escrito. **El estado
de un job es telemetría**: perder una actualización no puede abortar un procesamiento que ya
corrió. Angostarlos a `psycopg2.Error` haría que un `TypeError` serializando el `detail` tumbara
una corrida que había terminado bien — exactamente al revés de lo que la tarea busca.

**El invariante quedó en un test, no en una costumbre.** `test_no_queda_ningun_except_exception_sin_justificar`
corre `ruff --select BLE` sobre el repo entero y falla si aparece uno nuevo. Existe porque **el
CI sólo corre ruff sobre `pipeline/`** (M.0.1, y el CI no se toca por pedido del usuario): sin
esto, el criterio de aceptación dependería de que alguien se acuerde del comando. Control
negativo corrido: con un `except Exception: return None` agregado a mano, sale rojo nombrando
archivo y línea.

**Una observación que quedó anotada y no se actuó:** si nada escribe ya en `BASE_OUTPUT_DIR`, el
chequeo de arranque `verificar_outputs` está verificando una carpeta que no usa nadie. No se saca
acá porque atrapó un fallo real de producción (`Permission denied: '../outputs'`, por job y no al
arrancar) y cuesta poco; pero la variable y el chequeo son candidatos a irse juntos.

**Cómo se verificó.** Suite: **637 verdes** (dos nuevos). `ruff check . --select BLE` limpio;
`ruff check .` en la raíz baja de 92 a **77** — borrar los dos módulos se llevó más
hallazgos de los que tenían sus `except`.

**Con esto el sprint M.6 queda cerrado**, salvo M.6.3, que M.6.2 vació: de los cinco
`Request*Async` sobrevivió `RequestHeatmapAsync`, y sin duplicación no hay refactor que hacer.


---

## 63. La ventana de observación: por pasada puro, y la cobertura se mide sobre la parcela (2026-09-25)

> **Seis decisiones del usuario**, tomadas juntas sobre el bloque M.9.0. Vienen de una crítica
> al compuesto mensual y de discutirla contra el código. Diseño:
> [`ARQUITECTURA_PIPELINE.md` §3.5](ARQUITECTURA_PIPELINE.md). Backlog:
> [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md) §M.9.

**Contexto.** `#31` decidió el compuesto mensual y reemplazó a `#19`, que guardaba por pasada.
Esa decisión se tomó con la cuenta del **ráster** —146 descargas contra 24, TiTiler abriendo 3
a 6 COG por tile, el MosaicJSON con sus preguntas abiertas—, y toda esa cuenta **sigue siendo
correcta**. Pero en ese diseño las estadísticas salen de la misma imagen que el mapa, así que
**los números viajaron con la decisión del ráster sin que nadie hiciera la cuenta por
separado**. Para un número no hay descargas, ni MosaicJSON, ni TiTiler: es un `reduceRegion`.

### Lo decidido

1. **Se mide antes de decidir** (M.9.0). Sale de `scripts/check_pipeline_real.py`, y son **dos
   números**: cuántas pasadas limpias hay por mes, y **qué cobertura tiene cada pasada**. El
   segundo hoy no existe: la cobertura se calcula sobre el compuesto.
2. **El agrupamiento es un dato de la receta** (M.9.0b), y `pipeline/ejecucion.py` **crece una
   llamada declarada**, `fechas_de(coleccion)`. `#32` dice «un solo borde», no «una sola
   llamada»: una llamada con nombre se sigue mejor que una indirección que existe sólo para no
   agregarla.
3. **Por pasada puro**: todas las filas de `measurements` son pasadas. **No hay migración** —la
   clave `(parcela, índice, fecha)` sirve tal cual con la fecha de adquisición—, hay una sola
   clase de fila, y el mensual (o cualquier rango) sale de agregar al leer.
4. **Sin columna `ventana`**, que era la consecuencia de guardar las dos.
5. **El ráster sigue siendo mensual.** Es la parte de `#31` que no cambió.
6. **El umbral de cobertura se aplica al leer, no al escribir.**

### Por qué «puro», y qué se acepta al elegirlo

**Lo que un `GROUP BY` sobre las pasadas no puede rehacer** no son las medianas —que no
componen, y es lo menor—: es que **cada pasada cubre un pedazo distinto de la parcela**. Una
estadística por pasada describe el pedazo que estaba despejado; el compuesto toma para cada
píxel la mediana de las pasadas en que **ese** píxel estaba limpio, y cubre casi toda. Con
pasadas parciales, y si el pedazo despejado es siempre la misma ladera, la serie por pasada
**repite el mismo sesgo** tantas veces como pasadas haya. El valor del píxel tapado el día 7
nunca se guardó.

O sea: la fila mensual del compuesto no sería el mismo dato otra vez — **es otra medición, que
sólo existe si se calcula**. Se acepta no tenerla, y **M.9.0 es la red**: si la cobertura por
pasada viene alta, el compuesto no estaba haciendo nada que el `GROUP BY` no haga.

**En escalabilidad las dos opciones empatan donde uno esperaría que no.** Las filas pasan de 96
a ~500-770 por parcela en dos años **en las dos** —la fila mensual agrega un 12-20 % arriba— y
el 8x de reducciones de GEE también ocurre en las dos. Lo que las separa es otra cosa:
**mantener las dos clases de fila obliga a filtrar por `ventana` en toda consulta del
sistema**, y la que se olvide no falla: mezcla ocho pasadas con un compuesto y devuelve un
número plausible y equivocado. Es la misma forma del bug de M.7.1. Con «puro», una fila es una
fila.

**Y equivocarse es reversible del lado barato:** si hace falta el compuesto, se reprocesa —GEE
es la fuente de verdad, `#31` ya lo dice—. La complejidad de guardar las dos, en cambio, se
paga todos los días.

**Si algún día hicieran falta las dos, van en tablas separadas**, no en una columna: dos
semánticas en dos tablas no se pueden mezclar por olvido.

### La cobertura se mide sobre la parcela, nunca sobre el rancho

**Una pasada que tapa medio rancho puede ser perfecta para una parcela**, y evaluarla a nivel
rancho la descartaría para todas. El umbral se aplica con la granularidad con la que el dato se
consume, que es la parcela: es la fila de `measurements`.

El pipeline **ya respeta el principio en dos lugares** y hay que no perderlo: `nubes.py`
enmascara **sin descartar pasadas** —una pasada parcial aporta donde está limpia— y
`cobertura_minima` es "la fracción de **la parcela**". Lo que falta es medirla **por pasada**.

### El umbral, al leer

Hoy `cobertura_minima` **descarta al escribir**: bajo 0,3 la fila sale nula. Eso es otra
reducción con pérdida antes de guardar, y es la que no se puede deshacer. Guardando cada pasada
con su cobertura, «descartar lo que no llega al 30 %» pasa a ser un `WHERE` — y el día que 0,3
resulte mal puesto, se cambia el número y no el histórico. Es el mismo argumento que hace que
la cadencia sea de consulta.

### Consecuencias

- **`PREGUNTAS_ABIERTAS` B-3 queda contestada** en su parte de diseño; lo que falta es el
  número de M.9.0.
- **`/api/measurements` tiene que aprender a agregar** (una cadencia como parámetro). Es
  trabajo real, pero es **la funcionalidad**, no un costo del diseño.
- **Lo que hoy es `valor = null` por cobertura baja va a dejar de existir**: la pasada se
  guarda con su cobertura y el filtro es del que lee.
- El techo de `limit` empieza a importar: con los cuatro índices, ~768 filas por parcela cada
  dos años contra el `limit=2000` que pide el panel hoy.

---

## 64. El router `/mosaic` del tileserver se borra (2026-09-25)

**Contexto.** El hallazgo **T-3** del mapeo OWASP: el `path_dependency` valida la URL del
MosaicJSON, pero **no los assets que ese documento lista adentro** — `cogeo-mosaic` los abre
tal como vengan. Desde M.8.1 pesa más: lo que se saltearía ya no es sólo el filtro anti-SSRF,
es **el aislamiento entre tenants**, porque el tenant se compara contra la URL del documento y
no contra lo que lista.

**Decisión del usuario: se borra el router**, no se validan los assets.

**Por qué.** `#31` dice explícitamente que **no se usa MosaicJSON**, y se verificó el
2026-09-25: no lo usan el panel, ni `/piloto`, ni `scripts/check_prod.py`, ni el worker. Es
**superficie muerta que carga un hallazgo abierto**, que es el peor negocio posible.

Es la misma cura que **W-3** (`#23`): cerrar un hallazgo **borrando la superficie** en vez de
arreglándola, para no reimplementar un control que después hay que mantener.

**Alcance del cambio**, para cuando se haga: el `MosaicTilerFactory` y su `include_router` en
`main.py`, el aviso que lo acompaña, las rutas `/mosaic/*` de `tests/test_app_tenant.py` —que
hoy las cubre a propósito— y revisar `scripts/check_mosaic_median.py`, que verifica la
composición por mediana y puede depender del endpoint. **No toca el pipeline**: el `mosaic()`
de `pipeline/etapas/compuesto.py` es de GEE y no tiene nada que ver.

### ✅ Hecho el 2026-09-24 · terra-tileserver#4

Salió todo el alcance de arriba, y dos cosas más que sólo existían para el router:
**`titiler.mosaic` y `boto3` se fueron de `requirements.txt`** —boto3 lo pedía
`cogeo_mosaic.backends.s3.S3Backend`; GDAL nunca lo usó, llega a MinIO por sus propias
variables— y **`scripts/check_mosaic_median.py` se borró**, porque importa `cogeo_mosaic` y sin
el router no verifica nada del servicio. `AWS_ENDPOINT_URL_S3` **se deja puesta** en
`configure_gdal`: es una variable de entorno de más, y sacarla toca el único camino por el que
GDAL llega a MinIO — eso se prueba contra el deploy, no de paso en esta tarea.

**El control negativo era obligatorio acá**, y se corrió: sacar `/mosaic` de la lista `RUTAS`
deja todos los tests verdes **aunque el router siga montado**. Por eso entró
`test_el_router_mosaic_ya_no_existe`, que pide `/mosaic/info` **con token** —un 401 también
sería «no pasa» y probaría otra cosa— y espera 404. Con el router remontado a mano, sale en
rojo.

**Y se comprobó que la app arranca sin los paquetes** antes de esperar al CI: se importó `main`
con `titiler.mosaic`, `cogeo_mosaic`, `boto3` y `botocore` bloqueados en el `meta_path`.
Arranca, y monta `cog`, `health`, `piloto`, `viewer`, `static`, `docs`, `redoc` y
`openapi.json` — ninguna ruta `/mosaic`. Hacía falta porque el venv de la máquina todavía tenía
los paquetes instalados: los tests solos no probaban `requirements.txt`.

Tests del tileserver: **181 verdes** (eran 185; se van 5 casos parametrizados y entra 1).
Crónica: `tileserver/docs/SESSION_2026-09-24_borrar_el_router_mosaic.md`.

---

## 65. Retención: lo sistemático para siempre, lo a demanda 90 días (2026-09-25)

**Contexto.** M.8.5 decidió la retención de la **bitácora de jobs** (`DECISIONS #46` de
Geocore) y dejó afuera los **objetos de MinIO**, que son la parte que más ocupa. Es la ficha
`PREGUNTAS_ABIERTAS` C-5, que además mezclaba tres cosas distintas.

**Decisión del usuario:**

- **Los COG sistemáticos se guardan para siempre.** Son el producto: el histórico es lo que se
  le vende al cliente, y borrarlo sería borrar eso.
- **Los COG a demanda (`adhoc` y `ondemand`) se borran a los 90 días.** Según
  `pipeline/claves.py`, esos objetos **no se reutilizan ni se sobrescriben** —la identidad de
  un `adhoc` es el job, así que dos pedidos del mismo polígono son dos objetos—: se acumulan
  uno por clic. Es la mitad que crece sin que nadie la vuelva a mirar.
- **Las filas por pasada no llevan retención** por ahora (si entra `s2-pasada-v2`). Unas 760
  filas por parcela en dos años es un dato chico en una tabla con índices; **poner una política
  ahora sería decidir con un número que todavía no se conoce**. Se revisa cuando haya un tenant
  grande.

**Consecuencia:** `PREGUNTAS_ABIERTAS` C-5 queda partida en sus tres cosas, y **la única que
sigue abierta es cuándo se implementa el borrado de los a demanda** — la política ya está
decidida.

---

## 66. M.9.0: hay 3 pasadas limpias por mes, y la fila mensual del compuesto no hace falta (2026-09-24)

> **La compuerta de `#63`, medida.** `#63` decidió «por pasada puro» y dejó M.9.0 como red: *si
> la cobertura por pasada viene alta, el compuesto no estaba haciendo nada que el `GROUP BY` no
> haga; si viniera parcial, la fila mensual iría en una tabla aparte*. **Vino alta.** La tabla
> aparte no se abre. Ficha: [`PREGUNTAS_ABIERTAS`](PREGUNTAS_ABIERTAS.md) B-3, **cerrada**.

### Cómo se midió

`scripts/check_pipeline_real.py --pasadas`, un escalón nuevo (el 6) que **no toca el pipeline**:
compone las etapas que ya existen —`fuente.coleccion`, `nubes.enmascarar`,
`compuesto.por_pasada`, `compuesto.indices_de`— y reduce **cada pasada por separado** sobre el
ROI. La columna del compuesto sale de `reduccion.valores`, que es la que escribe producción: la
comparación sólo vale si el lado de referencia es el de verdad.

**Una sola llamada a GEE por parcela y mes**, unos 3 s. Corre solo, sin los escalones 1 a 4:
esos son la compuerta de M.2.6 sobre 3 meses y cuestan 6 llamadas por parcela y mes; correr los
cinco sobre 24 meses serían ~430 llamadas para leer una tabla.

Dos cosas que costaron un rato y conviene dejar escritas:

- **una `ee.FeatureCollection` metida en un `ee.Dictionary` vuelve vacía.** `getInfo()` la
  serializa como `{"type": "FeatureCollection", "columns": {}}`, sin un solo rasgo. Con
  `toList(size).map(...)` vuelve la lista entera. Está en un comentario del script;
- **una pasada enteramente enmascarada no trae la clave del valor**, igual que documenta
  `reduccion.leer` para el mes sin píxeles: GEE omite la salida en vez de mandarla en `None`.

**Alcance:** 3 parcelas reales de los Llanos Orientales (Colombia) × los 24 meses de
`meses_historico` (2024-09 a 2026-08) = **72 meses de parcela**, más el cuadrado del Bajío
—que no es de un cliente— como segunda geografía, 24 meses más. Receta `s2-mensual-v1`, huella
`75dbb738dd2a`.

### Los números

| | Parcelas reales (72) | Bajío (24) |
|---|---|---|
| Pasadas limpias por mes (≥ `cobertura_minima` = 0,30) | **mediana 3**, media 2,76, máximo 8 | mediana 6, media 5,58 |
| Meses con 0 · 1 · 2 · ≥3 limpias | 4,2 % · 12,5 % · 29,2 % · **54,2 %** | 0 % · 0 % · 0 % · **100 %** |
| `comp − mejor`: lo que el compuesto agrega sobre la mejor pasada sola | mediana **+0,0000**, p90 +0,148, máx +0,485 | mediana +0,009, p90 +0,049, máx +0,251 |
| Meses en que agrega más de 0,05 | 13 de 72 (**18,1 %**) | 2 de 24 (8,3 %) |
| **Meses en que el compuesto llega al umbral y ninguna pasada sola** | **0 de 72** | **0 de 24** |
| \|mediana del compuesto − mediana de las medianas por pasada\| | mediana **0,008**, p90 0,029, máx 0,089 | mediana 0,004, p90 0,013, máx 0,031 |
| Rango del índice entre las pasadas limpias de un mes | mediana **0,065**, p90 0,164, máx **0,336** | mediana 0,038, p90 0,154, máx 0,172 |
| Meses con `valor` nulo por cobertura baja | 3 de 72 — y los tres son 2026-05, con cobertura **exactamente 0** | 0 de 24 |

**La estacionalidad, en las parcelas reales** (promedio de las 3, por mes del año):

| Mes | 1 | 2 | 3 | 4 | 5 | 6 | 7 | 8 | 9 | 10 | 11 | 12 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Pasadas | 7,5 | 5,0 | 8,0 | 9,0 | 10,0 | 9,0 | 9,0 | 9,5 | 7,0 | 7,5 | 7,0 | 7,0 |
| **Limpias** | 3,5 | 3,2 | 3,5 | 2,2 | **1,0** | **1,5** | 2,7 | 2,5 | 2,0 | 2,7 | 3,0 | **5,5** |
| `comp − mejor` | 0,000 | 0,000 | 0,000 | 0,040 | 0,025 | **0,236** | 0,078 | 0,003 | 0,033 | 0,000 | 0,047 | 0,000 |

### Lo que deciden

**1. El bloque M.9.0 NO se cierra acá.** `SPRINTS_FASE_M` decía: *si casi siempre son 1 o 2, el
mensual está bien y este bloque se cierra*. **No son 1 o 2**: la mediana es 3 y más de la mitad
de los meses tienen 3 o más. En 60 de los 72 meses hay al menos dos pasadas limpias, y el
índice se mueve entre ellas una mediana de 0,065 —con un p90 de 0,164 y un máximo de 0,336—.
Eso es señal que hoy se descarta. **M.9.0b, M.9.0c y M.9.0d siguen en pie.**

El caso más claro está en la tabla: parcela_1 en 2025-02 tuvo 5 pasadas limpias con NDVI de
0,443, 0,412, 0,381, **0,107** y 0,147. La fila mensual dice **0,377**. Una caída de 0,44 a
0,11 —lo que sea que haya pasado ahí— hoy no existe en la base.

**2. «Por pasada puro» se confirma, y lo confirma un número y no un argumento.** El que cierra
la pregunta es **0 de 72**: no hay un solo mes en que el compuesto llegue al umbral y ninguna
pasada sola llegue. Guardar por pasada **no deja sin valor a ningún mes que hoy lo tenga**.

Los otros dos van en el mismo sentido:

- **el compuesto casi nunca agrega cobertura.** La mediana de `comp − mejor` es exactamente
  0,0000: en la mitad de los meses la mejor pasada sola ya cubre lo mismo que el compuesto;
- **«las medianas no componen» es cierto y es chico.** Recomponer el mes agregando las pasadas
  al leer se aparta 0,008 de NDVI en la mediana y 0,089 en el peor caso. Contra una variación
  intramensual de 0,065, el error de composición es **un orden de magnitud menor que la señal
  que se gana**. `#63` lo llamaba «el menor»; ahora tiene número.

**3. Lo que se acepta a sabiendas.** En el 18 % de los meses —casi todos de mayo a julio, el
pico de lluvias— el compuesto cubre hasta 0,485 más de la parcela que la mejor pasada sola. Ahí
la serie por pasada describe **el pedazo despejado** y no la parcela entera, que es exactamente
el sesgo que `#63` describe. No se pierde el mes, pero el número de esos meses es de menos
parcela que el de hoy. **Lo que lo hace tolerable es que la cobertura va en la fila**: quien
lea puede verlo y filtrar, que es justo lo que «el umbral al leer» habilita. Si algún día
molesta, la salida sigue siendo la tabla aparte de `#63`, y se puede abrir sin migrar nada.

**4. Un caveat sobre la muestra, que hay que decir.** Las tres parcelas son rectángulos
contiguos (~0,58 × 1,73 km cada uno) en el mismo punto: ven **las mismas pasadas**, y la
columna de pasadas es idéntica entre las tres en cada mes. Los 72 meses de parcela son 24 meses
× 3 muestras correlacionadas, **no 72 independientes**. Lo que sí varía entre ellas —y es el
número que se quería medir— es la **cobertura de cada pasada sobre su parcela**. El Bajío es la
segunda geografía y va en el mismo sentido, más limpio. Con parcelas de otra región o de otro
tamaño, el reparto de pasadas limpias puede cambiar; lo que difícilmente cambie es el **0 de
72**, que es una desigualdad y no un promedio: el compuesto no puede cubrir menos que su mejor
pasada, y para que el umbral se cruce harían falta dos pasadas cada una por debajo de 0,30 que
juntas pasen 0,30 **sin solaparse**.

### Lo que cambia en el código

Sólo `scripts/check_pipeline_real.py`: el escalón 6, los flags `--pasadas` y `--csv`, y
`_cerrar()`, que era el final de `main()` y ahora lo comparten los dos caminos. **El pipeline
no se tocó**, que es lo que corresponde a una tarea de medir. La suite queda en **637 verdes**
y 21 omitidos, igual que antes; `ruff check .` en la raíz sigue en 77, los históricos.

Una copia de `reduccion._reducir` vive en el script, a propósito: exponerlo habría cambiado un
módulo de producción para un informe, y los cinco argumentos —`bestEffort=False` sobre todo—
tienen que coincidir para que la cobertura por pasada sea comparable con la del compuesto. Está
dicho en su docstring.

### El control negativo

El escalón comprueba, mes a mes, que **el compuesto no cubra menos que su mejor pasada**. Es
una desigualdad que tiene que valer por construcción —el compuesto tiene dato donde lo tuvo
alguna pasada—, así que si saliera en rojo la tabla entera no significaría lo que dice. En los
96 meses medidos no salió ninguna vez.

---

## 67. M.9.0b: el agrupamiento es un dato de la receta, y la ventana de una pasada todavía no selecciona (2026-09-25)

> **Refactor sin cambio de comportamiento**, y el control negativo lo dice con números: las
> filas de `s2-mensual-v1` antes y después son **idénticas**. Diseño:
> [`ARQUITECTURA_PIPELINE.md` §3.5](ARQUITECTURA_PIPELINE.md). Decisión de diseño que ejecuta:
> [`#63`](#63-la-ventana-de-observación-por-pasada-puro-y-la-cobertura-se-mide-sobre-la-parcela-2026-09-25).

### Lo que se hizo

"El mes" vivía en cinco lugares —`periodos.Mes`, el `median()` de `compuesto()`, la `fecha` de
`filas.py`, el `{AAAA-MM}` de `claves.py` y el `periodo` de los jobs— y cambiar la cadencia era
tocar los cinco esperando no olvidarse de ninguno. Es la misma forma de problema que M.7.1 y
que `#44` de Geocore: **una regla repartida en instancias**.

Ahora hay una abstracción, `pipeline/ventanas.py`, que es la que el compuesto ya era sin
decirlo: **de un pedido a una lista de ventanas**. Una `Ventana` es `(etiqueta, inicio, fin)`, y
la etiqueta es la única pieza que llega a todos lados — la key del COG, la `fecha` de la fila y
el `periodo` del job salen de ahí. Con `del_mes(mes)` la etiqueta es `AAAA-MM`, así que ninguna
key y ningún `periodo` cambian.

El resto del pipeline dejó de saber qué es un mes: `fuente.coleccion`, `productos.compuesto_de`
/ `estadisticas_de` / `mapa_de`, `ejecucion.reduccion_de`, `filas.filas_de` y las tres
`claves_cog_*` reciben una ventana. El `Mes` queda donde corresponde: en el job y en la
bitácora, que **no se tocaron** (`#63`).

### Las cuatro decisiones que hubo que tomar

**1. Una llamada más al borde, no una descripción perezosa.** §3.5 dejaba las dos abiertas y
`#63` ya había elegido: `ejecucion.fechas_de(roi, pedido, receta)`. `#32` dice «un solo borde»,
no «una sola llamada», y una llamada con nombre se sigue mejor que una indirección que existe
sólo para no agregarla. **Partir quedó puro**: `Agrupamiento.partir(pedido, fechas)` es una
función de Python sobre datos, se prueba sin credenciales, y quien orquesta pregunta las fechas
sólo si `necesita_fechas`. Con `entero` no se pregunta nada, y por eso **M.9.0b no cambia
cuántas llamadas cuesta un mes** (hay un test `gee` que lo fija en 0).

**2. `mensual` y `rango_libre` eran la misma función.** El diseño listaba cuatro agrupamientos;
dos resultaron ser "una imagen con todo el pedido adentro", y lo que los distinguía era el
pedido. Quedó uno, `entero`. Que la abstracción colapse dos casos es señal de que está en el
lugar correcto.

**3. Dos campos en la receta, no uno.** `agrupamiento_estadisticas` y `agrupamiento_raster`,
porque el ráster y los números tienen costos distintos y `#31` eligió mensual con la cuenta del
ráster. Con un agrupamiento global se repetiría el error que M.9 viene a corregir.

**4. El ráster exige una sola ventana, y lo dice.** `handlers/rancho.py` lee
`agrupamiento_raster`, arma su ventana y **rechaza** cualquier receta que parta el mes en más de
una. No se generalizó a N a propósito: es el camino más caro y más frágil del worker —descarga,
COG y subida—, y un bucle cuyo N es siempre 1 sería código que nadie ejecuta hasta que alguien
cambie la receta, y que fallaría justo ahí. Vale más un error que dice qué falta hacer.

### La huella de v1 se re-fija sin subir la versión

Los dos campos nuevos cambian la huella de `s2-mensual-v1`, que **ya escribió filas en
producción** — y la regla de `#36` dice que una versión se congela con su primera fila. **Se
re-fija igual** (decisión del usuario, 2026-09-24).

El porqué: lo que esa regla protege es que no se pueda mirar un número guardado y no saber con
qué parámetros salió. Acá **ningún número se movió**: un supuesto que estaba cableado pasó a
estar escrito, con el valor que ya tenía. Pasar a `v2` habría dejado filas `v1` y `v2` con
números idénticos en la misma tabla, que es peor para esa misma pregunta. La huella no se
guarda en ninguna fila, key ni respuesta de la API: vive sólo en el test que la fija.

Hay precedente parcial —el 2026-09-16 se sumaron `nubes_erosion_px` y `acotar_indices` igual—,
pero entonces v1 no había escrito nada. La diferencia está anotada en `HUELLAS`.

### El control negativo, que es la aceptación de la tarea

No alcanzaba con que los tests quedaran verdes: los tests los estaba tocando yo. Se comparó
**lo que produce el código de `main` contra lo que produce la rama**, con un `git worktree` de
`main` al lado y un volcador que se adapta a las dos API:

- **puro, sin GEE:** los 24 meses de la receta × 4 coberturas que cruzan el umbral en los dos
  sentidos, más las tres familias de claves de capa y el intervalo que se le pide a GEE. 120
  entradas;
- **contra GEE:** 3 parcelas reales × 3 meses, incluido 2026-05, que es el mes en que las 10
  escenas quedan tapadas por la máscara y la cobertura da 0.

**Las 129 entradas dieron idénticas**, campo por campo. Y en los tests, lo único que cambió
fueron las llamadas: **ninguna expectativa se tocó**, salvo la huella.

### El hallazgo: la ventana de una pasada todavía no selecciona sus escenas

`por_pasada` parte bien —una ventana por pasada, con su instante—, pero **esa ventana no sirve
todavía para seleccionar las escenas de esa pasada**, y por eso ninguna receta la usa. Lo
encontró correr el camino entero contra GEE, que es la regla del `WORKFLOW`: *probar contra lo
real, no contra lo que uno cree*.

`S2_SR` y `S2_CLOUD_PROBABILITY` comparten el `system:index` —que es por donde las une
`fuente.coleccion`— pero **no el `system:time_start`**. El de SR va después, y por minutos.
Medido el 2026-09-25:

| ROI | Escenas | Desfase SR − nubes |
|---|---|---|
| Una parcela real de los Llanos | 105, de 12 meses | **129 a 260 s** |
| El cuadrado de prueba del Bajío | las de 2026-07 | **668 a 1169 s** (casi 20 min) |

**Depende de dónde caiga el ROI en la pasada**, así que no hay un margen chico que sirva para
todos — que era la salida fácil, y queda descartada con número. Con una ventana de un segundo
alrededor del instante de SR, la imagen de nubes queda fuera del `filterDate`, el join no
encuentra par, la colección sale vacía y el compuesto no tiene bandas.

**Lo que M.9.0c tiene que hacer:** filtrar la colección de nubes por un **superconjunto** del
pedido y dejar que el join por `system:index` —que es exacto— haga el resto. El filtro de fecha
sobre las nubes es una optimización, no un criterio.

**Por qué no entró acá:** eso **cambia el borde del mes**. Una escena de los primeros minutos de
un mes tiene su imagen de nubes en el mes anterior y hoy **se descarta**; con el filtro ancho
pasaría a contarse. Es correcto, y es un cambio de números — justo lo que M.9.0b no puede hacer.
Va con la receta que lo necesite.

De paso quedó medido algo que el pipeline ya suponía sin decirlo: **2 de 105 escenas no tienen
imagen de nubes**, y `fuente.coleccion` las descarta a propósito desde M.2.1.

El hallazgo está fijado en un test `gee`
—`test_las_dos_colecciones_fechan_la_misma_escena_con_minutos_de_diferencia`— y repetido en el
docstring de `_por_pasada`, que es donde va a mirar quien haga M.9.0c.

### Lo que no cambió, y conviene tener presente

- **Los jobs y el cierre de mes**: un job sigue diciendo "procesá agosto de esta parcela". Lo
  único que cambiaría con otra receta es cuántas filas escribe;
- **`FilaMensual` sigue llamándose así.** Renombrarla tocaría el repositorio y sus tests sin que
  ninguna fila cambiara. Se renombra cuando cambie el dato, en M.9.0c;
- **el mapa a demanda es de un mes** y usa `del_mes` directo, no `agrupamiento_raster`: que la
  receta parta el mes para el ráster sistemático no cambia lo que alguien pide a mano;
- **`pipeline/periodos.py` no se tocó.** `Mes` sigue siendo la unidad del pedido, y está bien
  que lo sea: lo que se sacó es el supuesto de que la unidad del pedido y la de la observación
  son la misma.

---

## 68. `observaciones` se redondea, y el `.env` del worker apunta a producción (2026-09-25)

> Dos cosas chicas que salieron de una pregunta del usuario mirando la tabla `measurements`
> en Supabase: «hay columnas con null y otra que es un json». La forma de la tabla **está
> bien** —esas columnas son nullable a propósito y `estadisticas` es `jsonb` con un CHECK que
> exige que sea un objeto—, pero mirándola aparecieron estas dos.

### 1. El ruido de float de `observaciones`

**El síntoma:** en la columna `observaciones` de `measurements` hay `2.9999999999999947` donde
el número es 3. Estaba anotado como pendiente desde antes y nunca se había arreglado.

**De dónde sale:** GEE devuelve `n_obs` en float32 y el cliente lo pasa a float64. Los
decimales que aparecen no son del dato: los inventa la conversión.

**El arreglo:** `reduccion.leer` redondea `observaciones` a tres decimales, y **sólo
`observaciones`**.

**Por qué es exacto y no una aproximación**, que es lo que justifica hacerlo: `n_obs` cuenta
pasadas limpias por píxel, así que su **mediana** sobre la parcela sólo puede ser un entero o
un entero y medio. Con tres decimales no se pierde ningún valor legítimo. Hay un test que fija
las dos mitades: que `2.9999999999999947` da 3, y que **2,5 sobrevive** — redondear a entero,
que es lo primero que uno haría, se llevaría puesto un valor real.

**Por qué no se redondean las estadísticas de los índices:** tienen decimales de verdad. El
mismo ruido está en ellas, pero ahí no hay forma de distinguirlo del dato, y un NDVI con tres
decimales sería perder precisión de verdad.

**Va en `reduccion.leer` y no en `filas.py`** porque el número se guarda en dos lados —la
columna `observaciones` de `measurements` y el `estadisticas` de `layers`, que arma
`handlers/rancho.py`— y ahí arriba se arreglan los dos de una.

**Lo ya guardado no cambia.** Las filas que están en la base siguen con su ruido hasta que se
reprocesen; el arreglo es para lo que se escriba de acá en adelante.

### 2. El `.env` del worker apunta a la base de producción

**Lo que decía `PROXIMA_SESION`:** *«Las credenciales de base de ese `.env` son locales, no las
de GeoData»*. **Es falso.** `DB_HOST` es `aws-0-us-east-1.pooler.supabase.com`, que es donde
vive GeoData.

Hoy lo contiene que la contraseña está vencida: un script local falla con
`password authentication failed` en vez de escribir. **Eso es un accidente, no un control.** El
día que alguien actualice esa contraseña —para correr `check_schema.py`, por ejemplo— un script
local pasa a escribir en producción sin avisar.

Es **exactamente la misma trampa** que la de `MINIO_*`, que en la sesión 9 dejó un COG de
prueba en el bucket de producción, y que ya está documentada al lado. La diferencia es que de
aquella hay un aviso escrito y de ésta había un aviso **que decía lo contrario**.

**El arreglo es de documentación**, porque el `.env` es del usuario y no está en git: el
párrafo ahora dice lo que hay, y cómo correr contra una base de prueba —pasando `DB_*` por el
entorno, porque `load_dotenv()` no pisa lo que ya está—.

### Lo que no se tocó, y por qué

**La forma de la tabla está bien y no hace falta cambiar nada.** Lo que llama la atención al
mirarla es de diseño:

| Columna | Cuándo es NULL | Por qué |
|---|---|---|
| `valor` | cobertura bajo el mínimo de la receta | La fila se escribe igual: el panel dibuja el hueco y el cierre sabe que el mes ya se procesó (`ARQUITECTURA` §6). **Es lo que `s2-pasada-v2` saca**, con el umbral al leer (`#63`) |
| `min_val`, `max_val` | siempre, en las filas mensuales | Son de la capa vieja por pasada; el upsert las anula a propósito para que una fila vieja del día 1 no las deje colgadas |
| `observaciones` | sin un píxel limpio | Mismo caso que `valor` |
| `estadisticas` | nunca | Es el `jsonb`, con un CHECK que exige que sea un objeto |

**Lo que falta comprobar, y no se pudo desde acá:** que cada `valor` NULL tenga de verdad
cobertura baja. Eso pide consultar la base, y la contraseña del `.env` está vencida. Quedó
escrito en `geocore/docs/sql/2026-09-25_revisar_measurements.sql`, siete consultas de sólo lectura con
qué se espera de cada una; la tercera es la que decide.

---

## 69. M.9.0c (worker): `s2-pasada-v2` existe, no es la vigente, y el filtro de nubes se arregló (2026-09-25)

> La mitad del worker de M.9.0c. **No cambia nada en producción**: v2 está escrita, probada y
> verificada contra GEE, pero las altas y el cierre siguen escribiendo con `s2-mensual-v1`.
> La mitad de Geocore —que `/api/measurements` sepa agregar— va aparte.

### Por qué v2 no es la vigente

**Decisión del usuario, 2026-09-25**, y es la regla de despliegue de M.8.1 con el que **lee** en
el lugar del que exige: `/api/measurements` tiene que saber agregar y el panel dibujarlo
**antes** de que existan filas por pasada. Si el worker empezara primero, habría filas que nadie
sabe leer, y el panel dibujaría ocho puntos donde dibujaba uno.

Ponerla vigente es cambiar una línea, y es su propia decisión.

### El arreglo que M.9.0b dejó anotado

`#67` encontró que la ventana de una pasada no selecciona sus escenas: `S2_SR` y
`S2_CLOUD_PROBABILITY` comparten el `system:index` —que es por donde las une
`fuente.coleccion`— pero **no el `system:time_start`**, y el desfase va de 129 a 1169 segundos
según dónde caiga el ROI en la pasada.

**El arreglo es el que `#67` ya había identificado:** el filtro de fecha de la colección de
nubes pasa a ser un **superconjunto** del pedido —un día de margen de cada lado—. Quien decide
qué escena entra es el join por `system:index`, que es exacto; la fecha está sólo para no traer
la colección entera. Un día es mil veces el desfase medido, y es una unidad natural en vez de
un número ajustado a lo que se midió.

**Se aplicó a las dos recetas, no sólo a v2** (decisión del usuario). Es un bug, no una
diferencia de receta: una escena de los primeros minutos de un mes tenía su imagen de nubes en
el mes anterior y **se descartaba entera**.

**Y se midió antes de darlo por inocuo**, que era la condición: sobre 3 parcelas reales × 24
meses, **576 escenas unidas antes y 576 después, y 0 meses en que cambie algo**. Con el paso a
las 15:11 UTC, ninguna escena cae en los primeros minutos de un mes. El bug era real y el
arreglo es correcto; en estas parcelas no tenía efecto.

### Lo que v2 cambia contra v1, y nada más

Dos campos, y los dos salieron de lo que midió M.9.0:

- **`agrupamiento_estadisticas: por_pasada`**. La mediana de 3 pasadas limpias por mes (`#66`)
  es lo que hace que valga la pena, y el **0 de 72** —ningún mes llega al umbral con todas sus
  pasadas por debajo— es lo que dice que no se pierde ninguno;
- **`umbral_al_escribir: False`**. El umbral deja de descartar al escribir (`#63`).

**El ráster sigue mensual**: `agrupamiento_raster` es `entero` en las dos. Hay un test que fija
que v2 es v1 **con esos dos cambios y ninguno más** — sin eso, la comparación entre las dos no
significaría nada.

### `umbral_al_escribir` es un campo de la receta, no un `if`

Descartar al escribir es una reducción con pérdida **antes** de guardar, y es la que no se puede
deshacer: el día que 0,3 resulte mal puesto, con el umbral al leer se cambia el número y con el
umbral al escribir se reprocesa el histórico. Que sea un campo —y no una rama en `filas.py`—
es lo que lo pone en la huella y lo deja escrito por receta.

Hay un test del control y otro del control del control: con el campo en `False` el valor va
aunque la cobertura sea baja, y con el mismo dato en `True` sale nulo. Y un tercero para la
distinción que importa: **un mes sin un solo píxel sigue sin valor en v2**, porque la mediana
vino en `None` desde GEE. «No llegó al umbral» y «no hay dato» son cosas distintas.

### `FilaMensual` pasa a llamarse `Fila`

M.9.0b la dejó como estaba a propósito, porque renombrarla entonces habría tocado el
repositorio y sus tests sin que ninguna fila cambiara. Ahora dejó de ser cierto: con v2, una
fila es una pasada.

### El control negativo, otra vez

Igual que en M.9.0b, y contra el `main` de ahora: las **129 entradas** —24 meses × 4 coberturas
que cruzan el umbral, las tres familias de claves de capa, el intervalo que se le pide a GEE, y
9 meses de parcela contra GEE de verdad— dieron **idénticas**. Eso cubre las dos cosas que
podían haber cambiado v1 sin querer: el filtro de nubes ancho y el campo nuevo de la receta.

### Verificado contra GEE, no sólo con tests

El camino entero por pasada se corrió sobre una parcela real, en tres meses elegidos:

| Mes | Pasadas | Qué salió |
|---|---|---|
| 2026-07 | 9 | 36 filas, 36 claves distintas. Tres pasadas limpias con NDVI 0,483 / 0,607 / 0,572 |
| 2025-02 | 5 | 20 filas. NDVI de 0,443 a 0,107 **dentro del mes** — el evento que la mediana mensual (0,377) borra |
| 2026-05 | 10 | 40 filas, **todas sin valor**: es el mes en que las diez pasadas quedan tapadas por la máscara |

**Armar las ventanas cuesta una sola llamada a GEE**, la de `fechas_de`. Y el último caso deja
ver el costo de «por pasada puro» que `#63` aceptó: un mes enteramente nublado pasa de 4 filas
a 40, todas con cobertura 0. Entra dentro de la estimación de `#63` (~768 filas por parcela en
dos años) y es información —«hubo una pasada el día 3 y no sirvió» no es lo mismo que «no hubo
pasada»—, pero conviene saberlo antes de poner v2 vigente.

### Lo que falta, y es la otra mitad

`/api/measurements` tiene que aprender a agregar, con **`mensual` por defecto**: eso es lo que
hace que poner v2 vigente **no rompa el panel de hoy**. El número mensual es la **mediana de las
medianas por pasada** (decisión del usuario, 2026-09-25), que es la que `#66` ya midió: se
aparta 0,008 de NDVI de la mediana del compuesto, con p90 de 0,029.

Va con SQL crudo (`FromSql`), porque la mediana en Postgres es `percentile_cont` y EF Core no la
traduce — y agregar en memoria pediría traer ~3.800 filas contra un techo de 2.000, que es
justo el modo de fallo silencioso que `MedicionesQueries` ya documenta. Es el primer SQL crudo
del repo; decisión del usuario del 2026-09-25.

---

## 70. `s2-pasada-v2` pasa a ser la receta vigente (2026-09-25)

> **Decisión del usuario.** Desde acá, las altas y el cierre de mes escriben **una fila por
> pasada** en vez de una por mes. Es el último paso del bloque M.9.0, y el que convierte en
> datos lo que `#66` midió.

### El orden de despliegue se respetó, y era la condición

`/api/measurements` ya sabe agregar desde Geocore#60 (`DECISIONS #48` de Geocore), **con
`cadencia=mensual` por defecto**. O sea que el panel pide lo mismo que pedía y recibe puntos
mensuales, **sin tocar una línea**. Es la regla de M.8.1 —el que exige va último— con el que
**lee** en ese lugar.

Sobre filas mensuales, agrupar por mes es la identidad, y hay un test contra PostgreSQL que lo
fija: es lo que sostiene la promesa de arriba.

### Lo que cuesta, medido antes de hacerlo

Contra GEE, sobre una parcela real de 101 ha, cuatro meses:

| Mes | v1 | v2 |
|---|---|---|
| 2025-02 | 2,0 s · 1 llamada · 4 filas | **13,6 s · 6 llamadas · 20 filas** |
| 2026-05 | 2,8 s · 1 llamada · 4 filas | **25,1 s · 11 llamadas · 40 filas** |
| 2026-07 | 2,2 s · 1 llamada · 4 filas | **20,6 s · 10 llamadas · 36 filas** |
| 2026-08 | 2,1 s · 1 llamada · 4 filas | **22,4 s · 11 llamadas · 40 filas** |

**Unas 9 veces más**, en tiempo y en llamadas. El peor mes medido, 25,1 s, entra en la compuerta
de M.2.6 —que pide menos de 60 s por mes— pero con **menos del doble de margen**.

**⚠️ Y eso es sobre una parcela de 101 ha.** La compuerta de M.2.6 se corrió sobre esas mismas
parcelas y **el tiempo con una parcela grande sigue sin medirse**: es un pendiente anotado desde
M.2. Con v1 un mes iba de 2 a 8 s; si v2 multiplica por 9, una parcela en el extremo alto daría
**~72 s y pasaría la compuerta**. Es lo primero a mirar si un alta empieza a fallar.

**Lo que NO se mueve:** la cuota de Inngest. Sigue habiendo **un step por mes** —24 por alta—,
así que las ~27 ejecuciones por alta no cambian. Lo que crece es lo que hace cada step.

### Lo que pasa con lo que ya está escrito

**Nada.** Las filas de `s2-mensual-v1` se quedan con su versión y su fecha —el día 1 del mes— y
las nuevas conviven al lado con la fecha de adquisición. No hay migración: la clave
`(parcela, índice, fecha)` sirve para las dos (`#63`).

**Un mes que tenga las dos clases de fila sale de `/api/measurements` con
`receta: "s2-mensual-v1,s2-pasada-v2"`**, a la vista y no en silencio: el `string_agg DISTINCT`
de `DECISIONS #48` de Geocore está justamente para que mezclar dos semánticas no devuelva un
número plausible y equivocado.

Eso pasa **sólo si se reprocesa un mes que ya tenía fila v1**. Un mes nuevo —el cierre mensual—
sale limpio en v2.

**Los COG nuevos cuelgan de `…/s2-pasada-v2/…`**, porque la receta va en la key (`#47`). Los
objetos de v1 quedan en el bucket bajo su prefijo; la fila de `layers` se repunta sola al
reprocesar, porque la `natural_key` no lleva la receta. Está documentado en `claves.py`.

### `RECETA_MENSUAL_V1` no se borra

La receta v1 sigue en el código con su nombre propio. Es la receta de las filas que ya están
escritas, y `measurements.receta` las nombra por su versión: borrarla dejaría filas apuntando a
una receta que no existe, que es justo lo que la versión en cada fila viene a evitar. Su huella
sigue fijada en `HUELLAS`.

### Lo que cambió en los tests, y por qué así

**Los tests que fijaban la orquestación mensual clavan `RECETA_MENSUAL_V1` explícitamente**, en
vez de seguir a `RECETA_VIGENTE`. Lo que prueban —el plan, un step por mes, la bitácora, la
barra, la forma de la key, lo que el repositorio hace con una fila sin valor— **no es de la
receta**, y clavándola se sigue leyendo cuánto cuesta *un* mes sin que el test cambie de
significado cada vez que cambia la vigente.

Un test que sigue a `RECETA_VIGENTE` y afirma un literal deja de decir nada el día que la
vigente cambia: se vuelve verde por construcción.

**Y entraron tres tests de la orquestación por pasada**, que es lo que producción hace ahora:
que un mes escriba N × 4 filas con una clave por pasada e índice —si dos ventanas cayeran en la
misma fecha, el upsert rechazaría el lote entero y el mes se perdería—, que cueste una reducción
por pasada, y que una pasada bajo el umbral **conserve su valor**.

### Lo que falta

**M.9.0d, el panel**: el eje pasa a ser una fecha y aparece el interruptor mensual / por pasada.
Hasta entonces el panel sigue viendo la serie mensual, que es correcta — simplemente no muestra
todavía la serie fina que ya se está guardando.

---

## 71. Con una fila por pasada, la bitácora cuenta pasadas, y el mes "deja dato" si una sirve (2026-09-26)

> Salió de mirar el alta de una parcela de prueba en el valle del Cauca. La línea del mes decía
> `cobertura 21,0 %, bajo el mínimo de 30,0 %: filas sin valor · 76 fechas escritas`, y las
> tres cosas estaban mal para v2.

**Lo medido primero**, contra GEE y con el código del pipeline: julio de 2025 tuvo **19
pasadas**, 10 tapadas por completo, 5 con 1 a 15 % de la parcela a la vista y 4 con 72 a 100 %.
La cobertura de cada una coincide con la clasificación de escena (SCL) de la ESA, así que **la
máscara y el cruce con las nubes están bien** —el hallazgo de `#67` sigue resuelto—: son nubes.

**Lo que decía la línea, y por qué era falso con v2:**

- el 21 % era el **promedio de las 19**, tapadas incluidas: un número que no describe nada;
- "bajo el mínimo: filas sin valor" es la frase de v1, donde el umbral se aplica al escribir.
  Con v2 no se aplica, y la frase salía en **cualquier** mes con una pasada tapada, aunque
  hubiera cuatro buenas;
- el mes contaba como "sin valor" en el resumen del alta, porque la regla pedía que **todas**
  las filas tuvieran valor.

**Lo decidido:**

- **El mes deja dato si al menos una ventana es útil**: llega a `cobertura_minima` y tiene
  mediana. Con v1 hay una sola ventana, así que la regla da exactamente lo mismo que antes; los
  18 tests de la orquestación mensual pasan sin tocarlos.
- **Con v2 la línea cuenta**: `19 pasadas, 4 con al menos 30,0 % de la parcela a la vista, 10
  tapadas por completo`, y avisa sólo si ninguna sirve. El detalle suma `pasadas`, `utiles` y
  `tapadas`. La línea de v1 no cambia.
- **Las pasadas tapadas se siguen guardando** (decisión del usuario, 2026-09-26): registran que
  el satélite pasó y la parcela estaba tapada, que no es lo mismo que "no hubo pasada", y
  sirven para medir la confianza de cada mes y dónde haría falta radar (M.9.4).

**Lo que queda abierto, y es lo más importante de esto: el costo.** Hoy cada pasada, tapada o
no, cuesta una reducción completa en GEE. El costo de un mes depende de **cuántas pasadas hay**,
no de cuántas sirven, y en el Cauca son 19 por mes. La compuerta de 60 s se puede pasar por una
parcela grande **o por una zona nublada**. La propuesta es medir la cobertura de todas las
pasadas en una sola llamada y reducir sólo las que tienen algún píxel limpio, con el control
negativo de M.9.0b: las filas tienen que salir idénticas.
