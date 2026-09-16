# Sesión 4 (2026-09-15), primera parte: la fuente, la máscara y el compuesto

> La sesión 4 del orden sugerido: M.2, las etapas contra GEE real. Esta parte cubre M.2.1
> (geeworker2#16, `DECISIONS #38`), M.2.2 (#17, `#39`) y M.2.3 (#20, `#40`). Siguió en el
> mismo día que la sesión 3, apenas cerrada esa.

## 0. Al empezar

**M.0.6 queda postergada por la demo, por decisión del usuario.** Sigue sin protección en
`main`, así que cada merge va detrás de `gh pr checks` con `pass`.

## 1. M.2.1: la fuente

`pipeline/etapas/fuente.py` arma la colección de S2 del mes sobre el ROI:
- une cada escena con su probabilidad de nube por `system:index`;
- pide solo las bandas que la receta usa, más el NIR de la máscara;
- divide por 10.000 y remuestrea solo las espectrales: `SCL` es una clasificación;
- arma cada imagen con `addBands` sobre la escena, para no perder sus propiedades.

**Cómo se prueba una etapa.** Una etapa no se puede ejecutar sin `ee.Initialize()`, así que
hay dos niveles:
- **Funciones puras**, para lo que la etapa decide (bandas, remuestreo, rango del mes), que
  corren en el CI.
- **Tests marcados `gee`**, que corren solo con `pytest --gee`. Tener un `.env` no alcanza,
  que es la lección de `#37`.

Sumé además `tests/test_pipeline_borde.py`, que rompe si algo fuera de `ejecucion.py` le pide
un cálculo a GEE.

**Controles negativos contra GEE real:**
- **Sin dividir, B8 llega a 3987**, así que el test que pide menos de 2 lo rechaza.
- **El azimut solar de una escena es 138,3; después de `divide` es `None`.** El `addBands`
  hace falta de verdad: sin él, la máscara se quedaría sin azimut.

## 2. El sondeo antes de M.2.2

Un script le preguntó a GEE dos cosas:
- **La proyección:** B8 y `probability` salen en EPSG:32614 a 10 m, y `SCL` a 20 m. La
  proyección fija de la máscara es la del NIR de la escena.
- **Escenas nubladas:** julio de 2026 sobre un cuadrado de 2 km. La del 4 de julio tiene un
  30 % de nubes, y es la de los tests.

Y dejó un hallazgo para M.2.3: **cada pasada aparece dos veces**, una por tesela (T14QKH y
T14QLH), porque el ROI cae en el solape. La mediana no cambia si todas se duplican por igual,
pero `n_obs` sale el doble. Está anotado en M.2.3.

## 3. M.2.2: la máscara

`pipeline/etapas/nubes.py` es la máscara de la capa vieja, con los parámetros de la receta,
el NIR en 0 a 1 y un `reproject` a la proyección de la escena a `escala_m`.

**El control negativo**, sobre la escena del 4 de julio:

| | a 10 m | a 60 m |
|---|---|---|
| con la proyección fija | 0,949 | 0,952 |
| sin ella | 0,949 | **0,353** |

**Lo que salió: la máscara de hoy descarta el 95 %.** Separada por capas:

| Capa | Fracción |
|---|---|
| nube | 0,323 |
| nube dilatada 50 m | 0,898 |
| sombra | 0,102 |
| descarte | 0,949 |
| descarte, erosionando 2 px la nube antes de dilatar | 0,503 |

La dilatación sobre píxeles de nube sueltos se come casi todo. **No lo cambié**, porque
`ARQUITECTURA` §8.7 decidió que la máscara se queda igual, y M.2.6 compara lado a lado. M.2.6
suma la comparación con la erosión, y la decide el usuario antes de M.4.3.

## 4. M.2.3: el compuesto mensual

`pipeline/etapas/compuesto.py` junta las teselas de cada pasada, calcula los índices en cada
una y recién después toma la mediana por píxel, más la banda `n_obs`.

**Cómo se agrupa una pasada.** Un sondeo mostró que `DATATAKE_IDENTIFIER` es idéntico en las
dos teselas de una toma y distinto entre pasadas. Se prefirió a la combinación fecha +
satélite porque es un solo campo.

**El control negativo**, sobre el mismo ROI:

| | máximo | mediana |
|---|---|---|
| juntando las teselas | 6 | 2 |
| sin juntarlas | **12** | **4** |

Exactamente el doble. Sin la etapa, `measurements.observaciones` diría 4 donde hubo 2.

**Y un dato que no esperaba:** la mediana es de **2 observaciones limpias en todo julio**,
que es mes de lluvias, sobre 8 pasadas disponibles. Es consistente con el sobre-descarte de
`#39`, y es otro argumento para la decisión de la erosión que trae M.2.6.

**Un test compara los dos motores de la fórmula:** GEE con `Image.expression` y el evaluador
de Python de `pipeline.formulas`, sobre las bandas de un píxel real. Coinciden con 1e-6.
Costó dos intentos: un punto fijo caía en un píxel enmascarado y devolvía todo en `None`, y
`sample` con `numPixels=1` no devuelve una muestra sino ninguna, porque el muestreo es
probabilístico.

## 5. M.2.4: la reducción, y los primeros números

`pipeline/etapas/reduccion.py` convierte el compuesto en los números de la parcela: un solo
reductor combinado desde `plan_de_reduccion()`, la cobertura, las observaciones, y `leer()`,
que es pura y valida la respuesta.

**Las dos reglas de `DECISIONS #36`, ahora con control:**
- todos los pedidos van con `bestEffort=False` y un tope de píxeles explícito. Con el tope
  bajado a 10, GEE levanta en vez de responder a otra escala;
- una clave que falta es un error; una clave en `None` es un mes sin cobertura, y es válida.

**Los primeros números reales del pipeline**, julio de 2026 sobre el ROI de prueba:

| índice | min | p10 | mediana | media | p90 | max | desvío |
|---|---|---|---|---|---|---|---|
| ndvi | −0,115 | 0,066 | 0,278 | 0,329 | 0,676 | 0,925 | 0,230 |
| evi | −6,447 | 0,093 | 0,218 | 0,258 | 0,468 | 1,622 | 0,172 |
| ndre | −0,278 | 0,018 | 0,178 | 0,198 | 0,404 | 0,639 | 0,147 |
| ndmi | −0,462 | −0,080 | 0,037 | 0,051 | 0,209 | 0,496 | 0,113 |

Cobertura **0,955**, observaciones **2**.

Dos cosas salieron de ahí:
- **EVI se sale de su rango** en el 0,012 % de los píxeles, por construcción: su denominador
  puede acercarse a cero. La mediana está sana; se disparan el mínimo y el máximo, que
  también se guardan. Lo decide M.2.6.
- **La cobertura mensual aguanta el sobre-descarte de la máscara.** Con ocho pasadas, el
  95,5 % de la parcela tuvo al menos una observación limpia. Lo golpeado es `observaciones`,
  con mediana 2. Matiza lo de §3: el problema se ve en cuántas observaciones respaldan cada
  píxel, no en cuánta parcela queda sin dato.

## 6. Números

- `pytest tests`: **417 verdes, 10 salteados** (los `gee`). Antes de la sesión eran 386.
- `pytest --gee -m gee`: los 10 verdes contra GEE real.
- `ruff check pipeline/` y `ruff format --check pipeline/` limpios.
- PRs geeworker2#16, #17, #20 y #22, mergeados detrás de su CI verde.

## 7. Lo que sigue

**M.2.5**, el borde con GEE: el tiempo máximo de cada pedido, la traducción de errores y el
conteo de llamadas. Con eso cierra M.2, salvo M.2.6, que pide las 3 parcelas reales. El
detalle está en `geocore/docs/PROXIMA_SESION.md`.
