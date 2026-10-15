from django.shortcuts import render

from .maps import build_graph, public_edges


def render_map(request, *, title: str, edges, kind: str):
    """Общая обёртка для всех карт (общая, личная, пул, в прошлом)."""
    return render(request, "trackermap/map.html", {
        "map_title": title,
        "map_kind": kind,
        "graph": build_graph(edges),
    })


def graph_view(request):
    """Общая карта: живые рёбра с учётом доверия."""
    return render_map(request, title="Общая карта", edges=public_edges(), kind="public")
