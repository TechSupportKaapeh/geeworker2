# FASE M por sprints — el backlog

> Armado el 2026-09-12. Diseño: [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md).
> Decisiones:
> - `DECISIONS #31`: histórico mensual en GEE (✅);
> - `#32`: el pipeline;
> - `#33`: se reescribe la capa de satélite, no el servicio (✅).
>
> Cómo se trabaja cada tarea: [`WORKFLOW.md`](WORKFLOW.md).
>
> **Vista como página:** <https://claude.ai/code/artifact/71e508b6-3583-46b4-89f8-d305c258f36f>.
> La fuente es [`TABLERO_FASE_M.html`](TABLERO_FASE_M.html). Al cerrar cada sesión:
> 1. actualizar ahí el objeto `ESTADO` para que coincida con la columna de estado de abajo;
> 2. republicarla pasando esa URL (`Artifact` con `url`).
>
> Si no se pasa la URL, se crea otra página.
>
> **Este archivo es el tablero.** Al cerrar una sesión se actualiza la columna de
> estado: ⬜ pendiente · 🟡 en curso · ✅ hecho · ⛔ bloqueado. La sesión siguiente
> arranca por la primera tarea ⬜ del sprint en curso.

## Cómo se trabaja un sprint

- **Un sprint tiene un objetivo y dura de 1 a 3 sesiones.** No se abre el siguiente
  sin cerrar el objetivo del anterior, salvo las tareas marcadas "en paralelo".
- **Una tarea entra en una sesión.** Tamaños:
  - **S**: media sesión o menos;
  - **M**: una sesión;
  - si algo no entra, se parte antes de empezarla.
- **Cada tarea pasa por las seis etapas del `WORKFLOW`** (PLAN → BUILD → EXPLAIN →
  AUDIT → DOC → VERIFY) y cumple su *Definition of Done*, además del criterio de
  aceptación propio.
- **Desde M.0, rama por tarea y PR.**
  - La rama se nombra por la tarea: `m1-2-indices`.
  - El commit cita la tarea: `feat(pipeline): M.1.2 registro de indices`.
  - Se mergea con el CI en verde. Sin `gh` instalado, el PR se abre desde GitHub web.
- **👥 = lo hace el equipo:** migraciones en producción, configuración de GitHub y
  de Railway, secrets y datos reales. La tarea deja escrito exactamente qué hacer.
- **🚦 = compuerta.** Si no pasa, se revisa el diseño antes de seguir.

## El mapa

| Sprint | Objetivo | Sesiones | Repos |
|---|---|---|---|
| **M.0** Red de seguridad | Nada llega a `main` sin tests verdes | 1 | todos |
| **M.1** Núcleo | Receta, meses y registros, probados sin GEE | 1 | worker |
| **M.2** Etapas y borde | El pipeline calcula contra GEE real, y se valida | 2 | worker |
| **M.3** Esquema y API | Geocore guarda y sirve lo mensual | 2 | Geocore |
| **M.4** Altas | Parcela y rancho nuevos con 24 meses | 2 | worker |
| **M.5** Cierre de mes | Cada mes llega solo | 2 | Geocore, worker |
| **M.6** Limpieza | Lo viejo se borra | 1 | worker, Geocore |
| **M.7** Panel | Ver series y mapas mensuales; editor de geometría | 2–3 | panel |
| **M.8** Seguridad | Cerrar A01, A04 y A09, y tests de la API | 2 | Geocore, tileserver |
| **M.9** Analítica y futuro | Cultivo, anomalías, más índices, radar | abierto | todos |

**Orden sugerido de sesiones, unas 16:**

1. M.0
2. M.1
3. M.3.1 (la migración, temprano, para que el equipo la aplique mientras corre M.2) y M.8.2 (tests de la API, antes de tocarla)
4. M.2
5. M.2
6. M.3.2 a M.3.4
7. M.4
8. M.4
9. M.5
10. M.5
11. M.6
12. M.7
13. M.7
14. M.7
15. M.8
16. M.8

---

## M.0 — Red de seguridad

**Objetivo:** que un test rojo no pueda llegar a producción. Va primero porque el
refactor es grande, y hoy todo va directo a `main` y a Railway.

| | Tarea | Repo | T | Aceptación | Estado |
|---|---|---|---|---|---|
| M.0.1 | CI: Python 3.13, `pip install --only-binary=:all:`, `pytest tests`, `pip-audit`, y `ruff` estricto **solo sobre `pipeline/`**. El resto arrastra más de 200 hallazgos históricos: la regla es no sumar | worker | S | verde en `main`; un PR con un test roto sale rojo | ⬜ |
| M.0.2 | CI: `dotnet build`, `dotnet test` y `dotnet list package --vulnerable` (falla si hay alguno) | Geocore | S | ídem | ⬜ |
| M.0.3 | Dependencias y lint, para que el CI pueda ser compuerta: `shadcn` a `devDependencies`, subir `react-router-dom`, y los 9 errores de lint viejos | panel | M | `npm audit --omit=dev` sin altas; `eslint src` sin errores | ⬜ |
| M.0.4 | CI: `tsc`, `eslint`, `npm run build` y `npm audit --omit=dev --audit-level=high` | panel | S | verde en `main` | ⬜ |
| M.0.5 | CI: `pytest` | tileserver | S | verde en `main` | ⬜ |
| 👥 M.0.6 | Proteger `main` en los cuatro repos (checks obligatorios, sin push directo) y activar "Wait for CI" en cada servicio de Railway | GitHub, Railway | S | un push directo a `main` se rechaza | ⬜ |

**Riesgo:** pushear `.github/workflows/` exige que la credencial de git tenga el
scope `workflow`. Si el push lo rechaza, es eso.

---

## M.1 — Núcleo del pipeline (sin GEE)

**Objetivo:** las piezas puras del diseño (`ARQUITECTURA` §3.2, §3.4 y §4), con
tests, sin tocar la red.

| | Tarea | T | Aceptación | Estado |
|---|---|---|---|---|
| M.1.1 | `pipeline/periodos.py`: `Mes` (`AAAA-MM`, orden, anterior y siguiente), `rango(mes)` semiabierto y `meses_cerrados(hoy, n)` | S | tests: diciembre a enero, bisiestos, "hoy" el día 1; los 24 meses de un alta el 2026-09-12 van de 2024-09 a 2026-08 | ⬜ |
| M.1.2 | `pipeline/indices.py`. **Las fórmulas son texto** (`"(NIR - RED) / (NIR + RED)"`) sobre bandas con nombre, en reflectancia 0–1. v1: NDVI (vegetación), EVI (vegetación densa), NDRE (clorofila), NDMI (humedad) | M | un evaluador de Python calcula la misma fórmula contra valores de referencia de la literatura; nombres únicos; bandas que existen en S2; rangos coherentes | ⬜ |
| M.1.3 | `pipeline/estadisticas.py`: mediana, media, mín, máx, p10, p90 y desvío, con el nombre con que GEE devuelve cada una | S | tests de las claves de salida con uno y con varios índices | ⬜ |
| M.1.4 | `pipeline/receta.py`: `Receta` inmutable y `RECETA_VIGENTE = "s2-mensual-v1"`. v1: los 4 índices, las 7 estadísticas, cobertura mínima 0,3, 24 meses, escala 10 m, `max_prob` 45 y dilatación 50 m. Se valida contra los registros | S | un test fija la huella de la receta: cambiar un parámetro sin subir la versión lo rompe | ⬜ |
| M.1.5 | Importar `pipeline` no toca la red | S | test que lo importa en un proceso con el socket saboteado (el patrón de `DECISIONS #24`) | ⬜ |

**Cierre del sprint:** `pytest` verde y `ruff` limpio en `pipeline/`.

---

## M.2 — Etapas y borde, contra GEE real

**Objetivo:** el pipeline calcula el mes de una parcela y el COG de un rancho, y
los números se validan contra la realidad (`WORKFLOW` §6).

| | Tarea | T | Aceptación | Estado |
|---|---|---|---|---|
| M.2.1 | `etapas/fuente.py`: S2 SR HARMONIZED con su probabilidad de nubes, filtro por ROI y mes, y **bandas ÷ 10000** | S | revisado en M.2.6 | ⬜ |
| M.2.2 | `etapas/nubes.py`: s2cloudless con sombras y parámetros de la receta, **sin** el descarte por pasada (`ARQUITECTURA` §8) | S | ídem | ⬜ |
| M.2.3 | `etapas/compuesto.py`: índices por pasada, después la mediana por píxel, y la banda `n_obs` | S | ídem | ⬜ |
| M.2.4 | `etapas/reduccion.py`: el reductor combinado desde el registro y la cobertura (píxeles válidos sobre el total) | S | ídem | ⬜ |
| M.2.5 | `productos.py` y `ejecucion.py`. `ejecucion.py` hace tres cosas: el deadline con `ee.data.setDeadline`; traducir errores (sin memoria → no reintentable, concurrencia o timeout → reintentable); y contar las llamadas | M | tests puros de la traducción de errores | ⬜ |
| M.2.6 | `scripts/check_pipeline_real.py`: 3 parcelas reales × 3 meses (uno de lluvia), lado a lado con el código de hoy; tiempo por mes; el COG de un rancho validado con `rio-cogeo` | M | resultados anotados en la sesión | ⬜ |

👥 **Para M.2.6:** 3 parcelas reales (ids o KML) y las credenciales de GEE en el
`.env` local.

🚦 **Compuerta antes de M.4:**
- los números tienen que ser plausibles: NDVI en [-1, 1], y la cobertura coherente
  con la estación;
- un mes de parcela tiene que tardar menos de unos 60 s.

Si no pasa, se revisa el diseño.

---

## M.3 — Esquema y API en Geocore

**Objetivo:** Geocore guarda y sirve lo mensual (`ARQUITECTURA` §6; `DECISIONS #22`
de Geocore).

| | Tarea | T | Aceptación | Estado |
|---|---|---|---|---|
| M.3.1 | Migración `MedicionesMensuales`, con su script SQL idempotente en `docs/sql/`. **Se hace temprano** (sesión 3). Tres tablas: | M | `dotnet test`; el script revisado | ⬜ |
| | · `measurements`: `valor` nullable, `estadisticas` jsonb, `cobertura`, `observaciones`, `receta` | | | |
| | · `processing_jobs`: `periodo` y el índice único por tipo, entidad y periodo | | | |
| | · `layers`: `receta` y `estadisticas` | | | |
| 👥 M.3.1b | Aplicar la migración en GeoData | S | `pg_indexes` y columnas verificadas | ⬜ |
| M.3.2 | `GET /api/measurements` devuelve estadísticas, cobertura y receta; `parcelaId` acepta una lista; techo para `limit` (A04) | M | tests | ⬜ |
| M.3.3 | `GET /api/ranchos/{id}/metricas?indice=&desde=&hasta=`: el promedio ponderado por área de las parcelas, más la fracción del área con dato | M | tests con parcelas sin dato en un mes | ⬜ |
| M.3.4 | `check_schema.py` del worker valida las columnas nuevas: el contrato entre repos | S | corre contra la base real | ⬜ |
| 👥 M.3.5 | Borrar las filas por pasada de prueba (SQL listo en la sesión) | S | — | ⬜ |

---

## M.4 — Las altas sobre el pipeline

**Objetivo:** una parcela nueva trae sus 24 meses; un rancho nuevo, sus 24 mapas.

| | Tarea | T | Aceptación | Estado |
|---|---|---|---|---|
| M.4.1 | **Decidir la forma de la key con el tenant adentro**, por ejemplo `tenants/{tenantId}/ranchos/{id}/{indice}/{AAAA-MM}.tif`, antes de escribir un solo COG nuevo. Es la base para cerrar A01 (M.8.1) sin mover objetos después | S | `DECISIONS` escrito | ⬜ |
| M.4.2 | `handlers/`: sacar de `inngest_handlers.py` el wrapper de jobs, las claves y las utilidades, **sin cambiar comportamiento** | M | la suite entera verde sin tocar un test | ⬜ |
| M.4.3 | Escritura: upsert de la fila mensual y `insert_layer` con receta y estadísticas | S | tests con conexión falsa | ⬜ |
| M.4.4 | `process_parcela` sobre el pipeline: el plan y 24 steps `mes-AAAA-MM`, con bitácora y avance | M | tests con el step que imita al SDK | ⬜ |
| M.4.5 | `process_rancho` sobre el pipeline: 24 COG de NDVI | M | ídem | ⬜ |
| M.4.6 | De punta a punta: un rancho y una parcela reales desde el panel. Se miran las filas, el COG por el tileserver (`check_prod.py`) y la pestaña Procesos | S | anotado en la sesión | ⬜ |

**Riesgo:** los runs en vuelo durante el deploy rehacen sus steps. Es idempotente.

---

## M.5 — El cierre de mes

**Objetivo:** cada mes cerrado se procesa solo (`DECISIONS #23` de Geocore).

| | Tarea | Repo | T | Aceptación | Estado |
|---|---|---|---|---|---|
| M.5.1 | La lógica pura del reconciliador: qué publicar dado "hoy", las entidades activas y los jobs existentes. Día 5; republicar un `pending` de más de 1 h; no reintentar un `failed` | Geocore | M | tests con `TimeProvider` falso | ⬜ |
| M.5.2 | `CierreMensualService` (`BackgroundService` + `PeriodicTimer`): pagina entidades, crea jobs, publica `terra/*.mes.requested` | Geocore | M | tests; dos instancias no duplican (índice único) | ⬜ |
| M.5.3 | Handlers `terra/parcela.mes.requested` y `terra/rancho.mes.requested`, con límite de concurrencia en Inngest | worker | M | tests | ⬜ |
| M.5.4 | `POST /api/admin/procesos/reprocesar` (TerraAdmin): republica el alta de un tenant o de una entidad. Sirve para las parcelas que ya existen | Geocore | M | tests de la política | ⬜ |
| M.5.5 | Verificación: forzar el mes objetivo en desarrollo y reiniciar Geocore dos veces | todos | S | sin duplicados, sin meses perdidos | ⬜ |

---

## M.6 — Limpieza

**Objetivo:** que el worker termine con menos código que al empezar.

| | Tarea | Repo | T | Aceptación | Estado |
|---|---|---|---|---|---|
| M.6.1 | Borrar la capa vieja (`ARQUITECTURA` §9) y dejar de crear `sentinel2_dates` | worker | M | suite verde; líneas netas negativas | ⬜ |
| 👥 M.6.1b | `DROP TABLE sentinel2_dates` (SQL listo) | GeoData | S | — | ⬜ |
| M.6.2 | Los handlers a demanda. 👥 Confirmar si el front de tenants usa `timeseries`, `dates`, `stats` y `export`; borrarlos o rehacerlos. El mapa a demanda pasa al pipeline | worker, Geocore | M | decisión escrita | ⬜ |
| M.6.3 | Un solo `EncolarAsync` en vez de los cinco `Request*Async` | Geocore | S | tests | ⬜ |
| M.6.4 | Excepciones explícitas en lugar de `except Exception` en lo que queda | worker | M | ruff sin hallazgos nuevos | ⬜ |

---

## M.7 — Panel

**Objetivo:** ver lo que el pipeline produce, y cargar geometría sin sufrir.

| | Tarea | T | Aceptación | Estado |
|---|---|---|---|---|
| M.7.1 | Un `Selector` que envuelve el Select de Base UI con `items` obligatorio: cierra la clase de bug del 09-12 | S | los selects del panel lo usan | ⬜ |
| M.7.2 | Partir `RanchosPage` en hooks de datos y componentes, con pedidos cancelables | M | sin cambio visible; build | ⬜ |
| M.7.3 | La serie mensual de una parcela: mediana con banda p10–p90, meses de baja cobertura marcados, huecos en los nulos, selector de índice | M | prueba en `vite dev` con datos reales | ⬜ |
| M.7.4 | El mapa del rancho por mes: selector de mes, COG con token de mapa, parcelas encima y la métrica del rancho | M | ídem | ⬜ |
| M.7.5 | Editor de geometría: dibujar con clics, el polígono en vivo, el rancho de referencia y aviso de vértices afuera. ¿Capa satelital? Confirmar la licencia | M | ídem | ⬜ |
| M.7.6 | Primeros tests (vitest) de `src/lib/` | S | corren en el CI | ⬜ |

---

## M.8 — Seguridad (de la revisión del 2026-09-12)

**Objetivo:** cerrar lo que la revisión OWASP dejó en rojo y naranja.

| | Tarea | Repo | T | Aceptación | Estado |
|---|---|---|---|---|---|
| M.8.1 | 🔴 **A01:** el token de mapa lleva `tenant_id`, y TiTiler exige que la key empiece con ese tenant (depende de M.4.1) | Geocore, tileserver | M | un token de otro tenant da 403 | ⬜ |
| M.8.2 | Proyecto `Geocore.API.Tests` con `WebApplicationFactory`: políticas `TerraAdmin` y `TerraStaff`, 401 y 403. **Temprano** (sesión 3) | Geocore | M | en el CI | ⬜ |
| M.8.3 | **A04:** rate limiting (ASP.NET `RateLimiter`) en escritura y admin | Geocore | S | tests | ⬜ |
| M.8.4 | **A09:** registro de auditoría de acciones privilegiadas: roles, altas de usuarios, reprocesos | Geocore | M | tests | ⬜ |
| M.8.5 | Retención de `processing_job_events` | Geocore | S | decisión y job | ⬜ |

---

## M.9 — Analítica y futuro (sin orden fijo)

| | Qué | Qué toca |
|---|---|---|
| M.9.1 | El cultivo en la parcela, y la métrica del rancho agrupada por cultivo (`DECISIONS #22` de Geocore) | Geocore, panel |
| M.9.2 | `analitica/`: anomalía contra la mediana histórica del mismo mes, tendencia y alerta de caída | worker o Geocore |
| M.9.3 | Más índices, una entrada de registro cada uno: SAVI (cultivo joven, suelo expuesto), GNDVI o CIre (clorofila), MSI (estrés hídrico), NDWI (agua) | worker |
| M.9.4 | Sentinel-1 (radar) para los meses de lluvia | worker |
| M.9.5 | El mes en curso, como provisorio | worker, panel |

---

## Decisiones que el backlog necesita, y cuándo

| Decisión | Antes de | Estado |
|---|---|---|
| `#31` histórico mensual en GEE | M.1 | ✅ 2026-09-12 (opción B) |
| La receta v1: 4 índices, 7 estadísticas, cobertura 0,3, 24 meses | M.1.4 | ✅ 2026-09-12 |
| `#33` se reescribe la capa, no el servicio | M.1 | ✅ 2026-09-12 |
| La forma de la key con tenant | M.4 | M.4.1 |
| Los handlers a demanda | M.6.2 | 👥 |
| La capa satelital en el editor (licencia) | M.7.5 | 👥 |
