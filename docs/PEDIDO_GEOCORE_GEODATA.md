# Pedido a Geocore — esquema y API de `geodata`

> Redactado el **2026-09-07** desde el repo del worker (`geework 2.0`).
> Es el `PLAN.md` **G.1** del worker, escrito para que lo ejecute quien trabaja
> Geocore. Contexto de producto: [`PLAN.md`](PLAN.md) FASE G.
>
> ⚠️ **2026-09-12: reemplazado por [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md)
> §6 (FASE M.3).** Qué queda de este pedido:
> - El **punto 1 caduca**: la métrica del rancho se deriva de las parcelas.
> - Los **puntos 3 y 4** se absorben: estadísticas en `jsonb`, cobertura y receta.
> - El **punto 0** sigue valiendo, y ya se desplegó el 2026-09-11.
> - El **2b** (lista de ids) sigue valiendo.

## Por qué esto lo tiene que hacer Geocore

`DECISIONS #15`: la base `geodata` tiene su propio `GeoDataDbContext` **en
Geocore**, con migraciones en `Persistence/Migrations/GeoData/`. El worker
**escribe** filas en `layers` y `measurements`, pero **no es dueño del DDL**.

Si el worker hiciera el `ALTER TABLE` por su cuenta, el modelo de EF Core
quedaría desincronizado con la base real y la próxima migración de Geocore
pelearía con ese cambio. Es la misma clase de problema que ya pasó al revés:
cuando Geocore renombró las columnas a snake_case, **el worker dejó de escribir
en silencio durante cuatro días** y nadie se enteró, porque no hay compilador que
agarre ese desajuste.

## Por qué todo junto

Cada migración cuesta coordinación entre dos repos: alguien toca Geocore, genera
la migración, la aplica, despliega, y **recién ahí** el worker puede empezar a
escribir la columna nueva. Hacerlo cuatro veces cuesta cuatro veces esa
coordinación.

---

# 0. 🔴 Un bug de hoy, que no debería esperar al resto

`MeasurementsController.GetMeasurements` devuelve **las mediciones más viejas**:

```csharp
.OrderBy(m => m.Fecha)
.Take(limit)          // limit = 500 por defecto
```

Orden ascendente + `Take` = las 500 más antiguas. Para un gráfico es al revés de
lo que se quiere: **en cuanto una parcela pase las 500 mediciones, el front deja
de ver los datos actuales para siempre.** Sin `parcelaId` es peor — 500 filas de
todo el tenant, elegidas por antigüedad.

Con la ingesta por pasada de la FASE C (~73 mediciones por año por índice) el
techo se toca solo.

**Arreglo:** `OrderByDescending(m => m.Fecha)`, y que el front invierta para
graficar. O paginar de verdad con un cursor sobre `Fecha`.

**Esto es independiente del resto del pedido y se puede desplegar solo.**

---

# 1. Tabla nueva: `rancho_measurements`

## Qué necesita el producto

La vista de mapa muestra los tiles del **rancho** con sus parcelas dibujadas
encima, y **métricas de las dos cosas**: por parcela al seleccionarla, y del
rancho en un panel aparte.

Hoy las métricas de rancho **no existen ni pueden existir**:

```csharp
builder.HasKey(m => new { m.ParcelaId, m.Indice, m.Fecha });   // MeasurementConfiguration
public Guid ParcelaId { get; private set; }                     // Measurement — sin RanchoId
```

Y `process_rancho` no escribe ninguna medición: produce el ráster y emite
`terra/raster.ingested`.

## Por qué una tabla nueva y no `rancho_id` en `measurements`

Se consideró agregar `rancho_id` nullable a `measurements` —espejando lo que
`layers` ya hace con sus dos ids—. **Se descartó, por tres razones:**

1. **Riesgo.** `fecha` está en la PK de `measurements`. Hacer `parcela_id`
   nullable exige PK sustituta, un `CHECK` de exclusividad y dos índices únicos
   parciales — y eso **cambia el `ON CONFLICT` del que depende la idempotencia
   del worker** (`ARCHITECTURE_PLAN` §5). Una tabla nueva no toca nada que ya
   funcione: es la migración más segura que hay.
2. **A `measurements` siempre se le pide el sujeto.** Es la diferencia con
   `layers`: a una capa se le pide **por su id**, sin importar de quién es. A las
   mediciones **siempre** se les pide *"la serie de esta parcela"* o *"la del
   rancho"*. Si nadie consulta sin discriminar, la tabla única no compra nada.
3. **La jerarquía se resuelve un paso antes.** El front pide
   `GET /api/parcelas?ranchoId=…` para dibujar los polígonos, así que llega a las
   mediciones **con los ids en la mano**. Un `rancho_id` en las filas de parcela
   sería denormalización pura.

**El costo aceptado:** esquema duplicado —una columna nueva se agrega en dos
lados— y un endpoint más.

## La forma pedida

Simétrica a `measurements`, cambiando el sujeto:

| Columna | Tipo | Notas |
|---|---|---|
| `rancho_id` | `uuid` | parte de la PK |
| `indice` | `text` | parte de la PK |
| `fecha` | `timestamptz` | parte de la PK. **`DateTimeOffset` en la entidad**, igual que `Measurement.Fecha` |
| `tenant_id` | `uuid` | NOT NULL |
| `valor` | `double precision` | NOT NULL |
| `min_val` | `double precision` | nullable |
| `max_val` | `double precision` | nullable |

`HasKey(m => new { m.RanchoId, m.Indice, m.Fecha })` — el worker se apoya en esa
PK para el `ON CONFLICT`, que es lo que hace idempotente el reprocesamiento.

⚠️ **Los nombres de columna son contrato entre repos**, igual que los de `layers`
y `measurements`. El propio `LayerConfiguration` ya lo dice: *"Lo escribe el
worker externo, no Geocore. Por eso los nombres de columna son contrato entre dos
repos"*.

---

# 2. Endpoints

| | Qué | Por qué |
|---|---|---|
| **2a** | `GET /api/measurements` con **`OrderByDescending`** | Es el punto 0. Bug de hoy |
| **2b** | `parcelaId` acepta **una lista** de ids, o un filtro `ranchoId` | Para pintar los N polígonos de un rancho según su valor actual, el front hoy hace **N llamadas**: un N+1 a nivel de API |
| **2c** | Endpoint para las métricas de rancho | Sin esto la tabla del punto 1 no se puede leer |

Nota sobre **2b**: con el filtro `ranchoId`, Geocore tendría que traducir
rancho → parcelas usando la base principal. Es un `join` de aplicación entre dos
proyectos de Supabase. **La versión con lista de ids es más simple** y le sirve
igual al front, que ya tiene los ids porque los necesitó para los polígonos.

---

# 3. Estadísticas del ráster en `layers`

**El worker ya las calcula y las tira a la basura.** `export_heatmap` hace un
`reduceRegion` con `mean`, `min`, `max` y `stdDev`, y el resultado se descarta
porque no hay dónde ponerlo — está comentado en el código:

```python
# `stats` (min/max/mean/stddev) no se persiste: `layers` no tiene esas
# columnas. Requiere migracion en Geocore.
```

**Para qué las necesita el producto:** hoy el front pinta con `rescale=-1,1`
fijo. Un solo ráster de rancho que cubre platanal, casa y cultivo de mora **se ve
todo parecido** con una escala fija. Con las estadísticas se puede escalar por
capa.

| Columna | Tipo |
|---|---|
| `min_val`, `max_val`, `mean_val`, `stddev_val` | `double precision`, nullable |

Nullable porque el `reduceRegion` puede fallar y hoy eso no aborta el
procesamiento.

---

# 4. Trazabilidad y calidad del dato

## 4a. Parámetros de procesamiento en `layers`

Hoy `max_prob=45`, `cloud_pct=30` y `min_coverage=0.5` están **hardcodeados** en
el worker, y `layers` no registra con qué parámetros se procesó cada capa. Si
mañana se cambia el umbral de nubes, **no hay forma de saber qué capas se hicieron
con cuál**.

Basta con una columna `jsonb` —`processing_params`— antes que una por parámetro:
el conjunto va a cambiar.

## 4b. Calidad de cada medición

Esto salió de revisar el código del worker y es más importante de lo que parece
para la UX.

El filtro de cobertura **ya existe**: `check_roi_coverage` cuenta píxeles válidos
post-máscara de nubes contra el total del ROI, y descarta las pasadas por debajo
de `min_coverage=0.5`. Pero:

```python
cloud_thresholds = [min(cloud_pct, 80), 90]
for threshold in cloud_thresholds:
    ...
    if size > 0:
        break
```

**Si con el umbral pedido no aparece ninguna imagen, reintenta con 90% de
nubes** — y el punto devuelto no dice con qué umbral salió. **Dos puntos
contiguos de la misma serie pueden venir de una pasada al 30% y de otra al 90%,
y nada los distingue.**

En la vista, el número va al lado del polígono de la parcela sin ningún contexto.
Un valor derivado de una pasada al 90% de nubes se ve idéntico a uno bueno.

Pedido: en `measurements` y en `rancho_measurements`,

| Columna | Tipo | Qué guarda |
|---|---|---|
| `roi_coverage` | `double precision`, nullable | fracción de píxeles válidos, 0..1 |
| `cloud_pct_usado` | `integer`, nullable | el umbral con el que salió |

Con eso el front puede marcar un punto como de baja confianza en vez de
mostrarlo igual que el resto.

---

# Resumen para planificar

| | Qué | Tipo de cambio | Bloquea |
|---|---|---|---|
| **0** | `OrderByDescending` en `/api/measurements` | corrección de bug | Nada. **Desplegable solo** |
| **1** | Tabla `rancho_measurements` | migración **aditiva** | Métricas de rancho en la vista |
| **2b** | `parcelaId` como lista | API | Pintar N polígonos sin N llamadas |
| **2c** | Endpoint de métricas de rancho | API | Leer lo del punto 1 |
| **3** | Estadísticas en `layers` | migración aditiva | Escala de color por capa |
| **4a** | `processing_params` en `layers` | migración aditiva | Trazabilidad. No urgente |
| **4b** | `roi_coverage` y `cloud_pct_usado` | migración aditiva | Marcar puntos de baja confianza |

**Ninguna es destructiva**: todas agregan tabla o columnas nullable, así que el
worker sigue funcionando sin cambios hasta que se actualice para escribirlas.

## Lo que el worker hace de su lado, después

- `process_rancho` agrega un `reduceRegion` y escribe en `rancho_measurements`
  (`PLAN.md` G.4).
- `insert_layer` pasa a persistir las estadísticas que hoy descarta.
- `insert_measurement`/`insert_measurements` agregan cobertura y umbral.
- Nada de eso se puede empezar antes de que la migración esté aplicada.
