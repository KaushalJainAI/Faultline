from django.contrib import admin

from campaigns.models import Campaign, Finding, ToolRun


@admin.register(Campaign)
class CampaignAdmin(admin.ModelAdmin):
    list_display = ("id", "status", "target_url", "created_at", "started_at", "finished_at")
    search_fields = ("id", "target_path", "target_url")
    list_filter = ("status", "created_at")


@admin.register(Finding)
class FindingAdmin(admin.ModelAdmin):
    list_display = ("title", "campaign", "category", "severity", "status", "created_at")
    search_fields = ("title", "summary", "evidence", "file_path")
    list_filter = ("category", "severity", "status")


@admin.register(ToolRun)
class ToolRunAdmin(admin.ModelAdmin):
    list_display = ("tool_name", "campaign", "status", "started_at", "finished_at")
    search_fields = ("tool_name", "input_summary", "output_summary", "error_message")
    list_filter = ("status", "tool_name")
