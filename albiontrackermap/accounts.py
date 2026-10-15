"""Игроки, токены устройств и вход через браузер (device flow)."""
import hashlib
import secrets
from datetime import datetime, timedelta, timezone as dt_timezone

from django.conf import settings
from django.utils import timezone

from .models import DeviceLoginCode, DeviceToken, Player

TOKEN_PREFIX = "atm_"
# Без похожих символов (0/O, 1/I/L), чтобы код было легко перепечатать
USER_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"

DISCORD_EPOCH_MS = 1420070400000


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


# --- Игроки ----------------------------------------------------------------

def sync_player_from_discord(user, discord_data: dict) -> Player:
    """Создаёт или обновляет Player по данным Discord (extra_data из allauth)."""
    discord_id = str(discord_data.get("id", ""))
    username = discord_data.get("global_name") or discord_data.get("username") or ""
    avatar = discord_data.get("avatar")
    avatar_url = f"https://cdn.discordapp.com/avatars/{discord_id}/{avatar}.png" if avatar else ""

    player, _ = Player.objects.get_or_create(user=user)
    player.discord_id = discord_id or None
    player.discord_username = username
    player.avatar_url = avatar_url
    player.save()
    return player


def discord_account_created_at(discord_id: str) -> datetime | None:
    """Дата создания Discord-аккаунта: она зашита в ID (snowflake)."""
    try:
        ms = (int(discord_id) >> 22) + DISCORD_EPOCH_MS
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(ms / 1000, tz=dt_timezone.utc)


def token_denial_reason(player: Player | None) -> str | None:
    """Почему игроку нельзя выдать токен, или None, если можно."""
    if player is None or not player.discord_id:
        return "Войдите на сайт через Discord."
    if player.banned:
        return "Ваш аккаунт заблокирован."
    created = discord_account_created_at(player.discord_id)
    min_age = timedelta(days=settings.DISCORD_MIN_ACCOUNT_AGE_DAYS)
    if created is None or timezone.now() - created < min_age:
        return (f"Discord-аккаунт должен быть старше "
                f"{settings.DISCORD_MIN_ACCOUNT_AGE_DAYS} дней.")
    return None


# --- Токены ----------------------------------------------------------------

def issue_token(player: Player, name: str = "") -> tuple[DeviceToken, str]:
    """Создаёт токен устройства. Сам токен возвращается один раз, хранится только хеш."""
    raw = TOKEN_PREFIX + secrets.token_urlsafe(32)
    token = DeviceToken.objects.create(player=player, name=name[:100], token_hash=hash_secret(raw))
    return token, raw


def find_active_token(raw: str) -> DeviceToken | None:
    if not raw.startswith(TOKEN_PREFIX):
        return None
    return (
        DeviceToken.objects.select_related("player")
        .filter(token_hash=hash_secret(raw), revoked_at__isnull=True)
        .first()
    )


def touch_token(token: DeviceToken, client_version: str = "") -> None:
    """Обновляет last_used_at не чаще раза в минуту, чтобы не писать в базу на каждый запрос."""
    now = timezone.now()
    fields = {}
    if token.last_used_at is None or now - token.last_used_at > timedelta(minutes=1):
        fields["last_used_at"] = now
    if client_version and client_version[:32] != token.client_version:
        fields["client_version"] = client_version[:32]
    if fields:
        DeviceToken.objects.filter(pk=token.pk).update(**fields)
        for key, value in fields.items():
            setattr(token, key, value)


# --- Вход через браузер ----------------------------------------------------

def normalize_user_code(code: str) -> str:
    """'k7f2qx', 'K7F-2QX ' → 'K7F-2QX'."""
    clean = "".join(ch for ch in code.upper() if ch.isalnum())
    return f"{clean[:3]}-{clean[3:]}" if len(clean) == 6 else clean


def start_device_login(device_name: str = "") -> tuple[DeviceLoginCode, str]:
    """Создаёт код входа. Возвращает (код, device_code для клиента)."""
    device_code = secrets.token_urlsafe(32)
    while True:
        raw = "".join(secrets.choice(USER_CODE_ALPHABET) for _ in range(6))
        user_code = f"{raw[:3]}-{raw[3:]}"
        # Код должен быть уникален среди ещё живых
        if not DeviceLoginCode.objects.filter(user_code=user_code,
                                              expires_at__gt=timezone.now()).exists():
            break
    login = DeviceLoginCode.objects.create(
        device_code_hash=hash_secret(device_code),
        user_code=user_code,
        device_name=device_name[:100],
        expires_at=timezone.now() + timedelta(seconds=settings.DEVICE_CODE_TTL_SECONDS),
    )
    return login, device_code


def find_pending_code(user_code: str) -> DeviceLoginCode | None:
    """Живой, ещё не подтверждённый код по тому, что ввёл человек."""
    return (
        DeviceLoginCode.objects.filter(
            user_code=normalize_user_code(user_code),
            expires_at__gt=timezone.now(),
            approved_by__isnull=True,
        )
        .first()
    )


def approve_device_login(login: DeviceLoginCode, player: Player) -> None:
    login.approved_by = player
    login.approved_at = timezone.now()
    login.save(update_fields=["approved_by", "approved_at"])
