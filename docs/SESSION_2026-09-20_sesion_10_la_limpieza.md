# 2026-09-20 — sesión 10: la limpieza

> Sprint M.6. Se hicieron **M.6.1** y, después de que el usuario contestara la pregunta que
> trababa el sprint, **M.6.2**: los handlers a demanda se borran. Queda **M.6.2b**, el mapa a
> demanda al pipeline. Fuera del tablero salieron dos cosas: un test flaky en `main` y el panel
> traduciendo 5 de 11 tipos de proceso.
>
> Decisiones: `DECISIONS #59` y `#60` del worker, `#35` de Geocore.
> PR: geeworker2#54, #55, #56 y #57; Geocore#36 y #37; Terra-admin#11.
> Sesión anterior: [`SESSION_2026-09-19_sesion_9_el_cierre_de_mes.md`](SESSION_2026-09-19_sesion_9_el_cierre_de_mes.md)
> y, el mismo día 20, `geocore/docs/SESSION_2026-09-20_el_cierre_en_produccion_y_las_geometrias.md`.

## 1. Lo primero que apareció: `main` estaba rojo

Corriendo la suite antes de tocar nada —que es lo que pide el `WORKFLOW` para tener una
línea de base—, `test_health_responde_mientras_corre_un_step` falló. Corrido solo, pasa en
2,6 s.

Es un test de reloj de pared: pedía `/health` a mitad de un step falso y exigía que
contestara en **menos de 0,2 s**. Con los otros 600 tests detrás en la misma máquina, 0,2 s
no alcanza.

No lo arreglé en el momento —no era M.6.1 y mezclarlo habría ensuciado el PR—, pero sí
antes de cerrar la sesión, porque **el CI es la única compuerta de merge** mientras M.0.6
siga postergada: un test que falla al azar bloquea PR sin motivo y, peor, enseña a
reintentar sin mirar, que es cómo un rojo de verdad pasa inadvertido. Va en §4.

## 2. M.6.1 — el límite de §9 no es el que §9 dice

`ARQUITECTURA_PIPELINE` §9 lista lo que el pipeline mensual reemplaza. Lo primero que hice
fue verificar esa lista contra el código, y **no coincide**: §9 se escribió en M.0, antes de
que existieran las altas mensuales, y da por borradas siete funciones que hoy siguen
teniendo llamador.

| §9 dice borrar | Estado real |
|---|---|
| `composite_embedding`, `maskS2clouds`, `SUPPORTED_INDICES` | ✅ sin llamadores: se borraron |
| `get_sentinel2_dates`, `insert_sentinel2_date`, la tabla | ✅ la escritura se borró; la consulta a GEE queda, la usa un handler |
| `get_sentinel2_collection`, `get_sentinel2_time_series`, `compute_sentinel2_index` | ❌ **vivas**: las usan los handlers a demanda |
| `apply_scsc`, `check_roi_coverage` | ❌ **vivas**: dentro de `get_sentinel2_collection` |
| `una_por_dia` | ❌ **viva**: dentro de `get_sentinel2_time_series` |
| `process_kml` | ✅ ya borrado en E.2 |

Podría haber borrado las siete igual. No lo hice porque `apply_scsc` y el descarte por
cobertura **cambian los números** que devuelven endpoints documentados en
`api-frontend.html`: quitarlos sin avisar es cambiar en silencio lo que ve un cliente. Que
sean código malo (§8 explica por qué los dos están mal) es motivo para borrarlos con su
handler, no para cambiarles el resultado por debajo.

Ese límite quedó escrito en un test, `test_lo_que_sigue_vivo_es_de_m62`, que se pone rojo si
alguien lo cruza en cualquiera de las dos direcciones. Su docstring dice qué hacer cuando se
ponga rojo por la razón buena: borrar el test, no revivir el código.

### `init_db` se fue entero

`init_db()` hacía un `SELECT 1` y el `CREATE TABLE IF NOT EXISTS sentinel2_dates`. Borrada
la tabla, lo que quedaba era un precalentamiento **que ya estaba reemplazado**:
`registrar_conexiones()` corre inmediatamente después en el arranque, abre una conexión del
mismo pool, consulta y **reporta**. `init_db` atrapaba su propia excepción y la logueaba, así
que el `try` de `_startup()` nunca la veía: con la base caída, el arranque terminaba sin
quejarse. El comentario de `app._startup()` ya lo decía; nadie había sacado la conclusión.

### El test que hace seguro el `DROP TABLE`

👥 M.6.1b es el `DROP TABLE`. Dejar de crear la tabla es lo que lo hace seguro: mientras el
worker la creara al arrancar, el primer deploy después del `DROP` la traía de vuelta.

Por eso el test no se conforma con que falten las dos funciones: recorre el repo con `ast` y
falla si **cualquier módulo nombra la tabla en código**. Distingue código de comentario a
propósito —el comentario que explica el borrado es lo que se quiere conservar— y para
identificadores compara exacto, porque `get_sentinel2_dates` contiene la cadena y no toca la
tabla: consulta GEE. La primera versión no hacía ninguna de las dos cosas y salía roja
señalando mis propios comentarios.

### Y algo que casi se escribe mal en el doc

Al escribir `DECISIONS #59` puse el SQL de M.6.1b como `DROP TABLE IF EXISTS
sentinel2_dates;`. Antes de darlo por bueno fui a mirar la base local, donde la tabla existe:
**está en el esquema `geodata`**, y el `search_path` por defecto de ese servidor es
`"$user", public, topology, tiger`, que no lo incluye. Ese `DROP` sin calificar **no borra
nada y sale con éxito**. Corregido a `geodata.sentinel2_dates`, con la consulta para
confirmar el esquema en producción antes de correrlo.

De paso quedó verificado lo que el doc afirmaba sin pruebas: 0 claves foráneas entrantes, 0
vistas dependientes, 0 filas.

### Verificación contra lo real

El `WORKFLOW` pide que cuando una pieza habla con el mundo, al menos una verificación toque
el mundo. Contra el contenedor `terra-geodata`:

- `verificar_geodata` contra la base `geodata`: `OK … con las 2 tablas`;
- el worker levantado con `uvicorn` contra una base **vacía y descartable**: `/health` 200,
  el reporte de arranque nombra `layers, measurements` como faltantes, y
  `to_regclass('sentinel2_dates')` sigue en `NO EXISTE` después del arranque;
- control negativo del test de `ast`: con un `INSERT INTO sentinel2_dates` agregado a mano,
  sale rojo nombrando el archivo.

### Las líneas, y el criterio de aceptación

El criterio de M.6.1 era «líneas netas negativas». El código de producción baja **61
líneas**; el archivo de tests nuevo suma 149, así que **el repo sube 91**. Se cumple en el
código y no en el repo, y conviene decirlo así en vez de elegir el número que queda bien.

En el camino recorté mis propios comentarios: la primera versión tenía 26 líneas de
explicación en `ee_client.py`. El *porqué* largo va en `DECISIONS`; el código apunta ahí.

## 3. El sprint M.6 no se puede terminar, y no es sólo M.6.2

Con M.6.1 mergeada fui por la siguiente ⬜. Ahí apareció lo que cambia el plan.

**M.6.3** unifica los cinco `Request*Async` de `ParcelaService` en un `EncolarAsync`. Busqué
el patrón —cargar la parcela, autorizar, crear el job, guardar, publicar— en el resto de
Geocore: **no existe en ningún otro lado**. `SeguimientoDeProceso`, `ReconciliadorMensual`,
`ReprocesoService` y `ProcessingJobsController` crean jobs con otra forma. Si M.6.2 borra
cuatro de los cinco, el `EncolarAsync` unificado queda con un llamador y el refactor se tira.

**M.6.4** son las excepciones explícitas. 38 de los ~74 `except Exception` que quedan viven
en `ee_indices.py`, `ee_client.py`, `ee_service.py` y `export_service.py` — los cuatro
módulos que M.6.2 borra. Revisé uno por uno los que **sí** sobreviven, y la conclusión es que
no hay tarea ahí:

- los 7 de `db_repository.py` son deliberados y están explicados. El estado de un job es
  telemetría: perder la actualización no puede abortar un procesamiento que ya corrió.
  Angostarlos a `psycopg2.Error` sería una **regresión** — un `TypeError` serializando el
  `detail` pasaría a tumbar la corrida. Y los dos de los caminos de escritura de datos no
  tragan nada: hacen `rollback` y **relanzan**;
- los de `conexiones.py`, `app.py` y `check_schema.py` ya llevan su `noqa: BLE001` con el
  motivo escrito;
- los de `pipeline/ejecucion.py` son la traducción de errores de GEE, que es su trabajo.

Así que las tres cuelgan de la misma pregunta al equipo del front. Quedaron en ⛔ y no en ⬜,
y la página del tablero aprendió a no proponer una tarea bloqueada como «siguiente».

**Lo que se averiguó para esa pregunta**, revisando el código de los cuatro handlers:

- `stats` y el CSV de `export` **nunca devolvieron un dato**. Le piden a GEE el rango
  `fecha → fecha`; `filterDate` es semiabierto, así que ese rango es vacío siempre. El mismo
  bug en los dos.
- `timeseries` no es neutro: escribe filas con `receta IS NULL`, que es exactamente lo que el
  equipo borró a mano en 👥 M.3.5. Además mide a 60 m (el pipeline, a 10), tira las pasadas
  con menos del 50 % de cobertura, y calcula EVI y SAVI con constantes pensadas para
  reflectancia 0–1 sobre bandas que vienen en miles.
- `dates` es el único que el pipeline no reemplaza, pero escribía en una tabla sin lectores.
  La parte útil —«cuántas observaciones limpias tuvo este mes»— ya está guardada en la
  columna `observaciones`.
- Lo único que se pierde de verdad es la **ventana arbitraria** (pedir del 3 al 20 de marzo
  por un granizo). Las etapas del pipeline están tipadas sobre `Mes`. Pero conservar este
  código no preserva esa capacidad: la contestaría con números medidos a 60 m y un EVI mal
  calculado. Recuperarla bien es cambiar `Mes` por un rango semiabierto.

Recomendación registrada: borrarlos, avisándole antes al equipo del front que esos cinco
endpoints de `api-frontend.html` se retiran.

## 4. El test flaky (geeworker2#55)

El arreglo no fue subir el presupuesto de 0,2 s a 1 s: eso mueve el problema, no lo saca. Lo
que el test quiere afirmar no es cuánto tarda `/health`, sino **quién la atiende**.

Ahora el step se bloquea en un `threading.Event` que el test suelta después, y la afirmación
es una relación de orden: cuando `/health` contestó, `step.done()` era `False` — el step
seguía corriendo. Una máquina cargada hace todo más lento sin cambiar ese orden. Los topes
de 5 s que quedan son redes contra un cuelgue, y ningún `assert` los compara.

Y trae el control negativo que faltaba: el mismo helper con `inngest.fast_api.serve`, que
corre los handlers en el event loop, donde `/health` vuelve recién cuando el step terminó y
`step.done()` ya es `True`. Mismo escenario, misma aserción, resultado opuesto.

**Me equivoqué al escribirlo**, y vale anotarlo: la primera versión del control negativo
medía tiempo y daba 0,00 s. No era que el SDK hubiera dejado de serializar; era que un
`await asyncio.sleep(0)` no alcanza para que el step esté en vuelo, así que estaba midiendo
un `/health` contra un servidor ocioso. La lección es la de siempre acá: un control negativo
que no se ve fallar por la razón correcta no prueba nada.

## 5. El panel: 5 de 11 tipos (Terra-admin#11)

Pendiente que venía de la sesión 9. `src/lib/procesos.ts` no traducía `ParcelaMensual` ni
`RanchoMensual`, y el cierre de mes corre en producción desde el 19: **casi todas las filas
de la pestaña Procesos mostraban el tipo crudo**.

En vez de agregar los dos que el pendiente nombraba, saqué del código de Geocore todos los
`requestType` que existen. Faltaban seis. Los cuatro a demanda los agregué igual: sus jobs
viejos siguen en la tabla, y la pestaña muestra historial aunque M.6.2 retire los handlers.

`docs/PROCESOS.md` tenía la misma deuda más vieja: su tabla describía las etapas
**anteriores a M.4.4** —«8 trimestres de fechas Sentinel-2 → 12 meses de serie NDVI»—, que
dejaron de ser ciertas el 2026-09-18.

No se verificó en pantalla: el panel no tiene tests (M.7.6) y el build no prueba dibujo.

## 6. Números

| | Antes | Después |
|---|---|---|
| Suite del worker | 612 (con 1 flaky) | **619**, 21 omitidos |
| `ruff check .` en la raíz | 199 hallazgos | **187** |
| `ruff` sobre `pipeline/` | limpio | limpio |
| Código de producción del worker | — | **−61 líneas** |

CI verde en los tres PR, mergeados con `--merge`.

## 7. Lo que queda

1. 👥 **La pregunta del front** (`timeseries`, `dates`, `stats`, `export`). Traba M.6.2, M.6.3
   y M.6.4, o sea el resto del sprint.
2. 👥 **M.6.1b**: `DROP TABLE IF EXISTS geodata.sentinel2_dates;` — ya es seguro, y el nombre
   va calificado.
3. 👥 **Inngest**: el soporte sigue sin contestar por la espera entre steps (`#55`).
4. Si la sesión que viene arranca con M.6 todavía trabado, la siguiente ⬜ del tablero es
   **M.7.1**.

---

# Segunda parte: el usuario contestó, y M.6.2 se hizo

## 8. La decisión

**«Borralos».** Con eso el sprint se destrabó entero. También quedaron contestadas las otras
preguntas de la lista: se sigue en Inngest Cloud por ahora; M.0.6 y el backup de MinIO van
**después de la demo**; la consola de MinIO se deja abierta por ahora; la retención se decide
más tarde; el panel sigue sin librería de gráficos; la licencia de la capa satelital y el
número del rancho contra su mapa se ven cuando lleguen. Y **M.6.1b está hecho**: el usuario
aplicó el `DROP TABLE`.

## 9. Lo que se borró, y lo que se cayó con ello

Los cuatro handlers, sus cuatro endpoints, y todo lo que no tenía otro llamador:
`get_sentinel2_time_series` con su `add_index_band_fast`, `una_por_dia`, `get_sentinel2_dates`,
`generate_time_series_data`, `export_time_series`, `insert_measurement`, `insert_measurements`
y `round_sig`. Más el lado a lado de `check_pipeline_real.py`, que ya no tiene contra qué
comparar.

**−467 líneas de producción.** Ése es el borrado que M.6 prometía y que M.6.1 no podía dar.

Tres cosas que aparecieron al leer lo que se iba:

- **`add_index_band_fast` era la segunda copia de las fórmulas, y no coincidía con la primera.**
  EVI y SAVI con constantes de reflectancia 0–1 sobre bandas en miles: el SAVI que devolvía era,
  en la práctica, `1,5 × NDVI`.
- **`insert_measurement(s)` eran la canilla de las filas `receta IS NULL`**, las que el equipo
  borró a mano en M.3.5. Con ellas se va la última escritora de `min_val` y `max_val`.
- **`una_por_dia` promediaba dos medias espaciales parciales** de la misma pasada en el borde de
  dos teselas MGRS. El pipeline mosaica por `DATATAKE_IDENTIFIER` antes de reducir, así que el
  problema no llega a existir.

## 10. El casi-accidente, que es lo más interesante de la tarea

La lista de la tarea decía cuatro handlers y cuatro endpoints. Revisando **qué publica cada
controlador de Geocore** —no la lista— apareció un quinto:
`POST /api/processing/jobs/timeseries-on-the-fly` publica
`terra/parcela.timeseries.requested`, y el handler que se estaba borrando era su **único
oyente**.

Dejarlo vivo habría convertido ese endpoint en una fábrica de jobs `pending` eternos: el
síntoma que la pestaña Procesos marca como «En cola hace más de 10 minutos», y que ya se había
sufrido al prender el cierre de mes. **Un endpoint que fabrica jobs colgados es peor que uno que
no existe.**

De ahí salió `test_cada_evento_que_geocore_publica_tiene_oyente`, que compara los disparadores
registrados del worker contra la lista escrita de lo que Geocore publica, **en las dos
direcciones**: que no falte un oyente, y que no sobre uno — un handler que escucha un evento que
ya nadie publica es código muerto que parece vivo. No hay compilador que cruce los dos repos.
Control negativo corrido: quitando un handler de `all_functions`, sale rojo.

**La lección:** cuando se borra un consumidor, la lista de lo que hay que borrar no está en la
tarea, está en quién produce. Buscarla del lado del productor encontró lo que la tarea no decía.

## 11. Los tests borrados se verificaron antes, no después

Se fueron cinco tests con `insert_measurements` y `una_por_dia`. Antes de borrarlos hubo que
comprobar que lo que cuidaban seguía cuidado: que el lote sea una sola llamada a
`execute_values`, que las filas repetidas no lleguen a la base, que un lote vacío no abra
conexión, y que la fecha viaje como `datetime` con zona. **Todo eso está en
`test_escritura_mensual.py`**, sobre el camino que de verdad se usa. Y ahí las filas repetidas
son un `ValueError` y no un `warning`, porque `filas_del_mes` no puede producirlas.

Borrar un test sin buscar su reemplazo es cómo se pierde cobertura sin que nadie lo note.

## 12. El orden de despliegue, al revés que en M.5.5

Allá había que desplegar el worker **antes** de prender la variable de Geocore, para que hubiera
quien escuchara. Acá se está quitando, así que es al revés: **primero Geocore** (deja de
publicar), **después el worker** (deja de escuchar). Los PR se mergearon en ese orden.

## 13. Números finales de la sesión

| | Al abrir | Al cerrar |
|---|---|---|
| Suite del worker | 612, con 1 flaky | **617**, sin flaky |
| Suite de Geocore | 447 | **447** |
| `ruff check .` en la raíz del worker | 199 | **157** |
| Funciones registradas en Inngest | 11 | **7** |
| Código de producción del worker | — | **−528 líneas** (−61 en M.6.1, −467 en M.6.2) |

## 14. Lo que queda de M.6

**M.6.2b**: el mapa a demanda al pipeline. Se lleva `ee_service.py`, `export_service.py`,
`ee_indices.py`, el constructor de colecciones de `ee_client.py` (con `apply_scsc` y el descarte
por pasada) e `index_band_and_vis`. Resuelve además dos pendientes viejos: los GeoTIFF de los
on-demand siguen sin nodata, y sus keys no llevan tenant — que es lo que M.8.1 necesita.

**M.6.3 quedó vaciada**: de los cinco `Request*Async` sobrevivió uno, y sin duplicación no hay
refactor. **M.6.4 se desbloqueó y encogió**, y conviene hacerla después de M.6.2b para no
revisar dos veces.
