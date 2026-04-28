import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable

from django.utils import timezone

from campaigns.models import Campaign, Finding, ToolRun
from core.agent import AegisAgent
from core.tools import analyze_project_structure, index_project_documentation
from skills.medic import Medic

logger = logging.getLogger("CampaignService")


def _truncate(value: Any, limit: int = 4000) -> str:
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


def _create_error_finding(campaign: Campaign, title: str, summary: str, evidence: str = "") -> Finding:
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
    )


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
        lines.extend(["| Tool | Status | Started | Finished |", "| --- | --- | --- | --- |"])
        for run in tool_runs:
            lines.append(f"| {run.tool_name} | {run.status} | {run.started_at} | {run.finished_at or ''} |")
    else:
        lines.append("No tools were recorded.")

    lines.extend(["", "## Findings table", ""])
    if findings:
        lines.extend(["| Severity | Category | Title | Status |", "| --- | --- | --- | --- |"])
        for finding in findings:
            lines.append(f"| {finding.severity} | {finding.category} | {finding.title} | {finding.status} |")
    else:
        lines.append("No findings were recorded.")

    lines.extend(["", "## Detailed findings", ""])
    for finding in findings:
        lines.extend(
            [
                f"### {finding.title}",
                "",
                f"- Severity: {finding.severity}",
                f"- Category: {finding.category}",
                f"- Status: {finding.status}",
                f"- Location: {finding.file_path or 'unknown'}{':' + str(finding.line_number) if finding.line_number else ''}",
                "",
                "#### Summary",
                "",
                finding.summary or "No summary provided.",
                "",
                "#### Reproduction steps",
                "",
                finding.reproduction_steps or "No reproduction steps recorded.",
                "",
                "#### Raw crash/log evidence",
                "",
                "```text",
                finding.evidence or "No raw evidence recorded.",
                "```",
                "",
                "#### Suggested next fixes",
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

    if not os.environ.get("OPENROUTER_API_KEY"):
        campaign.status = Campaign.Status.ERROR
        campaign.error_message = "OPENROUTER_API_KEY is required to run autonomous campaigns."
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
                    initial_prompt=(
                        "Run a Django/DRF quality campaign. Identify endpoints, run at least one functional "
                        "test when feasible, generate adversarial payloads, execute them, and save a report."
                    ),
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
