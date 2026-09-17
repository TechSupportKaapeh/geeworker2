# Sesión 5 (2026-09-17): la compuerta, y las dos decisiones de la receta

> M.2.6, la última tarea del sprint M.2. Con esto **M.2 queda cerrado**. PRs:
> geeworker2#26 (los dos parámetros), #27 (el script) y el de esta sesión.
> Decisiones: `DECISIONS #44` (las confirmaciones de GEE) y `#45` (la receta).

## 1. Las parcelas que llegaron

Tres cuadrados de 101 ha en la Orinoquía (4,68 N, 69,79 O), los tres iguales y en fila,
separados unos 550 m. **No eran lo que la tarea pedía**: el punto de las 3 parcelas era
cubrir tamaños y regiones distintas, porque el tiempo por mes crece con el área y el NDVI
plausible depende del cultivo. Con tres copias de la misma parcela, la compuerta midió una
situación tres veces.

Lo que sí aportaron: es zona de mucha nube, que es donde la máscara se pone a prueba. Los
meses se eligieron para esa región, no para México: **febrero** (seco), **abril** (arranque
de lluvias) y **julio** (lluvias).

## 2. La compuerta

| Criterio | Pedido | Medido |
|---|---|---|
| NDVI plausible | en [−1, 1] | 0,30–0,35 en seco, 0,54–0,56 en lluvias |
| Cobertura coherente con la estación | — | 1,000 en seco; 0,94–1,00 en julio |
| Un mes de parcela | < 60 s | **2 a 8 s** |

**Pasa.** Y el resultado que más dice del rediseño: en `parcela_3` de julio, **la capa vieja
no devolvió nada** —descartó todas las pasadas con su filtro de cobertura del 50 %— y el
pipeline dio 0,537 cubriendo el 93,6 % de la parcela. Es exactamente el problema que
`DECISIONS #31` quería resolver.

La capa vieja da valores 0,05 a 0,09 más altos en todos los meses. Estaba previsto
(`ARQUITECTURA` §8): promedia en vez de tomar la mediana, reduce a 60 m con `bestEffort=True`
y descarta pasadas.

## 3. Las dos decisiones (`DECISIONS #45`)

El usuario eligió **erosión de 2 px** y **acotar los índices**.

- **La erosión** no empeoró nada en ningún caso: en los siete meses despejados no cambió, y
  en los dos nublados subió la cobertura (0,856 → 0,936 y 0,980 → 0,995) y las observaciones
  (1 → 2 y 2 → 3).
- **El acotado** no cambió un solo número en estas parcelas: EVI nunca se salió de rango acá.
  Se acotó igual porque el mínimo que se guarda en `estadisticas` se le muestra al usuario, y
  el −6,4 del Bajío no significa nada para quien lo lee.
- **Las dos atacan cosas distintas:** con la erosión puesta, la escena del Bajío **sigue**
  teniendo EVI fuera de rango.

Al aplicarlas, el NDVI de julio bajó un poco (0,568 → 0,561 y 0,553 → 0,537): al dejar de
descartar píxeles limpios, la mediana deja de estar sesgada hacia los que sobrevivían.

**La huella de v1 se re-fijó** a `75dbb738…`. Es la tercera vez, y la primera en que **los
números cambian de verdad**. Se puede porque M.4.3 todavía no escribió ninguna fila; desde
esa primera fila, cualquier cambio exige v2.

## 4. Dos cosas que la decisión dio vuelta

- **Tres tests comparaban "v1 contra la variante"**, y desde que v1 *es* la variante
  comparaban contra sí mismos. Ahora comparan contra las alternativas apagadas. El de EVI
  lleva su propio control: si la escena de prueba deja de tener el caso, falla con ese
  mensaje en vez de volverse verde por vacío.
- **El escalón 2 del script tenía el mismo defecto**, y salía con las dos columnas idénticas.
  Ahora compara contra las alternativas apagadas, y marca como problema que la erosión baje
  la cobertura en alguna parcela: sería el dato que obligaría a revisar `#45`.

## 5. Números

- `pytest tests`: **448 verdes, 20 salteados**.
- `pytest --gee`: los **20 verdes** contra GEE real.
- `ruff check pipeline/` y `ruff format --check pipeline/` limpios.

## 6. Lo que M.2 deja sin medir

- **El tiempo por mes con una parcela grande.** Las tres eran de 101 ha; es el número que
  M.5 necesita para fijar el límite de concurrencia en Inngest.
- **El COG de un rancho** (`--cog`), que nunca se corrió sobre una parcela real.
- **El Dockerfile del worker todavía no copia `pipeline/`.** Ahora es urgente: el primer
  handler que lo importe (M.4.4) mata el contenedor al arrancar.
