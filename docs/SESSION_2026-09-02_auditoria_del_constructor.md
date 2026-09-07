# Sesión 2026-09-02 — La auditoría del constructor (F.13 y `DECISIONS #24`)

> Qué pasó y por qué. Estado permanente en [`HANDOFF.md`](HANDOFF.md).
> Lo que quedó sin decidir: [`PREGUNTAS_ABIERTAS.md`](PREGUNTAS_ABIERTAS.md).
> Qué sigue: [`PLAN.md`](PLAN.md). Sesión anterior:
> [`SESSION_2026-09-01_la_region_del_lado_de_escritura.md`](SESSION_2026-09-01_la_region_del_lado_de_escritura.md).

## Dónde quedó

**F.13 cerrada, dos hallazgos OWASP más del mismo constructor, y 20 tests
nuevos.** La suite pasó de **4 tests en ~33 s a 24 en ~6 s**: la diferencia era
I/O al importar.

Dos commits sobre `fix/region-minio-f12`, sin pushear.

```
6709512  refactor(storage)!: constructor sin I/O, perezoso y que falla cerrado (F.13)
0fa8e77  test(storage): los invariantes de storage entran en la compuerta VERIFY
```

Lo que sigue sin resolverse quedó como **F.14** (tres hallazgos 🟡), **F.15**
(`requirements-dev.txt`) y **F.16** (el scope de `OWASP_TOP10.md`).

---

## 1. La auditoría no encontró lo que buscaba

Se pidió auditar el cambio de F.12 —¿sigue buenas prácticas, mapea bien a
OWASP?—. El cambio de F.12 salió limpio: **ruff da 12 hallazgos antes y los
mismos 12 después**, verificado extrayendo los archivos del commit anterior y
pasándolos por el linter.

Pero al mirar el resto del constructor aparecieron **tres defectos más**, y los
tres son variantes de algo que este proyecto ya pagó dos veces.

### Herramientas usadas

| | Resultado |
|---|---|
| `pytest` | 4 → 24 tests, ~33 s → ~6 s |
| `ruff check` | 12 antes, 12 después del cambio de F.12. Lo nuevo, limpio |
| `ruff check --select S,B` (bandit + bugbear) | 1 falso positivo, 1 preexistente |
| `pip-audit -r requirements.txt` | *No known vulnerabilities* (A06 limpio) |
| Etapa AUDIT del `WORKFLOW` (5 ejes) | 5 hallazgos, 2 rojos |

⚠️ `ruff` y `pip-audit` se instalaron **a mano** en el `.venv`, que ya no
coincide con `requirements.txt` — justo lo que `DECISIONS #22` quería evitar.
Es F.15.

---

## 2. Los tres defectos del constructor (`DECISIONS #24`)

### El singleton era eager

`storage_service = StorageService()` a nivel de módulo, con `ensure_bucket()`
dentro del `__init__`. **`import app` abría un socket.**

El singleton en sí estaba bien: comparte el pool de urllib3 del cliente de
minio-py, y crear un cliente por subida tira ese pool y vuelve a pagar el saludo
TCP en cada archivo. Lo que estaba mal era el momento.

Pasa a `get_storage_service()` con `lru_cache`. La medición es la prueba: **la
suite tardaba ~33 s para 4 tests que no tocan la red, y ahora tarda ~6 s para
24.** Todo ese tiempo era minio-py reintentando contra `localhost:9000`.

### `ensure_bucket()` pedía otro permiso de más

Llama a `bucket_exists()`, que necesita `s3:ListBucket`. O sea: en el **mismo
constructor** del que acabábamos de sacar el `GetBucketLocation` quedaba una
segunda petición pidiendo un segundo permiso que la subida real no usa.

Crear el bucket es tarea de despliegue, no de cada arranque. Salió del
constructor y dejó de tragarse la excepción.

### Las credenciales caían a la root del docker-compose

```python
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "minioadmin")
```

Un deploy al que le faltara la variable no fallaba: **se autenticaba como root**.
Y en local funcionaba, porque el `docker-compose` corre MinIO con esas
credenciales exactas, así que el problema solo podía aparecer en producción.

Es el patrón que `DECISIONS #16` eliminó en Geocore (`?? DevMapTokenSecret`) y
que el tileserver eliminó en `MAP_TOKEN_SECRET`. **Los dos fallaban abiertos, y
este era el tercero del mismo tipo en el mismo ecosistema.**

---

## 3. Por qué levantar no contradice `DECISIONS #16`

`#16` decidió que un secreto faltante **degrade un endpoint** en vez de tumbar
el arranque, y eso sigue valiendo: el worker tiene que poder arrancar y
responder `/health` aunque el storage no esté configurado.

Por eso la validación **no corre al importar**. Corre al construir el cliente,
que ahora es perezoso. La diferencia es de momento, no de criterio: revienta
cuando alguien necesita el storage —dentro de un handler de Inngest, que
reintenta y registra el fallo en `processing_jobs`— y no cuando arranca el
proceso.

**Hacer el singleton perezoso es lo que hizo posible fallar cerrado.** Con el
singleton eager, levantar en el constructor habría sido levantar en el `import`,
que es exactamente el bloqueo de arranque que `#16` descartó. Los dos cambios se
necesitaban mutuamente, y por eso van en el mismo commit.

---

## 4. De paso: la coherencia del endpoint

`validate_endpoint()` detecta **sin tocar la red** las cuatro formas de confundir
los dos dominios de Railway, más el esquema de más y la barra final:

| Endpoint | `MINIO_SECURE` | Qué dice |
|---|---|---|
| `minio.railway.internal` | `False` | falta el puerto explícito `:9000` |
| `minio.railway.internal:9000` | `True` | el privado va sin TLS |
| `bucket-x.up.railway.app` | `False` | el público va con TLS |
| `http://minio.railway.internal:9000` | — | no lleva esquema |

Es el error más repetido de este despliegue, y equivocarse **no da un error de
SSL**: da un `ConnectionReset`.

Las dos validaciones son **funciones puras que reciben los valores** en vez de
leer la config al importarse — misma lección que el tileserver aprendió con
`crear_validador_de_token`: una validación que lee globales resueltas al
importar no se puede probar sin recargar módulos.

---

## 5. Los invariantes entraron a la compuerta

Hasta hoy **ningún test tocaba `storage_service`**. Los 4 de la suite fijaban la
superficie HTTP, y lo único que ejercitaba la subida era un script que hay que
acordarse de correr.

`tests/test_storage_service.py`, 20 tests, tres invariantes:

1. **La subida emite solo el `PUT`**, con su control negativo (un cliente sin
   `region` tiene que fallar). El doble de S3 **solo acepta el `PUT`**: si el
   cliente pide algo de más, no hay forma de que la prueba pase.
2. **Importar el módulo no abre conexiones.** Corre en un proceso aparte con
   `socket.connect` saboteado, porque el import se cachea por intérprete.
3. **Una config inválida falla cerrado nombrando la variable.**

`scripts/check_minio_region.py` queda como la versión humana y explicada; los
tests son los que corren solos.

---

## 6. Dos lecciones chicas sobre linters

- **Silenciar un linter que nadie corre es agregar deuda.** Puse un
  `# noqa: S105` para el falso positivo del secreto de mentira, y ruff lo marcó
  como `RUF100 unused noqa directive` — porque el proyecto no tiene la regla S
  activada. Reemplazado por un comentario que explica el falso positivo. Me pasó
  **dos veces en la misma sesión** (la segunda con `E402`), que es justamente el
  argumento para tener el linter fijado en `requirements-dev.txt` y corriéndolo,
  en vez de adivinar qué reglas están activas.
- **El linter no encontró ninguno de los cinco hallazgos OWASP.** Los cinco
  salieron de leer el código con las preguntas de la etapa AUDIT. `ruff` sí
  encontró un `import os` sin usar y seis anotaciones de tipo flojas. Es el
  reparto esperado, y es el argumento de `PREGUNTAS_ABIERTAS` E-2: el SAST no
  reemplaza la revisión, la libera de lo mecánico.

---

## 7. Lo que esta sesión NO arregló

- **Los tres hallazgos 🟡** (F.14): `object_exists()` que convierte cualquier
  error en "no existe" —y **E.9 se apoya en esa función**—,
  `get_presigned_url()` que devuelve una URL sin firmar cuando falla el firmado,
  y los `print()` en vez del logger.
- **`MINIO_SECURE` sigue con default `False`.** Ya no es silencioso, pero el
  default sigue siendo el inseguro.
- **`requirements-dev.txt` no existe** (F.15) y el `.venv` quedó desalineado.
- **`OWASP_TOP10.md` no cubre este repo** (F.16). Dice *"Scope: `Geocore/` +
  `terra-admin/src/`"*, cero menciones al worker, al tileserver o a MinIO. Los
  cinco hallazgos de hoy **no tienen dónde anotarse** salvo acá y en el
  `HANDOFF`. Es el mismo agujero que tenía `WORKFLOW.md` hasta el 2026-08-27
  (`PREGUNTAS_ABIERTAS` E-1): se arregló para el pipeline y no para el mapeo de
  seguridad.
- **El hueco del multipart** que abrió la sesión anterior: arriba de 5 MiB la
  subida es multipart y la compuerta ejercita solo el `PUT` único. Un COG real
  pesa más que eso.
- **Ningún test toca un handler.** `process_parcela`, `process_rancho` y la
  conversión a COG siguen sin cobertura.
- **El worker sigue sin escribir en MinIO real** (A-3). Esta sesión elimina tres
  causas de fallo conocidas y hace que las que queden se anuncien temprano y con
  el nombre de la variable. No prueba que la subida funcione.
