# Sesión 20 · 2026-10-02 · v3 vigente, y el sprint K planificado

> M.9.7g a medias: **v3 es la vigente**, medida antes con un rancho real, y el primer rancho con v3
> destapó un bug que le pegaba a toda alta nueva. Además, pedidos del usuario fuera del tablero:
> reprocesar desde el panel con la estimación de lo que ocupa, exportar la serie a CSV y la tabla
> de las recetas en la doc del front. Y el sprint K —crear extensión y subgrupos desde KML, GeoJSON
> o WKT— quedó planificado por fases.

## Lo que se hizo, por PR

| Repo | PR | Qué |
|---|---|---|
| worker | #97 | **M.9.7g: `RECETA_VIGENTE = RECETA_PASADA_V3`** (`DECISIONS #78`) |
| worker | #98 | **Las tomas de prueba de ESA no entran** (Sentinel-2C, base 99.xx; `#79`) |
| Geocore | #87 | El SQL del borrado de lo de prueba, por receta |
| Geocore | #88 | `GET /api/admin/procesos/reprocesar/estimacion` (`#64`) |
| Geocore | #89, #91 | `GET /api/measurements/csv`, con la métrica en el nombre (`#65`) |
| Geocore | #90 | «Las recetas» en `api-frontend.html`: las tres comparadas y por qué v3 |
| panel | #28 | Reprocesar con confirmación y lo que va a ocupar |
| panel | #29, #30 | Exportar la serie a CSV: todas las métricas o una, dicha |

## Lo medido antes de poner v3 vigente

El rancho grande real salió de GeoData con un SELECT que corrió el usuario: **Zapotlan, 27.349 ha y
1.239 vértices**, en Jalisco. **No entra ni con v2**: la descarga pide 61,0 MB (v2) o 106,7 MB (v3)
contra los 48 de `getDownloadURL`, y la cobertura del mes en una llamada da `Too many concurrent
aggregations` (en lotes de 4 entra, en 50,6 s sólo los números). Rombito, 2.623 ha: **42,0 y 32,7 s**
por mes. §3.6 de la arquitectura tenía mal el techo —calculaba con el COG comprimido— y se corrigió:
una caja de ~22.000 ha con v3. Va a **M.9.7h**.

## El bug que destapó el primer rancho con v3

El alta del rancho de prueba del Yaqui falló los 4 intentos en `mes-2024-12`: «1 par de pasadas a
menos de 0:01:00». Eran **dos satélites**: el 11 de diciembre de 2024 Sentinel-2C, en su puesta en
marcha, voló en tándem 30 s detrás del 2A, y su toma es de prueba (`N99.05`). Hay una por lugar, del 11
al 13 de diciembre, así que **toda alta nueva fallaba en ese mes** desde `#75` (2026-09-27), con v2
también. Se excluyen las bases 99.xx en `fuente.coleccion`, para todas las recetas y sin subir la
versión (decisión del usuario, como `#75`). Dos tests `gee` nuevos, en rojo sin el filtro.

## Lo que quedó para la próxima

- **M.9.7g**: ver en el panel el rancho del Yaqui reprocesado con v3 —«Por pasada», la última imagen
  buena, el color real y la dudosa—; 👥 aplicar `geocore/docs/sql/2026-10-02_borrado_de_prueba_v3.sql`
  con su paso 5; y reprocesar el resto con el botón.
- **K.1**: los lectores por formato. Pedidos al usuario: uno o dos KML reales.

## Lecciones

- **Un commit sobre un PR ya mergeado no llega a `main` y nadie lo ve.** El selector de métricas del
  CSV se subió a las ramas de Terra-admin#29 y Geocore#89 después de que el usuario los mergeara, y
  el panel no lo tenía. Se rehízo en PR nuevos (#30 y #91). **Un cambio sobre algo ya entregado va en
  un PR nuevo**, y antes de subir a una rama se mira si su PR sigue abierto.
- **Medir con un dato real cambió la decisión**: el cuadrado de Buga (2.500 ha) entraba; el rancho
  real más grande es diez veces eso, y además rompe la receta vigente.
- **El clasificador de permisos frenó leer los user secrets de Geocore**, y estuvo bien: el SELECT lo
  corrió el usuario y pegó el resultado.
