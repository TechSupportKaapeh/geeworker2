# Sesión 7 — Las altas, primera parte (2026-09-17)

> Sprint M.4 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). Se hicieron **M.4.1, M.4.2 y
> M.4.3**, cada una por PR con el CI en verde: geeworker2#31, #32 y #33. Decisiones: `#47`,
> `#48` y `#49`. Suite: de 463 a **518 verdes**, más 20 con `--gee`.
>
> Lo que sigue es M.4.4, en la sesión 8: [`geocore/docs/PROXIMA_SESION.md`](../../geocore/docs/PROXIMA_SESION.md).

## 1. M.4.1 — la forma de la key (`DECISIONS #47`)

La propuesta del tablero era `tenants/{tenantId}/ranchos/{id}/{indice}/{AAAA-MM}.tif`. Antes
de fijarla se miró qué la toca:

- **El tileserver cachea los tiles con `Cache-Control: public, max-age=31536000, immutable`**
  (`terra_tiles/caching.py`). Con la forma del tablero, un reproceso con otra receta escribe
  sobre la misma key, la URL del tile no cambia, y el mapa viejo queda en caché hasta un año.
- **El tenant de un rancho no cambia:** `Rancho.TenantId` es `private set`. La key no puede
  quedar vieja por eso.
- **TiTiler valida la ruta con un `startswith`** (`terra_tiles/security.py`): alcanza con que
  el tenant sea el primer segmento.

Se le planteó al usuario la forma con la receta adentro, y la eligió:

```
tenants/{tenantId}/ranchos/{ranchoId}/{receta}/{indice}/{AAAA-MM}.tif
```

La natural_key **no** lleva la receta: la fila de `layers` es una por rancho, índice y mes. Lo
arma `pipeline/claves.py`, que además canonicaliza los uuid (Geocore firma el tenant en
minúsculas) y rechaza un id que armaría otra ruta (`../otro-tenant`) o el uuid nulo. Control
negativo: sin la canonicalización, 4 tests en rojo.

## 2. M.4.2 — `handlers/` (`DECISIONS #48`)

El criterio era "la suite entera verde sin tocar un test", y resultó ser lo que decidió el
corte: dos archivos de tests parchean `update_processing_job` **sobre
`services.inngest_handlers`**. Si el wrapper se mudaba e importaba la función en su módulo
nuevo, los parches dejaban de alcanzarlo. Por eso el wrapper recibe con qué escribe el estado,
y cada capa le pasa el suyo.

**Lo que no estaba en el plan y era lo más importante: el Dockerfile.** Copia los directorios
uno por uno y no copiaba ni `pipeline/` ni `handlers/`. Estaba anotado como 👥 "antes de
M.4.4", pero **M.4.2 ya lo necesitaba**: `inngest_handlers` pasa a importar `handlers`, y el
merge habría desplegado un contenedor que muere al arrancar. Entró en el mismo PR, con un test
(`tests/test_dockerfile.py`) que importa `app` con solo lo que copian los `COPY`, porque el CI
no construye la imagen. La imagen se construyó y arrancó con Docker en local: `/health` 200 y
las 8 funciones registradas.

Una desviación del tablero: `claves_de_capa()` se quedó con la capa vieja, porque las claves
mensuales ya estaban en `pipeline/claves.py`.

## 3. M.4.3 — la escritura (`DECISIONS #49`)

`pipeline/filas.py` (pura) y `db_repository.upsert_mediciones_mensuales`. Lo que las separa
de la escritura vieja:

- **las filas con `valor` nulo se escriben**: el mes existe aunque esté nublado;
- `estadisticas` va siempre, y quien la lee filtra por `valor` o `cobertura`;
- en el conflicto, `min_val` y `max_val` pasan a `NULL`: una fila vieja por pasada del día 1
  cae en la misma PK que la del mes.

De paso se arregló `insert_layer`, que devolvía la conexión al pool con la transacción
abortada cuando el insert fallaba.

**Contra lo real.** PostGIS 15 en un contenedor, las migraciones de Geocore con
`dotnet ef database update`, `check_schema.py` en **43 de 43**, y un script que escribió contra
esa base:
- el upsert es idempotente;
- la fila vieja del día 1 queda convertida en la mensual;
- el mes nublado y el mes sin dato se escriben;
- `cobertura = 73` lo rechaza `ck_measurements_cobertura`: no entra nada del lote y el pool
  sigue sano;
- la capa mensual es una sola fila después de dos escrituras.

## 4. Lo que queda abierto

- **No se pudo confirmar el deploy del worker** después de #32 (el primero con el Dockerfile
  cambiado): la URL pública del worker no está en los docs, vive en `Worker__BaseUrl` de
  Railway. La imagen se construyó y arrancó en local; **mirar en Railway que el deploy esté
  sano**.
- **`s2-mensual-v1` se congela al mergear M.4.4.** Las filas de la verificación fueron locales
  y se borraron.
- **Para M.8.1**: TerraAdmin y TerraSupport no tienen tenant, y su token tiene que poder leer
  `tenants/…`. Los on-demand y los exports no llevan tenant en la key hasta M.6.2.
- **Para M.7.3**: la banda p10–p90 de un mes con `valor` nulo no se dibuja; las estadísticas
  están en la fila igual.
- El ruff estricto de `handlers/` corre en local, no en el CI (el CI no se toca por ahora).
