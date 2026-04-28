import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from campaigns.models import Campaign, Finding
from campaigns.services import generate_campaign_report, run_campaign_pipeline
from core.cli_provider import ClaudeAdapter, CodexAdapter, GeminiAdapter, ProviderManager
from skills.ast_grapher import ASTGrapher
from skills.guardrails import GuardrailValidator
from skills.qa_engineer import QAEngineer


class CampaignAPITests(TestCase):
    def test_project_map_rejects_missing_directory(self):
        response = self.client.get(reverse("project-map"), {"path": "C:/definitely/not/here"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("path", response.json())

    def test_start_campaign_validates_target_url(self):
        with tempfile.TemporaryDirectory() as target_dir:
            with patch.dict("os.environ", {"FAULTLINE_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "test-key"}):
                response = self.client.post(
                    reverse("start-campaign"),
                    data={
                        "target_path": target_dir,
                        "target_url": "not-a-url",
                        "start_command": "python -m http.server 8765",
                    },
                    content_type="application/json",
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("target_url", response.json())

    @patch("campaigns.views.threading.Thread")
    def test_start_campaign_accepts_valid_request(self, thread_cls):
        with tempfile.TemporaryDirectory() as target_dir:
            with patch.dict("os.environ", {"FAULTLINE_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "test-key"}):
                response = self.client.post(
                    reverse("start-campaign"),
                    data={
                        "target_path": target_dir,
                        "target_url": "http://127.0.0.1:8765",
                        "start_command": "python -m http.server 8765",
                        "log_file": "server.log",
                    },
                    content_type="application/json",
                )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["target"], str(Path(target_dir).resolve()))
        self.assertIn("campaign_id", body)
        self.assertEqual(body["status"], Campaign.Status.QUEUED)
        self.assertTrue(Campaign.objects.filter(id=body["campaign_id"]).exists())
        thread_cls.assert_called_once()

    @patch.dict("os.environ", {}, clear=True)
    def test_start_campaign_requires_openrouter_key(self):
        with tempfile.TemporaryDirectory() as target_dir:
            response = self.client.post(
                reverse("start-campaign"),
                data={
                    "target_path": target_dir,
                    "target_url": "http://127.0.0.1:8765",
                    "start_command": "python -m http.server 8765",
                },
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("OPENROUTER_API_KEY", response.json()["error"])

    @patch("campaigns.views.threading.Thread")
    @patch("core.provider_config.ProviderManager.get_status")
    def test_start_campaign_accepts_authenticated_cli_provider(self, get_status, thread_cls):
        get_status.return_value = {
            "claude": {"installed": True, "auth_ok": True, "message": "Claude CLI detected"},
        }

        with tempfile.TemporaryDirectory() as target_dir:
            with patch.dict("os.environ", {"FAULTLINE_PROVIDER": "claude_cli"}, clear=True):
                response = self.client.post(
                    reverse("start-campaign"),
                    data={
                        "target_path": target_dir,
                        "target_url": "http://127.0.0.1:8765",
                        "start_command": "python -m http.server 8765",
                    },
                    content_type="application/json",
                )

        self.assertEqual(response.status_code, 202)
        self.assertIn("campaign_id", response.json())
        thread_cls.assert_called_once()

    def test_campaign_model_creation_and_status_transition(self):
        campaign = Campaign.objects.create(
            id=uuid.uuid4(),
            target_path="C:/target",
            target_url="http://127.0.0.1:9000",
            start_command="python manage.py runserver 9000",
        )

        campaign.status = Campaign.Status.RUNNING
        campaign.started_at = timezone.now()
        campaign.save(update_fields=["status", "started_at"])

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.RUNNING)
        self.assertIsNotNone(campaign.started_at)

    def test_campaign_detail_endpoint_returns_metadata(self):
        campaign = Campaign.objects.create(
            id=uuid.uuid4(),
            target_path="C:/target",
            target_url="http://127.0.0.1:9000",
            start_command="python manage.py runserver 9000",
        )
        Finding.objects.create(
            campaign=campaign,
            title="Example bug",
            category=Finding.Category.RUNTIME,
            severity=Finding.Severity.HIGH,
        )

        response = self.client.get(reverse("campaign-detail", args=[campaign.id]))

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], str(campaign.id))
        self.assertEqual(body["finding_count"], 1)

    def test_campaign_findings_endpoint_returns_stored_findings(self):
        campaign = Campaign.objects.create(
            id=uuid.uuid4(),
            target_path="C:/target",
            target_url="http://127.0.0.1:9000",
            start_command="python manage.py runserver 9000",
        )
        Finding.objects.create(
            campaign=campaign,
            title="Stored finding",
            category=Finding.Category.API,
            severity=Finding.Severity.MEDIUM,
        )

        response = self.client.get(reverse("campaign-findings", args=[campaign.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["title"], "Stored finding")

    def test_campaign_report_endpoint_returns_report_content(self):
        campaign = Campaign.objects.create(
            id=uuid.uuid4(),
            target_path="C:/target",
            target_url="http://127.0.0.1:9000",
            start_command="python manage.py runserver 9000",
            report_path="README.md",
        )

        response = self.client.get(reverse("campaign-report", args=[campaign.id]))

        self.assertEqual(response.status_code, 200)
        self.assertIn("Faultline", response.json()["report"])

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_openrouter_key_marks_campaign_error(self):
        campaign = Campaign.objects.create(
            id=uuid.uuid4(),
            target_path="C:/target",
            target_url="http://127.0.0.1:9000",
            start_command="python manage.py runserver 9000",
        )

        run_campaign_pipeline(str(campaign.id))

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.ERROR)
        self.assertIn("OPENROUTER_API_KEY", campaign.error_message)
        self.assertTrue(campaign.findings.exists())
        self.assertTrue(Path(campaign.report_path).exists())
        Path(campaign.report_path).unlink(missing_ok=True)

    @patch.dict("os.environ", {"FAULTLINE_PROVIDER": "openrouter", "OPENROUTER_API_KEY": "test-key"})
    @patch("campaigns.services.Medic")
    def test_background_campaign_failure_stores_error(self, medic_cls):
        medic_cls.return_value.start_server.return_value = False
        campaign = Campaign.objects.create(
            id=uuid.uuid4(),
            target_path="C:/target",
            target_url="http://127.0.0.1:9000",
            start_command="python manage.py runserver 9000",
        )

        run_campaign_pipeline(str(campaign.id))

        campaign.refresh_from_db()
        self.assertEqual(campaign.status, Campaign.Status.ERROR)
        self.assertIn("failed to start", campaign.error_message)
        Path(campaign.report_path).unlink(missing_ok=True)

    def test_report_generation_produces_required_sections(self):
        campaign = Campaign.objects.create(
            id=uuid.uuid4(),
            target_path="C:/target",
            target_url="http://127.0.0.1:9000",
            start_command="python manage.py runserver 9000",
        )
        Finding.objects.create(
            campaign=campaign,
            title="Report finding",
            category=Finding.Category.RUNTIME,
            severity=Finding.Severity.HIGH,
            summary="Something broke.",
        )

        report_path = generate_campaign_report(campaign)
        content = Path(report_path).read_text(encoding="utf-8")

        self.assertIn("## Campaign summary", content)
        self.assertIn("## Target configuration", content)
        self.assertIn("## Tools executed", content)
        self.assertIn("## Findings table", content)
        self.assertIn("## Detailed findings", content)
        Path(report_path).unlink(missing_ok=True)


class SkillTests(TestCase):
    @patch("core.cli_provider.subprocess.run")
    def test_claude_adapter_uses_headless_prompt_mode(self, subprocess_run):
        subprocess_run.return_value.returncode = 0
        subprocess_run.return_value.stdout = "ok"
        subprocess_run.return_value.stderr = ""

        output = ClaudeAdapter(target_dir=".").run_task("hello")

        self.assertEqual(output, "ok")
        self.assertEqual(subprocess_run.call_args.args[0], ["claude", "-p", "hello"])

    @patch.dict("os.environ", {"FAULTLINE_CLAUDE_BINARY": "C:\\Tools\\claude.cmd"})
    @patch("core.cli_provider.subprocess.run")
    def test_cli_adapter_accepts_binary_override(self, subprocess_run):
        subprocess_run.return_value.returncode = 0
        subprocess_run.return_value.stdout = "ok"
        subprocess_run.return_value.stderr = ""

        output = ClaudeAdapter(target_dir=".").run_task("hello")

        self.assertEqual(output, "ok")
        self.assertEqual(subprocess_run.call_args.args[0], ["C:\\Tools\\claude.cmd", "-p", "hello"])

    @patch.dict("os.environ", {"FAULTLINE_CLAUDE_BINARY": "C:\\Tools\\claude.cmd"})
    @patch("core.cli_provider.shutil.which")
    def test_cli_adapter_checks_binary_override_installation(self, which):
        which.return_value = "C:\\Tools\\claude.cmd"

        self.assertTrue(ClaudeAdapter(target_dir=".").is_installed())
        which.assert_called_once_with("C:\\Tools\\claude.cmd")

    @patch.dict("os.environ", {"FAULTLINE_GEMINI_CLI_ARGS": ""})
    @patch("core.cli_provider.subprocess.run")
    def test_gemini_adapter_uses_headless_prompt_mode(self, subprocess_run):
        subprocess_run.return_value.returncode = 0
        subprocess_run.return_value.stdout = "ok"
        subprocess_run.return_value.stderr = ""

        output = GeminiAdapter(target_dir=".").run_task("hello")

        self.assertEqual(output, "ok")
        self.assertEqual(subprocess_run.call_args.args[0], ["gemini", "-p", "hello", "--skip-trust"])

    @patch("core.cli_provider.subprocess.run")
    def test_codex_adapter_uses_exec_prompt_mode(self, subprocess_run):
        subprocess_run.return_value.returncode = 0
        subprocess_run.return_value.stdout = "ok"
        subprocess_run.return_value.stderr = ""

        output = CodexAdapter(target_dir=".").run_task("hello")

        self.assertEqual(output, "ok")
        self.assertEqual(
            subprocess_run.call_args.args[0],
            ["codex", "exec", "hello", "--cd", ".", "--sandbox", "read-only"],
        )

    @patch.dict("os.environ", {"FAULTLINE_CODEX_SANDBOX": "workspace-write", "FAULTLINE_CODEX_CLI_ARGS": "--skip-git-repo-check"})
    @patch("core.cli_provider.subprocess.run")
    def test_codex_adapter_accepts_extra_args(self, subprocess_run):
        subprocess_run.return_value.returncode = 0
        subprocess_run.return_value.stdout = "ok"
        subprocess_run.return_value.stderr = ""

        output = CodexAdapter(target_dir=".").run_task("hello")

        self.assertEqual(output, "ok")
        self.assertEqual(
            subprocess_run.call_args.args[0],
            ["codex", "exec", "hello", "--cd", ".", "--sandbox", "workspace-write", "--skip-git-repo-check"],
        )

    @patch("core.cli_provider.ProviderManager.get_status")
    def test_provider_manager_rejects_unauthenticated_cli(self, get_status):
        get_status.return_value = {
            "gemini": {"installed": True, "auth_ok": False, "message": "not logged in"},
        }

        output = ProviderManager(target_dir=".").run("gemini", "hello")

        self.assertIn("not authenticated", output)

    def test_ast_grapher_skips_generated_and_virtualenv_paths(self):
        with tempfile.TemporaryDirectory() as target_dir:
            root = Path(target_dir)
            (root / "app.py").write_text("from os import path, environ\n\ndef view():\n    pass\n", encoding="utf-8")
            (root / ".aegis_patches").mkdir()
            (root / ".aegis_patches" / "generated.py").write_text("def ignored():\n    pass\n", encoding="utf-8")

            graph = ASTGrapher(root).analyze_project()

        self.assertIn("app.py", graph["files"])
        self.assertNotIn(".aegis_patches/generated.py", graph["files"])
        self.assertIn("os.path", graph["files"]["app.py"]["imports"])
        self.assertIn("os.environ", graph["files"]["app.py"]["imports"])

    def test_ast_grapher_detects_django_routes_and_drf_serializers(self):
        with tempfile.TemporaryDirectory() as target_dir:
            root = Path(target_dir)
            (root / "urls.py").write_text(
                "from django.urls import path\n"
                "from rest_framework.routers import DefaultRouter\n"
                "from .views import HealthView, WidgetViewSet\n"
                "router = DefaultRouter()\n"
                "router.register('widgets', WidgetViewSet)\n"
                "urlpatterns = [path('health/', HealthView.as_view(), name='health')]\n",
                encoding="utf-8",
            )
            (root / "serializers.py").write_text(
                "from rest_framework import serializers\n"
                "class WidgetSerializer(serializers.Serializer):\n"
                "    name = serializers.CharField()\n",
                encoding="utf-8",
            )

            graph = ASTGrapher(root).analyze_project()

        routes = graph["files"]["urls.py"]["django"]["routes"]
        serializers = graph["files"]["serializers.py"]["django"]["serializers"]
        self.assertEqual(routes[0]["route"], "widgets")
        self.assertEqual(routes[1]["route"], "health/")
        self.assertEqual(serializers[0]["name"], "WidgetSerializer")

    def test_guardrail_accepts_local_modules(self):
        with tempfile.TemporaryDirectory() as target_dir:
            Path(target_dir, "local_module.py").write_text("VALUE = 1\n", encoding="utf-8")
            validator = GuardrailValidator(target_dir)

            is_valid, message = validator.validate_code("import local_module\n")

        self.assertTrue(is_valid, message)

    def test_propose_patch_rejects_paths_outside_target(self):
        with tempfile.TemporaryDirectory() as target_dir:
            qa = QAEngineer(target_dir)

            message = qa.propose_code_patch("../outside.py", "print('nope')\n")

        self.assertIn("must be inside target_dir", message)
