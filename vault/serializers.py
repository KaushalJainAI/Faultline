from rest_framework import serializers
from vault.models import AuthFlow

class AuthFlowSerializer(serializers.ModelSerializer):
    class Meta:
        model = AuthFlow
        fields = '__all__'
