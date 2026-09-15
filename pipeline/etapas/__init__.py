"""Las etapas del pipeline (``ARQUITECTURA_PIPELINE.md`` §2 y §3.1, sprint M.2).

Cada etapa recibe una expresión de GEE y devuelve otra: fuente, nubes,
compuesto y reducción. **Arman, no calculan.** Ninguna llama a ``getInfo()`` ni
a ``getDownloadURL()``: eso lo hace ``pipeline/ejecucion.py``, el único borde
con GEE (§3.3). Lo fija ``tests/test_pipeline_borde.py``.

Importarlas no toca la red, igual que el resto del paquete: ``import ee`` no
abre conexiones, y ninguna arma nada a nivel de módulo.
"""
