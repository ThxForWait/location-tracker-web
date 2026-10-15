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
    # Города и всё, что к ним относится: банки, рынки, порталы городов в чёрной
    # зоне, Карлеон, Брисилиен. Переходы с ними не учитываются вообще.
    CITY = "city"
    # Постоянная зона открытого мира (открытые зоны, проходы, перекрёстки)
    STATIC = "static"
    # Зона Дорог Авалона (TNL-*)
    ROAD = "road"


# Типы городских зон (поле type в zones.json)
CITY_ZONE_TYPES = frozenset({
    # Королевские города и их банки/рынки
    "PLAYERCITY_SAFEAREA_01",
    "PLAYERCITY_SAFEAREA_02",
    "PLAYERCITY_SAFEAREA_NOFURNITURE",
    # Порталы королевских городов в чёрной зоне («Fort Sterling Portal» и т.п.)
    "PLAYERCITY_BLACK_PORTALCITY_NOFURNITURE",
    # Карлеон, его банк, рынки и зал славы
    "PLAYERCITY_BLACK_ROYAL",
    "PLAYERCITY_BLACK_ROYAL_NOFURNITURE",
    "PLAYERCITY_BLACK_ROYAL_NOFURNITURE_HALL_OF_FAME",
    # Брисилиен, его банк и рынок
    "PLAYERCITY_BLACK",
    "PLAYERCITY_BLACK_NOFURNITURE",
    # Логово антиквара внутри городов
    "PLAYERCITY_HELLDEN",
})


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
    if zone_type in CITY_ZONE_TYPES:
        return ZoneKind.CITY
    if (
        zone_type.startswith(("OPENPVP_", "PASSAGE_", "PLAYERCITY_"))
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


@lru_cache(maxsize=1)
def city_zone_ids() -> frozenset[str]:
    """ID всех городских зон — чтобы убрать их с карты одним запросом."""
    return frozenset(zone_id for zone_id, zone in load_zones().items()
                     if classify_zone_type(zone.get("type", "")) == ZoneKind.CITY)
