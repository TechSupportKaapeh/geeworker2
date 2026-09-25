# Sesión 14 — el número de las pasadas (2026-09-24)

> **M.9.0.** La tarea era medir, y lo que se mide decide dos cosas: si el bloque M.9.0 sigue, y
> si «por pasada puro» (`DECISIONS #63`) aguanta sin la fila mensual del compuesto.
> **Las dos respuestas salieron de la misma tabla, y van en direcciones distintas**: el bloque
> sigue, y la tabla aparte no se abre.
>
> Decisión: [`DECISIONS #66`](DECISIONS.md). Ficha cerrada:
> [`PREGUNTAS_ABIERTAS`](PREGUNTAS_ABIERTAS.md) B-3.

## Qué se hizo

Un escalón nuevo en `scripts/check_pipeline_real.py`, el 6, detrás de `--pasadas`. Para cada
parcela y cada mes pide **una** cosa a GEE y saca tres:

- cuántas escenas de S2 hay y cuántas pasadas son —la diferencia es el solapamiento de teselas
  que `compuesto.por_pasada` ya resolvía—;
- **la cobertura de cada pasada sobre la parcela**, que es el número que no existía: hoy
  `reduccion.cobertura` la calcula sobre el compuesto;
- la fila del compuesto mensual al lado, sacada de `reduccion.valores`, que es la que escribe
  producción.

**El pipeline no se tocó.** El escalón compone las etapas que ya existen. Es lo que corresponde
a una tarea de medir, y es también lo que hace que el número signifique algo: si la medición
hubiera cambiado el código medido, no habría medición.

Corrió sobre **3 parcelas reales de los Llanos (Colombia) × los 24 meses de `meses_historico`**
—72 meses de parcela— y sobre el cuadrado del Bajío, que no es de un cliente, como segunda
geografía. Receta `s2-mensual-v1`, huella `75dbb738dd2a`. Unos 3 s por parcela y mes.

## Lo que dio

| | Parcelas reales (72 meses) | Bajío (24) |
|---|---|---|
| Pasadas limpias por mes | **mediana 3**, media 2,76, máx 8 | mediana 6 |
| Meses con 0 · 1 · 2 · ≥3 | 4,2 % · 12,5 % · 29,2 % · **54,2 %** | 0 · 0 · 0 · **100 %** |
| `comp − mejor` | mediana **+0,0000**, p90 +0,148, máx +0,485 | mediana +0,009 |
| Meses en que agrega más de 0,05 | 13 de 72 (18,1 %) | 2 de 24 |
| **Compuesto sobre el umbral con todas sus pasadas debajo** | **0 de 72** | **0 de 24** |
| \|mediana del compuesto − mediana de medianas por pasada\| | mediana 0,008, máx 0,089 | mediana 0,004 |
| Rango del índice entre pasadas limpias | mediana **0,065**, p90 0,164, máx **0,336** | mediana 0,038 |

Estacionalidad, promediando las tres parcelas: **3,2 a 5,5 pasadas limpias en seca** (dic–mar)
contra **1,0 a 1,5 en el pico de lluvias** (may–jun).

## Las dos decisiones, y por qué van en direcciones distintas

**El bloque sigue.** `SPRINTS_FASE_M` decía: *si casi siempre son 1 o 2, el mensual está bien y
este bloque se cierra acá*. La mediana es 3 y más de la mitad de los meses tienen 3 o más. En
60 de los 72 meses hay dos o más pasadas limpias, y entre ellas el índice se mueve una mediana
de 0,065. **Eso es señal que hoy se tira**, y es lo que M.9.0c viene a guardar.

El caso que lo muestra sin estadística: parcela_1, febrero de 2025, cinco pasadas limpias con
NDVI **0,443 · 0,412 · 0,381 · 0,107 · 0,147**. La fila mensual dice **0,377**. Lo que pasó ahí
—fuego, corte, lo que fuera— hoy no existe en la base.

**Y la fila mensual del compuesto no hace falta.** El número que lo cierra es **0 de 72**: no
hay un solo mes en que el compuesto llegue a `cobertura_minima` y ninguna pasada sola llegue.
Pasar a por pasada **no deja sin valor a ningún mes que hoy lo tenga**. Los otros dos van en el
mismo sentido: el compuesto agrega 0,0000 de cobertura en la mediana, y recomponer el mes
agregando al leer se aparta 0,008 de NDVI —un orden de magnitud menos que los 0,065 que se
ganan—.

Dicho de otro modo: **las dos mitades de la pregunta se contestan con la misma tabla y no se
contradicen**. Hay suficientes pasadas limpias para que la serie fina valga la pena, y son
suficientemente completas para que no haga falta guardar además el compuesto.

## Lo que se acepta a sabiendas

En el 18 % de los meses —casi todos de mayo a julio— el compuesto cubre hasta **0,485 más** de
la parcela que la mejor pasada sola. Ahí la serie por pasada describe el pedazo despejado y no
la parcela entera: es exactamente el sesgo que `#63` describe. No se pierde el mes, pero ese
número es de menos parcela que el de hoy.

Lo que lo hace tolerable es que **la cobertura va en la fila**. Quien lea puede verlo y
filtrar, que es justo lo que «el umbral al leer» habilita. Y si algún día molesta, la salida
sigue siendo la tabla aparte de `#63`: se puede abrir sin migrar nada, porque las filas por
pasada no cambian.

## Tres cosas que costaron, y quedan escritas

**Una `ee.FeatureCollection` metida en un `ee.Dictionary` vuelve vacía.** `getInfo()` sobre el
diccionario la serializa como `{"type": "FeatureCollection", "columns": {}}` — sin un solo
rasgo, sin error, sin aviso. Es la forma más silenciosa de perder datos que apareció en el
proyecto hasta ahora: el pedido sale bien, tarda lo mismo, y devuelve la cáscara. Lo que
funciona es `coleccion.toList(size).map(...)`, que vuelve la lista entera. Está en un
comentario del script, porque el próximo que arme un pedido compuesto va a tropezar igual.

**Una pasada enteramente enmascarada no trae la clave de su valor.** Es el mismo caso que
`reduccion.leer` ya documentaba para el mes sin píxeles: GEE **omite** la salida en vez de
mandarla en `None`. Leerlo con `dict.get` alcanza, pero hay que saberlo: `respuesta["valor"]`
habría reventado en el primer mes nublado.

**El escalón corre solo, y eso es una decisión.** Los escalones 1 a 4 son la compuerta de M.2.6
y cuestan 6 llamadas a GEE por parcela y mes; el 6 cuesta una. Sobre 24 meses la diferencia son
~430 llamadas contra 72. Un flag que cambia *cuáles* escalones corren se lee raro, así que está
dicho en el docstring y en un comentario de `main()`.

## El control negativo

El escalón comprueba, mes a mes, que **el compuesto no cubra menos que su mejor pasada**. Tiene
que valer por construcción —el compuesto tiene dato donde lo tuvo alguna pasada—, así que si
saliera en rojo, la tabla entera no significaría lo que dice. En los 96 meses medidos no salió
ninguna vez.

Es el mismo patrón de la sesión 12: *un control negativo también necesita su control*. Una
tabla de comparación sin una desigualdad que tenga que valer es una tabla que no se puede
auditar sola.

## El caveat de la muestra, que hay que decir

Las tres parcelas son rectángulos contiguos de ~0,58 × 1,73 km en el mismo punto. **Ven las
mismas pasadas**: la columna de pasadas es idéntica entre las tres en cada mes. Los 72 meses de
parcela son 24 meses × 3 muestras correlacionadas, no 72 independientes.

Lo que sí varía entre ellas —y es el número que se quería medir— es la **cobertura de cada
pasada sobre su parcela**. El Bajío es la segunda geografía y va en el mismo sentido, más
limpio. Con parcelas de otra región el reparto de pasadas limpias puede cambiar; lo que
difícilmente cambie es el **0 de 72**, porque es una desigualdad y no un promedio: para que el
umbral se cruce harían falta dos pasadas cada una por debajo de 0,30 que juntas pasen 0,30
**sin solaparse**.

## Verificación

| Qué | Resultado |
|---|---|
| `pytest tests -q` | **637 verdes**, 21 omitidos — igual que al abrir |
| `ruff check pipeline/` y `ruff format --check pipeline/` | limpios |
| `ruff check .` en la raíz | 77, los históricos; el escalón nuevo no suma ninguno |
| `ruff check --select BLE .` | limpio |
| El camino viejo del script (escalones 1 a 4) | corrido sobre el Bajío, sin cambios |

