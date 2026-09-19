# Sesión 8, segunda parte — Las altas en producción (2026-09-18 y 19)

> Sprint M.4 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). La primera parte (M.4.4 y M.4.5) está en
> [`SESSION_2026-09-18_sesion_8_las_altas_II.md`](SESSION_2026-09-18_sesion_8_las_altas_II.md).
> Acá: **M.4.6**, la verificación en producción, y las cuatro tareas que salieron de hacerla,
> **M.4.7 a M.4.10** (geeworker2#40, #41, #42 y #43; `DECISIONS #52` a `#55`). Suite: de 547 a
> **591 verdes**, más 21 con `--gee`. **El sprint M.4 queda cerrado.**
>
> Lo que sigue es M.5.1, en Geocore: [`geocore/docs/PROXIMA_SESION.md`](../../geocore/docs/PROXIMA_SESION.md).

## 1. Las altas no llegaban al worker: dos variables de Railway

El usuario creó un rancho y una parcela desde el panel, y los jobs quedaron en "Esperando al
worker".
- **El evento no llegaba a Inngest.** Geocore publica después de guardar la entidad y el job, sin
  `try/catch`, así que el panel habría mostrado un error si la publicación hubiera fallado. No lo
  mostró: Inngest aceptaba el evento **en otro entorno**. La `Inngest__EventKey` de Geocore era de
  un entorno distinto del que tenía sincronizado al worker. Se corrigió en Railway.
- **Después, el sync del worker falló** con `POST https://inn.gs/fn/register` → 404. Había quedado
  `INNGEST_BASE_URL=https://inn.gs` en el worker, y **el SDK la lee por su cuenta** aunque el worker
  no se la pase. El reporte de arranque decía que en producción "no se usa". Se borró la variable,
  y **M.4.9** (`#54`) hace que el arranque marque como problema esa variable y las otras tres que
  lee el SDK.

## 2. Jobs colgados al cancelar: M.4.7 (`DECISIONS #52`)

El usuario canceló las corridas en Inngest y el panel las siguió mostrando "en proceso". El wrapper
marca `failed` solo cuando **ve** el error, y una corrida cancelada o muerta no pasa por ahí.
Ahora las altas tienen `on_failure` (`inngest/function.failed`) y una función escucha
`inngest/function.cancelled`. Las dos cierran con un `UPDATE … WHERE status IN ('pending',
'running')`. Probado contra un Inngest real: una alta cancelada queda `failed` a los 5 s. 👥 Los
jobs colgados de antes se cierran con el SQL de `#52`.

## 3. Cada mes tardaba un minuto

Inngest mostraba, por step, **5 a 10 s del worker y de 2 a 55 s de espera**. La investigación, en
orden:

1. **El worker atendía de a un step por vez: M.4.8 (`#53`).** `inngest.fast_api.serve()` corre los
   handlers síncronos dentro del event loop, así que un step de GEE congelaba el proceso entero.
   `DECISIONS #26` decía lo contrario, y era falso. Ahora la ruta usa la variante síncrona del SDK
   en el pool de hilos, con el pool de conexiones y el plazo de GEE seguros entre hilos. Contra un
   Inngest real, **tres altas a la vez: 66 s antes, 23 s después**. La primera versión del test
   reemplazaba el endpoint y no probaba nada; al rehacerla para invocar la ruta real apareció un
   500 que habría salido en cada pedido.
2. **En producción, la espera siguió**, incluso con una sola alta. El plan Hobby de Inngest tiene
   5 steps a la vez y 50.000 ejecuciones al mes, pero una alta usa uno solo.
3. **M.4.10 (`#55`): una función con 5 steps vacíos.** En local, 0,12 a 0,20 s entre steps. En
   producción, **esperas al azar de 38 a 75 s** con steps que no hacen nada. **La espera es de
   Inngest Cloud**, no del worker ni del pipeline.

El usuario preguntó si ir más rápido había costado datos. Se comprobó: la parcela 1 corrida el
2026-09-18 y otra vez el 19, con el código nuevo, dio **las mismas 96 filas y las mismas 672
estadísticas, con diferencia máxima 0**.

## 4. M.4.6 — la verificación en producción

Con un rancho ("Prueba2") y una parcela creados desde el panel, leídos por la API de Geocore:

| Qué | Resultado |
|---|---|
| Procesos | los jobs en `completed` al 100 %, sin errores |
| Filas de la parcela | 96 (24 × 4), todas con `s2-mensual-v1` y las 7 estadísticas; ninguna con valor y cobertura bajo 0,3 |
| Capas del rancho | 24 `mensual`, con la key `tenants/{tenant}/ranchos/{rancho}/s2-mensual-v1/ndvi/AAAA-MM.tif` |
| COG por el tileserver | `check_prod.py` 7 de 7 |
| La máscara del COG | `nodata_type: Mask`, mínimo −0,095 (ningún −9999 visible): el arreglo de `#51` en producción |
| Métrica del rancho | 24 meses, `fraccionArea` 1, 1 parcela (la única que se creó) |

**El NDVI de esa parcela da 0,09 a 0,14 todo el año**: suelo desnudo o zona construida. Es un
error al elegir las coordenadas de prueba, no de los datos. Para la demo, un polígono sobre campo
real.

## 5. Lo que queda registrado

- 👥 **Escribirle al soporte de Inngest** con las corridas de `diagnostico-latencia`. Si no se
  resuelve, un piloto de Inngest autohosteado (sin límites de plan y sin la espera, pero con un
  servicio más que operar).
- **M.5.3 fija la concurrencia en 5 o menos**, el techo del plan.
- **`GET /api/layers` no filtra por rancho**: M.7.4 lo va a necesitar.
- **El `.venv` del tileserver sigue muerto**: `check_prod.py` se corrió con el Python del worker.
- **El dominio del tileserver no resolvió una vez** (un fallo de DNS momentáneo, con la red de
  la sesión intermitente); después respondió bien.
