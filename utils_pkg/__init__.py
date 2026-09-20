"""Utilidades del worker.

**No exporta nada.** Lo que tenía se fue con los módulos que lo usaban:

- `roi.py` el 2026-09-07 (F.17): sus siete funciones quedaron sin llamadores al
  desaparecer el camino de KML por el worker. Tomaban un objeto `req` con
  atributos, que era la forma de los requests HTTP que la FASE D eliminó; los
  handlers arman el ROI con `coords_to_geometry(payload["coordinates"])`,
  directo del evento.
- `visualization.py` en M.6.2b, con la capa vieja: el rango y la paleta de cada
  índice los define el panel (`src/lib/indices.ts`), no el worker.
- `cache.py` e `io.py` en M.6.4, los dos **sin un solo llamador**. Guardaban
  mapids de GEE y estadísticas de cálculo en `BASE_OUTPUT_DIR`; lo último que
  escribía ahí era `export_service.py`. Hoy las descargas van a `tempfile`.

Queda `conexiones.py` —el reporte de arranque— y `arranque.py` y
`logging_config.py`.

**Observación para cuando se retome:** si nada escribe en `BASE_OUTPUT_DIR`, el
chequeo `verificar_outputs` verifica una carpeta que ya no usa nadie. No se saca
acá porque atrapó un fallo real de producción y cuesta poco; pero si sigue sin
usarse, la variable y el chequeo son candidatos a irse juntos.
"""
