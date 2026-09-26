# HANDOFF.md — Estado permanente de GeeWorker

> **2026-09-25, sesión 16 · M.9.0d: el panel ya muestra la serie por pasada** (Terra-admin#21,
> `DECISIONS #49` de Geocore). **El bloque M.9.0 está cerrado.** El worker no cambió.
>
> Lo que el worker tiene que saber de esto:
>
> - **Lo que escribe `s2-pasada-v2` ya se ve**: el panel pide `?cadencia=pasada` cuando alguien
>   elige «Por pasada», y el eje del gráfico es la fecha, así que las pasadas desparejas se ven
>   desparejas. Una fila con cobertura baja se dibuja con el punto hueco; **el panel no pide
>   `coberturaMinima`**.
> - **La hora de la pasada no llega al panel**: Geocore formatea `fecha` como `yyyy-MM-dd` y
>   se come la hora que `filas.py` sí escribe. No rompe nada —el panel no usa la fecha de
>   clave—, pero dos pasadas del mismo día quedarían en el mismo punto del eje. Pendiente en
>   Geocore.
> - **Un mes reprocesado con las dos recetas se avisa en pantalla**, que es para lo que la API
>   devuelve `receta: "s2-mensual-v1,s2-pasada-v2"`.
>
> Crónica: [`SESSION_2026-09-25_sesion_16_el_eje_de_fechas.md`](SESSION_2026-09-25_sesion_16_el_eje_de_fechas.md).

> **2026-09-25 · `s2-pasada-v2` ES LA RECETA VIGENTE** (`DECISIONS #70`, decisión del
> usuario). Desde acá, las altas y el cierre escriben **una fila por pasada** en vez de una por
> mes. **Esto sí cambia producción.**
>
> - **El panel no se rompe**, y esa era la condición: `/api/measurements` agrega con
>   `cadencia=mensual` por defecto desde Geocore#60, y sobre filas mensuales agrupar por mes es
>   la identidad.
> - **Cuesta unas 9 veces más**, medido contra GEE sobre una parcela de 101 ha: un mes pasa de
>   2,0–2,8 s y 1 llamada a **13,6–25,1 s y 6–11 llamadas**, y de 4 filas a 20–40. El peor mes
>   medido entra en la compuerta de 60 s, **con menos del doble de margen**.
> - **⚠️ Lo primero a mirar si un alta empieza a fallar:** el tiempo con una **parcela grande**
>   sigue sin medirse —es un pendiente desde M.2—. Con v1 un mes iba de 2 a 8 s; por 9, el
>   extremo alto daría ~72 s y **pasaría la compuerta**.
> - **La cuota de Inngest no se mueve**: sigue habiendo un step por mes. Lo que crece es lo que
>   hace cada step.
> - **Lo ya escrito no cambia** y no hay migración. Un mes que tenga filas de las dos recetas
>   sale de la API con `receta: "s2-mensual-v1,s2-pasada-v2"`, a la vista — pasa **sólo si se
>   reprocesa** un mes que ya tenía fila v1.
> - **Los COG nuevos cuelgan de `…/s2-pasada-v2/…`**, porque la receta va en la key.
> - **`RECETA_MENSUAL_V1` no se borra**: es la receta de las filas que ya están escritas.
>
> **Para escribir tests de acá en adelante:** los que fijan la orquestación **mensual** clavan
> `RECETA_MENSUAL_V1` a propósito, porque lo que prueban no es de la receta — un test que sigue
> a `RECETA_VIGENTE` y afirma un literal se vuelve verde por construcción el día que la vigente
> cambia.
>
> Suite: **680 verdes**, 25 omitidos.

> **2026-09-25, sesión 15 · M.9.0c (la mitad del worker): `s2-pasada-v2` existe y NO es la
> vigente** (`DECISIONS #69`). **Producción no cambia**: las altas y el cierre siguen
> escribiendo con `s2-mensual-v1`.
>
> - **`RECETA_POR_PASADA` es v1 con dos campos cambiados y ninguno más**, y hay un test que lo
>   fija: `agrupamiento_estadisticas: por_pasada` y `umbral_al_escribir: False`. El ráster sigue
>   mensual en las dos.
> - **`umbral_al_escribir` es un campo de la receta**, no una rama en `filas.py`: así entra en
>   la huella y queda escrito por receta. Con `False`, el valor va aunque la cobertura sea baja
>   — pero **un mes sin un solo píxel sigue sin valor**, porque ahí la mediana vino en `None`
>   desde GEE. «No llegó al umbral» y «no hay dato» son cosas distintas.
> - **`FilaMensual` pasó a llamarse `Fila`.** Con v2, una fila es una pasada.
> - **Se arregló el filtro de nubes que `#67` había dejado anotado**: la colección de nubes se
>   filtra por un **superconjunto** del pedido —un día de cada lado—, porque quien decide qué
>   escena entra es el join por `system:index`, que es exacto. Se aplicó **a las dos recetas**,
>   porque era un bug: una escena de los primeros minutos de un mes tenía su imagen de nubes en
>   el mes anterior y se descartaba entera. **Medido antes de darlo por inocuo**: 576 escenas
>   unidas antes y 576 después, 0 meses en que cambie algo.
> - **El camino por pasada se corrió contra GEE**, no sólo contra tests. Lo que conviene saber
>   antes de poner v2 vigente: **un mes enteramente nublado pasa de 4 filas a 40**, todas con
>   cobertura 0. Entra en la estimación de `#63` y es información, pero es el costo concreto de
>   «por pasada puro».
>
> **Falta la otra mitad, y es la que habilita el cambio de vigente:** `/api/measurements` tiene
> que agregar, con **`mensual` por defecto** — eso es lo que hace que poner v2 vigente no rompa
> el panel de hoy. El número mensual es la **mediana de las medianas por pasada**, que es la que
> `#66` midió (se aparta 0,008 de NDVI del compuesto). Va con SQL crudo, porque
> `percentile_cont` no lo traduce EF.

> **2026-09-25 · dos arreglos chicos, de mirar la tabla `measurements`** (`DECISIONS #68`).
>
> - **`observaciones` deja de salir con ruido de float.** `reduccion.leer` la redondea a tres
>   decimales, y **sólo a ella**: `n_obs` cuenta pasadas, así que su mediana es un entero o un
>   entero y medio y el redondeo es **exacto**. Las estadísticas de los índices no se tocan,
>   que tienen decimales de verdad. **Lo ya guardado sigue con su ruido** hasta que se
>   reprocese.
> - **⚠️ El `.env` del worker apunta a la base de PRODUCCIÓN.** `PROXIMA_SESION` decía que las
>   credenciales de base eran locales y **era falso**: `DB_HOST` es el pooler de Supabase, que
>   es donde vive GeoData. Hoy lo contiene que la contraseña está vencida, que es un accidente
>   y no un control. Misma trampa que `MINIO_*`, la de la sesión 9. Para correr contra una base
>   de prueba, pasar `DB_*` por el entorno: `load_dotenv()` no pisa lo que ya está.
>
> **La forma de la tabla está bien**: `valor`, `cobertura` y `observaciones` son nullable a
> propósito, `min_val`/`max_val` son de la capa vieja y el upsert las anula, y `estadisticas`
> es `jsonb` con un CHECK que exige que sea un objeto. Lo que falta comprobar —que cada `valor`
> NULL tenga de verdad cobertura baja— pide consultar la base y quedó escrito en
> `geocore/docs/sql/2026-09-25_revisar_measurements.sql`.

> **2026-09-25, sesión 15 · M.9.0b: la ventana de observación dejó de estar cableada**
> (`DECISIONS #67`). Refactor **sin cambio de comportamiento**, y el control negativo lo dice
> con números.
>
> - **`pipeline/ventanas.py` es nuevo y es el centro de esto.** Una `Ventana` es
>   `(etiqueta, inicio, fin)`, y **la etiqueta es lo único que llega afuera**: la key del COG, la
>   `fecha` de la fila y el `periodo` del job salen de ahí. Con `del_mes(mes)` la etiqueta es
>   `AAAA-MM`, así que ninguna key y ningún `periodo` cambiaron.
> - **Lo que hay que saber para tocar el pipeline:** `fuente.coleccion`,
>   `productos.compuesto_de` / `estadisticas_de` / `mapa_de`, `ejecucion.reduccion_de`,
>   `filas.filas_de` y las tres `claves_cog_*` reciben **una ventana, no un mes**. Los nombres
>   `*_del_mes` ya no existen. `Mes` sigue vivo donde corresponde: el job y la bitácora.
> - **Partir es puro.** `Agrupamiento.partir(pedido, fechas)` no toca `ee`, así que se prueba sin
>   credenciales. Lo que sí necesita GEE es `ejecucion.fechas_de`, **la llamada declarada que
>   `#63` le suma al borde** — y `entero` no la usa, así que un mes cuesta las mismas llamadas
>   que antes. Hay un test `gee` que lo fija en 0.
> - **La receta lleva dos campos**, `agrupamiento_estadisticas` y `agrupamiento_raster`, porque
>   el ráster y los números tienen costos distintos (`ARQUITECTURA` §3.5). Los dos en `entero`.
> - **La huella de `s2-mensual-v1` cambió y la versión no**, por decisión del usuario: los campos
>   nuevos valen lo que el código ya hacía cableado. El porqué está en `HUELLAS`, en
>   `tests/test_pipeline_receta.py`.
> - **`handlers/rancho.py` exige una sola ventana para el ráster** y rechaza la receta que pida
>   más. No se generalizó a propósito: es el camino más caro del worker, y un bucle cuyo N es
>   siempre 1 fallaría recién el día que alguien lo use.
>
> **⚠️ El hallazgo que se lleva M.9.0c, y es lo primero que tiene que resolver:** `por_pasada`
> parte bien, pero **la ventana que produce todavía no sirve para seleccionar sus escenas**.
> `S2_SR` y `S2_CLOUD_PROBABILITY` comparten el `system:index` —que es por donde las une
> `fuente.coleccion`— pero **no el `system:time_start`**: el de SR va de **129 a 1169 segundos
> después**, y cuánto depende de dónde caiga el ROI en la pasada (medido sobre 105 escenas de
> los Llanos y sobre el cuadrado del Bajío). Con una ventana de un segundo, la imagen de nubes
> queda fuera del `filterDate`, el join no encuentra par y el compuesto sale sin bandas.
>
> Se arregla **filtrando la colección de nubes por un superconjunto del pedido** y dejando que
> el join por `system:index` haga el resto. Eso **cambia el borde del mes** —una escena de los
> primeros minutos tiene su imagen de nubes en el mes anterior, y hoy se descarta—, así que es
> un cambio de números y no podía entrar en M.9.0b. Está fijado en un test `gee` y repetido en
> el docstring de `_por_pasada`.
>
> **Cómo se verificó que no cambió nada**, que es la aceptación de la tarea: con un
> `git worktree` de `main` al lado y un volcador que se adapta a las dos API, se compararon los
> 24 meses de la receta × 4 coberturas que cruzan el umbral, las tres familias de claves, el
> intervalo que se le pide a GEE, y 9 meses de parcela **contra GEE de verdad** —incluido
> 2026-05, el mes en que todo queda enmascarado—. **129 entradas, idénticas.** En los tests no se
> tocó ninguna expectativa: sólo las llamadas.
>
> Crónica: [`SESSION_2026-09-25_sesion_15_el_agrupamiento.md`](SESSION_2026-09-25_sesion_15_el_agrupamiento.md).

> **2026-09-24, sesión 14 · M.9.0: el número que faltaba, y el pipeline sigue igual**
> (`DECISIONS #66`). La tarea era medir, y se midió: **el pipeline no se tocó**. Lo único que
> cambió es `scripts/check_pipeline_real.py`, que ganó un escalón 6.
>
> - **Hay una mediana de 3 pasadas limpias por mes** sobre parcelas reales (media 2,76, máximo
>   8; 54 % de los meses con 3 o más). La hipótesis de `SPRINTS_FASE_M` era «si casi siempre
>   son 1 o 2, el mensual está bien y el bloque se cierra»: **no se cierra**. M.9.0b, M.9.0c y
>   M.9.0d siguen en pie.
> - **«Por pasada puro» (`#63`) queda confirmado, y lo confirma un número**: en **0 de 72**
>   meses de parcela el compuesto llega a `cobertura_minima` con todas sus pasadas por debajo.
>   Guardar por pasada no deja sin valor a ningún mes que hoy lo tenga. **La tabla aparte que
>   `#63` dejaba prevista no se abre.**
> - **Lo que se acepta:** en el 18 % de los meses —casi todos de mayo a julio— el compuesto
>   cubre hasta 0,485 más de la parcela que la mejor pasada sola. Ahí la serie por pasada
>   describe el pedazo despejado. Lo hace tolerable que la cobertura vaya en la fila.
> - **Dos cosas de GEE que aparecieron midiendo, y que conviene recordar:**
>   - **una `ee.FeatureCollection` dentro de un `ee.Dictionary` vuelve vacía.** `getInfo()` la
>     serializa como `{"type": "FeatureCollection", "columns": {}}`, sin un solo rasgo. Lo que
>     funciona es `toList(size).map(...)`;
>   - **una pasada enteramente enmascarada no trae la clave de su valor**, igual que el mes sin
>     píxeles que documenta `reduccion.leer`: GEE omite la salida en vez de mandar `None`.
> - **El escalón 6 corre solo** (`--pasadas`), sin los escalones 1 a 4. No es capricho: esos
>   son la compuerta de M.2.6 y cuestan 6 llamadas a GEE por parcela y mes, contra la única que
>   cuesta este. Sobre 24 meses la diferencia son ~430 llamadas contra 72.
> - **Una copia de `reduccion._reducir` vive en el script**, a propósito y con su porqué
>   escrito: exponerlo habría cambiado producción para un informe, y los cinco argumentos
>   —`bestEffort=False` sobre todo— tienen que coincidir o la comparación no significa nada.
> - **El caveat de la muestra**: las 3 parcelas de `scratch/` son rectángulos contiguos y ven
>   **las mismas pasadas**. Son 24 meses × 3 muestras correlacionadas, no 72 independientes. Lo
>   que sí varía entre ellas es la cobertura de cada pasada, que es lo que se medía. El Bajío
>   es la segunda geografía y va en el mismo sentido, más limpio (mediana 6).
>
> **Y en el tileserver, la misma sesión: se borró el router `/mosaic`** (terra-tileserver#4,
> `DECISIONS #64`), y con él el hallazgo **T-3**. Lo que le toca al worker es una sola cosa:
> **`scripts/check_mosaic_median.py` ya no existe**, así que salió de la tabla de
> verificaciones ejecutables de [`WORKFLOW.md`](WORKFLOW.md) — donde en su lugar entró
> `check_pipeline_real.py --pasadas`. El `mosaic()` de `pipeline/etapas/compuesto.py` **no
> tiene nada que ver**: es de GEE, junta las teselas de una pasada, y sigue igual.
>
> Crónica: [`SESSION_2026-09-24_sesion_14_el_numero_de_las_pasadas.md`](SESSION_2026-09-24_sesion_14_el_numero_de_las_pasadas.md).
> Suite: **637 verdes**, 21 omitidos, sin cambios. `PREGUNTAS_ABIERTAS` B-3, **cerrada**.

> **2026-09-24, sesión 13: el sprint M.8 está cerrado. El worker no cambió.** Tres cosas de
> Geocore que le tocan igual:
>
> - **`POST /api/admin/procesos/reprocesar` tiene techo: 10 por minuto y por usuario**
>   (`DECISIONS #44` de Geocore, nivel `Encolado`). Es el nivel más estricto y está ahí por la
>   cuota del worker: un alta cuesta ~27 ejecuciones de Inngest y un reproceso alcanza 200
>   entidades. Lo mismo para las altas, los mapas a demanda y los KML.
> - **Un `429` no es un fallo del worker.** Si alguien reprocesa en bucle, lo corta Geocore
>   antes de publicar el evento; no hay job ni corrida.
> - **La bitácora de jobs se borra a los 90 días** (`DECISIONS #46`). El worker la sigue
>   escribiendo igual —`registrar_evento_job`— pero **lo que escriba hoy no está dentro de tres
>   meses**. Los jobs no se borran: lo que se vence es el detalle de por dónde pasó.
>
> Crónica: [`SESSION_2026-09-24_sesion_13_el_sprint_de_seguridad.md`](SESSION_2026-09-24_sesion_13_el_sprint_de_seguridad.md).
> Suite: **637 verdes**, sin cambios.

> **2026-09-20, sesión 12: M.8.1, el token de mapa lleva el tenant. El worker no cambió.**
> Pero lo que el worker escribe pasó a ser **la otra mitad de un control de seguridad**, y
> eso cambia qué significa equivocarse en una key.
>
> - **La key es ahora el criterio de autorización del tileserver.** El token de mapa lleva
>   `tenant_id` y el tileserver sólo sirve lo que cuelga de `tenants/{ese tenant}/`. Una key
>   armada a mano, o con el tenant de otra entidad, **no da un error al escribir**: da un
>   COG que nadie puede mirar, o —peor— uno que mira el tenant equivocado.
> - **Por eso `claves_cog_mensual()` y sus hermanas no son una comodidad.** El uuid
>   canónico de `_uuid_canonico()` es exactamente lo que Geocore firma con
>   `Guid.ToString()`: minúsculas y con guiones. El tileserver compara **texto**.
> - **`prefijo_de_tenant()` tiene un espejo del otro lado**: `prefijo_del_tenant()` en
>   `terra_tiles/security.py`. Las dos arman `tenants/{id}/` **con la barra**, y por el
>   mismo motivo.
> - **Lo que el worker escribe fuera de `tenants/` ya no se puede servir.** No queda nada en
>   el código que lo haga desde M.6.2b; lo que había en el bucket se borra (Geocore
>   `DECISIONS #43`).
> - **`/mosaic` sigue con el hallazgo T-3 abierto**, y ahora pesa más: el tenant se compara
>   contra la URL del MosaicJSON, no contra los assets que lista. Hoy lo contiene que sólo
>   `worker-rw` escriba en el bucket.
>
> Crónica: [`SESSION_2026-09-20_sesion_12_el_token_con_tenant.md`](SESSION_2026-09-20_sesion_12_el_token_con_tenant.md).
> Suite: **637 verdes**, sin cambios.

> **2026-09-20, sesión 11: el sprint M.7 (el panel), cerrado. El worker no cambió.** Lo que
> importa desde acá: **el panel ya muestra lo que el pipeline produce**, y eso pone a la
> vista dos cosas que el worker decide.
>
> - **Un mes sin un píxel limpio no tiene COG** (`DECISIONS #51`): en el deslizador de meses
>   del mapa del rancho, ese mes **no está**. La lista tiene huecos a propósito, y el panel
>   lo dice con palabras.
> - **Un mes puede tener ráster del rancho y no tener métrica**: el rancho tuvo píxeles
>   limpios pero ninguna parcela llegó a la cobertura mínima de la receta. El panel lo
>   escribe así, en vez de mostrar un guión.
> - **El mapa del rancho es de los cuatro índices** (`#58`), y el panel tiene el selector:
>   lo que se procesó antes de esa decisión sólo tiene NDVI hasta que se reprocese, y ahí se
>   ve como "este rancho no tiene ningún mapa de EVI".
> - El panel dibuja con `receta`/`estadisticas` y `valor` nulo tal como los escribe el
>   worker: **`valor` null es un mes procesado sin dato** y se dibuja distinto de un mes
>   ausente.
>
> Crónica: [`SESSION_2026-09-20_sesion_11_el_panel.md`](SESSION_2026-09-20_sesion_11_el_panel.md).
> Decisiones: `#37` a `#41` de Geocore, que es donde viven las del panel.

> Estado del repo, no crónica. Lo que pasó en cada sesión va en los
> `SESSION_*.md`. Cómo funciona el servicio, en [`FUNCIONAMIENTO.md`](FUNCIONAMIENTO.md).
>
> **2026-09-20, sesión 10 · M.6.4, y con ella el sprint M.6** (`DECISIONS #62`). La tarea
> suponía angostar 35 `except Exception`; correr `ruff --select BLE` mostró que **sólo 8 son del
> tipo que tapa bugs** —ruff no marca los que relanzan, que es el patrón correcto—. Tres estaban
> en `utils_pkg/cache.py` e `io.py`, **sin un solo llamador**: se borraron los dos módulos. Los
> cinco de `db_repository.py` son telemetría y ahora lo dicen con su `noqa` y su motivo.
>
> El invariante quedó en un test que corre `ruff --select BLE` sobre el repo entero, porque **el
> CI sólo lo corre sobre `pipeline/`** y esto no dependía de que alguien se acordara del comando.
> Control negativo corrido.
>
> **Sprint M.6 cerrado: −1.150 líneas de producción.** Sólo M.6.3 quedó sin hacer, y sin objeto:
> M.6.2 la vació. Suite: **637 verdes**; `ruff check .` en la raíz, de 199 al abrir la sesión a
> **77**.

> **2026-09-20, sesión 10 · M.6.2b: la capa vieja desapareció** (`DECISIONS #61`). El mapa a
> demanda corre sobre el pipeline (`handlers/mapa.py`), es de **un mes** y su key cuelga de
> `tenants/{t}/parcelas/{p}/{receta}/{indice}/{AAAA-MM}.tif`. **Con eso, todo lo que el worker
> escribe está bajo `tenants/`**, que es lo que M.8.1 necesitaba.
>
> Se borraron `ee_service.py`, `export_service.py`, `ee/ee_indices.py`, `utils_pkg/
> visualization.py` y `services/inngest_handlers.py` enteros; `ee_client.py` pasó de 436 líneas a
> **57**, que son sólo `init_ee`. La lista de funciones vive en `handlers/registro.py`, y lo que
> comparten los dos handlers que bajan rásters, en `handlers/raster.py`.
>
> El mapa a demanda ganó de paso tres cosas que le faltaban: **declara nodata** (antes una nube se
> pintaba como NDVI 0), su fila lleva **`receta` y `estadisticas`**, y **un mes sin un píxel
> limpio no sube nada** en vez de un ráster de ceros.
>
> Contrato con Geocore: el evento trae **`periodo`** (`AAAA-MM`) e `indice`; se fueron
> `fechaInicio`, `fechaFin` y `cloudPct` — este último el worker lo recibía y **nunca lo aplicó**.
> **Desplegar el worker antes que Geocore.**
>
> Suite: **635 verdes**. `ruff check .` en la raíz, de 157 a **92**. Código de producción:
> **−622 líneas**, y **−1.150 en todo el sprint M.6**.
>
> **Para M.8.1:** los rásters a demanda **que ya están** en el bucket siguen en `parcelas/{id}/`,
> sin tenant y sin nodata, con sus filas en `layers`. Hay que decidir si se mueven, se borran o se
> deja que el token los rechace.

> **2026-09-20, sesión 10 · M.6.2: se borran los handlers a demanda** (`DECISIONS #60`).
> Decisión del usuario, con el equipo del front avisado. Se fueron `compute_timeseries`,
> `query_available_dates`, `export_data` y `compute_parcela_stats`, sus cinco endpoints en Geocore
> (`DECISIONS #35` de Geocore) y **todo lo que sólo ellos sostenían**:
> `get_sentinel2_time_series` —la segunda copia de las fórmulas, con EVI y SAVI mal calculados—,
> `una_por_dia`, `get_sentinel2_dates`, `generate_time_series_data`, `export_time_series`,
> `insert_measurement`, `insert_measurements` y `round_sig`.
>
> **`insert_measurement(s)` eran la escritura por pasada**, la que producía filas con `receta`
> nula: las que el equipo borró a mano en M.3.5. La canilla quedó cerrada.
>
> **Un casi-accidente:** `POST /api/processing/jobs/timeseries-on-the-fly` publicaba
> `terra/parcela.timeseries.requested`, cuyo único oyente era el handler borrado — habría quedado
> fabricando jobs `pending` eternos. Se borró también, y quedó
> `test_cada_evento_que_geocore_publica_tiene_oyente`, que compara los disparadores registrados
> contra lo que Geocore publica en las dos direcciones. **El orden de despliegue es el inverso
> del de M.5.5: primero Geocore, después el worker.**
>
> **7 funciones registradas** (eran 11). Código de producción: **−467 líneas**. `ruff check .` en
> la raíz, de 187 a 157 sin sumar ninguno. Suite: **617 verdes**, más 21 con `--gee`.
>
> **Falta M.6.2b**: el mapa a demanda al pipeline, que se lleva `ee_service.py`,
> `export_service.py`, `ee_indices.py` y el constructor de colecciones de `ee_client.py`.

> **2026-09-20, sesión 10: M.6.1, la capa vieja empieza a irse** (`DECISIONS #59`). Se borró lo
> que `ARQUITECTURA_PIPELINE` §9 lista y hoy **no tiene ningún llamador**: `composite_embedding`,
> `maskS2clouds`, la re-exportación de `compute_sentinel2_index`, los cinco nombres de
> `services/ee/__init__.py`, `config.SUPPORTED_INDICES`, `init_db` e `insert_sentinel2_date`. Y el
> worker **dejó de crear y de escribir `sentinel2_dates`**, que es lo que hace seguro el
> `DROP TABLE` de 👥 M.6.1b.
>
> **El resto de §9 no se pudo borrar:** `get_sentinel2_collection`, `get_sentinel2_time_series`,
> `compute_sentinel2_index`, `apply_scsc`, `check_roi_coverage`, `una_por_dia` y
> `get_sentinel2_dates` siguen vivas porque las sostienen los cinco handlers a demanda. Son M.6.2,
> que espera la confirmación 👥 de si el front de los tenants los usa.
>
> Código de producción: −61 líneas. Suite: **619 verdes**, más 21 con `--gee`.
> Verificado contra Postgres de verdad: el worker arrancado contra una base vacía **no recrea la
> tabla**.
>
> **El sprint M.6 no se pudo terminar, y no es sólo M.6.2.** M.6.3 unificaría cinco
> `Request*Async` de los que cuatro se borrarían, y 38 de los ~74 `except Exception` de M.6.4
> viven en los módulos que M.6.2 borra —los que sobreviven ya son deliberados—. Las tres quedaron
> ⛔ colgando de la misma pregunta 👥. Crónica:
> [`SESSION_2026-09-20_sesion_10_la_limpieza.md`](SESSION_2026-09-20_sesion_10_la_limpieza.md).
>
> Fuera del tablero, el mismo día: **`test_health_responde_mientras_corre_un_step` era flaky y se
> lo encontró rojo en `main`** (geeworker2#55). Exigía que `/health` contestara en menos de 0,2 s;
> ahora afirma un orden —el step seguía corriendo cuando `/health` contestó— y trae el control
> negativo con el `serve` del SDK. Importa porque el CI es la única compuerta de merge mientras
> M.0.6 siga postergada.

> **2026-09-20, decisión del usuario (`DECISIONS #58`): el mapa del rancho es de los cuatro
> índices**, un COG por índice y por mes, cada uno con su fila en `layers` y sus propias
> estadísticas. No toca la receta —los cuatro ya se calculaban—, pero el mes pasa de 1 a 4
> descargas y de 2 a 5 llamadas a GEE. Lo que ya está en producción sigue con sólo NDVI hasta
> que se reprocese. Suite: 612 verdes.
>
> Antes, el mismo día: el primer cierre de mes real sacó un bug de clasificación (`DECISIONS #57`):
> `Pixel grid dimensions ... must be less than or equal to 32768` se trataba como pasajero y se
> reintentaba cuatro veces —el primer intento gastó 144 s de GEE— cuando la misma geometría da
> siempre el mismo error. Ya está en `_DEFINITIVOS`. Lo levantó un rancho de 13 x 111332 px, o
> sea 130 m por 1113 km: un polígono mal cargado. Suite: 610 verdes.
>
> **Antes, 2026-09-19, sesión 9: M.5.3, el cierre de mes**
> (`DECISIONS #56`). `handlers/mes.py` registra `process-parcela-mes` y `process-rancho-mes`,
> que atienden `terra/parcela.mes.requested` y `terra/rancho.mes.requested` de Geocore: **un
> solo step, con el mes que manda el evento en `periodo`**, y reusando `procesar_mes` de las
> altas (que dejó de ser privada) para que el mes 25 se calcule igual que los 24 del alta.
> **Las cuatro funciones que le piden a GEE comparten una cola de concurrencia de 5**
> (`handlers.altas.CONCURRENCIA_GEE`), que es el techo del plan Hobby. **11 funciones
> registradas.** Suite: 608 verdes, más 21 con `--gee`.
>
> Verificado contra un Inngest local: las cuatro comparten el mismo `hash` de cola, una corrida
> real escribió las 4 filas de un mes, y **un evento reenviado con el mismo id no dispara otra
> corrida** — que es de lo que depende la republicación de Geocore.
>
> 👥 Falta que el equipo prenda `CierreMensual__Habilitado` en Geocore (M.5.5): hasta entonces
> estas dos funciones no reciben ningún evento.
>
> Antes, **2026-09-19, sesión 8, segunda parte:**
> [`SESSION_2026-09-19_sesion_8_las_altas_en_produccion.md`](SESSION_2026-09-19_sesion_8_las_altas_en_produccion.md).
> **El sprint M.4 está cerrado: las altas corren en producción**, verificadas de punta a punta
> (M.4.6). Salieron cuatro tareas:
> - M.4.7 (`#52`): el job se cierra si su corrida se cancela o muere (`on_failure` y
>   `cerrar-altas-canceladas`);
> - M.4.8 (`#53`): **el worker atiende en paralelo**. Antes, de a un step por vez: el SDK
>   corría los handlers dentro del event loop. La ruta la monta `services/inngest_serve.py`;
> - M.4.9 (`#54`): el arranque marca `INNGEST_BASE_URL` y las otras URLs que lee el SDK si están
>   en producción;
> - M.4.10 (`#55`): `diagnostico-latencia`. **La espera de ~50 s entre steps es de Inngest Cloud.**
>
> 9 funciones registradas. Suite: 591 verdes, más 21 con `--gee`.
>
> Antes, **2026-09-18, sesión 8:**
> [`SESSION_2026-09-18_sesion_8_las_altas_II.md`](SESSION_2026-09-18_sesion_8_las_altas_II.md).
> **M.4.4 y M.4.5: las dos altas corren sobre el pipeline mensual**, y `s2-mensual-v1` quedó
> congelada:
> - `handlers/parcela.py` escribe los 24 meses de una parcela nueva (`DECISIONS #50`);
> - `handlers/rancho.py` sube un COG de NDVI por mes con dato, con su fila `mensual` en
>   `layers` (`#51`). Un mes sin un píxel limpio no tiene mapa;
> - **probarlas contra GEE real sacó dos bugs**: con cobertura 0 GEE omite las claves (el alta
>   no terminaba), y su GeoTIFF no declara nodata (una nube se pintaba como NDVI 0);
> - `register_layer` se borró con el `process_rancho` viejo: **el worker no emite eventos** y
>   registra 7 funciones.
>
> Suite: 547 verdes, más 21 con `--gee`.
>
> Antes, **2026-09-17, sesión 7:**
> [`SESSION_2026-09-17_sesion_7_las_altas_I.md`](SESSION_2026-09-17_sesion_7_las_altas_I.md).
> M.4.1, M.4.2 y M.4.3:
> - la key del COG mensual lleva el tenant y la receta (`DECISIONS #47`, `pipeline/claves.py`);
> - el wrapper de jobs y las utilidades están en `handlers/` (`#48`), y **el Dockerfile copia
>   `pipeline/` y `handlers/`**, con un test que lo cuida;
> - la escritura mensual (`#49`) está probada contra PostGIS con las migraciones de Geocore.
>
>
> Antes, **2026-09-17, sesión 6.** La sesión fue sobre todo en Geocore (M.3.2 y
> M.3.3); de este lado va **M.3.4: `check_schema.py` verifica el contrato del esquema en vez
> de imprimirlo** y sale con código 1 si falta algo (`DECISIONS #46`). Corrido contra PostGIS
> con las migraciones de Geocore aplicadas: **43 de 43 en ok**; con la migración anterior, 33
> de 43. **Correrlo antes de M.4.3**, que es la que escribe la primera fila mensual. Suite:
> 463 verdes, más 20 con `--gee`. Crónica de la sesión:
> `geocore/docs/SESSION_2026-09-17_sesion_6_la_api_mensual.md`.
>
> Antes: **2026-09-15, a mitad de la sesión 4**:
> [`SESSION_2026-09-15_sesion_4_fuente_y_nubes.md`](SESSION_2026-09-15_sesion_4_fuente_y_nubes.md).
> Están M.2.1, la fuente; M.2.2, la máscara en proyección fija; y M.2.3, el compuesto que
> junta las teselas de cada pasada (`DECISIONS #38`, `#39` y `#40`). Los tests que le hablan
> a GEE corren solo con `pytest --gee`. **La máscara de hoy descarta de más**: el 95 % de una
> escena con 32 % de nubes, y en julio quedan 2 observaciones limpias por píxel sobre 8
> pasadas. Lo decide M.2.6.
>
> Sesión 3, de este lado:
> [`SESSION_2026-09-15_sesion_3_el_test_de_arranque.md`](SESSION_2026-09-15_sesion_3_el_test_de_arranque.md).
> La sesión fue sobre todo en Geocore: M.3.1 (la migración mensual) y M.8.2 (los tests de
> la API). Su crónica es `geocore/docs/SESSION_2026-09-15_la_migracion_mensual_y_los_tests_de_la_api.md`.
> Antes, el mismo día:
> [`SESSION_2026-09-15_el_nucleo_del_pipeline.md`](SESSION_2026-09-15_el_nucleo_del_pipeline.md).
> Antes, el 2026-09-14:
> [`SESSION_2026-09-14_el_ci_en_los_cuatro_repos.md`](SESSION_2026-09-14_el_ci_en_los_cuatro_repos.md).
> Y el 2026-09-12:
> [`SESSION_2026-09-12_la_bitacora_del_worker.md`](SESSION_2026-09-12_la_bitacora_del_worker.md)
> y [`SESSION_2026-09-12_primera_corrida_y_el_pipeline_mensual.md`](SESSION_2026-09-12_primera_corrida_y_el_pipeline_mensual.md).
> Para retomar: `geocore/docs/PROXIMA_SESION.md`.
>
> **🧩 Desde el 2026-09-15, `pipeline/` tiene el núcleo del diseño** (sprint M.1,
> `DECISIONS #35` y `#36`): meses, fórmulas, registros de índices y de estadísticas,
> y la receta `s2-mensual-v1` con su huella. No usa GEE ni la red, y ningún handler
> lo importa todavía: en producción no cambió nada.
>
> Después de revisarlo contra la capa vieja se le hicieron dos arreglos (M.1.6 y
> M.1.7):
> - las estadísticas son declarativas, y los percentiles salen de un solo
>   histograma;
> - la receta fija el remuestreo y la sombra en píxeles;
> - los pedidos van con `bestEffort=False`.
>
> **M.2 tiene que respetarlo**: ver `DECISIONS #36`.
>
> **🛡️ Desde el 2026-09-14 hay CI en los cuatro repos** (sprint M.0,
> `DECISIONS #34`). En este repo corre `pytest`, `pip-audit` y un ruff estricto
> solo sobre `pipeline/`. Referencia: [`CI.md`](CI.md).
> **Hasta que el equipo haga M.0.6, el CI avisa pero no frena** un push directo a
> `main`.
>
> **🎯 Hacia dónde va (2026-09-12, tarde).**
> - El histórico pasa a ser **mensual**, con el compuesto armado en GEE.
> - La capa de satélite se rehace como un pipeline: receta, registros de índices y
>   estadísticas, etapas y un solo borde con GEE.
>
> Diseño: [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md). **Tablero:
> [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md).**
> - `DECISIONS #31`: ✅ decidida (opción B), reemplaza a #19 y #20.
> - `#32`: aceptada.
> - `#33`: ✅ confirmada. Se reescribe la capa de satélite, no el servicio.
>
> **La primera corrida real falló** con `CardinalityViolation` en el mes 1 de la
> serie: dos imágenes del mismo día en un lote. Está arreglado (`DECISIONS #30`).
>
> **Novedades del 2026-09-12:** cada job escribe su bitácora
> (`processing_job_events`, `DECISIONS #29`) y su `progress`. El histórico de una
> parcela se procesa por ventanas: 8 trimestres de fechas y 12 meses de serie.
> Salieron y se arreglaron tres bugs:
>
> - los fallos de un step eran invisibles para el wrapper;
> - un `NonRetriableError` dejaba el job en `running` para siempre;
> - la serie anual se truncaba en 30 imágenes.
>
> **Necesita la migración `ProcessingJobEvents` de Geocore aplicada.** Sin ella
> pausa la bitácora, pero no rompe nada.
>
> **Novedades desde el 2026-09-07:** el flujo corre de punta a punta (Inngest
> registrado, firma verificada, COG subido y servido). La contraseña de
> `geodata` está resuelta, así que §5b-bis quedó histórica. El worker ahora
> reporta su configuración y verifica sus conexiones al arrancar
> (`DECISIONS #27`). Variables de Railway, versión final: sesión del 09-11, §8.

---

## 1. Qué es este servicio

Worker de rásters del ecosistema Terra. **Se dispara por eventos de Inngest**,
no por HTTP de negocio. Consume geometría de Geocore, calcula índices sobre
Sentinel-2 en Google Earth Engine, sube los COG a MinIO y escribe métricas y
catálogo en la base `geodata`.

Su única superficie HTTP es `/health` y `/api/inngest`. No expone API de lectura
— eso lo sirve Geocore, que ya aplica aislamiento de tenant.

---

## 2. Estado por pieza

| Pieza | Estado |
|---|---|
| Escrituras a `geodata` (`layers`, `measurements`, `processing_jobs`) | ✅ SQL alineado con el esquema real, verificado con `PREPARE` |
| `storage_key` como key pelada, bucket único | ✅ Verificado contra el bucket real el 2026-08-26 |
| **Cadena de tiles completa** | ✅ **Primer tile real el 2026-08-26** — ver §2b |
| Composición por MosaicJSON | ✅ Spike verificado el 2026-08-27 (`DECISIONS #20`) |
| **Escrituras del worker contra MinIO real** | ✅ **2026-09-07 — probadas de verdad.** `check_write_path.py` y `check_ingest_real.py` |
| COG del worker validado por `rio-cogeo` | ✅ 2026-09-07 — cerró la verificación que `DECISIONS #22` dejó pendiente |
| Escrituras a `geodata` contra la DB real | ❌ **`password authentication failed`** — ver §5b-bis |
| Paridad de permisos en la subida | ✅ 2026-09-01 — la subida emite solo el `PUT`; lo fija `scripts/check_minio_region.py` |
| Paridad de permisos en el arranque | ✅ 2026-09-02 — `ensure_bucket()` salió del constructor (`DECISIONS #24`) |
| Config de MinIO ausente o incoherente | ✅ Falla cerrado nombrando la variable (`DECISIONS #24`) |
| `process_parcela` | ✅ 2026-09-18 (M.4.4, `DECISIONS #50`): sobre el pipeline, en `handlers/parcela.py`. **Verificado en producción el 2026-09-19** (M.4.6): 96 filas con `s2-mensual-v1` |
| `process_rancho` | ✅ 2026-09-18 (M.4.5, `DECISIONS #51`): sobre el pipeline, en `handlers/rancho.py`. **Verificado en producción el 2026-09-19** (M.4.6): 24 capas `mensual`, `check_prod.py` 7 de 7 y la máscara (`nodata_type: Mask`) en el tileserver |
| `register_layer` | ✅ **Borrado** el 2026-09-18 (M.4.5): escuchaba `terra/raster.ingested`, que solo emitía el `process_rancho` viejo |
| **Cierre de jobs fuera del handler** | ✅ 2026-09-19 (M.4.7, `DECISIONS #52`): `on_failure` de las altas y `cerrar-altas-canceladas`. Probado contra un Inngest real |
| **Concurrencia del worker** | ✅ 2026-09-19 (M.4.8, `DECISIONS #53`): `/api/inngest` en el pool de hilos (`services/inngest_serve.py`), `ThreadedConnectionPool` y plazo de GEE contado por hilos. **Corrige `#26`** |
| **Inngest Cloud** | 🟡 Plan Hobby: 5 steps a la vez, 50.000 ejecuciones al mes (un alta ≈ 27). Esperas de 38 a 75 s entre steps, de la plataforma (`#55`). 👥 Soporte de Inngest, o piloto autohosteado |
| Handlers on-demand | ✅ **Queda sólo `heatmap`**, y corre sobre el pipeline desde M.6.2b (`handlers/mapa.py`, `#61`): un índice, un mes, con nodata y con el tenant en la key |
| `process_kml` | ❌ Handler muerto: su evento fue eliminado en Geocore |
| **Capa vieja de GEE** | ✅ **Ya no existe** (M.6.2b, `#61`). `ee_service.py`, `export_service.py`, `ee/ee_indices.py`, `utils_pkg/visualization.py` y `services/inngest_handlers.py` se borraron; `ee_client.py` quedó en 57 líneas, sólo `init_ee` |
| **`sentinel2_dates`** | ✅ **Cerrada.** El worker dejó de crearla y escribirla (M.6.1) y el usuario aplicó el `DROP TABLE` el 2026-09-20 (M.6.1b) |
| Superficie HTTP de lectura (`routes/`, `schemas/`, `auth.py`) | ✅ **Borrada** el 2026-08-30 (`DECISIONS #23`) |
| Firma de Inngest | ✅ 2026-09-04 — se verifica en modo cloud; 401 sin firma (`DECISIONS #25`, `W-8`) |
| Criterio de entorno | ✅ Uno solo (`config.IS_PRODUCTION`); lo desconocido cuenta como producción |
| Correlación en los logs | ✅ 2026-09-07 — `run_id`, `attempt`, `job_id` e ids de entidad en cada línea (F.18) |
| Despliegue del worker | 🟡 `Dockerfile` escrito el 2026-09-07. **Construido y arrancado en local el 2026-09-17** (M.4.2) y otra vez el 2026-09-18 después de M.4.5: `/health` 200 y **7 funciones** registradas. El deploy de geeworker2#32 quedó sano en Railway (confirmado por el usuario el 2026-09-18). Desde ese día copia `pipeline/` y `handlers/`, y `tests/test_dockerfile.py` pone el CI en rojo si un paquete que `app` importa no se copia (`DECISIONS #48`) |
| **`handlers/`** | ✅ 2026-09-17 (M.4.2, `DECISIONS #48`): el wrapper de jobs (`con_seguimiento`), el ROI y las utilidades, fuera de la capa vieja. Los handlers mensuales usan esto, no `inngest_handlers.py` |
| TLS contra MinIO | ✅ 2026-09-07 — el default se deduce del host; lo desconocido asume TLS (`W-2`) |
| Commits del worker | ✅ Commiteado desde el 2026-08-30, sin pushear |
| **Bitácora de jobs** (`processing_job_events` + `progress`) | 🟡 2026-09-12 — migración aplicada; **corrió contra Inngest y la base real** y mostró cada intento. Falta una corrida que termine bien (`DECISIONS #29`) |
| **Pipeline mensual** | 🟡 **Núcleo hecho el 2026-09-15** (sprint M.1, `DECISIONS #35`): `pipeline/` con meses, fórmulas, registros de índices y estadísticas, y la receta `s2-mensual-v1` con su huella. No usa GEE ni la red, y lo cuida el ruff estricto de `pipeline/ruff.toml`. **El sprint M.2 está cerrado** (2026-09-17). Están las cuatro etapas (`pipeline/etapas/`, M.2.1 a M.2.4), `productos.py`, el borde `ejecucion.py` (M.2.5) y la validación contra parcelas reales (M.2.6): `DECISIONS #38` a `#45`. **La compuerta pasó**: NDVI coherente con la estación, cobertura coherente, y 2 a 8 s por mes contra los 60 que pedía. En un mes la capa vieja no devolvió nada y el pipeline cubrió el 93,6 %. La receta v1 quedó con erosión de 2 px y acotando los índices. Faltan los handlers (M.4), que son los que van a usar `ejecucion`. Diseño: [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md); tablero: [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md) |
| Entorno ejecutable + `pytest` | ✅ `.venv` sobre Python 3.13 (`DECISIONS #22`); **637 tests con `pytest tests`** (203 antes de M.1; 352 antes de M.1.6 y M.1.7; 386 al cerrar M.1; 612 antes de M.6.1). La raíz también junta los scripts de `scratch/`, que piden GEE. Desde geeworker2#14, el test de arranque ya no sale a la red con el `.env` local (§4, `DECISIONS #37`) |
| **CI** (`.github/workflows/ci.yml`) | ✅ 2026-09-14 — verde en `main` ([PR #1](https://github.com/TechSupportKaapeh/geeworker2/pull/1)), y un PR con un test roto sale rojo en pytest (#2, cerrado). `main` todavía sin proteger (M.0.6, equipo). [`CI.md`](CI.md), `DECISIONS #34` |
| `.venv` == los requirements | ✅ 2026-09-02 — `requirements-dev.txt` con `pytest`, `httpx`, `ruff` y `pip-audit` (F.15) |

## 2b. La cadena de tiles, verificada

Al **2026-08-26** las tres piezas se hablaron por primera vez. Un COG en MinIO,
leído por el tileserver desplegado, con un token firmado por Geocore.

| | |
|---|---|
| Tileserver desplegado | ✅ `terra-tileserver-production.up.railway.app` |
| Convención de `storage_key` contra un bucket real | ✅ `ranchos/{id}/pasadas/{fecha}/ndvi.tif` |
| `MAP_TOKEN_SECRET` idéntico de los dos lados | ✅ 401 sin token, 200 con token |
| `tiler-ro` alcanza para las lecturas de GDAL | ✅ |
| `minio.railway.internal:9000` resuelve | ✅ **con puerto explícito** — ver abajo |
| `Cache-Control` sobre un tile real | ✅ `public, max-age=31536000, immutable` |
| PNG transparente en los bordes | ✅ 68 bytes |

**Se verifica con un comando**, desde el repo del tileserver:

```powershell
python scripts\check_prod.py --base https://<titiler> --token $TOKEN --key <storage_key>
```

Y el estado de un deploy, sin token, con `GET /health/ready`. Los dos están
documentados en el README de `terra-tileserver`.

**El dominio privado de Railway lleva puerto explícito (`:9000`) y va sin TLS;
el público va sin puerto y con TLS.** Confundirlos es el error más repetido de
este despliegue y hoy lo detecta `/health/ready` sin tocar la red.

---

## 3. Contratos con Geocore

Cambiarlos deja de ser trabajo local: no hay compilador que agarre el error.

**Esquema de `geodata` — columnas en snake_case.** Definitivo desde la migración
`20260817165517_GeoDataSnakeCase`. Verificado contra la DB real el 2026-08-19, y **desde el
2026-09-17 lo verifica `python check_schema.py`**, que compara el esquema contra el contrato
de abajo y sale con código 1 si falta algo (`DECISIONS #46`). Correrlo después de cada
migración de Geocore, y antes de M.4.3.

```
layers            id, tenant_id, parcela_id, rancho_id, storage_key,
                  product, acquired_ts, bbox, created_at, source
measurements      parcela_id, indice, fecha, tenant_id, valor, min_val, max_val,
                  estadisticas (jsonb), cobertura, observaciones, receta
                  PK: (parcela_id, indice, fecha)
                  (las 4 ultimas y `valor` nullable, migracion MedicionesMensuales,
                   aplicada el 2026-09-15)
processing_jobs   id, tenant_id, parcela_id, rancho_id, request_type, status,
                  progress, error_message, created_by, created_at,
                  started_at, finished_at, periodo
processing_job_events
                  id, job_id, created_at, attempt, stage, level, message, detail
                  (2026-09-12, migracion ProcessingJobEvents; el worker solo inserta)
sentinel2_dates   PascalCase — tabla del worker, no la administra EF Core
```

**La bitácora (2026-09-12).** Cómo se llenan las columnas:

| Columna | Qué lleva |
|---|---|
| `attempt` | Desde 1. |
| `level` | `info`, `warning` o `error`. |
| `stage` | El id del step, o `inicio` / `fin` / `reintento`. |
| `detail` | JSON con `desde`, `hasta`, `imagenes`, `escritas`, `megas`, `ms` y `error`. |

La escribe `registrar_evento_job`. El panel traduce los niveles y los `request_type`: si se cambia algo, avisar en `terra-admin/src/lib/procesos.ts`.

**`storage_key` guarda la key pelada.** Geocore compone
`s3://{GeoData:MinioBucket}/{storage_key}` (`LayersController.cs`). Guardar la
URI completa produce `s3://terra-assets/s3://…` y TiTiler no resuelve nada.

Convención de keys, bucket único `terra-assets`:

```
ranchos/{ranchoId}/{periodo}_{indice}.tif     process_rancho
parcelas/{parcelaId}/{periodo}_{indice}.tif   process_parcela y los on-demand
exports/{parcelaId}/{fecha}_{indice}.{fmt}    export_data (.tif, .png o .csv)
```

`{fecha}` es `YYYY-MM-DD`. **Los cuatro prefijos son planos**: `{entidad}/{id}/`
y el archivo. No hay subcarpeta por fecha.

**Lo mensual, desde M.4 (`DECISIONS #47`), va en otra forma**, con el tenant primero y
la receta adentro:

```
tenants/{tenantId}/ranchos/{ranchoId}/{receta}/{indice}/{AAAA-MM}.tif
```

La arma `pipeline/claves.py:claves_cog_mensual()`, con su natural_key
(`rancho_mensual_{indice}_{ranchoId}_{AAAA-MM}`, sin la receta: una fila por mes). Los
uuid salen en minúsculas y con guiones, que es como Geocore va a firmar el tenant del token
en M.8.1. Las keys de arriba son de la capa vieja: no se migran, y las borra M.6.

**El COG mensual lleva máscara interna** (M.4.5, `DECISIONS #51`): el GeoTIFF de GEE no
declara nodata, así que lo enmascarado se rellena con `-9999` y `convert_to_cog(…, nodata=…)`
lo convierte en la máscara. El tileserver pinta esos píxeles transparentes. Un mes sin un
píxel limpio no tiene COG ni fila en `layers`.

⚠️ Dos cosas que no coinciden con esto y conviene tener presentes:

- **`ranchos/{id}/{fecha}_original.tif` ya no existe** — se borró con E.8 el
  2026-09-04 (`DECISIONS #19` y `#26`). Si aparece una key así en el bucket, es
  de antes de esa fecha.
- **`heatmaps/` tampoco existe** — se unificó con `parcelas/` el 2026-09-07
  (E.9). Estaba separado por *por qué se pidió* en vez de por *qué es*, y su
  `natural_key` **colisionaba** con la de la capa sistemática, dejando un objeto
  huérfano en el bucket.
- **`{periodo}` es `{fecha}` para una sola pasada y `{inicio}_{fin}` para un
  rango.** Antes la key usaba solo el inicio, así que dos rangos distintos
  escribían el mismo objeto.
- **Las dos claves de una capa salen de `claves_de_capa()`**, no se arman a
  mano: la del bucket y la `natural_key` que siembra el UUIDv5 de `layers`
  tienen que identificar la misma capa.
- **El COG de prueba del 2026-08-26 está en otro layout**:
  `ranchos/{id}/pasadas/{fecha}/ndvi.tif`. Se subió a mano para el primer tile y
  **el worker nunca produce esa forma**. Es el que sigue en el bucket bajo el
  rancho inventado `00000000-…-0001`.

**Eventos consumidos.** `terra/rancho.created` y `terra/parcela.created` disparan
el procesamiento; los 5 `terra/parcela.*.requested` son pedidos puntuales.
Coordenadas como `CoordinateDto`, nunca ValueTuples.

**Desde el 2026-09-12 las altas traen `JobId`**, con `request_type`
`ParcelaInicial` o `RanchoInicial`. Puede venir `null` si Geocore no pudo crear
el job: en ese caso el worker procesa igual y no reporta.

**Idempotencia obligatoria.** Inngest reintenta y el backoff de Geocore puede
publicar dos veces. `layers` usa un UUIDv5 determinista; `measurements`, un
`ON CONFLICT` sobre su PK.

**Lo mensual se escribe con `upsert_mediciones_mensuales`** (M.4.3, `DECISIONS #49`): una
fila por índice y mes, **también con `valor` nulo** cuando la cobertura quedó bajo el
mínimo, con `estadisticas`, `cobertura`, `observaciones` y `receta`. En el conflicto pone
`min_val` y `max_val` en `NULL`. `insert_layer` acepta `receta` y `estadisticas` (un
`dict`). Probado contra PostGIS con las migraciones de Geocore el 2026-09-17.

**Tres variables tienen que coincidir entre repos.** Nada las valida al
desplegar, pero desde el 2026-08-26 `GET /health/ready` del tileserver detecta
las dos primeras sin necesidad de un tile:

| GeeWorker | Geocore | TiTiler | Si no coinciden |
|---|---|---|---|
| `MINIO_BUCKET` | `GeoData__MinioBucket` | `MINIO_BUCKET` | 400 `URL_NO_PERMITIDA` por tile |
| — | `GeoData__MapTokenSecret` | `MAP_TOKEN_SECRET` | 401 en todos los tiles |
| — | `GeoData__TiTilerUrl` | — | El front pide tiles a la nada |

**Geocore necesita las dos cadenas de conexión, en formato Npgsql.** `geodata`
no es otra base del mismo servidor: es **otro proyecto de Supabase**.

| Variable | Apunta a | Si falta o está mal |
|---|---|---|
| `ConnectionStrings__Default` | Proyecto principal | **Toda** request autenticada falla: `TenantMiddleware` va a la base antes de cualquier controller |
| `ConnectionStrings__GeoData` | Proyecto de `geodata` | No falla al arrancar: cae a `Default` (`DependencyInjection.cs:25`) y busca `layers` donde no existe |

El formato URI (`postgresql://…`) que dan Supabase y Railway **no lo acepta
Npgsql**: hay que traducirlo a `Host=…;Port=…;Database=…;Username=…;Password=…;SSL Mode=Require`.
Supabase lo ofrece ya convertido en la pestaña `.NET` del diálogo de conexión.

---

## 4. Deuda abierta

**Cerrada el 2026-09-15** (sesión 3, geeworker2#14, `DECISIONS #37`):

- ✅ **En local, la suite le hablaba de verdad a GEE, a la base y a MinIO.**
  `test_el_worker_arranca_aunque_falten_las_credenciales` reemplazaba `app.init_ee` y
  `app.init_db`, pero `_startup()` termina en `registrar_conexiones()`, que importa su
  propio `init_ee`. Ahora el test reemplaza también `registrar_conexiones` y sabotea
  `socket.connect`. En el control negativo, el test como estaba anotó 9 intentos.
  **El guardia no ve la base:** psycopg2 se conecta desde libpq, en C.

  Cómo se encontró: [`SESSION_2026-09-15_el_nucleo_del_pipeline.md`](SESSION_2026-09-15_el_nucleo_del_pipeline.md) §2.2.

**Abierta el 2026-09-12** (sesión del día):

- **`get_sentinel2_dates` hace un `getInfo()` por imagen**: cientos en el
  histórico de dos años. Ahora se ve el avance por trimestre, pero sigue siendo
  lo lento. `aggregate_array` lo haría en una sola llamada.
- **`compute_timeseries` (a demanda) conserva el tope de 30 imágenes**, ordenadas
  de la más vieja: un rango largo pierde el final. `process_parcela` ya no lo
  sufre porque va mes por mes.
- ✅ **Verificado en la primera corrida real: `ctx.attempt` vale 0 en el request
  que recibe un `StepError`.** La línea `fin` queda con `attempt` 1 aunque el step
  haya fallado cuatro veces. El panel ya no la usa para separar intentos.
- **La capa de satélite tiene deuda que el pipeline mensual reemplaza** en vez de
  arreglar:
  - fórmulas duplicadas;
  - `cloud_pct` que no se usa;
  - una SCS+C incompleta;
  - stats y export a CSV con el rango vacío;
  - `try/except` que no pueden disparar.

  Detalle en la sesión del 2026-09-12 (tarde) §2, y en `ARQUITECTURA_PIPELINE.md`
  §8 y §9.

**Cerrado el 2026-08-30** — se deja el registro porque explica qué mirar si algo
de esto reaparece:

- **`get_ranch_parcels` era un simulacro** que inventaba parcelas `-A`/`-B`
  contra una columna `uuid`. Borrado junto con su step y todo
  `services/geocore_client.py` (E.1). `process_rancho` queda con una sola
  responsabilidad: el ráster a nivel rancho.
- **La superficie HTTP de lectura ya no existe** (`DECISIONS #23`). Lo fija
  `tests/test_http_surface.py` como igualdad exacta, no como "contiene".

**Nuevo, salido de esos borrados:**

- **`sentinel2_dates` es un caché de solo escritura.** Se inserta en cada
  pedido y nadie lee la tabla. `PREGUNTAS_ABIERTAS` A-4.
- ✅ **El cliente de MinIO se construía sin `region`** — cerrado el 2026-09-01
  (F.12). Era `DECISIONS #21` otra vez, del lado de escritura: sin ese
  parámetro, la primera operación contra el bucket ejecuta un
  `GetBucketLocation` (`minio/api.py:481`) que exige un permiso **que la subida
  real no usa**, y contra una policy de solo escritura salía como un
  `AccessDenied` sobre el bucket que se lee como un problema de credenciales de
  `worker-rw` que no existe. Hoy la región viene de `AWS_REGION` en `config.py`,
  con el mismo nombre y default que el tileserver, y lo cuida
  `scripts/check_minio_region.py`. **Si A-3 falla, esto ya no es la causa** — se
  comprueba corriendo el script.
- ✅ **`storage_service` le pegaba a MinIO al importarse** — cerrado el
  2026-09-02 (F.13, `DECISIONS #24`). Era un singleton de módulo con
  `ensure_bucket()` en el constructor, así que `import app` abría una conexión.
  Hoy es `get_storage_service()` con `lru_cache`: uno solo —comparte el pool de
  urllib3, que es el motivo correcto— pero construido al primer uso, como
  `db_repository.get_connection()` ya hacía. **La suite pasó de ~33 s con 4
  tests a ~6 s con 24.** Lo fija un test que corre el import en un proceso
  aparte con el socket saboteado.
- ✅ **`ensure_bucket()` ya no corre en cada arranque** (misma decisión). Pedía
  `s3:ListBucket`, otro permiso que la subida real no usa. Sigue existiendo como
  método para el despliegue, y ya no se traga la excepción con un `print`.
- ✅ **Las credenciales de MinIO ya no caen a `minioadmin`** (misma decisión).
  Era la credencial root del `docker-compose`: un deploy sin las variables se
  autenticaba como root en vez de fallar. Es el patrón de default silencioso que
  `DECISIONS #16` eliminó en Geocore y el tileserver en `MAP_TOKEN_SECRET`; este
  era el tercero. **Ojo: `ENVIRONMENT=development` reabre el default.**

**La auditoría del 2026-09-02 y su cierre.** Los ocho hallazgos del worker viven
en [`OWASP_TOP10.md`](OWASP_TOP10.md) como `W-1` a `W-8`, con su estado. Siete
están cerrados; queda uno:

- ✅ **`W-6` `object_exists()` mezclaba "no existe" con "no pude averiguar"** —
  cerrado el 2026-09-04. Hoy devuelve `False` **solo** ante `NoSuchKey`; todo lo
  demás se propaga y lo reintenta Inngest. Importaba por **E.9**, que quiere
  saltear el recálculo si la capa ya existe: con el `False` viejo, un error de
  permisos o de red disparaba un recálculo completo en GEE o una sobrescritura
  decidida sobre un falso negativo.
- ✅ **`W-7` `get_presigned_url()` fallaba abierto** — **borrado** el 2026-09-04,
  no arreglado. Era el único método de la clase sin call sites, y devolvía una
  URL sin firma y sin vencimiento cuando el firmado fallaba. Criterio de
  `DECISIONS #23`. Un test lo fija para que no vuelva por costumbre.
- ✅ **`W-5` `print()` en vez del logger** — cerrado el 2026-09-04. Los 6 de
  código de producción (`app.py`, `db_repository.py`, `ee_indices.py`) pasaron a
  `logging.getLogger(__name__)`. Los de `scratch/` quedan: es material
  gitignoreado, no código del servicio.
- ✅ **`W-8` `/api/inngest` sin verificar la firma** — cerrado el 2026-09-04
  (`DECISIONS #25`). Era el único hallazgo bloqueante del despliegue.
- ✅ **`W-2` cerrado el 2026-09-07.** El default de `MINIO_SECURE` **se deduce
  del host**: solo `localhost`, `127.0.0.1`, `minio`, `host.docker.internal` y
  `*.railway.internal` arrancan sin TLS; **cualquier otro host se asume
  público**. Misma dirección que `IS_PRODUCTION`: lo desconocido se trata como lo
  más seguro. Antes era `False` fijo, así que un deploy que se olvidara la
  variable mandaba la access key, el secret y el COG **en texto plano** — y no
  fallaba, funcionaba.

**Durabilidad — cerrado el 2026-09-04** (`DECISIONS #26`):

- ✅ **Rutas temporales cruzando steps.** Era el reintento envenenado: un step
  memoiza lo que devuelve, así que el reintento recibía la ruta de un archivo
  que el intento anterior ya había borrado. **Ningún reintento podía funcionar.**
  Descarga, COG y subida van en un solo step, y lo que cruza es la
  `storage_key`. Regla: entre steps solo **referencias durables**.
- ✅ **Trabajo bloqueante en el event loop.** Los 8 handlers son `def` con
  `inngest.StepSync`. Lo fija un test que pregunta `is_handler_async` por cada
  función registrada.
- ✅ **`failed` en el primer intento.** Solo lo marca el último, comparando
  `ctx.attempt` contra un `RETRIES` compartido con los decoradores.
- ✅ **La grilla de descarga estaba duplicada** entre `inngest_handlers.py` y
  `export_service.py`, con `scale` y `crs` a mano en los dos lados: cumplían
  `DECISIONS #19` por casualidad. Ahora en `services/ee/gee_download.py`.
  Aparecieron dos cosas al unificar: **ninguna descarga tenía `timeout`**, y un
  GeoTIFF de 0 bytes pasaba el `raise_for_status` para reventar después en
  `rasterio` con un error que no mencionaba la descarga.

**Corrección:**

- **Fechas como string contra `timestamptz`.** El cast implícito usa el
  `TimeZone` de la sesión: si el server no está en UTC, se corren un día.
- **`insert_measurement` abre una conexión por fila** (~70 por serie anual).

**Higiene:**

- `DOCUMENTACION_TECNICA.md` duplicado en raíz y `docs/`, describe SQLite.
- `test.tif`, `test_file.txt` y `scratch/` siguen en disco, ya ignorados por
  `.gitignore`. `rewrite_handlers*.py` y `tests/test_api.py` se borraron el
  2026-08-30.
- `docker-compose.yml` levanta TimescaleDB; la DB real no tiene la extensión.
- 7 jobs colgados en `running` desde el 2026-08-10.
- Las 3 filas de `layers` del 2026-08-10 tienen `storage_key` en formato viejo.

---

## 5. Decisiones abiertas

Se mudaron a [`PREGUNTAS_ABIERTAS.md`](PREGUNTAS_ABIERTAS.md), donde cada una es
una tarea ejecutable por el pipeline de [`WORKFLOW.md`](WORKFLOW.md).

<details>
<summary>La tabla anterior, ya cubierta por ese documento</summary>

| | Estado |
|---|---|
| **Inngest Cloud vs self-hosted** | Sin decidir. Define si el worker necesita URL pública o queda privado. |
| **Estadísticas de rásters** | `layers` no tiene columnas para min/max/mean/stddev. Decidir si se pide migración a Geocore. |
| **Backup del volumen de MinIO** | Railway no trae snapshots. Aceptar el riesgo por escrito o resolverlo. |
| **Consola de MinIO pública** | Decidido dejarla abierta para monitoreo. Registrar el riesgo aceptado. |

---

</details>

---

## 5b. Lo que hay que decidir sobre el flujo mismo

Además de las de `PREGUNTAS_ABIERTAS`, dos que salieron de mirar el flujo real:

- **A-5 ✅ contestada por la UX prevista.** El mapa pinta los tiles del
  **rancho** y las parcelas van encima como **polígonos vectoriales**, con las
  `coordinates` que ya trae `ParcelaDto`. Las métricas por parcela son números
  de un `reduceRegion`. **Conclusión: no hacen falta COG por parcela** — el
  diseño barato no es recortar el rancho, es no producir los N ráster.
- ✅ **La contención de las parcelas dentro del rancho.** El 2026-09-12 se
  verificó que Geocore **no** la validaba, aunque se había dicho que sí. Desde ese
  día la valida al crear, al editar la geometría y en el import de KML, con una
  tolerancia del 1 % de la superficie (Geocore `DECISIONS #21`). Las parcelas
  creadas antes no se revisaron.
- 🔴 **A-6 — esa UX necesita métricas de rancho, y no hay dónde guardarlas.**
  `measurements` es `PK (parcela_id, indice, fecha)`, sin `rancho_id`, y
  `process_rancho` **no escribe ninguna medición**. Hay que elegir entre pedir
  la columna a Geocore o derivarlas promediando parcelas — que **no es lo
  mismo**: excluye la superficie del rancho que no pertenece a ninguna parcela.
  Y la UX pone **B-1** (mediana en el mapa vs promedio en el gráfico) en la
  misma pantalla, donde deja de ser teórico.
- ✅ **E.6 cerrada el 2026-09-07, y el bloqueo era falso.** El tipo estaba en el
  código de Geocore, que es quien define el esquema: `Measurement.Fecha` y
  `Layer.AcquiredTs`/`CreatedAt` son `DateTimeOffset`, o sea **`timestamptz`**.
  No hacía falta la DB. Y el problema era más grave de lo que E.6 decía: **`fecha`
  está en la PK**, así que el mismo día escrito desde dos husos daba dos filas y
  el `ON CONFLICT` no colapsaba ninguna — rompía la idempotencia, no solo corría
  la fecha. Lo normaliza `a_timestamptz()` en el borde con la DB.

---

## 5b-bis. 🔴 Las credenciales de la DB del `.env` están muertas

Al levantar el worker el 2026-09-07:

Primero fue el project ref:

```
FATAL: (ENOTFOUND) tenant/user postgres.<ref> not found
```

y después de cambiar las credenciales, el 2026-09-07:

```
FATAL: password authentication failed for user "postgres"
```

**El formato de `DB_USER` es correcto** —`postgres.<ref>`, que es lo que el
pooler de Supabase exige— y el ref ahora **resuelve**: ya no dice `ENOTFOUND`.
Lo que falla es la **contraseña**.

El worker arranca igual —el fallo de `init_db` se degrada a un ERROR en el log,
a propósito— pero `insert_layer` e `insert_measurement` van a fallar. **Es lo
único que queda de A-3.**

---

## 5c. El pedido a Geocore

[`PEDIDO_GEOCORE_GEODATA.md`](PEDIDO_GEOCORE_GEODATA.md) — redactado el
2026-09-07. Siete ítems: la tabla `rancho_measurements`, tres cambios de API,
estadísticas y parámetros en `layers`, y la calidad de cada medición.

**Ninguno es destructivo** —todos agregan tabla o columnas nullable—, así que el
worker sigue andando hasta que se actualice para escribirlas.

🔴 **El punto 0 es un bug de hoy y va aparte:** `GET /api/measurements` devuelve
las mediciones **más viejas** (`OrderBy(Fecha).Take(500)`).

---

## 6. Docs faltantes

Referenciados desde `docs/` y ausentes: `DEPLOYMENT_DECISION.md`, `TEAM.md`,
`SECURITY_FIXES.md`, `KML_CASOS_Y_REDUNDANCIA.md`,
`SESSION_2026-08-14_geodata_deploy.md`. El más necesario es **`TEAM.md`**: define
la forma de los payloads que los handlers dan por supuesta y nadie verificó.
