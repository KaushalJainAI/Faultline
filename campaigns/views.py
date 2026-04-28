import uuid
import logging
import os
import threading
from pathlib import Path
from django.http import JsonResponse
from django.db.models import Count
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status

from campaigns.models import Campaign
from campaigns.serializers import (
    CampaignCreateSerializer,
    CampaignDetailSerializer,
    CampaignResponseSerializer,
    FindingSerializer,
    ProjectMapRequestSerializer,
)
from campaigns.services import run_campaign_pipeline
from skills.ast_grapher import ASTGrapher

logger = logging.getLogger("CampaignAPI")

class StartCampaignView(APIView):
    """
    Triggers the Aegis-Breaker LangGraph agent to start a new chaos campaign.
    """
    def post(self, request, *args, **kwargs):
        if not os.environ.get("OPENROUTER_API_KEY"):
            return Response(
                {"error": "OPENROUTER_API_KEY is required to start an autonomous campaign."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        serializer = CampaignCreateSerializer(data=request.data)
        if serializer.is_valid():
            payload_data = serializer.validated_data
            campaign = Campaign.objects.create(
                id=uuid.uuid4(),
                target_path=payload_data["target_path"],
                target_url=payload_data["target_url"],
                start_command=payload_data["start_command"],
                health_url=payload_data.get("health_url") or None,
                log_file=payload_data.get("log_file", "server.log"),
            )

            threading.Thread(target=run_campaign_pipeline, args=(str(campaign.id),), daemon=True).start()

            response_data = {
                "message": "Chaos campaign initiated successfully in the background.",
                "target": campaign.target_path,
                "campaign_id": str(campaign.id),
                "status": campaign.status,
                "tasks": ["Start target", "Index documentation", "Map structure", "Generate payloads", "Execute chaos run", "Write report"]
            }
            return Response(CampaignResponseSerializer(response_data).data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CampaignDetailView(APIView):
    def get(self, request, campaign_id, *args, **kwargs):
        try:
            campaign = Campaign.objects.annotate(finding_count=Count("findings")).get(id=campaign_id)
        except Campaign.DoesNotExist:
            return Response({"error": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(CampaignDetailSerializer(campaign).data)


class CampaignFindingsView(APIView):
    def get(self, request, campaign_id, *args, **kwargs):
        try:
            campaign = Campaign.objects.get(id=campaign_id)
        except Campaign.DoesNotExist:
            return Response({"error": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response(FindingSerializer(campaign.findings.all(), many=True).data)


class CampaignReportView(APIView):
    def get(self, request, campaign_id, *args, **kwargs):
        try:
            campaign = Campaign.objects.get(id=campaign_id)
        except Campaign.DoesNotExist:
            return Response({"error": "Campaign not found."}, status=status.HTTP_404_NOT_FOUND)
        if not campaign.report_path:
            return Response({"error": "Campaign report is not ready."}, status=status.HTTP_404_NOT_FOUND)
        report_path = Path(campaign.report_path)
        if not report_path.exists():
            return Response({"error": "Campaign report file was not found."}, status=status.HTTP_404_NOT_FOUND)
        return Response({"campaign_id": str(campaign.id), "report": report_path.read_text(encoding="utf-8")})

class ProjectMapView(APIView):
    """
    Generates a structural dependency map of the target directory.
    """
    def get(self, request, *args, **kwargs):
        serializer = ProjectMapRequestSerializer(data=request.query_params)
        if serializer.is_valid():
            path = serializer.validated_data['path']
            try:
                grapher = ASTGrapher(root_dir=path)
                graph = grapher.analyze_project()
                return JsonResponse(graph, safe=False)
            except Exception as e:
                return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
