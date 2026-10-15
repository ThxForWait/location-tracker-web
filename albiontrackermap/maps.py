"""Карты: какие рёбра показывать и как превратить их в граф для шаблона.

Все карты рисуются одним шаблоном trackermap/map.html; отличаются только
выборкой рёбер (функции *_edges) и заголовком.

Сейчас есть общая карта. Задуманы (см. CLAUDE.md, «Карты»):
- личная карта игрока — всё, что он открыл сам, без фильтров доверия;
- карта пула — то, что открыли участники пула, видна только участникам;
  данные приватного пула не попадают на общую карту;
- общая карта в прошлом (`at`) — без учёта приватности пулов.
"""
from datetime import datetime

from django.db.models import QuerySet
from django.utils import timezone

from .models import Edge, EdgeStatus
from .zones import ZoneKind, city_zone_ids, get_zone_kind, load_zones


def edges_alive_at(at: datetime | None = None) -> QuerySet[Edge]:
    """Рёбра, живые в момент `at` (по умолчанию — сейчас).

    Рёбра с городами (могли попасть в базу до запрета) на картах не показываются.
    """
    moment = at or timezone.now()
    cities = city_zone_ids()
    return (
        Edge.objects.filter(created_at__lte=moment, expires_at__gt=moment)
        .exclude(source_id__in=cities)
        .exclude(target_id__in=cities)
    )


def public_edges(at: datetime | None = None) -> QuerySet[Edge]:
    """Общая карта: учитывается доверие — спорные рёбра (о них сообщали
    только игроки в карантине) не показываются."""
    return edges_alive_at(at).exclude(status=EdgeStatus.DISPUTED)


def _band_for(zone_id: str, zone: dict | None) -> str:
    """Полоса узла на карте: top — чёрная зона, middle — дороги, bottom — остальное."""
    if zone is None:
        return "bottom"
    if get_zone_kind(zone_id) == ZoneKind.ROAD:
        return "middle"
    if zone.get("pvpType") == "black":
        return "top"
    return "bottom"


def build_graph(edges) -> dict:
    """Узлы (по полосам) и рёбра в формате, который ждёт map.html."""
    zones = load_zones()
    edges = list(edges)

    used_ids = {zone_id for e in edges for zone_id in (e.source_id, e.target_id)}
    groups = {"top": [], "middle": [], "bottom": []}
    for zone_id in sorted(used_ids):
        zone = zones.get(zone_id)
        groups[_band_for(zone_id, zone)].append({
            "id": zone_id,
            "label": (zone.get("name") if zone else None) or zone_id,
            "tier": zone.get("tier") if zone else None,
            "pvpType": zone.get("pvpType") if zone else None,
            "missing": zone is None,
        })

    return {
        "groups": groups,
        "edges": [
            {
                "id": e.id,
                "from": e.source_id,
                "to": e.target_id,
                "expiresAt": e.expires_at.isoformat(),
                "confirmed": e.status == EdgeStatus.CONFIRMED,
            }
            for e in edges
        ],
    }
