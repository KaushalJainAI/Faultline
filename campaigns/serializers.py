from rest_framework import serializers

class CampaignCreateSerializer(serializers.Serializer):
    target_path = serializers.CharField(required=True)
    target_url = serializers.CharField(required=True)
    start_command = serializers.CharField(required=True)
    health_url = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    log_file = serializers.CharField(required=False, default="server.log")

class CampaignResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    target = serializers.CharField()
    campaign_id = serializers.CharField()
    tasks = serializers.ListField(child=serializers.CharField())

class ProjectMapRequestSerializer(serializers.Serializer):
    path = serializers.CharField(required=True)
