from django.contrib import admin
from django.utils import timezone

from .models import DeviceLoginCode, DeviceToken, Edge, EdgeReport, Player, StaticEdge


@admin.register(Edge)
class EdgeAdmin(admin.ModelAdmin):
    list_display = ("source_id", "target_id", "status", "confirmations", "reporters_count",
                    "created_at", "last_seen", "expires_at")
    list_filter = ("status", "created_at")
    search_fields = ("source_id", "target_id")
    readonly_fields = ("created_at",)


@admin.register(StaticEdge)
class StaticEdgeAdmin(admin.ModelAdmin):
    list_display = ("source_id", "target_id", "is_reference", "created_at")
    list_filter = ("is_reference",)
    search_fields = ("source_id", "target_id")
    readonly_fields = ("created_at",)
    actions = ("mark_reference", "unmark_reference")

    @admin.action(description="Отметить как эталон")
    def mark_reference(self, request, queryset):
        queryset.update(is_reference=True)

    @admin.action(description="Снять отметку эталона")
    def unmark_reference(self, request, queryset):
        queryset.update(is_reference=False)


class DeviceTokenInline(admin.TabularInline):
    model = DeviceToken
    extra = 0
    fields = ("name", "client_version", "created_at", "last_used_at", "revoked_at")
    readonly_fields = ("name", "client_version", "created_at", "last_used_at")


@admin.register(Player)
class PlayerAdmin(admin.ModelAdmin):
    list_display = ("__str__", "discord_id", "trust", "is_trusted", "banned", "created_at")
    list_filter = ("is_trusted", "banned")
    search_fields = ("discord_username", "discord_id", "user__username")
    inlines = (DeviceTokenInline,)
    actions = ("ban", "unban", "mark_trusted", "revoke_tokens")

    @admin.action(description="Забанить")
    def ban(self, request, queryset):
        queryset.update(banned=True)

    @admin.action(description="Разбанить")
    def unban(self, request, queryset):
        queryset.update(banned=False)

    @admin.action(description="Сделать доверенным")
    def mark_trusted(self, request, queryset):
        queryset.update(is_trusted=True)

    @admin.action(description="Отозвать все токены")
    def revoke_tokens(self, request, queryset):
        DeviceToken.objects.filter(player__in=queryset, revoked_at__isnull=True).update(
            revoked_at=timezone.now())


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ("__str__", "player", "client_version", "created_at", "last_used_at", "revoked_at")
    list_filter = ("revoked_at",)
    search_fields = ("player__discord_username", "name")
    exclude = ("token_hash",)
    readonly_fields = ("player", "created_at", "last_used_at", "client_version")


@admin.register(DeviceLoginCode)
class DeviceLoginCodeAdmin(admin.ModelAdmin):
    list_display = ("user_code", "device_name", "created_at", "expires_at", "approved_by", "token")
    exclude = ("device_code_hash",)


@admin.register(EdgeReport)
class EdgeReportAdmin(admin.ModelAdmin):
    list_display = ("source_id", "target_id", "kind", "status", "player", "created_at",
                    "received_at", "ip")
    list_filter = ("status", "kind", "received_at")
    search_fields = ("source_id", "target_id", "player__discord_username")
    readonly_fields = [f.name for f in EdgeReport._meta.fields]
