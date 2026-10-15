"""Схлопывает дубли Edge, накопившиеся до агрегата.

Раньше каждый отчёт создавал новое ребро. Рёбра одной пары зон, чьи
интервалы жизни пересекаются, — это один и тот же портал: оставляем одно,
продлеваем ему expires_at, а число отчётов пишем в confirmations.
Все старые рёбра считаются подтверждёнными (присланы до введения проверок).
"""
from django.db import migrations


def merge_duplicates(apps, schema_editor):
    Edge = apps.get_model("albiontrackermap", "Edge")

    current = None
    to_delete = []
    for edge in Edge.objects.order_by("source_id", "target_id", "created_at"):
        same_portal = (
            current is not None
            and (edge.source_id, edge.target_id) == (current.source_id, current.target_id)
            and edge.created_at <= current.expires_at
        )
        if same_portal:
            current.expires_at = max(current.expires_at, edge.expires_at)
            current.last_seen = max(current.last_seen, edge.created_at)
            current.confirmations += 1
            to_delete.append(edge.pk)
            continue
        if current is not None:
            current.save()
        current = edge
        current.last_seen = edge.created_at
        current.confirmations = 1
        current.reporters_count = 1
        current.status = "confirmed"
    if current is not None:
        current.save()

    Edge.objects.filter(pk__in=to_delete).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("albiontrackermap", "0004_players_tokens_reports"),
    ]

    operations = [
        migrations.RunPython(merge_duplicates, migrations.RunPython.noop),
    ]
