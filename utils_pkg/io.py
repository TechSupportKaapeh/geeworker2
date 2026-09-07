import os
import json
from pathlib import Path
from config import BASE_OUTPUT_DIR


def ensure_outputs_dir():
    os.makedirs(BASE_OUTPUT_DIR, exist_ok=True)


def save_compute_stats(stats_obj, base: str = None):
    try:
        import datetime
        ensure_outputs_dir()
        ts = datetime.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')
        if not base:
            base = f'stats_{ts}'
        fname = f'compute_stats_{base}_{ts}.json'
        path = Path(BASE_OUTPUT_DIR) / fname
        with open(path, 'w', encoding='utf-8') as fh:
            json.dump(stats_obj, fh, ensure_ascii=False, indent=2)
        return str(path)
    except Exception:
        return None


def timestamped_base(index, start, end):
    import time
    ts = int(time.time())
    base = f"{index}_{start}_{end}_{ts}"
    return base, ts


def round_sig(x, sig=2):
    """Redondea un número a `sig` cifras significativas.

    - Si x no es numérico devuelve None.
    - Mantiene None como None.
    """
    try:
        if x is None:
            return None
        x = float(x)
        if x == 0:
            return 0.0
        import math
        return float(round(x, sig - int(math.floor(math.log10(abs(x)))) - 1))
    except Exception:
        return None
