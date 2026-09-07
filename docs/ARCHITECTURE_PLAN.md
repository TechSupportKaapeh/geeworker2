# ARCHITECTURE_PLAN.md — Ecosistema de microservicios

> Creado el 2026-06-13. Planning de la arquitectura objetivo.
> **Convención de estado:** ✅ implementado · 🟡 parcial · 🔵 proyectado (no existe aún).
> Regla rectora del diseño: **servicios desacoplados, desplegables por separado** (Railway, un Dockerfile por servicio). No compartir base de datos ni código de negocio entre servicios.

---

## 1. Estado actual (lo que existe)

| Componente | Estado | Responsabilidad |
|---|---|---|
| **Geocore** (.NET 10) | ✅ | Identidad, organizaciones (tenants), agricultura (ranchos/parcelas). Fuente de verdad de la geometría. |
| **terra-admin** (React) | ✅ | Panel TerraStaff. Consume Geocore + Supabase. |
| **Supabase** | ✅ | Auth (JWT ES256) + Postgres + Edge Function `create-user`. |
| **Inngest** | 🟡 | Bus de eventos / orquestación durable. Hoy solo se publica `parcela.created`. |

---

## 2. Estado objetivo (proyectado)

```
                         ┌────────────────────────────────────────────┐
                         │                FRONTEND                     │
                         │   terra-admin (panel) · app cliente         │
                         └───┬───────────────┬───────────────┬────────┘
                  REST/JWT   │        tiles   │      auth     │
                             ▼                ▼               ▼
                     ┌──────────────┐  ┌────────────┐  ┌────────────┐
                     │   Geocore    │  │  TiTiler   │  │  Supabase  │
                     │  (.NET API)  │  │ (tiles COG)│  │   (auth)   │
                     └──────┬───────┘  └─────▲──────┘  └────────────┘
                 rancho.    │ (outbox)       │ lee COG
                 created    ▼                │
                     ┌──────────────┐        │
                     │   Inngest    │        │
                     │ (orquestación)│       │
                     └──────┬───────┘        │
                            ▼                │
                     ┌──────────────┐   ┌────┴───────┐
                     │ Svc Rasters/  │──▶│   MinIO    │  (COG = blobs)
                     │ Análisis (wkr)│   │ (object st)│
                     └──────┬───────┘   └────────────┘
                            ▼
                     ┌──────────────┐
                     │   DB propia   │  catálogo + análisis (con timestamps)
                     │ (Postgres)    │
                     └──────────────┘
```

| Servicio (🔵 nuevo) | Responsabilidad | Datos que posee |
|---|---|---|
| **Servicio Rasters/Análisis** (worker) | **Dos funciones Inngest, costura = evento `raster.ingested`:** (1) *Ingesta* — consume `rancho.created`, descarga escenas → COG → MinIO, emite `raster.ingested`. (2) *Análisis* — consume `raster.ingested`, calcula y persiste índices/estadísticas (por rancho/parcela, **con timestamp**). Ambas idempotentes. Arrancan como **un desplegable**; se parten en dos servicios cuando el perfil de escalado (IO vs CPU/GPU) lo exija — sin cambiar el contrato del evento. | **Su propia DB** (catálogo + análisis). Nunca el Postgres de Geocore. |
| **DB del servicio Rasters/Análisis** | Metadata de capas + serie temporal de análisis. | `raster_layers(ranchoId, fecha, cogKey, bbox, status)` · `analysis(ranchoId/parcelaId, métrica, valor, capturedAt, …)`. Referencia a Geocore **por ID**, sin FK cross-DB. |
| **MinIO** | Object storage S3-compatible para los COG (blobs). **Guarda las dos etapas**: el COG **crudo** que el worker descarga del proveedor satelital, y el COG **procesado** (el índice calculado). De ese material el worker deriva después las métricas. | Los rasters, particionados por `ranchoId`/fecha. |
| **TiTiler** | Renderizado dinámico de tiles (XYZ/WMTS) leyendo los COG de MinIO. Sirve las capas al front. | Nada propio: lee de MinIO. |

**Split de almacenamiento (regla):** blobs pesados → MinIO; metadata + análisis consultable (series temporales) → DB del servicio. No mezclar (ni rasters en DB, ni análisis en MinIO). El **catálogo de capas vive en esta DB** (resuelve la decisión §6.1 hacia un servicio dedicado).

> **Un índice (p. ej. NDVI) puede existir en DOS formas, y cada una va a su sitio:**
> 1. **Como raster** (NDVI por píxel, una imagen por fecha) → es un COG → **MinIO** → se renderiza como capa en el mapa (TiTiler).
> 2. **Como estadística agregada** (NDVI promedio/min/max de una parcela en una fecha) → es un número → **DB** → series temporales y análisis.
>
> No es "uno u otro": el worker normalmente produce **ambos** del mismo cálculo — el COG del índice para visualizar y el resumen para consultar.

---

## 3. Reglas de desacople (el núcleo del diseño)

Los servicios se comunican **solo por estos tres canales** — nada más:

1. **Eventos asíncronos (Inngest)** → "algo pasó" (p. ej. `rancho.created`, `raster.ingested`). Productor y consumidor no se conocen.
2. **APIs REST + JWT** → consultas/comandos entre fronteras (el front pregunta a Geocore o al catálogo).
3. **Object storage (MinIO)** → blobs grandes (los COG). Nadie pasa rasters por HTTP de negocio.

Y dos prohibiciones que mantienen el desacople:

- **🚫 Sin base de datos compartida.** El worker y TiTiler **nunca** tocan el Postgres de Geocore. Es la regla #1 anti-acople.
- **🚫 Sin librería de negocio compartida.** A lo sumo, contratos (shape de eventos) versionados. Cada servicio compila y despliega solo.

**Auth desacoplada:** cada servicio valida el JWT de Supabase por su cuenta (mismo JWKS, sin código común) — el patrón que Geocore ya usa. No se centraliza la auth en un gateway (eso re-acopla).

---

## 4. Decisión del gateway / Traefik

**Veredicto: no introducir gateway todavía.** En Railway:
- Railway ya da TLS + dominio público por servicio + red privada interna → el rol de infra de un gateway está cubierto.
- El front llama directo a Geocore, a TiTiler (tiles) y a Supabase. Cada uno valida su JWT.

**Cuándo reconsiderarlo** (criterios concretos, no "por si acaso"):
- Necesitas **rate-limiting unificado** en el edge (gap OWASP **N-5**) en vez de implementarlo servicio por servicio.
- Quieres **una sola superficie pública** / un dominio.
- WAF o logging centralizado de requests.

Si llega ese momento: gateway **fino** (solo políticas cross-cutting, cero lógica de negocio). Traefik es una opción; en Railway quizá baste su ingress + un gateway ligero. **Importante:** los **tiles de TiTiler NO deben pasar por un gateway pesado** (volumen alto, mejor directo / CDN-friendly; auth por URL firmada o token).

> Justificación completa (hosting de la demo + por qué no Traefik hasta cambiar de nube): [`DEPLOYMENT_DECISION.md`](DEPLOYMENT_DECISION.md).

---

## 5. Flujo del pipeline de rasters

> ⚠️ Este bloque describía el diseño *previsto*. Actualizado el 2026-08-17 con lo que
> efectivamente se construyó; los pasos que siguen pendientes están marcados.

```
1. TerraStaff sube el KML → POST /api/kml/ranchos
   Geocore parsea el archivo y guarda el rancho   (DECISIONS #17)

2. Geocore publica `terra/rancho.created` a Inngest
   ⚠️ PENDIENTE: hoy se publica DESPUÉS del SaveChanges, sin outbox. Si Inngest
      falla, el rancho queda creado y nadie lo procesa nunca.

3. Inngest → invoca al worker (repo aparte):
   a. resolver las escenas satelitales para el bbox del rancho
   b. descargar las escenas          → MinIO   (COG crudo)
   c. procesar / calcular el índice  → MinIO   (COG procesado)
   d. derivar métricas del resultado → DB GeoData, tabla `measurements`
   e. registrar la capa disponible   → DB GeoData, tabla `layers`
                                       (StorageKey = ruta del COG en MinIO)

4. El front pide la capa:
   GET /api/layers/{id}   → Geocore arma la URL de tiles apuntando a TiTiler
   GET /api/maps/token    → JWT de 1 h que TiTiler valida   (DECISIONS #16)
   → TiTiler lee el COG desde MinIO y renderiza → Leaflet pinta la capa
```

**Geocore nunca toca MinIO.** Es la parte que más se malinterpreta, así que conviene fijarla:
no tiene cliente de storage ni sube ni baja nada. Solo guarda `Layer.StorageKey` —un texto con
la ruta del objeto— y con él compone la URL `s3://{bucket}/{key}` que le pasa a TiTiler
(`LayersController.cs:78-81`). Quien lee del bucket es **TiTiler**; quien escribe, el **worker**.
El bucket se configura con `GeoData__MinioBucket` (default `terra-assets`).

**El split de almacenamiento, aplicado a este flujo:** los blobs pesados (los dos COG) van a
MinIO; lo consultable (las métricas de `measurements` y el catálogo de `layers`) va a la DB de
GeoData. Nunca al revés — ni rasters en la DB, ni métricas en el bucket.

**Idempotencia:** el worker debe ser idempotente por `ranchoId`+fecha (Inngest reintenta).
Re-procesar no debe re-descargar lo ya subido.

> **Nota de despliegue:** [`DEPLOYMENT_DECISION.md`](DEPLOYMENT_DECISION.md) §1 decidió que
> **para la demo** el object storage sea **Supabase Storage** en lugar de self-hostear MinIO —
> una pieza con estado menos que operar. Como ambos hablan la API S3, el flujo de arriba es el
> mismo y cambiar es configuración; el nombre `MinioBucket` quedó como resabio. MinIO sigue
> siendo el destino para paridad full self-host más adelante.

---

## 6. Decisiones abiertas (a definir antes de construir esas piezas)

1. **Dónde vive el catálogo de capas.** (a) Read-model dentro de Geocore que consume `raster.ingested` → una sola API para el front, pero Geocore pasa a "saber" de rasters (algo de acople). (b) Servicio de catálogo aparte → más puro, un servicio más. Recomendación pragmática: empezar por (a) si el front ya habla con Geocore; migrar a (b) si crece.
2. **Auth de los tiles.** URLs prefirmadas de MinIO, o TiTiler validando un token corto. Evitar proxiar tiles por lógica de negocio.
3. **Formato/origen de los rasters.** Qué fuente (Sentinel/Landsat/GEE), bandas, resolución, rango temporal → define el payload que el worker necesita además de la geometría.
4. **MinIO gestionado vs. self-host** en Railway (volumen + servicio) vs. S3 externo.

---

## 7. Roadmap por fases

> Cada fase es un incremento del [WORKFLOW.md](WORKFLOW.md). Las fases dentro de Geocore son las que puedo implementar (scope: `Geocore/` + `terra-admin/`).

> **Reordenado el 2026-08-17.** El orden original ponía el outbox (P0/P1/P1.5) **antes** del
> worker. Se decidió diferirlo (DECISIONS #18): es puramente interno, no cambia ningún
> contrato y agregarlo después cuesta lo mismo. **El worker ya no tiene prerequisitos dentro
> de Geocore.**

| Fase | Qué | Dónde | Estado |
|---|---|---|---|
| ~~**P0/P1/P1.5**~~ | ~~Outbox transaccional + eventos vía outbox~~ | Geocore | ⏸️ **Diferido** (DECISIONS #18). Mitigado con reintento y backoff. Ya no bloquea nada. |
| **P1** | Eventos `rancho.created` / `parcela.created` con payload de geometría | Geocore | ✅ **Hecho.** Se publican al crear, incluido el import masivo de KML. |
| **P2a** | **Storage**: decidir MinIO vs Supabase Storage y provisionarlo | infra | 🔵 **Siguiente.** Bloquea a P2b y P2c. Ver DEPLOYMENT_DECISION §1. |
| **P2b** | **TiTiler** apuntado al storage, validado con un COG subido a mano | repo aparte | 🔵 No necesita el worker. Cierra la verificación pendiente del token de mapa (#16) antes de que nadie construya encima. |
| **P2c** | **Worker de rasters** (Dockerfile, función Inngest, storage, idempotente) | repo aparte | 🔵 La pieza grande. **Stack sin decidir** — registrarlo en DECISIONS antes de escribir código. |
| **P3** | Capa de tiles en el front | terra-admin | 🟡 parcial |
| **P4** | Catálogo de capas (decisión §6.1) + consumo en el front | Geocore o aparte | 🟡 |
| **P5** | Gateway/Traefik + rate-limiting N-5 (solo si se cumplen los criterios §4) | infra | 🚫 fuera de scope |

**Por qué P2b antes de P2c:** TiTiler no depende del worker. Con un COG de ejemplo subido a
mano se prueba el flujo de tiles completo —incluido que `GeoData__MapTokenSecret` esté bien
configurado de **los dos lados**— sin escribir una línea del worker. Es barato y valida un
contrato que hoy es puro papel.

---

## 8. Próximo incremento concreto — 🔵 fuera de Geocore

> **Geocore ya cumplió su parte del pipeline** (2026-08-17): crea entidades desde KML,
> publica los eventos, expone capas/mediciones/jobs y emite tokens de mapa. Lo que falta
> está en otros repos. La deuda que queda en Geocore (ver `HANDOFF.md`) **no bloquea nada**
> de lo que sigue.

**El orden y el porqué están en §7.** Resumen: storage → TiTiler con un COG de prueba →
worker.

**Antes de escribir el worker, fijar estos cuatro contratos.** Cambiarlos después deja de ser
trabajo local y pasa a ser coordinación entre dos repos, sin compilador que agarre el error
— que es exactamente lo que pasó con el rename de columnas de DECISIONS #15:

1. **Eventos que consume.** `terra/rancho.created` y `terra/parcela.created` disparan el procesamiento; los 5 `terra/parcela.*.requested` son pedidos puntuales. Forma del payload: `TEAM.md` → Eventos publicados. Coordenadas como `CoordinateDto`, nunca ValueTuples.
2. **Esquema de `geodata`.** El worker escribe `layers` y `measurements` y actualiza `processing_jobs`. **Columnas en snake_case** — nombre definitivo desde 2026-08-17.
3. **Convención de llaves del bucket.** `Layer.StorageKey` es la ruta del COG. Geocore la lee para armar la URL de TiTiler y **nunca toca el bucket** (§5).
4. **Idempotencia obligatoria.** Inngest reintenta, y el backoff de `InngestEventPublisher` puede publicar dos veces el mismo evento. Tolerar duplicados por `ranchoId`+fecha.

**Decisiones abiertas que conviene cerrar antes de empezar**, no durante:
- **Storage:** MinIO self-host vs Supabase Storage (DEPLOYMENT_DECISION §1 eligió Supabase para la demo; si va MinIO, revertirla formalmente). No afecta al código de Geocore.
- **Stack del worker:** no está definido en ningún lado. El trabajo es GDAL/COG/rásters. Registrarlo en `DECISIONS.md` antes de la primera línea de código.

Contexto completo de cómo se llegó hasta acá: [`SESSION_2026-08-17_kml_y_cierre_de_deuda.md`](SESSION_2026-08-17_kml_y_cierre_de_deuda.md).
