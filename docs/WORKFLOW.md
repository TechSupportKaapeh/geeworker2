# WORKFLOW.md — Pipeline de desarrollo del ecosistema Terra

> Acordado el 2026-06-13. Describe **cómo** trabajamos cada cambio, no qué cambiamos.
> El objetivo es que cada incremento pase por las mismas etapas y compuertas, para
> confirmar que el proceso se sigue de forma consistente.
>
> **Revisión 2026-08-27:** el scope original decía "solo Geocore/ y terra-admin/",
> y la etapa VERIFY solo conocía `dotnet` y `npx tsc`. Los dos repos de Python
> —GeeWorker y el tileserver— quedaban fuera, así que el pipeline no se podía
> seguir aunque se quisiera. Ver §Repos y §6.

---

## Repos cubiertos

| Repo | Stack | Ruta local | Verificación |
|---|---|---|---|
| `Geocore` | .NET 10 | `Downloads\geocore` | `dotnet build` + `dotnet test` |
| `terra-admin` | React/TS | — | `npx tsc --noEmit` + `npm run build` |
| **GeeWorker** (`terra-api`) | Python | `Downloads\geework 2.0` | `pytest` |
| **terra-tileserver** | Python | `Downloads\tileserver-titiler` | `pytest` |

Los dos repos de Python son **hermanos**: viven en directorios distintos y una
misma tarea suele cruzarlos. Para tocar el tileserver desde el worker hay que
sumarlo con `/add-dir C:\Users\aayal\Downloads\tileserver-titiler`.

> ⚠️ El `.venv` del tileserver está **muerto**: se creó en otra máquina y su
> intérprete no existe. Los tests corren con cualquier Python 3.11+ instalando
> `requirements-dev.txt`; `scripts/check_prod.py` corre con librería estándar
> sola.
>
> El worker sí tiene `.venv` desde el **2026-08-30**, sobre Python 3.13
> (`DECISIONS #22`). Antes de esa fecha no existía y `pytest` ni siquiera
> colectaba, así que la compuerta VERIFY de este repo era inaplicable:
>
> ```powershell
> cd "C:\Users\aayal\Downloads\geework 2.0"
> py -3.13 -m venv .venv
> .venv\Scripts\python.exe -m pip install --only-binary=:all: -r requirements.txt -r requirements-dev.txt
> .venv\Scripts\python.exe -m pytest tests/ -q
> ```
>
> **`requirements-dev.txt` existe desde el 2026-09-02** y es de donde sale
> `pytest`: sin él la compuerta VERIFY no se puede correr. Lleva también `ruff`
> y `pip-audit`, pinneados — una herramienta sin pin tampoco es reproducible,
> porque dos versiones de ruff dan listas de hallazgos distintas.
>
> El `--only-binary=:all:` no es opcional: es la regla que impide que un pin
> sin wheel se ponga a compilar `rasterio` contra GDAL.

---

## Vista de pipeline

```
                ┌─────────────────────────────────────────────────────────────┐
                │  GUARDRAILES (aplican a TODO el pipeline)                     │
                │  • Scope: los 4 repos de la tabla de arriba                   │
                │  • Verificar en el código, no confiar en el tracker          │
                │  • Cuestionar decisiones; proponer, no solo obedecer         │
                │  • Los tests son la red de seguridad: nunca dejarlos rojos   │
                │  • Ante la duda, probar contra el sistema real antes que     │
                │    revisar con más cuidado (ver §6 y DECISIONS #21)          │
                └─────────────────────────────────────────────────────────────┘

   ┌──────────┐   ┌──────────┐   ┌────────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
   │ 1. PLAN  │──▶│ 2. BUILD │──▶│ 3. EXPLAIN │──▶│ 4. AUDIT │──▶│ 5. DOC   │──▶│ 6. VERIFY│
   │  acotar  │   │  SOLID + │   │  qué se    │   │ 5 ejes   │   │ inline   │   │ build +  │
   │  el      │   │  buenas  │   │  hizo y    │   │ (seg/ef/ │   │ en el    │   │ tests +  │
   │ incremento│  │ prácticas│   │  por qué   │   │ atom...) │   │ código   │   │ tracker  │
   └──────────┘   └──────────┘   └────────────┘   └────┬─────┘   └──────────┘   └────┬─────┘
                       ▲                                │                              │
                       │         si la auditoría        │     si verify falla         │
                       └───────  encuentra algo ────────┘◀───── (rojo) ───────────────┘
                                 (volver a BUILD)

                ┌─────────────────────────────────────────────────────────────┐
                │  Al cerrar el bloque funcional → atacar GAPS OWASP            │
                │  (N-5 rate limiting, N-6 audit log, N-7 deps) con el MISMO    │
                │  pipeline, uno por uno.                                       │
                └─────────────────────────────────────────────────────────────┘
```

---

## Las 6 etapas en detalle

### 1. PLAN — Acotar el incremento
- Definir un cambio **pequeño y coherente** (una pieza, no un megacommit).
- Leer el código real involucrado **antes** de escribir (no asumir desde docs/memoria).
- Si hay una decisión de diseño con trade-offs, **plantearla y recomendar** antes de codear.

### 2. BUILD — Escribir el código (SOLID + buenas prácticas)
- **S**RP: cada función/componente con una responsabilidad. (Ej.: `GeometryView` solo dibuja; `GeometryInput` solo edita.)
- **O**CP / **L**SP / **I**SP / **D**IP: depender de abstracciones (la arquitectura hexagonal del backend ya lo impone; en frontend, props/contratos claros como `Shape[]`).
- Buenas prácticas frontend: estado mínimo, sin lógica en el render que cause trabajo repetido, manejo explícito de errores (nada de `catch {}` silencioso), accesibilidad heredada de los componentes Base UI.
- Reutilizar patrones existentes del repo antes de inventar nuevos.

### 3. EXPLAIN — Explicar lo hecho
- Resumir **qué** cambió y, sobre todo, **por qué** (el razonamiento, no solo el diff).
- Señalar trade-offs y alternativas descartadas.

### 4. AUDIT — Auditoría de la pieza (compuerta de calidad)
Revisar explícitamente estos ejes y reportar hallazgos (incluso menores):

| Eje | Pregunta guía |
|---|---|
| 🔐 **Seguridad** | ¿Toca authz/authn? ¿Expone datos? ¿Vector de inyección/XSS? ¿Respeta aislamiento de tenant? ¿Mapea a OWASP? |
| ⚡ **Eficiencia** | ¿Trabajo O(n) innecesario? ¿Re-renders/refetch de más? ¿Round-trips evitables? ¿Tamaño de bundle? |
| ⚛️ **Atomicidad** | ¿La operación es todo-o-nada? ¿Estados intermedios inconsistentes? ¿Rollback ante fallo? |
| 🧱 **Corrección** | ¿Casos borde (null, vacío, concurrencia)? ¿La regla de dominio se respeta? |
| 📝 **Mantenibilidad** | ¿Se entiende? ¿Duplicación? ¿Acoplamiento innecesario? |

Si la auditoría encuentra algo → **volver a la etapa 2** y corregir antes de avanzar.

> **Deuda técnica del proceso (provisional):** Hoy la etapa 4 es una **auditoría manual**
> hecha por el asistente. Limitación real: es heurística, no determinista y no reproducible
> — no sustituye a un análisis estático. Pendiente integrar **SAST** (CodeQL, Semgrep o
> SonarQube; herramienta **por definir**) ejecutable y consultable por terminal, que correría
> reglas fijas (taint-tracking, CVEs conocidos, patrones) de forma consistente. Mientras no
> esté, la auditoría manual es un **stopgap más débil**, no una compuerta confiable. Al
> definir la herramienta, esta etapa pasa a: SAST automatizado → triage manual de hallazgos.

> **Primer tramo automatizado, en los repos de Python (2026-09-02).** Desde
> `requirements-dev.txt` hay dos comandos que corren reglas fijas:
>
> ```powershell
> .venv\Scripts\python.exe -m ruff check .                      # lint + imports
> .venv\Scripts\python.exe -m ruff check --select S,B .          # bandit + bugbear
> .venv\Scripts\python.exe -m pip_audit -r requirements.txt      # CVEs (OWASP A06)
> ```
>
> **No cierra la deuda de arriba, y la primera corrida lo demostró:** de los
> cinco hallazgos OWASP de la auditoría del 2026-09-02 —credenciales root por
> defecto, TLS apagado, la excepción tragada, el error que se lee como "no
> existe", la URL sin firmar— **ruff no encontró ninguno**. Encontró un import
> sin usar y seis anotaciones de tipo flojas. Es el reparto esperado: el linter
> libera a la revisión de lo mecánico, no la reemplaza.
>
> Dos cosas prácticas, aprendidas a la mala esa sesión:
>
> - **Pinnear la herramienta.** Dos versiones de ruff dan listas distintas, y un
>   bump aparece como "hallazgos nuevos" sin que nadie tocara el código.
> - **No silenciar reglas que no están activas.** Un `# noqa: XXX` de una regla
>   que el proyecto no corre lo marca ruff como `RUF100 unused noqa`. Comprobar
>   antes que la regla corra; si no corre, un comentario que explique el falso
>   positivo es mejor que un `noqa` inerte.

### 5. DOC — Documentar dentro del código
- Comentarios que expliquen el **porqué** (decisiones, gotchas, invariantes), no el qué obvio.
- Doc de funciones/componentes nuevos con su responsabilidad y contrato.

### 6. VERIFY — Verificación objetiva
- **Geocore:** `dotnet build` limpio + `dotnet test` verde.
- **terra-admin:** `npx tsc -p tsconfig.app.json --noEmit` + `npm run build`.
- **GeeWorker / tileserver:** `pytest` verde. No hay compilador que agarre un
  error de nombres, así que el peso recae entero en los tests.
- Caveat: build/typecheck **no** prueban runtime → un check en `vite dev`, o
  levantar la app y pegarle, cuando aplique.
- Actualizar los docs de contexto pertinentes (`HANDOFF`, `PLAN`,
  `PREGUNTAS_ABIERTAS`, `DECISIONS`, `SECURITY_FIXES`, `OWASP_TOP10`) y la
  memoria.

> **Probar contra lo real, no contra lo que uno cree.** Esta regla se ganó a la
> mala el 2026-08-26. Dos bugs sobrevivieron a tests que pasaban porque los tests
> usaban excepciones fabricadas a mano: la clasificación de fallos TLS nunca
> disparaba, y el sondeo de MinIO exigía un permiso que el camino real no usa
> (`DECISIONS #21`). Los dos aparecieron al reproducir el fallo contra un socket
> de verdad. Cuando una pieza habla con el mundo —red, storage, otro servicio—,
> al menos una verificación tiene que tocar el mundo.

> **Verificaciones ejecutables por repo.** Sirven de compuerta de VERIFY sin
> depender de que alguien se acuerde de los pasos:
>
> | Comando | Repo | Qué prueba |
> |---|---|---|
> | `scripts/check_prod.py` | tileserver | La cadena de tiles contra el deploy real, en 7 escalones |
> | `GET /health/ready` | tileserver | Config y MinIO alcanzable, sin token |
> | `scripts/check_mosaic_median.py` | tileserver | Que la composición por mediana respete el nodata |
> | `check_schema.py` | worker | Que el SQL case contra el esquema real |
> | `scripts/check_minio_region.py` | worker | Que la subida no pida permisos que no usa |
> | `scripts/check_write_path.py` | worker | **A-3**: el camino de escritura contra el MinIO real, en seis escalones |
> | `scripts/check_ingest_real.py` | worker | **A-3**: la cadena GEE -> COG -> MinIO con datos reales, y que `rio-cogeo` acepte el COG |
>
> El último no necesita MinIO ni credenciales: levanta un servidor S3 de mentira
> que **solo acepta el `PUT`** y verifica que la subida de un objeto chico emita
> esa petición y ninguna más — arriba de 5 MiB el camino es multipart y todavía
> no está cubierto. Trae control negativo, y sale en rojo si alguien quita el
> `region` del cliente creyéndolo redundante (`DECISIONS #21`, revisión del
> 2026-09-01). Es el patrón que conviene copiar cuando una pieza habla con un
> servicio que aplica permisos: **un doble que otorga exactamente los permisos
> del camino real convierte "pide algo de más" en un fallo reproducible en
> local**, sin esperar al despliegue.

---

## Definition of Done (checklist por incremento)

- [ ] Cambio acotado y código leído antes de editar.
- [ ] Código sigue SOLID + buenas prácticas de frontend/backend.
- [ ] Explicado el qué y el porqué.
- [ ] Auditado en los 5 ejes; hallazgos resueltos o registrados.
- [ ] Documentado inline.
- [ ] `build` + `tests` verde (back y/o front según aplique).
- [ ] Docs de contexto + memoria actualizados.

---

## Fase siguiente: gaps OWASP (mismo pipeline)

Cada gap se trata como un incremento completo a través de las 6 etapas:

1. **N-5 — Rate limiting** (OWASP A04) en login/`create-user`.
2. **N-6 — Audit log** (OWASP A09) de acciones privilegiadas de TerraStaff.
3. **N-7 — Auditoría de dependencias** (OWASP A06): `npm audit` / `dotnet list package --vulnerable`.

Referencias: [`SECURITY_FIXES.md`](SECURITY_FIXES.md) (tracker), [`OWASP_TOP10.md`](OWASP_TOP10.md) (mapeo).
