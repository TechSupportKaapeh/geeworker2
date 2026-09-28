# Sesión 18 — 2026-09-27: el mes en una llamada, la receta v3 y el mapa por fechas

> Arrancó por M.9.7d y cerró M.9.7d, M.9.7e (partida en e1 y e2) y M.9.7f. **Producción cambió en
> una sola cosa**: el mes de una parcela cuesta dos llamadas a GEE en vez de una por pasada. Todo lo
> de v3 está en el código y **no es la vigente**: lo es con M.9.7g.

## Lo que se hizo, en orden

| PR | Qué | Decisión |
|---|---|---|
| geeworker2#87 | **M.9.7d**: todas las pasadas del mes en un pedido | `#74` |
| geeworker2#88 | Los tests `gee` otra vez al día (11 rotos en `main`) | `#74` |
| geeworker2#89 | La ventana de una pasada incluye todas sus teselas | `#75` |
| geeworker2#90 | **M.9.7e1**: la receta v3, sin ser la vigente | `#76` |
| geeworker2#91 | **M.9.7e2**: el rancho por pasada, sin ser la vigente | `#77` |
| Geocore#73 | **M.9.7f** (API): el listado trae la cobertura y la mediana | Geocore `#55` |
| Terra-admin#27 | **M.9.7f** (panel): el mapa del rancho por fechas | Geocore `#56` |

## Lo que cambió el plan

**M.9.7d decía "cobertura primero y reducir sólo lo útil"**, y la medición lo dio vuelta: una pasada
tapada costaba lo mismo que una útil, unos 2 s de ida y vuelta. Se pide el mes entero en una llamada
y por entidad (decisión del usuario). Un mes del Cauca bajó de 44 s a 5 s; uno del Yaqui, de 18 s a
3,4 s; y una parcela de ~2.500 ha nublada, de 88 s —**pasaba la compuerta de 60 s en producción**— a
10 s. Mismos números.

**M.9.7e se partió en dos** (decisión del usuario), y **v3 vigente pasó a M.9.7g**: el mapa mensual
del panel pedía el día 1, y una pasada de ese día se habría mezclado con el compuesto.

**Apareció un error de v2 en producción** (`#75`): la ventana de una pasada era de un segundo desde
la primera tesela que apareciera, y dejaba afuera a su gemela de la otra zona UTM (Sinaloa: 5 de 7
pasadas). Se corrigió para todas las recetas sin subir la versión, porque es código y lo escrito por
v2 es de prueba (decisión del usuario).

## Lo que se decidió (todo del usuario, 2026-09-27)

- M.9.7d: el mes en una llamada, por entidad; no juntar parcelas distintas.
- M.9.7e en dos; v3 vigente con M.9.7g, después del panel.
- El color real es una fila `rgb` con `bandas = [5, 6, 7]`, y el mensual también lo lleva.
- **Los campos opcionales de la receta, apagados, no entran en la huella** (`#76`): v1 y v2
  conservan la suya exacta.
- El arreglo de la ventana, para todas las recetas y en un PR aparte.

## Lo medido

- **v3 contra la "ambas" de M.9.7a**: 42 de 42 pasadas con la misma cobertura y el mismo NDVI.
- **El rancho por pasada contra GEE** (descarga y COG reales): el Cauca de 226 ha, 24 s por mes con 11
  archivos; el Yaqui, 19,9 s; **el Cauca de ~2.500 ha, 48,6 s**. Más hilos no ayuda: 2 → 61,2 s,
  4 → 48,6 s, 8 → 56,9 s.

## Lo que queda

- **M.9.7g**: medir un rancho grande real con v3 antes de ponerla vigente (el de 2.500 ha ronda los
  50 s), el SQL del borrado para el equipo, y el reproceso.
- **Las coordenadas del Yaqui** que se usaron hoy son un cuadrado propio (`scratch/parcelas_m97d/`),
  no el rancho del equipo.
- Los chicos que siguen abiertos: d03 (el mínimo de cobertura por defecto en `/api/measurements`) y
  d04 (la hora de la pasada en la fecha).
