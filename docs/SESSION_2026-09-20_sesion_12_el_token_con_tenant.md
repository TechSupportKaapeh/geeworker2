# Sesión 12 — el token de mapa lleva el tenant (M.8.1)

> 2026-09-20. Repos tocados: **terra-admin** (#20), **Geocore** (#46) y **terra-tileserver**
> (#3). Los tres PR mergeados con el CI en verde. El worker no cambió.
>
> M.8.1 era el **🔴 más viejo** del proyecto: el hallazgo A01 de la revisión del
> 2026-09-12. No estaba esperando a que alguien lo hiciera; estaba esperando a **M.6.2b**,
> porque hasta que todo lo que el worker escribe no colgó de `tenants/{t}/` no había con
> qué comparar.

## El agujero, en una línea

El token de mapa decía **quién** pedía tiles y **no decía cuáles**.

El tileserver validaba firma, emisor, audiencia y `type: map-access`. Con eso servía
**cualquier** COG del bucket. Y la key del COG no es un secreto: viaja a la vista en la URL
de cada tile, `?url=s3://terra-assets/tenants/…`. Un usuario con su propio token —legítimo,
vigente, bien firmado— y la key de otro tenant veía los rásters de ese otro tenant.

## Lo que se hizo

| | Repo | PR | Qué |
|---|---|---|---|
| 1 | terra-admin | #20 | El token se pide con `X-Tenant-ID`, y al cambiar de tenant el anterior deja de contar |
| 2 | Geocore | #46 | `GET /api/maps/token` exige el header y firma el claim `tenant_id` |
| 3 | terra-tileserver | #3 | La key tiene que colgar de `tenants/{tenant del token}/`, o 403 |

Decisiones: `DECISIONS #42` (el token) y `#43` (los rásters viejos) de Geocore.

## 1. El orden de los PR es el orden de despliegue, y es el opuesto al de M.5.5

Los tres repos se despliegan solos al mergear, así que **el orden de los merges es el orden
de los deploys**, y acá no daba lo mismo.

La regla de M.5.5 era «primero el oyente, después el que publica». La de acá es otra, y
conviene escribirla así: **el que empieza a exigir va último**.

- El panel mandando `X-Tenant-ID` a la Geocore de antes: no cambia nada. El middleware ya
  aceptaba el header en todos lados.
- Geocore firmando un claim que todavía nadie mira: no cambia nada.
- El tileserver exigiendo el claim **antes** de que Geocore lo firme: **todos** los mapas
  en 403, en los dos sentidos —el panel y el front de clientes—.

Entre el deploy de Geocore y el del tileserver no se rompe nada. Al revés se rompía todo.

## 2. Por qué el prefijo, y no una lista de keys firmadas

Se consideraron tres formas de que el tileserver sepa qué puede abrir:

- **firmar la key exacta en el token** — obliga a un token por capa, y el mapa de un rancho
  son 96 (cuatro índices × 24 meses);
- **que el tileserver consulte la base** — le da una dependencia de base de datos a un
  servicio que hoy no tiene ninguna. Que sea de **sólo lectura del bucket**, sin base y sin
  escritura, es parte de por qué es chico y de por qué se puede exponer al público;
- **comparar el prefijo** — que es lo que se hizo. No necesita ninguna de las dos cosas, y
  es exacto porque la key la arma `claves_cog_mensual()` y nunca se escribe a mano.

La comparación lleva **la barra final**: `tenants/{id}/`. Con uuid de largo fijo un id no
puede ser prefijo de otro, pero la comparación no tiene por qué depender de ese detalle.
Hay un test con `{tenant}-bis`.

## 3. Los dos controles del tileserver dejaron de ser independientes

`security.py` tenía dos fábricas y el módulo decía, con razón, que eran independientes: el
token decide *quién*, la ruta decide *qué*. **Ahí estaba el agujero.** La pregunta que
faltaba —*¿qué puede pedir **éste**?*— sólo se puede contestar con las dos cosas a la vez.

`crear_validador_de_token` ahora **devuelve el tenant** en vez de sólo aprobar, y
`crear_validador_de_ruta` lo recibe por `Depends`. Lo que importa del cableado:

```python
validador_token = crear_validador_de_token(...)
verificar_token = Depends(validador_token)
validar_ruta    = crear_validador_de_ruta(settings.prefijo_valido, validador_token)
```

Es **la misma función**, no otra igual. FastAPI cachea por objeto, así que el claim se lee
una sola vez por pedido y **no hay dos lecturas que puedan discrepar**. Con dos instancias
todo seguiría funcionando igual, y por eso esto va escrito en el código: es un error que no
se ve.

### 400 y 403 no dicen lo mismo

- **400 `URL_NO_PERMITIDA`**: una ruta que no puede pedir **nadie** (otro bucket, `http://`
  a la red interna, `file://`). Es SSRF, A10.
- **403 `TENANT_AJENO`**: una ruta legítima que no puede pedir **éste**. Es autorización,
  A01.

Y el orden importa: **el `..` se mira antes que el tenant**. Si no,
`tenants/{mío}/../{ajeno}/r.tif` empieza con el prefijo propio y pasaría. Hay un test con
exactamente esa URL.

### Un token sin tenant no abre nada, ni lo suyo

Los tokens emitidos antes de este cambio dan **403 `TOKEN_SIN_TENANT`**. Tolerarlos «por
compatibilidad» sería dejar abierto justo lo que la tarea cierra. La ventana dura lo que
dura un token —una hora—, y es el motivo por el que Geocore se desplegó antes.

## 4. El cableado es lo que ningún test unitario prueba

`test_security.py` prueba las dos fábricas como funciones. Ninguno de esos tests se entera
de que `main.py` las conecte mal, ni de que un router se quede sin el `path_dependency`.

Por eso `tests/test_app_tenant.py` **levanta la app entera**, con TiTiler montado, y pega
pedidos HTTP de verdad. Sin red: el endpoint de MinIO apunta a `127.0.0.1:1`, un puerto
cerrado. El entorno se fija **antes** de importar `main`, porque `load_dotenv()` no pisa lo
que ya está —el mismo truco que el worker usa para no escribir en el MinIO de producción—,
así que el test no depende de cómo esté configurada la máquina.

Cubre `/cog` **y `/mosaic`**, que es el que más fácil se olvida.

**El control negativo, y el control del control.** Sacando la comparación por tenant caen
**12** tests, incluidos los de la app real. Pero un validador que rechazara **todo** también
dejaría verdes a esos doce, así que hay un test más: con su propia key, el pedido
**atraviesa** las dos compuertas y falla recién al abrir el COG. Es el único que llega hasta
GDAL, y por eso el módulo le fija timeouts cortos.

## 5. Dos consumidores que no estaban en la tarea

Salieron de preguntarse quién más pide un token de mapa, que es la misma regla que en M.6.2
hizo aparecer `timeseries-on-the-fly`: **la lista de lo que hay que tocar está del lado de
quien consume, no en la tarea.**

- **`scripts/check_prod.py`** verifica la cadena contra el deploy real. Ahora tiene un
  escalón nuevo —el 4— que prueba el aislamiento **sin un segundo token**: toma la `--key`
  que se le pasó, le cambia el uuid del tenant y espera un 403. Un 200 ahí es el agujero
  abierto; un 500 también, porque significa que pasó el control y falló al leer.
- **El piloto del front** (`/piloto`) pedía el token **sin** `X-Tenant-ID`. Habría quedado
  en 400 apenas se desplegó Geocore, y es la página que el equipo del front usa como
  referencia. Ahora lo manda y traduce los dos 403 nuevos.

## 6. Lo que este control no alcanza, y queda escrito

Los assets listados **dentro** de un MosaicJSON. El tenant se compara contra la URL del
documento, no contra lo que el documento lista, y `cogeo-mosaic` los abre tal como vengan.

Es el hallazgo **T-3** del mapeo OWASP, que ya estaba abierto y contenido —sólo `worker-rw`
escribe en el bucket, así que un mosaico sólo aparece ahí si lo puso el worker—, pero desde
M.8.1 **pesa más**: lo que se saltearía es el aislamiento entre tenants, no sólo el filtro
anti-SSRF. Está anotado en `main.py`, en el README y en el tracker.

También queda abierto, y es de diseño: **un token sirve para todo su tenant**. Quien puede
mirar un rancho puede mirar los cuatro. Acotarlo por rancho pediría el token por capa, que
es lo que este diseño descarta.

## 7. Las capas viejas se borran

Las keys anteriores al pipeline mensual —`parcelas/{id}/…` del mapa a demanda de antes de
M.6.2b, y `ranchos/{id}/…` de la capa vieja— **no tienen tenant**, así que ningún token las
alcanza.

Decisión del usuario: **se borran**, los objetos y sus filas en `layers`. Mover pedía cruzar
cada parcela con su tenant y copiar objetos que **igual no sirven** —se escribieron sin
declarar nodata, así que una nube se pinta como NDVI 0, y sin `receta`, así que no se pueden
comparar con nada—. Dejarlas y que el token las rechace era la otra opción, y deja un
catálogo que ofrece lo que el tileserver no sirve.

Los cinco pasos están en `geocore/docs/sql/2026-09-20_capas_sin_tenant.sql`: mirar, contar
las dos mitades, guardar la lista de keys, borrar las filas, borrar los objetos. **Las filas
van antes que los objetos**: una fila sin objeto se diagnostica sola —el tile da 500 «no
existe»—, y un objeto sin fila no se ve desde ningún lado. Es 👥 y va **después** del
deploy.

## 8. El panel: el token viejo no sobrevive al cambio de tenant

`useMapToken` guardaba el token y nada más. Con el token atado a un tenant, seguir usándolo
después de cambiar de tenant **no da un error de permisos visible: da un mapa que no carga**.

No se limpia con un efecto: el estado guarda **para qué tenant se pidió** y el token se
deriva de esa comparación, así no existe el render intermedio en el que el token de A está
disponible con B elegido. Es la misma familia de descuido que el rancho elegido que
sobrevivía al cambio de tenant (M.7.2).

Las reglas puras salieron a `src/lib/mapToken.ts`, que es donde pueden tener tests
(`DECISIONS #40`). Una de las tres es sutil y ahora está fijada: **«hay que renovar» y «se
puede reusar» no son la misma regla**. Con la cuenta regresiva todavía en null, la primera
dice que no —un token recién llegado no pide otro— y la segunda dice que no se reuse —antes
de pintar, uno nuevo no cuesta nada—.

## Números

| Repo | Tests | CI |
|---|---|---|
| terra-admin | **70** (eran 51) | verde |
| Geocore | **456** + 6 omitidos (eran 452) | verde |
| terra-tileserver | **185** (eran 150) | verde |
| GeeWorker | 637, sin cambios | verde |

Controles negativos corridos en los tres repos: `tokenPara` sin comparar el tenant (1 test
en rojo), el claim firmado en mayúsculas (3), y la comparación de prefijo sacada (12).

## Lo que queda de M.8

**M.8.3** (A04, rate limiting), **M.8.4** (A09, auditoría) y **M.8.5** (retención de
`processing_job_events`), las tres en Geocore. Y quedó anotado que **la solapa Tiles del
Diagnóstico cambió de sentido**: ahora es la pantalla donde se verifica el 403 de un token
de otro tenant, y conviene podarle lo que ya no hace falta.
