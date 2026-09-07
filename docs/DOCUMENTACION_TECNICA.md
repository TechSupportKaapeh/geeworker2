# Documentación Técnica: GeeWorker

GeeWorker es un microservicio basado en **FastAPI** diseñado para el procesamiento, análisis y visualización de datos geoespaciales y satelitales (principalmente del satélite **Sentinel-2 L2A SR**) dentro del ecosistema **Terra**. Actúa como puente entre la plataforma de Terra y **Google Earth Engine (GEE)** para computar índices espectrales, generar mapas de calor, extraer series temporales y gestionar geometrías agrícolas.

---

## 1. Arquitectura del Servicio

GeeWorker utiliza una arquitectura de microservicio liviana basada en Python y FastAPI. Sus principales componentes y flujos de datos son:

```mermaid
graph TD
    User([Cliente / Frontend]) -->|Requests HTTP| FastAPI[FastAPI App / Routes]
    FastAPI -->|Consulta / Inicialización| GEE[Google Earth Engine API]
    FastAPI -->|Metadatos / Medidas| DB[(SQLite: terra.db)]
    FastAPI -->|Operaciones Locales| Disk[(Filesystem: outputs/)]
    
    subgraph Almacenamiento Local (outputs/)
        Disk -->|Subidas KML| KML[kml_uploads/]
        Disk -->|Caché de Map IDs| Cache[cache/]
        Disk -->|GeoTIFF/PNG/CSV| Downloads[Descargas/Exports]
        Disk -->|Errores| Logs[compute_errors/]
    end
```

### Componentes Clave
*   **API Framework**: FastAPI con Uvicorn para el servidor de desarrollo/producción.
*   **Motor de Cómputo Espacial**: Google Earth Engine Python SDK (`ee`). Requiere una cuenta de servicio vinculada y credenciales en formato JSON.
*   **Persistencia Local**: Base de datos SQLite (`terra.db`) que almacena metadatos de los productos generados, registros históricos de mediciones y fechas de imágenes disponibles.
*   **Sistema de Caché**: Caché basado en archivos JSON (`outputs/cache/`) indexados con hashes SHA-1 para almacenar temporalmente los Map IDs de GEE y evitar llamadas redundantes de renderizado de teselas.
*   **Procesamiento de Geometrías**: Uso de **Shapely** para el parseo, corrección de geometrías inválidas y cálculo aproximado de áreas a partir de archivos KML.

---

## 2. Base de Datos (Esquema SQLite)

El servicio inicializa automáticamente una base de datos local en `outputs/terra.db` al arrancar. Contiene tres tablas principales:

### Tabla `assets`
Almacena metadatos de los archivos exportados (GeoTIFF, PNG, CSV) y los conjuntos de teselas mapeados.
*   `asset_id` (TEXT, PK): Identificador único del recurso (ej: `ndvi_2024-01-01_2024-12-31_1715764800.tif`).
*   `product` (TEXT): Tipo de índice calculado (ej: `ndvi`, `ndre`).
*   `sensor` (TEXT): Nombre del sensor satelital (normalmente `sentinel-2`).
*   `url_s3` (TEXT): Ruta local del archivo exportado o URL del servidor de teselas de GEE.
*   `epsg` (INTEGER): Código de proyección espacial (habitualmente `4326` o `3857`).
*   `resolution_m` (REAL): Resolución espacial en metros (ej: `10.0` para Sentinel-2).
*   `acquired_ts` (TEXT): Marca de tiempo de adquisición de la imagen.
*   `ingested_ts` (TEXT): Fecha y hora de procesamiento/ingesta en formato ISO.
*   `footprint` (TEXT): Geometría GeoJSON exacta del recurso.
*   `bbox` (TEXT): Bounding Box en formato JSON `[west, south, east, north]`.
*   `min_val`, `max_val`, `mean_val`, `stddev_val` (REAL): Estadísticas descriptivas del índice sobre el área.
*   `cog_ok` (INTEGER): Flag (0 o 1) para indicar si el TIFF es un Cloud Optimized GeoTIFF (COG).
*   `tenant_id`, `plot_id` (TEXT): Referencias al inquilino y a la parcela asociada.

### Tabla `measurements`
Registra mediciones numéricas promediadas o agregadas por parcela y fecha.
*   `id` (INTEGER, PK, Auto): Clave primaria autoincremental.
*   `metric_id` (TEXT): Identificador externo o UUID de la medición.
*   `tenant_id`, `plot_id` (TEXT): Referencias a la organización y parcela.
*   `ts` (TEXT): Fecha asociada a la medición (formato `YYYY-MM-DD`).
*   `metric_type` (TEXT): Tipo de índice calculado (ej: `ndvi`, `savi`).
*   `value` (REAL): Valor medio obtenido en la parcela.
*   `quality` (TEXT): Indicador de calidad o metadatos de validez (opcional).

### Tabla `sentinel2_dates`
Actúa como caché local de disponibilidad de imágenes para evitar consultas recurrentes de fechas a GEE.
*   `id` (INTEGER, PK, Auto): Clave primaria autoincremental.
*   `geometry_id` (TEXT, UNIQUE): Hash SHA-256 corto (16 caracteres) derivado de la geometría consultada.
*   `user_id` (TEXT): ID del usuario que consulta (actualmente `NULL`).
*   `date` (TEXT, UNIQUE): Fecha de la imagen satelital (`YYYY-MM-DD`).
*   `system_time_start` (INTEGER, UNIQUE): Timestamp en milisegundos de GEE.
*   `cloud_cover` (REAL): Porcentaje de cobertura de nubes reportado por el satélite.
*   `tile_id` (TEXT): Identificador de tesela MGRS (ej: `30TXT`).
*   `roi_geojson` (TEXT): Geometría GeoJSON del área de interés en formato stringificado.
*   `created_at` (TEXT): Marca de tiempo de la inserción en BD (`CURRENT_TIMESTAMP`).

---

## 3. Índices Espectrales y Visualización

GeeWorker calcula dinámicamente varios índices a partir de las bandas reflectivas de Sentinel-2 L2A. A continuación se detallan las bandas de origen, fórmulas aplicadas y parámetros de visualización:

| Índice | Nombre Completo | Banda GEE | Fórmula Matemática / Definición | Rango Vis. | Paleta de Colores |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`rgb`** | Color Verdadero | `B4`, `B3`, `B2` | Combinación estándar Roja-Verde-Azul | `[0, 3000]` | Color Real |
| **`ndvi`** | Índice de Vegetación de Diferencia Normalizada | `ndvi` | `(B8 - B4) / (B8 + B4)` | `[-0.2, 0.8]` (Discreto) | Marrón arcilla → Amarillo → Verde bosque |
| **`ndwi`** | Índice de Agua de Diferencia Normalizada | `ndwi` | `(B3 - B8) / (B3 + B8)` | `[-0.5, 0.6]` (Discreto) | Tonos tierra → Amarillo → Azul intenso |
| **`ndmi`** | Índice de Humedad de Diferencia Normalizada | `ndmi` | `(B8 - B11) / (B8 + B11)` (Clampeado a `[-0.6, 0.6]`) | `[-0.6, 0.6]` (Discreto) | Azul marino → Celeste → Naranja → Rojo |
| **`ndre`** | Índice del Borde Rojo de Diferencia Normalizada | `ndre` | `(B8 - B5) / (B8 + B5)` (Clampeado a `[-0.5, 0.6]`) | `[-1.0, 1.0]` (Discreto) | Marrón tierra → Crema → Verde azulado |
| **`evi`** | Índice de Vegetación Mejorado | `evi` | `2.5 * ((B8 - B4) / (B8 + 6*B4 - 7.5*B2 + 1))` | `[-0.2, 0.6]` (Continuo) | Rojo oscuro → Naranja → Amarillo → Verde |
| **`savi`** | Índice de Vegetación Ajustado al Suelo | `savi` | `1.5 * ((B8 - B4) / (B8 + B4 + 0.5))` | `[-0.2, 0.8]` (Continuo) | Marrón arcilla → Amarillo → Verde bosque |
| **`lai`** | Índice de Área Foliar | `lai` | `(NDVI * 3.618) - 0.118` (Clampeado a `max(0)`) | `[0, 8]` (Discreto) | Amarillo claro → Verde claro → Verde oscuro |
| **`soil_ph`**| Proxy de pH de Suelo | `soil_ph` | Ratio SWIR/NIR: `B11 / B8` | `[0, 2]` (Discreto) | Azul → Celeste → Amarillo → Naranja → Rojo |

> [!WARNING]
> **Deficiencias de Implementación Actuales (Crucial para el Planning):**
> 1. **Falso Fallback de Índices**: Los índices `gci`, `vegetation_health`, `water_detection`, `urban_index`, `soil_moisture` y `change_detection` están definidos en los validadores de rutas de la API, pero **no tienen fórmula propia en la lógica de procesamiento**. Caes en un fallback donde el servicio computa **NDVI** en su lugar, lo renombra con el nombre del índice solicitado y le aplica la paleta visual configurada en `visualization.py`.
> 2. **Simplificación en Series Temporales**: En la función de extracción rápida de series temporales (`get_sentinel2_time_series`), las fórmulas de `evi` y `savi` están simplificadas incorrectamente como meras multiplicaciones lineales de NDVI (`NDVI * 2.5` y `NDVI * 1.5`), omitiendo sus ecuaciones reales para priorizar velocidad.

---

## 4. Detalle de Endpoints (API Reference)

### 4.1. Endpoints de Utilidad General

#### `GET /`
Retorna información general del servicio.
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "message": "Terra API - Google Earth Engine con Sentinel-2 (Heatmaps) y Alpha Earth (Series/Export)",
      "status": "active",
      "docs": "/docs"
    }
    ```

#### `GET /health`
Valida la conectividad del backend con Google Earth Engine consultando un objeto de fecha virtual.
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "status": "ok"
    }
    ```
*   **Ejemplo de Error (200 OK / 500 Internal Error)**:
    ```json
    {
      "status": "error",
      "detail": "Detailed Earth Engine error description here..."
    }
    ```

#### `GET /auth/info`
Informa el estado de la autenticación. Indica que la autenticación está desactivada en el backend por diseño ya que el frontend se encarga de esa validación.
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "authentication": "disabled",
      "message": "Backend no requiere autenticación. El frontend maneja la lógica de autenticación.",
      "public_endpoints": [
        "/",
        "/compute",
        "/dates",
        "/time-series",
        "/stats/kml",
        "/measurements",
        "/assets",
        "/kml"
      ]
    }
    ```

---

### 4.2. Procesamiento de Geometrías y KML

#### `POST /upload-kml`
Permite subir un archivo `.kml` de texto, extraer sus coordenadas cartográficas usando Shapely, calcular el área y envolvente espacial (bounds) y guardar la geometría resultante en formato GeoJSON en el disco (`outputs/kml_uploads/{kml_id}.geojson`).
*   **Parámetros**:
    *   `file` (UploadFile, requerido): Archivo `.kml`.
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "success": true,
      "message": "KML procesado correctamente. 1 polígono(s) encontrado(s).",
      "geometry": {
        "type": "Polygon",
        "coordinates": [
          [[-101.52, 21.12], [-101.52, 21.13], [-101.51, 21.13], [-101.51, 21.12], [-101.52, 21.12]]
        ]
      },
      "features_count": 1,
      "area_hectares": 120.45,
      "bounds": {
        "west": -101.52,
        "south": 21.12,
        "east": -101.51,
        "north": 21.13
      },
      "kml_id": "8b9f1d04-4b53-4da1-85b4-d53fdebc62e1"
    }
    ```
*   **Códigos de Error**:
    *   `400 Bad Request`: Si el archivo no termina en `.kml`, si no contiene coordenadas válidas o si la decodificación Unicode falla.

---

### 4.3. Disponibilidad de Fechas Sentinel-2

#### `POST /dates`
Consulta a Google Earth Engine para listar todas las fechas de imágenes Sentinel-2 disponibles dentro de un rango de tiempo y una región de interés (ROI), filtrando por porcentaje máximo de nubes. Registra automáticamente cada fecha descubierta en la tabla `sentinel2_dates` de la base de datos SQLite.
*   **Cuerpo de la Petición (JSON)**:
    ```json
    {
      "kml_id": "8b9f1d04-4b53-4da1-85b4-d53fdebc62e1", // Opcional: ID de KML previamente subido
      "geometry": null,                                  // Opcional: GeoJSON Geometry
      "lon": null,                                       // Opcional: Longitud central para bbox
      "lat": null,                                       // Opcional: Latitud central para bbox
      "width_m": 1000,                                   // Opcional: Ancho en metros para bbox
      "height_m": 1000,                                  // Opcional: Alto en metros para bbox
      "start": "2026-01-01",                             // Requerido: Fecha inicial (YYYY-MM-DD)
      "end": "2026-06-01",                               // Requerido: Fecha final (YYYY-MM-DD)
      "cloud_pct": 20                                    // Opcional: Filtro de nubes (0-100), default 100
    }
    ```
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "success": true,
      "message": "Se encontraron 3 imágenes Sentinel-2 disponibles",
      "roi": {
        "type": "Polygon",
        "coordinates": [...]
      },
      "start": "2026-01-01",
      "end": "2026-06-01",
      "total_images": 3,
      "dates": [
        {
          "date": "2026-03-15",
          "system_time_start": 1773600000000,
          "cloud_cover": 4.52,
          "tile_id": "14QPM"
        },
        {
          "date": "2026-04-04",
          "system_time_start": 1775328000000,
          "cloud_cover": 12.1,
          "tile_id": "14QPM"
        }
      ]
    }
    ```

#### `GET /dates`
Lista las fechas de Sentinel-2 previamente almacenadas/cacheadas en la base de datos local SQLite.
*   **Parámetros de Consulta (Query Parameters)**:
    *   `geometry_id` (TEXT, opcional): Hash corto de la geometría para filtrar.
    *   `start_date` (TEXT, opcional): Fecha mínima (`YYYY-MM-DD`).
    *   `end_date` (TEXT, opcional): Fecha máxima (`YYYY-MM-DD`).
    *   `limit` (INTEGER, opcional, default 500): Máximo de filas devueltas.
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "success": true,
      "total": 1,
      "dates": [
        {
          "id": 4,
          "geometry_id": "a9f8b7c6d5e4f3a2",
          "user_id": null,
          "date": "2026-03-15",
          "system_time_start": 1773600000000,
          "cloud_cover": 4.52,
          "tile_id": "14QPM",
          "roi_geojson": { ... },
          "created_at": "2026-06-18 01:00:00"
        }
      ]
    }
    ```

---

### 4.4. Procesamiento de Índices y Computación (`/compute`)

#### `POST /compute`
Es el endpoint nuclear del servicio. Soporta múltiples modos de procesamiento a través de un cuerpo de petición configurable y persiste metadatos automáticamente.

*   **Cuerpo de la Petición (JSON)**:
    ```json
    {
      "geometry": null,           // Opcional: Geometría GeoJSON
      "kml": null,                // Opcional: Contenido XML del KML como string
      "kml_id": "kml-uuid-here",  // Opcional: Referencia a KML en outputs/kml_uploads/
      "lon": -101.52,             // Opcional: Longitud para bbox
      "lat": 21.12,               // Opcional: Latitud para bbox
      "width_m": 500,             // Opcional: Ancho en metros
      "height_m": 500,            // Opcional: Alto en metros
      "start": "2026-03-01",      // Requerido: Fecha inicial
      "end": "2026-03-20",        // Requerido: Fecha final
      "mode": "heatmap",          // Requerido: "heatmap" | "series"
      "index": "ndvi",            // Requerido: Índice espectral (ndvi, ndre, rgb, lai, etc.)
      "cloud_pct": 30,            // Opcional: % de cobertura de nubes aceptable
      "export_format": "geotiff", // Opcional: "png" | "geotiff" | "csv" (fuerza descarga física)
      "split_kml": false          // Opcional: Si true, divide y procesa feature por feature
    }
    ```

*   **Comportamientos y Formatos de Respuesta**:

    *   **Caso A: `mode="heatmap"` (Generación de Mapa de Calor)**:
        Crea un composite promedio de GEE sobre el periodo especificado, remuestrea la resolución a 10m mediante interpolación bicúbica, colorea la imagen en servidor según la paleta del índice, recorta a la ROI exacta y devuelve la URL para renderizar teselas en un mapa web.
        *   **Ejemplo de Respuesta (200 OK)**:
            ```json
            {
              "mode": "heatmap",
              "index": "ndvi",
              "roi": { "type": "Polygon", "coordinates": [...] },
              "roi_bounds": [-101.521, 21.119, -101.519, 21.121],
              "tileUrlTemplate": "https://earthengine.googleapis.com/v1/projects/earthengine-legacy/maps/db23984fa.../tiles/{z}/{x}/{y}",
              "vis": {
                "baked": true,
                "palette": ["#8c2d04", "#d95f0e", "#feb24c", "#ffffbf", "#a1d99b", "#31a354", "#006837"],
                "min": -0.2,
                "max": 0.8
              },
              "min_val": 0.12,
              "max_val": 0.76,
              "mean_val": 0.54,
              "stddev_val": 0.15,
              "stats_file": "outputs/compute_stats_ndvi_..."
            }
            ```

    *   **Caso B: `mode="heatmap"` con `export_format="geotiff"` o `"png"`**:
        En lugar de retornar URLs de teselas de mapa interactivo, descarga la imagen original georreferenciada (.tif) o la imagen de previsualización renderizada (.png) al disco del servidor, inserta los metadatos en la tabla `assets` y retorna la ruta local junto a las estadísticas calculadas.
        *   **Ejemplo de Respuesta (200 OK)**:
            ```json
            {
              "mode": "heatmap",
              "index": "ndvi",
              "roi": { ... },
              "roi_bounds": [...],
              "saved_files": {
                "geotiff": "outputs/ndvi_2026-03-01_2026-03-20_1715764800.tif"
              },
              "min_val": 0.12,
              "max_val": 0.76,
              "mean_val": 0.54,
              "stddev_val": 0.15,
              "stats_file": "outputs/compute_stats_ndvi_..."
            }
            ```

    *   **Caso C: `mode="series"` (Generación de Serie Temporal)**:
        Extrae los valores promedio históricos del índice para cada imagen satelital individual sin nubes capturada en el periodo seleccionado (limitado a 30 puntos por desempeño). Registra cada punto de la serie en la tabla `measurements` del SQLite local y almacena los metadatos generales en `assets`.
        Si `export_format="csv"`, escribe un archivo `.csv` en la carpeta `outputs/` y opcionalmente descarga un thumbnail PNG.
        *   **Ejemplo de Respuesta (200 OK)**:
          ```json
          {
            "mode": "series",
            "index": "ndvi",
            "roi": { "type": "Polygon", "coordinates": [...] },
            "roi_bounds": [-101.521, 21.119, -101.519, 21.121],
            "series": [
              { "date": "2026-03-05", "value": 0.42 },
              { "date": "2026-03-10", "value": 0.45 },
              { "date": "2026-03-15", "value": 0.51 }
            ],
            "saved_files": {
              "csv": "outputs/ndvi_2026-03-01_2026-03-20_1715764800.csv"
            }
          }
          ```

    *   **Caso D: `split_kml=true` (Procesamiento por Features)**:
        Cuando la entrada es una FeatureCollection (subida mediante KML, KML ID o GeoJSON), computa una única imagen de composite maestro para toda la envolvente geográfica a fin de ahorrar cuota y optimizar llamadas a GEE, luego recorta la visualización individualmente para cada polígono/parcela y genera sus correspondientes URLs de teselas.
        *   **Ejemplo de Respuesta (200 OK)**:
            ```json
            {
              "mode": "heatmap",
              "index": "ndvi",
              "features": [
                {
                  "feature_id": "feature_1",
                  "feature_name": "Lote Norte",
                  "area_m2": 15200.4,
                  "tileUrlTemplate": "https://earthengine.googleapis.com/v1/.../tiles/{z}/{x}/{y}"
                },
                {
                  "feature_id": "feature_2",
                  "feature_name": "Lote Sur",
                  "area_m2": 24800.1,
                  "tileUrlTemplate": "https://earthengine.googleapis.com/v1/.../tiles/{z}/{x}/{y}"
                }
              ],
              "master_tile": "https://earthengine.googleapis.com/v1/projects/.../tiles/{z}/{x}/{y}"
            }
            ```

*   **Códigos de Error**:
    *   `400 Bad Request`: Si no se proporciona geometría o parámetros espaciales, o si el modo no es válido.
    *   `404 Not Found`: Si no existen imágenes Sentinel-2 aptas libres de nubes en las fechas dadas.
    *   `500 Internal Server Error`: Errores al interaccionar con la API de GEE o al guardar archivos locales (se crea un log completo en `outputs/compute_errors/compute_error_TIMESTAMP.log`).

---

### 4.5. Otros Endpoints del Sistema

#### `POST /time-series`
Endpoint simplificado de consulta rápida. Extrae la serie temporal de un índice devolviendo los valores promedios por fecha de pasada, estadísticas del periodo completo (promedio de periodo, mínimos, máximos, procedencia del sensor) pero **sin** escribir registros a la base de datos local SQLite ni exportar archivos al disco de forma directa.
*   **Cuerpo de la Petición (JSON)**:
    ```json
    {
      "geometry": null,
      "lon": -101.52,
      "lat": 21.12,
      "width_m": 500,
      "height_m": 500,
      "start": "2026-01-01",
      "end": "2026-06-01",
      "index": "ndvi",
      "cloud_pct": 70,      // Opcional, por defecto 80 (más permisivo para series)
      "fast_mode": true
    }
    ```
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "analysis_type": "ndvi",
      "roi": { "type": "Polygon", "coordinates": [...] },
      "date_range": { "start": "2026-01-01", "end": "2026-06-01" },
      "time_series": [
        {
          "date": "2026-03-15",
          "datetime": "2026-03-15 12:00:00",
          "timestamp": 1773600000000,
          "mean": 0.45
        }
      ],
      "summary": {
        "total_points": 1,
        "valid_points": 1,
        "period_mean": 0.45,
        "period_min": 0.45,
        "period_max": 0.45,
        "total_images_used": 1,
        "data_source": "Sentinel-2 SR Harmonized (Individual Passes)",
        "cloud_threshold": "< 70%"
      }
    }
    ```

#### `POST /heatmap`
Endpoint dedicado exclusivo para generar un mapa de calor y centrar la visualización. Acepta un `days_buffer` que define la ventana de tiempo alrededor de la fecha (`date`) para crear el composite promedio.
*   Si `days_buffer = 0` (un solo día solicitado), aplica un buffer interno automático de ±3 días para consolidar una imagen sin huecos nubosos, y además **computa y retorna una serie temporal adicional de 10 días** alrededor de la fecha objetivo.
*   **Cuerpo de la Petición (JSON)**:
    ```json
    {
      "kml_id": "8b9f1d04-4b53-4da1-85b4-d53fdebc62e1",
      "geometry": null,
      "lon": null,
      "lat": null,
      "date": "2026-03-15",
      "index": "ndvi",
      "cloud_pct": 30,
      "days_buffer": 0
    }
    ```
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "success": true,
      "message": "Heatmap generado para 2026-03-15 (ndvi)",
      "date": "2026-03-15",
      "index": "ndvi",
      "roi": { "type": "Polygon", "coordinates": [...] },
      "tile_url": "https://earthengine.googleapis.com/v1/projects/earthengine-legacy/maps/...",
      "map_id": "db23984fa...",
      "bounds": {
        "west": -101.52,
        "south": 21.12,
        "east": -101.51,
        "north": 21.13,
        "center": { "lon": -101.515, "lat": 21.125 }
      },
      "stats": {
        "min": 0.12,
        "max": 0.76,
        "mean": 0.54,
        "stdDev": 0.15
      },
      "time_series": [
        // Retornado solo si days_buffer = 0 (serie de 10 días aproximada)
        { "date": "2026-03-10", "datetime": "2026-03-10 12:00:00", "timestamp": 1773168000000, "mean": 0.52 },
        { "date": "2026-03-15", "datetime": "2026-03-15 12:00:00", "timestamp": 1773600000000, "mean": 0.54 }
      ]
    }
    ```

#### `POST /stats/kml`
Genera un informe consolidado descriptivo (mínimos, máximos, promedios y desviaciones estándar) de **los 14 índices espectrales** para cada feature geométrica que exista dentro del KML provisto.
Escribe un archivo estructurado `.txt` localmente y lo devuelve en la misma llamada como una descarga de archivo (`FileResponse`).
*   **Cuerpo de la Petición**: Igual a `ComputeRequest` (usando `kml_id`, `geometry` o `kml`).
*   **Respuesta**: Descarga de archivo de texto plano (`text/plain`).
    *   *Formato interno del archivo generado*:
        ```text
        Estadísticas descriptivas por feature - 20260618T010000Z
        Periodo: 2026-03-01 -> 2026-03-20

        Feature: lote_1 - Lote Principal - area_m2: 124500.0
          ndvi: mean=0.621, min=0.150, max=0.820, stddev=0.112
          ndwi: mean=-0.412, min=-0.500, max=-0.210, stddev=0.084
          ... (sucesivo para los 14 índices)
        ```

---

### 4.6. Gestión de Activos y Mediciones de la Base de Datos

#### `GET /assets`
Retorna una lista filtrada de los activos persistidos en la base de datos SQLite.
*   **Parámetros de Consulta (Query Parameters)**:
    *   `tenant_id` (TEXT, opcional): Filtrar por organización.
    *   `plot_id` (TEXT, opcional): Filtrar por ID de parcela o KML.
    *   `limit` (INTEGER, opcional, default 100): Límite de resultados.
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "count": 1,
      "assets": [
        {
          "asset_id": "ndvi_2026-03-01_2026-03-20_1715764800.tif",
          "product": "ndvi",
          "sensor": "sentinel-2",
          "url_s3": "outputs/ndvi_2026-03-01_2026-03-20_1715764800.tif",
          "epsg": 4326,
          "resolution_m": 10.0,
          "acquired_ts": null,
          "ingested_ts": "2026-06-18T01:00:00Z",
          "footprint": { ... },
          "bbox": [-101.52, 21.12, -101.51, 21.13],
          "min_val": 0.12,
          "max_val": 0.76,
          "mean_val": 0.54,
          "stddev_val": 0.15,
          "cog_ok": true,
          "tenant_id": null,
          "plot_id": "8b9f1d04-4b53-4da1-85b4-d53fdebc62e1"
        }
      ]
    }
    ```

#### `GET /assets/{asset_id}`
Obtiene el metadato detallado de un activo en base a su ID único.
*   **Ejemplo de Respuesta (200 OK)**: Retorna el objeto dict del activo directamente (similar a un elemento de la lista superior). Lanza `404 Not Found` si el ID de recurso no existe.

#### `GET /measurements`
Recupera los registros históricos de mediciones de índices promediados.
*   **Parámetros de Consulta (Query Parameters)**:
    *   `plot_id` (TEXT, opcional): Filtrar por parcela/KML.
    *   `metric_type` (TEXT, opcional): Filtrar por tipo de índice espectral.
    *   `limit` (INTEGER, opcional, default 500): Límite de resultados.
*   **Ejemplo de Respuesta (200 OK)**:
    ```json
    {
      "count": 1,
      "measurements": [
        {
          "metric_id": null,
          "ts": "2026-03-15",
          "value": 0.51,
          "metric_type": "ndvi",
          "plot_id": "8b9f1d04-4b53-4da1-85b4-d53fdebc62e1"
        }
      ]
    }
    ```

#### `GET /measurements/{metric_id}`
Obtiene una medición individual completa filtrando por su UUID. Retorna `404 Not Found` si no existe.

---

## 5. Limitaciones Actuales y Oportunidades de Mejora (Backlog)

Durante el relevamiento del código se identificaron las siguientes inconsistencias lógicas y arquitectónicas, ideales para estructurar el plan de mejora del microservicio:

1.  **Incongruencia de Índices (Falsos Índices)**:
    *   *Descripción*: La API publica soporte en sus schemas para índices como `gci`, `vegetation_health`, `water_detection`, `urban_index`, `soil_moisture` y `change_detection`. Sin embargo, `ee_indices.py` no posee sus ecuaciones. El sistema calcula silenciosamente un **NDVI** y lo renombra, induciendo a error al cliente.
    *   *Propuesta*: Implementar las ecuaciones reales de GEE para cada uno de estos índices usando bandas del satélite (por ejemplo, GCI = `B8 / B3 - 1`, NDVI modificado, etc.).
2.  **Inconsistencia de Fórmulas en Modo Serie Temporal**:
    *   *Descripción*: Por optimizar la velocidad en `get_sentinel2_time_series`, las ecuaciones de `evi` y `savi` se simplificaron linealmente basándose en NDVI (`NDVI * 2.5` y `NDVI * 1.5`). Esto arroja valores científicamente incorrectos.
    *   *Propuesta*: Refactorizar la función `add_index_band_fast` para que emplee las operaciones algebraicas correctas sobre las bandas correspondientes de Sentinel-2 (`B8`, `B4`, `B2`).
3.  **Seguridad y Autenticación Ausente**:
    *   *Descripción*: El backend carece por completo de autenticación y validación de tokens Bearer/JWT (el módulo `auth.py` es dummy y retorna `None`). Los endpoints de consulta y descarga de datos están totalmente abiertos.
    *   *Propuesta*: Implementar middleware de seguridad o conectores OAuth2 en FastAPI para validar las firmas JWT emitidas por el servicio central de autenticación de Terra.
4.  **Error en el Modo `export` de `/compute`**:
    *   *Descripción*: La variable `Mode` de Pydantic acepta `"export"`, pero la estructura condicional de `/compute` solo atiende `"heatmap"` y `"series"`. Cualquier petición con `mode="export"` resulta en un error HTTP `400: mode inválido`. El exportado en realidad se realiza enviando `mode="heatmap"` y agregando el parámetro `export_format`.
    *   *Propuesta*: Eliminar `"export"` como opción de `mode` en los schemas Pydantic, o separar formalmente la lógica de exportado en una ruta específica o flujo de control exclusivo.
5.  **Hardcode de Límites en Series**:
    *   *Descripción*: La obtención de series temporales rápidas limita de manera rígida la respuesta a 30 imágenes en la colección (`.limit(30)`). Esto impide consultar históricos plurianuales.
    *   *Propuesta*: Permitir la parametrización de este límite en el Request schema o implementar paginación de datos temporales.
6.  **Falta de Logs Centralizados**:
    *   *Descripción*: Se alternan instrucciones `print()`, `logger.info()` sin configurar de forma consistente, y escrituras manuales de traceback sobre archivos de log de texto ante excepciones.
    *   *Propuesta*: Homogeneizar la consola de logging con un manejador de FastAPI unificado y estructurar los mensajes de error en formato JSON para simplificar su monitoreo en entornos cloud.
