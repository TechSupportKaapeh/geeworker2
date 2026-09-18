"""Las piezas que comparten los handlers de Inngest (M.4.2, ``ARQUITECTURA`` §4).

Salieron de ``services/inngest_handlers.py`` sin cambiar comportamiento, para que
los handlers del pipeline mensual (M.4.4 y M.4.5) las usen sin importar la capa
vieja, que M.6.1 borra:

- ``seguimiento``: el wrapper que lleva el estado del job y su bitácora, y
  ``RETRIES``;
- ``geometria``: el ROI desde el payload de Geocore;
- ``utilidades``: temporales, avance y milisegundos.

Las claves de capa de lo mensual están en ``pipeline/claves.py`` (M.4.1). Las de
la capa vieja se quedan con ella.

Importar este paquete no toca la red, igual que ``pipeline`` (``DECISIONS #24``).
"""
