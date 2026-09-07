# Sesión 2026-08-17 — Import de KML y cierre de la deuda de GeoData

> Contexto cargado para retomar. Estado permanente del proyecto: [`HANDOFF.md`](HANDOFF.md).
> Este archivo cubre **qué pasó en esta sesión y por qué**, no el estado general.
> Sesión anterior: [`SESSION_2026-08-14_geodata_deploy.md`](SESSION_2026-08-14_geodata_deploy.md).

## Dónde quedó todo

**Diez commits, empujados y desplegados.** Working tree limpio. Railway sano.

| Commit | Qué trae |
|---|---|
| `e0c16c5` | `feat(kml)` — import de KML parseado en el servidor |
| `e7c78bc` | `refactor(geodata)` — columnas en snake_case + migración |
| `e2f8734` | `docs` — DECISIONS #17, flujo real del pipeline, mapa de caminos de KML |
| `4b184a9` | `docs(security)` — rotación del secreto del token de mapa |
| `fe820b9` | `docs` — cierre de la ventana del snake_case |
| `c53b9df` | `docs` — DECISIONS #18, dual-write y outbox diferido |
| `9303bff` | `fix(maps)` — el secreto faltante degrada un endpoint, no tumba el arranque |
| `5b5746a` | `refactor(kml)` — borra el endpoint de llave de bucket |
| `6be1687` | `docs` — revisión de DECISIONS #16 y checklist de deploy |
| `0877525` | `feat(events)` — reintento con backoff al publicar a Inngest |

Tests: **165 → 196**. Proyecto nuevo `Geocore.Infrastructure.Tests` para los adaptadores.

---

## Lo que se hizo y por qué

### 1. El KML lo parsea Geocore (DECISIONS #17)

Era la deuda #1 del HANDOFF. **El planteo original estaba equivocado**, y se descubrió preguntando qué hace realmente el worker: no crea ranchos, solo descarga y procesa el histórico satelital. Los dos endpoints de KML que existían le entregaban el archivo a un worker que no hace ese trabajo — uno en base64 dentro del evento, otro como llave de bucket.

La deuda pedía "inyectar un cliente de storage en Geocore". **No hacía falta ninguno**: si Geocore parsea el archivo y persiste la geometría, el KML no necesita almacenarse en ningún lado.

Detalles en [`KML_CASOS_Y_REDUNDANCIA.md`](KML_CASOS_Y_REDUNDANCIA.md).

### 2. snake_case en `geodata` (DECISIONS #15)

Migración de rename, 29 columnas, sin drops, reversible. Aplicada en la DB remota y desplegada.

**Se hizo en ese momento a propósito:** era la última ventana barata. Los nombres de columna de `geodata` son **contrato con el worker** —él escribe `layers` y `measurements`— así que en cuanto el worker exista, renombrar deja de ser local y pasa a ser un cambio coordinado entre dos repos, sin compilador que agarre el error.

### 3. El guard de arranque, arreglado por la raíz (DECISIONS #16, revisión)

`Program.cs` abortaba el arranque si faltaba `GeoData:MapTokenSecret`. La objeción del equipo era razonable: tumbar toda la API por una feature de 1 endpoint entre ~35 es desproporcionado.

Pero el guard **compensaba un defecto que estaba una línea más allá**: `MapsController` tenía un `?? DevMapTokenSecret`, o sea que sin config habría firmado tokens con un secreto que está en el repo. Se eliminó el fallback; ahora sin secreto el endpoint devuelve **503** y falla cerrado. Resuelta la causa, el abort ya no compensaba nada y pasó a ser un `LogError`.

⚠️ **Costo aceptado:** un deploy mal configurado ahora **sube** con los mapas rotos. Que el deploy esté verde ya no prueba que la variable esté cargada — hay que mirar los logs.

### 4. Secreto rotado

El valor de `GeoData__MapTokenSecret` estaba en texto plano en un doc commiteado. Se generó uno nuevo (CSPRNG, 48 chars) que vive **solo** en las env vars de Railway. El viejo sigue en el historial de git pero ya no abre nada.

Se hizo ahora porque era el momento barato: sin TiTiler desplegado, hay un solo lugar que actualizar.

### 5. Dual-write: outbox diferido, backoff implementado (DECISIONS #18)

La publicación de eventos no es atómica con la escritura en DB. Se decidió **no** hacer el Transactional Outbox todavía y mitigar con reintento (3 intentos, 1 s y 2 s).

**Por qué se puede diferir**, a diferencia del rename de columnas: el outbox es puramente interno, no cambia ningún contrato, y agregarlo en seis meses cuesta lo mismo que hoy. No hay ventana que se cierre.

⚠️ **El reintento no es tolerancia a fallos.** Si Inngest está caído más que el presupuesto, o si el proceso muere entre el `SaveChanges` y la llamada, el evento se pierde igual.

---

## Correcciones a supuestos previos

Vale registrarlas porque estaban escritas en los docs y eran falsas:

- **`HANDOFF` decía que el camino correcto del KML era el bucket.** Asumía que el worker creaba los ranchos. No los crea.
- **`OWASP_TOP10` afirmaba que el backend solo recibe coordenadas ya extraídas**, y por eso daba XXE por cubierto. Dejó de ser cierto al parsear KML en el servidor; se documentó la defensa real y sus tests.
- **`ARCHITECTURE_PLAN` §5 describía un outbox que no existe** y un "catálogo" que en la práctica terminó siendo la DB de GeoData.
- **El fallo de publicación se venía describiendo como "silencioso".** No lo es en el caso común: `InngestEventPublisher` relanza y el llamador recibe un 500. El problema real es que la entidad ya está commiteada, así que el llamador no sabe si se creó — reintenta y duplica, o no reintenta y queda huérfana.

---

## Verificado y NO verificado

**Verificado en producción** (rutas de control incluidas, para que los códigos signifiquen algo):
`/health` 200 · endpoints protegidos 401 · el endpoint borrado 404 · los nuevos de KML vivos.

**⚠️ NO verificado — las tres necesitan un request autenticado:**

1. **Que el secreto nuevo funcione.** El 401 de `/api/maps/token` no prueba nada: la autorización corre antes que el cuerpo del controller. Si estuviera mal cargado, el 503 aparece solo *con* token.
2. **Que las consultas snake_case funcionen** contra la DB.
3. **Que el import de KML ande con un archivo real** del registro catastral. El parser está escrito contra una forma *supuesta*, no contra datos reales.

Las tres se cubren con **una sola sesión desde el panel**: listar capas y subir un KML de prueba.

---

## Deuda abierta (ninguna bloqueante)

| Qué | Dónde |
|---|---|
| N+1 en el import masivo de parcelas | HANDOFF deuda 1b |
| El import no es transaccional | HANDOFF deuda 1c |
| `MapsController` sin cobertura de tests (no hay proyecto de tests para `Geocore.API`) | HANDOFF deuda 3 |
| `limit` sin techo en `/api/layers` y `/api/measurements` | HANDOFF deuda 4 |
| Transactional Outbox | DECISIONS #18 |
| 13 warnings CS8618 preexistentes en `Geocore.Domain` | — |
| La URL de tiles se arma sin `Uri.EscapeDataString` | `LayersController.cs:80-81` |

---

## Lo que sigue: contratos que el worker debe respetar

**Geocore ya cumplió su parte del pipeline.** Crea entidades, publica eventos, expone capas/mediciones/jobs y emite tokens de mapa. Lo que falta está fuera de este repo.

Antes de escribir el worker, estos contratos tienen que quedar fijos — cambiarlos después es trabajo coordinado entre dos repos:

1. **Eventos a los que se suscribe.** `terra/rancho.created` y `terra/parcela.created` disparan el procesamiento; los 5 `terra/parcela.*.requested` son pedidos puntuales. Forma del payload: `TEAM.md` → Eventos publicados. Las coordenadas van como `CoordinateDto`, nunca ValueTuples.
2. **Esquema de `geodata`.** El worker escribe `layers` y `measurements`, y actualiza `processing_jobs`. **Columnas en snake_case** desde esta sesión — ese es el nombre definitivo.
3. **Convención de llaves en el bucket.** `Layer.StorageKey` es la ruta del COG. Geocore la lee para armar la URL de TiTiler y **nunca toca el bucket**; escribe el worker, lee TiTiler (ARCHITECTURE_PLAN §5).
4. **Idempotencia obligatoria.** Inngest reintenta, y el reintento con backoff de Geocore puede publicar dos veces el mismo evento. El worker tiene que tolerar duplicados por `ranchoId`+fecha.

**Decisión de infraestructura pendiente:** `DEPLOYMENT_DECISION.md` §1 eligió Supabase Storage para la demo en lugar de self-hostear MinIO. Si finalmente va MinIO, esa decisión hay que revertirla formalmente. **No requiere cambios en Geocore** — ambos hablan S3 y Geocore solo compone un string; lo que cambia es la config de TiTiler y del worker.
