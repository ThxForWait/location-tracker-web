"""Тесты доверия к игрокам и подтверждения рёбер (уровень 4)."""
from datetime import timedelta

from django.test import TestCase, override_settings
from django.utils import timezone

from .edges import register_dynamic_edge
from .models import Edge, EdgeStatus, Player, ReportStatus
from .tests import make_discord_player


def report(player, ip, source="TNL-001", target="TNL-002", status=ReportStatus.ACCEPTED):
    now = timezone.now()
    edge, _ = register_dynamic_edge(source, target, now, now + timedelta(hours=2), player=player,
                                    report_meta={"ip": ip, "status": status})
    edge.refresh_from_db()
    return edge


def trust_of(player: Player) -> float:
    player.refresh_from_db()
    return player.trust


class ConfirmationTests(TestCase):
    def setUp(self):
        self.alice = make_discord_player("alice", discord_id="80351110224678912")
        self.bob = make_discord_player("bob", discord_id="80351110224678913")
        self.eve = make_discord_player("eve", discord_id="80351110224678914")

    def test_single_reporter_is_unconfirmed(self):
        self.assertEqual(report(self.alice, "1.1.1.1").status, EdgeStatus.UNCONFIRMED)

    def test_two_independent_players_confirm(self):
        report(self.alice, "1.1.1.1")
        edge = report(self.bob, "2.2.2.2")
        self.assertEqual(edge.status, EdgeStatus.CONFIRMED)
        # Оба получили доверие: Боб подтвердил ребро Алисы
        self.assertEqual(trust_of(self.alice), 1.0)
        self.assertEqual(trust_of(self.bob), 1.0)
        # Третий игрок получает бонус только себе
        report(self.eve, "3.3.3.3")
        self.assertEqual((trust_of(self.alice), trust_of(self.eve)), (1.0, 1.0))

    def test_same_ip_is_not_independent(self):
        report(self.alice, "1.1.1.1")
        edge = report(self.bob, "1.1.1.1")
        self.assertEqual(edge.status, EdgeStatus.UNCONFIRMED)
        self.assertEqual(trust_of(self.alice), 0.0)

    def test_repeat_by_same_player_does_not_confirm(self):
        report(self.alice, "1.1.1.1")
        edge = report(self.alice, "2.2.2.2")
        self.assertEqual(edge.status, EdgeStatus.UNCONFIRMED)

    def test_trusted_player_confirms_alone(self):
        Player.objects.filter(pk=self.alice.pk).update(is_trusted=True)
        self.alice.refresh_from_db()
        self.assertEqual(report(self.alice, "1.1.1.1").status, EdgeStatus.CONFIRMED)

    @override_settings(TRACKER_TRUST_RELIABLE=3.0)
    def test_high_trust_confirms_alone(self):
        Player.objects.filter(pk=self.alice.pk).update(trust=3.0)
        self.alice.refresh_from_db()
        self.assertEqual(report(self.alice, "1.1.1.1").status, EdgeStatus.CONFIRMED)


class QuarantineTests(TestCase):
    def setUp(self):
        self.mallory = make_discord_player("mallory", discord_id="80351110224678915")
        self.bob = make_discord_player("bob", discord_id="80351110224678913")
        Player.objects.filter(pk=self.mallory.pk).update(trust=-6.0)
        self.mallory.refresh_from_db()

    def test_quarantined_reports_are_hidden(self):
        edge = report(self.mallory, "6.6.6.6")
        self.assertEqual(edge.status, EdgeStatus.DISPUTED)
        r = self.client.get("/map/")
        self.assertNotContains(r, "TNL-001")

    def test_good_player_revives_disputed_edge(self):
        report(self.mallory, "6.6.6.6")
        edge = report(self.bob, "2.2.2.2")
        # Отчёт Мэллори не считается, остаётся один независимый игрок
        self.assertEqual(edge.status, EdgeStatus.UNCONFIRMED)
        self.assertEqual(trust_of(self.bob), 0.0)


class PenaltyTests(TestCase):
    def test_suspicious_report_lowers_trust(self):
        player = make_discord_player("alice")
        report(player, "1.1.1.1", status=ReportStatus.SUSPICIOUS)
        self.assertEqual(trust_of(player), -0.5)

    @override_settings(TRACKER_TRUST_MIN=-1.0)
    def test_trust_has_floor(self):
        player = make_discord_player("alice")
        for _ in range(5):
            report(player, "1.1.1.1", status=ReportStatus.SUSPICIOUS)
        self.assertEqual(trust_of(player), -1.0)


class MapTests(TestCase):
    def test_unconfirmed_edge_is_dashed(self):
        report(make_discord_player("alice"), "1.1.1.1")
        r = self.client.get("/map/")
        self.assertContains(r, '"confirmed": false')
        self.assertContains(r, "dashes: !e.confirmed")

    def test_old_edges_without_reports_keep_status(self):
        now = timezone.now()
        Edge.objects.create(source_id="TNL-001", target_id="TNL-002", created_at=now,
                            last_seen=now, expires_at=now + timedelta(hours=1),
                            status=EdgeStatus.CONFIRMED)
        self.assertContains(self.client.get("/map/"), '"confirmed": true')
