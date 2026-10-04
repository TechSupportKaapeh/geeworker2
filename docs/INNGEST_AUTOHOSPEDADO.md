# Inngest autohospedado en Railway — cómo se pasa, cómo se verifica, cómo se vuelve

> Decidido por el usuario el 2026-10-04 (`DECISIONS #80`), con una plantilla de Inngest ya
> desplegada en el **mismo proyecto de Railway** que Geocore y el worker. Reemplaza a Inngest Cloud.
> Cierra `PREGUNTAS_ABIERTAS` C-6.

## Estado (2026-10-04)

La plantilla está desplegada (con Postgres y Redis) en `https://inngestapp-production-29a7.up.railway.app`.
**Ese dominio público está abierto y sin login**, verificado ese día sólo con pedidos de lectura: `/` sirve
el dashboard y `/v0/gql` —el API del dashboard— contesta consultas sin credenciales. `/dev` pide
autenticación (la signing key está puesta). La lista de apps volvía **vacía**: el worker no se sincronizó
todavía, así que no había eventos expuestos. **Antes del paso 3 de abajo, ese dominio se saca.**

El nombre privado del servicio sale de Railway → el servicio → Settings → Networking → Private
Networking (si el servicio se llama `inngestapp`, es `inngestapp.railway.internal`), y el puerto es el
que escucha Inngest (8288 salvo que la plantilla lo cambie: es al que apunta el dominio público).

## Por qué

- **La espera entre steps es de Inngest Cloud**: de 38 a 75 s entre un step y el siguiente (`#55`),
  medido con steps vacíos. Un alta son ~27 steps, así que **un alta tarda más de 20 minutos esperando**,
  y una importación de 12 altas, eso por cada una con la concurrencia de 5. En el dev server la espera
  fue de 0,1 a 0,2 s.
- **La cuota**: el plan Hobby da 50.000 ejecuciones por mes y un alta gasta ~27. Autohospedado no hay
  cuota de ejecuciones; el límite pasa a ser GEE y el propio servidor.

## Cómo queda

```
Geocore ──(red privada)──▶ Inngest ──(URL pública del worker)──▶ worker /api/inngest
worker  ──(red privada)──▶ Inngest   (el sync y, si hiciera falta, emitir eventos)
```

- **Inngest → worker por la URL pública**, como hoy con Cloud. El worker escucha en `0.0.0.0` y la red
  privada de Railway puede ser sólo IPv6: así no se toca cómo arranca el worker.
- **Geocore y el worker → Inngest por la red privada** (`http://<servicio>.railway.internal:8288`).
- **El dashboard de Inngest no tiene login**: muestra cada evento con su payload —ids de tenant,
  geometrías— y deja re-ejecutar funciones, que gastan cuota de GEE. **Sin dominio público.** Si hace
  falta mirarlo, un túnel puntual o, si se decide, un proxy con contraseña delante (está abierto,
  ver abajo).
- **La firma sigue prendida**: el worker sigue en modo producción, y el servidor propio firma con la
  misma `INNGEST_SIGNING_KEY` que el worker verifica.

## Las variables

| Servicio | Variable | Valor |
|---|---|---|
| Inngest | `INNGEST_SIGNING_KEY` | una clave nueva, en hexadecimal: `openssl rand -hex 32` |
| Inngest | `INNGEST_EVENT_KEY` | una clave nueva: `openssl rand -hex 16` |
| Inngest | Postgres y Redis | los de la plantilla. **Sin persistencia, un reinicio pierde las corridas en curso** |
| worker | `INNGEST_SELF_HOSTED_URL` | `http://<servicio-inngest>.railway.internal:8288` |
| worker | `INNGEST_SIGNING_KEY` | **la misma** que la de Inngest |
| worker | `INNGEST_EVENT_KEY` | **la misma** que la de Inngest |
| Geocore | `Inngest__BaseUrl` | `http://<servicio-inngest>.railway.internal:8288` |
| Geocore | `Inngest__EventKey` | **la misma** `INNGEST_EVENT_KEY` |

En el worker siguen **prohibidas** `INNGEST_BASE_URL`, `INNGEST_API_BASE_URL`,
`INNGEST_EVENT_API_BASE_URL` e `INNGEST_DEV`: el SDK las lee solo, y `INNGEST_DEV` apagaría la firma.
`INNGEST_SELF_HOSTED_URL` es la única URL de Inngest que el worker acepta en producción, y no acepta
una de Cloud (`inn.gs`) ni `localhost`: si no sirve, el reporte de arranque lo dice y el worker sigue
con Cloud.

## El cambio, en orden

1. **Que no haya nada corriendo.** En el panel, Procesos sin jobs `pending` ni `running`. Lo que esté en
   vuelo en Cloud se pierde al cambiar, y su job queda colgado. **No hacerlo el día 5 mientras corre el
   cierre de mes.**
2. **Inngest**: las dos claves y la persistencia; **sacarle el dominio público** si la plantilla lo trae.
3. **El worker**: las tres variables, y desplegar. En los logs de arranque, `INNGEST_SELF_HOSTED_URL`
   tiene que aparecer con su valor y **sin** ninguna línea de problema de Inngest.
4. **El sync**: `curl -X PUT https://<worker-público>/api/inngest`. El SDK se registra contra el
   servidor propio con la URL por la que llegó el pedido, que es la pública. En el dashboard (por el
   túnel) tienen que aparecer **las 7 funciones** del worker, más las compañeras que Inngest crea
   para cada `on_failure` (con Cloud, 11 funciones se veían como 15).
5. **Geocore**: las dos variables, y desplegar. Desde acá, los eventos van al servidor propio.
6. **Verificar** (abajo). Si algo falla, volver (más abajo).

## Verificar

- **Un alta de punta a punta**: importar `4_caso4_solo_parcelas.kml` de
  `Downloads/archivos_sprint_K_zonas_nuevas` (8 altas) en un tenant de prueba, y ver los jobs pasar de
  `pending` a `completed` en Procesos. **Medir cuánto tarda un alta**: es el número que justifica el
  cambio.
- **La deduplicación por id** (`#30`/`#31`): republicar un job `pending` no tiene que disparar otra
  corrida. Se probó en el dev server y en Cloud, no en el servidor propio.
- **La cola de 5 de GEE** (`scope="account"`, `key="'gee'"`): con la importación de arriba, nunca más
  de 5 corriendo a la vez.
- **La firma**: un `POST /api/inngest` sin firma al worker sigue dando 401
  (`test_con_servidor_propio_una_invocacion_sin_firma_se_rechaza` lo fija en la suite).

## Volver a Cloud

1. Worker: **borrar** `INNGEST_SELF_HOSTED_URL` y volver a poner las claves de Cloud; desplegar.
2. Geocore: `Inngest__BaseUrl=https://inn.gs` y la event key de Cloud; desplegar.
3. Sincronizar la app desde el dashboard de Inngest Cloud.

Lo que haya quedado en vuelo en el servidor propio se pierde igual: volver también con Procesos vacío.

## Abierto

- **Cómo se mira el dashboard**: un túnel cuando haga falta (recomendado) o un proxy con contraseña.
  Mientras no se decida, sin dominio público.
- **Los backups de la base de Inngest**: con Postgres, el historial de corridas vive ahí. No es dato
  del negocio (eso está en Geocore y GeoData), así que perderlo no pierde nada que no se pueda reprocesar.
