import os
import tempfile
import rasterio
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles

def convert_to_cog(input_path: str, output_dir: str = None, profile_name: str = "deflate") -> str:
    """
    Convierte un TIFF local en un Cloud Optimized GeoTIFF (COG).
    
    Args:
        input_path: Ruta local del archivo TIFF.
        output_dir: Carpeta destino. Si es None, se usa una temporal.
        profile_name: Nombre de perfil de compresión de rio-cogeo.
        
    Returns:
        Ruta local del COG generado.
    """
    if not os.path.exists(input_path):
        raise FileNotFoundError(f"No existe el archivo de entrada: {input_path}")
        
    output_dir = output_dir or tempfile.mkdtemp(prefix="cog_")
    os.makedirs(output_dir, exist_ok=True)
    
    filename = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{filename}_cog.tif")
    
    dst_profile = cog_profiles.get(profile_name)
    dst_profile.update(blockxsize=512, blockysize=512)
    
    cog_translate(
        input_path,
        output_path,
        dst_profile,
        in_memory=False,
        quiet=True,
        web_optimized=True,
        add_mask=True
    )
    
    return output_path
