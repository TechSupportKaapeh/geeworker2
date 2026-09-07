# `roi.py` se borro el 2026-09-07 (PLAN.md F.17). Sus siete funciones quedaron sin
# llamadores al desaparecer el camino de KML por el worker: la rama `kml_id` leia
# una key que nadie escribia desde la FASE D, y `get_roi_and_bounds` —su unico
# consumidor— tampoco tenia llamadores. Tomaban un objeto `req` con atributos,
# que era la forma de los requests HTTP que la FASE D elimino; los handlers arman
# el ROI con `coords_to_geometry(payload["coordinates"])`, directo del evento.
from .visualization import index_band_and_vis, get_tile_url
from .cache import make_cache_key, save_mapid, load_mapid
from .io import save_compute_stats, ensure_outputs_dir, timestamped_base
from .io import round_sig

__all__ = [
	"index_band_and_vis",
	"get_tile_url",
	"make_cache_key",
	"save_mapid",
	"load_mapid",
	"save_compute_stats",
	"ensure_outputs_dir",
	"timestamped_base",
	"round_sig",
]
