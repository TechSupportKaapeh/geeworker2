# Sesión 16 — el eje de fechas (2026-09-25)

> **M.9.0d, la última del bloque M.9.0.** El panel ya muestra la serie por pasada que el
> worker guarda desde que `s2-pasada-v2` es la vigente (`DECISIONS #70`). Terra-admin#21.
>
> Y la suelta: **la solapa Tiles del Diagnóstico, podada** (Terra-admin#22).
>
> Las decisiones son del panel y están en Geocore, como las de M.7: `DECISIONS #49` y `#50`.

## Qué se hizo

| PR | Qué | Tests |
|---|---|---|
| **Terra-admin#21** | M.9.0d: el eje de la serie es una fecha, y el interruptor mensual / por pasada | 70 → **92** |
| **Terra-admin#22** | Tiles pinta con la escala de cada índice; sin rescale ni paleta a mano | 92 → **96** |

Los dos con el CI en verde antes de mergear. Ningún otro repo cambió código.

## Por qué el eje era el problema, y no el gráfico

El gráfico ponía **un punto por fila, a paso fijo**: el eje era el número de la fila. Con una
fila por mes eso engañaba poco, porque los meses vienen parejos. Con una fila por pasada no:
tres pasadas en marzo y una en junio ocupaban el mismo ancho, y la serie decía que en junio
hubo tanta medición como en marzo.

El gráfico ya sabía dibujar huecos —la línea cortada, el punto hueco, la marca en el eje—, así
que **el cambio fue del eje y no del dibujo**. Con 13 pasadas desparejas de un año, las de
enero quedan en los píxeles 40, 54 y 81, y el tramo de marzo a mayo ocupa 160 píxeles vacíos:
el hueco es ancho porque el tiempo lo fue.

Las marcas del eje también cambiaron de origen: salen **del calendario** —bordes de mes, de
trimestre o de año según el tramo, y de día en una serie de pocas semanas— y no de una de cada
N filas. Con 24 meses quedan 8 marcas a paso de trimestre, a 86–88 píxeles una de otra.

## Lo que se decidió (`DECISIONS #49` de Geocore)

- **El panel elige la cadencia, no la calcula.** El número mensual lo agrega la API (`#48`).
- **Arranca en `mensual`**, como la API: es la vista que se compara con lo de antes.
- **No pide `coberturaMinima`.** Sin el parámetro la API no filtra, y una medición de poca
  cobertura se dibuja —punto hueco, o hueco si no trae valor— en vez de desaparecer.
- **El interruptor son dos botones con `aria-pressed`**, no un `Selector` y no pestañas: son dos
  opciones que se comparan alternando sobre el mismo gráfico.
- **`agregadas` va en una tira de barras bajo el eje**, no en el tamaño del punto, que ya dice
  la cobertura. Con `pasada` la tira no se dibuja: serían barras todas iguales.
- **Se dibuja con la cadencia que la API dice haber aplicado**, no con la del interruptor.
- **`truncado` y la mezcla de recetas se avisan en pantalla.** La mezcla es la que la API
  muestra a propósito en un mes reprocesado (`"s2-mensual-v1,s2-pasada-v2"`).

## El hallazgo: la hora no llega

**`MeasurementsController` formatea `fecha` como `yyyy-MM-dd` para las dos cadencias.** La
columna tiene la hora de adquisición —`filas.py` escribe `ventana.inicio`, el instante de la
pasada—, pero el JSON se la come. `api-frontend.html` dice lo contrario: que con
`cadencia=pasada` cada punto llega «con su fecha y hora de adquisición».

**No rompe nada**, y por eso no se tocó Geocore en esta tarea, que era del panel:

- el panel lee las dos formas (`instanteDe`), así que el día que Geocore mande la hora, la
  dibuja sin cambiar una línea;
- las claves de React no usan la fecha, así que dos pasadas del mismo día no se pisan, y hay un
  test para eso;
- dos pasadas de la misma parcela el mismo día UTC son muy raras en estas latitudes.

Lo que sí hace: **la documentación de la API promete algo que el código no da**, y el eje no
puede separar dos pasadas del mismo día. El arreglo es de una línea en el controller —o de una
frase en la doc—, y queda como pendiente para decidir cuál.

## La poda de Tiles (`DECISIONS #50` de Geocore)

Se fueron los dos campos de `rescale`, sus tres atajos y el desplegable de paleta: existían
cuando todos los índices se pintaban con la escala del NDVI. Desde que cada uno tiene la suya,
lo único que dejaban hacer era **pintar un COG distinto de como lo ve quien trabaja**, y la
solapa contesta «¿por qué no se ve?».

Lo que servía de esos controles se volvió un aviso: **un ráster que cae entero fuera de la
escala de su índice** se pinta de un solo color sin que el dato lo sea. Lo dice
`fueraDeEscala`, en `src/lib/indices.ts`, con cuatro tests.

Y un arreglo de paso: el valor del píxel decía «vegetación densa» para **cualquier** métrica,
también para un NDMI de 0,5, que es humedad. Ahora la categoría sale sólo para NDVI y EVI.

El archivo pasó de 602 a 575 líneas. La nota vieja decía 672: era de antes de que M.8.1 sacara
el token a `useMapToken`.

## Una lección: el test que el CI no podía ver fallar

El control negativo de `instanteDe` —leer una fecha con `T` y sin zona en la hora local— salía
en rojo en esta máquina, que está en `America/Bogota`. **En una máquina en UTC habría pasado
con el código roto**, porque ahí la hora local es la de la API. El CI de GitHub corre en UTC:
el test no protegía nada justo donde más se corre.

Se arregló fijando la zona adentro del test (`vi.stubEnv('TZ', ...)`; Node relee `TZ` cada vez
que se asigna), y el control negativo se repitió con la máquina en UTC: sale en rojo igual.

**La regla que deja**: un test que depende del entorno —zona, idioma, separador decimal— tiene
que fijar el entorno, o el control negativo tiene que correrse también en el entorno del CI.

## Verificación

- Terra-admin: `npm test` **96 verdes** (eran 70); `npm run lint` 0 errores y los mismos 3
  warnings de `exhaustive-deps`; `npm run build` limpio. CI verde en los dos PR y en `main`.
- **Control negativo**, cuatro invariantes rotos a propósito, cada uno en rojo en su test y
  ningún otro: el eje por posición de fila, el dominio del tiempo sin la guarda de ancho cero,
  la fecha sin zona en hora local (también con la máquina en UTC), y `fueraDeEscala` sin su
  rama de abajo.
- La geometría real, calculada con el mismo código sobre 24 meses y sobre 13 pasadas: los
  extremos caen en los márgenes y las marcas quedan a paso parejo.
- **Sin verificar en pantalla.** El panel no tiene tests de componentes y esta sesión no tuvo
  un navegador logueado. Lo que conviene mirar: Ranchos → una parcela → «Serie», alternar el
  interruptor, y Diagnóstico → Tiles con una capa de NDMI (la leyenda tiene que marcar el 0).

## Lo que quedó sin confirmar

El prompt de la sesión traía cinco cosas para que el usuario confirme, y **ninguna vino
tildada**: el mapa de un rancho después de borrar `/mosaic`, los 429 en producción, la
respuesta de Inngest, la licencia de la capa satelital del editor, y **si alguna alta o algún
cierre falló desde que v2 es la vigente** —el riesgo abierto de `#70`: un mes pasó de 2 s a
13–25 s sobre 101 ha, y una parcela grande podría pasar la compuerta de 60 s—. Pasan tal cual a
la próxima sesión.
