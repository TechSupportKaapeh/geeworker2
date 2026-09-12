# Sesión 2026-09-12 (tarde) — La primera corrida real y el pipeline mensual

> Estado permanente en [`HANDOFF.md`](HANDOFF.md). Sesión anterior, el mismo día:
> [`SESSION_2026-09-12_la_bitacora_del_worker.md`](SESSION_2026-09-12_la_bitacora_del_worker.md).
> Del lado de Geocore: `geocore/docs/SESSION_2026-09-12_contencion_y_plan_mensual.md`.
> Para retomar: `geocore/docs/PROXIMA_SESION.md`.

## 1. La primera corrida real

El equipo aplicó la migración `ProcessingJobEvents` y creó una parcela desde el
panel. La bitácora funcionó: se vio cada etapa, cada intento y el error. El job
terminó `failed` al 45 %:

```
compute-time-series-1  warning  Fallo en el intento 2 de 4; Inngest lo va a reintentar:
                                CardinalityViolation: ON CONFLICT DO UPDATE command cannot
                                affect row a second time
…                      error    Fallo en el intento 4 de 4 y no se reintenta
fin                    error    (attempt 1) Falló y no se va a reintentar
```

### La causa

`get_sentinel2_time_series` devuelve **un punto por imagen**, con la fecha cortada
al día. Una parcela en el borde de dos tiles MGRS recibe dos imágenes de la misma
pasada, con la misma fecha. Desde E.7 las mediciones se escriben en un solo
`INSERT … ON CONFLICT DO UPDATE`, y Postgres rechaza el comando entero si dos filas
del lote tienen la misma PK. De a una fila, la segunda pisaba a la primera en
silencio.

### El arreglo (`DECISIONS #30`)

- `una_por_dia()` en `ee_client.py` promedia las imágenes que comparten fecha.
  Se redondea **después** de juntar, no antes.
- `insert_measurements` deduplica por PK antes de mandar el lote: queda la última
  y se loguea un warning. Es la defensa en el borde con la base.
- La fecha de cada imagen se calcula en UTC. Antes `fromtimestamp` usaba el huso
  de la máquina.
- Dos tests nuevos. **Suite: 203.** Ruff: `ee_client.py` bajó de 18 a 17
  hallazgos, y `db_repository.py` quedó en 22.

**Transitorio:** con el pipeline mensual (§3) este código se borra.

### Lo que la corrida contestó

**`ctx.attempt` vale 0 en el request que recibe el `StepError`.** Era lo último
de "Lo que NO se verificó" en la sesión anterior. La línea `fin` queda escrita con
`attempt` 1 después de un step que falló cuatro veces. El panel dibujaba un
"Intento 1" después del intento 4; ahora no abre separador con `inicio` ni con
`fin` (`terra-admin`, `BitacoraJob.tsx`).

## 2. La revisión de la capa de satélite

El usuario pidió una revisión honesta. En este repo encontró:

| Hallazgo | Evidencia |
|---|---|
| Cada fórmula de índice está escrita **dos veces**, y distinta | `ee_indices.compute_sentinel2_index` (mapa: mediana de bandas → índice) y `ee_client.add_index_band_fast` (serie: índice por imagen) |
| **`cloud_pct` no hace nada** en la serie | `get_sentinel2_collection` lo recibe y no lo usa: el "rescate al 90 %" de B-4 no cambia la colección |
| `apply_scsc` no es SCS+C | le falta `cos(pendiente)` en el numerador, y el `C` es fijo |
| `compute_parcela_stats` y el export a CSV no devuelven datos | piden `fecha → fecha`, y el `filterDate` de GEE excluye el fin |
| `try/except` alrededor de expresiones de GEE | GEE es perezoso: el error aparece en `getInfo()`, así que esos respaldos nunca corren |
| 53 `except Exception` en el código del servicio | `ruff --statistics`: 50 BLE001, 6 S110 |
| Código sin llamadores | `composite_embedding`, `maskS2clouds`, `SUPPORTED_INDICES` |
| No escala | dos `getInfo()` por imagen en la serie, y uno por imagen en las fechas |

Lo que está bien y se reutiliza tal cual:
- los steps de Inngest, con la bitácora y los reintentos;
- las escrituras idempotentes y la subida a MinIO;
- la configuración que falla cerrada;
- `pip-audit` limpio.

## 3. El pipeline mensual

El usuario definió la necesidad:
- 2 años de historia al dar de alta una parcela;
- ventana **mensual** con mediana, para reducir nulos;
- datos nuevos cada mes;
- series para análisis estadístico;
- que agregar una métrica sea fácil.

Lo que hay no lo cumple: un compuesto de unos 10 días por parcela, uno de 30 por
rancho y la serie por imagen.

Decisiones:

| | Qué | Estado |
|---|---|---|
| `#31` | Histórico mensual, con el compuesto armado en GEE. Reemplaza #19 y #20 | propuesta: el usuario pidió que se le explicara la alternativa |
| `#32` | La capa de satélite como pipeline: receta versionada, registros de índices y estadísticas, etapas y un solo borde con GEE | propuesta |
| Geocore `#22` | La métrica del rancho es el promedio ponderado por área de las parcelas | ✅ decidida por el usuario. A futuro, agrupada por cultivo |
| Geocore `#23` | El cierre de mes lo dispara Geocore, con un evento de Inngest por entidad | ✅ decidida por el usuario (opción a) |
| Estadísticas | mediana, media, mín, máx, p10, p90, desvío y cobertura, **modificables** | ✅ decidido. En `jsonb`, sin migración para agregar |

Diseño: [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md). Plan:
[`PLAN.md`](PLAN.md) FASE M, de M.0 a M.8, cada paso por las seis etapas del
`WORKFLOW`.

## Lo que NO se hizo

- **No se volvió a correr contra GEE** después del arreglo. La próxima parcela
  que se cree lo prueba.
- **No se empezó el pipeline**: es un diseño a confirmar.
- **CI sigue sin existir**: es M.0, y está antes que el resto a propósito.
