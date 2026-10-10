r"""M.9.7e2 y M.9.7g: el paso del rancho con la receta v3, contra GEE, sin tocar nada.

QUE HACE
--------
Corre `handlers/rancho.procesar_mes` con la receta vigente (o la de `--receta`) —el compuesto del mes y un
COG por cada pasada con algun pixel en el rancho, bajados en paralelo— sobre un rancho y
unos meses, y dice cuanto tardo cada mes, cuantos archivos y filas salieron y cuanto
pesan. **La descarga, el COG y la conversion son los de verdad**; lo unico que se
reemplaza es:

- la subida a MinIO, por una copia a una carpeta local (`--salida`);
- `insert_layer`, por una lista en memoria.

No escribe en la base ni en el bucket. Sirve para lo que M.9.7g pide ANTES de poner v3
vigente: **medir un rancho grande real** contra la compuerta de 60 s por mes. Uno de
~2.500 ha nublado dio 48,6 s el 2026-09-27 (`DECISIONS #77`).

LOS RANCHOS
-----------
Un GeoJSON (Polygon, Feature o FeatureCollection) por archivo. La geometria de un rancho
de cliente va en `scratch/`, que esta en `.gitignore`.

USO
---
    .venv\Scripts\python.exe scripts\check_rancho_por_pasada.py --rancho scratch\rancho.geojson --meses 2025-07 2025-10 --salida scratch\cogs_v3

Con `--hilos N` se prueba otra cantidad de descargas en paralelo (por defecto, la del
worker). Medido: mas de 4 no ayuda, porque el cuello es GEE.
"""

import argparse
import functools
import json
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Ids de mentira, validos como uuid: la key los necesita y nada se sube.
_RANCHO = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
_TENANT = "7f3c2a10-5b6d-4e8f-9a01-23456789abcd"
_COMPUERTA_S = 60


def _coordenadas(archivo: Path) -> list[dict]:
    """Las coordenadas del rancho como las manda Geocore: `[{lat, lng}, ...]`."""
    crudo = json.loads(archivo.read_text(encoding="utf-8"))
    if crudo.get("type") == "FeatureCollection":
        crudo = crudo["features"][0]
    geometria = crudo.get("geometry", crudo)
    anillo = geometria["coordinates"][0]
    return [{"lat": lat, "lng": lng} for lng, lat in anillo]


def main() -> int:
    parser = argparse.ArgumentParser(description="El rancho por pasada, sin subir nada")
    parser.add_argument("--rancho", type=Path, required=True, help="GeoJSON del rancho")
    parser.add_argument("--meses", nargs="+", required=True, help="meses AAAA-MM")
    parser.add_argument("--salida", type=Path, required=True,
                        help="carpeta donde quedan los COG; conviene scratch/")
    parser.add_argument("--hilos", type=int, default=None,
                        help="descargas en paralelo (por defecto, las del worker)")
    # M.9.3 (2026-10-09): para medir v3 y v4 lado a lado. Por defecto, la vigente.
    parser.add_argument("--receta", choices=["v3", "v4"], default=None,
                        help="la receta a medir (por defecto, la vigente)")
    args = parser.parse_args()

    from handlers import rancho, raster
    from pipeline.periodos import Mes
    from pipeline.productos import productos_del_cog
    from pipeline.receta import RECETA_PASADA_V3, RECETA_PASADA_V4, RECETA_VIGENTE

    receta = {"v3": RECETA_PASADA_V3, "v4": RECETA_PASADA_V4}.get(args.receta, RECETA_VIGENTE)
    por_archivo = len(productos_del_cog(receta))

    args.salida.mkdir(parents=True, exist_ok=True)

    class _Local:
        # La firma de `StorageService.upload_file`: `tipo` no se usa, pero se recibe.
        def upload_file(self, key, ruta, tipo):
            shutil.copy(ruta, args.salida / key.replace("/", "__"))
            return key

    filas: list[dict] = []
    raster.get_storage_service = _Local
    rancho.insert_layer = lambda **kw: filas.append(kw)
    if args.hilos:
        rancho.en_paralelo = functools.partial(raster.en_paralelo, hilos=args.hilos)

    coordenadas = _coordenadas(args.rancho)
    lentos = []
    print(f"receta {receta.version} · {args.rancho.name}")
    for texto in args.meses:
        antes = len(filas)
        reloj = time.monotonic()
        resultado = rancho.procesar_mes(
            rancho_id=_RANCHO, tenant_id=_TENANT, coordenadas=coordenadas,
            mes=Mes.desde_texto(texto), posicion=1, total=1, receta=receta,
        )
        segundos = time.monotonic() - reloj
        nuevas = filas[antes:]
        pasadas = sum(1 for f in nuevas if f["source"] == "pasada") // por_archivo
        print(f"  {texto}: {segundos:5.1f} s · {len(resultado['storage_keys'])} archivos "
              f"({pasadas} pasadas) · {resultado['mapas']} filas · "
              f"cobertura del mes {resultado['cobertura']:.2f}")
        if segundos > _COMPUERTA_S:
            lentos.append(texto)

    tamanos = [a.stat().st_size / 1e6 for a in args.salida.glob("*.tif")]
    if tamanos:
        print(f"  archivos: {min(tamanos):.2f} a {max(tamanos):.2f} MB")
    if lentos:
        print(f"FALLA: pasan la compuerta de {_COMPUERTA_S} s: {', '.join(lentos)}")
        return 1
    print(f"OK: todos los meses bajo {_COMPUERTA_S} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
