from django.contrib import admin

from vault.models import AuthFlow


@admin.register(AuthFlow)
class AuthFlowAdmin(admin.ModelAdmin):
    list_display = ("name", "auth_type", "injection_type", "injection_key", "created_at")
    list_filter = ("auth_type", "injection_type", "created_at")
    search_fields = ("name", "auth_url", "injection_key")
    readonly_fields = ("created_at", "updated_at")
    date_hierarchy = "created_at"
