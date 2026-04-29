from rest_framework import viewsets
from vault.models import AuthFlow
from vault.serializers import AuthFlowSerializer

class AuthFlowViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing authentication flows.
    """
    queryset = AuthFlow.objects.all()
    serializer_class = AuthFlowSerializer
