# Sesión 2026-09-12 — La bitácora del worker

> Estado permanente en [`HANDOFF.md`](HANDOFF.md). Sesión anterior:
> [`SESSION_2026-09-11_el_arranque_que_dice_la_verdad.md`](SESSION_2026-09-11_el_arranque_que_dice_la_verdad.md).
> Del otro lado: `geocore/docs/SESSION_2026-09-12_bitacora_de_procesos.md` (la tabla, los
> endpoints y cómo aplicar la migración) y `terra-admin/docs/PROCESOS.md` (cómo se lee en el
> panel). Para retomar: `geocore/docs/PROXIMA_SESION.md`.

## Dónde quedó

Cada job escribe su bitácora en `processing_job_events`, una tabla de Geocore. La bitácora dice:
- qué etapa arrancó y sobre qué ventana de fechas;
- cuántas imágenes encontró y cuánto tardó;
- qué falló en cada intento.

El job escribe además `progress`, que hasta hoy **nunca se había escrito**: en `HEAD`, las tres llamadas a `update_processing_job` pasaban sólo el estado. El panel lo muestra en la pestaña Procesos.

Tests: 201. De ellos, 32 nuevos en `tests/test_avance_job.py`.

## 1. `services/avance_job.py`

El módulo tiene cuatro piezas:
- `seguimiento()`: lo abre el wrapper de jobs, junto al contexto de logging.
- `reportar()`
- `paso()`
- `resumir_error()`

Se rige por tres reglas, las tres por cómo ejecuta Inngest. Están en el docstring del módulo y en `DECISIONS #29`:

1. **Se reporta desde adentro de los `step.run`.** El cuerpo del handler corre de nuevo en
   cada request; una línea escrita ahí se repetiría una vez por step ya terminado.
2. **Los fallos de un step se registran adentro del step** (`paso()`): ver §3.
3. **Nunca levanta.** Es telemetría.

## 2. El histórico de una parcela, por ventanas

| Step | Qué | Avance |
|---|---|---|
| `plan-historico` | Fija "hoy" y anuncia las ventanas | 2 % |
| `query-sentinel2-dates-1` … `-8` | Fechas Sentinel-2 de 730 días, un trimestre por step | 2 → 30 % |
| `generate-recent-heatmap` | Mapa NDVI de la fecha más reciente | 31 → 45 % |
| `compute-time-series-1` … `-12` | Serie NDVI de 365 días, un mes por step | 45 → 98 % |
| `compute-time-series-rescate` | Sólo si ningún mes dio un valor | 98 → 99 % |

Las ventanas salen de `ventanas()`: contiguas, sin huecos ni solapes, porque el `filterDate`
de GEE es semiabierto. Un reintento repite una ventana, no los dos años.

**"Hoy" se fija en un step.** Antes era `datetime.now()` en el cuerpo del handler, que corre
de nuevo en cada request de Inngest. Si un run cruzaba la medianoche —o un reintento esperaba
horas—, las ventanas de los steps que faltaban se correrían un día respecto de las ya
memoizadas.

`process_rancho`, `generate_heatmap_on_demand` y `compute_timeseries` reportan sus etapas
(búsqueda, descarga con sus MB, COG, subida, registro) sin cambiar de forma.

## 3. Lo que salió al hacerlo

### 🔴 Los fallos de un step no los veía nadie

inngest-py 0.4 convierte la excepción de un step en un `ResponseInterrupt`
(`step_lib/step_sync.py`), que hereda de **`BaseException`**. El `except Exception` de
`_correr_con_estado` no la atrapa nunca. Hasta hoy:

- el `logger.warning` de "falló en el intento N, se va a reintentar" (E.4) **no se emitía para
  un error dentro de un step**, que son casi todos;
- lo único que llegaba al wrapper era el `StepError` que el SDK entrega cuando el step ya agotó
  sus reintentos, y el wrapper decidía con `ctx.attempt`, cuyo valor en ese request no está
  verificado.

Los tests no lo mostraban porque el `_StepFalso` de `test_inngest_handlers.py` propaga la
excepción tal cual. El `_StepComoElSdk` de los tests nuevos imita al SDK.

**Arreglo:** `paso()` registra el fallo desde adentro del step, intento por intento, y
`es_definitivo()` trata un `StepError` como definitivo en cualquier intento.

### 🔴 Un rancho sin imágenes quedaba en `running` para siempre

`process_rancho` levanta `NonRetriableError` si no hay imágenes en 30 días. Inngest no lo
reintenta, pero el wrapper sólo marcaba `failed` si `ctx.attempt >= RETRIES`, o sea nunca.
Ahora es definitivo, con test.

### 🟠 La serie anual perdía los últimos meses

`get_sentinel2_time_series` ordena de la imagen más vieja a la más nueva y corta en
`limit=30`. En un año con más de 30 pasadas útiles, que es posible con dos satélites cada 5
días, se perdían justo los meses recientes. **No se midió cuántas parcelas lo sufrieron.** Mes
por mes, cada ventana tiene a lo sumo una docena.

`compute_timeseries` (a demanda) conserva el tope: un rango largo sigue truncando.

### El rescate se conservó, sobre el año

Antes, si no había ninguna imagen con ≤30 % de nubes en el año, se aceptaba hasta 90 %. Mes
por mes eso cambiaba el criterio: cada mes nublado caería al 90 % y la serie mezclaría dos
calidades. `get_sentinel2_time_series` y `generate_time_series_data` ganaron `rescate`
(default `True`: los demás llamadores no cambian). `process_parcela` lo apaga mes por mes, y si
ningún mes dio valor corre un step extra sobre el año entero con el criterio de antes y
`limit=100`.

Hay una diferencia fina. Antes la condición era "no hubo imagen ≤30 %"; ahora es "ninguna dio
valor". Coinciden salvo que haya imágenes con todos los píxeles enmascarados.

### `error_message` crudo le llegaba al tenant

`GET /api/processing/jobs/{id}` le devuelve `error_message` al usuario del tenant, y era
`str(e)`. Eso podía filtrar tres cosas:
- una URL prefirmada de MinIO trae su firma;
- una cadena de conexión trae su contraseña;
- un error de red trae el host privado.

`resumir_error()` las saca y deja una línea de hasta 500 caracteres. El crudo sigue yendo al log.

### El pool de conexiones podía quedar envenenado

`update_processing_job` tenía dos problemas:
- pedía la conexión **fuera** del `try`, así que si la base no respondía levantaba, contra lo que promete;
- no hacía rollback, así que una sentencia fallida devolvía al pool una conexión con la transacción abortada, y quien la tomara después recibía `current transaction is aborted`.

Está arreglado en `update_processing_job` y en la nueva `registrar_evento_job`.

## 4. Deploy

- **Necesita la migración `ProcessingJobEvents` de Geocore**, pero el orden no importa. Sin la
  tabla, `registrar_evento_job` reconoce el `42P01`, avisa una vez y pausa la bitácora 10
  minutos; el avance se sigue escribiendo. Aplicar la migración no exige reiniciar el worker.
- **Runs en vuelo:** los ids de step cambiaron (`query-sentinel2-dates` → `…-1…8`,
  `compute-time-series` → `…-1…12`). Un run que esté a mitad durante el deploy rehace esas
  etapas. Es idempotente: `ON CONFLICT` en `measurements` y en `sentinel2_dates`.
- **Más llamadas a GEE:** 20 steps en vez de 2, cada uno más chico. Cada step es también un
  request de Inngest al worker.

## Ruff

Los archivos tocados suman tres `BLE001` nuevos. Son los `except Exception` de "nunca
levanta" de la bitácora: el mismo patrón que ya usaba `update_processing_job` y que ruff ya
marcaba ahí. El resto de los hallazgos son los que ya había en `HEAD`.

## Lo que NO se verificó

- **Nada corrió contra Inngest ni GEE reales.** El comportamiento del SDK se leyó de su código
  y lo reproduce `_StepComoElSdk`. Primera prueba real: crear una parcela desde el panel y
  seguirla en Procesos.
- **El valor de `ctx.attempt` en el request que recibe un `StepError`.** Ya no decide si el job
  se marca `failed`, pero sí el `attempt` con que queda escrita la línea `fin`.
- **Cuánto tarda ahora el histórico.** `get_sentinel2_dates` sigue haciendo un `getInfo()` por
  imagen, cientos en dos años. Ahora se ve el avance por trimestre, pero sigue siendo lo lento.
  `aggregate_array` lo haría en una llamada.
