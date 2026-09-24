# Sesión 13 — el sprint M.8, cerrado

> 2026-09-21 y 2026-09-24. Repo tocado: **Geocore**, tres PR (#48, #49, #50), los tres
> mergeados con el CI en verde. El worker, el tileserver y el panel no cambiaron.
>
> Quedaban tres tareas de M.8: rate limiting (A04), registro de auditoría (A09) y la
> retención de la bitácora. Entraron las tres. Geocore pasó de **452 a 559 tests**.

## Lo que se hizo

| | Qué | PR | Decisión |
|---|---|---|---|
| M.8.3 | Rate limiting por usuario, en escritura y admin | #48 | `#44` |
| M.8.4 | Registro de auditoría de acciones privilegiadas | #49 | `#45` |
| M.8.5 | Retención de `processing_job_events` | #50 | `#46` |

## 1. La misma forma para los dos controles: una regla, no una llamada por endpoint

Las dos tareas del medio tienen la misma pinta, y no por casualidad.

La forma obvia de agregar rate limiting en ASP.NET es `[EnableRateLimiting("x")]` en cada
acción. La forma obvia de auditar es llamar a `audit.Log(...)` en cada handler. Las dos
tienen el mismo defecto: **el endpoint que alguien agregue el mes que viene nace sin
control, y nadie se entera** — el rate limiting, hasta que alguien lo abuse; la auditoría,
hasta que un auditor pregunte.

Es exactamente el bug de M.7.1: poner `items` en los cinco desplegables que fallaban no
cerró nada, y ocho días después faltaba en seis.

Así que en los dos casos la decisión es una **función que mira el método y la ruta**:

```csharp
LimitesDePeticiones.NivelDe("POST", "/api/ranchos")            // → Encolado
AuditoriaMiddleware.EsAuditable("PATCH", "/api/users/{id}/role") // → true
```

Un `POST` nuevo queda limitado por existir. Y el test que lo cierra **no lee una lista
escrita a mano**: recorre los endpoints que la aplicación registra (`EndpointDataSource`) y
comprueba que ninguna ruta de escritura se escape. Eso cubre también los endpoints que
todavía no existen.

## 2. El lugar en el pipeline es parte del diseño, y los dos tienen su test

```
ForwardedHeaders → cabeceras → [AUDITORÍA] → Excepciones → CORS
   → Autenticación → [RATE LIMITING] → Autorización → Tenant → controllers
```

**El limitador va antes de `TenantMiddleware`** porque ese middleware **consulta la base en
cada request autenticado**: un pico de peticiones sería un pico de consultas aunque todas
terminaran en 400. Y va **después** de la autenticación porque el cupo es por usuario: una
oficina entera sale por la misma IP, y castigar a todos por uno es un bug de disponibilidad.
Como efecto, **el cupo se gasta aunque el pedido termine en 403** — si no contara, sondear
los endpoints de admin sería gratis.

**La auditoría va por fuera de `ExceptionMiddleware`**, y esto costó un test en rojo. Adentro,
una acción que **lanza** no dejaba fila: la excepción sube por encima del middleware y el
código de después de `next()` nunca corre. Y son justo los casos interesantes — el `409` del
guard del último TerraAdmin, un `500`. Desde afuera, al volver se lee el estado final.

Mover cualquiera de los dos pone tests en rojo: se comprobó moviéndolos.

## 3. Tres cosas que encontraron los tests, y no la revisión

Ninguna de las tres se ve leyendo el código.

- **`Encolado` (30) era más permisivo que `Admin` (20)**, lo cual contradecía la regla que el
  propio código declaraba: «cuando dos niveles aplican, gana el más estricto» —y `reprocesar`
  es los dos—. El test que afirmaba esa regla sobre los números salió en rojo. Se corrigieron
  los números, no el test: `Encolado` bajó a 10.
- **La auditoría adentro de `ExceptionMiddleware` perdía los 409 y los 500.** El test del
  cambio de rol falló con «índice fuera de rango» porque no había ninguna fila: los dobles de
  la fábrica devuelven `null` y el servicio lanza `NotFoundException`.
- **`config.GetValue<int?>` lanza** ante un valor que no puede convertir; no devuelve null.
  Como la retención se lee **al arrancar**, un `Retencion__DiasDeBitacora=muchos` habría
  tumbado la API entera, que es lo contrario de lo que el código decía hacer. Se parsea con
  `TryParse`. Lo mismo vale para `GetValue<bool>`.

## 4. Qué decide cada control

**Rate limiting** (`#44`), por usuario y por minuto:

| Nivel | Techo | Qué |
|---|---|---|
| `Escritura` | 120 | El resto de la escritura |
| `Admin` | 20 | `api/admin/*` —incluidos los GET, que salen a sondear servicios— y crear usuarios y tenants |
| `Encolado` | 10 | Lo que publica eventos |

La lista de `Encolado` salió de **seguir `PublishAsync` en el código**, no de la memoria. Dos
no se ven desde el controller: **cambiar una geometría encola el reproceso** (`#34`) y **un
KML crea N entidades, y publica N eventos, en un solo pedido**.

**Auditoría** (`#45`): toda la escritura de `api/users`, `api/tenants` y `api/admin`. Guarda
quién —con el correo y el rol **copiados en la fila**, para que se lea cuando el usuario ya no
esté—, qué (la **plantilla** de ruta, que agrupa, y la ruta concreta, que dice a quién), los
ids, el tenant, el resultado y la IP. **No guarda el cuerpo del pedido**: es entrada del
usuario, de forma y tamaño no acotados.

**Retención** (`#46`): 90 días de bitácora, un `DELETE` por día. Se borra **la bitácora, no los
jobs**: «¿esto se procesó?» no se vence.

## 5. Dos defaults que van al revés que el del cierre de mes

`CierreMensualService` arranca **apagado** (`#30`) porque prendido de más publica eventos que
nadie escucha. Los dos controles nuevos arrancan **prendidos**, y por el mismo tipo de
razonamiento al revés:

- el rate limiting apagado deja la API como estaba, o sea con el agujero;
- la retención apagada no hace nada — y **una retención que hay que acordarse de prender no
  es una retención**. Es la trampa de M.0.6, que sigue postergada justamente por eso.

Y la retención hoy no borra nada: la bitácora más vieja es de septiembre de 2026, así que el
primer borrado real llega dentro de tres meses.

## 6. Lo que M.8 no cubre, escrito en vez de tapado

- **El login y la edge function `create-user` no pasan por Geocore.** Son superficie de
  Supabase, y es donde vive la fuerza bruta de credenciales del hallazgo original. Lo que sí
  quedó limitado es `POST /api/users`, que provisiona un `authId` que **ya existe**.
- **Las lecturas fuera de `api/admin` no tienen techo.** Acotarlas pide medir primero qué hace
  el panel en una pantalla: el catálogo de Tiles hace 2 + N pedidos al elegir un tenant.
- **El estado del limitador vive en el proceso**: con más de una instancia de Geocore, el techo
  efectivo se multiplica por la cantidad de instancias. Hoy hay una.
- **El registro de auditoría se escribe después de la acción**, así que una fila se puede
  perder si la base está caída — queda un `LogError`. Y **no hay pantalla**: se consulta por
  SQL.
- **Los assets de un MosaicJSON** siguen sin pasar por la comparación de tenant (T-3, de M.8.1).

## 7. Lo que no se pudo verificar acá

- **Los tres tests del borrado real corren contra PostgreSQL** y están detrás de
  `GEOCORE_TEST_PG`, como los del cierre de mes. **No se corrieron**: Docker Desktop no estaba
  levantado. Lo que sí corre siempre es que el filtro se traduzca a SQL con el proveedor de
  Npgsql, parametrizado.
- **Una corrida de la solución entera dio un fallo que no se pudo reproducir** en las ocho
  corridas siguientes, y del que sólo quedó el conteo. No se sabe cuál fue. Lo que sí se supo
  es que tres aserciones de los tests de rate limiting afirmaban la **cuenta exacta** contra
  una ventana de reloj de pared que el test no controla, y eso es frágil por diseño: pasaron a
  ser un mínimo.

## Números

| Repo | Tests | CI |
|---|---|---|
| Geocore | **559** + 9 omitidos (eran 452 + 6 al abrir M.8) | verde |
| GeeWorker | 637, sin cambios | verde |
| terra-tileserver | 185, sin cambios | verde |
| terra-admin | 70, sin cambios | verde |

## 👥 Lo que queda para producción

**Aplicar la migración de `audit_log`**: `geocore/docs/sql/2026-09-21_RegistroDeAuditoria.sql`,
sobre la base de **identidad** (`ConnectionStrings:Default`), **no** sobre GeoData. Hasta
entonces cada acción privilegiada deja un `LogError` en Railway en vez de una fila, y la API
sigue funcionando: eso es a propósito.

Y sigue pendiente de M.8.1: **borrar las capas viejas** del bucket y sus filas
(`geocore/docs/sql/2026-09-20_capas_sin_tenant.sql`).
