from rest_framework import serializers
from vault.models import AuthFlow

class AuthFlowSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuthFlow
        fields = '__all__'

    def validate(self, data):
        auth_type = data.get('auth_type')
        auth_url = data.get('auth_url')
        if auth_type == AuthFlow.AuthType.LOGIN_ENDPOINT and not auth_url:
            raise serializers.ValidationError({"auth_url": "This field is required when auth_type is LOGIN_ENDPOINT."})
        return data
