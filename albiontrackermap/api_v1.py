"""API v1 на Django Ninja.

Подключено в config/urls.py по адресу /api/v1/, документация — /api/v1/docs.
Старый endpoint /map/api/packet/ (albiontrackermap/api.py) работает параллельно,
пока клиенты не перейдут на v1.
"""
from ninja import NinjaAPI, Schema

api = NinjaAPI(
    title="Albion Tracker Map API",
    version="1",
    urls_namespace="api-v1",
)


class HealthOut(Schema):
    ok: bool


@api.get("/health", response=HealthOut, auth=None)
def health(request):
    """Проверка доступности сервера, без авторизации."""
    return {"ok": True}
