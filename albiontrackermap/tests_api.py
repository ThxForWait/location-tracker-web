"""Тесты эндпоинтов рёбер API v1: лимиты (уровень 2) и проверки правдоподобия (уровень 3)."""
from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from .accounts import issue_token
from .edges import register_dynamic_edge
from .models import Edge, EdgeReport, ReportStatus, StaticEdge
from .tests import make_discord_player
from .zones import ZoneKind, get_zone_kind


class ApiTestCase(TestCase):
    def setUp(self):
        cache.clear()  # счётчики лимитов не должны переходить между тестами
        self.player = make_discord_player("alice")
        _, self.raw = issue_token(self.player, "ПК")

    def post(self, path, payload, raw=None, version="1.0.0"):
        headers = {"HTTP_AUTHORIZATION": f"Bearer {raw or self.raw}"}
        if version:
            headers["HTTP_X_CLIENT_VERSION"] = version
        return self.client.post(f"/api/v1{path}", payload, content_type="application/json", **headers)

    def dynamic(self, source, target, created=None, lifetime=timedelta(hours=2), raw=None):
        created = created or timezone.now()
        return self.post("/edges/dynamic", {
            "source_id": source, "target_id": target,
            "created_at": created.isoformat(),
            "expires_at": (created + lifetime).isoformat(),
        }, raw=raw)

    def static(self, source, target, created=None):
        created = created or timezone.now()
        return self.post("/edges/static", {
            "source_id": source, "target_id": target, "created_at": created.isoformat(),
        })

    def last_report(self) -> EdgeReport:
        return EdgeReport.objects.order_by("-pk").first()


class ZoneKindTests(TestCase):
    def test_same_as_client(self):
        self.assertEqual(get_zone_kind("TNL-001"), ZoneKind.ROAD)
        self.assertEqual(get_zone_kind("3356"), ZoneKind.STATIC)
        self.assertEqual(get_zone_kind("1000"), ZoneKind.STATIC)
        self.assertEqual(get_zone_kind("ISLAND-PLAYER-0001a"), ZoneKind.INSTANCE)
        self.assertEqual(get_zone_kind("@HIDEOUT@4356@abc"), ZoneKind.INSTANCE)


class EdgeEndpointTests(ApiTestCase):
    def test_dynamic_created_then_confirmed_by_other_player(self):
        r1 = self.dynamic("TNL-001", "3356")
        self.assertEqual(r1.status_code, 201, r1.content)
        self.assertTrue(r1.json()["created"])

        bob = make_discord_player("bob", discord_id="80351110224678913")
        _, bob_raw = issue_token(bob)
        r2 = self.dynamic("3356", "TNL-001", raw=bob_raw)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["edge_id"], r1.json()["edge_id"])

        edge = Edge.objects.get()
        self.assertEqual((edge.confirmations, edge.reporters_count), (2, 2))
        report = self.last_report()
        self.assertEqual((report.player, report.status), (bob, ReportStatus.ACCEPTED))
        self.assertIsNotNone(report.token)

    def test_static_idempotent(self):
        self.assertEqual(self.static("1000", "1006").status_code, 201)
        # Второй отчёт через 10 секунд, чтобы не сработала проверка скорости
        self.assertEqual(self.static("1006", "1000", timezone.now() + timedelta(seconds=10)).status_code, 200)
        self.assertEqual(StaticEdge.objects.count(), 1)

    def test_requires_token(self):
        r = self.client.post("/api/v1/edges/dynamic", {}, content_type="application/json")
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["code"], "unauthorized")

    def test_invalid_payload(self):
        r = self.post("/edges/dynamic", {"source_id": "TNL-001"})
        self.assertEqual(r.status_code, 422)
        self.assertEqual(r.json()["code"], "invalid_payload")

    @override_settings(TRACKER_MIN_CLIENT_VERSION="1.0.0")
    def test_outdated_client(self):
        r = self.post("/edges/static", {}, version="0.9.5")
        self.assertEqual(r.status_code, 426)
        self.assertEqual(r.json()["code"], "client_outdated")
        self.assertEqual(self.post("/edges/static", {}, version=None).status_code, 426)


class RateLimitTests(ApiTestCase):
    def test_edges_per_minute(self):
        # Лимит 20/мин на токен; некорректные запросы тоже считаются
        for _ in range(20):
            self.assertEqual(self.post("/edges/static", {}).status_code, 422)
        r = self.post("/edges/static", {})
        self.assertEqual(r.status_code, 429)
        self.assertEqual(r.json()["code"], "rate_limited")
        self.assertIn("Retry-After", r)

        # Другой токен не затронут
        _, other = issue_token(self.player, "Ноутбук")
        self.assertEqual(self.post("/edges/static", {}, raw=other).status_code, 422)

    def test_device_start_per_ip(self):
        for _ in range(10):
            self.client.post("/api/v1/device/start", {}, content_type="application/json")
        r = self.client.post("/api/v1/device/start", {}, content_type="application/json")
        self.assertEqual(r.status_code, 429)


class RejectionTests(ApiTestCase):
    def assert_rejected(self, response, status, code):
        self.assertEqual(response.status_code, status, response.content)
        self.assertEqual(response.json()["code"], code)
        report = self.last_report()
        self.assertEqual((report.status, report.reasons), (ReportStatus.REJECTED, [code]))
        self.assertIsNone(report.edge)

    def test_unknown_and_same_zone(self):
        self.assert_rejected(self.dynamic("TNL-001", "NOPE"), 422, "unknown_zone")
        self.assert_rejected(self.dynamic("TNL-001", "TNL-001"), 422, "same_zone")

    def test_wrong_kind(self):
        self.assert_rejected(self.dynamic("1000", "1006"), 422, "wrong_kind")
        self.assert_rejected(self.static("TNL-001", "1000"), 422, "wrong_kind")
        self.assert_rejected(self.dynamic("TNL-001", "ISLAND-PLAYER-0001a"), 422, "wrong_kind")

    def test_time_window(self):
        now = timezone.now()
        self.assert_rejected(self.dynamic("TNL-001", "TNL-002", now + timedelta(minutes=2)),
                             422, "created_in_future")
        self.assert_rejected(self.dynamic("TNL-001", "TNL-002", now - timedelta(minutes=6)),
                             422, "too_old")
        # Небольшое расхождение часов допустимо
        self.assertEqual(self.dynamic("TNL-001", "TNL-002", now + timedelta(seconds=10)).status_code, 201)

    def test_invalid_lifetime(self):
        self.assert_rejected(self.dynamic("TNL-001", "TNL-002", lifetime=timedelta(hours=5)),
                             422, "invalid_lifetime")
        self.assert_rejected(self.dynamic("TNL-001", "TNL-002", lifetime=timedelta(0)),
                             422, "invalid_lifetime")

    @override_settings(TRACKER_STATIC_GRAPH_FROZEN=True)
    def test_frozen_static_graph(self):
        self.assert_rejected(self.static("1000", "1006"), 409, "not_in_reference")
        self.assertFalse(StaticEdge.objects.exists())

        StaticEdge.objects.create(source_id="1000", target_id="1006", is_reference=True)
        self.assertEqual(self.static("1006", "1000").status_code, 200)


class SuspiciousTests(ApiTestCase):
    def test_continuous_path_is_accepted(self):
        now = timezone.now() - timedelta(minutes=1)
        self.dynamic("3356", "TNL-001", now)
        self.dynamic("TNL-001", "TNL-002", now + timedelta(seconds=30))
        self.assertEqual(self.last_report().status, ReportStatus.ACCEPTED)

    def test_path_gap(self):
        now = timezone.now() - timedelta(minutes=1)
        self.dynamic("3356", "TNL-001", now)
        self.dynamic("TNL-005", "TNL-006", now + timedelta(seconds=30))
        report = self.last_report()
        self.assertEqual((report.status, report.reasons), (ReportStatus.SUSPICIOUS, ["path_gap"]))
        # Подозрительный отчёт всё равно попадает в граф
        self.assertIsNotNone(report.edge)

    def test_too_fast(self):
        now = timezone.now() - timedelta(minutes=1)
        self.dynamic("3356", "TNL-001", now)
        self.dynamic("TNL-001", "TNL-002", now + timedelta(seconds=2))
        self.assertEqual(self.last_report().reasons, ["too_fast"])

    def test_zone_overloaded(self):
        now = timezone.now()
        for i in range(2, 10):  # 8 живых выходов у TNL-001
            register_dynamic_edge("TNL-001", f"TNL-{i:03d}", now, now + timedelta(hours=1))
        self.dynamic("TNL-001", "TNL-010")
        self.assertEqual(self.last_report().reasons, ["zone_overloaded"])
