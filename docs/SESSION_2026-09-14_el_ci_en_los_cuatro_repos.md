# Sesión 2026-09-14 — El CI en los cuatro repos (sprint M.0)

> Estado permanente en [`HANDOFF.md`](HANDOFF.md). Sesión anterior:
> [`SESSION_2026-09-12_primera_corrida_y_el_pipeline_mensual.md`](SESSION_2026-09-12_primera_corrida_y_el_pipeline_mensual.md).
> Del lado de Geocore y el panel: `geocore/docs/SESSION_2026-09-14_ci_de_geocore_y_el_panel.md`.
> Referencia del CI: [`CI.md`](CI.md). Decisión: [`DECISIONS #34`](DECISIONS.md).
> Para retomar: `geocore/docs/PROXIMA_SESION.md`.

## Dónde quedó

**M.0.1 a M.0.5 están hechas**: cada una se mergeó por PR con el CI en verde, y el
push a `main` salió verde en los cuatro repos. Falta **M.0.6**, que hace el equipo:
proteger `main` y activar "Wait for CI" en Railway. Hasta entonces el CI **avisa
pero no frena**.

| Tarea | Repo | PR | Merge | CI en `main` |
|---|---|---|---|---|
| M.0.1 | worker | [geeworker2#1](https://github.com/TechSupportKaapeh/geeworker2/pull/1) | `1142b7a` | ✅ [run](https://github.com/TechSupportKaapeh/geeworker2/actions/runs/34907372934) |
| M.0.2 | Geocore | [Geocore#1](https://github.com/TechSupportKaapeh/Geocore/pull/1) | `dfbef59` | ✅ [run](https://github.com/TechSupportKaapeh/Geocore/actions/runs/34907421076) |
| M.0.3 | panel | [Terra-admin#1](https://github.com/TechSupportKaapeh/Terra-admin/pull/1) | `2395fff` | sin workflow propio: lo validó el PR de M.0.4 |
| M.0.4 | panel | [Terra-admin#2](https://github.com/TechSupportKaapeh/Terra-admin/pull/2) | `818f50f` | ✅ [run](https://github.com/TechSupportKaapeh/Terra-admin/actions/runs/34907165244) |
| M.0.5 | tileserver | [terra-tileserver#1](https://github.com/TechSupportKaapeh/terra-tileserver/pull/1) | `e5e9711` | ✅ [run](https://github.com/TechSupportKaapeh/terra-tileserver/actions/runs/34906259690) |

Las confirmaciones del usuario al arrancar:
- `gh auth login` lo corrió durante la sesión;
- el tablero **no** pasa a GitHub por ahora;
- a `TERRA_ADMIN_URL` todavía no le sumó `http://localhost:5173`, y `GEOCORE_API_URL`
  apunta al Geocore de Railway.

---

## 1. La línea de base

Antes de escribir un workflow se midió cada repo:

| Repo | Estado al empezar |
|---|---|
| worker | Python 3.13.9 · 203 verdes · `pip-audit` limpio · ruff 198 hallazgos · `pipeline/` no existía |
| Geocore | SDK 10.0.200 · build con 13 warnings · 243 verdes · 0 paquetes vulnerables, transitivos incluidos |
| panel | `tsc` limpio · **eslint con 9 errores** · **`npm audit --omit=dev`: 15 vulnerabilidades, 9 altas** |
| tileserver | `.venv` muerto; con un venv nuevo de 3.13, 150 verdes |

El panel era el único que no podía tener CI de entrada: con 9 errores de lint y 9
altas, el primer run iba a salir rojo. Por eso M.0.3 va antes que M.0.4.

## 2. ¿Las suites necesitan secretos?

Era la pregunta que decidía si el CI podía ser compuerta desde el primer día. Se
contestó corriendo cada suite **desde un clon limpio**, sin `.env` ni
`.env.local`, que es exactamente lo que ve el runner:
- el worker dio 203;
- Geocore, 243;
- el tileserver, 150;
- el panel hizo `npm ci`, lint, build y audit en verde.

Ninguna depende de secretos locales. En los repos públicos importa doble (§5): un
PR desde un fork también dispara el CI.

## 3. Lo que salió en el camino

**`dotnet list package --vulnerable` sale con 0 aunque encuentre algo.** Se probó en
un clon de Geocore:
- el primer intento, con Newtonsoft 12, falló por otra cosa (`NU1605`, choca con el
  13 que trae el SDK de tests);
- el segundo, con `System.Text.RegularExpressions 4.3.0`, listó el paquete con
  severidad High y **salió con 0**.

Un paso que solo corriera el comando habría dado verde con un CVE adentro. El paso
busca la frase de la salida (Geocore `DECISIONS #24`).

**El ruff estricto es jerárquico** (`pipeline/ruff.toml`). Control negativo: una
función sin anotaciones da `ANN001` y `ANN202` en `pipeline/`, y la misma función
pasa limpia en la raíz. El primer intento sumó un hallazgo al total del repo:
`CPY001`, un aviso de copyright que el repo no usa. Se excluyó con su porqué, y el
total volvió a 198.

**Los pins del worker se instalaron por primera vez en Linux.** El Dockerfile nunca
se construyó (FASE H), así que no estaba probado que `--only-binary=:all:` pasara
en Linux con cp313. En el runner pasó, y la suite corrió en 5 s (en Windows, 44 s).

**Las actions van por la major vigente:** `checkout@v7`, `setup-python@v7`,
`setup-dotnet@v6` y `setup-node@v7`. Antes de usarlas se leyó el `action.yml` de
cada una para confirmar que aceptan los inputs del workflow.

## 4. Las pruebas de aceptación

M.0.1 (y M.0.2, "ídem") pedía que **un PR con un test roto saliera rojo**. Se abrió
un PR borrador por repo, con un único test que falla a propósito, y se miró en qué
paso caía. Un rojo en la instalación o en el build no probaría lo mismo.

| PR | Dónde cayó | Resultado |
|---|---|---|
| [geeworker2#2](https://github.com/TechSupportKaapeh/geeworker2/pull/2) | paso **pytest**; ruff y `pip-audit` salteados | `1 failed, 203 passed`, por `test_el_ci_tiene_que_salir_rojo` |
| [Geocore#2](https://github.com/TechSupportKaapeh/Geocore/pull/2) | paso **Test**, con Build en verde | Domain: `1 failed` de 116, por `El_CI_tiene_que_salir_rojo` |

Los dos se cerraron sin mergear, con el resultado como comentario, y sus ramas se
borraron. En los verdes también se miró cada paso (ninguno salteado) y los números
del log: 203, 243 (115 + 70 + 58) y 150 (ahora con 3.11).

## 5. Lo que se descubrió de GitHub

- **`TechSupportKaapeh` es una cuenta personal**, no una organización: `gh` quedó
  logueado con esa cuenta.
- **`geeworker2`, `Terra-admin` y `terra-tileserver` son públicos; Geocore es
  privado.** Los docs decían "los cuatro son privados"; se corrigió con lo que
  devuelve la API.
- La protección de `main` se consultó por API: da `404 Branch not protected` en los
  tres públicos, así que ahí está disponible con el plan gratis, y
  `403 Upgrade to GitHub Pro` en Geocore. M.0.6 queda así en [`CI.md`](CI.md).

> ⚠️ **Tres repos son públicos, y eso no estaba en ningún doc.** El `HANDOFF` de este
> repo describe la infraestructura: dominios de Railway, nombres de variables,
> dónde vive cada credencial. Ningún valor secreto, pero sí el mapa. Esta sesión no
> revisó si el **historial** de los tres tiene algo sensible. En el de Geocore hubo
> un secreto (`DECISIONS #16` de Geocore), pero Geocore es privado. Queda para el
> usuario confirmar si la visibilidad es intencional.

## 6. Un tropiezo propio

El commit de la corrección de "privados" falló: el mensaje llevaba comillas dobles,
y PowerShell 5.1 las rompe al pasárselas a `git`, que tomó las palabras como rutas.
Los cambios quedaron en el índice, y el commit siguiente, el del test roto, se los
llevó a su rama. Se arregló hacia adelante, sin force-push:
1. los dos archivos se trajeron a la rama de la tarea y se commitearon con
   `git commit -F`, con el mensaje leído desde un archivo;
2. la rama de la tarea se mergeó dentro de la de prueba, para que su PR mostrara
   solo el test.

Desde ahí, todos los mensajes de commit salen de un archivo.

## 7. Lo que NO se verificó

- **"Wait for CI" en Railway:** no se puede configurar desde acá; es de M.0.6.
- **Los deploys que dispararon los merges.** Solo se miró el tileserver
  (`/health` → 200). Del worker, Geocore y el panel no hay URL a mano ni acceso a
  Railway desde esta sesión.
- **El CI no construye imágenes Docker.** Un Dockerfile roto lo agarra el deploy, no
  el CI. El caso que viene: el Dockerfile del worker no copia `pipeline/` (M.4.4).

## 8. Lo que sigue

1. 👥 **M.0.6**, con los pasos de [`CI.md`](CI.md): los rulesets en los tres repos
   públicos, la decisión sobre GitHub Pro para Geocore, y "Wait for CI" en los cuatro
   servicios de Railway.
2. **M.1**, el núcleo del pipeline sin GEE. Puede empezar aunque M.0.6 no esté: el
   paquete `pipeline/` no lo importa ningún handler, así que M.1 no cambia nada de lo
   que corre en producción.
