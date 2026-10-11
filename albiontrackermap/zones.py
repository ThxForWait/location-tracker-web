import json
from functools import lru_cache
from pathlib import Path

ZONES_PATH = Path(__file__).resolve().parent / "data" / "zones.json"


@lru_cache(maxsize=1)
def load_zones() -> dict:
    """Возвращает словарь {zone_id: zone_data} из zones.json."""
    with ZONES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def get_zone(zone_id: str) -> dict | None:
    return load_zones().get(str(zone_id))
