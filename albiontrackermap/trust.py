"""Доверие к игрокам и подтверждение рёбер (уровень 4 защиты).

- Ребро подтверждено, если о нём сообщили TRACKER_CONFIRMATIONS_REQUIRED
  независимых игроков (разные аккаунты и разные IP) или один надёжный игрок
  (is_trusted или доверие ≥ TRACKER_TRUST_RELIABLE).
- Доверие растёт, когда ребро игрока подтверждает другой независимый игрок,
  и падает за подозрительные и отклонённые отчёты.
- Игрок с доверием ниже TRACKER_TRUST_QUARANTINE в карантине: его отчёты
  сохраняются, но ребро, о котором сообщали только игроки в карантине,
  становится «спорным» и на карте не показывается.
Пороги — в settings.
"""
from django.conf import settings
from django.db.models import F
from django.db.models.functions import Greatest, Least

from .models import Edge, EdgeReport, EdgeStatus, Player, ReportStatus


def is_quarantined(player: Player) -> bool:
    return player.trust < settings.TRACKER_TRUST_QUARANTINE


def is_reliable(player: Player) -> bool:
    return player.is_trusted or player.trust >= settings.TRACKER_TRUST_RELIABLE


def adjust_trust(player: Player, delta: float) -> None:
    """Атомарно меняет доверие в пределах [TRACKER_TRUST_MIN, TRACKER_TRUST_MAX]."""
    Player.objects.filter(pk=player.pk).update(
        trust=Greatest(Least(F("trust") + delta, settings.TRACKER_TRUST_MAX),
                       settings.TRACKER_TRUST_MIN),
    )
    player.refresh_from_db(fields=["trust"])


def penalize(player: Player | None, status: str) -> None:
    """Штраф за подозрительный или отклонённый отчёт."""
    if player is None:
        return
    if status == ReportStatus.SUSPICIOUS:
        adjust_trust(player, -settings.TRACKER_TRUST_SUSPICIOUS_PENALTY)
    elif status == ReportStatus.REJECTED:
        adjust_trust(player, -settings.TRACKER_TRUST_REJECTED_PENALTY)


def independent_reporters(edge: Edge) -> list[Player]:
    """Игроки, чьи отчёты о ребре считаются независимыми, в порядке поступления.

    Не считаются: отклонённые отчёты, забаненные и игроки в карантине,
    повторы того же игрока и отчёты с IP, который уже засчитан другому игроку.
    """
    seen_players: set[int] = set()
    seen_ips: set[str] = set()
    result = []
    reports = (
        edge.reports.exclude(status=ReportStatus.REJECTED)
        .filter(player__isnull=False)
        .select_related("player")
        .order_by("received_at", "pk")
    )
    for report in reports:
        player = report.player
        if player.pk in seen_players or player.banned or is_quarantined(player):
            continue
        if report.ip and report.ip in seen_ips:
            continue
        seen_players.add(player.pk)
        if report.ip:
            seen_ips.add(report.ip)
        result.append(player)
    return result


def refresh_edge_status(edge: Edge, report: EdgeReport) -> None:
    """Пересчитывает статус ребра после нового отчёта и начисляет доверие."""
    if not edge.reports.filter(player__isnull=False).exists():
        return  # старое ребро без отчётов игроков — статус не трогаем

    reporters = independent_reporters(edge)
    if (len(reporters) >= settings.TRACKER_CONFIRMATIONS_REQUIRED
            or any(is_reliable(p) for p in reporters)):
        edge.status = EdgeStatus.CONFIRMED
    elif reporters:
        edge.status = EdgeStatus.UNCONFIRMED
    else:
        # Сообщали только игроки в карантине или с одного IP с забаненными
        edge.status = EdgeStatus.DISPUTED
    edge.save(update_fields=["status"])

    # Новый независимый игрок подтвердил чужое ребро: доверие ему,
    # а если он второй — и тому, кто сообщил о ребре первым.
    player = report.player
    joined = (
        player is not None
        and any(p.pk == player.pk for p in reporters)
        and not edge.reports.filter(player=player).exclude(pk=report.pk)
        .exclude(status=ReportStatus.REJECTED).exists()
    )
    if joined and len(reporters) >= 2:
        adjust_trust(player, settings.TRACKER_TRUST_CONFIRM_BONUS)
        if len(reporters) == 2:
            first = reporters[0] if reporters[0].pk != player.pk else reporters[1]
            adjust_trust(first, settings.TRACKER_TRUST_CONFIRM_BONUS)
