import json
from datetime import timedelta
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from .accounts import (
    DISCORD_EPOCH_MS,
    discord_account_created_at,
    issue_token,
    sync_player_from_discord,
)
from .edges import register_dynamic_edge, register_static_edge
from .models import DeviceLoginCode, DeviceToken, Edge, EdgeReport, Player, StaticEdge
from .signals import create_player_on_discord_login


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


# --- Фаза 2: Discord, токены, вход через браузер ---------------------------

OLD_DISCORD_ID = "80351110224678912"  # аккаунт 2015 года


def young_discord_id() -> str:
    now_ms = int(timezone.now().timestamp() * 1000)
    return str((now_ms - DISCORD_EPOCH_MS) << 22)


def make_discord_player(name: str, discord_id: str = OLD_DISCORD_ID) -> Player:
    user = get_user_model().objects.create_user(username=name)
    return sync_player_from_discord(user, {"id": discord_id, "username": name, "avatar": "abc"})


class DiscordPlayerTests(TestCase):
    def test_login_signal_creates_and_updates_player(self):
        user = get_user_model().objects.create_user(username="u1")
        sociallogin = SimpleNamespace(account=SimpleNamespace(
            provider="discord",
            extra_data={"id": OLD_DISCORD_ID, "username": "old", "global_name": "Nick", "avatar": "h"},
        ))
        create_player_on_discord_login(None, request=None, user=user, sociallogin=sociallogin)
        sociallogin.account.extra_data["global_name"] = "NewNick"
        create_player_on_discord_login(None, request=None, user=user, sociallogin=sociallogin)

        player = Player.objects.get()
        self.assertEqual(player.discord_id, OLD_DISCORD_ID)
        self.assertEqual(player.discord_username, "NewNick")
        self.assertIn(f"/avatars/{OLD_DISCORD_ID}/h.png", player.avatar_url)

    def test_account_age_from_snowflake(self):
        self.assertEqual(discord_account_created_at(OLD_DISCORD_ID).year, 2015)


class DeviceFlowTests(TestCase):
    def start(self):
        r = self.client.post("/api/v1/device/start", {"device_name": "Мой ПК"},
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def poll(self, device_code):
        return self.client.post("/api/v1/device/poll", {"device_code": device_code},
                                content_type="application/json")

    def approve(self, player, user_code):
        self.client.force_login(player.user)
        r = self.client.post("/link/", {"code": user_code.lower().replace("-", "")})
        self.client.logout()
        return r

    def test_full_flow(self):
        player = make_discord_player("alice")
        start = self.start()
        self.assertIn(f"/link/?code={start['user_code']}", start["verification_url"])

        r = self.poll(start["device_code"])
        self.assertEqual(r.status_code, 428)
        self.assertEqual(r.json()["code"], "authorization_pending")

        self.assertContains(self.approve(player, start["user_code"]), "Трекер подключён")

        r = self.poll(start["device_code"])
        self.assertEqual(r.status_code, 200)
        token = r.json()["token"]
        self.assertTrue(token.startswith("atm_"))
        self.assertEqual(DeviceToken.objects.get().name, "Мой ПК")
        # Сам токен в базе не хранится
        self.assertFalse(DeviceToken.objects.filter(token_hash=token).exists())

        # Код одноразовый
        self.assertEqual(self.poll(start["device_code"]).json()["code"], "code_used")

        me = self.client.get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {token}",
                             HTTP_X_CLIENT_VERSION="1.2.0")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json()["discord_username"], "alice")
        self.assertEqual(DeviceToken.objects.get().client_version, "1.2.0")

    def test_expired_and_unknown_codes(self):
        start = self.start()
        DeviceLoginCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.poll(start["device_code"]).json()["code"], "code_expired")
        self.assertEqual(self.poll("nope").status_code, 404)

    def test_young_discord_account_is_denied(self):
        player = make_discord_player("fresh", discord_id=young_discord_id())
        start = self.start()
        self.assertContains(self.approve(player, start["user_code"]), "старше")
        self.assertEqual(self.poll(start["device_code"]).status_code, 428)

    def test_link_requires_login(self):
        r = self.client.get("/link/?code=ABC-DEF")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/accounts/login/", r["Location"])


class TokenAuthTests(TestCase):
    def setUp(self):
        self.player = make_discord_player("bob")
        self.token, self.raw = issue_token(self.player, "ПК")

    def me(self, raw):
        return self.client.get("/api/v1/me", HTTP_AUTHORIZATION=f"Bearer {raw}")

    def test_missing_and_wrong_token(self):
        self.assertEqual(self.client.get("/api/v1/me").status_code, 401)
        self.assertEqual(self.me("atm_wrong").status_code, 401)
        self.assertEqual(self.me(self.raw).status_code, 200)

    def test_revoked_token(self):
        self.client.force_login(self.player.user)
        self.client.post("/account/devices/", {"revoke": self.token.pk})
        self.client.logout()
        self.assertEqual(self.me(self.raw).status_code, 401)

    def test_banned_player(self):
        Player.objects.filter(pk=self.player.pk).update(banned=True)
        r = self.me(self.raw)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(r.json()["code"], "banned")

    def test_devices_page_lists_only_own_tokens(self):
        other = make_discord_player("eve", discord_id="80351110224678913")
        issue_token(other, "Чужой ПК")
        self.client.force_login(self.player.user)
        r = self.client.get("/account/devices/")
        self.assertContains(r, "ПК")
        self.assertNotContains(r, "Чужой ПК")
        # Чужой токен отключить нельзя
        self.client.post("/account/devices/", {"revoke": other.tokens.get().pk})
        self.assertIsNone(other.tokens.get().revoked_at)
