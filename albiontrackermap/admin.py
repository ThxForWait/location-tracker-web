from django.contrib import admin

from .models import Edge, StaticEdge


@admin.register(Edge)
class EdgeAdmin(admin.ModelAdmin):
    list_display = ("source_id", "target_id", "created_at", "expires_at")
    list_filter = ("created_at",)
    search_fields = ("source_id", "target_id")
    readonly_fields = ("created_at",)


@admin.register(StaticEdge)
class StaticEdgeAdmin(admin.ModelAdmin):
    list_display = ("source_id", "target_id", "created_at")
    search_fields = ("source_id", "target_id")
    readonly_fields = ("created_at",)
