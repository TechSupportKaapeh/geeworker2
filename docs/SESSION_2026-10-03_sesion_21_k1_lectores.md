# Sesión 21 — M.9.7g cerrada de nuestro lado, y K.1 (2026-10-02 a 10-03)

> Dos tareas: terminar M.9.7g y hacer K.1, la primera fase del sprint K (crear extensión y
> subgrupos). Las dos, hechas hasta donde nos toca. Todo por PR con el CI en verde.

## Lo que quedó

| Repo | PR | Qué |
|---|---|---|
| Geocore | #93 | El prompt de esta sesión (quedaba abierto de la 20) |
| Geocore | #94 | **K.1**: `ILectorDePoligonos`, los lectores de KML, GeoJSON y WKT, `DECISIONS #67` |
| Geocore, worker | el cierre | Este archivo, el tablero, los HANDOFF y `PROXIMA_SESION.md` |

Tests de Geocore: **879 verdes** con PostgreSQL local (eran 799). El worker, el panel y el
tileserver no cambiaron.

## M.9.7g

Lo confirmó el usuario al empezar: **el rancho de prueba del Yaqui, reprocesado con v3, se ve bien
en el panel** —«Por pasada», la última imagen buena, el color real y la dudosa—, y **el equipo
aplicó el SQL del borrado** (`geocore/docs/sql/2026-10-02_borrado_de_prueba_v3.sql`, con el paso 5).
Con eso M.9.7g queda ✅. Lo que falta, **reprocesar el resto con el botón tenant por tenant**, es del
equipo y pasó a una tarea propia: 👥 **M.9.7g2**. Zapotlan va a fallar: es M.9.7h.

## K.1: los lectores

**Antes de codear, dos preguntas al usuario** que ni `#66` ni el tablero contestaban:

- **Un multiparte** (`MultiGeometry`, `MultiPolygon`, `GEOMETRYCOLLECTION`): **un polígono por
  parte** (d-K7). Rechazarlo, como hace el KML de hoy, dejaba un WKT `MULTIPOLYGON` de varios ranchos
  sin forma de importarse.
- **Los huecos**: **se descartan, pero se cuentan** (d-K8). Es lo de siempre, sin el silencio.

**Los KML reales** (los tres de `Downloads`, del 21 y 22 de septiembre). Los dos de Google My Maps
traen un Placemark cada uno, y la parcela de Jalisco cae adentro de la Subregión. **`Zapotlan.kml`, un
export de QGIS, lo rechaza el parser de hoy**: el Placemark no trae `<name>`, y el municipio viene en
`ExtendedData` (`NOMMUN`). El lector nuevo lo resuelve igual que en GeoJSON y WKT: el polígono queda
sin nombre y se nombra en la vista previa, y el `ExtendedData` viaja como pista. **Ninguno de los tres
trae varios polígonos**: un archivo real de los casos 1 o 4 sigue faltando para K.2.

**El control contra `main`**, la aceptación de K.1. `KmlParser` pasó a leer con `LectorKml`, así que
hay un solo camino XML. Se compiló un volcado contra el `Geocore.Infrastructure` de `main` y contra el
de la rama, y se pasaron los 3 reales y 13 sintéticos. **Las dos salidas son idénticas byte a byte.**
El control negativo: dar vuelta lat/lng y romper el `EMPTY` del WKT hace fallar 4 tests.

**La auditoría midió el peor caso dentro de los 10 MB** y encontró dos cosas que se arreglaron en el
mismo PR:

| Caso | Antes | Después |
|---|---|---|
| Un GeoJSON con una "posición" de 10 MB | el error devolvía los 10 MB | 124 caracteres |
| Un WKT de 10 MB con 830.000 `POINT` | 3,5 GB asignados, 2,1 s | 134 MB: `WKT_DEMASIADAS_PIEZAS` |
| Un polígono WKT de 830.000 vértices | — | 338 MB, 1,2 s (queda así) |

## Lo que quedó registrado y no se hizo

- **`GeoPolygon` acepta `NaN`**: las comparaciones de rango con `NaN` dan falso, y un KML con
  `NaN,NaN` entra hoy. **No se arregló a ciegas** porque `GeoConverter`, que lee de la base, también
  pasa por `GeoPolygon.Create`: si hubiera una fila con `NaN`, endurecer la regla rompería su lectura.
  Primero, 👥 que el equipo corra
  `SELECT 'ranchos', id FROM ranchos WHERE ST_AsText(geometry) ILIKE '%nan%' UNION ALL SELECT 'parcelas', id FROM parcelas WHERE ST_AsText(geometry) ILIKE '%nan%';`
  Con cero filas, el arreglo es una línea en `GeoPolygon` y un test.
- **`SlugTests.FromName_WithInvalidInput_Throws("###")` falló una vez** en la suite completa y no se
  repitió en cuatro corridas más. El código es determinista. Si vuelve, guardar la salida.
