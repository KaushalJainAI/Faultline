import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable

from django.utils import timezone

from campaigns.models import Campaign, Finding, ToolRun
from core.agent import AegisAgent
from core.pipeline import PipelineRunner
from core.provider_config import get_config_status
from core.tools import analyze_project_structure, index_project_documentation
from skills.medic import Medic
from vault.services import Authenticator

logger = logging.getLogger("CampaignService")


def _truncate(value: Any, limit: int = 16000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str, indent=2)
    return text[:limit]


def _run_tool(campaign: Campaign, tool_name: str, input_summary: str, fn: Callable[[], Any]) -> Any:
    tool_run = ToolRun.objects.create(
        campaign=campaign,
        tool_name=tool_name,
        input_summary=_truncate(input_summary),
    )
    try:
        result = fn()
    except Exception as e:
        tool_run.status = ToolRun.Status.ERROR
        tool_run.error_message = str(e)
        tool_run.finished_at = timezone.now()
        tool_run.save(update_fields=["status", "error_message", "finished_at"])
        raise

    tool_run.status = ToolRun.Status.PASSED
    tool_run.output_summary = _truncate(result)
    tool_run.finished_at = timezone.now()
    tool_run.save(update_fields=["status", "output_summary", "finished_at"])
    return result


def _target_has_docs(target_path: str) -> bool:
    return any(Path(target_path).rglob("*.md"))


def _create_error_finding(campaign: Campaign, title: str, summary: str, evidence: str = "", vision_step: int = None) -> Finding:
    return Finding.objects.create(
        campaign=campaign,
        title=title,
        category=Finding.Category.RUNTIME,
        severity=Finding.Severity.HIGH,
        status="open",
        summary=summary,
        evidence=evidence,
        reproduction_steps="Run the campaign again with the same target configuration.",
        suggested_fix="Inspect the campaign error and target logs, then fix the failing setup or runtime issue.",
        vision_step=vision_step,
    )


def _persist_pipeline_findings(campaign: Campaign, pipeline_result: dict) -> dict:
    deterministic = pipeline_result.get("stages", {}).get("deterministic_checks", {})
    for item in deterministic.get("findings", []):
        cat = item.get("category")
        vision_step = 1 if cat == "syntax" else 2

        Finding.objects.create(
            campaign=campaign,
            title=item.get("title", "Deterministic finding")[:255],
            category=cat if cat in Finding.Category.values else Finding.Category.RUNTIME,
            severity=item.get("severity") if item.get("severity") in Finding.Severity.values else Finding.Severity.MEDIUM,
            status="open",
            summary=item.get("summary", ""),
            evidence=item.get("evidence", ""),
            reproduction_steps="Run `python scripts/faultline_cli.py --mode pipeline --target-dir <target>`.",
            suggested_fix=item.get("suggested_fix", ""),
            file_path=item.get("file_path", ""),
            line_number=item.get("line_number"),
            vision_step=vision_step,
        )
    return pipeline_result


VISION_STEPS = {
    1: "Step 1: Syntax & Hardcoded Runtime Checks",
    2: "Step 2: Deterministic & Dependency Checks",
    3: "Step 3: AST Dependency Failure Analysis",
    4: "Step 4: Agentic API Testing & DB Log Analysis",
    5: "Step 5: Semantic Intent vs. Implementation",
    6: "Step 6: Production Readiness Profiling",
    7: "Step 7: Cyber Security Chaos Engineering",
}

def generate_campaign_report(campaign: Campaign) -> str:
    campaign.refresh_from_db()
    findings = list(campaign.findings.all())
    tool_runs = list(campaign.tool_runs.all())
    report_dir = Path("reports")
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / f"campaign_{campaign.id}.md"

    lines = [
        f"# Faultline Campaign Report: {campaign.id}",
        "",
        "## Campaign summary",
        "",
        f"- Status: {campaign.status}",
        f"- Execution mode: {campaign.execution_mode}",
        f"- Target URL: {campaign.target_url}",
        f"- Target path: {campaign.target_path}",
        f"- Created at: {campaign.created_at}",
        f"- Started at: {campaign.started_at or 'not started'}",
        f"- Finished at: {campaign.finished_at or 'not finished'}",
        f"- Findings: {len(findings)}",
    ]
    if campaign.error_message:
        lines.extend(["", f"- Error: {campaign.error_message}"])

    lines.extend(
        [
            "",
            "## Target configuration",
            "",
            f"- Start command: `{campaign.start_command}`",
            f"- Health URL: {campaign.health_url or 'not provided'}",
            f"- Log file: {campaign.log_file}",
            "",
            "## Tools executed",
            "",
        ]
    )

    if tool_runs:
        lines.extend(["| Step | Tool | Status | Started | Finished |", "| --- | --- | --- | --- | --- |"])
        for run in tool_runs:
            step_str = str(run.vision_step) if run.vision_step else "Uncategorized"
            lines.append(f"| {step_str} | {run.tool_name} | {run.status} | {run.started_at} | {run.finished_at or ''} |")
    else:
        lines.append("No tools were recorded.")

    lines.extend(["", "## Findings by Vision Step", ""])
    
    if not findings:
        lines.append("No findings were recorded.")
    
    # Group findings by vision_step
    from collections import defaultdict
    findings_by_step = defaultdict(list)
    for f in findings:
        findings_by_step[f.vision_step or 0].append(f)
        
    for step in sorted(findings_by_step.keys()):
        step_title = VISION_STEPS.get(step, "Uncategorized Findings")
        lines.extend([f"### {step_title}", ""])
        lines.extend(["| Severity | Category | Title | Status |", "| --- | --- | --- | --- |"])
        for finding in findings_by_step[step]:
            lines.append(f"| {finding.severity} | {finding.category} | {finding.title} | {finding.status} |")
        lines.append("")
        
        lines.append("#### Detailed Evidence")
        lines.append("")
        for finding in findings_by_step[step]:
            lines.extend(
                [
                    f"##### {finding.title}",
                    "",
                    f"- Severity: {finding.severity}",
                    f"- Category: {finding.category}",
                    f"- Status: {finding.status}",
                    f"- Location: {finding.file_path or 'unknown'}{':' + str(finding.line_number) if finding.line_number else ''}",
                    "",
                    "**Summary**",
                    "",
                    finding.summary or "No summary provided.",
                    "",
                    "**Reproduction steps**",
                    "",
                    finding.reproduction_steps or "No reproduction steps recorded.",
                    "",
                    "**Raw crash/log evidence**",
                    "",
                    "```text",
                    finding.evidence or "No raw evidence recorded.",
                    "```",
                    "",
                    "**Suggested next fixes**",
                    "",
                    finding.suggested_fix or "No suggested fix recorded.",
                    "",
                ]
            )

    report_path.write_text("\n".join(lines), encoding="utf-8")
    campaign.report_path = str(report_path)
    campaign.save(update_fields=["report_path"])
    return str(report_path)


def run_campaign_pipeline(campaign_id: str) -> None:
    campaign = Campaign.objects.get(id=campaign_id)
    medic = None

    configured, message = get_config_status(campaign.target_path)
    if not configured:
        campaign.status = Campaign.Status.ERROR
        campaign.error_message = message
        campaign.finished_at = timezone.now()
        campaign.save(update_fields=["status", "error_message", "finished_at"])
        _create_error_finding(campaign, "Campaign configuration error", campaign.error_message)
        generate_campaign_report(campaign)
        return

    try:
        campaign.status = Campaign.Status.RUNNING
        campaign.started_at = timezone.now()
        campaign.save(update_fields=["status", "started_at"])

        medic = Medic(
            start_command=campaign.start_command,
            health_url=campaign.health_url or None,
            target_dir=campaign.target_path,
        )

        target_path_exists = Path(campaign.target_path).exists() and Path(campaign.target_path).is_dir()
        if campaign.execution_mode in {Campaign.ExecutionMode.PIPELINE, Campaign.ExecutionMode.HYBRID} and target_path_exists:
            _run_tool(
                campaign,
                "pipeline_runner.run",
                campaign.target_path,
                lambda: _persist_pipeline_findings(
                    campaign,
                    PipelineRunner(campaign.target_path).run(include_semantic=True),
                ),
            )

        if campaign.execution_mode == Campaign.ExecutionMode.PIPELINE:
            if not target_path_exists:
                raise RuntimeError("Pipeline mode requires target_path to be an existing directory.")
            campaign.status = Campaign.Status.FAILED if campaign.findings.exists() else Campaign.Status.PASSED
            campaign.finished_at = timezone.now()
            campaign.save(update_fields=["status", "finished_at"])
            return

        target_started = _run_tool(campaign, "medic.start_server", campaign.start_command, medic.start_server)
        if not target_started:
            raise RuntimeError("Target server failed to start.")

        _run_tool(
            campaign,
            "analyze_project_structure",
            campaign.target_path,
            lambda: analyze_project_structure.invoke(campaign.target_path),
        )

        if _target_has_docs(campaign.target_path):
            _run_tool(
                campaign,
                "index_project_documentation",
                campaign.target_path,
                lambda: index_project_documentation.invoke({"target_dir": campaign.target_path}),
            )

        session_headers = {}
        if campaign.auth_flow:
            auth_service = Authenticator(campaign.target_url, campaign.auth_flow)
            auth_result = _run_tool(
                campaign,
                "authenticator.execute_flow",
                f"Executing AuthFlow {campaign.auth_flow.name}",
                auth_service.execute_flow,
            )
            session_headers = auth_result.get("headers", {})

        agent = AegisAgent()
        _run_tool(
            campaign,
            "aegis_agent.run_campaign",
            f"{campaign.target_path} -> {campaign.target_url}",
            lambda: asyncio.run(
                agent.run_campaign(
                    target_dir=campaign.target_path,
                    target_url=campaign.target_url,
                    log_file=campaign.log_file,
                    session_headers=session_headers,
                    initial_prompt=(
                        "Run a Django/DRF quality campaign. Identify endpoints and components. "
                        "Read `docs/TESTING_GUIDE.md`. To minimize output tokens, copy the relevant boilerplate "
                        "from `agent_assets/test_boilerplates/`, edit it in-place to fit the discovered endpoints or models, "
                        "and save the resulting script in the `reports/testcases/` directory. "
                        "Verbalize your step-by-step reasoning clearly so human reviewers can trace your logic in the logs."
                    ),
                    campaign_id=str(campaign.id)
                )
            ),
        )

        campaign.status = Campaign.Status.FAILED if campaign.findings.exists() else Campaign.Status.PASSED
        campaign.finished_at = timezone.now()
        campaign.save(update_fields=["status", "finished_at"])
    except Exception as e:
        logger.exception("Campaign %s failed", campaign_id)
        campaign.status = Campaign.Status.ERROR
        campaign.error_message = str(e)
        campaign.finished_at = timezone.now()
        campaign.save(update_fields=["status", "error_message", "finished_at"])
        _create_error_finding(campaign, "Campaign execution error", str(e))
    finally:
        if medic:
            try:
                _run_tool(campaign, "medic.kill_server", "Stop target process", medic.kill_server)
            except Exception:
                logger.exception("Failed to stop target process for campaign %s", campaign_id)
        generate_campaign_report(campaign)
