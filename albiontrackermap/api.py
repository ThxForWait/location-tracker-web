import json
from datetime import datetime, timezone as dt_timezone

from django.http import JsonResponse, HttpRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from .models import Edge, StaticEdge
from .zones import get_zone


# --- Команды ---------------------------------------------------------------

CMD_CONNECT = 1   # создать ребро для авалонов
CMD_STATIC_EDGE = 2  # Статичные переходы между обычными локациями
# CMD_PING       = 3  # на будущее


# --- Утилиты ---------------------------------------------------------------

def _is_tunnel(zone_id: str) -> bool:
    return zone_id.startswith("TNL-")

def _err(message: str, status: int = 400, **extra) -> JsonResponse:
    payload = {"ok": False, "error": message}
    payload.update(extra)
    return JsonResponse(payload, status=status)


def _parse_iso(value) -> datetime | None:
    """ISO 8601 → aware datetime. Принимаем и 'Z', и '+00:00'."""
    if not isinstance(value, str):
        return None
    try:
        # Питон до 3.11 не понимает 'Z'
        s = value.replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


# --- Хендлеры --------------------------------------------------------------

def _handle_connect(data: dict) -> JsonResponse:
    source_id = data.get("source_id")
    target_id = data.get("target_id")
    created_raw = data.get("created_at")
    expires_raw = data.get("expires_at")

    # 1. Обязательные поля
    missing = [k for k, v in {
        "source_id": source_id,
        "target_id": target_id,
        "created_at": created_raw,
        "expires_at": expires_raw,
    }.items() if not v]
    if missing:
        return _err("missing fields", missing=missing)

    source_id = str(source_id)
    target_id = str(target_id)
    if source_id == target_id:
        return _err("ids is the same", vertex_id=source_id, status=404)
    # 2. Вершины должны существовать в zones.json
    if get_zone(source_id) is None:
        return _err("unknown source vertex", vertex_id=source_id, status=404)
    if get_zone(target_id) is None:
        return _err("unknown target vertex", vertex_id=target_id, status=404)

    # 3. Времена
    created_at = _parse_iso(created_raw)
    expires_at = _parse_iso(expires_raw)
    if created_at is None:
        return _err("invalid created_at", value=created_raw)
    if expires_at is None:
        return _err("invalid expires_at", value=expires_raw)
    if expires_at <= created_at:
        return _err("expires_at must be after created_at")

    # Хотябы один обязан быть TNL
    if not(_is_tunnel(source_id)) and not(_is_tunnel(target_id)):
        return _err("edge must involve TNL zones",
                    source_id=source_id, target_id=target_id)

    norm_source, norm_target = Edge.normalize(source_id, target_id)
    # 4. Создаём ребро
    edge = Edge.objects.create(
        source_id=norm_source,
        target_id=norm_target,
        created_at=created_at,
        expires_at=expires_at,
    )

    return JsonResponse({
        "ok": True,
        "command": CMD_CONNECT,
        "edge_id": edge.id,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "created_at": edge.created_at.isoformat(),
        "expires_at": edge.expires_at.isoformat(),
    }, status=201)

def _handle_static_edge(data: dict) -> JsonResponse:
    source_id = data.get("source_id")
    target_id = data.get("target_id")
    created_raw = data.get("created_at")

    missing = [k for k, v in {
        "source_id": source_id,
        "target_id": target_id,
        "created_at": created_raw,
    }.items() if not v]
    if missing:
        return _err("missing fields", missing=missing)

    source_id = str(source_id)
    target_id = str(target_id)

    if get_zone(source_id) is None:
        return _err("unknown source vertex", vertex_id=source_id, status=404)
    if get_zone(target_id) is None:
        return _err("unknown target vertex", vertex_id=target_id, status=404)

    # Оба конца обязаны быть не-TNL
    if _is_tunnel(source_id) or _is_tunnel(target_id):
        return _err("static edge must not involve TNL zones",
                    source_id=source_id, target_id=target_id)

    created_at = _parse_iso(created_raw)
    if created_at is None:
        return _err("invalid created_at", value=created_raw)

    #нормализуем пару
    norm_source, norm_target = StaticEdge.normalize(source_id, target_id)
    # Идемпотентно
    edge, created = StaticEdge.objects.get_or_create(
        source_id=norm_source,
        target_id=norm_target,
        defaults={"created_at": created_at},
    )

    return JsonResponse({
        "ok": True,
        "command": CMD_STATIC_EDGE,
        "edge_id": edge.id,
        "created": created,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "created_at": edge.created_at.isoformat(),
    }, status=201 if created else 200)


HANDLERS = {
    CMD_CONNECT: _handle_connect,
    CMD_STATIC_EDGE: _handle_static_edge,
    # CMD_DISCONNECT: _handle_disconnect,
}


# --- Основная вьюха --------------------------------------------------------

@csrf_exempt
@require_POST
def api_packet(request: HttpRequest) -> JsonResponse:
    # 1. Разбираем JSON
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError as e:
        return _err(f"invalid json: {e}")
    if not isinstance(data, dict):
        return _err("payload must be a json object")

    # 2. Команда
    command = data.get("command")
    if command is None:
        return _err("missing field: command")
    if not isinstance(command, int):
        return _err("command must be an integer")
    handler = HANDLERS.get(command)
    if handler is None:
        return _err("unknown command", command=command, status=400)

    # 3. Отдаём в хендлер
    return handler(data)
