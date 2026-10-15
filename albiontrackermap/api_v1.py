"""API v1 на Django Ninja.

Подключено в config/urls.py по адресу /api/v1/, документация — /api/v1/docs.
Старый endpoint /map/api/packet/ (albiontrackermap/api.py) работает параллельно,
пока клиенты не перейдут на v1.

Ошибки возвращаются как {"detail": "...", "code": "..."}.
"""
from django.conf import settings
from django.db import transaction
from django.urls import reverse
from ninja import NinjaAPI, Schema
from ninja.security import HttpBearer

from . import accounts
from .models import DeviceLoginCode


class ApiError(Exception):
    def __init__(self, status: int, code: str, detail: str):
        super().__init__(detail)
        self.status = status
        self.code = code
        self.detail = detail


class TokenAuth(HttpBearer):
    """Authorization: Bearer atm_…  →  request.auth = DeviceToken."""

    def authenticate(self, request, token):
        device_token = accounts.find_active_token(token)
        if device_token is None:
            return None  # → 401
        if device_token.player.banned:
            raise ApiError(403, "banned", "Аккаунт заблокирован")
        accounts.touch_token(device_token, request.headers.get("X-Client-Version", ""))
        return device_token


api = NinjaAPI(
    title="Albion Tracker Map API",
    version="1",
    urls_namespace="api-v1",
    auth=TokenAuth(),
)


@api.exception_handler(ApiError)
def handle_api_error(request, exc: ApiError):
    return api.create_response(request, {"detail": exc.detail, "code": exc.code}, status=exc.status)


class ErrorOut(Schema):
    detail: str
    code: str


# --- Служебное -------------------------------------------------------------

class HealthOut(Schema):
    ok: bool


@api.get("/health", response=HealthOut, auth=None)
def health(request):
    """Проверка доступности сервера, без авторизации."""
    return {"ok": True}


class MeOut(Schema):
    discord_id: str | None
    discord_username: str
    device_name: str
    is_trusted: bool


@api.get("/me", response=MeOut)
def me(request):
    """Кому принадлежит токен — клиент проверяет им, что токен рабочий."""
    token = request.auth
    return {
        "discord_id": token.player.discord_id,
        "discord_username": token.player.discord_username,
        "device_name": token.name,
        "is_trusted": token.player.is_trusted,
    }


# --- Вход через браузер (device flow) --------------------------------------

class DeviceStartIn(Schema):
    device_name: str = ""


class DeviceStartOut(Schema):
    device_code: str
    user_code: str
    verification_url: str
    expires_in: int
    interval: int


@api.post("/device/start", response=DeviceStartOut, auth=None)
def device_start(request, payload: DeviceStartIn):
    """Шаг 1: клиент получает код и открывает verification_url в браузере."""
    login, device_code = accounts.start_device_login(payload.device_name)
    url = request.build_absolute_uri(reverse("link")) + f"?code={login.user_code}"
    return {
        "device_code": device_code,
        "user_code": login.user_code,
        "verification_url": url,
        "expires_in": settings.DEVICE_CODE_TTL_SECONDS,
        "interval": settings.DEVICE_CODE_POLL_INTERVAL_SECONDS,
    }


class DevicePollIn(Schema):
    device_code: str


class DevicePollOut(Schema):
    token: str
    discord_username: str


@api.post("/device/poll", response={200: DevicePollOut, 404: ErrorOut, 410: ErrorOut, 428: ErrorOut},
          auth=None)
@transaction.atomic
def device_poll(request, payload: DevicePollIn):
    """Шаг 2: клиент раз в interval секунд спрашивает, подтвердил ли человек код.

    428 — ещё ждём, 200 — токен выдан, 410 — код истёк или уже использован.
    """
    login = (
        DeviceLoginCode.objects.select_for_update()
        .select_related("approved_by")
        .filter(device_code_hash=accounts.hash_secret(payload.device_code))
        .first()
    )
    if login is None:
        raise ApiError(404, "unknown_code", "Код не найден")
    if login.token_id is not None:
        raise ApiError(410, "code_used", "Код уже использован")
    if login.is_expired:
        raise ApiError(410, "code_expired", "Код истёк, начните вход заново")
    if login.approved_by is None:
        raise ApiError(428, "authorization_pending", "Ожидаем подтверждения на сайте")

    reason = accounts.token_denial_reason(login.approved_by)
    if reason:
        raise ApiError(403, "denied", reason)

    token, raw = accounts.issue_token(login.approved_by, login.device_name)
    login.token = token
    login.save(update_fields=["token"])
    return {"token": raw, "discord_username": login.approved_by.discord_username}
