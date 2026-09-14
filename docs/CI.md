# El CI de los cuatro repos

> Desde el 2026-09-14 (sprint M.0 de [`SPRINTS_FASE_M.md`](SPRINTS_FASE_M.md)).
> Por qué es así: [`DECISIONS #34`](DECISIONS.md) (lo común) y `DECISIONS #24` de
> Geocore (lo propio de Geocore y del panel). Este archivo es la referencia de los
> cuatro repos. Geocore y el tileserver apuntan acá.

## Qué corre cada uno

Cada repo tiene un solo archivo, `.github/workflows/ci.yml`, con un solo job
llamado **`ci`**. Corre en cada PR y en cada push a `main`.

| Repo | Versión | Pasos |
|---|---|---|
| **worker** (`geeworker2`) | Python 3.13 | `pip install --only-binary=:all:` de los dos requirements · `pytest tests` · `ruff check` y `ruff format --check` **solo sobre `pipeline/`** · `pip-audit -r requirements.txt` |
| **Geocore** | .NET `10.0.x` | `restore` · `build -c Release` · `test` · paquetes vulnerables, transitivos incluidos: falla si aparece alguno |
| **panel** (`Terra-admin`) | Node 22 | `npm ci` · `npm run lint` · `npm run build` (incluye `tsc -b`) · `npm audit --omit=dev --audit-level=high` |
| **tileserver** (`terra-tileserver`) | Python 3.11 | `pip install -r requirements-dev.txt` · `pytest` |

Lo común a los cuatro:
- La versión es la de la imagen de cada Dockerfile. Un CI verde con otra versión no
  dice nada del deploy.
- `permissions: contents: read`: el CI no escribe en el repo ni publica nada.
- Un push nuevo a un PR cancela la corrida anterior del mismo PR. En `main` no se
  cancela, así que cada commit que llega a producción tiene su resultado.
- **Ninguno necesita secretos.** Se verificó corriendo cada suite desde un clon
  limpio, sin `.env` ni `.env.local`.

## Reproducirlo en local

Es lo mismo que la etapa VERIFY del [`WORKFLOW`](WORKFLOW.md). El CI es la red de
seguridad, no el reemplazo: se corre en local antes de abrir el PR.

```powershell
# worker
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m ruff check pipeline/
.venv\Scripts\python.exe -m ruff format --check pipeline/
.venv\Scripts\python.exe -m pip_audit -r requirements.txt

# Geocore
dotnet build Geocore.slnx -c Release
dotnet test Geocore.slnx -c Release --no-build
dotnet list Geocore.slnx package --vulnerable --include-transitive   # mirar la salida, no el código de salida

# panel
npm run lint; npm run build; npm audit --omit=dev --audit-level=high

# tileserver (su .venv está muerto: cualquier Python 3.11+ con requirements-dev.txt)
python -m pytest
```

## Dos cosas que no son obvias

**`dotnet list package --vulnerable` sale con código 0 aunque encuentre algo.**
Se probó el 2026-09-14 metiendo a propósito `System.Text.RegularExpressions 4.3.0`
(advisory High) en un clon: listó el paquete y salió con 0. Por eso el paso
busca la frase `has the following vulnerable packages`, y fija
`DOTNET_CLI_UI_LANGUAGE=en` para que la frase no dependa del idioma.

**El ruff estricto vive en `pipeline/ruff.toml`.** Ruff usa la configuración más
cercana a cada archivo. Dentro de `pipeline/` rige `select = ["ALL"]`, y cada
excepción está escrita con su porqué; en el resto del repo siguen las reglas por
defecto y sus 198 hallazgos históricos. Control negativo del 2026-09-14: una
función sin anotaciones da `ANN001` y `ANN202` dentro de `pipeline/`, y pasa
limpia en la raíz.

## 👥 M.0.6 — lo que hace el equipo

Hasta que esto esté hecho, el CI **avisa pero no frena**: un push directo a `main`
sigue desplegando.

### 1. Proteger `main` en GitHub, en los cuatro repos

Para cada repo de `TechSupportKaapeh` (`Geocore`, `geeworker2`, `Terra-admin` y
`terra-tileserver`):

**Settings → Rules → Rulesets → New ruleset → New branch ruleset**
- **Name:** `main`. **Enforcement status:** Active.
- **Target branches:** Add target → *Include default branch*.
- **Bypass list:** vacía. Si un admin puede saltearla, un push directo no se
  rechaza, y ese es el criterio de aceptación.
- Tildar:
  - *Restrict deletions*;
  - *Block force pushes*;
  - *Require a pull request before merging*, con **0** aprobaciones requeridas:
    hoy trabaja una sola persona, y con 1 nadie podría mergear su propio PR;
  - *Require status checks to pass* → *Add checks* → **`ci`**. El check aparece
    en la lista después de que el workflow corrió al menos una vez.

> ⚠️ **Plan de GitHub.** Los cuatro repos son privados. Los rulesets y la
> protección de ramas en repos privados piden GitHub Team para una organización
> (o Pro para una cuenta personal). Con el plan Free la pantalla aparece, pero la
> regla no se aplica. Si ese es el caso, las opciones son subir el plan o quedarse
> solo con el punto 2, que igual frena el deploy.

### 2. Activar "Wait for CI" en Railway, en los cuatro servicios

En cada servicio (Geocore, el worker, el tileserver y el panel):
**Settings → Source → Wait for CI** (el interruptor aparece cuando el repo
conectado tiene workflows de GitHub Actions).

Con eso, Railway espera a que el workflow del commit termine, y **si sale rojo no
despliega**. Es lo que cumple el objetivo del sprint, "que un test rojo no pueda
llegar a producción", aunque la protección del punto 1 no se pueda activar.

### 3. Cómo se verifica

- `git push origin main` con un commit cualquiera → GitHub lo rechaza con
  `GH013` (ruleset) o `GH006` (protección clásica).
- Un PR con un test roto → el check `ci` sale rojo y el botón de merge queda
  bloqueado.
- En Railway, un commit con el CI rojo aparece como *skipped*, no como
  desplegado.

## Si algo falla

| Síntoma | Causa |
|---|---|
| El push de `.github/workflows/` se rechaza con `refusing to allow an OAuth App to create or update workflow … without workflow scope` | La credencial de git no tiene el scope `workflow`. `gh auth refresh -s workflow` y `gh auth setup-git` |
| El CI del worker falla en "Instalar dependencias" con `No matching distribution` | Un pin sin wheel para cp313 en Linux. Es el pin el que está mal, no el CI (`DECISIONS #22`) |
| `ci` no aparece para elegirlo como check obligatorio | El workflow todavía no corrió en ese repo. Abrir un PR cualquiera |
| Rojo en `pip-audit`, `npm audit` o vulnerables sin haber tocado código | Salió un advisory nuevo. Se arregla subiendo la dependencia, no silenciando el paso |

## Lo que el CI no cubre (registrado)

- **Ninguno construye la imagen Docker.** La construye Railway. Un Dockerfile
  roto no lo agarra el CI, lo agarra el deploy.
  - Caso concreto que viene: el Dockerfile del worker copia los directorios uno
    por uno y **no copia `pipeline/`**. Cuando un handler lo importe (M.4.4), hay
    que sumar `COPY pipeline/ ./pipeline/`. Si no, el contenedor muere al
    arrancar: es el mismo bug que tuvo el tileserver.
- **El tileserver no audita dependencias**, y varias no tienen pin (`aiofiles`,
  `boto3`, `numpy`).
- **El panel no frena por las altas de sus herramientas de desarrollo.** El
  2026-09-14 eran 9, `vite` entre ellas: no llegan al bundle, pero `vite dev`
  corre en la máquina de quien desarrolla.
- **Las actions van por tag de major (`@v7`), no por SHA.** Son todas de
  `actions/*`, mantenidas por GitHub. Si se suma una action de terceros, esa va
  por SHA.
