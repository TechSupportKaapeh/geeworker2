# Arquitectura del pipeline satelital

> **Propuesta del 2026-09-12.** Decisiones: `DECISIONS #31` (el histórico es
> mensual y el compuesto lo arma GEE) y `#32` (esta arquitectura), las dos a
> confirmar. Del lado de Geocore: `DECISIONS #22` (métrica del rancho) y `#23`
> (el cierre de mes). Plan de trabajo: [`PLAN.md`](PLAN.md) FASE M.

## 1. Qué tiene que resolver

| Necesidad | Cómo |
|---|---|
| Al dar de alta una parcela, 2 años de historia | 24 meses, un step por mes |
| Menos nulos por nubes | compuesto mensual: mediana por píxel de todas las pasadas limpias del mes |
| Datos nuevos cada mes | Geocore dispara el mes cerrado con un evento de Inngest (Geocore `#23`) |
| Series para análisis estadístico | por parcela y mes: mediana, media, mín, máx, p10, p90, desvío y cobertura |
| El mapa de cada mes | un COG por rancho y mes |
| Sumar un índice, una estadística o un análisis sin tocar el resto | registros y etapas (§3) |

El **mes** es el calendario en UTC. Sentinel-2 pasa sobre México cerca de las 17:00
UTC (las 11:00 locales), así que el mes UTC y el local coinciden siempre: B-5 no
aplica.

## 2. El recorrido de un mes

```
                 ┌──────────── núcleo: arma expresiones de GEE, sin I/O ────────────┐
 entidad (ROI)   │                                                                  │
 + mes           │  Fuente ──► Nubes ──► Índices ──► Compuesto ──┬──► Reducción      │
 + receta ──────►│  S2 SR +    s2cloud-   registro    mediana     │    espacial ──►  │ dict
                 │  prob. de   less +     (§3.2)      por píxel   │    registro de   │
                 │  nubes      sombras                + n_obs     │    estadísticas  │
                 │                                                └──► imagen del mes│
                 └──────────────────────────────┬───────────────────────────────────┘
                                                │  borde: el ÚNICO lugar que llama a GEE
                                        pipeline/ejecucion.py   (getInfo / getDownloadURL)
                                                │
                  ┌─────────────────────────────┴──────────────────────────┐
             measurements (una fila por parcela, índice y mes)     COG → MinIO + fila en layers
                                                │
                    handlers de Inngest: un step por mes, bitácora y reintentos
```

Una parcela usa la rama de la **reducción**: números, sin descargar un píxel. Un
rancho usa la de la **imagen**: el COG del mes. Las dos parten del **mismo
compuesto**, con la misma receta y a la misma escala. Por eso el número de la
parcela y el color del mapa salen de los mismos píxeles, y B-1 se cierra por
construcción.

## 3. Los patrones, y por qué estos

### 3.1 Tubería de etapas (*pipes and filters*)

Cada etapa es una función que recibe una expresión de GEE y devuelve otra. Se
prueban por separado, y se reemplazan sin tocar a las vecinas. Sumar Landsat, o
Sentinel-1 para los meses de lluvia, es escribir otra `Fuente` con la misma
salida.

### 3.2 Registros de índices y de estadísticas

Hoy un índice es una rama de un `if/elif`, y hay **dos** cadenas distintas: la
del mapa en `ee_indices.py` y la de la serie en `ee_client.py`. Además no dan lo
mismo. En el diseño nuevo un índice y una estadística son **datos**: una entrada
en un diccionario, que las etapas recorren. Agregar uno es agregar una entrada.

Los registros guardan **fábricas** (`lambda: ee.Reducer.median()`), no objetos de
GEE: las clases de GEE existen recién después de `ee.Initialize()`. Así importar
el módulo no toca la red, que es la regla que el repo ya sigue (`DECISIONS #24`).

### 3.3 Armar no es calcular: núcleo sin I/O y un solo borde

GEE es **perezoso**. `img.normalizedDifference(...)` no calcula nada ni puede
fallar: arma una expresión. El cálculo, y cualquier error, ocurre recién en
`getInfo()` o en `getDownloadURL()`.

Por eso los `try/except` que envuelven fórmulas en el código de hoy, como
"si falla B5, usá B4", **nunca se ejecutan**. Dan una seguridad que no existe.

Consecuencia de diseño: las etapas solo arman, y `pipeline/ejecucion.py` es el
**único** módulo que le pide algo a GEE. Ahí se concentran tres cosas:
- el tiempo máximo de cada pedido (`ee.data.setDeadline`);
- la traducción de errores, porque "límite de memoria" no se arregla reintentando
  y "demasiados pedidos concurrentes" sí;
- el conteo de llamadas para la bitácora.

### 3.4 La receta versionada

Todos los parámetros que cambian un número viven juntos en una `Receta`, con
nombre de versión:
- la colección;
- el umbral de nubes y la dilatación de la máscara;
- la escala;
- la cobertura mínima;
- los índices y las estadísticas.

Cada fila que se escribe guarda qué receta la produjo. Eso da tres cosas:
- **Trazabilidad** (D-1): se sabe con qué parámetros salió cada número.
- **Reproceso selectivo**: al cambiar de receta, se sabe qué filas son viejas.
- **"Modificables"**: cambiar las estadísticas es cambiar la receta, no el esquema.

La receta vive **en el código**, no en variables de entorno. Un parámetro que
cambia los datos tiene que quedar en el historial de git y pasar por revisión. Un
test fija el contenido de la receta vigente: si alguien cambia un parámetro sin
subir la versión, el test falla.

### Lo que se descartó: un framework de pipelines

Kedro, Dagster y Airflow resuelven la orquestación, los reintentos y la
programación. **Inngest ya lo hace**: steps que se guardan, reintentos, límite de
concurrencia, y la bitácora encima. Un framework sumaría otro servicio que
desplegar para algo que resuelven cinco módulos. Si algún día hace falta
paralelizar miles de parcelas por fuera de Inngest, se revisa.

## 4. Dónde vive cada cosa

```
pipeline/
  receta.py          Receta (dataclass inmutable) y RECETA_VIGENTE
  periodos.py        Mes, rango de un mes, los últimos N meses cerrados   ← puro
  indices.py         registro INDICES                                     ← puro
  estadisticas.py    registro ESTADISTICAS                                ← puro
  etapas/
    fuente.py        coleccion(roi, mes, receta)            -> ImageCollection
    nubes.py         enmascarar(img, receta)                -> Image
    compuesto.py     compuesto(coleccion, receta)           -> Image (índices + n_obs)
    reduccion.py     reductor(receta), cobertura(img, roi)  -> expresiones
  productos.py       estadisticas_del_mes(...) y mapa_del_mes(...): unen etapas, siguen sin I/O
  ejecucion.py       el borde con GEE
handlers/
  seguimiento.py     _with_job_tracking y claves de capa (sale de inngest_handlers.py)
  parcela.py         alta (24 meses) y mes cerrado
  rancho.py          alta (24 COG) y mes cerrado
  a_demanda.py       mapa de un período e índice cualquiera, con el mismo pipeline
repositories/, services/storage_service.py, services/cog_converter.py, services/avance_job.py
                     quedan como están
```

Un handler queda así de corto. El "qué" está en `productos`, el "cómo" en las
etapas, y el handler solo ordena los pasos:

```python
def process_parcela(ctx, step, payload):
    parcela = Entidad.desde_evento(payload)
    meses = paso(step, "plan", lambda: meses_cerrados(hoy_utc(), RECETA_VIGENTE.meses_historico))
    for mes in meses:
        paso(step, f"mes-{mes}", lambda mes=mes: escribir_estadisticas(parcela, mes, RECETA_VIGENTE))
```

El id del step lleva el mes (`mes-2025-09`), no un número de orden: queda estable
aunque el run cruce la medianoche o se reintente días después.

## 5. Cómo se agrega…

### …una estadística

```python
# pipeline/estadisticas.py
ESTADISTICAS = registro(
    Estadistica("mediana", lambda: ee.Reducer.median(),         sufijo="median"),
    Estadistica("media",   lambda: ee.Reducer.mean(),           sufijo="mean"),
    Estadistica("min",     lambda: ee.Reducer.min(),            sufijo="min"),
    Estadistica("max",     lambda: ee.Reducer.max(),            sufijo="max"),
    Estadistica("p10",     lambda: ee.Reducer.percentile([10]), sufijo="p10"),
    Estadistica("p90",     lambda: ee.Reducer.percentile([90]), sufijo="p90"),
    Estadistica("desvio",  lambda: ee.Reducer.stdDev(),         sufijo="stdDev"),
)
```

Para sumar `p25`, por ejemplo:
1. Una línea más en el registro.
2. `"p25"` en la receta, que pasa a otra versión.

Se guarda en `measurements.estadisticas` (jsonb), así que **no pide migración**.
Geocore la devuelve tal cual, y el front la muestra cuando la conoce.

### …un índice

```python
# pipeline/indices.py
INDICES = registro(
    Indice("ndvi", dif_normalizada("B8", "B4"),  rango=(-1, 1), descripcion="Vigor de la vegetación"),
    Indice("ndre", dif_normalizada("B8", "B5"),  rango=(-1, 1), descripcion="Clorofila; no se satura en cultivos densos"),
    Indice("ndmi", dif_normalizada("B8", "B11"), rango=(-1, 1), descripcion="Agua en la hoja"),
)
```

Una entrada y un test. El compuesto, las estadísticas, el mapa y la escala de
color lo toman del registro. B5 y B11 son bandas de 20 m que se remuestrean a
10 m: es lo habitual, pero conviene saberlo.

### …un análisis sobre la serie

Por ejemplo, la anomalía contra la mediana histórica del mes, la tendencia o una
caída brusca. **No toca GEE**: trabaja sobre las filas mensuales ya guardadas. Va
en su propio módulo (`analitica/`), con funciones puras que reciben una serie y
devuelven otra. Es lo más barato de agregar y lo más fácil de probar.

### …una fuente

Por ejemplo, Sentinel-1 (radar, que atraviesa las nubes) para los meses de lluvia.
Es una `Fuente` nueva con la misma salida que la óptica, y una receta que la
nombra.

## 6. Los datos

La migración la hace Geocore, que es dueño del esquema (`DECISIONS #15`).

**`measurements`**, una fila por parcela, índice y mes:

| Columna | Cambio | Qué lleva |
|---|---|---|
| `parcela_id`, `indice`, `fecha` | la PK no cambia | `fecha` = primer día del mes, 00:00 UTC |
| `valor` | pasa a **nullable** | la mediana. Nula si la cobertura quedó bajo el mínimo: la fila existe igual, para que el front dibuje el hueco (B-6) y el reconciliador sepa que el mes se procesó |
| `estadisticas` | nueva, `jsonb` | `{"mediana": 0.61, "p10": 0.48, …}` |
| `cobertura` | nueva | 0 a 1: la fracción de la parcela con al menos una observación limpia en el mes |
| `observaciones` | nueva | pasadas limpias por píxel, la mediana de `n_obs` |
| `receta` | nueva | `s2-mensual-v1` |
| `min_val`, `max_val` | se dejan de escribir | se borran en una migración posterior |

Las filas por pasada que existen hoy son de prueba y se borran antes de cambiar.
Lo hace el equipo.

**`layers`**, el mapa mensual del rancho:
- `acquired_ts` es el primer día del mes y `source` vale `mensual`;
- la key es `ranchos/{id}/ndvi/{AAAA-MM}.tif`, la forma que A-7 proponía: "todo el
  NDVI del rancho" queda como un prefijo;
- columnas nuevas: `receta` y `estadisticas` (`jsonb`, que es D-2).

**`processing_jobs`**: `periodo` (`AAAA-MM`, nullable) y un índice único por tipo,
entidad y periodo. Es lo que impide que el cierre de mes cree dos jobs para lo
mismo, aunque Geocore corra en dos réplicas.

**`rancho_measurements` ya no hace falta.** La métrica del rancho es el promedio
ponderado por área de sus parcelas, y la calcula Geocore al consultar (Geocore
`#22`). El punto 1 del pedido a Geocore caduca.

## 7. Los disparadores

| Evento | Lo publica | Steps | Produce |
|---|---|---|---|
| `terra/parcela.created` (ya existe) | Geocore, al crear | 24, uno por mes | 24 filas |
| `terra/rancho.created` (ya existe) | Geocore, al crear | 24 | 24 COG y sus filas en `layers` |
| `terra/parcela.mes.requested` 🆕 | el reconciliador de Geocore | 1 | la fila del mes cerrado |
| `terra/rancho.mes.requested` 🆕 | el reconciliador de Geocore | 1 | el COG del mes cerrado |
| `terra/parcela.heatmap.requested` (ya existe) | Geocore, a pedido | 1 | un mapa de cualquier período e índice |

Cómo funciona el reconciliador está en `DECISIONS #23` de Geocore. Las funciones
mensuales llevan un **límite de concurrencia** en Inngest: el cierre de mes
publica un evento por entidad, e Inngest los encola sin pasarse de la cuota de GEE.

Las parcelas que ya existen se procesan una vez con el mismo evento de alta,
republicado (M.5).

## 8. Criterios científicos que cambian respecto de hoy

Hay que validarlos con datos en M.2, lado a lado con lo de hoy.

1. **Compuesto: índice por pasada y después la mediana por píxel.** El mapa de hoy
   hace la mediana de las bandas y después el índice. `DECISIONS #19` ya había
   elegido el orden nuevo.
2. **No se descartan pasadas por cobertura.** Hoy una pasada con menos del 50 % de
   la parcela limpia se tira entera. En un compuesto mensual, esa pasada aporta
   los píxeles que sí están limpios: tirarla agrega nulos, que es lo contrario de
   lo que se busca. La calidad se mide al final, con la `cobertura` del mes.
3. **El umbral de nubes por escena de hoy no hace nada.** `get_sentinel2_collection`
   recibe `cloud_pct` y no lo usa, así que el "rescate al 90 %" que discute B-4
   no cambia nada. Se borran los dos.
4. **Sin corrección topográfica en la receta v1.**
   - `apply_scsc` no es SCS+C: le falta `cos(pendiente)` en el numerador, y usa un
     `C` fijo de 0,1 en lugar de uno por banda, sacado de una regresión.
   - Para índices normalizados (NDVI, NDRE, NDMI), el efecto de la iluminación se
     cancela casi entero en el cociente.
   - Si más adelante se usan reflectancias sueltas, se implementa bien y se valida.
5. **Una sola escala, 10 m**, para el compuesto, el mapa y las estadísticas. La
   serie de hoy reduce a 60 m.
6. **Una sola fórmula por índice.**
7. La máscara s2cloudless con sombras (`max_prob=45` y 50 m de dilatación) se
   queda igual, pero sus parámetros pasan a la receta.

## 9. Lo que se borra

Verificado que no tiene más llamadores, o que el pipeline lo reemplaza:

| Qué | Por qué |
|---|---|
| `ee_indices.compute_sentinel2_index` | lo reemplazan el compuesto y el registro |
| `ee_client.get_sentinel2_time_series`, `add_index_band_fast` | la segunda copia de las fórmulas |
| `una_por_dia` y la deduplicación de `insert_measurements` | el arreglo del 2026-09-12 (`#30`). El compuesto ya junta por mes |
| `get_sentinel2_dates`, `insert_sentinel2_date` y la tabla `sentinel2_dates` | A-4: nadie la lee |
| `check_roi_coverage` por pasada, `apply_scsc` | §8 |
| `maskS2clouds`, `composite_embedding`, `SUPPORTED_INDICES` | sin llamadores |
| `index_band_and_vis`, `get_classified_palette` | el rango y la paleta pasan al registro |
| `process_kml` | handler muerto desde que Geocore borró su evento |

**Propuesto, a confirmar antes de borrar:**
- `compute_timeseries`, `query_available_dates` y `compute_parcela_stats`: los
  datos mensuales ya van a estar.
- El export a CSV: hoy pide el rango `fecha → fecha`, que en GEE es vacío, así que
  nunca devolvió datos. Se rehace sobre las filas guardadas.

Los endpoints de Geocore que los disparan están documentados para el front de los
tenants (`api-frontend.html`), así que antes hay que confirmar que nadie los usa.

## 10. Riesgos y lo que falta decidir

- **Confirmar `#31`.** Reemplaza `#19` y `#20`. Lo que se pierde es poder elegir
  una ventana arbitraria sin volver a GEE.
- **La receta v1.** Propuesta:
  - índices NDVI, NDRE y NDMI: cuestan casi lo mismo que uno, porque salen de la
    misma colección;
  - mapa solo de NDVI;
  - cobertura mínima de 0,3;
  - 24 meses de historia.
- **El mes en curso.** ¿Se muestra como provisorio? En la v1, no: aparece cuando
  cierra.
- **La cuota de GEE.** En M.2 se mide cuánto tarda el mes de una parcela y el COG
  de un rancho, antes de fijar el límite de concurrencia.
- **El tope de `getDownloadURL`** es de unos 48 MB por pedido: una banda float32
  llega a unas 120.000 ha a 10 m. Un rancho más grande se exporta en partes.
- **Agrupar por cultivo** (M.8). La parcela todavía no tiene el atributo, y
  Geocore tiene que agregarlo antes de calcular métricas por cultivo.
