# OWASP Top 10 (2021) — Mapeo del ecosistema Terra

> Generado el 2026-06-12. **Scope extendido el 2026-09-02** a los cuatro repos:
> `Geocore/`, `terra-admin/src/`, **GeeWorker** y **terra-tileserver**.
> Complementa a [`SECURITY_FIXES.md`](SECURITY_FIXES.md): aquel rastrea *hallazgos individuales*;
> este los reagrupa por categoría OWASP y marca los gaps que aún no se han atacado.

**Por qué se extendió.** Hasta el 2026-09-02 este documento decía *"Scope:
`Geocore/` + `terra-admin/src/`"* y no mencionaba ni al worker, ni al
tileserver, ni a MinIO. Los dos repos de Python son los que se trabajan hoy, y
sus hallazgos de seguridad **no tenían dónde anotarse**: quedaban en un SESSION,
que es una crónica y no un registro consultable por categoría.

Es el mismo agujero que tenía [`WORKFLOW.md`](WORKFLOW.md) hasta el 2026-08-27
(`PREGUNTAS_ABIERTAS` E-1): se arregló para el pipeline de desarrollo y no para
el mapeo de seguridad. Extenderlo era barato; no tenerlo extendido hizo que
tres hallazgos del mismo tipo —un default inseguro que falla abierto— se
descubrieran uno por uno en tres repos distintos en vez de buscarse a propósito
en los cuatro.

---

## Cómo leer este documento

Cada categoría OWASP lista:
- **Controles presentes** — qué fix/decisión ya la cubre (con su ID en `SECURITY_FIXES.md`).
- **Estado** — ✅ cubierto / ⚠️ gap parcial / ⏸️ diferido.

Los hallazgos de los repos de Python viven en su propia sección, con prefijo de
repo, y se referencian desde las categorías: **`W-n`** para GeeWorker, **`T-n`**
para el tileserver. Se separan porque son **servicios desplegables por
separado**: al desplegar uno, lo que importa es su postura, no la del conjunto.

---

## A01 — Broken Access Control  ⚠️ (1 gap diferido)

El eje central de toda la auditoría. Controles presentes:

- **C-1** — Aislamiento de tenant en 9 endpoints (`RequireTenantId` en `RanchosController`/`ParcelasController`); un `Client` sin `X-Tenant-ID` recibe 400, no acceso cross-tenant.
- **M-1** — `TenantMiddleware` valida `IsActive` en **toda** request autenticada: un JWT válido de un usuario desactivado en Geocore es rechazado (403 `USER_INACTIVE`) sin esperar a que expire el token.
- **N-4** — La edge function `create-user` autoriza al caller (`TerraStaff`) **antes** de tocar Supabase Auth.
- **Endpoints nuevos (2026-06-12)** — `POST /api/ranchos/{id}/activate` y `POST /api/parcelas/{id}/activate` pasan por el mismo `RequireTenantId` + lanzan `ForbiddenException` ante tenant ajeno (con cobertura de tests). `GET /api/tenants/{id}/members` está bajo `[Authorize(Policy = "TerraStaff")]`.

**Gap:** **N-1 (diferido)** — No hay separación entre `TerraAdmin` y `TerraSupport`; ambos comparten la política `TerraStaff` y `TerraSupport` puede auto-promoverse vía `PATCH /api/users/{id}/role`. Riesgo aceptado explícitamente hasta que el equipo defina el alcance de `TerraSupport`. Ver [N-1] en el tracker.

---

## A02 — Cryptographic Failures  ✅

- JWT firmado con **ES256** (clave asimétrica), validado vía el **JWKS** de Supabase (`MetadataAddress` en `Program.cs`), no con un secreto compartido. Rotación de claves automática.
- Las contraseñas nunca tocan Geocore — las gestiona Supabase Auth (ver `DECISIONS.md` #2).

---

## A03 — Injection  ✅

- **SQL:** EF Core parametriza todas las queries; no hay SQL crudo concatenado.
- **XSS:** React auto-escapa todo el contenido renderizado. No se usa `dangerouslySetInnerHTML`. El `Tooltip` del mapa (`GeometryView`) renderiza nombres de rancho/parcela como texto plano.
- **XXE (client-side):** El parseo de KML/GeoJSON/WKT del `GeometryInput` usa el `DOMParser` del navegador, que **no resuelve entidades externas** (sin acceso a red ni FS).
- **XXE (server-side):** Desde 2026-08-16 el backend **también** parsea KML subido por el usuario (`POST /api/kml/*`, DECISIONS #17) — antes solo recibía coordenadas ya extraídas. `KmlParser` configura el `XmlReader` con `DtdProcessing.Prohibit` y `XmlResolver = null`, que cierra tanto XXE como la expansión recursiva de entidades ("billion laughs"). Ambos casos están cubiertos por tests en `Geocore.Infrastructure.Tests/Geospatial/KmlParserTests.cs`. **No relajar esa configuración.**

---

## A04 — Insecure Design  ⚠️ (falta rate limiting)

- **N-2** — Paginación acotada (`Pagination.Normalize`: page≥1, pageSize 1..200) → evita agotamiento de recursos por `?pageSize=10000000`.
- **N-3** — Guard de "último TerraAdmin activo" en `ChangeRoleAsync`/`DeactivateAsync` → evita lockout operacional.

**Gap:** **No hay rate limiting** en `create-user` (edge function) ni en el login. Riesgo: fuerza bruta de credenciales y enumeración de emails registrados (el ciclo crear→rollback revela colisiones de email). Mitigado parcialmente por A-1/N-4 (validación temprana), pero sin límite de tasa real.

---

## A05 — Security Misconfiguration  ✅

- **C-2** — `verify_jwt = true` en `config.toml` (Supabase valida el JWT antes de invocar la función) y `Access-Control-Allow-Origin` acotado a `TERRA_ADMIN_URL` (no `*`).
- CORS de Geocore restringido a `Cors:Origins` (whitelist en `appsettings`).
- **Hardening de producción (2026-07-11)** — Un solo artefacto (imagen Docker); comportamiento por entorno vía `ASPNETCORE_ENVIRONMENT`: Swagger y HTTPS-redirect solo en dev, HSTS solo en prod. `ForwardedHeaders` reconstruye scheme/IP tras el proxy de Railway (TLS en el edge). Cabeceras de seguridad en toda respuesta (`X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`). Secretos solo por env vars (Railway) / user-secrets (dev), nunca versionados. Artefactos filtrables (`supabase/.temp/`, `.sonarqube/`) sacados del repo y gitignoreados. Guía: `DEPLOYMENT_RAILWAY.md`.

---

## A06 — Vulnerable & Outdated Components  ⚠️ (con proceso en un repo de cuatro)

- **Geocore y terra-admin:** no hay proceso documentado de auditoría de dependencias (`dotnet list package --vulnerable`, `npm audit`).
- **GeeWorker** ✅ desde el 2026-09-02: dependencias directas pinneadas contra Python 3.13 (`DECISIONS #22`), `pip-audit` pinneado en `requirements-dev.txt`, y la regla de que todo instale como wheel. Última corrida: *No known vulnerabilities*.
- **terra-tileserver** ⚠️ `rasterio`, `rio-tiler` y GDAL entran **sin pinnear**, como dependencias transitivas: la imagen se lleva lo que haya el día del build. Ver **T-5**.

---

## A07 — Identification & Authentication Failures  ✅

- **B-1** — Manejo de sesión expirada: `SessionExpiredError` + `endExpiredSession()` (signOut → redirección al login). Ya no se envía `Authorization: Bearer undefined`, y los errores dejan de silenciarse en las páginas (se muestran en un banner).
- **B-2** — El gate de UI por `global_role` está documentado como **cosmético**; la autorización real la hace Geocore validando la firma del JWT.
- Lockout/MFA/verificación de email → delegados a Supabase Auth.

---

## A08 — Software & Data Integrity Failures  ✅

- **C-3** — Creación de usuario atómica: si Geocore rechaza, la edge function hace `rollback()` (hard delete en Supabase Auth para liberar el email). Huérfanos no eliminables se loguean como `ORPHANED_USER`.

---

## A09 — Security Logging & Monitoring Failures  ⚠️ (sin audit trail)

- Existe el log `ORPHANED_USER` (con `authId` + `email`) para limpieza manual.

**Gap:** **No hay audit log de acciones privilegiadas.** Operaciones de TerraStaff — cambiar rol global, activar/desactivar usuarios, gestionar miembros de tenant (lo agregado en 2026-06-12) — no dejan rastro de *quién* las ejecutó ni *cuándo*. Es lo primero que pediría un auditor para investigar un abuso de privilegios.

---

## A10 — Server-Side Request Forgery (SSRF)  ⚠️ (dejó de ser ✅ al aparecer el tileserver)

- **Geocore y terra-admin:** la única llamada saliente server-side es la edge function → Geocore, usando `GEOCORE_API_URL` desde una **variable de entorno**, no desde input de usuario. Sin superficie de SSRF.
- ⚠️ **La afirmación "no hay superficie de SSRF" ya no vale para el ecosistema.** El tileserver abre con GDAL lo que recibe en `?url=`, que es input de usuario: es la superficie de SSRF más directa que tiene Terra. Ver **T-2** (cerrado) y **T-3** (abierto).

---

---

# GeeWorker — hallazgos `W-n`

> Repo: `Downloads\geework 2.0`. Auditado el **2026-09-02** con la etapa AUDIT
> del `WORKFLOW` más `ruff` y `pip-audit`. Crónica:
> [`SESSION_2026-09-02_auditoria_del_constructor.md`](SESSION_2026-09-02_auditoria_del_constructor.md).

**Contexto que cambia cómo se leen estos hallazgos:** el worker **no expone API
de negocio**. Su superficie HTTP es `GET /health` y `/api/inngest`, y nada más
(`DECISIONS #23`, fijado por `tests/test_http_surface.py`). No recibe input de
usuario por HTTP: recibe eventos de Inngest con ids y geometrías que produjo
Geocore. Eso hace que las categorías de inyección y de control de acceso pesen
poco, y que el riesgo real esté en **credenciales, permisos y configuración**.

| | Categoría | Hallazgo | Estado |
|---|---|---|---|
| **W-1** | A05 | **Credenciales root por defecto.** `MINIO_ACCESS_KEY`/`MINIO_SECRET_KEY` caían a `minioadmin`, la credencial **root** del `docker-compose`. Un deploy sin las variables no fallaba: se autenticaba como root, y en local funcionaba, así que solo podía aparecer en producción | ✅ Cerrado 2026-09-02 (`DECISIONS #24`). Fuera de desarrollo no hay default y el cliente falla cerrado nombrando la variable. **Ojo: `ENVIRONMENT=development` reabre el default** |
| **W-2** | A02 | **`MINIO_SECURE` por defecto en `False`.** Contra el dominio público de Railway hace falta `True`, o la access key, el secret y el COG viajan en texto plano | ⚠️ **Abierto.** Mitigado: `validate_endpoint()` detecta la incoherencia sin tocar la red y nombra la variable. Pero el default sigue siendo el inseguro |
| **W-3** | A01 | **`/assets` tomaba el `tenant_id` de un query param** y su `get_current_user_optional` se ignoraba: cualquiera podía leer los assets de cualquier tenant escribiendo su id en la URL. Además `CORS` con `allow_origins=["*"]` | ✅ Cerrado 2026-08-30 (`DECISIONS #23`) **borrando la superficie**, no arreglándola: reimplementar en Python el aislamiento que Geocore ya tiene testeado en C# era duplicar una decisión de seguridad |
| **W-4** | A01/A04 | **Permisos de más en el camino de escritura.** Sin `region`, minio-py pedía `s3:GetBucketLocation`; `ensure_bucket()` pedía `s3:ListBucket` en cada arranque. Ninguno lo usa la subida real. No es una vulnerabilidad: es lo contrario del menor privilegio, y produce un `AccessDenied` que se lee como un problema de credenciales | ✅ Cerrado 2026-09-01/02 (F.12 y F.13). Lo fijan `tests/test_storage_service.py` y `scripts/check_minio_region.py` |
| **W-5** | A09 | **El fallo se imprime con `print` y se traga.** Una config mala arrancaba "bien" con un warning perdido entre los logs y fallaba media hora despues, lejos de su causa. Repo-wide: tambien `db_repository.py` y `ee_indices.py`, mientras la convencion dominante en `services/` es `logging.getLogger(...)` | ✅ **Cerrado 2026-09-04.** Los 6 `print()` de codigo de produccion pasaron al logger de modulo. Los de `scratch/` quedan: es material gitignoreado, no codigo del servicio |
| **W-6** | A09 | **`object_exists()` convertia cualquier error en "no existe".** `except Exception: return False` mezclaba `AccessDenied`, red caida y `NoSuchKey`. **E.9 se apoya en esta funcion** para saltear el recalculo: un error transitorio disparaba un recalculo completo en GEE, o una sobrescritura decidida sobre un falso negativo | ✅ **Cerrado 2026-09-04.** Devuelve `False` solo ante `NoSuchKey`; todo lo demas se propaga y lo reintenta Inngest. `DECISIONS #21`: no haber podido concluir no es haber concluido |
| **W-7** | A01/A02 | **`get_presigned_url()` devolvia una URL sin firmar** si fallaba el firmado: `{protocol}://{endpoint}/{bucket}/{key}`, sin firma y sin vencimiento. En el mejor caso 403 que confunde; si el bucket fuera publico, un enlace permanente y anonimo en lugar de uno de 24 h. Fallaba abierto | ✅ **Cerrado 2026-09-04 borrandolo.** Era el unico metodo de la clase sin call sites, asi que se elimino en vez de arreglarse (criterio de `DECISIONS #23`). Un test lo fija para que no vuelva por costumbre |
| **W-8** | A01/A07 | **`/api/inngest` sin verificación de firma**, y por un motivo distinto del que decía el plan. El SDK ya leía `INNGEST_SIGNING_KEY` del entorno; lo que estaba mal era el **modo**: `_validate_sig` devuelve `None` sin mirar nada en dev, y el modo se calculaba como `ENVIRONMENT == "production"`, así que `prod`, un typo o la variable ausente apagaban la verificación. **Era más permisivo que no configurar nada**, porque el default del SDK es cloud. Sin firma, cualquiera podía invocar `process_parcela` con ids de otro tenant y gasto de cuota de GEE — es la única superficie pública del worker, sin segunda capa detrás | ✅ **Cerrado 2026-09-04** (`DECISIONS #25`). Un solo criterio de entorno, y **lo desconocido cuenta como producción**. Verificado con un cliente HTTP: en cloud, una invocación sin firma da **401 antes de parsear el cuerpo**; el caso dev queda como control negativo |
| — | A03 | **Inyección: sin superficie.** Las seis llamadas de `db_repository.py` usan placeholders `%s` de psycopg2 —verificado, cero f-strings ni concatenación—, y el `/upload-kml` que parseaba XML de usuario se borró en FASE D: eso lo hace Geocore con defensa XXE testeada (`DECISIONS #17`) | ✅ |
| — | A08 | **Idempotencia.** Inngest entrega *at-least-once* y el backoff de Geocore puede publicar dos veces. `layers` usa un UUIDv5 determinista, `measurements` un `ON CONFLICT` sobre su PK, y el `PUT` de S3 es idempotente por definición | ✅ |
| — | A10 | **SSRF: sin superficie.** `MINIO_ENDPOINT` viene del entorno, no de un request, y no hay endpoints que acepten URLs | ✅ |

---

# terra-tileserver — hallazgos `T-n`

> Repo: `Downloads\tileserver-titiler`. **Es el único servicio de Terra que
> recibe input de usuario y lo usa para abrir un recurso remoto**, así que su
> perfil de riesgo es distinto al del worker.

| | Categoría | Hallazgo | Estado |
|---|---|---|---|
| **T-1** | A02/A05 | **`MAP_TOKEN_SECRET` con fallback en el repo.** `os.getenv("MAP_TOKEN_SECRET", "default_terra_...")`: sin la variable, validaba tokens contra un secreto que está en el código, así que cualquiera podía forjar un token para cualquier COG. **Fallaba abierto**, y con una asimetría peligrosa: Geocore ya fallaba cerrado (503) para el mismo contrato | ✅ Cerrado 2026-08-21. Sin secreto devuelve **503 `MAP_TOKEN_UNAVAILABLE`**, el mismo código que Geocore, y loguea un ERROR al arrancar |
| **T-2** | A10 | **SSRF vía `?url=`.** `TilerFactory()` abría con GDAL cualquier cosa que le pasaran. Con un token válido —o sea, cualquier usuario legítimo del front— se podía pedir `http://minio.railway.internal:9000/…` y usar el tileserver como proxy hacia la red privada | ✅ Cerrado 2026-08-21 con un `path_dependency` que exige el prefijo `s3://{MINIO_BUCKET}/` y rechaza `..`. Segunda capa: la policy de `tiler-ro` acota el bucket del lado de MinIO |
| **T-3** | A10 | **Los assets listados *dentro* del MosaicJSON no pasan por el filtro.** El `path_dependency` valida la URL del documento, pero `cogeo-mosaic` abre los assets que ese documento lista tal como vengan, incluidos `http://` a la red interna. Es T-2 una capa más adentro | ⚠️ **Abierto, hoy contenido** (`PREGUNTAS_ABIERTAS` C-1): solo `worker-rw` escribe en el bucket, así que un MosaicJSON solo aparece ahí si lo puso el worker. **Deja de estar contenido si gana Geocore en A-1**, porque el contenido pasaría a llegar por HTTP |
| **T-4** | A05 | **El sondeo de salud exigía un permiso que el camino real no usa** (`s3:GetBucketLocation`). No es una vulnerabilidad: es un falso negativo, y los falsos negativos entrenan a la gente a ignorar el semáforo. Costó una sesión entera de depuración creyendo que la policy estaba mal | ✅ Cerrado 2026-08-26 (`DECISIONS #21`). De acá sale la regla que después atrapó **W-4** |
| **T-5** | A06 | **`rasterio`, `rio-tiler` y GDAL entran sin pinnear**, como transitivas de `titiler.core==0.18.0`. La imagen se lleva lo que haya el día del build, y el comportamiento de composición depende de esas versiones | ⚠️ **Abierto** (`PREGUNTAS_ABIERTAS` C-2). Mitigación existente: `scripts/check_mosaic_median.py` detecta si la composición cambia |
| **T-6** | A05 | **La consola de MinIO está pública.** Es la superficie de administración **root**. Se decidió dejarla abierta el 2026-08-21 para monitoreo | ⚠️ **Riesgo aceptado, recomendado revertir** (`PREGUNTAS_ABIERTAS` C-4): la razón para dejarla abierta era no tener otra vía de monitoreo, y `mc admin info` ya está configurado y funcionando |
| — | A01 | **Doble capa de autorización, independiente.** El token de mapa decide quién puede *pedir* un tile; `tiler-ro` decide qué puede hacer el tileserver *contra el bucket*. Un usuario con token válido no puede escribir, porque el tileserver tampoco | ✅ |
| — | A05 | **Caché acotado.** `Cache-Control: immutable` solo sobre **200** y solo bajo `/cog/tiles`: cachear un 5xx congelaría una caída momentánea de MinIO por un año | ✅ |

---

## Resumen de gaps abiertos

| Categoría | Repo | Gap | Severidad sugerida | Acción |
|---|---|---|---|---|
| A10 | tileserver | **T-3**: los assets del MosaicJSON no pasan por el filtro anti-SSRF | Alto **si** el mosaico pasa a servirse por HTTP | Decidir `PREGUNTAS_ABIERTAS` A-1 primero |
| A05 | GeeWorker | **`ENVIRONMENT` es una variable de seguridad** desde `DECISIONS #25`: en `development` apaga la verificación de firma **y** reactiva el default de credenciales de MinIO. No es un hallazgo, es una consecuencia que hay que vigilar | Alto si se configura mal | Ponerla en el checklist de despliegue |
| A02 | GeeWorker | **W-2**: `MINIO_SECURE` por defecto en `False` | Medio (alto en el momento de A-3) | Fijar `True` en el entorno desplegado |
| A05 | tileserver | **T-6**: consola de MinIO pública (root) | Medio | Cerrarla: `mc admin info` ya cubre el monitoreo |
| A06 | tileserver | **T-5**: `rasterio`/GDAL sin pinnear | Medio | `PREGUNTAS_ABIERTAS` C-2 |
| A01 | Geocore | N-1: separación TerraAdmin/TerraSupport | Alto (si Support debe ser acotado) | Diferido — esperando definición del equipo |
| A04 | Geocore | Rate limiting en login/create-user | Medio | Por planificar |
| A09 | Geocore | Audit log de acciones privilegiadas | Medio | Por planificar |
| A06 | Geocore/admin | Proceso de auditoría de dependencias | Bajo | Por planificar |

## El patrón que este documento hace visible

Tres hallazgos de tres repos distintos son **el mismo defecto**: un secreto o
una credencial con default en el código, que hace que un deploy mal configurado
**funcione de forma insegura** en vez de fallar.

| | Repo | Qué |
|---|---|---|
| `DECISIONS #16` | Geocore | `?? DevMapTokenSecret` al firmar tokens de mapa |
| **T-1** | tileserver | `MAP_TOKEN_SECRET` con default en el repo |
| **W-1** | GeeWorker | `MINIO_ACCESS_KEY`/`SECRET_KEY` con la credencial root del compose |

Se descubrieron **uno por uno, en tres sesiones distintas y a lo largo de tres
semanas**, cada uno como si fuera nuevo. Con el scope de este documento
limitado a dos de los cuatro repos, no había dónde ver que era un patrón.

**La regla, escrita para que se busque a propósito y no se tropiece:** un valor
por defecto es aceptable cuando su ausencia produce un servicio que **no
funciona**; es un defecto cuando produce un servicio que funciona **con menos
seguridad**. Al agregar cualquier `getenv(X, default)`, la pregunta es: *si esta
variable falta en producción, ¿esto se rompe o se abre?*

> El build de producción del frontend (Base UI vs. Radix) **no es un tema de seguridad**:
> su decisión y estado viven en `DECISIONS.md #14` (resuelto 2026-06-13).
