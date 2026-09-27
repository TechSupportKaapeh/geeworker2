# Sesión 17 — el ráster por pasada (2026-09-26)

> Empezó mirando la primera parcela con datos de `s2-pasada-v2`, y eso destapó tres errores en
> cómo se leía la cobertura. Terminó con el ráster por pasada **diseñado, medido y con sus dos
> primeros pasos en producción**: la máscara decidida (M.9.7a) y el COG multibanda (M.9.7b).
>
> **Cambió la prioridad**: por pedido del equipo, el ráster es por pasada —también el histórico—
> y **M.9.7 va antes que cultivos** (M.9.1), que se saltea por ahora.

## Qué se hizo

| PR | Qué |
|---|---|
| Terra-admin#23 | La serie pide `coberturaMinima=0.3` y une la línea (punteada si hay más de 40 días sin foto útil) |
| Geocore#63 | `DECISIONS #51`: el panel pide el mínimo de cobertura (corrige un punto de `#49`) |
| Geocore#64 | **Bug**: `coberturaMinima` no filtraba con `cadencia=pasada` (`#52`) |
| geeworker2#78 | La bitácora de v2 cuenta pasadas; el mes deja dato si una sirve (`#71`) |
| Geocore#65, #66 | HANDOFF y `PROXIMA_SESION`; "Cambios recientes" en `api-frontend.html` para la app web |
| geeworker2#79 | El bloque M.9.6 (a demanda por ventana), en diseño |
| geeworker2#80 | El diseño del ráster por pasada (`ARQUITECTURA_PIPELINE.md` §3.6) y el bloque M.9.7 |
| geeworker2#81 | **M.9.7a**: la máscara, validada — la receta **y** Cloud Score+ a la vez (`#72`) |
| Geocore#67 | **M.9.7b**: `layers.bandas` y `layers.escala`, y `bidx` en la plantilla (`#53`); migración `CapasMultibanda` aplicada por el equipo |
| Terra-admin#24, #25 | **M.9.7b**: el rango del tile en las unidades del COG, y una escala inválida no divide por cero |
| geeworker2#82 | **M.9.7b**: el COG del rancho es multibanda, enteros ×10.000 (`#73`) |
| geeworker2#83, #84 | Tablero, y M.9.7b verificada en producción |

Además: **un tablero de decisiones** en una página propia
(<https://claude.ai/artifact/9Ltu2QwZzwjJTCkZvMgB2h>), con 41 decisiones de lo que queda de M.9 y
las respuestas guardadas. Decididas hoy: d36 a d41.

## Los tres errores de la cobertura, en orden

1. **El panel no pedía el umbral.** Con `s2-pasada-v2` el worker guarda todas las pasadas, también
   las tapadas, y el umbral pasó a aplicarse al leer (`#63`). La API sin parámetro no filtra, y el
   panel no lo mandaba (lo decidí así en M.9.0d y fue un error). En una parcela del Cauca: 19
   pasadas en julio, 10 al 0 %, y la cobertura mensual salía **0 %**.
2. **La API ignoraba el umbral en la cadencia por pasada**: `#48` lo puso sólo en el SQL mensual.
   Ningún test lo pedía, porque la cadencia mensual la prueban los tests de PostgreSQL y los de la
   API no probaban el parámetro. Cada mitad cubierta por un lado distinto.
3. **La bitácora decía "filas sin valor"** en cualquier mes con una pasada tapada, con la frase de
   v1 y el promedio de todas las pasadas.

Antes de tocar nada se midió que **no era un error de la máscara**: la cobertura de cada pasada
coincide con la clasificación de escena (SCL) de la ESA. Eran nubes.

## El ráster por pasada: lo medido

Sólo lectura, con el camino real del worker (§3.6):

- **Qué se guarda**: toda pasada con algún píxel despejado en el rancho (d37). En el Cauca, 14 de 19.
- **Espacio**: 30–130 MB por rancho chico cada dos años; 0,3–0,5 GB uno de 2.500 ha.
- **Tiempo**: el Cauca chico en serie son ~46 s por mes: **las descargas van en paralelo** (M.9.7e).
- **Cuántas pasadas hay depende de la ubicación**: el Cauca chico cae donde se solapan dos órbitas
  (19 por mes); el grande, a 10 km, queda fuera de una (9).
- **El color real necesita la banda verde (B3)**, que hoy no se baja.

## La máscara (M.9.7a)

Sobre 5 parcelas × 24 meses (1.100 pasadas), contra la mediana de las pasadas de consenso
despejado cercanas: **errores propios de máscara 11 con la receta, 27 con Cloud Score+ sola, 5 con
las dos a la vez**, con 7 % menos de pasadas útiles. Decidido: **las dos a la vez** (d36).

**Una corrección del mismo día**: dije que las 18 pasadas que fallan con las tres máscaras eran
"casi seguro" cambios del cultivo. Al mirarlas: **7 lo son, 10 son caídas aisladas que ninguna
máscara detecta** (Cauca, 2025-10-19: 0,71 → 0,23 → 0,71) y 1 no tiene siguiente. Queda un
**~3 % de error residual** que se ataca mirando el tiempo: marcar como dudosa la pasada que se
aparta de la anterior y de la siguiente cuando esas coinciden (M.9.7f).

## El COG multibanda (M.9.7b)

Un archivo por mes con los cuatro índices como bandas, en enteros ×10.000, en vez de cuatro
archivos en decimales. **Una fila de `layers` por índice, con su banda y su escala** (decisión del
usuario). Una descarga por mes en vez de cuatro.

**Sin cambio de comportamiento, medido contra GEE**: misma máscara, diferencia máxima 5e-5, y menos
del 1 % de los píxeles cambia un tono de 256. **Verificado en producción** por el usuario con un
rancho nuevo del Valle del Yaqui: los cuatro índices se ven bien.

**La auditoría encontró un error real y se corrigió antes de mergear**: un valor fuera de rango
caía en -32.768, el centinela de "sin dato", y el píxel desaparecía del mapa. Verificado contra
GEE; ahora se acota a ±32.767.

## M.9.7c: el listado de capas en el servidor

Después del cierre, en la misma sesión. `GET /api/layers` suma `ranchoId`, `desde` y `hasta`
(Geocore#71, `DECISIONS #54` de Geocore), y el mapa del rancho pide `?ranchoId=` en vez de
traer el tenant entero y filtrar (Terra-admin#26). `ranchoId` trae las capas del rancho **sin
las de sus parcelas**, y las fechas son días UTC, los dos incluidos, sobre `acquired_ts`.

- **Índice nuevo `ix_layers_tenant_rancho_acquired`** (migración `CapasPorRancho`): `layers` no
  tenía ninguno. Con el `EXPLAIN`, Postgres lo recorre hacia atrás y el `LIMIT` sale sin ordenar.
  **Falta aplicarlo**, pero no bloquea nada, porque el código funciona sin él.
- **La auditoría encontró un 500:** `hasta=9999-12-31` no tiene día siguiente, y cualquier
  usuario podía tirar la excepción con un parámetro. Ahora ese valor no filtra nada.
- **Un test contra Postgres de verdad pagó su costo:** Npgsql rechaza un `DateTimeOffset` con
  offset distinto de cero. El proveedor en memoria no lo habría visto.
- Geocore: 597 verdes con PostgreSQL local, 15 nuevos. Panel: 117 verdes, 4 nuevos. Control
  negativo hecho en los dos.

## Lecciones

- **Un parámetro que cruza dos caminos de código necesita un test en cada uno.** `coberturaMinima`
  estaba probado en el SQL mensual y en ningún lado del camino por pasada.
- **"Casi seguro" no es un número.** La afirmación de las 18 pasadas se sostuvo hasta que se
  miró la pasada siguiente. Cuando algo se puede verificar con una consulta, se verifica antes de
  escribirlo.
- **La ventana del alta son los 24 meses cerrados**, no años calendario. Describí un rancho de
  prueba con los años equivocados y el usuario lo marcó.
- **La auditoría encuentra lo que los tests no piden**: el centinela del int16 no lo habría
  agarrado ningún test, porque ninguna receta de hoy produce valores fuera de rango.

## Lo que queda

- **M.9.7d**: la cobertura por pasada en una llamada, que también baja el costo de `#71`. Es la
  siguiente.
- Aplicar `geocore/docs/sql/2026-09-26_CapasPorRancho.sql` (el índice de M.9.7c).
- M.9.7e (la
  receta v3 y el ráster por pasada), M.9.7f (el mapa por fechas y la pasada dudosa), M.9.7g (el
  histórico: se borran los datos de prueba y se reprocesa, d40).
- Abiertas en el tablero de decisiones: d03 (el default de `coberturaMinima` en la API), d04 (la
  hora de la pasada en la fecha) y el resto de M.9.
- **Las credenciales de la base del `.env` del worker están muertas**: no se pudo correr
  `check_schema.py` contra producción para verificar la migración.
- Coordenadas de prueba (rancho y parcela) en la conversación: Cauca 226 ha y 2.508 ha, Sinaloa
  226 ha, Yaqui 226 ha.
