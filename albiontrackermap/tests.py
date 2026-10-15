import json
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .edges import register_dynamic_edge, register_static_edge
from .models import Edge, EdgeReport, Player, StaticEdge


def make_player(name: str) -> Player:
    user = get_user_model().objects.create_user(username=name)
    return Player.objects.create(user=user, discord_id=name, discord_username=name)


class DynamicEdgeTests(TestCase):
    def test_repeated_report_confirms_instead_of_duplicating(self):
        now = timezone.now()
        alice, bob = make_player("alice"), make_player("bob")

        edge1, created1 = register_dynamic_edge("TNL-002", "TNL-001", now, now + timedelta(hours=2),
                                                player=alice)
        # Тот же портал в обратную сторону, другим игроком
        edge2, created2 = register_dynamic_edge("TNL-001", "TNL-002", now, now + timedelta(hours=3),
                                                player=bob)
        # Повтор того же игрока не увеличивает число разных игроков
        register_dynamic_edge("TNL-001", "TNL-002", now, now + timedelta(hours=1), player=bob)

        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(edge1.pk, edge2.pk)
        self.assertEqual(Edge.objects.count(), 1)
        edge = Edge.objects.get()
        self.assertEqual((edge.source_id, edge.target_id), ("TNL-001", "TNL-002"))
        self.assertEqual(edge.confirmations, 3)
        self.assertEqual(edge.reporters_count, 2)
        self.assertEqual(edge.expires_at, now + timedelta(hours=3))
        self.assertEqual(EdgeReport.objects.filter(edge=edge).count(), 3)

    def test_expired_edge_is_not_reused(self):
        past = timezone.now() - timedelta(hours=3)
        register_dynamic_edge("TNL-001", "TNL-002", past, past + timedelta(hours=2))
        now = timezone.now()
        _, created = register_dynamic_edge("TNL-001", "TNL-002", now, now + timedelta(hours=2))
        self.assertTrue(created)
        self.assertEqual(Edge.objects.count(), 2)


class StaticEdgeTests(TestCase):
    def test_idempotent_and_reported(self):
        now = timezone.now()
        _, created1 = register_static_edge("1006", "1000", now)
        _, created2 = register_static_edge("1000", "1006", now)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(StaticEdge.objects.count(), 1)
        self.assertEqual(EdgeReport.objects.count(), 2)


class LegacyEndpointTests(TestCase):
    url = "/map/api/packet/"

    def post(self, payload):
        return self.client.post(self.url, json.dumps(payload), content_type="application/json")

    def test_connect_twice_gives_one_edge(self):
        now = timezone.now().replace(microsecond=0)
        payload = {
            "command": 1,
            "source_id": "TNL-001",
            "target_id": "TNL-002",
            "created_at": now.isoformat(),
            "expires_at": (now + timedelta(hours=2)).isoformat(),
        }
        r1 = self.post(payload)
        r2 = self.post(payload)
        self.assertEqual(r1.status_code, 201)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.json()["edge_id"], r2.json()["edge_id"])
        self.assertEqual(Edge.objects.get().confirmations, 2)
        self.assertEqual(EdgeReport.objects.filter(player__isnull=True).count(), 2)

    def test_static_edge(self):
        payload = {"command": 2, "source_id": "1000", "target_id": "1006",
                   "created_at": timezone.now().isoformat()}
        self.assertEqual(self.post(payload).status_code, 201)
        self.assertEqual(self.post(payload).status_code, 200)


class HealthTests(TestCase):
    def test_health(self):
        r = self.client.get("/api/v1/health")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"ok": True})
