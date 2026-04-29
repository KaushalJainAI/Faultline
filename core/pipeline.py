import json
from pathlib import Path
from typing import Dict

from core.tools import analyze_project_structure, index_project_documentation
from skills.deterministic_checker import DeterministicChecker


class PipelineRunner:
    """Deterministic-first Faultline runner.

    This gives the CLI and API a predictable baseline while the agent-first
    workflow remains available for model-led investigation.
    """

    def __init__(self, target_dir: str, reports_dir: str = "reports"):
        self.target_dir = str(Path(target_dir).expanduser().resolve())
        self.reports_dir = Path(reports_dir)
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def run(self, include_semantic: bool = True) -> Dict:
        deterministic = DeterministicChecker(self.target_dir).run_all()
        structure = json.loads(analyze_project_structure.invoke(self.target_dir))

        semantic = {"status": "skipped"}
        if include_semantic and any(Path(self.target_dir).rglob("*.md")):
            semantic = {
                "status": "completed",
                "result": index_project_documentation.invoke({"target_dir": self.target_dir}),
            }

        report = {
            "mode": "pipeline-first",
            "target_dir": self.target_dir,
            "stages": {
                "deterministic_checks": deterministic,
                "dependency_graph": {
                    "files": len(structure.get("files", {})),
                    "dependencies": len(structure.get("dependencies", [])),
                },
                "semantic_indexing": semantic,
                "agentic_api_tests": {"status": "available_in_agent_or_hybrid_mode"},
                "production_readiness": {"status": "planned"},
                "authorized_security": {"status": "available_as_bounded_chaos_tools"},
            },
        }
        report["report_path"] = self.write_report(report)
        return report

    def write_report(self, report: Dict) -> str:
        path = self.reports_dir / "pipeline_report.md"
        det = report["stages"]["deterministic_checks"]
        lines = [
            "# Faultline Pipeline Report",
            "",
            f"- Mode: {report['mode']}",
            f"- Target: `{report['target_dir']}`",
            f"- Findings: {det['summary']['total_findings']}",
            f"- High/Critical: {det['summary']['high_or_critical']}",
            "",
            "## Deterministic Findings",
            "",
        ]
        if det["findings"]:
            for finding in det["findings"]:
                location = finding.get("file_path") or "project"
                if finding.get("line_number"):
                    location += f":{finding['line_number']}"
                lines.extend([
                    f"### {finding['title']}",
                    "",
                    f"- Severity: {finding['severity']}",
                    f"- Category: {finding['category']}",
                    f"- Location: {location}",
                    "",
                    finding.get("summary") or "",
                    "",
                    "Suggested fix:",
                    "",
                    finding.get("suggested_fix") or "Inspect the evidence and fix the underlying issue.",
                    "",
                ])
        else:
            lines.append("No deterministic findings.")

        lines.extend(["", "## Dependency Root Causes", ""])
        roots = det["dependency_root_causes"]
        if roots:
            for root in roots:
                lines.append(f"- `{root['root_file']}` impacts {root['impact_count']} dependent file(s).")
        else:
            lines.append("No dependency failure propagation detected.")

        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)
