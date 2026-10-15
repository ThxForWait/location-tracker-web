import json
from enum import Enum
from functools import lru_cache
from pathlib import Path

ZONES_PATH = Path(__file__).resolve().parent / "data" / "zones.json"


class ZoneKind(str, Enum):
    """Категория зоны с точки зрения карты.

    Должна совпадать с классификацией в клиенте (location-tracker, config/zones.go).
    """
    # Всё, что не узел карты: данжи, острова, хайдауты, арены, Мгла, неизвестные ID
    INSTANCE = "instance"
    # Постоянная зона открытого мира (города, открытые зоны, проходы)
    STATIC = "static"
    # Зона Дорог Авалона (TNL-*)
    ROAD = "road"


@lru_cache(maxsize=1)
def load_zones() -> dict:
    """Возвращает словарь {zone_id: zone_data} из zones.json."""
    with ZONES_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def get_zone(zone_id: str) -> dict | None:
    return load_zones().get(str(zone_id))


def classify_zone_type(zone_type: str) -> ZoneKind:
    if zone_type.startswith("TUNNEL_"):
        return ZoneKind.ROAD
    if (
        zone_type.startswith(("OPENPVP_", "PASSAGE_"))
        or (zone_type.startswith("PLAYERCITY_") and "HALL_OF_FAME" not in zone_type)
        or zone_type in ("STARTINGCITY", "SAFEAREA", "STARTAREA")
    ):
        return ZoneKind.STATIC
    return ZoneKind.INSTANCE


def get_zone_kind(zone_id: str) -> ZoneKind:
    """Категория зоны; ID, которых нет в базе, считаются инстансами."""
    zone = get_zone(zone_id)
    if zone is None:
        return ZoneKind.INSTANCE
    return classify_zone_type(zone.get("type", ""))
