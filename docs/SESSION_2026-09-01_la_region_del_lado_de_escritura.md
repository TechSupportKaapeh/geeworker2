# Sesión 2026-09-01 — La región del lado de escritura (F.12)

> Qué pasó y por qué. Estado permanente en [`HANDOFF.md`](HANDOFF.md).
> Lo que quedó sin decidir: [`PREGUNTAS_ABIERTAS.md`](PREGUNTAS_ABIERTAS.md).
> Qué sigue: [`PLAN.md`](PLAN.md).

## Dónde quedó

**F.12 cerrada, y con compuerta propia.** El cliente de MinIO del worker ya
recibe `region`, así que la subida deja de pedir un permiso que no usa. El
fallo se **reprodujo antes de arreglarlo**, contra un servidor S3 de mentira, y
esa reproducción quedó en el repo como `scripts/check_minio_region.py`.

Con eso, **A-3 deja de estar bloqueada de hecho**: lo que falta para la primera
escritura real ya no es código, es el dominio público de la API de MinIO y las
credenciales de `worker-rw`.

Sigue abierta **F.13**: importar el módulo todavía toca la red.

---

## 1. Qué era F.12, en una línea

`StorageService.__init__` construía el cliente sin `region`. Sin ese parámetro,
minio-py resuelve la región llamando a `GetBucketLocation` **antes** de la
primera operación sobre el bucket (`minio/api.py::_get_region`), y esa llamada
exige `s3:GetBucketLocation` — un permiso que la subida real no usa.

Es `DECISIONS #21` otra vez. La diferencia con agosto es dónde muerde: allá era
el sondeo de salud del tileserver —molesto pero inofensivo, los tiles se servían
igual porque GDAL no hace esa llamada—, acá es **el camino que el worker existe
para recorrer**.

---

## 2. Reproducir antes de arreglar

El guardrail del `WORKFLOW` dice que cuando una pieza habla con el mundo, al
menos una verificación tiene que tocar el mundo. Esta es la primera vez que se
aplicó **antes** del bug en vez de después.

Se levantó un servidor HTTP mínimo que hace de S3 y solo registra qué le piden.
No hace falta MinIO, ni credenciales, ni red: lo que se observa es **el orden de
las peticiones**.

### Escenario A — servidor permisivo, cliente como estaba

```
Durante `import services.storage_service`:
   1. GET   /terra-assets?location=     <-- GetBucketLocation
   2. HEAD  /terra-assets

Durante upload_bytes():
   1. GET   /terra-assets?location=     <-- GetBucketLocation
   2. PUT   /terra-assets/parcelas/prueba/...tif
```

La petición fantasma existe: para subir un archivo salen dos peticiones y la
primera no la pidió nadie. Y arriba, lo que era teoría hasta ese momento — **el
`import` disparó dos peticiones más**, antes de que el programa hiciera nada.

### Escenario B — el servidor niega *solo* el `GetBucketLocation`

O sea, los permisos exactos de una policy de escritura: el `PUT` permitido, la
pregunta por la ubicación no.

```
Durante upload_bytes():
   1. GET   /terra-assets?location=

Resultado: FALLO  S3Error: code: AccessDenied, resource: /terra-assets/
```

**El `PUT` nunca salió.** La subida —que el servidor habría aceptado— jamás se
intentó. Y el mensaje apunta al **bucket**, no al objeto, que es lo que hace que
cualquiera concluya *"`worker-rw` no tiene permisos"* y se vaya a revisar una
policy que está perfecta. Es palabra por palabra el camino que consumió la
sesión del 2026-08-26.

### Escenario C — el control

Mismo servidor negando, mismo código, una sola diferencia: `region="us-east-1"`.

```
Durante upload_bytes():
   1. PUT   /terra-assets/parcelas/prueba/...tif

Resultado: OK
```

Una petición en lugar de dos, **funcionando contra un servidor que deniega el
permiso que rompía**. Hipótesis cerrada: no era la policy ni las credenciales.

---

## 3. Tres cosas que el reproductor mostró y la lectura del código no

- **El caché de región es por cliente, no global.** En el escenario A el
  `GetBucketLocation` aparece dos veces para el mismo bucket, porque
  `_region_map` vive dentro de cada objeto `Minio`. Cada cliente nuevo vuelve a
  pagar el peaje.
- **`ensure_bucket()` gastaba una petición más de la contada.** El
  `HEAD /terra-assets` es `bucket_exists()`. El arranque pedía **dos** permisos
  que la subida no usa, no uno.
- **F.12 sola no alcanza para un arranque limpio.** En el escenario C la subida
  funciona y el warning del import sigue apareciendo. Los dos arreglos van
  juntos.

---

## 4. El arreglo

`config.py`:

```python
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
```

**Mismo nombre y mismo default que el tileserver** (`terra_tiles/settings.py`),
para que los dos lados del bucket no puedan divergir. Con el comentario que
explica por qué no es opcional aunque MinIO no tenga regiones — sin eso, el
próximo que lo lea lo borra por redundante.

`services/storage_service.py`: `region=AWS_REGION` en el constructor, con el
mismo razonamiento inline y un puntero al script que lo cuida.

---

## 5. `scripts/check_minio_region.py`

El worker no tenía carpeta `scripts/`. Ahora sí, y esta es la primera.

**Qué hace.** Levanta un servidor S3 de mentira que **solo acepta el `PUT`** y
niega todo lo demás —los permisos exactos de una policy de solo escritura—,
apunta el `StorageService` real contra él y mira la secuencia de peticiones.

**Para qué sirve.** `region` es un parámetro que parece redundante: MinIO ni
siquiera tiene regiones. Es exactamente el tipo de línea que alguien borra
"limpiando". Si se borra, el script se pone en rojo **acá**, en dos segundos,
en vez de aparecer contra la infraestructura real disfrazado de problema de
credenciales.

**Qué comprueba.**

| | Estado hoy | Qué exige |
|---|---|---|
| `subida` | ✅ OK | La subida de un objeto chico emite una sola petición, y es el `PUT` |
| `import` | 🟡 PENDIENTE | Importar el módulo no emite ninguna petición (F.13) |
| `control neg.` | ✅ OK | Un cliente **sin** `region` tiene que fallar |

⚠️ **El objeto de prueba pesa 14 bytes, y eso acota lo que la compuerta cubre.**
Verificado el 2026-09-02 contra el mismo servidor de mentira: pasando los
**5 MiB** (`MIN_PART_SIZE` en `minio/helpers.py:50`), `put_object` deja de emitir
un `PUT` único y pasa a multipart —`POST ?uploads`, un `PUT ?partNumber=N` por
trozo, `POST ?uploadId`—, con los trozos en paralelo y fuera de orden. Un COG de
un rancho real va a pesar más que eso, así que **el camino de escritura real es
casi con seguridad multipart y la compuerta no lo ejercita.** No debería cambiar
los permisos —las tres operaciones caen bajo `s3:PutObject`— pero eso está sin
verificar, y "no debería" es la clase de suposición que F.12 acaba de castigar.
Pendiente: subir también 6 MiB en el script y exigir que en ninguno de los dos
caminos aparezca un `GET ?location=`.

El control negativo es lo que hace que la primera comprobación signifique algo:
si un cliente sin `region` pasara, querría decir que minio-py cambió de
comportamiento y que la compuerta dejó de vigilar lo que cree vigilar. Es el
mismo criterio que el cuadrante SE del spike de MosaicJSON — sin un caso que
*deba* fallar, "todo pasa" no prueba nada.

`PENDIENTE` no tumba la corrida: es deuda conocida, no una regresión.

```powershell
.venv\Scripts\python.exe scripts\check_minio_region.py
```

**Antes del arreglo salía `exit=1`**, señalando la línea a tocar. Después,
`exit=0`. Esa transición es la prueba de que la compuerta mide lo que dice.

---

## 6. Qué logramos, y qué no

**Logramos:**

- La subida del worker pide **exactamente** los permisos que usa.
- Un modo de fallo que ya había costado una sesión entera queda cerrado con un
  comando que lo demuestra, en vez de con un párrafo en un doc.
- La regla de `DECISIONS #21` deja de estar redactada como anécdota del
  tileserver y pasa a valer para todo cliente S3 del ecosistema (revisión del
  2026-09-01).
- **A-3 se queda sin excusas de código.** Lo que falta es infraestructura.

**No logramos —y conviene decirlo:**

- **El worker sigue sin escribir en MinIO real.** Esto elimina una causa de
  fallo conocida; no prueba que la subida funcione. El servidor de la prueba es
  de mentira: acepta cualquier firma y no valida credenciales. Que el `PUT`
  salga solo no significa que MinIO lo vaya a aceptar.
- **F.13 sigue abierta.** El `import` toca la red, la suite tarda ~33 s para 4
  tests que no tocan nada, y el arranque puede escupir un warning de
  `AccessDenied` que no significa nada — que en A-3 es justo lo que manda a
  revisar la policy equivocada.
- **Ningún test toca un handler todavía.** Los 4 que hay fijan la superficie
  HTTP. `process_parcela`, `process_rancho` y la conversión a COG siguen sin
  cobertura.
- **Nada está pusheado**, y el worker sigue sin Dockerfile ni deploy.

---

## 7. En qué habíamos fallado

Vale registrarlo porque el patrón se repitió y no era falta de información.

1. **La lección estaba escrita y guardada en el lugar equivocado.**
   `DECISIONS #21` documentó el bug en agosto, pero como crónica de un problema
   del tileserver. Redactada así, no aplicaba a nada más — y el worker usaba la
   misma librería con el mismo parámetro faltante en el camino que más importa.
   **El fallo no fue no saberlo: fue archivarlo como anécdota en vez de como
   regla.**

2. **Verificamos en aislamiento algo que solo falla en integración.** El
   `storage_key` estaba validado con un mock, y un mock no tiene policy de
   permisos: contesta que sí. Este bug es estructuralmente invisible para un
   mock porque no vive en nuestro código, vive en lo que la librería hace de más
   contra un servidor que dice que no.

3. **Es la tercera vez que aparece la misma familia.** El test de clasificación
   TLS con excepciones fabricadas, el sondeo de salud pidiendo un permiso de
   más, y ahora la escritura. Los tres son verificar contra una idea de la
   realidad en vez de contra la realidad.

4. **Un sistema que arranca "bien" estando roto.** El `try/except` de
   `ensure_bucket` imprime un warning con `print` y sigue. En producción eso
   arranca verde y explota en la primera subida, lejísimos de su causa.

La corrección de método es una sola, y es la que ordenó esta sesión: **cuando
una pieza habla con el mundo, la verificación tiene que tocar el mundo — y
conviene que lo toque antes de escribir el arreglo, no después.**
