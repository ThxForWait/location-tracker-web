from allauth.account.signals import user_logged_in
from django.dispatch import receiver

from .accounts import sync_player_from_discord


@receiver(user_logged_in)
def create_player_on_discord_login(sender, request, user, sociallogin=None, **kwargs):
    """При каждом входе через Discord создаём/обновляем Player (имя и аватар могли смениться)."""
    if sociallogin is None or sociallogin.account.provider != "discord":
        return
    sync_player_from_discord(user, sociallogin.account.extra_data)
