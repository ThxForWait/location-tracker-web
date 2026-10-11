import json
from django.shortcuts import render
from django.utils import timezone

from .models import Edge
from .zones import load_zones


def _band_for(zone_id: str, zone: dict | None) -> str:
    """Определяет полосу узла. Логика живёт на бэкенде."""
    if zone is None:
        return "bottom"
    if str(zone_id).startswith("TNL-"):
        return "middle"
    if zone.get("pvpType") == "black":
        return "top"
    return "bottom"

def graph_view(request):
    zones = load_zones()
    edges_qs = Edge.objects.filter(expires_at__gt=timezone.now())

    used_ids = set()
    for e in edges_qs:
        used_ids.add(e.source_id)
        used_ids.add(e.target_id)

    groups = {"top": [], "middle": [], "bottom": []}

    for zone_id in used_ids:
        zone = zones.get(zone_id)
        node = {
            "id": zone_id,
            "label": (zone.get("name") if zone else None) or zone_id,
            "tier": zone.get("tier") if zone else None,
            "pvpType": zone.get("pvpType") if zone else None,
            "missing": zone is None,
        }
        groups[_band_for(zone_id, zone)].append(node)

    edges = [
        {
            "id": e.id,
            "from": e.source_id,
            "to": e.target_id,
            "expiresAt": e.expires_at.isoformat(),
        }
        for e in edges_qs
    ]

    return render(request, "trackermap/map.html", {
        "groups_json": json.dumps(groups),
        "edges_json": json.dumps(edges),
    })
