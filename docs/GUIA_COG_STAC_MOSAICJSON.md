# Guía: COG, MosaicJSON y STAC — qué son y por qué nos importan

> Escrita el 2026-08-21 para el equipo de Terra. Asume cero conocimiento previo
> de formatos geoespaciales. Si ya sabés qué es un COG, saltá a §5.

---

## 1. El problema, sin jerga

Tenemos ranchos y parcelas. Queremos mostrarle al cliente **cómo está su campo
hoy** y **cómo cambió en el tiempo**, usando imágenes de satélite.

Eso se rompe en cuatro problemas que se resuelven con herramientas distintas:

| Problema | Herramienta |
|---|---|
| Las imágenes de satélite son enormes | **COG** |
| Las nubes tapan pedazos, hay que combinar varias fechas | **MosaicJSON** |
| Con miles de imágenes hay que saber cuál es cuál | **STAC** |
| Hay que pintar todo eso en un mapa web | **TiTiler** |

Este documento explica las tres primeras. La cuarta ya la usamos.

---

## 2. Qué manda realmente un satélite

Sentinel-2 **no saca una foto**. Mide cuánta luz refleja el suelo en 13 rangos
de longitud de onda distintos. Cada rango es una **banda**:

- **B4** — luz roja
- **B8** — infrarrojo cercano (invisible al ojo humano)
- **B11** — infrarrojo de onda corta
- …y diez más

Cada banda es una imagen en escala de grises. Las 13 apiladas son la medición
completa de ese pedazo de tierra.

### Un índice es una cuenta entre bandas

El **NDVI** —el más usado en agricultura— no es una banda, es aritmética:

```
NDVI = (B8 − B4) / (B8 + B4)
```

Funciona porque **la vegetación sana refleja mucho infrarrojo y absorbe el
rojo**. Suelo desnudo hace lo contrario. El resultado va de −1 a 1: cerca de 1
es vegetación densa, cerca de 0 es suelo, negativo suele ser agua.

Otros índices usan otras bandas para otras preguntas: NDWI mira agua, NDMI mira
humedad, NDRE mira nitrógeno. Todos son cuentas sobre las mismas 13 bandas.

**Dato importante:** una vez que calculás el NDVI, **las bandas ya no están**.
El resultado es una sola imagen. No podés sacar NDWI del NDVI, porque esa cuenta
necesita B3 y B3 se perdió.

### Una "pasada" es una visita del satélite

Sentinel-2 son dos satélites, y entre los dos pasan por el mismo lugar **cada
~5 días**. Cada visita es una **pasada** (o "escena"). Cada pasada tiene su
fecha, su hora, su ángulo solar y sus nubes.

---

## 3. Por qué hay que combinar varias pasadas

Una pasada suelta puede tener nubes tapando media parcela. Un mapa con agujeros
no le sirve a nadie.

La solución es un **composite**: agarrás las pasadas de un período, **enmascarás
las nubes de cada una** (los píxeles nublados se marcan como "sin dato"), y por
cada píxel tomás la mediana de los valores que sí sirven.

```
Pasada 1 (día 3):   ☁☁🟩🟩       nubes a la izquierda
Pasada 2 (día 8):   🟩🟩☁☁       nubes a la derecha
Pasada 3 (día 13):  🟩☁🟩🟩       una nube en el medio
                    ─────────
Composite:          🟩🟩🟩🟩       sin agujeros
```

**La ventana del composite es tu resolución temporal.** Si componés por mes,
tenés un mapa por mes. Si componés por semana, uno por semana — pero con más
riesgo de que una semana quede vacía por nubes.

> Esto ya lo hace nuestro código, dentro de Google Earth Engine:
> `mask_s2cloudless_and_shadows` enmascara nubes y sombras, `apply_scsc` corrige
> el efecto de las montañas, y `collection.median()` compone.

---

## 4. COG — el formato

### El problema

Un GeoTIFF de una escena satelital puede pesar cientos de megas. Si el navegador
quiere mostrar un pedacito, un formato normal lo obliga a **bajar el archivo
entero** para leer un rincón.

### La analogía

**Un GeoTIFF normal es un rollo de papiro.** Para leer lo del final, desenrollás
todo.

**Un COG es un libro con índice y capítulos.** Vas directo a la página 200 sin
pasar por las 199 anteriores.

### Cómo lo logra

Dos trucos:

1. **Tiling interno** — la imagen se guarda en cuadraditos, no en filas
   completas. Para leer una zona, se leen solo sus cuadraditos.
2. **Overviews** — copias más chicas pre-calculadas, como las miniaturas de una
   galería. Si el mapa está muy alejado, se lee la miniatura en vez de la imagen
   completa.

Sumado a las **HTTP Range Requests** —pedir "dame los bytes 5000 al 8000" de un
archivo remoto— el servidor de tiles se trae **unos pocos KB de un archivo de
200 MB**.

### Qué significa para nosotros

Por eso el worker convierte todo a COG (`services/cog_converter.py`) antes de
subirlo. **Un GeoTIFF sin convertir en MinIO es prácticamente inútil**: TiTiler
tendría que descargarlo entero para cada tile.

---

## 5. MosaicJSON — combinar varios COGs

### El problema

Tenemos un COG por pasada. Para el mapa mensual queremos **la mediana de las
pasadas de ese mes**. ¿Los combinamos y guardamos el resultado, o los
combinamos al momento de mostrar?

### La analogía

**MosaicJSON es una lista de reproducción.** No contiene la música: dice qué
canciones, en qué orden, y qué hacer con ellas. El reproductor las junta al
sonar.

Un MosaicJSON es un archivo chiquito que dice: *"para esta zona del mapa, usá
estos COGs"*. Los archivos siguen donde estaban.

### Por qué es mejor que hacerlo a mano

Combinar rásters parece fácil y no lo es:

- Las imágenes tienen que estar en la **misma grilla** de píxeles, o no se pueden
  apilar
- Hay que manejar el **"sin dato"** correctamente — un cero en NDVI es un valor
  válido, no un agujero, y confundirlos arruina el resultado en silencio
- Hay que decidir **cómo combinar**: ¿la primera que tenga dato? ¿el promedio?
  ¿la mediana?

`rio-tiler`, la librería debajo de TiTiler, **ya resuelve las tres cosas**, y
trae varios métodos de combinación, incluida la mediana.

### Lo que gana Terra

En vez de:

```
bajar pasadas → alinear grillas → mediana con numpy → guardar composite → servir
                └──────── código nuestro, con sus bugs ────────┘
```

Hacemos:

```
bajar pasadas → MosaicJSON del período → TiTiler compone al servir el tile
                └── un archivo JSON ──┘  └─── código de terceros, probado ───┘
```

Y como el MosaicJSON es solo una lista, **cambiar de mensual a semanal es armar
otra lista**. No se reprocesa nada.

### El límite

Componer al vuelo cuesta lectura: 6 COGs por tile está bien, 73 no. Así que:

- **Ventanas chicas** (semana, mes → 2-6 pasadas): al vuelo
- **Ventanas grandes** (año): conviene precalcular

---

## 6. STAC — el catálogo

### El problema

Con 3 índices × 73 pasadas × 50 ranchos ya son **10.000 archivos**. ¿Cómo
encontrás "el NDVI del rancho X en agosto"? ¿Por el nombre del archivo?

Ese es el camino a inventar convenciones frágiles: `ranchos/abc/2026-08-19_ndvi.tif`
funciona hasta que alguien necesita filtrar por nubosidad, o por satélite, o
buscar por área geográfica.

### La analogía

**STAC es el catálogo de la biblioteca.** Los libros están en las estanterías
(MinIO). El catálogo tiene una ficha por libro: título, autor, año, tema, y en
qué estante está.

Sin catálogo, encontrar un libro es recorrer las estanterías.

### Cómo se estructura

Tres niveles, todo en JSON:

```
Collection  ── "NDVI de ranchos"          (el conjunto)
  └─ Item   ── una fecha + una geometría  (la ficha)
       └─ Asset ── el archivo             (dónde está)
```

Un **Item** tiene siempre los mismos campos, y esa es la gracia:

| Campo | Qué es |
|---|---|
| `id` | Identificador |
| `datetime` | Cuándo se capturó |
| `bbox` | Rectángulo que cubre |
| `geometry` | La forma exacta |
| `properties` | Nubosidad, satélite, lo que quieras |
| `assets` | Los archivos, con su URL |

### Por qué importa que sea un estándar

Porque **las herramientas ya lo hablan**. Si tu catálogo es STAC:

- TiTiler puede servirlo directo
- QGIS puede conectarse
- `pystac-client` busca por fecha y por zona sin que escribas SQL
- **Copernicus, NASA, AWS y Microsoft publican sus catálogos así** — y podés
  consultar sus imágenes con el mismo código que consulta las tuyas

Ese último punto es el más valioso a futuro.

### Por qué NO lo adoptamos todavía

Nuestra tabla `geodata.layers` **ya es un catálogo**, chiquito pero funcional:

| `layers` | Equivalente STAC |
|---|---|
| `id` | `id` |
| `acquired_ts` | `datetime` |
| `bbox` | `bbox` |
| `product` | parte de `properties` |
| `storage_key` | `assets.href` |

Está a un paso. Pero reemplazarla implica cambiar Geocore, que es quien la sirve
al front. Es un cambio entre dos repos para resolver un problema que **hoy no
tenemos**: no somos 10.000 archivos todavía.

**La decisión práctica:** no migrar ahora, pero **nombrar los campos como STAC**
para que la migración sea barata cuando toque. Cero costo hoy, opción abierta
mañana.

**Cuándo sí migrar:** cuando el catálogo tenga que servir a algo que no sea
nuestro front (QGIS, un cliente que quiere sus datos), o cuando queramos mezclar
nuestras capas con catálogos públicos.

---

## 7. ¿Estamos reinventando la rueda?

Respuesta honesta, pieza por pieza:

| Lo que hacemos | ¿Estándar? | Veredicto |
|---|---|---|
| Convertir a COG | Sí, es *el* formato | ✅ Bien |
| Servir tiles con TiTiler | Sí, es la herramienta de referencia | ✅ Bien |
| MinIO como storage S3 | Sí | ✅ Bien |
| Correcciones en GEE (s2cloudless, SCS+C) | Sí, algoritmos publicados | ✅ Bien |
| Arquitectura por eventos | Sí | ✅ Bien |
| `layers` como catálogo propio | Es un STAC casero | 🟡 Pragmático. Migrable. |
| **Componer rásters con código propio** | **No** | ❌ **Esto sí sería reinventar la rueda** |

**La única rueda que estabas por reinventar es el composite.** Alinear grillas,
manejar nodata y calcular la mediana es un problema resuelto, con casos borde
que muerden. MosaicJSON y `rio-tiler` lo cubren.

Todo lo demás está sobre estándares. No estás inventando: estás integrando.

---

## 8. Cómo encaja todo en Terra

```
Google Earth Engine
   │  máscara de nubes (s2cloudless) + corrección topográfica (SCS+C)
   │  ── esto pasa POR PASADA, antes de componer ──
   ▼
COG por pasada  ──────────────▶  MinIO
   │                              (los archivos)
   ▼
layers  ─────────────────────▶  geodata
   │                              (el catálogo)
   ▼
MosaicJSON del período pedido
   │
   ▼
TiTiler ── mediana al vuelo ──▶  tiles ──▶ Leaflet en el navegador
```

Y en paralelo, la otra mitad:

```
por cada pasada: promedio del índice sobre la parcela
   │
   ▼
measurements ──▶ geodata ──▶ Geocore ──▶ gráfico en el front
```

---

## 9. Los dos caminos, y por qué son simétricos

Hay una simetría que conviene ver, porque es el mismo principio aplicado dos
veces:

| | El mapa | El gráfico |
|---|---|---|
| **Dato atómico** | COG por pasada | Fila por pasada en `measurements` |
| **Agrupación** | MosaicJSON del período | `GROUP BY` por período en SQL |
| **Dónde ocurre** | Al servir el tile | Al consultar |
| **Se guarda el agregado** | No | No |

**En los dos casos guardamos el átomo y derivamos el agregado.** Nunca al revés:
de un promedio mensual no se recupera la pasada, pero de las pasadas se calcula
cualquier promedio.

Por eso "mensual, semanal o por pasada" es, exactamente como intuiste, **un
filtro** — no tres pipelines distintos. Se elige al consultar, no al ingerir.

---

## 10. Glosario

| Término | Qué es |
|---|---|
| **Banda** | Una de las 13 longitudes de onda que mide el satélite |
| **Índice** | Cuenta entre bandas (NDVI, NDWI…) que da una sola imagen |
| **Pasada / escena** | Una visita del satélite. Sentinel-2: cada ~5 días |
| **Composite** | Varias pasadas combinadas en una imagen sin nubes |
| **COG** | GeoTIFF organizado para leerse por pedazos desde internet |
| **Overview** | Copia reducida dentro del COG, para zooms alejados |
| **Range Request** | Pedir solo un tramo de bytes de un archivo remoto |
| **MosaicJSON** | Lista de COGs que se sirven como una capa |
| **STAC** | Estándar para catalogar imágenes satelitales |
| **Tile** | Cuadradito de 256×256 px que arma el mapa web |
| **nodata** | Marca de "acá no hay medición" (nube enmascarada) |
| **SCS+C** | Corrección del efecto de la pendiente en la iluminación |
| **s2cloudless** | Algoritmo de detección de nubes de Sentinel-2 |
| **L2A** | Nivel de producto ya corregido atmosféricamente |

---

## 11. Para seguir leyendo

- **COG** — `cogeo.org`
- **STAC** — `stacspec.org`; el tutorial de `pystac` es corto y concreto
- **MosaicJSON** — la spec vive en el repo `developmentseed/mosaicjson-spec`
- **TiTiler** — `developmentseed.org/titiler`
- **Catálogos públicos** — AWS Earth Search y Microsoft Planetary Computer
  publican Sentinel-2 L2A como COG con STAC, gratis
