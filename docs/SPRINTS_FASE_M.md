# FASE M por sprints — el backlog

> Armado el 2026-09-12. Diseño: [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md).
> Decisiones:
> - `DECISIONS #31`: histórico mensual en GEE (✅);
> - `#32`: el pipeline;
> - `#33`: se reescribe la capa de satélite, no el servicio (✅).
>
> Cómo se trabaja cada tarea: [`WORKFLOW.md`](WORKFLOW.md).
>
> **Vista como página:** <https://claude.ai/artifact/F4ixwF2wXa3bc6SSNpmDze>.
> La fuente es [`TABLERO_FASE_M.html`](TABLERO_FASE_M.html). Al cerrar cada sesión:
> 1. actualizar ahí el objeto `ESTADO` para que coincida con la columna de estado de abajo;
> 2. **correr `node docs/check_tablero.js`**;
> 3. republicarla pasando esa URL (`Artifact` con `url`).
>
> Si no se pasa la URL, se crea otra página.
>
> **El paso 2 no es opcional.** La página es un HTML con un `<script>` que la dibuja
> entera: una llave de menos sigue siendo HTML válido y se publica sin ruido, pero el
> script no parsea y **la página sale vacía**. Pasó con la versión 27 (2026-09-20), y el
> comprobador es lo que la habría agarrado.
>
> **Este archivo es el tablero.** Al cerrar una sesión se actualiza la columna de
> estado: ⬜ pendiente · 🟡 en curso · ✅ hecho · ⛔ bloqueado. La sesión siguiente
> arranca por la primera tarea ⬜ del sprint en curso, salvo las que el orden sugerido
> adelanta: M.3.1 y M.8.2 van "temprano", en la sesión 3, antes de M.2. La página del
> tablero lo respeta desde el 2026-09-15.

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
  - Se mergea con el CI en verde. Desde el 2026-09-14 hay CI en los cuatro repos
    (`DECISIONS #34`, [`CI.md`](CI.md)), y los PR se abren con `gh`, con la sesión
    de la cuenta `TechSupportKaapeh`.
- **Las tareas 👥 no frenan la sesión:** corren en paralelo, y la sesión sigue por la
  primera tarea ⬜ que no sea del equipo.
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
| M.0.1 | CI: Python 3.13, `pip install --only-binary=:all:`, `pytest tests`, `pip-audit`, y `ruff` estricto **solo sobre `pipeline/`**. El resto arrastra más de 200 hallazgos históricos: la regla es no sumar | worker | S | verde en `main`; un PR con un test roto sale rojo | ✅ 2026-09-14 · geeworker2#1; el test roto, #2 |
| M.0.2 | CI: `dotnet build`, `dotnet test` y `dotnet list package --vulnerable` (falla si hay alguno) | Geocore | S | ídem | ✅ 2026-09-14 · Geocore#1; el test roto, #2 |
| M.0.3 | Dependencias y lint, para que el CI pueda ser compuerta: `shadcn` a `devDependencies`, subir `react-router-dom`, y los 9 errores de lint viejos | panel | M | `npm audit --omit=dev` sin altas; `eslint src` sin errores | ✅ 2026-09-14 · Terra-admin#1 |
| M.0.4 | CI: `tsc`, `eslint`, `npm run build` y `npm audit --omit=dev --audit-level=high` | panel | S | verde en `main` | ✅ 2026-09-14 · Terra-admin#2 |
| M.0.5 | CI: `pytest` | tileserver | S | verde en `main` | ✅ 2026-09-14 · terra-tileserver#1 |
| 👥 M.0.6 | Proteger `main` en los cuatro repos (checks obligatorios, sin push directo) y activar "Wait for CI" en cada servicio de Railway | GitHub, Railway | S | un push directo a `main` se rechaza | ⬜ · **postergada por la demo** (decisión del usuario, 2026-09-15); ese día la API no mostraba rulesets ni protección |

**Cierre (2026-09-14):** M.0.1 a M.0.5 están hechas. Se mergearon por PR con el CI
en verde, el push a `main` salió verde en los cuatro repos, y los PR con un test
roto a propósito salieron rojos en el paso de tests. Crónica:
[`SESSION_2026-09-14_el_ci_en_los_cuatro_repos.md`](SESSION_2026-09-14_el_ci_en_los_cuatro_repos.md).

**Falta 👥 M.0.6**, con los pasos de [`CI.md`](CI.md). Hasta entonces el CI avisa
pero no frena. `geeworker2`, `Terra-admin` y `terra-tileserver` son públicos, y ahí
la protección funciona con el plan gratis. Geocore es privado y pide GitHub Pro.
"Wait for CI" en Railway no depende del plan.

El riesgo del scope `workflow` no se dio: `gh auth setup-git` dejó la credencial con
ese scope.

---

## M.1 — Núcleo del pipeline (sin GEE)

**Objetivo:** las piezas puras del diseño (`ARQUITECTURA` §3.2, §3.4 y §4), con
tests, sin tocar la red.

| | Tarea | T | Aceptación | Estado |
|---|---|---|---|---|
| M.1.1 | `pipeline/periodos.py`: `Mes` (`AAAA-MM`, orden, anterior y siguiente), `rango(mes)` semiabierto y `meses_cerrados(hoy, n)` | S | tests: diciembre a enero, bisiestos, "hoy" el día 1; los 24 meses de un alta el 2026-09-12 van de 2024-09 a 2026-08 | ✅ 2026-09-14 · geeworker2#4 |
| M.1.2 | `pipeline/indices.py`. **Las fórmulas son texto** (`"(NIR - RED) / (NIR + RED)"`) sobre bandas con nombre, en reflectancia 0–1. v1: NDVI (vegetación), EVI (vegetación densa), NDRE (clorofila), NDMI (humedad) | M | un evaluador de Python calcula la misma fórmula contra valores de referencia de la literatura; nombres únicos; bandas que existen en S2; rangos coherentes | ✅ 2026-09-14 · geeworker2#5 |
| M.1.3 | `pipeline/estadisticas.py`: mediana, media, mín, máx, p10, p90 y desvío, con el nombre con que GEE devuelve cada una | S | tests de las claves de salida con uno y con varios índices | ✅ 2026-09-15 · geeworker2#6 |
| M.1.4 | `pipeline/receta.py`: `Receta` inmutable y `RECETA_VIGENTE = "s2-mensual-v1"`. v1: los 4 índices, las 7 estadísticas, cobertura mínima 0,3, 24 meses, escala 10 m, `max_prob` 45 y dilatación 50 m. Se valida contra los registros | S | un test fija la huella de la receta: cambiar un parámetro sin subir la versión lo rompe | ✅ 2026-09-15 · geeworker2#7 |
| M.1.5 | Importar `pipeline` no toca la red | S | test que lo importa en un proceso con el socket saboteado (el patrón de `DECISIONS #24`) | ✅ 2026-09-15 · geeworker2#8 |
| M.1.6 | **Estadísticas declarativas y reducción fusionada.** El registro describe cada estadística (tipo y percentil) en vez de guardar una fábrica. `plan_de_reduccion()` junta los percentiles en **un** `ee.Reducer.percentile`, y mín y máx en un `minMax`: hoy mediana, p10 y p90 arman tres histogramas. La huella cubre el reductor entero, y `estadisticas.py` deja de importar `ee` | S | tests del plan: la receta v1 arma un solo histograma; las claves de salida siguen sin chocar; v1 re-fijada, porque no escribió filas | ✅ 2026-09-15 · geeworker2#11 |
| M.1.7 | **La receta fija lo que el pedido a GEE podría cambiar sin avisar.** El `remuestreo` de las bandas de 20 m (v1: `nearest`, lo de hoy; `bilinear` se compara en M.2.6) y la distancia de sombra en píxeles, que sale de `escala_m`. Hoy es `1000 / 10` fijo: a 60 m se proyectaba hasta 6 km. Política escrita: `bestEffort=False`, GEE nunca sube la escala solo | S | tests de la receta; `DECISIONS` escrito | ✅ 2026-09-15 · geeworker2#12 |

**Reabierto el 2026-09-15** con M.1.6 y M.1.7, que salen de la revisión de eficiencia
del código nuevo contra el viejo (crónica de la sesión, §6). Van antes de M.2 porque
cambian lo que M.2.2 y M.2.4 van a usar, y porque `s2-mensual-v1` todavía no escribió
ninguna fila: corregirla ahora es gratis.

**Cerrado otra vez el mismo día.** M.1.6 (#11) y M.1.7 (#12) están hechas. La suite
pasó de 352 a 386. Decisión: `DECISIONS #36`. `s2-mensual-v1` se re-fijó dos veces
sin pasar a v2; desde M.4.3, cuando escriba su primera fila, queda congelada.

**Cierre del sprint:** `pytest` verde y `ruff` limpio en `pipeline/`.

**Cierre (2026-09-15):** M.1.1 a M.1.5 están hechas, cada una por PR con el CI en
verde. La suite pasó de 203 a 352, y el ruff estricto de `pipeline/` quedó limpio
sin excepciones nuevas. Decisión: `DECISIONS #35`. Crónica:
[`SESSION_2026-09-15_el_nucleo_del_pipeline.md`](SESSION_2026-09-15_el_nucleo_del_pipeline.md).

Tres cosas que salieron y cambian lo que sigue:
- **La receta v1 lleva cuatro campos más** que los de M.1.4: los dos parámetros de
  sombras de la máscara de hoy (NIR oscuro 0,15 y 1000 m) y las dos colecciones.
  M.2.2 los toma de la receta, no los escribe como constantes.
- **`ARQUITECTURA` §3.2 estaba mal en la razón.** Los métodos de `ee.Reducer`
  existen antes de `ee.Initialize()`; lo que falla es llamarlos. Está corregido.
- **M.2.6 suma dos confirmaciones:** que GEE da 0 al dividir por cero, y la clave
  de `reduceRegion` con una banda y varias salidas.

---

## M.2 — Etapas y borde, contra GEE real

**Objetivo:** el pipeline calcula el mes de una parcela y el COG de un rancho, y
los números se validan contra la realidad (`WORKFLOW` §6).

| | Tarea | T | Aceptación | Estado |
|---|---|---|---|---|
| M.2.1 | `etapas/fuente.py`: S2 SR HARMONIZED con su probabilidad de nubes, filtro por ROI y mes, y **bandas ÷ 10000** | S | revisado en M.2.6 | ✅ 2026-09-15 · `DECISIONS #38`; verificado con `pytest --gee` |
| M.2.2 | `etapas/nubes.py`: s2cloudless con sombras y parámetros de la receta, **sin** el descarte por pasada (`ARQUITECTURA` §8). **La distancia de sombra va en píxeles y sale de la receta (M.1.7), y la máscara se arma en una proyección fija a `escala_m`**: `directionalDistanceTransform` mide en píxeles del pedido, así que sin eso el COG y las estadísticas podrían salir con máscaras distintas | S | ídem | ✅ 2026-09-15 · `DECISIONS #39`. Sin la proyección fija, el descarte pasa de 0,95 a 0,35 entre 10 y 60 m. **La máscara de hoy descarta el 95 % de una escena con 32 % de nubes** |
| M.2.3 | `etapas/compuesto.py`: índices por pasada, después la mediana por píxel, y la banda `n_obs`. **Juntar primero las teselas de una misma pasada:** donde dos teselas de S2 se solapan (T14QKH y T14QLH en el Bajío), cada pasada viene dos veces, y `n_obs` saldría el doble (sondeo del 2026-09-15). **Decidido por el usuario el 2026-09-15: un mosaico por pasada** (misma fecha y mismo satélite) antes del compuesto | S | ídem | ✅ 2026-09-15 · `DECISIONS #40`. Se agrupa por `DATATAKE_IDENTIFIER`: sobre el ROI de prueba, 16 imágenes son 8 pasadas |
| M.2.4 | `etapas/reduccion.py`: el reductor combinado **desde `plan_de_reduccion()` (M.1.6)** y la cobertura (píxeles válidos sobre el total). **`bestEffort=False`** y `maxPixels` explícito: si GEE no puede a `escala_m`, falla. Una clave que falta en la respuesta es un error, no un nulo | S | ídem | ✅ 2026-09-15 · `DECISIONS #41`. Primeros números reales: NDVI mediana 0,278, cobertura 0,955, observaciones 2 |
| M.2.5 | `productos.py` y `ejecucion.py`. `ejecucion.py` hace tres cosas: el deadline con `ee.data.setDeadline`; traducir errores (sin memoria → no reintentable, concurrencia o timeout → reintentable); y contar las llamadas | M | tests puros de la traducción de errores | ✅ 2026-09-16 · `DECISIONS #42`. El plazo exige `ee.Initialize()` hecho; el mes de una parcela cuesta **una** llamada |
| M.2.6 | `scripts/check_pipeline_real.py`: 3 parcelas reales × 3 meses (uno de lluvia), lado a lado con el código de hoy; tiempo por mes; el COG de un rancho validado con `rio-cogeo`. Suma: `nearest` contra `bilinear` en NDRE y NDMI; comparar con tolerancia de float32 (~1e-6), no la de los tests; confirmar que GEE da 0 al dividir por cero y la clave de `reduceRegion` con una banda y varias salidas. **Suma (`DECISIONS #39`): la máscara de hoy contra la misma con una erosión de 2 px antes de dilatar (en una escena, 0,95 contra 0,50 de descarte). El usuario decidió el 2026-09-15 elegir con los números de M.2.6, con la erosión como favorita; va antes de M.4.3. **Y suma `DECISIONS #41`: qué hacer con EVI, que se sale de [−1, 1] en el 0,012 % de los píxeles (mínimo −6,4) porque su denominador puede acercarse a cero; acotarlo o aceptar que su mín y su máx no son informativos.** Desde `DECISIONS #43` las dos comparaciones se hacen **cambiando la receta** (`nubes_erosion_px` y `acotar_indices`), no parcheando código: hoy valen 0 y `False`, que es lo de la capa vieja | M | resultados anotados en la sesión | ✅ 2026-09-17 · `DECISIONS #44` y `#45`. Corrido sobre 3 parcelas reales × 3 meses: **la compuerta pasa** (NDVI 0,30 en seco y 0,56 en lluvias, cobertura coherente, 2–8 s por mes contra los 60 que pide). En un mes la capa vieja no devolvió nada y el pipeline cubrió el 93,6 %. Quedan sin medir el tiempo con una parcela grande y el COG con `--cog` |

👥 **Para M.2.6:** 3 parcelas reales (ids o KML). Las credenciales de GEE ya están en
el `.env` local del worker: el round-trip respondió el 2026-09-15.

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
| M.3.1 | Migración `MedicionesMensuales`, con su script SQL idempotente en `docs/sql/`. **Se hace temprano** (sesión 3). Tres tablas: | M | `dotnet test`; el script revisado | ✅ 2026-09-15 · Geocore#6 |
| | · `measurements`: `valor` nullable, `estadisticas` jsonb, `cobertura`, `observaciones`, `receta` | | | |
| | · `processing_jobs`: `periodo` y el índice único por tipo, entidad y periodo | | | |
| | · `layers`: `receta` y `estadisticas` | | | |
| 👥 M.3.1b | Aplicar la migración en GeoData | S | `pg_indexes` y columnas verificadas | ✅ 2026-09-15 · la aplicó el usuario; los cinco CHECK verifican `ok` |
| M.3.2 | `GET /api/measurements` devuelve estadísticas, cobertura y receta; `parcelaId` acepta una lista; techo para `limit` (A04) | M | tests | ✅ 2026-09-17 · Geocore#15, `DECISIONS #28`. `estadisticas` va como objeto; la lista acepta el parámetro repetido y las comas; techo 5000, con el `limit` aplicado y `truncado` en la respuesta. El mismo techo en `/api/layers` |
| M.3.3 | `GET /api/ranchos/{id}/metricas?indice=&desde=&hasta=`: el promedio ponderado por área de las parcelas, más la fracción del área con dato | M | tests con parcelas sin dato en un mes | ✅ 2026-09-17 · Geocore#16, `DECISIONS #29`. **El promedio divide por el área con dato, no por la total**, y la fracción va al lado. Rango en meses, tope de 60 |
| M.3.4 | `check_schema.py` del worker valida las columnas nuevas: el contrato entre repos | S | corre contra la base real | ✅ 2026-09-17 · geeworker2#29, `DECISIONS #46` del worker. **43 de 43 en ok** contra PostGIS con las migraciones aplicadas; 33 de 43 y código 1 con la migración anterior |
| 👥 M.3.5 | Borrar las filas por pasada de prueba (SQL listo en la sesión) | S | — | ✅ 2026-09-19 · lo hizo el usuario con `DELETE FROM geodata.measurements WHERE receta IS NULL`: el pipeline siempre escribe `receta`, así que eso son exactamente las filas viejas (12 visibles, de 2024). Quedan las altas mensuales, de 96 filas cada una |

**M.3.1 hecha el 2026-09-15** (sesión 3, Geocore#6, `DECISIONS #25` de Geocore). Tres
cosas que cambian lo que sigue:
- **El índice único son dos índices parciales**: uno por parcela y otro por rancho. Uno solo
  no chocaba en los jobs de rancho, porque llevan `parcela_id` nulo y Postgres cuenta los
  NULL como distintos. M.5.2 inserta y deja que el índice rechace el duplicado.
- **Hay CHECK en la base:** `cobertura` en [0, 1], `observaciones` ≥ 0, `estadisticas`
  tiene que ser un objeto JSON, y `periodo` va como `AAAA-MM`. M.4.3 escribe la cobertura como
  fracción, no como porcentaje: con 73, el insert falla.
- **`observaciones` es `double precision`**, porque una mediana puede caer entre dos enteros.

👥 **M.3.1b:** aplicar `geocore/docs/sql/2026-09-15_MedicionesMensuales.sql` en GeoData y
correr `…_verificar.sql`, que es de solo lectura. Todas las filas tienen que decir `ok`. Se
probó contra PostGIS 15 en un contenedor local.

**Cierre del sprint (2026-09-17, sesión 6).** M.3.2, M.3.3 y M.3.4 están hechas, cada una por
PR con el CI en verde. Queda 👥 M.3.5, que no frena nada. Crónica:
[`geocore/docs/SESSION_2026-09-17_sesion_6_la_api_mensual.md`](../../geocore/docs/SESSION_2026-09-17_sesion_6_la_api_mensual.md).
Tres cosas que cambian lo que sigue:

- **La métrica del rancho divide por el área con dato, no por la total** (`DECISIONS #29` de
  Geocore). M.7.4 dibuja el valor **con** su `fraccionArea` al lado: sin ese número, un mes
  nublado se lee como un mes malo.
- **`/api/measurements` devuelve `{ data, limit, truncado }`.** Agrega campos, no cambia los
  que estaban, y el techo es 5000 (`#28`). M.7.3 mira `truncado` antes de dibujar.
- **`check_schema.py` es ahora una compuerta**, no un listado: sale con código 1 si falta una
  columna (`DECISIONS #46` del worker). **Correrlo antes de M.4.3**, que es la tarea que
  escribe la primera fila mensual.

---

## M.4 — Las altas sobre el pipeline

**Objetivo:** una parcela nueva trae sus 24 meses; un rancho nuevo, sus 24 mapas.

| | Tarea | T | Aceptación | Estado |
|---|---|---|---|---|
| M.4.1 | **Decidir la forma de la key con el tenant adentro**, por ejemplo `tenants/{tenantId}/ranchos/{id}/{indice}/{AAAA-MM}.tif`, antes de escribir un solo COG nuevo. Es la base para cerrar A01 (M.8.1) sin mover objetos después | S | `DECISIONS` escrito | ✅ 2026-09-17 · geeworker2#31, `DECISIONS #47`. **`tenants/{t}/ranchos/{r}/{receta}/{indice}/{AAAA-MM}.tif`**: la receta va adentro porque el tileserver cachea los tiles como `immutable` por un año. La arma `pipeline/claves.py`, con los uuid canónicos |
| M.4.2 | `handlers/`: sacar de `inngest_handlers.py` el wrapper de jobs, las claves y las utilidades, **sin cambiar comportamiento** | M | la suite entera verde sin tocar un test | ✅ 2026-09-17 · geeworker2#32, `DECISIONS #48`. Ningún test tocado. `claves_de_capa()` se quedó con la capa vieja (las mensuales están en `pipeline/claves.py`). **El Dockerfile copia `pipeline/` y `handlers/`**, y `test_dockerfile.py` lo cuida; la imagen se construyó y arrancó en local |
| M.4.3 | Escritura: upsert de la fila mensual y `insert_layer` con receta y estadísticas | S | tests con conexión falsa | ✅ 2026-09-17 · geeworker2#33, `DECISIONS #49`. Las filas sin `valor` se escriben; `estadisticas` va siempre. Probado contra PostGIS con las migraciones: `check_schema` 43/43, upsert idempotente, el CHECK de cobertura rechaza el lote entero |
| M.4.4 | `process_parcela` sobre el pipeline: el plan y 24 steps `mes-AAAA-MM`, con bitácora y avance | M | tests con el step que imita al SDK | ✅ 2026-09-18 · geeworker2#36, `DECISIONS #50`. Mismo `fn_id`; el alta vieja se borró. **Probado contra GEE real: con cobertura 0, GEE omite las claves** y el alta no terminaba nunca; arreglado en `leer`. Las 3 parcelas: 24 meses en 95–103 s. **`s2-mensual-v1` congelada** |
| M.4.5 | `process_rancho` sobre el pipeline: 24 COG de NDVI | M | ídem | ✅ 2026-09-18 · geeworker2#37, `DECISIONS #51`. Un mes sin un píxel limpio no tiene COG (decisión del usuario). **El GeoTIFF de GEE no declara nodata**: lo enmascarado llegaba como NDVI 0; ahora es la máscara del COG. Se borró `register_layer`. Contra lo real: 23 COG en 226 s, válidos |
| M.4.6 | De punta a punta: un rancho y una parcela reales desde el panel. Se miran las filas, el COG por el tileserver (`check_prod.py`) y la pestaña Procesos | S | anotado en la sesión | ✅ 2026-09-19 · en producción: jobs `completed`, 96 filas con `s2-mensual-v1`, 24 capas `mensual` con la key del tenant, `check_prod.py` 7 de 7 y **`nodata_type: Mask`** en el tileserver (el arreglo de `#51` visto en producción). Salieron dos arreglos de configuración (`Inngest__EventKey` de Geocore e `INNGEST_BASE_URL` del worker) y cuatro tareas: M.4.7 a M.4.10 |
| M.4.7 | 🆕 El job se cierra aunque la corrida termine fuera del handler: `on_failure` y las cancelaciones de Inngest | S | tests; probado contra un Inngest real | ✅ 2026-09-18 · `DECISIONS #52`. Cancelada una alta a mano, a los 5 s el job está `failed`. Los jobs ya colgados los cierra el equipo con el SQL de #52 (👥) |
| M.4.8 | 🆕 El worker atiende varios steps a la vez: la ruta de Inngest en un pool de hilos, el pool de conexiones y el plazo de GEE seguros entre hilos | S | tres altas a la vez contra un Inngest real | ✅ 2026-09-19 · `DECISIONS #53`. **3 altas a la vez: 66 s → 23 s.** El SDK corría los handlers síncronos dentro del event loop (corrige `#26`) |
| M.4.9 | 🆕 El arranque avisa si `INNGEST_BASE_URL` (o las otras URLs que lee el SDK) está en producción | S | tests con control negativo | ✅ 2026-09-19 · `DECISIONS #54`. Fue lo que rompió el sync del 2026-09-18; el reporte decía "en producción no se usa" |
| M.4.10 | 🆕 Función de diagnóstico: N steps vacíos que miden la espera de Inngest entre steps | S | corrida contra un Inngest real | ✅ 2026-09-19 · `DECISIONS #55`. Línea de base local: 0,12–0,20 s entre steps. **En producción, steps vacíos esperan de 38 a 75 s al azar: la espera es de Inngest Cloud**, no del worker. 👥 Escribirle a su soporte |

**Riesgo:** los runs en vuelo durante el deploy rehacen sus steps. Es idempotente.

**Sesión 8 (2026-09-18): M.4.4 y M.4.5 hechas**, cada una por PR con el CI en verde. Crónica:
[`SESSION_2026-09-18_sesion_8_las_altas_II.md`](SESSION_2026-09-18_sesion_8_las_altas_II.md).
Tres cosas que cambian lo que sigue:

- **`s2-mensual-v1` está congelada** desde el merge de geeworker2#36: cambiar un parámetro es
  la v2.
- **Probar contra GEE real sacó dos bugs que los tests no veían** (`DECISIONS #50` y `#51`):
  con cobertura 0 GEE **omite** las claves, y su GeoTIFF **no declara nodata**. Los dos
  estaban en supuestos escritos que nadie había mirado. M.4.6 los vuelve a mirar en producción.
- **El worker ya no emite eventos** y registra 7 funciones: `register_layer` se fue con el
  `process_rancho` viejo. Las dos altas no tienen límite de concurrencia: va con M.5.3.

**Sesión 8, segunda parte (2026-09-18 y 19): M.4.6 a M.4.10. El sprint M.4 queda cerrado.**
Las altas corren en producción con los datos verificados de punta a punta. Lo que cambia lo que
sigue:

- **La espera entre steps es de Inngest Cloud** (`DECISIONS #55`): steps vacíos esperan de 38 a
  75 s al azar, en local 0,1 a 0,2 s. No afecta los datos. 👥 Escribirle a su soporte; si no se
  resuelve, un piloto de Inngest autohosteado.
- **El plan Hobby de Inngest**: 5 steps a la vez en toda la cuenta y 50.000 ejecuciones al mes (un
  alta son ~27). **M.5.3 fija la concurrencia en 5 o menos**.
- **El worker atiende en paralelo** desde M.4.8 (`#53`): antes, de a un step por vez.
- **Un job ya no queda colgado** si su corrida se cancela o muere (`#52`). 👥 Los que ya estaban
  colgados se cierran con el SQL de `#52`.
- **`GET /api/layers` no filtra por rancho**: M.7.4 lo va a necesitar.

**Sesión 7 (2026-09-17): M.4.1, M.4.2 y M.4.3 hechas**, cada una por PR con el CI en verde.
Crónica: [`SESSION_2026-09-17_sesion_7_las_altas_I.md`](SESSION_2026-09-17_sesion_7_las_altas_I.md).
Tres cosas que cambian lo que sigue:

- **La key lleva la receta** además del tenant (`DECISIONS #47`). M.4.5 arma la key con
  `claves_cog_mensual()`, nunca a mano. M.8.1 compara `tenants/{tenantId}/` **con la barra**.
- **El 👥 del Dockerfile ya no hace falta:** copia `pipeline/` y `handlers/` desde M.4.2, y el
  CI se pone en rojo si un paquete que `app` importa no se copia.
- **`s2-mensual-v1` se congela al mergear M.4.4**, que es el primer handler que escribe una
  fila real. Si hay que re-fijarla, es antes de ese merge.

---

## M.5 — El cierre de mes

**Objetivo:** cada mes cerrado se procesa solo (`DECISIONS #23` de Geocore).

| | Tarea | Repo | T | Aceptación | Estado |
|---|---|---|---|---|---|
| M.5.1 | La lógica pura del reconciliador: qué publicar dado "hoy", las entidades activas y los jobs existentes. Día 5; republicar un `pending` de más de 1 h; no reintentar un `failed` | Geocore | M | tests con `TimeProvider` falso | ✅ 2026-09-19 · Geocore#23, `DECISIONS #30`. **Antes del día 5 el objetivo es el mes anterior al último**, no "ninguno", y **una entidad creada después del mes se saltea**: su alta ya lo cubrió (decisiones del usuario). El reloj es un parámetro, no un `TimeProvider` |
| M.5.2 | `CierreMensualService` (`BackgroundService` + `PeriodicTimer`): pagina entidades, crea jobs, publica `terra/*.mes.requested` | Geocore | M | tests; dos instancias no duplican (índice único) | ✅ 2026-09-19 · Geocore#24, `DECISIONS #31`. **Arranca apagado** (`CierreMensual__Habilitado`), publica con el `JobId` como id de evento, y el duplicado lo rechaza el índice. 3 tests contra PostGIS real |
| M.5.3 | Handlers `terra/parcela.mes.requested` y `terra/rancho.mes.requested`, con límite de concurrencia en Inngest | worker | M | tests | ✅ 2026-09-19 · geeworker2#47, `DECISIONS #56`. Un solo step, reusando `procesar_mes` del alta; el mes sale del evento. **Las cuatro funciones de GEE comparten una cola de 5** (mismo `hash`, verificado contra un Inngest real, igual que la deduplicación por id) |
| M.5.4 | `POST /api/admin/procesos/reprocesar` (TerraAdmin): republica el alta de un tenant o de una entidad. Sirve para las parcelas que ya existen | Geocore | M | tests de la política | ✅ 2026-09-19 · Geocore#26, `DECISIONS #32`. Alcance entidad, rancho (con sus parcelas) o tenant; **saltea lo que ya tiene un alta viva** y **rechaza el pedido entero pasadas 200 entidades**. Si el evento no sale, el job queda `failed` para no tapar el reintento |
| M.5.5 | Verificación: forzar el mes objetivo en desarrollo y reiniciar Geocore dos veces | todos | S | sin duplicados, sin meses perdidos | ✅ 2026-09-19 · en local, con Geocore, el worker, Inngest y PostGIS: 3 jobs en el primer arranque, **0 en los dos siguientes**, el `pending` de 2 h republicado sin duplicar, y los jobs borrados a mano recreados. 8 filas con 8 claves distintas. **Las dos republicaciones no dispararon ninguna corrida.** 👥 Falta prenderlo en Railway |

**Sesión 9 (2026-09-19): M.5.1, M.5.2 y M.5.3 hechas**, cada una por PR con el CI en verde.
Crónica: [`geocore/docs/SESSION_2026-09-19_sesion_9_el_cierre_de_mes.md`](../../geocore/docs/SESSION_2026-09-19_sesion_9_el_cierre_de_mes.md).
Tres cosas que cambian lo que sigue:

- **El cierre arranca apagado.** 👥 `CierreMensual__Habilitado=true` en el Geocore de Railway,
  **después** de que el worker con M.5.3 esté desplegado. Va con M.5.5, que es la tarea que lo
  verifica; hasta entonces las dos funciones nuevas del worker no reciben ningún evento.
- **La deduplicación por id de evento de Inngest está verificada** (`#56` del worker): un evento
  reenviado con el mismo id no dispara otra corrida. Es lo que hace segura la republicación de un
  `pending`. Se verificó contra el dev server; en Cloud lo mira M.5.5.
- **M.5.4 se adelanta a M.5.5** si hay que reprocesar algo a mano: reprocesar desde el panel es la
  forma de reintentar un alta cancelada o fallida sin entrar a Inngest.

**El sprint M.5 quedó cerrado el mismo 2026-09-19, en una sesión y no en dos**, con M.5.4
(Geocore#26) y M.5.5. Lo que queda es del equipo, y es lo único que separa al cierre de mes de
estar andando en producción:

- 👥 **`CierreMensual__Habilitado=true` en el Geocore de Railway**, después de que el worker con
  M.5.3 esté desplegado. El reporte de arranque dice en qué estado quedó.
- 👥 Con eso prendido, mirar en **Inngest Cloud** que un evento con id repetido tampoco dispare
  una corrida. En el dev server ya se vio: de tres envíos del mismo evento, corrió uno.

Y una limitación que conviene tener escrita: **republicar un `pending` sólo ayuda si el evento
nunca llegó a Inngest**, que es el agujero de dual-write que `DECISIONS #23` quería cerrar. Si el
evento llegó y la corrida murió, lo que cierra el job es `on_failure` (M.4.7), no la
republicación.

---

## M.6 — Limpieza

**Objetivo:** que el worker termine con menos código que al empezar.

| | Tarea | Repo | T | Aceptación | Estado |
|---|---|---|---|---|---|
| M.6.1 | Borrar la capa vieja (`ARQUITECTURA` §9) y dejar de crear `sentinel2_dates` | worker | M | suite verde; líneas netas negativas | ✅ 2026-09-20 · geeworker2#54, `DECISIONS #59`. Se borró **lo que no tiene llamador**; el resto de §9 lo sostienen los handlers a demanda y se va con M.6.2. Suite 612 → 618. Código de producción −61 líneas; con el test nuevo, el repo sube 91 |
| 👥 M.6.1b | `DROP TABLE geodata.sentinel2_dates` | GeoData | S | — | ✅ 2026-09-20 · lo aplicó el usuario |
| M.6.2 | Los handlers a demanda. 👥 Confirmar si el front de tenants usa `timeseries`, `dates`, `stats` y `export`; borrarlos o rehacerlos. El mapa a demanda pasa al pipeline | worker, Geocore | M | decisión escrita | 🟡 2026-09-20 · geeworker2#57 y Geocore#37, `DECISIONS #60` y `#35`. **El usuario decidió borrarlos.** Se fueron los cuatro handlers, sus cinco endpoints —suma `timeseries-on-the-fly`, que habría quedado fabricando jobs `pending` eternos— y todo lo que sólo ellos sostenían: **−467 líneas de producción**. **Falta M.6.2b**, el mapa a demanda al pipeline |
| M.6.2b | 🆕 El mapa a demanda pasa al pipeline (`ARQUITECTURA` §9) | worker, Geocore | M | el COG a demanda sale con nodata y con el tenant en la key | ✅ 2026-09-20 · geeworker2#59 y Geocore#39, `DECISIONS #61` y `#36`. **La capa vieja desapareció**: `ee_client.py` quedó en 57 líneas (sólo `init_ee`) y `services/inngest_handlers.py` se borró. El mapa es de **un mes** (decisión del usuario) y su key cuelga de `tenants/`, que es lo que desbloquea M.8.1. −622 líneas de producción |
| M.6.3 | Un solo `EncolarAsync` en vez de los cinco `Request*Async` | Geocore | S | tests | ⛔ **vaciada por M.6.2**: de los cinco quedó `RequestHeatmapAsync` sola, y sin duplicación no hay nada que unificar. Se revisa después de M.6.2b |
| M.6.4 | Excepciones explícitas en lugar de `except Exception` en lo que queda | worker | M | ruff sin hallazgos nuevos | ✅ 2026-09-20 · geeworker2#60, `DECISIONS #62`. **Ruff marcaba 8 de los 35**: no marca los que relanzan, que son el patrón correcto. Tres estaban en `utils_pkg/cache.py` e `io.py`, sin un solo llamador: se borraron. Los cinco de `db_repository.py` son telemetría y ahora lo dicen con su `noqa`. **El invariante quedó en un test**, porque el CI sólo corre ruff sobre `pipeline/` |
| M.6.5 | 🆕 El mapa del rancho, de los **cuatro** índices: un COG por índice y por mes | worker | S | tests; las keys no se pisan | ✅ 2026-09-20 · geeworker2#52, `DECISIONS #58`. Decisión del usuario. No toca la receta, pero el mes pasa de 1 a 4 descargas y de 2 a 5 llamadas a GEE. **Lo que está en producción sigue con sólo NDVI hasta que se reprocese** |

**Sprint cerrado el 2026-09-20 (sesión 10).** El objetivo era «que el worker
termine con menos código que al empezar», y se cumplió con margen: **−1.150 líneas de
producción** entre M.6.1, M.6.2 y M.6.2b.

| | Qué se fue |
|---|---|
| M.6.1 | Lo que ya no tenía llamador, y la escritura de `sentinel2_dates` (−61) |
| M.6.2 | Los cuatro handlers a demanda, cinco endpoints, y todo lo que sostenían (−467) |
| M.6.2b | El mapa a demanda al pipeline, y con él la capa vieja entera (−622) |
| M.6.4 | `utils_pkg/cache.py` e `io.py`, sin llamadores |

**Ya no queda nada anterior al pipeline mensual.** `ee_client.py` pasó de 436 líneas a 57 —sólo
`init_ee`—, `services/inngest_handlers.py` se borró, y la lista de funciones vive en
`handlers/registro.py`. El worker registra 7 funciones y **todo lo que escribe cuelga de
`tenants/{t}/`**, que es lo que M.8.1 necesitaba.

**M.6.4 cerró con menos código, no con más.** La tarea suponía angostar 35 `except Exception`;
correr `ruff --select BLE` mostró que sólo 8 eran del tipo que tapa bugs —ruff no marca los que
relanzan—, y tres de esos estaban en dos módulos **sin un solo llamador**, que se borraron. Los
otros cinco son telemetría y se justificaron con su `noqa`. El invariante quedó en un test,
porque el CI sólo corre ruff sobre `pipeline/`.

**M.6.3 es lo único que no se hizo, y no se va a hacer así:** M.6.2 la vació. De los cinco
`Request*Async` sobrevivió `RequestHeatmapAsync`, y sin duplicación no hay refactor.

**Tres cosas que salieron de hacerlo, y no estaban en la tarea:**
- **`timeseries-on-the-fly` habría quedado fabricando jobs `pending` eternos.** Publicaba un
  evento cuyo único oyente se estaba borrando. Apareció revisando **qué publica cada controlador
  de Geocore**, no leyendo la lista. Lo cuida ahora un test que compara los disparadores del
  worker contra lo que Geocore publica, en las dos direcciones.
- **`cloudPct` nunca hizo nada.** El worker lo recibía y no lo aplicaba. Se fue del contrato.
- **`test_health_responde_mientras_corre_un_step` era flaky y estaba rojo en `main`**
  (geeworker2#55). Apareció por correr la suite antes de empezar.

**Lo que M.6.2b deja abierto para M.8.1:** los rásters a demanda **que ya están** en el bucket
siguen en `parcelas/{id}/`, sin tenant y sin nodata, con sus filas en `layers`. Hay que decidir
si se mueven, se borran, o se deja que el token los rechace.

---

## M.7 — Panel

**Objetivo:** ver lo que el pipeline produce, y cargar geometría sin sufrir.

| | Tarea | T | Aceptación | Estado |
|---|---|---|---|---|
| M.7.1 | Un `Selector` que envuelve el Select de Base UI con `items` obligatorio: cierra la clase de bug del 09-12 | S | los selects del panel lo usan | ✅ 2026-09-20 · Terra-admin#12. Los **13** desplegables lo usan, y **eslint prohíbe importar el Select crudo** fuera del propio `Selector.tsx`: la prop es obligatoria (olvidarla es `TS2741`) y el componente dibuja las opciones desde esa misma lista, así etiqueta y opción no pueden divergir. Seis de los trece no tenían `items` |
| M.7.2 | Partir `RanchosPage` en hooks de datos y componentes, con pedidos cancelables | M | sin cambio visible; build | ✅ 2026-09-20 · Terra-admin#13. De **438 líneas a 217**. Lo cancelable no era orden: elegir el tenant A y enseguida el B podía dejar **los ranchos de A con B elegido**. Y salió un segundo bug de la misma familia: cambiar de tenant no limpiaba el rancho elegido |
| M.7.3 | La serie mensual de una parcela: mediana con banda p10–p90, meses de baja cobertura marcados, huecos en los nulos, selector de índice | M | prueba en `vite dev` con datos reales | ✅ 2026-09-20 · Terra-admin#14. **Panel lateral desde la tabla de parcelas** (decisión del usuario), no una pestaña nueva. El gráfico del prototipo se mudó a `components/series/` y lo usan los dos. **Sin librería de gráficos**, confirmado al abrir la tarea |
| M.7.4 | El mapa del rancho por mes: selector de mes, COG con token de mapa, parcelas encima y la métrica del rancho. **Desde M.6.5 hay un COG por índice**, así que suma el selector de índice | M | ídem | ✅ 2026-09-20 · Terra-admin#15. Panel lateral con deslizador de meses (con pausa de 250 ms) y selector de índice. **La métrica va arriba del mapa con la fracción del área con dato al lado**: el promedio divide por el área con dato (`#29`), así que el número solo se lee mal. `useMapToken` y el deslizador quedan compartidos con el catálogo de Tiles |
| M.7.5 | Editor de geometría: dibujar con clics, el polígono en vivo, el rancho de referencia y aviso de vértices afuera. ¿Capa satelital? Confirmar la licencia | M | ídem | ✅ 2026-09-20 · Terra-admin#17 y **#18**. **Sin dibujar con clics** (decisión del usuario el mismo día: se hizo y se sacó) y **sin capa satelital** (falta la licencia, 👥). Queda lo que el editor no tenía: el rancho de referencia de fondo, con el mapa encuadrado en él, y los vértices afuera en rojo y nombrados, sin bloquear —la autoridad es el 422 de Geocore |
| M.7.6 | Primeros tests (vitest) de `src/lib/` | S | corren en el CI | ✅ 2026-09-20 · Terra-admin#16. **51 tests** donde no había ninguno, en un paso propio del CI del panel (con la confirmación del usuario: M.0.6 sigue sin tocarse). Entorno `node`, sin jsdom. **Control negativo corrido**: tres invariantes rotos a propósito, cada uno puso en rojo su test y sólo ése |

**Cierre (2026-09-20, sesión 11): el sprint entero en una sesión, no en tres.** Las seis
tareas por PR con el CI en verde (Terra-admin#12 a #17).

Lo que deja, más allá de las pantallas:

- **El bug de los ids ya no se puede cometer.** M.7.1 no arregló seis desplegables: cerró
  la clase. La prop obligatoria la agarra el compilador y el import prohibido, el lint,
  y las dos compuertas corren en el CI. Control negativo corrido.
- **Los pedidos cancelables taparon una carrera visible en pantalla**, y de paso mostraron
  que el mismo descuido estaba en otros dos lados (el rancho elegido que sobrevivía al
  cambio de tenant, y el `fetch` sin cancelar del prototipo de Diagnóstico).
- **El panel tiene tests por primera vez**, y el CI los corre.
- **Tres piezas quedaron compartidas en vez de duplicadas**: el gráfico de la serie, el
  token de mapa y el deslizador de meses. Las tres tenían ya un segundo llamador el mismo
  día en que se escribieron.

**Lo único que quedó afuera: la capa satelital del editor** (👥, la licencia). El resto de
M.7.5 no dependía de ella.

---

## M.8 — Seguridad (de la revisión del 2026-09-12)

**Objetivo:** cerrar lo que la revisión OWASP dejó en rojo y naranja.

| | Tarea | Repo | T | Aceptación | Estado |
|---|---|---|---|---|---|
| M.8.1 | 🔴 **A01:** el token de mapa lleva `tenant_id`, y TiTiler exige que la key empiece con ese tenant (depende de M.4.1) | Geocore, tileserver | M | un token de otro tenant da 403 | ✅ 2026-09-20 · Terra-admin#20, Geocore#46 y terra-tileserver#3; `DECISIONS #42` y `#43` de Geocore. **Tres repos y tres PR, en el orden en que se pueden desplegar**: el panel manda `X-Tenant-ID`, Geocore firma el claim, y el tileserver —el que **exige**— va último. Los **185** tests del tileserver incluyen uno que levanta la app entera con TiTiler montado: es lo único que prueba el cableado |
| M.8.2 | Proyecto `Geocore.API.Tests` con `WebApplicationFactory`: políticas `TerraAdmin` y `TerraStaff`, 401 y 403. **Temprano** (sesión 3) | Geocore | M | en el CI | ✅ 2026-09-15 · Geocore#7 |
| M.8.3 | **A04:** rate limiting (ASP.NET `RateLimiter`) en escritura y admin | Geocore | S | tests | ✅ 2026-09-21 · Geocore#48, `DECISIONS #44`. **Un limitador global con una regla**, no un atributo por endpoint: un `POST` nuevo queda limitado por existir, y un test lo comprueba contra los endpoints que la app registra. Tres niveles por usuario y por minuto —`Escritura` 120, `Admin` 20, `Encolado` 10— y el limitador va **antes** de `TenantMiddleware`, que consulta la base en cada request |
| M.8.4 | **A09:** registro de auditoría de acciones privilegiadas: roles, altas de usuarios, reprocesos | Geocore | M | tests | ✅ 2026-09-21 · Geocore#49, `DECISIONS #45`. Tabla `audit_log` y un middleware **por fuera de `ExceptionMiddleware`**: adentro, una acción que lanza no dejaba fila, que son justo los casos interesantes. Un 403 y un 429 también dejan rastro. 👥 Falta aplicar la migración |
| M.8.5 | Retención de `processing_job_events` | Geocore | S | decisión y job | ✅ 2026-09-24 · Geocore#50, `DECISIONS #46`. **90 días**, un `DELETE` por día, y **arranca prendida**: apagada de más no hace nada, y una retención que hay que acordarse de prender no es una retención. Se borra la bitácora, **no** los jobs |

**Cierre de M.8.1 (2026-09-20, sesión 12).** Era el 🔴 más viejo —de la revisión del
2026-09-12— y lo que esperaba era M.6.2b: hasta que **todo** lo que el worker escribe no
colgó de `tenants/{t}/`, no había con qué comparar.

El agujero: el token decía **quién** pedía tiles y no decía **cuáles**. Validados firma,
emisor, audiencia y `type: map-access`, el tileserver servía cualquier COG del bucket, y la
key viaja a la vista en la URL del tile. Un usuario con su token legítimo y la key de otro
tenant veía los rásters de ese otro tenant.

Lo que quedó, más allá del 403:

- **El orden de despliegue es el que hizo que no se rompiera nada**, y es el opuesto al de
  M.5.5: **el que exige va último**. El panel mandando `X-Tenant-ID` a la Geocore de antes
  no cambia nada; el tileserver exigiendo el claim antes de que Geocore lo firme deja
  **todos** los mapas en 403.
- **Los dos controles del tileserver dejaron de ser independientes.** El de ruta recibe el
  de token por `Depends`, y se le pasa **la misma función, no otra igual**: FastAPI la
  resuelve una vez por pedido y no hay dos lecturas del claim que puedan discrepar.
- **400 y 403 dicen cosas distintas**: una ruta que no puede pedir nadie (SSRF) y una que
  no puede pedir *éste*. Y el `..` se mira **antes** que el tenant, o
  `tenants/{mío}/../{ajeno}/` pasaría.
- **El cableado es lo que ningún test unitario prueba.** `tests/test_app_tenant.py` levanta
  la app con TiTiler montado y sin red, y cubre `/cog` **y** `/mosaic`. Con la comparación
  por tenant sacada caen 12 tests; y hay un control negativo del control, porque un
  validador que rechazara todo dejaría verdes a los otros once.
- **Lo que no alcanza, escrito**: los assets listados *dentro* de un MosaicJSON (hallazgo
  **T-3**). Desde M.8.1, lo que se saltearía ahí es el aislamiento entre tenants, no sólo
  el filtro anti-SSRF.
- **Dos consumidores que nadie había mirado**: `scripts/check_prod.py` —que ahora verifica
  el 403 desde afuera, sin necesitar un segundo token— y el piloto del front, que pedía el
  token sin `X-Tenant-ID` y habría quedado en 400 apenas se desplegó Geocore.

**Las capas viejas ya no se pueden servir** (`DECISIONS #43`, decisión del usuario): las
keys `parcelas/{id}/…` y `ranchos/{id}/…` no tienen tenant. **Se borran**, objetos y filas,
con `geocore/docs/sql/2026-09-20_capas_sin_tenant.sql`. 👥, y **después** del deploy.

**Cierre del sprint M.8 (2026-09-24, sesión 13).** Las tres que faltaban entraron en una
sesión, cada una por PR con el CI en verde. Geocore pasó de **452 a 559 tests**.

Lo que dejan, más allá de cerrar los hallazgos:

- **La misma forma para los dos controles nuevos: una regla, no una llamada por endpoint.**
  El rate limiting decide el nivel mirando método y ruta; la auditoría decide qué registrar
  igual. En los dos casos **un endpoint nuevo queda cubierto por existir**, y en los dos hay
  un test que recorre los endpoints que la aplicación **registra de verdad** en vez de una
  lista escrita a mano. Es la lección de M.7.1 aplicada a propósito.
- **El lugar en el pipeline es parte del diseño, y los dos tienen su test.** El limitador va
  **antes** de `TenantMiddleware` —que consulta la base en cada request autenticado— así que
  lo rechazado no llega a la base; la auditoría va **por fuera de `ExceptionMiddleware`**,
  porque adentro una acción que lanza no deja fila. Mover cualquiera de los dos pone tests en
  rojo.
- **Los tests encontraron tres cosas que la revisión no habría encontrado leyendo:** que
  `Encolado` (30) era más permisivo que `Admin` (20) contradiciendo la regla de «gana el más
  estricto»; que la auditoría adentro de `ExceptionMiddleware` perdía justo los 409 y los 500;
  y que **`config.GetValue<int?>` lanza** ante un valor que no entiende, con lo cual un typo
  en una env var habría tumbado la API al arrancar.
- **Lo que M.8 no cubre, y quedó escrito en vez de tapado:** el login y la edge function
  `create-user` son superficie de Supabase, no de Geocore; las lecturas fuera de `api/admin`
  no tienen techo; el estado del limitador vive en el proceso, así que con más de una
  instancia el techo se multiplica; y los assets de un MosaicJSON siguen sin pasar por la
  comparación de tenant (T-3).

**👥 Queda una cosa de producción:** aplicar la migración de `audit_log`
(`geocore/docs/sql/2026-09-21_RegistroDeAuditoria.sql`), sobre la base de **identidad**. Hasta
entonces cada acción privilegiada deja un `LogError` en Railway en vez de una fila, y la API
sigue funcionando.

**M.8.2 hecha el 2026-09-15** (sesión 3, Geocore#7, `DECISIONS #26` de Geocore). La
cadena JWT de `Program.cs` corre de verdad, y solo la clave es de prueba. Son 35 tests de
401, 403, `TenantMiddleware` y el token de mapas. Salió un bug: un `app_metadata` que no era
un objeto daba 500. Está arreglado. M.3.2 y M.8.3 suman sus tests sobre esa fábrica.

---

## M.9 — Analítica y futuro (sin orden fijo)

| | Qué | Qué toca | Estado |
|---|---|---|---|
| M.9.0 | **Medir cuántas pasadas limpias hay por mes y qué cobertura tiene cada una**, sobre la parcela. Es la compuerta de todo lo que sigue | worker | ✅ 2026-09-24 · geeworker2#70, `DECISIONS #66`. **Mediana de 3 pasadas limpias por mes** sobre 3 parcelas reales × 24 meses: no son «1 o 2», así que **el bloque no se cierra acá**. Y **0 de 72 meses** en que el compuesto llegue al umbral y ninguna pasada sola: «por pasada puro» confirmado, la tabla aparte no se abre. `PREGUNTAS_ABIERTAS` B-3, **cerrada** |
| M.9.0b | **El agrupamiento es un dato de la receta**: la ventana deja de estar cableada al mes. Refactor **sin cambio de comportamiento** | worker | ⬜ |
| M.9.0c | **`s2-pasada-v2`**: estadísticas **sólo** por pasada, ráster mensual, y el umbral de cobertura al leer. Convive con `s2-mensual-v1` | worker, Geocore | ⬜ |
| M.9.0d | El panel: eje de fechas y el interruptor mensual / por pasada | panel | ⬜ |
| M.9.1 | El cultivo en la parcela, y la métrica del rancho agrupada por cultivo (`DECISIONS #22` de Geocore) | Geocore, panel | ⬜ |
| M.9.2 | `analitica/`: anomalía contra la mediana histórica del mismo mes, tendencia y alerta de caída | worker o Geocore | ⬜ |
| M.9.3 | Más índices, una entrada de registro cada uno: SAVI (cultivo joven, suelo expuesto), GNDVI o CIre (clorofila), MSI (estrés hídrico), NDWI (agua) | worker | ⬜ |
| M.9.4 | Sentinel-1 (radar) para los meses de lluvia | worker | ⬜ |
| M.9.5 | El mes en curso, como provisorio | worker, panel | ⬜ |

> **Las seis decisiones de diseño de este bloque están tomadas** (2026-09-25, `DECISIONS #63`):
> se mide antes de decidir, el agrupamiento es un dato de la receta, **por pasada puro** —sin
> columna `ventana` y sin migración—, el ráster sigue mensual, y **el umbral de cobertura se
> aplica al leer**. La cobertura se mide **sobre la parcela, nunca sobre el rancho**.
>
> **Y el número ya está** (2026-09-24, `DECISIONS #66`): mediana de **3 pasadas limpias por
> mes**, y **0 de 72 meses** en que el compuesto llegue al umbral de cobertura y ninguna pasada
> sola llegue. Las dos cosas que decide: el bloque **sigue** —no son «1 o 2»— y **por pasada
> puro se confirma**, sin la tabla aparte que `#63` dejaba prevista.

**Por qué el `0`.** M.9 no tiene orden fijo, pero estas cuatro sí van antes que el resto:
**M.9.2** (anomalía y tendencia) y **M.9.5** (el mes en curso) mejoran mucho con una serie
más fina, y hacerlas primero sobre 24 puntos mensuales es trabajo que después se rehace. Los
ids llevan `0` y sufijo en vez de renumerar, como `M.3.1b` y `M.6.2b`.

### La ventana de observación: por qué se reabre

**Lo que hay hoy.** Por cada mes, GEE enmascara nubes y sombras, calcula el índice **por
pasada** y después toma la **mediana por píxel** entre todas. De ahí salen las estadísticas
de la parcela y el COG del rancho. Las pasadas sueltas no se guardan: se calculan y se
tiran. Es la opción B de `DECISIONS #31`, decidida el 2026-09-12, que reemplazó a `#19`
—"se guarda por pasada, no por composite"—.

**La decisión fue correcta por lo que miraba, y se llevó puesto algo que no miraba.** La
tabla de `#31` compara: 146 descargas contra 24, TiTiler abriendo 3 a 6 COG por tile contra
uno solo, y el MosaicJSON con tres preguntas abiertas encima. Todo eso es del **ráster**, y
todo eso sigue siendo cierto. Pero en ese diseño las estadísticas salen de la misma imagen
que el mapa, así que **los números viajaron con la decisión del ráster sin que nadie hiciera
la cuenta por separado** — y para los números no hay descargas, ni MosaicJSON, ni TiTiler: es
un `reduceRegion`.

**Lo que cuesta, en concreto:**

- **Los eventos desaparecen.** Granizo, helada, una falla de riego: una caída de diez días la
  absorbe la mediana del mes.
- **Dos meses con el mismo nombre no son comparables.** Uno con seis pasadas limpias y otro
  con dos dan un número que se llama igual. `n_obs` lo registra, pero el valor no lo
  incorpora.
- **La cobertura mínima descarta el mes entero** cuando alcanzaría con descartar las pasadas
  malas. De ahí salen buena parte de los `valor = null`.
- **La mediana supone que lo que varía dentro del mes es ruido.** Para un mosaico eso es
  cierto; para algo que crece, la variación intramensual **es la señal**, y la mediana de las
  pasadas cae cerca de mitad de mes con un error que depende de cuándo hubo cielo.

**Cuánto de esto importa depende de qué hay en las parcelas**, y es lo primero a contestar. Con
pastura —que es lo que sugieren los números de M.2.6, NDVI 0,30–0,35 en seca y 0,54–0,56 en
lluvias— la dinámica es más lenta que un mes y lo que se pierde es sobre todo la detección de
eventos. Con cultivo anual, donde el NDVI se mueve de 0,3 a 0,8 en tres semanas, se está
borrando la parte informativa de la curva.

**Lo que este bloque NO propone:**

- **no propone dejar de componer.** En lluvias una pasada sola no da nada: M.2.6 midió un mes
  en que la capa vieja no devolvió nada y el pipeline cubrió el 93,6 %. Componer ahí gana;
- **no propone cambiar el ráster.** El mapa sigue siendo un compuesto mensual, por los mismos
  motivos de `#31`;
- **no reabre el mapa a demanda**, que es de un mes y no de un rango por decisión del usuario.
  El agrupamiento lo haría posible; que se use o no es otra conversación.

### Lo que un `GROUP BY` sobre las pasadas NO puede rehacer

Si se guarda por pasada, la serie mensual —o decadal, o de cualquier rango— sale de agregar al
leer, sin volver a GEE. Ésa es la ganancia entera. Pero **lo que se guarda por pasada no son
píxeles: son estadísticas ya reducidas sobre la parcela**, y de ahí salen dos límites.

**El menor: las medianas no componen.** La mediana de las medianas por pasada no es la mediana
del compuesto. Con la media casi se salva; con `p10` y `p90`, no. Es una diferencia chica.

**El que importa: cada pasada cubre un pedazo distinto de la parcela.** En un mes con ocho
pasadas de las cuales seis tienen media parcela tapada, cada una de esas seis filas es una
estadística **del 30 % que estaba despejado** — y si el pedazo despejado es siempre la misma
ladera, las seis repiten el mismo sesgo. El compuesto, en cambio, toma para cada píxel la
mediana de las pasadas en que **ese** píxel estaba limpio, así que cubre casi toda la parcela.
**Eso no se puede reconstruir agregando números**: el valor que tenía el píxel tapado el día 7
nunca se guardó.

Por eso la fila mensual no sería el mismo dato otra vez: **es otra medición, que sólo existe
si se calcula**.

**Decidido el 2026-09-25 (`DECISIONS #63`): por pasada puro.** Sin migración, una sola clase
de fila y el rango flexible gratis. Se acepta no tener el compuesto, y **M.9.0 es la red**:
si la cobertura por pasada viene alta, el compuesto no estaba haciendo nada que el `GROUP BY`
no haga. Si viniera parcial, la fila mensual iría en **una tabla aparte** y no en una columna
—dos semánticas en dos tablas no se mezclan por olvido—.

### La cobertura se mide sobre la parcela, nunca sobre el rancho

Decisión del usuario, 2026-09-24, y vale para todo el bloque. **Una pasada que tapa medio
rancho puede ser perfecta para una parcela**, y evaluarla a nivel rancho la descartaría para
todas. El umbral tiene que aplicarse con la granularidad con la que el dato se consume, que es
la parcela: es la fila de `measurements` y es el nivel al que `cobertura_minima` ya trabaja
hoy.

El pipeline actual ya respeta el principio en dos lugares y hay que no perderlo: `nubes.py`
enmascara **sin descartar pasadas** —una pasada parcial aporta donde está limpia— y
`cobertura_minima` es "la fracción de **la parcela**". Lo que M.9.0 agrega es medir esa
cobertura **por pasada**, que hoy no existe: se calcula sobre el compuesto.

**Y de ahí salió una decisión** (`DECISIONS #63`), que es el mismo argumento un paso más
allá: hoy `cobertura_minima` **descarta al escribir**, y eso es otra reducción con pérdida
antes de guardar — la que no se puede deshacer. **El umbral pasa a aplicarse al leer**:
guardando cada pasada con su cobertura, "descartar lo que no llega al 30 %" es un `WHERE`, y
el día que 0,3 resulte mal puesto se cambia el número y no el histórico.

**Consecuencia para el panel y la API:** `/api/measurements` tiene que aprender a agregar —una
cadencia como parámetro—, y lo que hoy es `valor = null` por cobertura baja deja de existir.

### Qué hace cada tarea

**M.9.0 — medir (S, compuerta). ✅ Hecha el 2026-09-24** (`DECISIONS #66`). El escalón 6 de
`scripts/check_pipeline_real.py --pasadas`, sobre 3 parcelas reales × los 24 meses de la
receta, más el Bajío como segunda geografía. La decisión quedó escrita en
`PREGUNTAS_ABIERTAS` B-3, que **se cerró**.

Lo que dio, y lo que decide cada número:

- **mediana de 3 pasadas limpias por mes** (media 2,76, máximo 8; 54 % de los meses con 3 o
  más). No son «1 o 2»: **el bloque no se cierra**, y M.9.0b, M.9.0c y M.9.0d siguen. La
  estacionalidad es la que se esperaba y más suave que la hipótesis: 3,2–5,5 en seca contra
  1,0–1,5 en el pico de lluvias, no «5–6 contra 1»;
- **0 de 72 meses** en que el compuesto llegue a la cobertura mínima y ninguna pasada sola
  llegue. Es el número que cierra B-3: **guardar por pasada no deja sin valor a ningún mes que
  hoy lo tenga**, así que la tabla aparte de `#63` no se abre;
- **el compuesto agrega 0,0000 de cobertura sobre la mejor pasada sola en la mediana**, y más
  de 0,05 en 13 de 72 meses (18 %), casi todos de mayo a julio. Eso es lo que se acepta: en el
  pico de lluvias la serie por pasada describe el pedazo despejado;
- **recomponer el mes agregando al leer se aparta 0,008 de NDVI** (p90 0,029, máximo 0,089).
  «Las medianas no componen» es cierto y es **un orden de magnitud menor** que la variación
  intramensual que se gana, que tiene mediana 0,065 y máximo 0,336.

**M.9.0b — el agrupamiento (M, sin cambio de comportamiento).** Ver
[`ARQUITECTURA_PIPELINE.md` §3.5](ARQUITECTURA_PIPELINE.md). Hoy "el mes" está cableado en
cinco lugares: `periodos.Mes`, el `median()` de `compuesto()`, la `fecha` de `filas.py`, el
`{AAAA-MM}` de `claves.py` y el `periodo` de los jobs. La tarea convierte eso en **un dato de
la receta**. **Termina cuando `s2-mensual-v1` produce exactamente las mismas filas que antes**
—control negativo obligatorio, comparando filas antes y después—. Vale la pena **aunque nunca
se cambie la cadencia**: saca un supuesto escondido y es lo que hace barato cualquier
respuesta.

**M.9.0c — `s2-pasada-v2` (M).** La receta nueva agrupa **por pasada** para las estadísticas y
**por mes** para el ráster. Convive con v1 porque la receta ya va en la key del COG y en cada
fila. **Por defecto, sólo por pasada**: así no hay migración —la clave `(parcela, índice,
fecha)` sirve tal cual, con la fecha de adquisición— ni dos clases de fila que distinguir. Si
M.9.0 muestra pasadas parciales, se suma la fila mensual del compuesto y ahí sí hace falta una
columna que diga a qué ventana pertenece cada fila.

**M.9.0d — el panel (M).** El eje pasa a ser una fecha y no un índice de mes, y aparece el
interruptor. La serie ya sabe dibujar huecos, así que el cambio es del eje, no del gráfico.

---

## Decisiones que el backlog necesita, y cuándo

| Decisión | Antes de | Estado |
|---|---|---|
| `#31` histórico mensual en GEE | M.1 | ✅ 2026-09-12 (opción B) |
| La receta v1: 4 índices, 7 estadísticas, cobertura 0,3, 24 meses | M.1.4 | ✅ 2026-09-12 |
| `#33` se reescribe la capa, no el servicio | M.1 | ✅ 2026-09-12 |
| La forma de la key con tenant | M.4 | ✅ 2026-09-17 · `DECISIONS #47` |
| Los handlers a demanda | M.6.2 | 👥 |
| La capa satelital en el editor (licencia) | M.7.5 | 👥 |
| Los rásters viejos, fuera de `tenants/` | M.8.1 | ✅ 2026-09-20 · se borran con sus filas (`DECISIONS #43` de Geocore) |
| **¿La ventana de observación sigue siendo el mes?** (`PREGUNTAS_ABIERTAS` B-3) | M.9.0c | ✅ 2026-09-25 · **por pasada puro** (`DECISIONS #63`), y **confirmado con el número el 2026-09-24** (`#66`): la fila mensual del compuesto no hace falta. B-3 **cerrada** |
| Retención de los objetos de MinIO (`PREGUNTAS_ABIERTAS` C-5) | — | ✅ 2026-09-25 · sistemático para siempre, a demanda 90 días (`DECISIONS #65`) |
| Qué hacer con el hallazgo T-3 del tileserver | — | ✅ 2026-09-25 · se borra el router `/mosaic` (`DECISIONS #64`) |
