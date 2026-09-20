# Sesión 11 — el panel (sprint M.7 entero)

> 2026-09-20. Repo tocado: **terra-admin**, seis PR (#12 a #17), todos mergeados con el CI
> en verde. Geocore, el worker y el tileserver no cambiaron de código.
>
> El sprint estaba estimado en **2–3 sesiones** y entró en una. El motivo no es que se
> haya corrido: cuatro de las seis tareas ya tenían la mitad hecha de antes —el gráfico
> de la serie, la escala por índice, el deslizador de fechas y el editor por texto
> existían como prototipos del 2026-09-20 en Diagnóstico—, y lo que faltaba era ponerlos
> donde se trabaja.

## Lo que se hizo

| | Qué | PR |
|---|---|---|
| M.7.1 | `Selector`: el desplegable con `items` obligatorio | #12 |
| M.7.2 | `RanchosPage` partida en hooks y componentes, con pedidos cancelables | #13 |
| M.7.3 | La serie mensual de una parcela, en un panel lateral | #14 |
| M.7.4 | El mapa del rancho por mes, con su métrica | #15 |
| M.7.6 | Los primeros 51 tests del panel, en el CI | #16 |
| M.7.5 | Dibujar con clics, el rancho de fondo y los vértices afuera | #17 |

M.7.6 se hizo antes que M.7.5 porque M.7.5 arrastraba una decisión del usuario (la
licencia de la capa satelital) y los tests no.

## 1. M.7.1 — arreglar seis desplegables no era la tarea

El bug del 2026-09-12 fue que cinco pantallas mostraban un UUID donde tenía que decir un
nombre: **sin la prop `items`, el `Select.Value` de Base UI muestra el value elegido, no
la etiqueta**. Ese día se arregló poniendo `items` en los cinco.

Ocho días después, **seis de los trece desplegables del panel no la tenían**, y el de
Índice mostraba `ndvi` en el botón y `NDVI` en la lista. El arreglo no había cerrado nada:
sólo había corregido las instancias que existían.

Lo que cierra la clase son dos cosas, y ninguna es un desplegable:

- **la prop es obligatoria en el tipo** → olvidarla es `TS2741`, no una pantalla fea;
- **eslint prohíbe importar `@/components/ui/select`** fuera del propio `Selector.tsx`. Se
  restringe el import y no la prop porque una regla de lint no puede exigir una prop; el
  tipo sí.

Y una tercera que no estaba en la tarea: **el componente dibuja las opciones desde la misma
lista**, así que `items` y las `<SelectItem>` dejaron de ser dos listas que había que
mantener iguales a mano. En Tenants, los idiomas estaban escritos dos veces.

**Control negativo corrido**: el import prohibido sale `error` en `npm run lint`; el
`Selector` sin `items`, `TS2741` en `tsc`. Las dos compuertas corren en el CI.

## 2. M.7.2 — "pedidos cancelables" era un bug, no una prolijidad

La tarea decía "partir `RanchosPage` … con pedidos cancelables", y sonaba a higiene. No lo
era:

```
elegir tenant A → getRanchos(A) sale
elegir tenant B → getRanchos(B) sale
                  llega B  → setRanchos(B)
                  llega A  → setRanchos(A)   ← la tabla muestra A, con B elegido
```

En un panel multi-tenant eso no se puede permitir, y **no se veía en el código de la
pantalla**, que sólo decía `setRanchos(await getRanchos(tenantId))`.

La forma que quedó: `useCargado` guarda **la clave junto con los datos** y sólo devuelve
los que corresponden a la clave de ahora; mientras la nueva no llega devuelve `null`
—"cargando", no "vacío"—. Vaciar el estado en un efecto era la otra opción, y es la que el
lint del panel prohíbe.

Buscando ese caso aparecieron otros dos del mismo descuido:

- **cambiar de tenant no limpiaba el rancho elegido**: la pestaña Parcelas seguía mostrando
  las del tenant anterior, y el desplegable de Rancho quedaba con un id que ya no estaba en
  su lista — que es justo el caso en que Base UI vuelve a mostrar el UUID, o sea el bug de
  M.7.1 entrando por la otra puerta;
- el prototipo de Diagnóstico tenía su propio `fetch` sin cancelar, con la misma carrera
  entre parcelas e índices. Se pasó al hook compartido en M.7.3.

`RanchosPage`: **438 líneas → 217**.

## 3. M.7.3 y M.7.4 — dónde vive lo que ya estaba dibujado

Las dos tareas tenían el dibujo hecho y les faltaba **la pantalla**: el gráfico y la escala
por índice vivían en Diagnóstico, que sólo ve TerraAdmin.

**Decisión del usuario: panel lateral desde la tabla**, no una pestaña nueva. Es el patrón
que ya usaba la bitácora de un proceso, lo ve también TerraSupport —que es quien tiene que
contestar "¿por qué esta parcela no tiene datos en mayo?"— y se llega desde donde la
entidad ya está en pantalla. "Serie" en cada parcela, "Mapa" en cada rancho.

**La decisión que M.7.4 tenía pendiente —cómo mostrar el número del rancho contra su
mapa— se resolvió así: arriba del mapa, y la fracción del área con dato al lado del
número.** No es decoración: el promedio de Geocore **divide por el área con dato, no por la
total** (`DECISIONS #29`), así que un NDVI de 0,62 del 20 % del rancho no dice lo mismo que
el mismo 0,62 del 95 %. El número solo se lee mal.

Un caso que hubo que nombrar con palabras: **un mes puede tener ráster y no tener
métrica**. El rancho tuvo píxeles limpios pero ninguna parcela llegó a la cobertura mínima
de la receta. Un guión ahí no explica nada.

## 4. Tres piezas que se compartieron el mismo día en que se escribieron

No es refactor preventivo: en las tres, el segundo llamador apareció en la misma sesión.

| Pieza | Por qué no se duplicó |
|---|---|
| `SerieMensual` | El Diagnóstico y el panel lateral tienen que dibujar el mismo gráfico, o uno de los dos miente |
| `useMapToken` | La renovación **con 5 minutos de margen** es lo que evita que un token que vence en medio de un paneo deje el mapa lleno de 401 sin ningún error visible. Si vive en dos lados, un día uno de los dos se olvida del margen |
| `DeslizadorDeMeses` | El mismo control en el catálogo de Tiles y en el mapa del rancho |

También se mudó `PALETAS` a `lib/indices.ts`: la leyenda tiene que salir del **mismo**
degradado que el tile, y si no, el mapa dice una cosa y su escala otra sin ningún error a
la vista. Eso quedó como test.

## 5. M.7.6 — los primeros tests, y qué se eligió probar

El panel no tenía ninguno. Ahora tiene **51**, sobre `src/lib/`: funciones puras, sin DOM y
sin red, entorno `node` (jsdom sería una dependencia más para nada).

El criterio no fue "cubrir": fue **dónde un error no rompe la pantalla sino que la hace
mentir**, que es peor porque nadie mira dos veces un número que se ve bien.

- que el último proceso de un rancho **no** sea el de una de sus parcelas (el `else` que se
  olvida: un job de parcela lleva también el `ranchoId` de su rancho);
- que un `0` se muestre — `0 imágenes` es justamente lo que hay que ver;
- que la paleta que cada índice pide **exista**;
- que un campo vacío **no se mande** (iría como `""`, que es un municipio vacío y no "sin
  dato");
- que una altitud de `0` no sea lo mismo que sin altitud.

**Control negativo corrido**: se rompieron tres invariantes a propósito y **cada uno puso
en rojo su test y sólo ése**.

El paso en el CI se agregó **preguntando**: la decisión vigente era "del CI y de M.0.6 no
se toca nada por ahora", y el criterio de aceptación de M.7.6 era "corren en el CI". El
usuario confirmó que el paso va; **M.0.6 —proteger `main`, Wait for CI— sigue sin
tocarse**.

## 6. M.7.5 — lo que se pudo hacer sin la licencia

La tarea traía una pregunta 👥: si el editor lleva capa satelital de fondo, hay que
confirmar la licencia de la imagen. **El resto no dependía de eso**, así que se hizo el
resto y quedó anotado.

- **Dibujar con clics**, con los clics y el cuadro de texto como **lo mismo**: el clic
  escribe una línea `lat,lng` y el borrador se sigue derivando del texto. Así se dibuja a
  mano alzada y después se corrige un número, sin dos estados que se peleen.
- **El rancho de referencia** de fondo, y con el editor vacío el mapa **arranca encuadrado
  en él**, que es donde hay que dibujar.
- **Los vértices afuera** en rojo y nombrados por número. **No bloquea**: la autoridad es
  Geocore, que valida con PostGIS y contesta 422. El aviso está para no mandar un POST que
  ya se sabe que vuelve rechazado, y para ver **cuál** punto hay que mover.

La prueba de adentro/afuera es el método del rayo sobre lat/lng: **plana y no geodésica**,
lo cual está dicho en el código, en el doc y en un test. A la escala de un rancho no cambia
de lado salvo pegado al borde.

## Lo que no se verificó

**Nada se miró en pantalla.** El panel no tiene tests de componentes y el build no prueba
dibujo: lo que se corrió en cada PR fue `tsc`, `eslint`, `vite build` y, desde M.7.6,
`vitest`. **Lo mira el usuario.** Lo que más conviene abrir:

1. el catálogo de Tiles del Diagnóstico, que ahora comparte el token y el deslizador con
   el mapa del rancho — si algo se rompió, es ahí;
2. un rancho con meses sin ráster, para ver el deslizador con huecos;
3. una parcela con meses sin dato, para ver la línea cortada en vez de interpolada;
4. dibujar una parcela nueva sacando a propósito un vértice fuera del rancho.

## Números

| | Antes | Después |
|---|---|---|
| Tests del panel | 0 | **51** |
| `RanchosPage` | 438 líneas | **217** |
| Desplegables sin `items` | 6 de 13 | **0**, y el lint no deja volver |
| Warnings de `exhaustive-deps` | 5 | 3 |
| Pasos del CI del panel | 3 | 4 |
