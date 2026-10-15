"""API v1 на Django Ninja.

Подключено в config/urls.py по адресу /api/v1/, документация — /api/v1/docs.

Ошибки возвращаются как {"detail": "...", "code": "..."}.
"""
from datetime import datetime, timezone as dt_timezone

from django.conf import settings
from django.db import transaction
from django.urls import reverse
from ninja import Field, NinjaAPI, Schema
from ninja.errors import AuthenticationError, Throttled, ValidationError
from ninja.security import HttpBearer
from ninja.throttling import AnonRateThrottle, SimpleRateThrottle

from . import accounts
from .edges import register_dynamic_edge, register_static_edge
from .models import DeviceLoginCode, EdgeKind
from .validation import Rejection, save_rejected, validate_dynamic, validate_static


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
        # До разбора тела запроса: у старого клиента может быть другой формат
        check_client_version(request)
        accounts.touch_token(device_token, request.headers.get("X-Client-Version", ""))
        return device_token


api = NinjaAPI(
    title="Albion Tracker Map API",
    version="1",
    urls_namespace="api-v1",
    auth=TokenAuth(),
)


def error_response(request, status: int, code: str, detail: str, **extra):
    return api.create_response(request, {"detail": detail, "code": code, **extra}, status=status)


@api.exception_handler(ApiError)
def handle_api_error(request, exc: ApiError):
    return error_response(request, exc.status, exc.code, exc.detail)


@api.exception_handler(Rejection)
def handle_rejection(request, exc: Rejection):
    return error_response(request, exc.status, exc.code, exc.detail)


@api.exception_handler(AuthenticationError)
def handle_auth_error(request, exc):
    return error_response(request, 401, "unauthorized", "Нет токена или токен недействителен")


@api.exception_handler(Throttled)
def handle_throttled(request, exc: Throttled):
    # Заголовок Retry-After Ninja добавит сама
    return error_response(request, 429, "rate_limited", "Слишком много запросов")


@api.exception_handler(ValidationError)
def handle_validation_error(request, exc: ValidationError):
    return error_response(request, 422, "invalid_payload", "Некорректные данные запроса",
                          errors=exc.errors)


class TokenRateThrottle(SimpleRateThrottle):
    """Лимит запросов на один токен устройства (уровень 2)."""

    def __init__(self, rate: str, scope: str):
        self.scope = scope
        super().__init__(rate)

    def get_cache_key(self, request):
        token = getattr(request, "auth", None)
        if token is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": token.pk}


def edge_throttles():
    return [
        TokenRateThrottle(settings.TRACKER_RATE_EDGES_PER_MINUTE, "edges_minute"),
        TokenRateThrottle(settings.TRACKER_RATE_EDGES_PER_HOUR, "edges_hour"),
    ]


def parse_version(value: str) -> tuple[int, int, int]:
    """'1.2.3' → (1, 2, 3), '1.2' → (1, 2, 0); мусор → (0, 0, 0)."""
    try:
        parts = [int(part) for part in value.strip().lstrip("v").split(".")]
    except ValueError:
        parts = []
    return tuple((parts + [0, 0, 0])[:3])


def check_client_version(request) -> None:
    minimum = parse_version(settings.TRACKER_MIN_CLIENT_VERSION)
    if parse_version(request.headers.get("X-Client-Version", "0")) < minimum:
        raise ApiError(426, "client_outdated",
                       f"Трекер устарел, обновите его до версии {settings.TRACKER_MIN_CLIENT_VERSION}")


def client_ip(request) -> str | None:
    return request.META.get("REMOTE_ADDR") or None


def as_utc(value: datetime) -> datetime:
    """Время без часового пояса считаем UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_timezone.utc)
    return value


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


@api.post("/device/start", response=DeviceStartOut, auth=None,
          throttle=[AnonRateThrottle(settings.TRACKER_RATE_DEVICE_START)])
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
          auth=None, throttle=[AnonRateThrottle(settings.TRACKER_RATE_DEVICE_POLL)])
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


# --- Рёбра графа -----------------------------------------------------------

class DynamicEdgeIn(Schema):
    source_id: str = Field(min_length=1, max_length=64)
    target_id: str = Field(min_length=1, max_length=64)
    created_at: datetime
    expires_at: datetime


class StaticEdgeIn(Schema):
    source_id: str = Field(min_length=1, max_length=64)
    target_id: str = Field(min_length=1, max_length=64)
    created_at: datetime


class EdgeOut(Schema):
    edge_id: int
    source_id: str
    target_id: str
    created: bool
    status: str | None = None
    confirmations: int | None = None
    expires_at: datetime | None = None


EDGE_RESPONSES = {200: EdgeOut, 201: EdgeOut, 401: ErrorOut, 403: ErrorOut, 409: ErrorOut,
                  422: ErrorOut, 426: ErrorOut, 429: ErrorOut}


@api.post("/edges/dynamic", response=EDGE_RESPONSES, throttle=edge_throttles())
def create_dynamic_edge(request, payload: DynamicEdgeIn):
    """Переход через портал Авалона: хотя бы один конец — зона дорог (TNL).

    201 — новое ребро, 200 — подтверждение уже живого.
    """
    token = request.auth
    created_at, expires_at = as_utc(payload.created_at), as_utc(payload.expires_at)
    try:
        verdict = validate_dynamic(token.player, payload.source_id, payload.target_id,
                                   created_at, expires_at)
    except Rejection as rejection:
        save_rejected(token.player, token, client_ip(request), EdgeKind.DYNAMIC,
                      payload.source_id, payload.target_id, created_at, expires_at, rejection)
        raise

    edge, created = register_dynamic_edge(
        payload.source_id, payload.target_id, created_at, expires_at,
        player=token.player,
        report_meta={"token": token, "ip": client_ip(request),
                      "status": verdict.status, "reasons": verdict.reasons},
    )
    return (201 if created else 200), {
        "edge_id": edge.pk,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "created": created,
        "status": edge.status,
        "confirmations": edge.confirmations,
        "expires_at": edge.expires_at,
    }


@api.post("/edges/static", response=EDGE_RESPONSES, throttle=edge_throttles())
def create_static_edge(request, payload: StaticEdgeIn):
    """Постоянный переход между двумя открытыми зонами. Идемпотентно."""
    token = request.auth
    created_at = as_utc(payload.created_at)
    try:
        verdict = validate_static(token.player, payload.source_id, payload.target_id, created_at)
    except Rejection as rejection:
        save_rejected(token.player, token, client_ip(request), EdgeKind.STATIC,
                      payload.source_id, payload.target_id, created_at, None, rejection)
        raise

    edge, created = register_static_edge(
        payload.source_id, payload.target_id, created_at,
        player=token.player,
        report_meta={"token": token, "ip": client_ip(request),
                      "status": verdict.status, "reasons": verdict.reasons},
    )
    return (201 if created else 200), {
        "edge_id": edge.pk,
        "source_id": edge.source_id,
        "target_id": edge.target_id,
        "created": created,
    }
