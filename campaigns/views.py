import uuid
import logging
import asyncio
from django.http import JsonResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from asgiref.sync import sync_to_async

from campaigns.serializers import CampaignCreateSerializer, CampaignResponseSerializer, ProjectMapRequestSerializer
from skills.ast_grapher import ASTGrapher
from core.agent import AegisAgent

logger = logging.getLogger("CampaignAPI")

async def run_agent_background(campaign_id: str, payload_data: dict):
    """Background task to run the full chaos campaign."""
    try:
        agent = AegisAgent()
        logger.info(f"Background campaign {campaign_id} started.")
        await agent.run_campaign(
            target_dir=payload_data['target_path'],
            target_url=payload_data['target_url'],
            log_file=payload_data.get('log_file', 'server.log'),
            initial_prompt=f"Start a chaos campaign against {payload_data['target_path']}."
        )
        logger.info(f"Background campaign {campaign_id} completed.")
    except Exception as e:
        logger.error(f"Campaign {campaign_id} failed: {e}")

class StartCampaignView(APIView):
    """
    Triggers the Aegis-Breaker LangGraph agent to start a new chaos campaign.
    """
    def post(self, request, *args, **kwargs):
        serializer = CampaignCreateSerializer(data=request.data)
        if serializer.is_valid():
            payload_data = serializer.validated_data
            campaign_id = str(uuid.uuid4())
            
            # Fire and forget the agent in the background
            # Note: in a true production DRF app you might use Celery. 
            # We are using asyncio.create_task assuming an ASGI server (like uvicorn or daphne).
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(run_agent_background(campaign_id, payload_data))
            except RuntimeError:
                # Fallback to threading if not running in an async event loop
                import threading
                def thread_runner():
                    asyncio.run(run_agent_background(campaign_id, payload_data))
                threading.Thread(target=thread_runner, daemon=True).start()

            response_data = {
                "message": "Chaos campaign initiated successfully in the background.",
                "target": payload_data['target_path'],
                "campaign_id": campaign_id,
                "tasks": ["Indexing", "Vulnerability Mapping", "Payload Generation"]
            }
            return Response(CampaignResponseSerializer(response_data).data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

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
