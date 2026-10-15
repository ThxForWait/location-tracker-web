"""Регистрация присланных переходов в графе.

Используется эндпоинтами API v1 (api_v1.py) после проверок из validation.py:
- динамическое ребро — агрегат, повторный отчёт о живом портале
  добавляет подтверждение, а не создаёт дубль;
- статичное ребро идемпотентно.
После каждого отчёта пересчитываются статус ребра и доверие к игроку (trust.py).
"""
from datetime import datetime

from django.db import transaction
from django.utils import timezone

from . import trust
from .models import Edge, EdgeKind, EdgeReport, Player, StaticEdge


@transaction.atomic
def register_dynamic_edge(
    source_id: str,
    target_id: str,
    created_at: datetime,
    expires_at: datetime,
    *,
    player: Player | None = None,
    report_meta: dict | None = None,
) -> tuple[Edge, bool]:
    """Записывает отчёт и возвращает (ребро, создано_ли_новое)."""
    norm_source, norm_target = Edge.normalize(source_id, target_id)
    now = timezone.now()

    edge = (
        Edge.objects.select_for_update()
        .filter(source_id=norm_source, target_id=norm_target, expires_at__gt=now)
        .order_by("-expires_at")
        .first()
    )
    created = edge is None
    is_new_reporter = player is not None and (
        created or not edge.reports.filter(player=player).exists()
    )

    if created:
        edge = Edge.objects.create(
            source_id=norm_source,
            target_id=norm_target,
            created_at=created_at,
            last_seen=created_at,
            expires_at=expires_at,
            confirmations=1,
            reporters_count=1 if player is not None else 0,
        )
    else:
        edge.confirmations += 1
        edge.last_seen = max(edge.last_seen, created_at)
        # Время жизни портала пока неизвестно точно — берём более позднее
        edge.expires_at = max(edge.expires_at, expires_at)
        if is_new_reporter:
            edge.reporters_count += 1
        edge.save()

    report = EdgeReport.objects.create(
        player=player,
        kind=EdgeKind.DYNAMIC,
        source_id=source_id,
        target_id=target_id,
        created_at=created_at,
        expires_at=expires_at,
        edge=edge,
        **(report_meta or {}),
    )
    trust.penalize(player, report.status)
    trust.refresh_edge_status(edge, report)
    return edge, created


@transaction.atomic
def register_static_edge(
    source_id: str,
    target_id: str,
    created_at: datetime,
    *,
    player: Player | None = None,
    report_meta: dict | None = None,
) -> tuple[StaticEdge, bool]:
    """Записывает отчёт и возвращает (ребро, создано_ли_новое). Идемпотентно."""
    norm_source, norm_target = StaticEdge.normalize(source_id, target_id)
    edge, created = StaticEdge.objects.get_or_create(
        source_id=norm_source,
        target_id=norm_target,
        defaults={"created_at": created_at},
    )
    report = EdgeReport.objects.create(
        player=player,
        kind=EdgeKind.STATIC,
        source_id=source_id,
        target_id=target_id,
        created_at=created_at,
        static_edge=edge,
        **(report_meta or {}),
    )
    trust.penalize(player, report.status)
    return edge, created
