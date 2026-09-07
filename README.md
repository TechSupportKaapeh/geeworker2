# API Terra-Satelite

Esta API proporciona endpoints para generar mapas de calor (heatmaps) a
partir de índices (NDVI, NDRE, LAI, soil_ph, etc.), series temporales de
valores por pasada de Sentinel-2 y operaciones de carga/registro de KML.

Resumen de funcionalidades

- Generación de mapas de calor (heatmap) para índices derivados de Sentinel-2.
- Series temporales por ROI (Time Series) usando pasadas individuales de Sentinel-2.
- Export de resultados: PNG, GeoTIFF, CSV.
- Persistencia: inserción de metadatos en la base de datos (assets, measurements).
- Soporte KML: subir KML, parseo y extracción de geometrías/áreas.
- Autenticación mínima basada en OAuth2 (Bearer/JWT) para endpoints que lo requieren.

Estructura principal del proyecto

- `app.py`: wiring mínimo de la aplicación (startup, CORS, registro de routers).
- `routes/`: routers FastAPI agrupados por funcionalidad (compute, kml, auth, measurements, time_series, assets, root).
- `services/ee`: inicialización y wrappers para Google Earth Engine y cálculos de índices.
- `utils_pkg/`: utilidades (visualización, ROI, IO y helpers).
- `models/` y `schemas/`: modelos Pydantic para requests/responses.

Rutas principales (endpoints)

- `GET /` (root)
	- Información básica de la API.

- `POST /compute` (Compute)
	- Descripción: Endpoint principal para generar heatmaps y series.
	- Payload (ejemplo resumido):
		```json
		{
			"mode": "heatmap" | "series" | "export",
			"index": "ndvi" | "ndre" | "lai" | ...,
			"start": "YYYY-MM-DD",
			"end": "YYYY-MM-DD",
			"geometry": { ... }  // geojson (opcional) o lon/lat + width_m/height_m
		}
		```
	- Comportamiento:
		- `mode=heatmap`: calcula un índice sobre la ventana y devuelve URL de tiles, estadísticas (min/max/mean/stddev) y archivos exportados si se pidió.
		- `mode=series`: obtiene serie temporal (valores medios por pasada). Devuelve lista de puntos con fecha y valor; opcionalmente exporta CSV y genera thumbnail.
		- `mode=export`: fuerza la exportación (PNG/GeoTIFF) según parámetros.

- `POST /time-series`
	- Devuelve solo la serie temporal (parecido a mode=series) útil para consultas rápidas.

- `POST /kml` (o ruta similar en `routes/kml`)
	- Sube/parsea un archivo KML, devuelve GeoJSON y métricas (área, bounds, features).

- `POST /login` y endpoints de `routes/auth`
	- Login y endpoints para autenticación/registro según la implementación en `routes/auth.py`.

- `routes/measurements`, `routes/assets`
	- Endpoints para consultar y listar mediciones y assets almacenados en la DB.

Formato de salida y redondeo

- Las series temporales devuelven objetos con campos: `date`, `value`.
- Los valores numéricos (series y estadísticas) están redondeados a 2 cifras significativas por defecto para facilitar la lectura.

Cómo empezar (desarrollo local)

Requisitos básicos

- Python 3.9+ (o la versión que uses en tu entorno virtual).
- Dependencias listadas en `requirements.txt` (instala en un virtualenv).

Pasos rápidos (PowerShell, desde la raíz del proyecto)

1) Crear y activar entorno virtual (recomendado):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2) Instalar dependencias:

```powershell
pip install -r requirements.txt
```

3) Definir variables de entorno requeridas (ejemplo `.env`):

- `EE_SERVICE_ACCOUNT_EMAIL`: correo del service account de Earth Engine
- `EE_SERVICE_ACCOUNT_KEY_JSON`: JSON del service account (como string)
- Otros (DB, `SECRET_KEY` para JWT, etc.) según `services/auth/auth_utils.py` y `config.py`.

Ejemplo `.env` mínimo (no compartas credenciales privadas):

```
EE_SERVICE_ACCOUNT_EMAIL=mi-service-account@proyecto.iam.gserviceaccount.com
EE_SERVICE_ACCOUNT_KEY_JSON={...}
```

4) Arrancar la API (modo desarrollo):

```powershell
uvicorn app:app --reload
```

5) Probar endpoints

- Petición ejemplo (usar Postman o curl) para series:

```json
POST /time-series
{
	"index": "ndvi",
	"start": "2024-01-01",
	"end": "2024-12-31",
	"geometry": { ... }
}
```

Debugging y recomendaciones

- Revisa la salida de la consola para logs y prints que ayudan a depurar (las rutas usan prints/logging en pasos clave).
- Antes de producción: restringir CORS, rotar/gestionar credenciales de EE, asegurar secretos y revisar políticas de almacenamiento.

¿Quieres que añada ejemplos cURL completos para `/compute` (heatmap y series) y un ejemplo de `.env` con variables adicionales (DB, SECRET_KEY)?

