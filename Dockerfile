# Imagen del worker de rásters.
#
# `python:3.13` no es una elección de este archivo: es el contrato que fijó
# `DECISIONS #22`, y la regla que va con él es que **todo instale como wheel**.
# Si un pin nuevo obliga a compilar `rasterio` contra GDAL, el pin está mal, no
# el entorno — y con `--only-binary=:all:` el build lo dice en el momento en vez
# de ponerse a compilar y fallar distinto en cada máquina.
FROM python:3.13-slim

# `rasterio` trae GDAL en su wheel, pero GDAL necesita `libexpat1`, que la imagen
# slim no incluye. Es la única dependencia de sistema: `psycopg2-binary` trae
# libpq, `shapely` trae GEOS, y `earthengine-api` es Python puro.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    && rm -rf /var/lib/apt/lists/*

# Sin .pyc en la imagen, y stdout sin buffer para que los logs salgan en orden
# — importa porque el log estructurado de Railway parsea por línea.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# `requirements-dev.txt` **no** se instala: pytest, httpx, ruff y pip-audit son
# herramientas de desarrollo y no tienen nada que hacer en la imagen del
# servicio (`PLAN.md` F.15).
COPY requirements.txt .
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

# El código. En el tileserver este paso faltaba y la imagen quedaba sin `main.py`:
# funcionaba en local solo por el bind mount del compose, y el contenedor moría
# al arrancar en Railway. Se copia explícito, archivo por archivo, para que el
# olvido sea visible.
COPY app.py config.py check_schema.py ./
COPY services/ ./services/
COPY repositories/ ./repositories/
COPY utils_pkg/ ./utils_pkg/
COPY scripts/ ./scripts/

# `config.py` hace `os.makedirs(BASE_OUTPUT_DIR)` **al importarse**, así que el
# directorio tiene que existir y ser escribible por el usuario del proceso antes
# de que arranque. `export_heatmap` escribe ahí sus GeoTIFF temporales.
RUN mkdir -p /app/outputs

# Servicio con URL pública —Inngest Cloud la exige— así que no corre como root.
RUN useradd --create-home --uid 10001 worker && chown -R worker:worker /app
USER worker

# Railway inyecta $PORT y espera que el proceso lo escuche. El 8000 es solo el
# fallback para `docker run` a mano.
ENV PORT=8000
EXPOSE 8000

# Sin `--reload`: es un flag de desarrollo, vigila el filesystem y duplica
# procesos. Y sin `--workers`: los handlers corren en el pool de hilos del SDK de
# Inngest (`DECISIONS #26`), no en procesos paralelos de uvicorn.
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
