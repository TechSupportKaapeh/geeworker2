# Sesión 8 — Las altas, segunda parte (2026-09-18)

> Sprint M.4 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md). Se hicieron **M.4.4 y M.4.5**, cada
> una por PR con el CI en verde: geeworker2#36 y #37. Decisiones: `#50` y `#51`. Suite: de 518
> a **547 verdes**, más 21 con `--gee`. **`s2-mensual-v1` quedó congelada** con el merge de #36.
>
> Lo que sigue es M.4.6 y después M.5, en la sesión 9:
> [`geocore/docs/PROXIMA_SESION.md`](../../geocore/docs/PROXIMA_SESION.md).

## 0. Lo que confirmó el usuario al abrir

- **El deploy del worker de geeworker2#32 quedó sano** en Railway.
- **`s2-mensual-v1` se congela al mergear M.4.4**, tal como está.

## 1. M.4.4 — `process_parcela` sobre el pipeline (`DECISIONS #50`)

`handlers/parcela.py`: un step `plan` y un step `mes-AAAA-MM` por mes, cada uno con la
reducción del mes (una llamada a GEE), `filas_del_mes` y el upsert. Con el mismo `fn_id` que el
viejo, que se borró con sus ayudantes y sus tres tests. Ya no sube el COG de la parcela ni
escribe `sentinel2_dates`.

Los tests pasaron a la primera: 15 nuevos, con un step que imita al SDK y memoiza por id, y
un control negativo que lee el reloj fuera del `plan` y sale rojo. **Y ahí no se paró**:
WORKFLOW §6 pide que una pieza que habla con el mundo lo toque al menos una vez. Se levantó
PostGIS con las migraciones de Geocore (`check_schema.py` 43 de 43) y se corrió el handler
entero contra GEE real con la parcela 1 de `scratch/`.

**Se cortó en el mes 21, 2026-05.** Ese mes tiene 10 escenas y la máscara las tapa enteras.
Con cobertura 0, **GEE no manda las estadísticas en `None`: las omite**, y la respuesta trae
una sola clave. `reduccion.leer` (M.2.4) trataba la falta como error, así que el step habría
fallado en los cuatro intentos, y el alta no habría terminado nunca. El supuesto estaba
escrito en el módulo y en un test, y nadie lo había visto: en M.2.6 no salió ningún mes con
cobertura cero.

El arreglo: con cobertura 0, lo que falte se lee como `None`; con cobertura mayor, sigue
siendo un error. Un test `--gee` tapa el compuesto con `updateMask(0)` y confirma que GEE
omite la clave. Después las tres parcelas pasaron: 95 a 103 s por alta, 96 filas cada una,
el job `completed` al 100 %, 27 líneas de bitácora sin errores. **Las tres tenían un mes con
cobertura 0**: sin el arreglo, ninguna habría terminado.

## 2. M.4.5 — `process_rancho` sobre el pipeline (`DECISIONS #51`)

Se le planteó al usuario qué hacer con un mes sin un píxel limpio, y eligió **no subir un
COG**: un mapa todo vacío ocupa storage y no muestra nada. Con cobertura mayor que cero, el
mapa va aunque quede bajo el mínimo de la receta.

`handlers/rancho.py`: por mes, en un solo step, las estadísticas del rancho, el mapa de NDVI,
la URL por el borde, la descarga, el COG, la subida a la key de `claves_cog_mensual` y la fila
`mensual` en `layers`. Lo común de las dos altas pasó a `handlers/altas.py`. Se borraron el
`process_rancho` viejo y `register_layer`, que escuchaba un evento que ya no emite nadie.

**Antes de escribir el handler se miró una descarga real, y salió el segundo bug:** el
GeoTIFF de GEE llega con `nodata=None`, y lo enmascarado (nubes, y todo lo que queda fuera del
polígono) viene como `0.0`. En NDVI eso es suelo desnudo: el COG habría pintado cada nube como
un lote pelado. La capa vieja tenía el mismo defecto. Ahora la imagen va con
`unmask(-9999)`, y `convert_to_cog(…, nodata=-9999)` deja la máscara interna del COG. Se probó
también el texto real del tope de `getDownloadURL` (unos 100 km de lado dan 620 MB contra 48),
y pasó a ser un error definitivo.

Contra lo real, con la parcela 1 como polígono del rancho, GEE de verdad, un MinIO local y el
PostGIS: 23 COG en 226 s (2026-05 sin mapa), los 23 válidos para `rio-cogeo` y con máscara;
23 filas `mensual`; idempotente. **Las medianas del rancho son las de la parcela sobre el mismo
polígono**: B-1 cerrado por construcción, como pedía el diseño.

La imagen del worker se construyó y arrancó con Docker después del merge: `/health` 200 y
**7 funciones** registradas.

## 3. Lo que queda registrado

- **Sin límite de concurrencia en las altas**: un KML de 500 parcelas son 500 altas a la vez
  contra GEE. Va con M.5.3.
- **`observaciones` sale con ruido de float** (`2.9999999999999947`). Redondearla cambia un
  número guardado: sería una v2.
- **Un mes de rancho cuesta 5 a 12 s**; falta medirlo con un rancho grande (el pendiente de
  M.2). Un rancho de más de unas 120.000 ha no entra en una descarga.
- **`init_ee()` corre en cada step**, y `convert_to_cog` deja su carpeta temporal. Los dos
  vienen de la capa vieja.

## 4. Cómo se verificó, en números

| | |
|---|---|
| Suite | 547 verdes, 21 salteados (`--gee`) |
| `--gee` de la reducción | 3 verdes, incluido el nuevo |
| ruff estricto en `pipeline/` y `handlers/` | limpio, formateado |
| CI | verde en #36, #37 y en `main` después de cada merge |
| Parcelas reales (alta) | 3 de 3, 95–103 s, 96 filas cada una |
| Rancho real (alta) | 23 COG válidos en 226 s |
| Imagen Docker | `/health` 200, 7 funciones |
