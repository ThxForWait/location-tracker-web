"""Страницы сайта для игроков: подтверждение входа трекера и список устройств."""
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils import timezone

from . import accounts
from .models import Player


def _player(user) -> Player | None:
    return Player.objects.filter(user=user).first()


@login_required
def link(request):
    """Человек подтверждает код, который показал трекер (device flow, шаг на сайте)."""
    player = _player(request.user)
    context = {"code": request.GET.get("code", ""), "player": player,
               "denial": accounts.token_denial_reason(player)}

    if request.method == "POST" and not context["denial"]:
        code = request.POST.get("code", "")
        context["code"] = code
        login = accounts.find_pending_code(code)
        if login is None:
            context["error"] = "Код не найден или истёк. Запустите вход в трекере заново."
        else:
            accounts.approve_device_login(login, player)
            context["approved"] = True

    return render(request, "trackermap/link.html", context)


@login_required
def devices(request):
    """Список токенов игрока с возможностью отключить устройство."""
    player = _player(request.user)
    if request.method == "POST" and player is not None:
        player.tokens.filter(pk=request.POST.get("revoke"), revoked_at__isnull=True).update(
            revoked_at=timezone.now())
        return redirect("devices")

    tokens = player.tokens.filter(revoked_at__isnull=True).order_by("-created_at") if player else []
    return render(request, "trackermap/devices.html", {"player": player, "tokens": tokens})
