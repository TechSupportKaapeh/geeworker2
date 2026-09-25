# Sesión 15 — el agrupamiento (2026-09-25)

> **M.9.0b.** Sacar "el mes" de los cinco lugares donde vivía y ponerlo en uno, **sin que
> ningún número cambie**. La aceptación no era que los tests quedaran verdes —los tests los
> tocaba yo— sino que las filas de antes y las de después fueran idénticas, y eso se comparó
> contra el código de `main` corriendo al lado.
>
> Decisión: [`DECISIONS #67`](DECISIONS.md). Diseño:
> [`ARQUITECTURA_PIPELINE.md` §3.5](ARQUITECTURA_PIPELINE.md).

## Qué se hizo

`pipeline/ventanas.py`, que es la abstracción que el compuesto ya era sin decirlo: **de un
pedido a una lista de ventanas**. Una `Ventana` es `(etiqueta, inicio, fin)`, y **la etiqueta es
lo único que llega afuera** — la key del COG, la `fecha` de la fila y el `periodo` del job salen
de ahí. Con `del_mes(mes)` la etiqueta es `AAAA-MM`, así que nada de eso cambió.

El resto del pipeline dejó de saber qué es un mes: `fuente.coleccion`, `productos.compuesto_de`
/ `estadisticas_de` / `mapa_de`, `ejecucion.reduccion_de`, `filas.filas_de` y las tres
`claves_cog_*` reciben una ventana. `Mes` queda donde corresponde: en el job y en la bitácora,
que **no se tocaron** (`#63`).

## Las cuatro decisiones, y por qué

**Una llamada más al borde, no una descripción perezosa.** §3.5 dejaba las dos abiertas y `#63`
ya había elegido. `ejecucion.fechas_de(roi, pedido, receta)` es esa llamada, y `partir` quedó
**puro**: una función de Python sobre datos, que se prueba sin credenciales. Quien orquesta
pregunta las fechas sólo si `necesita_fechas`, y con `entero` no pregunta nada — **un mes cuesta
las mismas llamadas que antes**, y hay un test `gee` que lo fija en 0.

**`mensual` y `rango_libre` eran la misma función.** El diseño listaba cuatro agrupamientos;
dos resultaron ser "una imagen con todo el pedido adentro", y lo que los distinguía era el
pedido. Quedó uno, `entero`. Que la abstracción colapse dos casos es la señal de que está en el
lugar correcto: si hubiera que escribir `rango_libre` aparte, la ventana no sería el concepto.

**Dos campos en la receta, no uno.** `agrupamiento_estadisticas` y `agrupamiento_raster`. El
ráster y los números tienen costos distintos y `#31` eligió mensual con la cuenta del ráster;
con un agrupamiento global se repetiría el error que M.9 viene a corregir.

**El ráster exige una sola ventana, y lo dice en vez de generalizarse.** `handlers/rancho.py`
lee `agrupamiento_raster`, arma su ventana y **rechaza** la receta que parta el mes en más de
una. Es el camino más caro y más frágil del worker —descarga, COG y subida—, y un bucle cuyo N
es siempre 1 sería código que nadie ejecuta hasta que alguien cambie la receta, y que fallaría
justo ahí. Vale más un error que dice qué falta hacer.

## La huella de v1, que es la decisión que no era mía

Los dos campos nuevos cambian la huella de `s2-mensual-v1`, que **ya escribió filas en
producción** — y la regla de `#36` dice que una versión se congela con su primera fila. La
consulté, porque era una regla del usuario y el caso no estaba contemplado: **se re-fija sin
subir la versión**.

El porqué quedó escrito en `HUELLAS`: lo que esa regla protege es que no se pueda mirar un
número guardado y no saber con qué parámetros salió, y acá **ningún número se movió** — un
supuesto que estaba cableado pasó a estar escrito, con el valor que ya tenía. Pasar a `v2`
habría dejado filas `v1` y `v2` con números idénticos en la misma tabla, que es peor para esa
misma pregunta.

## El control negativo, que era la tarea

Los tests verdes no probaban nada: los estaba tocando yo. Lo que prueba algo es comparar **lo
que produce el código de `main` contra lo que produce la rama**. Se hizo con un `git worktree`
de `main` al lado y un volcador que se adapta a las dos API:

| Qué | Cuánto |
|---|---|
| Puro, sin GEE: los 24 meses de la receta × 4 coberturas que cruzan el umbral en los dos sentidos, las tres familias de claves de capa, y el intervalo que se le pide a GEE | 120 entradas |
| Contra GEE: 3 parcelas reales × 3 meses, incluido **2026-05**, que es el mes en que las 10 escenas quedan tapadas por la máscara y la cobertura da 0 | 9 entradas |

**Las 129 dieron idénticas**, campo por campo. Y en los tests **no se tocó ninguna
expectativa**: sólo las llamadas. Lo revisé con un diff filtrado, y la única línea de
aserción que cambió es la huella.

## El hallazgo: la ventana de una pasada todavía no selecciona

`por_pasada` parte bien, pero **la ventana que produce no sirve todavía para seleccionar las
escenas de esa pasada** — y por eso ninguna receta la usa. No lo encontró leer el código: lo
encontró correr el camino entero contra GEE, que es la regla del `WORKFLOW`, *probar contra lo
real y no contra lo que uno cree*. El error era `Image.select: Band pattern 'ndvi' was applied
to an Image with no bands`, que no menciona la causa por ningún lado.

`S2_SR` y `S2_CLOUD_PROBABILITY` comparten el `system:index` —que es por donde las une
`fuente.coleccion`— pero **no el `system:time_start`**:

| ROI | Escenas | Desfase SR − nubes |
|---|---|---|
| Una parcela real de los Llanos | 105, de 12 meses | **129 a 260 s** |
| El cuadrado de prueba del Bajío | las de 2026-07 | **668 a 1169 s** (casi 20 min) |

O sea que el desfase **depende de dónde caiga el ROI en la pasada**. Eso es lo que mata la
salida fácil: no hay un margen chico que sirva para todos, y elegir uno a ojo habría funcionado
en los Llanos y fallado en México.

**Lo que M.9.0c tiene que hacer**: filtrar la colección de nubes por un **superconjunto** del
pedido y dejar que el join por `system:index` —que es exacto— haga el resto. El filtro de fecha
sobre las nubes es una optimización, no un criterio.

**Por qué no entró acá**: eso **cambia el borde del mes**. Una escena de los primeros minutos de
un mes tiene su imagen de nubes en el mes anterior y hoy se descarta; con el filtro ancho
pasaría a contarse. Es correcto, y es un cambio de números — justo lo que esta tarea no podía
hacer. Queda fijado en un test `gee` y repetido en el docstring de `_por_pasada`, que es donde
va a mirar quien haga M.9.0c.

De paso quedó medido algo que el pipeline suponía sin decirlo: **2 de 105 escenas no tienen
imagen de nubes**, y `fuente.coleccion` las descarta a propósito desde M.2.1.

## Dos cosas chicas que aparecieron

**El ruff estricto de `handlers/` lee el "Todo" español como un `TODO`.** Un comentario que
empezaba con "Todo el trabajo de GEE…" disparó cinco reglas (`TD002`, `TD003`, `TD004`,
`TD006`, `FIX002`). Se reescribió la frase; no hay nada que configurar.

**El nombre `etiqueta` ya estaba tomado en `claves.py`**, donde significaba la familia de la
capa (`mensual`, `ondemand`). Pasó a llamarse `familia`, porque ahora `etiqueta` es la de la
ventana y las dos entran en la misma `natural_key`.

## Verificación

| Qué | Resultado |
|---|---|
| El control negativo contra `main` | **129 entradas idénticas**, 120 puras y 9 contra GEE |
| `pytest tests -q` | **683 verdes**, 21 omitidos (eran 637 + 22 de `ventanas` + 4 `gee` nuevos, de los cuales 4 se omiten sin `--gee`) |
| `pytest --gee -m gee` de `ejecucion` | 9 verdes, incluidos los tres nuevos de `fechas_de` y el que fija el desfase |
| `ruff check pipeline/` y `ruff format --check pipeline/` | limpios |
| `ruff check --select BLE .` | limpio |
| `scripts/check_pipeline_real.py --pasadas` | corrido contra GEE después del refactor, mismos números |

---

# Y después: M.9.0c, la mitad del worker

> **geeworker2#74**, `DECISIONS #69`. `s2-pasada-v2` existe, está verificada contra GEE y
> **no es la vigente**. Producción no cambia.

## Lo que entró

La receta v2 es **v1 con dos campos cambiados y ninguno más** —hay un test que lo fija—:
`agrupamiento_estadisticas: por_pasada` y `umbral_al_escribir: False`. El ráster sigue mensual
en las dos.

`umbral_al_escribir` es un **campo de la receta** y no una rama en `filas.py`: así entra en la
huella y queda escrito por receta. Descartar al escribir es la reducción con pérdida que no se
puede deshacer.

Y entró el arreglo que M.9.0b había dejado anotado: **el filtro de fecha de la colección de
nubes pasa a ser un superconjunto del pedido**, un día de cada lado. Quien decide qué escena
entra es el join por `system:index`, que es exacto.

## Dos cosas que se midieron antes de darlas por buenas

**El arreglo del filtro se aplicó a las dos recetas**, porque era un bug y no una diferencia de
receta: una escena de los primeros minutos de un mes tenía su imagen de nubes en el mes anterior
y se descartaba entera. La condición para hacerlo era medir el efecto sobre v1, y se midió:
**576 escenas unidas antes, 576 después, 0 meses en que cambie algo** sobre 3 parcelas reales ×
24 meses. Con el paso a las 15:11 UTC ninguna escena cae en los primeros minutos de un mes.

**Y el control negativo de siempre**, contra el `main` de ahora: las 129 entradas, idénticas.
Cubre las dos cosas que podían haber movido v1 sin querer — el filtro ancho y el campo nuevo.

## Lo que se ve al correrlo contra GEE, y conviene saber antes del switch

| Mes | Pasadas | Qué salió |
|---|---|---|
| 2026-07 | 9 | 36 filas, 36 claves distintas |
| 2025-02 | 5 | 20 filas. NDVI de 0,443 a **0,107** dentro del mes — el evento que la mediana mensual (0,377) borra |
| 2026-05 | 10 | 40 filas, **todas sin valor** |

El último es el costo concreto de «por pasada puro»: **un mes enteramente nublado pasa de 4
filas a 40**, todas con cobertura 0. Entra en la estimación de `#63` y es información —«hubo una
pasada el día 3 y no sirvió» no es «no hubo pasada»—, pero es lo que hay que tener a la vista
antes de poner v2 vigente.

Armar las ventanas cuesta **una sola llamada** a GEE, la de `fechas_de`.

## Lo que falta

`/api/measurements` tiene que agregar, con **`mensual` por defecto**: eso es lo que hace que
poner v2 vigente **no rompa el panel de hoy**, y es la regla de M.8.1 con el que lee en el lugar
del que exige. El número mensual es la **mediana de las medianas por pasada**, que es la que
`#66` ya midió. Va con SQL crudo, porque `percentile_cont` no lo traduce EF Core y agregar en
memoria pediría traer ~3.800 filas contra un techo de 2.000.

---

# Y al final: v2 pasa a ser la vigente

> **geeworker2#75**, `DECISIONS #70`. Decisión del usuario. **Esto sí cambia producción**: las
> altas y el cierre escriben una fila por pasada.

**El orden se respetó, y era la condición.** `/api/measurements` ya agrega con
`cadencia=mensual` por defecto desde Geocore#60, así que el panel pide lo mismo y recibe puntos
mensuales sin tocar una línea. Sobre filas mensuales, agrupar por mes es la identidad — y hay
un test contra PostgreSQL que lo fija. Es la regla de M.8.1 con el que **lee** en el lugar del
que exige.

## Lo que cuesta, medido antes de hacerlo

| Mes | v1 | v2 |
|---|---|---|
| 2025-02 | 2,0 s · 1 llamada · 4 filas | **13,6 s · 6 · 20** |
| 2026-05 | 2,8 s · 1 llamada · 4 filas | **25,1 s · 11 · 40** |
| 2026-07 | 2,2 s · 1 llamada · 4 filas | **20,6 s · 10 · 36** |
| 2026-08 | 2,1 s · 1 llamada · 4 filas | **22,4 s · 11 · 40** |

Unas **9 veces más**. El peor mes entra en la compuerta de 60 s, pero con menos del doble de
margen — y **sobre una parcela de 101 ha**. El tiempo con una parcela grande sigue sin medirse,
y es lo primero a mirar si un alta empieza a fallar: con v1 un mes iba de 2 a 8 s, y por 9 el
extremo alto daría ~72 s.

La cuota de Inngest **no se mueve**: sigue habiendo un step por mes. Lo que crece es lo que hace
cada step.

## Lo que cambió en los tests, y es lo que más vale de esta parte

Los tests que fijaban la orquestación mensual **clavan `RECETA_MENSUAL_V1` explícitamente**, en
vez de seguir a `RECETA_VIGENTE`. Lo que prueban —el plan, un step por mes, la bitácora, la
forma de la key, lo que el repositorio hace con una fila sin valor— **no es de la receta**.

Y hay una razón más fuerte que la prolijidad: **un test que sigue a `RECETA_VIGENTE` y afirma un
literal se vuelve verde por construcción** el día que la vigente cambia. Deja de decir nada
justo cuando más falta haría.

Entraron tres tests de la orquestación por pasada: que un mes escriba N × 4 filas con una clave
por pasada e índice —si dos ventanas cayeran en la misma fecha, el upsert rechazaría el lote
entero y el mes se perdería—, que cueste una reducción por pasada, y que una pasada bajo el
umbral conserve su valor.

Suite: **680 verdes**, 25 omitidos.
