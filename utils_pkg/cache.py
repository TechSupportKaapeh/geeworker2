import json
import hashlib
from pathlib import Path
from config import BASE_OUTPUT_DIR


def _cache_dir() -> Path:
    d = Path(BASE_OUTPUT_DIR) / 'cache'
    d.mkdir(parents=True, exist_ok=True)
    return d


def make_cache_key(obj: dict) -> str:
    """Create a stable cache key from a JSON-serializable dict."""
    s = json.dumps(obj, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(s.encode('utf-8')).hexdigest()


def save_mapid(key: str, data: dict):
    p = _cache_dir() / f"mapid_{key}.json"
    try:
        with open(p, 'w', encoding='utf-8') as fh:
            json.dump(data, fh)
    except Exception:
        pass


def load_mapid(key: str):
    p = _cache_dir() / f"mapid_{key}.json"
    if not p.exists():
        return None
    try:
        with open(p, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        return None
