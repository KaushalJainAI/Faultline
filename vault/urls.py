from django.urls import path, include
from rest_framework.routers import DefaultRouter
from vault.views import AuthFlowViewSet

router = DefaultRouter()
router.register(r'auth-flows', AuthFlowViewSet)

urlpatterns = [
    path('', include(router.urls)),
]
