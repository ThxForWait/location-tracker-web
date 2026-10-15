"""Проверки правдоподобия присланных переходов (уровень 3 защиты).

Каждая проверка либо отклоняет отчёт (Rejection → ответ 4xx, отчёт
сохраняется со статусом «отклонён»), либо помечает его подозрительным:
отчёт принимается, а код причины пишется в EdgeReport.reasons
(дальше это снижает доверие к игроку). Пороги — в settings.
"""
from dataclasses import dataclass, field
from datetime import datetime

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from . import trust
from .models import Edge, EdgeKind, EdgeReport, Player, ReportStatus, StaticEdge
from .zones import ZoneKind, get_zone, get_zone_kind


@dataclass
class Rejection(Exception):
    status: int
    code: str
    detail: str


@dataclass
class Verdict:
    # Коды причин подозрения: "path_gap", "too_fast", "zone_overloaded"
    reasons: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return ReportStatus.SUSPICIOUS if self.reasons else ReportStatus.ACCEPTED


def validate_dynamic(player: Player, source_id: str, target_id: str,
                     created_at: datetime, expires_at: datetime) -> Verdict:
    _check_zones(source_id, target_id)
    kinds = {get_zone_kind(source_id), get_zone_kind(target_id)}
    if ZoneKind.INSTANCE in kinds or ZoneKind.ROAD not in kinds:
        raise Rejection(422, "wrong_kind",
                        "Ребро Авалона должно связывать дорогу (TNL) с дорогой или открытой зоной")
    _check_time(created_at)

    lifetime = expires_at - created_at
    if lifetime.total_seconds() <= 0 or lifetime > settings.TRACKER_MAX_PORTAL_LIFETIME:
        raise Rejection(422, "invalid_lifetime", "Недопустимое время жизни портала")

    verdict = Verdict()
    _check_player_path(verdict, player, source_id, created_at)
    _check_road_exits(verdict, source_id, target_id)
    return verdict


def validate_static(player: Player, source_id: str, target_id: str,
                    created_at: datetime) -> Verdict:
    _check_zones(source_id, target_id)
    if get_zone_kind(source_id) != ZoneKind.STATIC or get_zone_kind(target_id) != ZoneKind.STATIC:
        raise Rejection(422, "wrong_kind", "Статичное ребро должно связывать две открытые зоны")
    _check_time(created_at)

    if settings.TRACKER_STATIC_GRAPH_FROZEN:
        a, b = StaticEdge.normalize(source_id, target_id)
        if not StaticEdge.objects.filter(source_id=a, target_id=b, is_reference=True).exists():
            raise Rejection(409, "not_in_reference", "Такого статичного перехода нет на карте")

    verdict = Verdict()
    _check_player_path(verdict, player, source_id, created_at)
    return verdict


# --- Отдельные проверки ----------------------------------------------------

def _check_zones(source_id: str, target_id: str) -> None:
    if source_id == target_id:
        raise Rejection(422, "same_zone", "Начало и конец ребра совпадают")
    for zone_id in (source_id, target_id):
        if get_zone(zone_id) is None:
            raise Rejection(422, "unknown_zone", f"Неизвестная зона {zone_id}")


def _check_time(created_at: datetime) -> None:
    now = timezone.now()
    if created_at > now + settings.TRACKER_MAX_CLOCK_SKEW:
        raise Rejection(422, "created_in_future", "created_at в будущем — проверьте часы компьютера")
    if now - created_at > settings.TRACKER_MAX_REPORT_AGE:
        raise Rejection(422, "too_old", "Переход слишком старый")


def _check_player_path(verdict: Verdict, player: Player, source_id: str,
                       created_at: datetime) -> None:
    """Путь игрока непрерывен и не быстрее, чем физически возможно."""
    previous = (
        EdgeReport.objects.filter(player=player)
        .exclude(status=ReportStatus.REJECTED)
        .order_by("-created_at")
        .first()
    )
    if previous is None:
        return
    gap = created_at - previous.created_at
    if gap < settings.TRACKER_MIN_TRANSITION_INTERVAL:
        verdict.reasons.append("too_fast")
    if previous.target_id != source_id and gap < settings.TRACKER_PATH_GAP_WINDOW:
        verdict.reasons.append("path_gap")


def _check_road_exits(verdict: Verdict, source_id: str, target_id: str) -> None:
    """У зоны дорог не бывает больше N живых выходов одновременно."""
    now = timezone.now()
    pair = Edge.normalize(source_id, target_id)
    for zone_id in (source_id, target_id):
        if get_zone_kind(zone_id) != ZoneKind.ROAD:
            continue
        exits = (
            Edge.objects.filter(Q(source_id=zone_id) | Q(target_id=zone_id), expires_at__gt=now)
            .exclude(source_id=pair[0], target_id=pair[1])
            .count()
        )
        if exits >= settings.TRACKER_MAX_ROAD_EXITS:
            verdict.reasons.append("zone_overloaded")
            return


def save_rejected(player: Player, token, ip: str | None, kind: EdgeKind, source_id: str,
                  target_id: str, created_at: datetime, expires_at: datetime | None,
                  rejection: Rejection) -> None:
    """Отклонённый отчёт тоже сохраняем — для разбора и доверия к игроку."""
    EdgeReport.objects.create(
        player=player, token=token, ip=ip, kind=kind,
        source_id=source_id[:64], target_id=target_id[:64],
        created_at=created_at, expires_at=expires_at,
        status=ReportStatus.REJECTED, reasons=[rejection.code],
    )
    trust.penalize(player, ReportStatus.REJECTED)
