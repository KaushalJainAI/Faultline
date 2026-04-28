from django.urls import path
from .views import StartCampaignView, ProjectMapView

urlpatterns = [
    path('start/', StartCampaignView.as_view(), name='start-campaign'),
    path('map/', ProjectMapView.as_view(), name='project-map'),
]
