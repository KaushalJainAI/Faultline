from rest_framework import serializers
from pathlib import Path

from campaigns.models import Campaign, Finding

def validate_existing_directory(value):
    path = Path(value).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise serializers.ValidationError("Path must be an existing directory.")
    return str(path)

class CampaignCreateSerializer(serializers.Serializer):
    execution_mode = serializers.ChoiceField(choices=["pipeline", "agent", "hybrid"], required=False, default="hybrid")
    target_path = serializers.CharField(required=True)
    target_url = serializers.URLField(required=True)
    start_command = serializers.CharField(required=True)
    health_url = serializers.URLField(required=False, allow_null=True, allow_blank=True)
    log_file = serializers.CharField(required=False, default="server.log")

    def validate_target_path(self, value):
        return validate_existing_directory(value)

    def validate_log_file(self, value):
        if not value:
            return "server.log"
        return value

class CampaignResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    target = serializers.CharField()
    campaign_id = serializers.CharField()
    status = serializers.CharField()
    tasks = serializers.ListField(child=serializers.CharField())

class ProjectMapRequestSerializer(serializers.Serializer):
    path = serializers.CharField(required=True)

    def validate_path(self, value):
        return validate_existing_directory(value)


class CampaignDetailSerializer(serializers.ModelSerializer):
    finding_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Campaign
        fields = [
            "id",
            "status",
            "execution_mode",
            "target_path",
            "target_url",
            "start_command",
            "health_url",
            "log_file",
            "created_at",
            "started_at",
            "finished_at",
            "error_message",
            "report_path",
            "finding_count",
        ]


class FindingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Finding
        fields = [
            "id",
            "title",
            "category",
            "severity",
            "status",
            "summary",
            "evidence",
            "reproduction_steps",
            "suggested_fix",
            "file_path",
            "line_number",
            "created_at",
        ]
