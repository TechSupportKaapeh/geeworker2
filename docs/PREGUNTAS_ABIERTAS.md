# PREGUNTAS_ABIERTAS.md — Decisiones sin tomar

> Cada entrada es una **tarea**: se resuelve pasándola por las 6 etapas de
> [`WORKFLOW.md`](WORKFLOW.md). Última actualización: **2026-09-04**.
>
> Qué sigue en orden de ejecución: [`PLAN.md`](PLAN.md) · Decisiones ya tomadas:
> [`DECISIONS.md`](DECISIONS.md) · Estado del repo: [`HANDOFF.md`](HANDOFF.md)

Una pregunta sale de acá cuando se decide, y entra a `DECISIONS.md` con su
número. Lo que se decide y no se registra vuelve a discutirse en tres semanas.

---

## Cómo leer este documento

| Campo | Significa |
|---|---|
| **Bloquea** | Qué no se puede hacer hasta decidir |
| **Se vuelve caro** | Cuándo deja de ser barato cambiar de opinión |
| **Recomendación** | Lo que haría hoy con lo que sabemos. No es la decisión. |

Las tareas están agrupadas por **cuándo hay que decidirlas**, no por tema. Una
decisión que se puede posponer sin costo no es urgente aunque sea importante.

---

# A. Bloquean trabajo en curso

## A-1 · ¿Quién arma el MosaicJSON y dónde vive?

**Bloquea:** FASE C.5 · **Se vuelve caro:** cuando haya mosaicos servidos y
cacheados

El spike de la FASE B (2026-08-26) demostró que la composición funciona, pero
dejó la pregunta de dónde vive el documento. Ahora tenemos datos concretos:

| Opción | Cómo | Costo |
|---|---|---|
| **En el bucket** (`s3://`) | El worker lo escribe al ingerir | Ya cableado: `boto3` + `AWS_ENDPOINT_URL_S3`. Hay que regenerarlo en cada ingesta |
| **Lo sirve Geocore** (`https://`) | Lo arma por request desde `layers` | Nada que guardar ni invalidar. Requiere permitir un host en el filtro anti-SSRF, y `HttpBackend` pega a Geocore por tile |
| **Precalculado por período** | Un MosaicJSON por mes/semana | Rápido de servir, pero es un artefacto más que mantener sincronizado |

**Lo que ya no es duda:** el `S3Backend` de `cogeo-mosaic` usa **boto3**, que no
comparte ninguna variable con GDAL. Eso ya está resuelto en
`configure_gdal()`, así que la opción del bucket es viable hoy.

**Recomendación:** que lo sirva **Geocore**. Ya es el dueño del catálogo, ya
aplica aislamiento de tenant, y evita un artefacto que se puede desincronizar
del contenido real del bucket. El costo —abrir un host en el filtro de `?url=`—
es acotado y explícito. Medir antes el punto A-2, porque si hay que precalcular,
la respuesta cambia.

---

## A-2 · ¿Dónde está el corte entre componer al vuelo y precalcular?

**Bloquea:** A-1 y FASE C.5 · **Se vuelve caro:** cuando el front esté en manos
de usuarios

`DECISIONS #20` fija la regla —"ventanas chicas al vuelo, ventanas grandes
precalculadas"— pero **no dice dónde está el corte**, y el spike se hizo con
3 pasadas. Con ~73 pasadas al año, un composite anual al vuelo abre 73 COG por
tile.

**Es una medición, no una discusión.** Componer tiles con 3, 6, 12, 24 y 73
pasadas y medir latencia. Media tarde.

**Recomendación:** medirlo antes de decidir A-1. Es la única pregunta de esta
sección que se responde con datos y no con criterio.

---

## A-3 · El worker nunca escribió en el MinIO real

**Bloquea:** confianza en toda la ingesta · **Se vuelve caro:** ya lo es

La cadena de **lectura** está verificada de punta a punta (2026-08-26). La de
**escritura** no: las escrituras del worker están validadas con `PREPARE` y su
`storage_key` con un mock — que es exactamente el tipo de verificación en
aislamiento que esta misma semana demostró no alcanzar.

No es una decisión sino una tarea, pero está acá porque **no pertenece a ninguna
fase del `PLAN.md`** y por eso nadie la levanta.

**Recomendación:** disparar `process_parcela` a mano contra la infraestructura
desplegada, antes de invertir en C. Si el worker no escribe, todo lo demás es
teórico.

### ✅ La parte de MinIO, cerrada el 2026-09-07

**El worker escribe en el MinIO real.** Verificado con dos scripts, contra la
infraestructura desplegada:

| | Qué se probó |
|---|---|
| `scripts/check_write_path.py` | Los permisos de `worker-rw`: `PUT` chico, **`PUT` de 6 MiB por multipart** y round-trip de lectura |
| `scripts/check_ingest_real.py` | La cadena completa **GEE → GeoTIFF → COG → MinIO** con datos reales de Sinaloa |

Lo que eso cierra, en orden de importancia:

1. **La afirmación que llevaba semanas en rojo deja de ser verdad.** No es que
   "debería funcionar": subió 408 KB de GEE, produjo un COG de 428 KB y lo puso
   en el bucket.
2. **El multipart quedó verificado contra el motor de policies de MinIO.** Era
   lo que la sesión del 2026-09-01 dejó modelado *según la especificación de
   IAM* y sin confirmar. Un COG real pesa más de 5 MiB, así que ese era el
   camino de producción y no el `PUT` único.
3. **`DECISIONS #22` tenía una verificación pendiente escrita**: que el COG que
   produce `cog_converter.py` con `rio-cogeo` 5.4 fuera un COG válido, porque la
   FASE B se probó con archivos del spike y no con este convertidor.
   `cog_validate` lo acepta.
4. **Los cuatro escalones de configuración pasaron en el primer intento**, y el
   único fallo real —`…up.railway.app:9000` con `MINIO_SECURE=False`— lo detectó
   `validate_endpoint` **sin tocar la red**, nombrando las dos correcciones.
   Todo el trabajo del 2026-09-01 al 09-07 existía para eso.

### ⏳ Lo que falta

- 🔴 **Las escrituras a `geodata`**: `password authentication failed`. El formato
  de `DB_USER` es correcto y el project ref resuelve; falla la contraseña.
  Hasta que eso se arregle, `insert_layer` e `insert_measurement` fallan y
  `process_parcela` no puede terminar.
- ⏳ **Leer el COG de vuelta por el tileserver desplegado**, que cierra el
  círculo completo. Necesita un token de `GET /api/maps/token` de Geocore, y
  `MAP_TOKEN_SECRET` **no está en ninguno de los dos repos** —vive solo en las
  variables de Railway, que es lo que `DECISIONS #16` decidió—, así que no se
  puede firmar desde acá. El tileserver desplegado **sí** está sano: sus cuatro
  chequeos de `/health/ready` en verde y leyendo del mismo bucket.

---

## A-4 · `sentinel2_dates` es un caché de solo escritura

**Bloquea:** nada hoy · **Se vuelve caro:** cuando la cuota de GEE apriete

Apareció al ejecutar FASE D el 2026-08-30. Los handlers `process_parcela` y
`query_available_dates` insertan una fila en `sentinel2_dates` por cada fecha
devuelta por GEE, en **cada** pedido. Nadie lee la tabla: la única función que la
consultaba se importaba aliasada como `get_cached_dates` y no la llamaba nadie
(`DECISIONS #23`).

O sea que hoy se paga el costo de mantener un caché —una tabla que EF Core no
administra, una escritura por fecha por pedido— y no se cobra ninguno de sus
beneficios: cada pedido pega a GEE completo igual.

**Opciones:** leer el caché antes de ir a GEE, con una política de expiración ·
borrar la tabla y aceptar que cada pedido consulta GEE · dejarla como registro de
auditoría y decirlo por escrito.

**Recomendación:** decidirlo junto con FASE C, no antes. C cambia la forma de
consultar GEE —una pasada por vez en lugar de un composite— y con eso cambia qué
conviene cachear. Decidirlo ahora es decidir sobre un patrón de acceso que está
por cambiar.

---

## A-5 · ¿El rancho y sus parcelas se descargan por separado o se recorta uno del otro?

**Bloquea:** el diseño de FASE C · **Se vuelve caro:** después del backfill

Hoy `process_rancho` y `process_parcela` **bajan de GEE por separado**, y las
parcelas están geográficamente **adentro** del rancho: para un rancho con N
parcelas son N+1 descargas, y los mismos píxeles se calculan y transfieren dos
veces (una para el rancho, otra repartida entre las parcelas).

La alternativa evidente: **bajar el rancho una vez y recortar las parcelas
localmente** con `rasterio`, que es una lectura por ventana y cuesta casi nada.

### Lo que la hace atractiva no es solo el ahorro

Recortar de un padre común hace que **todas las parcelas queden alineadas al
píxel con el rancho y entre sí, por construcción**. Eso es exactamente lo que
`DECISIONS #19` exige —misma `region`, `scale` y `crs`— y hoy se cumple porque
los parámetros están centralizados (`services/ee/gee_download.py`), no porque
sea estructuralmente imposible que diverjan.

### Los tres obstáculos, en orden de dureza

1. 🔴 **El worker no puede enumerar las parcelas de un rancho, a propósito.**
   Para recortar hay que saber qué parcelas tiene, y eso es precisamente lo que
   `services/geocore_client.py` simulaba: partía el bbox en dos e inventaba
   parcelas. Se borró en **E.1**, y no se reemplazó por una llamada HTTP real
   porque habría pedido un endpoint nuevo en Geocore, auth service-to-service y
   una dirección de acoplamiento worker → Geocore que hoy no existe.
   **Este diseño necesita esa capacidad que se decidió no tener.**

2. **Los eventos son independientes.** `terra/parcela.created` puede llegar
   antes, después o sin `terra/rancho.created`. Hacer que la parcela dependa de
   que el ráster del rancho ya exista introduce un orden entre eventos que hoy
   no existe, y un caso "todavía no está" que hay que resolver.

3. **Hoy no son el mismo ráster.** El rancho es un composite de **30 días**; la
   parcela es la **pasada más reciente**, y además lleva una serie de 12 meses.
   Recortar exige unificar la semántica temporal — que es justo lo que FASE C va
   a cambiar de todos modos.

Y uno en contra del "bajar todo el rancho": **`getDownloadURL` tiene tope de
tamaño**. Un rancho entero a 10 m lo toca antes que una parcela suelta, así que
esta opción empuja hacia `ee.batch.Export` (C.6) más rápido, no menos.

### La pregunta de atrás, que puede hacer desaparecer el problema

**¿Hacen falta COG por parcela?** Un COG de parcela solo se necesita si el mapa
tiene que servir tiles de la parcela **independientes** del rancho. Si el ráster
del rancho ya cubre a todas sus parcelas, el front puede pintar el del rancho y
recortar la vista.

Y lo importante: **las mediciones por parcela no necesitan ningún COG.** Salen
de un `reduceRegion` en GEE, que devuelve un número sin descargar píxeles. O sea
que el caso de uso que más se consulta —la serie temporal— ya es eficiente y no
entra en esta discusión.

Si la respuesta es "no hacen falta", el diseño más barato no es recortar: es
**un ráster por rancho por pasada, y por parcela solo números**. Eso elimina las
N descargas en vez de abaratarlas.

### ✅ Respondida el 2026-09-04 por la UX prevista

El diseño de la vista contesta la pregunta de atrás, y con eso la de adelante
deja de importar. La UX es:

- **un mapa con los tiles del rancho**, servidos por TiTiler desde
  `ranchos/{id}/{fecha}_ndvi.tif`;
- **las parcelas dibujadas encima como polígonos**, con su id o su nombre;
- **métricas por parcela** al seleccionar una —porque el NDVI de un platanal, de
  una casa y de un cultivo de mora no significan lo mismo—;
- y **métricas del rancho** aparte.

**Los polígonos de las parcelas son vectores, no ráster.** Se dibujan con las
`coordinates` que ya viajan en `ParcelaDto` desde Geocore (`DECISIONS #13`), no
recortando píxeles. Y las métricas por parcela son **números**, que salen de un
`reduceRegion` en GEE sin descargar un solo píxel.

**Conclusión: no hacen falta COG por parcela.** Un COG de parcela solo se
justificaría si el mapa tuviera que servir tiles de la parcela independientes
del rancho, y esta UX no lo pide: pinta el ráster del rancho y superpone la
geometría.

Entonces el diseño no es "recortar el rancho para hacer N COG más baratos", es
**no hacer los N COG**:

| | Qué produce | Dónde vive |
|---|---|---|
| Rancho | **un ráster** por pasada/período | `ranchos/{id}/…` → tiles |
| Parcela | **solo números** | `measurements` → gráfico y panel |

Eso elimina las N descargas de GEE en vez de abaratarlas, y de paso desactiva el
obstáculo 1 —no hace falta enumerar las parcelas de un rancho, porque cada una
llega por su propio `terra/parcela.created`—.

**Lo que queda por decidir de C** es entonces si `process_parcela` sigue
produciendo su COG (`parcelas/{id}/…`). Con esta UX no se usa para el mapa. Los
`heatmaps/` on-demand sí siguen teniendo sentido: son un pedido explícito de un
ráster de una parcela, con su período y su índice.

**Lo que esta UX destapa, y hay que resolver antes de construirla:** ver **A-6**.

<details>
<summary>La recomendación anterior, antes de conocer la UX</summary>

Decidirlo dentro de FASE C, empezando por la pregunta de atrás; medir primero
cuántas parcelas tiene un rancho real y cuánto pesa una pasada (C.7).

</details>

---

## A-6 · La UX prevista necesita tres cosas que hoy no existen

**Bloquea:** la vista de mapa con métricas · **Se vuelve caro:** cuando el front
esté construido contra un contrato que no se puede cumplir

Salió de describir la UX en A-5. Las tres son de esquema o de decisión, no de
código del worker.

### 0. ¿Para qué `rancho_id`, si la parcela ya pertenece a un rancho?

**No es para las filas de parcela.** Ahí sería denormalización: `parcela_id` ya
determina el rancho, y duplicarlo abre la puerta a que los dos no coincidan si
una parcela cambia de rancho.

Es para **filas que no tienen parcela**: la medición del rancho entero, que es
una fila de otra naturaleza en la misma tabla.

**Y `layers` ya resolvió exactamente este problema.** Tiene `parcela_id` y
`rancho_id`, las dos nullable, con la regla *"una capa es de rancho o de parcela,
nunca de las dos"* — que `insert_layer` ya hace cumplir levantando si no viene
ninguna. Que `measurements` lo espeje es consistente con la tabla hermana, con el
mismo `DbContext` y con una regla que el código ya aplica.

⚠️ **Pero no es "agregar una columna": cambia la primary key.** Hoy es
`(parcela_id, indice, fecha)`, y una columna de PK no puede ser NULL. Con
`parcela_id` nullable hace falta:

- una PK sustituta (`id`),
- un `CHECK` de que exactamente una de las dos ids no sea nula,
- y **dos índices únicos parciales**, uno por caso.

Eso último toca el contrato de idempotencia: `insert_measurement` se apoya en
`ON CONFLICT (parcela_id, indice, fecha)`, y con un índice parcial la cláusula
necesita el predicado (`... WHERE parcela_id IS NOT NULL`). Es un cambio en el
worker, no solo en Geocore.

**Las tres opciones, honestamente:**

| | Costo | Consecuencia |
|---|---|---|
| **(a) Espejar `layers`** | migración + PK nueva + tocar el `ON CONFLICT` | Una tabla, una forma de consulta para el front. Consistente con la tabla hermana |
| **(b) Tabla `rancho_measurements` aparte** | migración simple, sin cirugía de PK | Esquema duplicado y el front hace dos consultas para una pantalla |
| **(c) Derivarlas al consultar** | cero migración | **El número significa otra cosa** — ver abajo |

### ✅ Decidido el 2026-09-04: **(b), una tabla aparte**

La recomendación inicial era (a) —espejar `layers`— por consistencia. **Se
revirtió**, porque consistencia con un patrón discutible no lo vuelve bueno: el
arco exclusivo es un olor de normalización, y la objeción era correcta.

Tres razones concretas, en orden de peso:

**1. Riesgo.** (a) modifica la **primary key de una tabla en la que el worker ya
escribe**, y la idempotencia de esa escritura es contrato
(`ARCHITECTURE_PLAN` §5). Verificado en Geocore:

```csharp
builder.HasKey(m => new { m.ParcelaId, m.Indice, m.Fecha });
```

Una tabla nueva **no toca nada existente**: es la migración más segura que hay.
En un repo donde cambiar el esquema ya rompió al worker en silencio durante
cuatro días (`DECISIONS #15`), esa diferencia pesa.

**2. Nadie consulta sin saber el sujeto.** Es la asimetría con `layers`, y es la
que decide:

- A `layers` se le pide **una capa por su id**, y a quien la pide le da igual de
  quién es. El arco tiene sentido: el sujeto varía y no se usa para buscar.
- A `measurements` **siempre** se le pide *"la serie de esta parcela"* o *"la del
  rancho"*. El sujeto está en la consulta, siempre.

Si nadie consulta sin discriminar, la tabla única no compra nada y solo paga el
nullable.

**3. La jerarquía se resuelve un paso antes.** `rancho → parcela` vive en la base
principal, y el front la usa para dibujar los polígonos **antes** de pedir
mediciones — o sea que llega con el `parcela_id` en la mano. Verificado:
`GET /api/parcelas?ranchoId=…` existe (`ParcelasController.GetByFilter`).

El contraargumento —*"sin `rancho_id`, Geocore tiene que ir primero a la base
principal por los ids"*— **no se sostiene**: esos ids ya están pedidos, porque
sin ellos no hay polígonos que dibujar. La consulta a `geodata` es una sola,
`WHERE parcela_id = ANY(...)`.

**Lo que hay que aceptar:** esquema duplicado —una columna nueva se agrega en dos
lados— y un endpoint nuevo en Geocore para la métrica de rancho. Con 7 columnas
estables, es el precio correcto.

**Y es barato de revertir:** `terra/parcela.created` **ya trae `ranchoId`**, y el
worker hoy lo ignora a propósito. Si aparece una consulta que mezcle niveles,
agregar la columna es aditivo y el dato ya está llegando.

### 1. 🔴 Las métricas del rancho no tienen dónde guardarse

`measurements` es **por parcela y solo por parcela**:

```
measurements   parcela_id, indice, fecha, tenant_id, valor, min_val, max_val
               PK: (parcela_id, indice, fecha)
```

No hay `rancho_id`. Y `process_rancho` **no escribe ninguna medición**: produce
el ráster y emite `terra/raster.ingested`, nada más. (El step que las calculaba
era el simulacro que borró E.1.)

O sea que "las métricas del rancho" hoy **no existen ni pueden existir**.
Opciones:

- **Agregar `rancho_id` nullable a `measurements`** y que `process_rancho`
  escriba su propio `reduceRegion`. Es un pedido de migración a Geocore.
- **Derivarlas al consultar**, promediando las parcelas. Ojo: no es lo mismo.
  Es un promedio ponderado por área, y **excluye la superficie del rancho que no
  pertenece a ninguna parcela** —caminos, construcciones, monte—. Para un rancho
  parcialmente parcelado, los dos números van a diferir y no hay uno "correcto":
  responden preguntas distintas.

**Hay que elegir a conciencia cuál de las dos es "la métrica del rancho"**,
porque la UX las va a mostrar al lado de las de parcela y el usuario va a
esperar que cierren.

### 2. B-1 se vuelve visible en la misma pantalla

`B-1` ya advertía que el mapa compone por **mediana** (lo que hace `rio-tiler`)
y el gráfico usa **promedio**. En esta UX los dos están **en la misma vista al
mismo tiempo**: el tile pintado y el número de la parcela. Un usuario que
compare el color con la cifra va a encontrar la discrepancia enseguida.

**Deja de ser una inconsistencia teórica y pasa a ser un bug reportable.**

### 3. La escala de color y el umbral de nubes

- **D-2** — el front hoy fija `rescale=-1,1`. Con un solo ráster de rancho
  cubriendo platanal, casa y mora, una escala fija aplasta las diferencias
  dentro de cada parcela. Escalar por parcela necesita estadísticas por parcela
  — que `export_heatmap` **ya calcula y tira**, porque `layers` no tiene
  columnas para min/max/mean/stddev.
- **B-4** — si el 80% de una parcela está nublada, su número no la representa.
  Hoy se guardaría igual. Con la métrica en pantalla junto al polígono, eso es
  un valor fantasma que el usuario ve.

**Recomendación:** juntar el punto 1 con **D-1** y **D-2** en **un solo pedido
de migración a Geocore** —`rancho_id` en `measurements`, columnas de
estadísticas y de parámetros de procesamiento en `layers`—. Cambiar el esquema
cuesta coordinación entre dos repos; hacerlo tres veces cuesta el triple. Y
resolver **B-1** antes de que el front muestre las dos cifras juntas.

---

## A-7 · ¿La convención de keys de MinIO sirve para consultar?

**Bloquea:** nada hoy · **Se vuelve caro:** cuando FASE C multiplique los objetos
por ~73 al año

Las cuatro convenciones actuales:

```
ranchos/{ranchoId}/{fecha}_ndvi.tif
parcelas/{parcelaId}/{fecha}_ndvi.tif
heatmaps/{parcelaId}/{fechaInicio}_{indice}.tif
exports/{parcelaId}/{fecha}_{indice}.{fmt}
```

### Lo primero: **el bucket no es el catálogo**

La sensación de que "son toscas para consultar" tiene una respuesta antes que un
rediseño: **no hay que consultar el bucket.** El catálogo es `geodata.layers`,
que tiene `product`, `acquired_ts`, `bbox`, `tenant_id`, `parcela_id`,
`rancho_id` e índices de verdad. Geocore lo sirve al front y **nunca parsea la
key**: solo concatena `s3://{bucket}/{storage_key}`.

Si alguna vez se responde una pregunta listando el bucket, eso es el síntoma —
S3 solo sabe filtrar por prefijo, y ninguna base de datos que se respete se
consulta así. **Antes de rediseñar las keys conviene confirmar que la pregunta
va a `layers`.**

### Dónde sí importa la forma de la key

Dos cosas, y las dos son reales:

1. **Las políticas de retención de S3 son por prefijo** (`PREGUNTAS_ABIERTAS`
   C-5). "Borrar los on-demand con más de 90 días" se expresa como regla sobre
   `heatmaps/` — pero "borrar las pasadas de más de 3 años **menos la última de
   cada año**" no se expresa por prefijo de ninguna forma. La estructura de la
   key decide qué políticas son posibles.
2. **Depuración humana.** Cuando algo falla, alguien abre `mc ls` y mira.

### Los dos problemas concretos que sí tiene

**`heatmaps/` está organizado por *por qué* se pidió, no por *qué es*.** Un
heatmap NDVI de la parcela X y el ráster sistemático NDVI de la parcela X son
**el mismo tipo de objeto** en carpetas distintas, y se pueden pisar
conceptualmente: si el on-demand pide el mismo índice y fecha que ya existe en
`parcelas/`, se calcula dos veces y se guarda dos veces. Eso es **E.9**, ya
anotado.

**`{fecha}_{indice}` pega dos dimensiones en el nombre de archivo.** S3 filtra
por prefijo, así que la dimensión que varía más —la fecha— debería ir **después**
de la estable, no antes. Con la forma actual, "todo el NDVI de la parcela X" no
es un prefijo: hay que listar la carpeta entera y filtrar por sufijo del lado del
cliente. Con FASE C son ~73 objetos por índice por año en una carpeta plana.

Curiosamente **el COG de prueba del 2026-08-26 usó una forma mejor** —
`ranchos/{id}/pasadas/{fecha}/ndvi.tif` — con el índice al final y la fecha como
segmento. Se subió a mano y el worker nunca la produjo.

### Lo que costaría cambiarlo

`storage_key` es **contrato con Geocore**, pero de forma débil: Geocore lo trata
como texto opaco. O sea que cambiar la convención **no rompe a Geocore**, pero sí
deja las filas viejas de `layers` apuntando a la forma anterior. Habría que
migrar los objetos del bucket y las filas, o aceptar dos convenciones conviviendo
—que es peor que cualquiera de las dos—.

**Recomendación:** **no rediseñar todavía, y hacerlo junto con FASE C**, que es
la que multiplica los objetos y la que va a exigir mover el histórico de todas
formas. Antes de eso, dos cosas baratas:

- **Confirmar que ninguna consulta lista el bucket.** Si todas van a `layers`,
  la convención solo tiene que ser legible y compatible con las políticas de
  retención, no consultable.
- **Escribir la política de retención primero** (C-5). La estructura de la key
  debe salir de ahí, no al revés: es la única consumidora real de la jerarquía.

Forma candidata, para cuando toque:

```
{entidad}/{id}/{indice}/{fecha}.tif      ← "todo el NDVI de X" es un prefijo
```

---

# B. Hay que decidirlas antes de migrar a ingesta por pasada (FASE C)

## B-1 · ¿Mediana o promedio?

**Bloquea:** C.5 · **Se vuelve caro:** en cuanto el cliente vea los dos números

Hoy el mapa haría **mediana** (lo que compone `rio-tiler`) y el gráfico de series
usa **promedio**. **Van a dar distinto para el mismo mes**, y el usuario los va a
ver uno al lado del otro.

**Recomendación:** que los dos usen mediana. Es más robusta a nubes residuales,
y es lo que el mapa hace de todas formas. Un mapa y un gráfico que se contradicen
erosionan la confianza más rápido que cualquier bug.

### ✅ Decidido el 2026-09-04: mediana en los dos — pero **no van a dar igual**

La decisión se toma, y con ella una advertencia que la recomendación original no
tenía. **Unificar el reductor no hace que las dos cifras coincidan**, porque la
diferencia no es solo el reductor: son **dos ejes de agregación distintos**.

| | Qué agrega | Cómo |
|---|---|---|
| El mapa | **en el tiempo**, píxel por píxel | mediana sobre las pasadas |
| El gráfico | **en el espacio**, y después en el tiempo | media espacial por pasada → agregación mensual |

O sea que hoy el gráfico calcula `agregado_temporal( media_espacial(pasada) )` y
el mapa muestra `mediana_temporal(píxel)`. **El orden de las operaciones es
distinto**, y `media_espacial(mediana_temporal) ≠ mediana_temporal(media_espacial)`
salvo en una parcela perfectamente homogénea y sin nubes.

Lo que la decisión sí resuelve es la **parte visible y grande**: cambiar la
agregación mensual del gráfico de media a mediana sobre las pasadas. La
diferencia por orden de operaciones queda, es chica en parcelas homogéneas, y
crece con la heterogeneidad y las nubes parciales.

**Por qué no se elimina del todo:** hacerlo exigiría calcular el número **desde
el composite** en vez de por pasada, y eso contradice `DECISIONS #19`, que guarda
por pasada justamente para poder reagrupar a cualquier cadencia. Se prefiere
conservar esa libertad y **documentar** que las dos cifras son parientes, no
gemelas.

⚠️ **Y hay un tercer eje que nadie había mirado: la resolución.** El ráster se
descarga a `scale=10`; la serie temporal reduce a **`scale=60`**
(`ee_client.py::get_sentinel2_time_series`, con `maxPixels=1e5` y
`bestEffort=True`). El número y la imagen no vienen de la misma grilla. Para una
parcela chica, 60 m son muy pocos píxeles. **Esto hay que decidirlo con B-1**, y
probablemente sea la mitad de la discrepancia.

---

## B-2 · El escalón al migrar el histórico

**Bloquea:** la migración de datos de C · **Se vuelve caro:** después de migrar

`DECISIONS #19` lo advierte: se pasa de `mediana(bandas) → NDVI` a
`mediana(NDVI)`. Como el NDVI es un cociente, **no son equivalentes**. Al
comparar series viejas con nuevas va a aparecer un escalón que no es del cultivo.

Ninguna de las dos formas es incorrecta. Lo que no se puede es mezclarlas sin
avisar.

**Opciones:** recalcular todo el histórico con el método nuevo · marcar la fecha
de corte en la UI · aceptar el escalón y documentarlo.

**Recomendación:** recalcular el histórico. Es caro una vez; un escalón sin
explicar es caro cada vez que alguien mira el gráfico.

---

## B-3 · Cadencia: ¿mensual, quincenal o semanal?

**Bloquea:** C.5 · **Se vuelve caro:** poco — es un `GROUP BY`

Con lluvias de junio a septiembre, la semanal va a tener huecos.

**Recomendación:** guardar por pasada (ya decidido en `DECISIONS #19`) y dejar la
cadencia como parámetro de consulta. Esta pregunta solo importa para el **valor
por defecto** del front. Empezar en mensual.

---

## B-4 · Umbral de píxeles válidos por parcela

**Bloquea:** C.4 · **Se vuelve caro:** en cuanto el cliente vea valores fantasma

### ⚠️ Corrección del 2026-09-04: **el umbral ya existe**

La pregunta estaba mal planteada, y lo que decía —*"hoy se guardaría como si
nada"*— **es falso**. Verificado en `services/ee/ee_client.py`:

```python
def check_roi_coverage(img, roi):
    # cuenta pixeles validos (no enmascarados) vs pixeles totales del ROI
...
filtered = (processed
            .map(lambda img: check_roi_coverage(img, roi))
            .filter(ee.Filter.gte('roi_coverage', min_coverage)))   # min_coverage=0.5
```

O sea que **una pasada que cubre menos del 50% de la parcela con píxeles válidos
ya queda fuera de la serie**. El conteo es post-máscara de nubes (s2cloudless +
sombras, `max_prob=45`), así que mide exactamente lo que la pregunta quería
medir.

### Lo que sí está sin decidir, y es más sutil

**El umbral existe pero la calidad de cada punto varía en silencio.**

1. **El valor `0.5` está hardcodeado** y nadie lo decidió: no está en `config.py`
   ni documentado. ¿50% es el corte correcto para una parcela de plátano?
2. 🔴 **Hay un fallback que relaja el filtro sin avisar:**

   ```python
   cloud_thresholds = [min(cloud_pct, 80), 90]
   for threshold in cloud_thresholds:
       ...
       if size > 0:
           break
   ```

   Si con el umbral pedido no aparece ninguna imagen, **reintenta con 90% de
   nubes**. El punto que devuelve no dice con qué umbral salió. Dos puntos
   contiguos de la misma serie pueden venir de una pasada con 30% de nubes y de
   otra con 90%, y **nada los distingue**.
3. **`measurements` no tiene dónde registrar esa calidad** — no hay columnas para
   cobertura, umbral usado ni `quality`. Es el mismo pedido de migración de A-6
   y D-1.

**En la UX esto importa mucho más que antes:** el número va al lado del polígono
de la parcela, sin contexto. Un valor derivado de una pasada al 90% de nubes se
ve idéntico a uno bueno.

**Recomendación:** sacar el `0.5` y el fallback a `config.py` como parámetros
explícitos, **registrar la cobertura real de cada punto** (junto con A-6/D-1), y
decidir si el fallback a 90% debe existir — hoy prefiere *un dato malo* sobre
*ningún dato*, y esa es una decisión de producto que nadie tomó.

---

## B-5 · Zona horaria de los períodos

**Bloquea:** C.5 · **Se vuelve caro:** después del primer backfill

Una pasada del 1-ago 00:30 UTC es el 31-jul en México. Agrupar por mes en UTC o
en hora local da meses distintos.

**Recomendación:** agrupar en la zona horaria del rancho, no en UTC. El usuario
razona en su calendario. Guardar siempre en UTC y convertir al agrupar.

---

## B-6 · Qué hacer con períodos vacíos

**Bloquea:** C.5 · **Se vuelve caro:** bajo

Va a pasar en temporada de lluvias: meses sin ninguna pasada utilizable.

**Recomendación:** devolver el período con el valor nulo y que el front dibuje el
hueco. Interpolar inventa datos; omitir la fila hace que el eje temporal mienta
sobre la continuidad.

---

# C. Deuda de infraestructura y seguridad

## C-1 · Los assets del MosaicJSON no pasan por el filtro anti-SSRF

**Bloquea:** nada hoy · **Se vuelve caro:** si alguna vez se aceptan mosaicos de
otro origen

El `path_dependency` del tileserver valida la URL del MosaicJSON, pero
`cogeo-mosaic` abre **los assets que ese documento lista adentro** tal como
vengan, incluidos `http://` hacia la red interna. Es la misma clase de agujero
(OWASP A10) que se cerró para `?url=`, una capa más adentro.

**Hoy está contenido** porque solo `worker-rw` puede escribir en el bucket: un
MosaicJSON solo puede aparecer ahí si lo puso el worker. Queda documentado en
`main.py`.

**La decisión de A-1 cambia esto.** Si el MosaicJSON pasa a servirlo Geocore por
HTTP, el contenido deja de estar respaldado por los permisos del bucket y hay que
validar los assets explícitamente.

**Recomendación:** decidir A-1 primero. Si gana Geocore, esta tarea deja de ser
teórica y pasa a bloqueante.

---

## C-2 · `rasterio` y GDAL entran sin pinnear

**Bloquea:** nada · **Se vuelve caro:** en un rebuild dentro de meses

`requirements.txt` del tileserver fija `titiler.core==0.18.0` y
`titiler.mosaic==0.18.0`, pero `rasterio`, `rio-tiler` y GDAL entran como
dependencias transitivas. La imagen se lleva lo que haya el día que se
construya, y el comportamiento de composición depende de esas versiones.

**Recomendación:** pinnear al menos `rio-tiler` y `rasterio`, y correr
`scripts/check_mosaic_median.py` después de cada cambio de versiones. El script
existe justamente porque esta respuesta puede cambiar sin que nadie la toque.

---

## C-3 · Backup del volumen de MinIO

**Bloquea:** nada · **Se vuelve caro:** el día que se pierda el volumen

Railway no trae snapshots. Hoy los COG se pueden regenerar desde GEE, así que la
pérdida cuesta cuota y tiempo, no datos irrecuperables — **mientras el backfill
siga siendo reproducible**.

**Recomendación:** aceptar el riesgo **por escrito** ahora, y revisarlo cuando el
costo de regenerar supere el de un backup. No dejarlo sin decidir: un riesgo no
registrado no es un riesgo aceptado, es un olvido.

---

## C-4 · Consola de MinIO pública

**Bloquea:** nada · **Se vuelve caro:** si se filtra la credencial root

Se decidió dejarla abierta para monitoreo (2026-08-21). Es la superficie de
administración root. La alternativa sin costo es `mc admin info` desde la
terminal, que ya está configurada y funcionando.

**Recomendación:** cerrarla ahora que `mc` está andando y verificado. La razón
para dejarla abierta era no tener otra vía de monitoreo, y esa razón ya no
aplica.

---

## C-5 · Retención de pasadas

**Bloquea:** nada · **Se vuelve caro:** lento y en silencio

~73 pasadas por año, por índice, por entidad. Es barato, pero sin política es
"para siempre".

**Recomendación:** no decidir todavía, y **poner una fecha para decidir**. Es el
tipo de pregunta que no duele hasta que duele mucho.

---

## C-6 · Inngest: Cloud o self-hosted

**Bloquea:** F.1 y el despliegue del worker · **Se vuelve caro:** después de
desplegar el worker

Define si el worker necesita URL pública o puede quedar privado, y si
`INNGEST_SIGNING_KEY` hace falta.

**Recomendación:** Cloud para la demo. Self-hosted agrega un servicio más que
mantener para resolver un problema que todavía no existe.

---

# D. Trazabilidad y esquema

## D-1 · Los parámetros de procesamiento no se registran

**Bloquea:** nada · **Se vuelve caro:** cuando alguien pregunte por qué cambió un
valor

`max_prob=45` está hardcodeado y `layers` no registra con qué parámetros se
procesó cada capa. Si mañana se cambia el umbral de nubes, no hay forma de saber
qué capas se hicieron con cuál.

**Recomendación:** agregar los parámetros a `layers` cuando se pida la migración
de D-2. Son el mismo cambio de esquema y el mismo pedido a Geocore.

---

## D-2 · `layers` no tiene columnas para estadísticas de ráster

**Bloquea:** nada · **Se vuelve caro:** bajo

Sin `min`/`max`/`mean`/`stddev`, el front no puede fijar la escala de color sin
abrir el ráster. Hoy eso se resuelve con `rescale=-1,1` fijo, que funciona para
NDVI pero no para índices con otro rango.

**Recomendación:** juntarlo con D-1 en un solo pedido de migración a Geocore.
Cambiar el esquema cuesta coordinación entre dos repos; hacerlo dos veces cuesta
el doble.

---

# E. Proceso

## E-1 · El `WORKFLOW.md` no cubría los repos de Python

**Estado:** ✅ resuelto el 2026-08-27

El guardrail decía *"Scope: solo Geocore/ y terra-admin/"* y la etapa VERIFY solo
conocía `dotnet build` y `npx tsc`. Los dos repos que se trabajan hoy —GeeWorker
y el tileserver— son Python y no estaban contemplados, así que el pipeline no se
podía seguir aunque se quisiera.

Resuelto extendiendo el scope y agregando los comandos de verificación de Python.
Se registra acá porque explica por qué el trabajo previo a esta fecha no cita las
etapas del workflow.

---

## E-2 · La etapa AUDIT sigue siendo manual

**Bloquea:** nada · **Se vuelve caro:** cuando el equipo crezca

El propio `WORKFLOW.md` lo reconoce: la auditoría de los 5 ejes es heurística, no
determinista y no reproducible. La herramienta SAST sigue **por definir**.

Esta semana dio un ejemplo concreto de la limitación: el falso negativo de
`GetBucketLocation` (`DECISIONS #21`) no lo habría encontrado ningún SAST, pero
tampoco lo encontró la auditoría manual — lo encontró reproducir el fallo contra
un socket real. **Los tests contra la realidad atraparon lo que la revisión no.**

**Recomendación:** priorizar Semgrep sobre SonarQube: corre por terminal, sin
servidor, y cubre Python y C# con un solo comando. Pero registrar también la
lección de arriba, que es más barata y más efectiva: **preferir una prueba contra
el sistema real a una revisión más cuidadosa**.
