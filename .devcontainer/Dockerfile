FROM python:3.11-slim

# Evitar escribir archivos bytecode .pyc y asegurar que logs fluyan inmediatamente
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instalar dependencias del sistema necesarias
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Copiar requirements e instalar
COPY requirements.txt /workspace/
RUN pip install --no-cache-dir -r requirements.txt

# Puerto para FastAPI
EXPOSE 8000

# Mantener el contenedor vivo para desarrollo interactivo
CMD ["sleep", "infinity"]
