
from django.conf import settings
from django.db import models
from django.utils import timezone


class EdgeStatus(models.TextChoices):
    UNCONFIRMED = "unconfirmed", "Не подтверждено"
    CONFIRMED = "confirmed", "Подтверждено"
    DISPUTED = "disputed", "Спорное"


class Edge(models.Model):
    """Живой (или истёкший) переход через Авалон между двумя зонами.

    Агрегат: одно ребро на пару зон на время жизни портала. Повторные
    отчёты о том же портале не создают новое ребро, а добавляют подтверждение.
    """
    source_id = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=64, db_index=True)

    # Время первого отчёта (first_seen)
    created_at = models.DateTimeField(default=timezone.now)
    # Время последнего отчёта
    last_seen = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    # Сколько всего отчётов о ребре и сколько разных игроков их прислали
    confirmations = models.PositiveIntegerField(default=1)
    reporters_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=EdgeStatus.choices,
                              default=EdgeStatus.UNCONFIRMED, db_index=True)

    class Meta:
        indexes = [  # noqa: RUF012
            models.Index(fields=["source_id", "target_id"]),
            models.Index(fields=["expires_at"]),
        ]

    @property
    def is_alive(self) -> bool:
        return self.expires_at > timezone.now()

    @staticmethod
    def normalize(a: str, b: str) -> tuple[str, str]:
        """Возвращает отсортированную пару. Источник — меньший ID."""
        return (a, b) if a <= b else (b, a)

    def __str__(self):
        return f"{self.source_id} → {self.target_id} (до {self.expires_at:%H:%M})"


class StaticEdge(models.Model):
    """Постоянный переход между двумя не-TNL локациями.

    Заполняется сканером, после завершения сканирования консервируется:
    рёбра с is_reference=True считаются эталоном, и новые статичные рёбра
    сверяются с ним.
    """
    source_id = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    is_reference = models.BooleanField("эталон", default=False, db_index=True)

    class Meta:
        unique_together = [("source_id", "target_id")]  # noqa: RUF012
        indexes = [  # noqa: RUF012
            models.Index(fields=["source_id"]),
            models.Index(fields=["target_id"]),
        ]

    @staticmethod
    def normalize(a: str, b: str) -> tuple[str, str]:
        """Возвращает отсортированную пару. Источник — меньший ID."""
        return (a, b) if a <= b else (b, a)

    def __str__(self):
        return f"{self.source_id} ↔ {self.target_id}"


class Player(models.Model):
    """Игрок, присылающий данные. Создаётся при первом входе через Discord."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                                related_name="player")
    discord_id = models.CharField(max_length=32, unique=True, null=True, blank=True)
    discord_username = models.CharField(max_length=100, blank=True)
    avatar_url = models.URLField(blank=True)

    # Доверие к данным игрока (растёт при подтверждениях, падает за подозрительные отчёты)
    trust = models.FloatField(default=0.0)
    # Выставляется вручную: отчётов такого игрока достаточно для подтверждения ребра
    is_trusted = models.BooleanField("доверенный", default=False)
    banned = models.BooleanField("забанен", default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.discord_username or self.user.get_username()


class DeviceToken(models.Model):
    """Токен одного устройства (одной установки трекера).

    Хранится только SHA-256 хеш, сам токен показывается клиенту один раз.
    """
    player = models.ForeignKey(Player, on_delete=models.CASCADE, related_name="tokens")
    name = models.CharField(max_length=100, blank=True)
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    client_version = models.CharField(max_length=32, blank=True)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def __str__(self):
        return f"{self.player} — {self.name or 'устройство'} #{self.pk}"


class DeviceLoginCode(models.Model):
    """Одноразовый код входа через браузер (device flow).

    Клиент держит device_code (у нас — только его хеш), человек подтверждает
    короткий user_code на сайте.
    """
    device_code_hash = models.CharField(max_length=64, unique=True)
    user_code = models.CharField(max_length=16, db_index=True)
    device_name = models.CharField(max_length=100, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    approved_by = models.ForeignKey(Player, on_delete=models.CASCADE, null=True, blank=True,
                                    related_name="login_codes")
    approved_at = models.DateTimeField(null=True, blank=True)
    # Заполняется, когда клиент забрал токен: повторно код не сработает
    token = models.OneToOneField(DeviceToken, on_delete=models.SET_NULL, null=True, blank=True)

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    def __str__(self):
        return self.user_code


class EdgeKind(models.TextChoices):
    DYNAMIC = "dynamic", "Авалон"
    STATIC = "static", "Статичное"


class ReportStatus(models.TextChoices):
    ACCEPTED = "accepted", "Принят"
    SUSPICIOUS = "suspicious", "Подозрительный"
    REJECTED = "rejected", "Отклонён"


class EdgeReport(models.Model):
    """Каждый присланный клиентом переход — как есть, для проверки и разбора."""
    # Пусто для отчётов через старый endpoint без авторизации
    player = models.ForeignKey(Player, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name="reports")
    token = models.ForeignKey(DeviceToken, on_delete=models.SET_NULL, null=True, blank=True,
                              related_name="reports")
    kind = models.CharField(max_length=16, choices=EdgeKind.choices)
    source_id = models.CharField(max_length=64)
    target_id = models.CharField(max_length=64)
    # Время из клиента
    created_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    ip = models.GenericIPAddressField(null=True, blank=True)

    status = models.CharField(max_length=16, choices=ReportStatus.choices,
                              default=ReportStatus.ACCEPTED, db_index=True)
    # Коды причин подозрения/отклонения, например ["path_gap", "too_fast"]
    reasons = models.JSONField(default=list, blank=True)

    edge = models.ForeignKey(Edge, on_delete=models.SET_NULL, null=True, blank=True,
                             related_name="reports")
    static_edge = models.ForeignKey(StaticEdge, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="reports")

    class Meta:
        indexes = [  # noqa: RUF012
            models.Index(fields=["player", "received_at"]),
        ]

    def __str__(self):
        return f"{self.source_id} → {self.target_id} ({self.get_status_display()})"
