from django.apps import AppConfig


class AlbiontrackermapConfig(AppConfig):
    name = 'albiontrackermap'

    def ready(self):
        from . import signals  # noqa: F401  регистрирует обработчики сигналов
