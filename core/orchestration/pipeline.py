"""
Faultline Deterministic Pipeline Runner.
This module orchestrates Step 1-3 of the vision: syntax checks, static analysis,
AST project mapping, and production-ready scoring without an LLM.
"""
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.tools import analyze_project_structure
from skills.deterministic_checker import DeterministicChecker
from skills.graph_3d import Graph3DGenerator
from langsmith import traceable


class PipelineAborted(Exception):
    """Raised when the operator presses Esc during the pipeline phase."""


# Ordered categories for report grouping
_CATEGORY_ORDER = ["syntax", "runtime", "api", "semantic", "security_candidate"]

# Rule-based next-steps: category â†’ checklist item
_NEXT_STEPS = {
    "syntax": "Fix all syntax errors â€” nothing else can run until Python can parse the files.",
    "runtime": "Resolve missing imports and dependency conflicts â€” run `pip install -r requirements.txt` in the target venv.",
    "api": "Investigate API-level issues surfaced by the AST scan before writing functional tests.",
    "semantic": "Address semantic mismatches between documentation intent and implementation.",
    "security_candidate": (
        "Review all security findings. CRITICAL/HIGH issues (CVEs, hardcoded secrets, "
        "missing auth decorators) must be fixed before production. Run `bandit -r .`, "
        "`semgrep --config p/django .`, and `pip-audit` locally to reproduce. "
        "Use propose_code_patch to generate fixes and record_finding to document them."
    ),
}


def _severity_gauge(score: int, max_score: int = 100) -> str:
    """ASCII progress bar, e.g. `â–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–ˆâ–‘â–‘  84/100`."""
    pct = max(0, min(score, max_score))
    filled = round(pct / 10)
    bar = "#" * filled + "." * (10 - filled)
    return f"`{bar}  {pct}/{max_score}`"


def _score_from_findings(findings: List[Dict]) -> int:
    penalties = {"critical": 20, "high": 10, "medium": 4, "low": 2}
    penalty = sum(penalties.get(f.get("severity", "medium"), 4) for f in findings)
    return max(0, 100 - min(penalty, 100))


class PipelineRunner:
    """Deterministic-first Faultline runner.

    Runs syntax/import/lint/dependency checks then writes a fully
    deterministic Markdown report â€” no LLM text anywhere in the output.
    """

    def __init__(self, target_dir: str, run_folder: Optional[Path] = None, reports_dir: str = "reports"):
        self.target_dir = str(Path(target_dir).expanduser().resolve())
        # If a per-run folder is provided, write there; otherwise fall back to flat reports/.
        if run_folder is not None:
            self.run_folder = Path(run_folder)
        else:
            self.run_folder = Path(reports_dir)
        self.run_folder.mkdir(parents=True, exist_ok=True)

    @traceable(name="PipelineRunner.run", run_type="chain")
    def run(
        self,
        include_semantic: bool = True,
        renderer=None,
        pause_event: Optional[Any] = None,
    ) -> Dict:
        """
        Run the pipeline. If `pause_event` is supplied, the runner checks
        `pause_event.is_set()` between steps and raises `PipelineAborted` if set.
        Any object exposing an `is_set()` method works (asyncio.Event,
        threading.Event, or a custom flag).
        """
        def _check_abort(stage: str) -> None:
            if pause_event is not None and pause_event.is_set():
                raise PipelineAborted(
                    f"Pipeline halted by operator (Esc) during '{stage}'."
                )

        started_at = datetime.now()

        _check_abort("startup")
        if renderer:
            renderer.show_pipeline_step("Deterministic Checks", "running")
        deterministic = DeterministicChecker(self.target_dir).run_all()
        _check_abort("deterministic-checks")
        if renderer:
            count = deterministic.get("summary", {}).get("total_findings", 0)
            renderer.show_pipeline_step(
                "Deterministic Checks", "done", detail=f"{count} finding(s)"
            )
            renderer.show_pipeline_step("AST Dependency Graph", "running")

        try:
            structure = json.loads(analyze_project_structure.invoke(self.target_dir))
        except Exception:
            structure = {"files": {}, "dependencies": []}
        _check_abort("ast-graph")

        graph_html_path = ""
        if renderer:
            file_count  = len(structure.get("files", {}))
            dep_count   = len(structure.get("dependencies", []))
            call_count  = len(structure.get("call_edges", []))
            inh_count   = len(structure.get("inheritance_edges", []))
            renderer.show_pipeline_step(
                "AST Dependency Graph", "done",
                detail=(
                    f"{file_count} files, {dep_count} import edges, "
                    f"{call_count} call edges, {inh_count} inheritance edges"
                ),
            )
            renderer.show_pipeline_step("3D Graph", "running")

        try:
            graph_html_path = Graph3DGenerator().generate(
                structure,
                str(self.run_folder / "dependency_graph.py"),
            )
            if renderer:
                renderer.show_pipeline_step(
                    "3D Graph", "done",
                    detail=f"run with: python {graph_html_path}",
                )
        except Exception as exc:
            if renderer:
                renderer.show_pipeline_step("3D Graph", "error", detail=str(exc))
        _check_abort("3d-graph")

        # Save serializer schemas for the Step-4 agent to use
        schemas = structure.get("serializer_schemas", [])
        if schemas:
            schema_path = self.run_folder / "api_schemas.json"
            schema_path.write_text(
                json.dumps(schemas, indent=2), encoding="utf-8"
            )
            
            # Export aggregated endpoints
            endpoints = structure.get("endpoints", [])
            endpoint_map_path = self.run_folder / "endpoint_map.json"
            endpoint_map_path.write_text(
                json.dumps(endpoints, indent=2), encoding="utf-8"
            )
            
            if renderer:
                renderer.show_pipeline_step(
                    "API Schema Export", "done",
                    detail=f"{len(schemas)} serializer(s), {len(endpoints)} endpoint(s) -> json"
                )

        semantic = {"status": "skipped"}
        if include_semantic and any(Path(self.target_dir).rglob("*.md")):
            from skills.semantic_indexer import project_db_path
            from core.intelligence import index_state
            db_path = str(project_db_path("./db/faiss_store", self.target_dir))
            index_state.start_background_index(self.target_dir, db_path)
            semantic = {"status": "indexing_in_background", "db_path": db_path}
            if renderer:
                renderer.show_pipeline_step(
                    "Semantic Indexing", "running",
                    detail="started in background â€” pipeline continues",
                )
        elif renderer:
            renderer.show_pipeline_step(
                "Semantic Indexing", "skipped",
                detail="no markdown docs found" if include_semantic else "disabled"
            )
        _check_abort("semantic")

        elapsed = (datetime.now() - started_at).total_seconds()

        report = {
            "mode": "pipeline-first",
            "target_dir": self.target_dir,
            "run_folder": str(self.run_folder),
            "elapsed_seconds": round(elapsed, 1),
            "stages": {
                "deterministic_checks": deterministic,
                "dependency_graph": {
                    "files":         len(structure.get("files", {})),
                    "dependencies":  len(structure.get("dependencies", [])),
                    "call_edges":    len(structure.get("call_edges", [])),
                    "inheritance_edges": len(structure.get("inheritance_edges", [])),
                    "graph_viewer_py": graph_html_path,
                },
                "modularity": {
                    "overall_score": deterministic.get("summary", {}).get("modularity_score", 100),
                    "report": deterministic.get("modularity_report", {}),
                },
                "semantic_indexing": semantic,
                "agentic_api_tests": {"status": "available_in_agent_or_hybrid_mode"},
                "production_readiness": {"status": "planned"},
                "authorized_security": {"status": "available_as_bounded_chaos_tools"},
            },
        }
        report["report_path"] = self.write_report(report)
        self.write_modularity_map(report["stages"]["modularity"]["report"])
        return report

    # ------------------------------------------------------------------
    # Deterministic report â€” zero LLM text
    # ------------------------------------------------------------------

    def write_report(self, report: Dict) -> str:
        path = self.run_folder / "pipeline_report.md"
        det = report["stages"]["deterministic_checks"]
        findings: List[Dict] = det.get("findings", [])
        summary = det.get("summary", {})
        roots = det.get("dependency_root_causes", [])

        score = _score_from_findings(findings)
        total = summary.get("total_findings", 0)
        high_crit = summary.get("high_or_critical", 0)
        elapsed = report.get("elapsed_seconds", 0)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        lines: List[str] = [
            "# Faultline Pipeline Report",
            "",
            f"- **Target:** `{report['target_dir']}`",
            f"- **Run folder:** `{report['run_folder']}`",
            f"- **Generated:** {now}",
            f"- **Duration:** {elapsed}s",
            "",
        ]

        # --- Executive summary ---
        lines += [
            "## Executive Summary",
            "",
            f"| Metric | Value |",
            f"| --- | --- |",
            f"| Total findings | {total} |",
            f"| High / Critical | {high_crit} |",
            f"| Files mapped | {report['stages']['dependency_graph']['files']} |",
            f"| Dependencies mapped | {report['stages']['dependency_graph']['dependencies']} |",
            f"| Modularity Score | {report['stages']['modularity']['overall_score']}/100 |",
            f"| Semantic indexing | {report['stages']['semantic_indexing']['status']} |",
            f"| Production-readiness score | {score}/100 |",
            "",
        ]

        # --- Production-readiness gauge ---
        lines += [
            "## Production-Readiness Score",
            "",
            _severity_gauge(score),
            "",
            "> Score starts at 100 and loses points per finding (critical âˆ’20, high âˆ’10, medium âˆ’4, low âˆ’2), capped at 0.",
            "",
        ]

        # --- Severity distribution ---
        sev_counts: Dict[str, int] = {}
        for f in findings:
            sev_counts[f.get("severity", "medium")] = sev_counts.get(f.get("severity", "medium"), 0) + 1

        lines += [
            "## Severity Distribution",
            "",
            "| Severity | Count |",
            "| --- | --- |",
            f"| ðŸ”´ Critical | {sev_counts.get('critical', 0)} |",
            f"| ðŸŸ  High | {sev_counts.get('high', 0)} |",
            f"| ðŸŸ¡ Medium | {sev_counts.get('medium', 0)} |",
            f"| ðŸ”µ Low | {sev_counts.get('low', 0)} |",
            "",
        ]

        # --- Findings by category ---
        lines += ["## Findings by Category", ""]
        by_category: Dict[str, List[Dict]] = {}
        for f in findings:
            cat = f.get("category", "runtime")
            by_category.setdefault(cat, []).append(f)

        ordered_cats = [c for c in _CATEGORY_ORDER if c in by_category]
        ordered_cats += [c for c in by_category if c not in ordered_cats]

        if not findings:
            lines.append("No deterministic findings â€” project passes all baseline checks.")
        else:
            for cat in ordered_cats:
                cat_findings = by_category[cat]
                lines += [f"### {cat.replace('_', ' ').title()} ({len(cat_findings)})", ""]

                # Group identical findings â€” same (severity, title) is one issue,
                # many locations. Collapse duplicates so the report stays scannable.
                groups: Dict[tuple, Dict] = {}
                for f in cat_findings:
                    key = (f.get("severity", "medium"), f.get("title", ""))
                    if key not in groups:
                        groups[key] = {"finding": f, "locations": []}
                    loc = f.get("file_path") or "project-level"
                    if f.get("line_number"):
                        loc += f":{f['line_number']}"
                    groups[key]["locations"].append(loc)

                _MAX_LOCS = 10
                for (sev_key, title_key), grp in groups.items():
                    sev = sev_key.upper()
                    f = grp["finding"]
                    locs = grp["locations"]
                    count = len(locs)
                    suffix = f" â€” {count} occurrence{'s' if count > 1 else ''}" if count > 1 else ""
                    lines += [
                        f"#### [{sev}] {title_key}{suffix}",
                        "",
                    ]
                    if f.get("suggested_fix"):
                        lines.append(f"- **Fix:** {f['suggested_fix']}")
                    if count == 1:
                        lines.append(f"- **Location:** `{locs[0]}`")
                    else:
                        lines.append(f"- **Locations ({count}):**")
                        for loc in locs[:_MAX_LOCS]:
                            lines.append(f"  - `{loc}`")
                        if count > _MAX_LOCS:
                            lines.append(f"  - â€¦ {count - _MAX_LOCS} more â€” see `findings.jsonl` for full list")
                    lines.append("")

        # --- Modularity Audit ---
        mod = report["stages"]["modularity"]["report"]
        lines += ["## Modularity Audit", ""]
        if mod:
            lines += [
                "Assessment of project containers and their independence:",
                "",
                "| Container | Status | Independence | Cohesion | Public API |",
                "| --- | --- | --- | --- | --- |",
            ]
            for cid, data in mod.get("containers", {}).items():
                m = data["metrics"]
                lines.append(f"| `{cid}` | {data['status']} | {m['independence_score']}% | {m['cohesion_density']} | {m['public_api_size']} |")
            lines += [
                "",
                "**View Detailed Map:** [modularity_map.md](modularity_map.md)",
                "",
            ]
        else:
            lines += ["Modularity assessment data not available.", ""]

        # --- AST dependency root causes ---
        lines += ["## AST Dependency Root Causes", ""]
        if roots:
            lines += [
                "Files whose failure propagates to the most dependents (fix these first):",
                "",
                "| Root File | Impacted Files |",
                "| --- | --- |",
            ]
            for root in roots:
                lines.append(f"| `{root['root_file']}` | {root['impact_count']} |")
            lines.append("")
        else:
            lines += ["No dependency failure propagation detected.", ""]

        # --- Next steps checklist ---
        lines += ["## Next Steps", ""]
        present_categories = set(by_category.keys())
        checklist = [_NEXT_STEPS[c] for c in _CATEGORY_ORDER if c in present_categories and c in _NEXT_STEPS]
        if not checklist:
            lines.append("- All baseline checks passed. Proceed to the agent phase for API and chaos testing.")
        else:
            for item in checklist:
                lines.append(f"- [ ] {item}")
        lines += [
            "- [ ] Re-run `python faultline.py --mode pipeline` after fixes to verify the score improved.",
            "",
        ]

        path.write_text("\n".join(lines), encoding="utf-8")
        return str(path)

    def write_modularity_map(self, mod_report: Dict):
        """Writes a detailed modularity breakdown with a Mermaid architecture map."""
        if not mod_report:
            return
        
        path = self.run_folder / "modularity_map.md"
        lines = [
            "# Modularity Architecture Map",
            "",
            "This document visualizes the project as a set of independent containers and assesses their modular health.",
            "",
            "## System Architecture",
            "",
            "```mermaid",
            mod_report.get("mermaid", ""),
            "```",
            "",
            "## Container Deep Dive",
            "",
        ]

        for cid, data in mod_report.get("containers", {}).items():
            lines += [
                f"### Container: `{cid}`",
                f"- **Status:** {data['status']}",
                f"- **Files:** {len(data['files'])}",
                f"- **Public API Surface:** `{', '.join(data.get('public_surface', [])) or 'None'}`",
                "",
                "#### Metrics",
                f"- **Independence:** {data['metrics']['independence_score']}% (High = Better)",
                f"- **Instability:** {data['metrics']['instability']} (0 = Stable, 1 = Unstable)",
                f"- **Cohesion Density:** {data['metrics']['cohesion_density']} (Links per file)",
                "",
                "---",
                ""
            ]

        path.write_text("\n".join(lines), encoding="utf-8")

