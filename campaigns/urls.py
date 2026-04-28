from django.urls import path
from .views import CampaignDetailView, CampaignFindingsView, CampaignReportView, ProjectMapView, StartCampaignView

urlpatterns = [
    path('start/', StartCampaignView.as_view(), name='start-campaign'),
    path('map/', ProjectMapView.as_view(), name='project-map'),
    path('<uuid:campaign_id>/', CampaignDetailView.as_view(), name='campaign-detail'),
    path('<uuid:campaign_id>/findings/', CampaignFindingsView.as_view(), name='campaign-findings'),
    path('<uuid:campaign_id>/report/', CampaignReportView.as_view(), name='campaign-report'),
]
