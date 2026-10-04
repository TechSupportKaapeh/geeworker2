# Sesión 22 — K.4, importar el plan confirmado, y el NaN de GeoPolygon (2026-10-04)

> Dos tareas: K.4, la fase del sprint K que crea de verdad, y el arreglo de `GeoPolygon` que esperaba la
> consulta del equipo. Las dos, hechas y por PR con el CI en verde.

## Lo que quedó

| Repo | PR | Qué |
|---|---|---|
| Geocore | #102 | **K.4**: `POST /api/importacion` y `…/estimacion`, `DECISIONS #70` |
| Geocore | #103 | `GeoPolygon` rechaza las coordenadas que no son finitas, `DECISIONS #71` |
| Geocore, worker | el cierre | Este archivo, el tablero, los HANDOFF y `PROXIMA_SESION.md` |

Tests de Geocore: **990 verdes** con PostgreSQL local (eran 932). El worker, el panel y el tileserver
no cambiaron de código.

## Al empezar

El usuario confirmó dos cosas: **K.5 se ve bien en el navegador** («Crear extensión y subgrupos», la
vista previa y la zona de carga) con los archivos de `Downloads/archivos_sprint_K` y con
`Zapotlan.kml`, y **la consulta del NaN dio cero filas**. No hubo nada que arreglar antes de K.4.

## K.4

**Antes de codear, cuatro preguntas al usuario** (el prompt las dejaba abiertas), con recomendación:

- **La atomicidad**: un solo `SaveChanges` para ranchos y parcelas, que viven en la misma base y el
  mismo contexto; jobs y eventos después (d-K10). Un UnitOfWork general no entraba limpio.
- **La auditoría**: sí, con el detalle de lo creado (d-K11).
- **Qué bloquea**: el nombre repetido entre hermanos y **también el solape** —el usuario fue más
  estricto que la recomendación, que era sólo avisar—. Se acordó con la tolerancia de `#33`: bloquea
  pasado el 1 % del más chico (d-K12). Y **el usuario sumó un pedido**: avisar si ya existe un rancho en
  el mismo lugar, de este tenant **o de otro**.
- **El tope**: 200 altas, el del reproceso (d-K14). Sin él, 1000 polígonos del caso 4 eran ~54.000
  ejecuciones de Inngest, más que el plan del mes.

**El pedido de mirar otros tenants era una fuga** (OWASP A01): cualquier Member podría mapear dónde
trabaja otro cliente subiendo polígonos. Se le planteó y decidió: **lo de otro tenant, sólo a
TerraStaff** (d-K13).

**Cómo quedó.** Dos endpoints con **la misma revisión**: la estimación dice lo que la importación haría.
Geocore vuelve a leer el archivo (`ArchivoDeImportacion`, compartida con la vista previa) y del plan
toma sólo rol, rancho, nombre y activo. Los errores van todos juntos. Las altas se encolan por el camino
del reproceso (`EncolarAltaAsync`): un evento que no sale deja su job `failed` y no corta el resto.

**Contra los archivos reales**, con el plan tal cual lo propone el clasificador: el de tres niveles y el
del caso 4 se importan, Zapotlan entra con el aviso de demasiado grande, y **el del caso 1 no**: el lote
que cruza el borde se pisa el 50 % con su vecino y dos lotes el 36 %. Es d-K12 funcionando, y es lo que
el operador va a corregir en K.6.

**La auditoría encontró dos cosas y se arreglaron en el mismo PR**: los ranchos de otros tenants entraban
al cálculo de un Member (su cantidad de pares podía filtrarse por el techo de `Relacionar`), y ahora se
filtran en el SQL; y `{"poligonos":[null]}` daba 500. **Controles negativos**: dos `SaveChanges`, lo
ajeno visible y la tolerancia al 5 % ponen tests en rojo.

**Un tropiezo de la suite**: los tests nuevos contra PostgreSQL dejaban ranchos activos en la base
compartida, y el del cierre de mes —que pagina todos los activos— se puso rojo. Ahora borran lo suyo al
terminar.

## El NaN de GeoPolygon

`lat < -90 || lat > 90` da falso con `NaN`, así que un punto `NaN` en medio del anillo pasaba. Ahora va
`double.IsFinite` antes del cierre y del rango. **`GeoPoint` tiene el mismo hueco y queda afuera**:
`GeoConverter` lee `parcelas.centroide` con él, y eso no se miró. La consulta para el equipo está en
`DECISIONS #71`.

## Lo que sigue

**K.6**, en el panel: corregir el árbol, activar y desactivar, y confirmar viendo la estimación de K.4.
Después K.7, la doc del front.
