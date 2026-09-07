# Sesión 2026-08-27 — El spike de MosaicJSON (FASE B)

> Qué pasó y por qué. Estado permanente en [`HANDOFF.md`](HANDOFF.md).
> Lo que quedó sin decidir: [`PREGUNTAS_ABIERTAS.md`](PREGUNTAS_ABIERTAS.md).

## Dónde quedó

**FASE B cerrada: `DECISIONS #19` y `#20` se sostienen.** `rio-tiler` compone
varias pasadas por mediana y respeta el nodata. La apuesta de guardar por pasada
y componer al vuelo es viable.

`terra-tileserver` en `3ea77f6`, 115 tests. El worker sigue sin commitear.

---

## 1. El spike, y por qué los datos son así

La pregunta era falsable o no servía. Se generaron tres pasadas sobre **la misma
grilla** —mismo origen, mismo tamaño de píxel, mismo CRS, como exige
`DECISIONS #19`— con valores constantes por fecha y agujeros complementarios,
de modo que la mediana esperada se calcula a mano:

| Cuadrante | 10-jun | 20-jun | 30-jun | Esperado | Obtuvo |
|---|---|---|---|---|---|
| NO | 0.2 | 0.5 | 0.8 | 0.500 | **0.500** |
| **NE** | — | — | 0.8 | **0.800** | **0.800** |
| SO | 0.2 | — | 0.8 | 0.500 | **0.500** |
| **SE** | — | — | — | **NODATA** | **NODATA** |

El **NE** es literalmente B.5 del plan: una zona nublada en dos pasadas y
despejada en una tiene que salir con el dato, no con un agujero.

El **SE** es lo que hace que la prueba valga algo. Sin un caso que *deba* salir
vacío, "todo tiene dato" no prueba que el nodata se respete: prueba que algo
rellenó todo.

Quedó como `scripts/check_mosaic_median.py` y no como test, porque tarda y porque
la respuesta depende de `rio-tiler` y `cogeo-mosaic`, que entran sin pinnear
(`PREGUNTAS_ABIERTAS` C-2).

### El spike falló dos veces antes de pasar, y las dos fue culpa de la prueba

Vale registrarlo porque el modo de fallo es el peligroso: **la prueba decía "la
arquitectura no funciona" cuando la que no funcionaba era ella.**

1. **`MosaicBackend` despacha por `urlparse`.** En Windows una ruta absoluta como
   `C:\...` se lee con esquema `"c"` y revienta. Una ruta relativa cae al
   `FileBackend`, que es lo que se quería. (`file:///C:/...` tampoco sirve: deja
   el path como `/C:/...`.)
2. **Se muestreaban los cuadrantes por fracción de la imagen del tile.** Pero el
   área ocupa solo una parte del tile, y a z=13 ni siquiera entra entera —un
   tile mide 0.044° de longitud y el área 0.102°—, así que los puntos caían
   fuera del dato y **todo salía NODATA**. Se corrigió ubicando los cuadrantes
   por coordenadas y eligiendo el zoom en función del ancho del área.

Ahora, si un punto de muestreo cae fuera del tile, el script **lo dice** en vez
de muestrear otra cosa en silencio.

---

## 2. Dos hallazgos que valen más que el "sí funciona"

### boto3 no comparte ninguna variable con GDAL

`cogeo_mosaic.backends.s3.S3Backend` crea su cliente con
`boto3_session().client("s3")`, **sin `endpoint_url`**. Toda la configuración
que el tileserver ya hacía para GDAL —`AWS_S3_ENDPOINT`, `AWS_S3_ADDRESSING_STYLE`,
`AWS_VIRTUAL_HOSTING`— **es invisible para boto3**.

Consecuencia: un `?url=s3://terra-assets/mosaico.json` habría ido a **AWS de
verdad** en vez de a MinIO. Y antes de eso habría reventado con un
`AssertionError`, porque boto3 ni siquiera estaba instalado.

Se agregó la dependencia y `configure_gdal()` ahora fija también
`AWS_ENDPOINT_URL_S3`, desde la misma `Settings`, para que las dos vías de acceso
al bucket no puedan divergir. Con test, incluido el detalle de que los formatos
son distintos: GDAL lo quiere pelado (`host:9000`), boto3 con esquema
(`http://host:9000`).

Esto convierte "dónde vive el MosaicJSON" de duda abierta en una decisión con
opciones concretas y costos conocidos (`PREGUNTAS_ABIERTAS` A-1).

### Los assets del MosaicJSON no pasan por el filtro anti-SSRF

El `path_dependency` valida la URL del MosaicJSON, pero `cogeo-mosaic` abre **los
assets que ese documento lista adentro** tal como vengan, incluidos `http://`
hacia la red interna. Es la misma clase de agujero (OWASP A10) que se cerró para
`?url=`, una capa más adentro.

Hoy está contenido porque solo `worker-rw` escribe en el bucket: un MosaicJSON
solo puede aparecer ahí si lo puso el worker. Queda documentado en `main.py` con
esa condición explícita, y registrado en `PREGUNTAS_ABIERTAS` C-1 porque **la
decisión de A-1 lo cambia**: si el mosaico pasa a servirlo Geocore por HTTP, el
contenido deja de estar respaldado por los permisos del bucket.

---

## 3. Lo que se montó

`MosaicTilerFactory` en `/mosaic` — 24 rutas, con los mismos dos controles de
acceso que `/cog`. Las instancias del validador de token y del validador de ruta
se construyen **una sola vez y se comparten** entre los dos routers, para que no
puedan divergir. Verificado: `/mosaic/info` sin token da 401.

---

## 4. Documentación y proceso

- **`PREGUNTAS_ABIERTAS.md`** *(nuevo)* — las 16 decisiones sin tomar, cada una
  como tarea ejecutable por el pipeline, agrupadas por **cuándo** hay que
  decidirlas y no por tema. Sale de la tabla suelta que vivía al final del
  `PLAN.md`, más lo que apareció esta semana.
- **`WORKFLOW.md`** — su scope decía *"solo Geocore/ y terra-admin/"* y la etapa
  VERIFY solo conocía `dotnet` y `npx tsc`. Los dos repos de Python no estaban
  cubiertos, así que el pipeline no se podía seguir aunque se quisiera. Se
  extendió con los cuatro repos, sus comandos de verificación, y las
  verificaciones ejecutables que se fueron construyendo (`check_prod.py`,
  `/health/ready`, `check_mosaic_median.py`, `check_schema.py`).

Se agregó también un guardrail que esta semana se ganó a la mala: **ante la duda,
probar contra el sistema real antes que revisar con más cuidado.** Los dos bugs
más caros de estos días sobrevivieron a tests que pasaban, porque los tests
usaban excepciones fabricadas a mano.

---

## 5. Lo que B no cierra

- **El límite de rendimiento sigue sin medirse** (`PREGUNTAS_ABIERTAS` A-2). El
  spike usó 3 pasadas; un composite anual son ~73 COG por tile.
- **Quién arma el MosaicJSON** sigue sin decidirse (A-1), aunque ahora con
  opciones y costos concretos.
- **El worker nunca escribió en MinIO real** (A-3). La cadena de lectura está
  verificada; la de escritura no, y esa tarea no pertenece a ninguna fase del
  plan, que es justamente por qué nadie la levanta.
