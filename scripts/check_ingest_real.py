r"""A-3 completo: la cadena de ingesta real, de GEE al bucket.

QUE HACE
--------
Corre el **mismo camino que `process_parcela`**, con datos reales, sobre un ROI
chico de Sinaloa: pide el indice a GEE, baja el GeoTIFF, lo convierte a COG,
**valida que sea un COG de verdad** y lo sube a MinIO.

No toca la base: `check_write_path.py` ya cubre los permisos del bucket y este
script cubre lo otro que faltaba.

PARA QUE SIRVE
--------------
Es la verificacion que `DECISIONS #22` dejo escrita y pendiente:

> `rio-cogeo` 5.3 -> 5.4 puede mover el COG que produce
> `services/cog_converter.py`, que llama a `cog_translate` con
> `web_optimized=True` y `add_mask=True`. La FASE B verifico la composicion por
> mediana con COGs armados por el spike, **no por este convertidor**. Que los
> COG del worker se apilen bien en el mosaico se prueba en A-3.

O sea: hasta ahora nadie confirmo que el archivo que este repo produce sea un COG
valido. `cog_validate` de rio-cogeo lo dice sin ambiguedad — y lo dice **antes**
de subirlo, que es donde sirve.

Y lo que sube queda disponible para el ultimo escalon, que cierra el circulo
completo: leerlo con el **tileserver desplegado**.

    cd C:\Users\aayal\Downloads\tileserver-titiler
    python scripts\check_prod.py --base https://<titiler> --token $TOKEN --key <la key que imprime este script>

USO
---
    .venv\Scripts\python.exe scripts\check_ingest_real.py

Escribe bajo `_diagnostico/`, igual que `check_write_path.py`, y **no borra**.
"""
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Un ROI chico y real: el mismo cuadrante de Sinaloa cuyos bounds quedaron
# registrados en el primer tile real del 2026-08-26. Chico a proposito —
# `getDownloadURL` tiene tope de tamano (C.6).
ROI_SINALOA = [[
    [-107.45, 24.7476], [-107.45, 24.7800],
    [-107.4100, 24.7800], [-107.4100, 24.7476], [-107.45, 24.7476],
]]
INDICE = "ndvi"
PREFIJO = "_diagnostico"

_ok = "  OK        "
_falla = "  FALLA     "


def main():
    from rio_cogeo.cogeo import cog_validate

    from services.cog_converter import convert_to_cog
    from services.ee.ee_client import init_ee
    from services.export_service import export_heatmap
    from services.inngest_handlers import _borrar_temporales, coords_to_geometry
    from services.storage_service import get_storage_service

    print()

    # --- 1. GEE responde -----------------------------------------------------
    try:
        init_ee()
        roi = coords_to_geometry(ROI_SINALOA)
        print(f"{_ok}gee         autenticado, ROI armado")
    except Exception as e:  # noqa: BLE001 - un diagnostico reporta, no propaga
        print(f"{_falla}gee         {type(e).__name__}: {e}")
        return 1

    # Una ventana de 30 dias, como usa `process_rancho`. Con menos, la
    # `Ventana Temporal de Curacion` de `ee_indices` la expande sola.
    fin = datetime.now(timezone.utc)
    inicio = fin - timedelta(days=30)
    f_inicio, f_fin = inicio.strftime("%Y-%m-%d"), fin.strftime("%Y-%m-%d")

    # --- 2. La descarga real: GEE compone y el worker baja el GeoTIFF -------
    ruta = cog = None
    try:
        ruta, stats = export_heatmap(
            roi=roi, roi_bounds=None, index=INDICE,
            start=f_inicio, end=f_fin, cloud_pct=30, export_format="geotiff",
        )
        peso = Path(ruta).stat().st_size
        print(f"{_ok}descarga    {peso:,} bytes de GEE ({f_inicio} a {f_fin})")
        if stats:
            # Estas estadisticas son las que `layers` no puede guardar: es el
            # item 3 del pedido a Geocore, y aca se ven calculadas y tiradas.
            print(f"{' ' * 12}            stats calculadas y descartadas: "
                  f"{ {k: round(v, 4) for k, v in stats.items() if isinstance(v, (int, float))} }")
    except Exception as e:  # noqa: BLE001
        print(f"{_falla}descarga    {type(e).__name__}: {e}")
        _borrar_temporales(ruta)
        return 1

    try:
        # --- 3. La conversion a COG ------------------------------------------
        try:
            cog = convert_to_cog(ruta)
            print(f"{_ok}cog         {Path(cog).stat().st_size:,} bytes")
        except Exception as e:  # noqa: BLE001
            print(f"{_falla}cog         {type(e).__name__}: {e}")
            return 1

        # --- 4. **Es un COG valido?** Lo que DECISIONS #22 dejo pendiente ----
        valido, errores, avisos = cog_validate(cog)
        if valido:
            print(f"{_ok}cog valido  rio-cogeo lo acepta"
                  f"{f' ({len(avisos)} aviso(s))' if avisos else ''}")
            for aviso in avisos:
                print(f"{' ' * 12}            aviso: {aviso}")
        else:
            print(f"{_falla}cog valido  rio-cogeo lo rechaza:")
            for error in errores:
                print(f"{' ' * 12}            {error}")
            return 1

        # --- 5. La subida, con la convencion de keys real --------------------
        corrida = uuid.uuid4().hex[:8]
        key = f"{PREFIJO}/{corrida}/{f_fin}_{INDICE}.tif"
        try:
            get_storage_service().upload_file(key, cog, "image/tiff")
            print(f"{_ok}subida      {key}")
        except Exception as e:  # noqa: BLE001
            print(f"{_falla}subida      {type(e).__name__}: {e}")
            return 1
    finally:
        _borrar_temporales(ruta, cog)

    print()
    print("  OK: La cadena GEE -> COG -> MinIO funciona con datos reales.")
    print()
    print("  Ultimo escalon, desde el repo del tileserver - cierra el circulo:")
    print("    python scripts" + chr(92) + "check_prod.py --base https://<titiler> " + chr(92) * 2)
    print(f"        --token $TOKEN --key {key}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
