
from django.db import models
from django.utils import timezone


class Edge(models.Model):
    source_id = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=64, db_index=True)

    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [  # noqa: RUF012
            models.Index(fields=["source_id", "target_id"]),
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

    Заполняется сканером, после завершения сканирования консервируется.
    """
    source_id = models.CharField(max_length=64, db_index=True)
    target_id = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)

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
