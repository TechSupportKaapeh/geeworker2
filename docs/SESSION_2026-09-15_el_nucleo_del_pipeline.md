# Sesión 2026-09-14/15 — El núcleo del pipeline (sprint M.1)

> Estado permanente en [`HANDOFF.md`](HANDOFF.md). Sesión anterior:
> [`SESSION_2026-09-14_el_ci_en_los_cuatro_repos.md`](SESSION_2026-09-14_el_ci_en_los_cuatro_repos.md).
> Diseño: [`ARQUITECTURA_PIPELINE.md`](ARQUITECTURA_PIPELINE.md). Decisión:
> [`DECISIONS #35`](DECISIONS.md). Tablero: [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md).
> Para retomar: `geocore/docs/PROXIMA_SESION.md`.

## Dónde quedó

**El sprint M.1 está cerrado.** M.1.1 a M.1.5 se hicieron cada una en su rama y se
mergearon por PR con el CI en verde. `pipeline/` tiene el núcleo puro del diseño
(`ARQUITECTURA` §3.2, §3.4 y §4): meses, fórmulas, registros de índices y de
estadísticas, y la receta versionada. Nada de eso toca GEE ni la red, y ningún
handler lo importa todavía, así que **en producción no cambió nada**.

| Tarea | PR | Merge | Tests nuevos |
|---|---|---|---|
| M.1.1 `periodos` | [geeworker2#4](https://github.com/TechSupportKaapeh/geeworker2/pull/4) | `02f2f94` | 39 |
| M.1.2 `indices` | [geeworker2#5](https://github.com/TechSupportKaapeh/geeworker2/pull/5) | `4d1364d` | 54 |
| M.1.3 `estadisticas` | [geeworker2#6](https://github.com/TechSupportKaapeh/geeworker2/pull/6) | `050d91f` | 18 |
| M.1.4 `receta` | [geeworker2#7](https://github.com/TechSupportKaapeh/geeworker2/pull/7) | `c30284e` | 36 |
| M.1.5 importar sin red | [geeworker2#8](https://github.com/TechSupportKaapeh/geeworker2/pull/8) | `93d9e2c` | 2 |

**La suite pasó de 203 a 352 verdes.** El ruff estricto de `pipeline/`
(`select = ["ALL"]`) está limpio, sin una sola excepción nueva en `ruff.toml`. Los
tests nuevos no suman hallazgos al ruff por defecto de la raíz.

Las tres confirmaciones que el prompt de arranque pedía llegaron **sin tildar**, y se
tomaron como no confirmadas:
- si el equipo hizo M.0.6;
- si la visibilidad pública de los tres repos es a propósito;
- si se sumó `http://localhost:5173` a `TERRA_ADMIN_URL`.

M.1 no dependía de ninguna.

---

## 1. Lo que hay en `pipeline/`

| Módulo | Qué es |
|---|---|
| `periodos.py` | `Mes` (`AAAA-MM`, orden, `anterior`, `siguiente`, `desplazar`), `rango(mes)` semiabierto en UTC, `meses_cerrados(hoy, n)` y `hoy_utc()` |
| `formulas.py` | 🆕 El lenguaje de las fórmulas: `bandas_de()` valida y `evaluar()` calcula un píxel en Python |
| `registro.py` | 🆕 `registro(*entradas)`: un mapa inmutable por nombre, validado al importar |
| `indices.py` | `INDICES` (NDVI, EVI, NDRE, NDMI), `BANDAS` y `BANDAS_S2` |
| `estadisticas.py` | `ESTADISTICAS` (las siete) y `claves_de_salida()` |
| `receta.py` | `Receta`, `RECETA_VIGENTE = s2-mensual-v1`, `contenido()` y `huella()` |

Los dos 🆕 no estaban en `ARQUITECTURA` §4: se agregaron ahí. El porqué de cada
decisión está en `DECISIONS #35`.

## 2. Lo que salió al probar contra lo real

### 2.1 Los métodos de `ee.Reducer` existen antes de `ee.Initialize()`

`ARQUITECTURA` §3.2 decía que "las clases de GEE existen recién después de
`ee.Initialize()`", y por eso el registro guardaba `lambda: ee.Reducer.median()`.
El ruff estricto marcó esas `lambda` como innecesarias (`PLW0108`). Antes de
silenciar la regla se probó con el `earthengine-api==1.7.41` pinneado:

```
ee.Reducer.median            -> existe al importar
ee.Reducer.median()          -> EEException: Earth Engine client library not initialized
ee.Reducer.percentile([10])  -> EEException: ídem
```

La conclusión del diseño se mantiene: el registro guarda fábricas, porque un
reductor no se puede armar antes de inicializar. Pero la razón era otra, y la
fábrica puede ser el método mismo. Ruff tenía razón. §3.2 quedó corregido, y un
test fija el hecho en un proceso aparte.

### 2.2 La suite en local le habla a GEE, a la base y a MinIO

La primera versión de ese test pasaba sola y salía roja dentro de la suite: GEE ya
estaba inicializado. Se descartaron tres hipótesis antes de dar con la causa, y
conviene dejarlas escritas porque las tres parecían razonables:

1. **Que el startup inicializara GEE.** No: el test reemplaza `app.init_ee` con
   `monkeypatch`.
2. **Que `import app` lo hiciera.** No: después del import, GEE sigue sin
   inicializar.
3. **Que el reporte de arranque corriera en un hilo.** No: no hay hilos, y a los
   30 s GEE sigue igual.

**La causa.** `test_http_surface.py::test_el_worker_arranca_aunque_falten_las_credenciales`
reemplaza `app.init_ee` y `app.init_db`, y llama a `_startup()`. Pero `_startup()`
termina en `registrar_conexiones()`, que importa **su propio** `init_ee`
(`utils_pkg/conexiones.py:409`) y verifica el disco, MinIO, la base `geodata`, GEE
con un round-trip (`ee.Number(1).getInfo()`) e Inngest. Reproducido a mano con el
`.env` local: GEE respondió en 3,4 s.

**Consecuencias:**
- El test no prueba lo que dice. Su docstring habla de un arranque *sin*
  credenciales, y en local corre con las de verdad.
- "La suite no toca la red" vale solo sin `.env`, que es el caso del CI.
  `DECISIONS #34` lo verificó así, desde un clon limpio, y ahí sigue siendo cierto.

**No se arregló en esta sesión:** es anterior a M.1 y no era de ninguna tarea. El
arreglo es una línea: reemplazar también `app.registrar_conexiones` en ese test.
Queda en el `HANDOFF` §4.

### 2.3 La receta tenía más parámetros que los que listaba el tablero

La máscara de hoy (`mask_s2cloudless_and_shadows`) usa, además de `max_prob=45` y
los 50 m de dilatación, un umbral de NIR oscuro (`0.15 * 10000` sobre B8) y 1000 m
de proyección de la sombra. El diseño dice que todo parámetro que cambia un número
va a la receta (§3.4 y §8.7). Si quedaban afuera, M.2.2 los iba a escribir como
constantes en la etapa, y cambiarlos no habría movido la huella. Entraron a v1,
junto con las dos colecciones, y `sombras_nir_oscuro` va en reflectancia 0-1.

## 3. Lo que queda anotado para M.2

- **Confirmar contra GEE en M.2.6:**
  - que una división por cero da 0 (lo dice la documentación de `ee.Image.divide`;
    el evaluador de Python levanta `ZeroDivisionError` a propósito);
  - la clave de `reduceRegion` con **una** banda y un reductor de varias salidas
    (`ndvi_median` o `median`). La receta v1 siempre usa varias bandas y varias
    salidas, así que no la toca, pero `claves_de_salida` asume `ndvi_median`.
- **NDRE usa B5 (705 nm) y B8 (842 nm).** Sus valores no se comparan uno a uno con
  los de la literatura, que usa otras bandas.
- **`valor` = la mediana** (§6) no está en la receta: lo fija la escritura en M.4.3.
- Sigue en pie: el Dockerfile tiene que sumar `COPY pipeline/ ./pipeline/` antes de
  M.4.4 (`CI.md`).
- 👥 Para M.2.6: 3 parcelas reales y las credenciales de GEE en el `.env` local.

## 4. Lo que hice mal, para que no se repita

- **Repetí una afirmación del diseño sin probarla.** El primer docstring de
  `estadisticas.py` decía que los métodos de GEE no existían antes de inicializar.
  Lo desmintió un comando de tres líneas.
- **Le presenté al usuario una hipótesis como si fuera el hallazgo** ("el startup
  inicializa GEE") antes de leer el test que la descartaba. La retiré en el momento.
  En la crónica del PR y acá quedó solo la causa comprobada.
- **Conté mal los tests de M.1.3** (315 en vez de 314) al escribir el cuerpo del PR
  antes de correr la suite. Se corrigió antes de publicarlo. Regla: el número va
  después de la corrida, no antes.
- Una llamada de edición salió contra una ruta de relleno. Falló sin tocar nada.

## 5. Verificación

- `pytest tests`: **352 verdes**.
- `ruff check pipeline/` y `ruff format --check pipeline/`: limpios.
- CI en `main` después del último merge (`93d9e2c`): ✅
  [run](https://github.com/TechSupportKaapeh/geeworker2/actions/runs/34935249269).
- Cada merge se hizo detrás de un `gh pr checks <n>` que dijera `pass`: sin M.0.6,
  `gh pr merge` mergea aunque el CI esté rojo.
