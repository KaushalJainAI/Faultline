"""
Faultline Agent Tools.
A collection of LangChain-wrapped tools that expose Faultline skills to the
Aegis-Breaker agent, including project analysis, test execution, and chaos testing.
"""
import json
import logging
import asyncio
import os
import shutil
from datetime import datetime
from pathlib import Path
from langchain_core.tools import tool

from skills.ast_grapher import ASTGrapher
from skills.attacker import SiegeEngine
from skills.deterministic_checker import DeterministicChecker
from skills.file_reader import ProjectFileReader
from skills.guardrails import GuardrailValidator
from skills.log_correlator import LogCorrelator
from skills.semantic_indexer import SemanticIndexer
from skills.qa_engineer import QAEngineer
from skills.visualizer import Visualizer
from core.cli_provider import ProviderManager

logger = logging.getLogger("FaultlineTools")

@tool
def list_project_files(target_dir: str, glob: str = "**/*.py", limit: int = 250) -> str:
    """
    Lists project-local files for agent-first investigation.
    Respects skipped directories such as venv, .git, caches, and node_modules.
    """
    logger.info("Tool Call: Listing project files in %s", target_dir)
    try:
        reader = ProjectFileReader(target_dir)
        return json.dumps(reader.list_files(glob=glob, limit=limit))
    except Exception as e:
        return f"Error listing project files: {e}"

@tool
def read_project_file(target_dir: str, relative_path: str, start_line: int = 1, max_lines: int = 240, run_folder: str = "") -> str:
    """
    Reads a bounded slice of a project-local file for agent-first investigation.
    Use this before proposing tests, payloads, or patches.

    run_folder: optional — when provided, dedup is enforced. If the file was already
    read this session and its content is unchanged, a cache-hit notice is returned
    instead of re-reading. Use retrieve_stored_content(run_folder, ref_id) to get
    the full content, or read_many_project_files to batch multiple reads.

    NOTE: For reading 2+ files at once use read_many_project_files or glob_and_read.
    """
    logger.info("Tool Call: Reading project file %s", relative_path)
    try:
        # Dedup check — skip re-read if content unchanged
        if run_folder:
            cache = _get_read_cache(run_folder)
            cached = cache.get(relative_path)
            if cached:
                try:
                    reader_tmp = ProjectFileReader(target_dir)
                    raw_tmp = reader_tmp.read_file(relative_path, start_line=1, max_lines=99999)
                    full_content = raw_tmp.get("content", "") if isinstance(raw_tmp, dict) else str(raw_tmp)
                    if _file_sha(full_content) == cached.get("sha", ""):
                        return json.dumps({
                            "cached": True,
                            "path": relative_path,
                            "ref_id": cached.get("ref_id", ""),
                            "turn": cached.get("turn", ""),
                            "note": (
                                f"Already read this session (content unchanged) → "
                                f"REF:{cached.get('ref_id','')}. "
                                "Call retrieve_stored_content if you need the full content again."
                            ),
                        }, indent=2)
                except Exception:
                    pass  # Fall through to normal read on any error

        reader = ProjectFileReader(target_dir)
        raw = reader.read_file(relative_path, start_line, max_lines)
        content = raw.get("content", "") if isinstance(raw, dict) else str(raw)
        sha = _file_sha(content)
        ref_id = f"read_project_file__{relative_path.replace('/', '_').replace(chr(92), '_').replace('.', '_')}"
        if run_folder:
            _update_read_cache(run_folder, relative_path, ref_id, "current", sha)
        return json.dumps(raw, indent=2)
    except Exception as e:
        return f"Error reading project file: {e}"

def _auto_fan_deterministic_findings(results: dict) -> None:
    """
    Automatically write grouped findings to the live report for every issue
    category that the deterministic checker returns. Groups by (category, file)
    and emits one rolled-up finding per group (max 5 examples shown).
    No-op if live_report_var is not set.
    """
    try:
        from core.context import live_report_var
        _lr = live_report_var.get(None)
        if _lr is None:
            return

        # Map checker result keys → finding category / severity
        _CATEGORY_MAP = {
            "syntax_errors":        ("syntax",   "high"),
            "import_errors":        ("syntax",   "medium"),
            "ruff_issues":          ("syntax",   "low"),
            "division_by_zero":     ("runtime",  "high"),
            "collection_failures":  ("runtime",  "medium"),
            "dependency_conflicts": ("runtime",  "medium"),
        }

        for key, (cat, sev) in _CATEGORY_MAP.items():
            issues = results.get(key)
            if not issues:
                continue

            # Normalise to a flat list of strings
            if isinstance(issues, dict):
                flat = [f"{f}: {v}" for f, v in issues.items()]
            elif isinstance(issues, list):
                flat = [str(i) for i in issues]
            else:
                flat = [str(issues)]

            if not flat:
                continue

            # Group by file prefix when possible
            from collections import defaultdict
            groups: dict = defaultdict(list)
            for item in flat:
                # Try to extract a file token (first path-like word)
                import re as _re
                m = _re.match(r"([^\s:]+\.(py|txt|cfg|toml|ini))", item)
                file_key = m.group(1) if m else "_general"
                groups[file_key].append(item)

            for file_key, items in groups.items():
                top5 = items[:5]
                remainder = len(items) - len(top5)
                evidence = "\n".join(f"  - {i}" for i in top5)
                if remainder:
                    evidence += f"\n  ... and {remainder} more"
                _lr.append_finding_sync({
                    "title": f"[AUTO] {key.replace('_', ' ').title()} in {file_key}",
                    "category": cat,
                    "severity": sev,
                    "summary": f"{len(items)} issue(s) found by deterministic checker ({key})",
                    "evidence": evidence,
                    "file_path": file_key if file_key != "_general" else "",
                    "vision_step": 1,
                    "auto": True,
                })
    except Exception as _e:
        logger.debug("_auto_fan_deterministic_findings error: %s", _e)


@tool
def run_deterministic_checks(target_dir: str) -> str:
    """
    Runs deterministic pre-agent checks: syntax parsing, missing imports,
    definite division-by-zero hazards, ruff, pip check, pytest collection,
    and AST dependency root-cause propagation.

    Findings are automatically written to the live report — you do NOT need
    to call record_finding for deterministic issues. Review the returned JSON
    and use it to prioritise deeper investigation.
    """
    logger.info("Tool Call: Running deterministic checks for %s", target_dir)
    try:
        checker = DeterministicChecker(target_dir)
        results = checker.run_all()
        _auto_fan_deterministic_findings(results)
        return json.dumps(results)
    except Exception as e:
        return f"Error running deterministic checks: {e}"

@tool
def analyze_project_structure(target_dir: str) -> str:
    """
    Analyzes the Python project structure at target_dir using AST parsing.
    Returns a JSON string describing classes, functions, and imports per file.
    Use this to understand the project architecture and identify vulnerable endpoints.
    """
    logger.info(f"Tool Call: Analyzing structure for {target_dir}")
    try:
        grapher = ASTGrapher(root_dir=target_dir)
        graph = grapher.analyze_project()
        return json.dumps(graph)
    except Exception as e:
        return f"Error analyzing project structure: {e}"

@tool
def query_knowledge_base(query_text: str, db_path: str = "") -> str:
    """
    Queries the FAISS semantic knowledge base for documentation intent.
    Helps in understanding the business logic and intended behavior of the system.
    If indexing is still running in the background, waits for it to complete first.
    """
    logger.info("Tool Call: Querying knowledge base for: %s", query_text)
    try:
        from core import index_state
        from skills.semantic_indexer import SemanticIndexer
        resolved_db = db_path or index_state.current_db_path() or "./db/faiss_store"
        if index_state.is_indexing():
            logger.info("query_knowledge_base: waiting for background indexer to finish...")
            err = index_state.wait_for_index(timeout=300.0)
            if err:
                return f"Semantic index not available (indexing failed): {err}"
        indexer = SemanticIndexer(db_path=resolved_db)
        results = indexer.query(query_text)
        return json.dumps(results)
    except Exception as e:
        return f"Error querying knowledge base: {e}"

@tool
def index_project_documentation(target_dir: str, db_path: str = "") -> str:
    """
    Indexes all project documentation (*.md) into the FAISS semantic index.
    Uses a per-project cache — skips re-indexing if docs have not changed since last run.
    """
    logger.info("Tool Call: Indexing documentation for %s", target_dir)
    try:
        from skills.semantic_indexer import SemanticIndexer, project_db_path
        resolved_db = db_path or str(project_db_path("./db/faiss_store", target_dir))
        indexer = SemanticIndexer(db_path=resolved_db)
        indexer.index_project_docs(target_dir)
        return f"Documentation indexed at {resolved_db} (cache used if docs unchanged)."
    except Exception as e:
        return f"Error indexing documentation: {e}"

@tool
def validate_python_code(code_string: str, target_dir: str) -> str:
    """
    Validates AI-generated Python code by checking for missing imports,
    hallucinated modules, and basic syntax errors.
    Returns 'Valid' or an error message.
    """
    logger.info("Tool Call: Validating generated Python code")
    try:
        validator = GuardrailValidator(target_dir=target_dir)
        is_valid, msg = validator.validate_code(code_string)
        return msg if not is_valid else "Valid."
    except Exception as e:
        return f"Validation error: {e}"

def _auto_fan_chaos_findings(crashes: list, anomaly_report: dict, target_url: str) -> None:
    """Auto-write one finding per crash and one rolled-up anomaly finding."""
    try:
        from core.context import live_report_var
        _lr = live_report_var.get(None)
        if _lr is None:
            return
        for crash in crashes[:20]:
            endpoint = crash.get("endpoint", target_url)
            _lr.append_finding_sync({
                "title": f"[AUTO] Server crash at {endpoint}",
                "category": "runtime",
                "severity": "high",
                "summary": f"HTTP 500 / traceback correlated with chaos payload at {endpoint}",
                "evidence": str(crash.get("traceback", crash))[:600],
                "reproduction_steps": f"Replay payload: {json.dumps(crash.get('payload', {}))[:300]}",
                "file_path": crash.get("file", ""),
                "line_number": crash.get("line", None),
                "vision_step": 4,
                "auto": True,
            })
        # Rolled-up anomaly finding
        n = anomaly_report.get("anomaly_count", 0)
        if n > 0:
            anomalies = anomaly_report.get("anomalies", [])
            rate_limit_hits = [a for a in anomalies if any(ann.get("type") == "rate_limit_hit" for ann in a.get("anomalies", []))]
            
            if rate_limit_hits:
                _lr.append_finding_sync({
                    "title": f"[AUTO] Campaign Throttled: {len(rate_limit_hits)} Rate Limit Hit(s) (HTTP 429)",
                    "category": "api",
                    "severity": "medium",
                    "summary": f"The chaos campaign hit server-side rate limits {len(rate_limit_hits)} times. This indicates the target's DoS protections are active but may prevent exhaustive testing.",
                    "evidence": f"First 3 endpoints throttled:\n" + "\n".join([f"- {h.get('endpoint')}" for h in rate_limit_hits[:3]]),
                    "vision_step": 4,
                    "auto": True,
                })
                # Remove them from the general count to avoid double-reporting if they are the only anomalies
                other_anomalies = [a for a in anomalies if not any(ann.get("type") == "rate_limit_hit" for ann in a.get("anomalies", []))]
                if other_anomalies:
                    _lr.append_finding_sync({
                        "title": f"[AUTO] {len(other_anomalies)} other anomalous response(s) during chaos campaign",
                        "category": "security_candidate",
                        "severity": "medium",
                        "summary": f"{len(other_anomalies)} non-throttled anomalies detected",
                        "evidence": json.dumps(other_anomalies[:5], indent=2)[:600],
                        "vision_step": 4,
                        "auto": True,
                    })
            else:
                _lr.append_finding_sync({
                    "title": f"[AUTO] {n} anomalous response(s) during chaos campaign",
                    "category": "security_candidate",
                    "severity": "medium",
                    "summary": anomaly_report.get("summary", f"{n} anomalies detected"),
                    "evidence": json.dumps(anomalies[:5], indent=2)[:600],
                    "vision_step": 4,
                    "auto": True,
                })
    except Exception as _e:
        logger.debug("_auto_fan_chaos_findings error: %s", _e)


@tool
async def execute_chaos_campaign(payloads_json: str, target_url: str, log_file: str) -> str:
    """
    Executes an asynchronous assault using the provided JSON list of attack payloads.
    payloads_json must be a JSON string of the format:
    [{"method": "POST", "endpoint": "/api/test", "payload": {}, "headers": {}}]
    """
    logger.info(f"Tool Call: Executing Chaos Campaign against {target_url}")
    try:
        from core.context import chaos_vetoed_var
        if chaos_vetoed_var.get():
            chaos_vetoed_var.set(False)  # one-shot veto, reset for next call
            return json.dumps({
                "status": "vetoed_by_operator",
                "total_executed": 0,
                "total_crashes_found": 0,
                "crash_details": [],
                "message": "Human operator denied permission for this chaos campaign."
            }, indent=2)

        payloads = json.loads(payloads_json)
        if not isinstance(payloads, list):
            return "Error: payloads_json must be a JSON array."

        from core.context import session_headers_var
        headers = session_headers_var.get()
        engine = SiegeEngine(target_url, session_headers=headers)
        correlator = LogCorrelator(log_file)
        
        correlator.start_watching()
        results = await engine.execute_assault(payloads)
        
        await asyncio.sleep(2) # Give logs time to flush
        correlator.stop_watching()
        
        crashes = correlator.get_correlations()

        # Analyze responses for subtle vulnerabilities beyond HTTP 500
        from skills.response_analyzer import analyze_assault_results
        anomaly_report = analyze_assault_results(results)

        summary = {
            "total_executed": len(results),
            "total_crashes_found": len(crashes),
            "crash_details": crashes,
            "anomaly_analysis": {
                "anomaly_count": anomaly_report["anomaly_count"],
                "severity_distribution": anomaly_report["severity_distribution"],
                "summary": anomaly_report["summary"],
                "anomalies": anomaly_report["anomalies"][:20],  # cap for context window
            },
        }
        # Auto-fan crash findings to live report
        _auto_fan_chaos_findings(crashes, anomaly_report, target_url)
        return json.dumps(summary, indent=2)
    except json.JSONDecodeError:
        return "Error: Invalid JSON format for payloads_json."
    except Exception as e:
        return f"Execution error: {e}"

def _auto_fan_test_findings(output: str, test_type: str, case_kind: str, run_folder: str, passed: bool = False) -> None:
    """
    Parse pytest output and auto-write findings.

    On FAILED tests: one finding per FAILED function (with severity based on status code).
    On PASSED tests: scan for unexpected status codes (e.g. 4xx on a happy-path test that
    "passes" via a loose assertion) and write a lower-severity note.
    Also writes an informational entry to api_results_log.jsonl for every call seen.
    """
    try:
        from core.context import live_report_var
        import re as _re, json as _json
        _lr = live_report_var.get(None)
        if _lr is None:
            return

        # ── 1. Always extract AEGIS_RESULT lines for the results log ──
        if run_folder:
            try:
                from pathlib import Path as _Path
                _results_path = _Path(run_folder) / "api_results_log.jsonl"
                _ts = __import__("datetime").datetime.now().isoformat(timespec="seconds")
                _aegis_re = _re.compile(r"AEGIS_RESULT:\s*(\{.*\})")
                for _line in output.splitlines():
                    _m = _aegis_re.search(_line)
                    if _m:
                        try:
                            _rec = _json.loads(_m.group(1))
                            _rec["ts"] = _ts
                            _rec["test_type"] = test_type
                            _rec["case_kind"] = case_kind
                            _rec["passed"] = passed
                            # Flag unexpected status
                            _st = _rec.get("status")
                            if isinstance(_st, int):
                                _rec["unexpected_status"] = (
                                    (case_kind == "happy" and _st >= 400) or
                                    (case_kind == "sad" and 200 <= _st < 300)
                                )
                            with open(_results_path, "a", encoding="utf-8") as _f:
                                _f.write(_json.dumps(_rec, ensure_ascii=False) + "\n")
                        except Exception:
                            pass
            except Exception:
                pass

        # ── 2. FAILED tests: one finding per FAILED function ──
        failed_lines = _re.findall(r"FAILED\s+(\S+::test_\w+)\s*-?\s*(.*)", output)
        for test_id, reason in failed_lines:
            reason = reason.strip()
            evidence_match = _re.search(
                rf"{_re.escape(test_id.split('::')[1])}.*?\n(.*?E\s+assert.*?)(?=\n[A-Z_]|\Z)",
                output, _re.DOTALL
            )
            evidence = evidence_match.group(1).strip()[:400] if evidence_match else reason[:400]

            if "404" in evidence or "404" in reason:
                sev, summary = "medium", "Endpoint not found (404) — URL may be wrong or not deployed"
                cat = "api"
            elif "500" in evidence or "500" in reason:
                sev, summary = "high", "Server error (500) — potential unhandled exception"
                cat = "runtime"
            elif "401" in evidence or "401" in reason or "403" in evidence or "403" in reason:
                sev, summary = "medium", "Auth/permission failure — check token or role requirements"
                cat = "security_candidate"
            elif "400" in evidence or "400" in reason or "422" in evidence or "422" in reason:
                sev, summary = "low", "Validation rejection (400/422) — schema or field mismatch"
                cat = "api"
            else:
                sev, summary = "low", f"Functional test assertion failed ({test_type}/{case_kind or 'auto'})"
                cat = "api"

            _lr.append_finding_sync({
                "title": f"[AUTO] Test failure: {test_id.split('::')[-1]}",
                "category": cat,
                "severity": sev,
                "summary": summary,
                "evidence": evidence,
                "reproduction_steps": f"Run: pytest {test_id}",
                "file_path": test_id.split("::")[0],
                "vision_step": 3,
                "auto": True,
            })

        # ── 3. PASSED tests: flag unexpected status codes in AEGIS_RESULT lines ──
        if passed:
            _aegis_re2 = _re.compile(r"AEGIS_RESULT:\s*(\{.*\})")
            for _line in output.splitlines():
                _m = _aegis_re2.search(_line)
                if not _m:
                    continue
                try:
                    _rec = __import__("json").loads(_m.group(1))
                    _st = _rec.get("status")
                    if not isinstance(_st, int):
                        continue
                    _unexpected = (
                        (case_kind == "happy" and _st >= 400) or
                        (case_kind == "sad" and 200 <= _st < 300)
                    )
                    if _unexpected:
                        _url = _rec.get("url", "?")
                        _lr.append_finding_sync({
                            "title": f"[AUTO] Unexpected {_st} on {case_kind}-path: {_rec.get('method','?')} {_url}",
                            "category": "api",
                            "severity": "medium" if _st >= 500 else "low",
                            "summary": (
                                f"Test passed but received HTTP {_st} on a {case_kind}-path call to {_url}. "
                                "The assertion may be too loose."
                            ),
                            "evidence": _json.dumps(_rec, ensure_ascii=False)[:400],
                            "reproduction_steps": f"Check test asserting {case_kind} behaviour on {_url}",
                            "file_path": "",
                            "vision_step": 3,
                            "auto": True,
                        })
                except Exception:
                    pass

    except Exception as _e:
        logger.debug("_auto_fan_test_findings error: %s", _e)


@tool
def run_functional_test(
    test_code: str,
    target_dir: str,
    test_type: str = "api",
    case_kind: str = "",
    run_folder: str = "",
) -> str:
    """
    Writes a Pytest script to the target directory and executes it.
    Validates that required dependencies are installed before running tests.

    The generated test source AND the execution result are persisted to the run
    folder (under testcases/ and generated_tests.json) so the suite survives the
    run. Always provide BOTH a "happy" and a "sad" case for each endpoint or
    behaviour you cover.

    IMPORTANT: Before calling this, verify the target endpoint exists in
    endpoint_map.json (use read_run_folder_file). If the endpoint is missing,
    update api_test_data.json first via write_run_folder_file.

    On FAILED tests, findings are automatically written to the live report.
    You still MUST call record_finding for any confirmed vulnerability with
    full evidence and a suggested fix.

    Args:
        test_code: The test code to execute
        target_dir: Directory to run tests in
        test_type: Type of test - 'api', 'auth', 'crud', 'validation', 'idor',
                   'django_model', 'load', 'e2e_journey', 'e2e_react'
        case_kind: "happy" (positive path) or "sad" (negative/error path).
                   Auto-inferred from the code if omitted.
        run_folder: Per-run output directory. When set, the generated test is
                    archived under <run_folder>/testcases/ and the execution
                    result appended to <run_folder>/generated_tests.json.

    Returns:
        String with execution status and output
    """
    logger.info(
        "Tool Call: Executing Pytest functional test (type=%s, kind=%s)",
        test_type, case_kind or "auto",
    )
    try:
        qa = QAEngineer(target_dir=target_dir, run_folder=run_folder or None)
        passed, output = qa.run_functional_test(
            test_code,
            test_type=test_type,
            case_kind=case_kind or None,
        )
        status = "PASSED" if passed else "FAILED"
        _auto_fan_test_findings(output, test_type, case_kind, run_folder, passed=passed)
        return f"Status: {status}\nOutput:\n{output}"
    except Exception as e:
        return f"Execution error: {e}"

@tool
def propose_code_patch(file_path: str, proposed_code: str, target_dir: str) -> str:
    """
    Proposes a fix to the main application code if a crash or bug is found.
    This saves the patched file to a `.aegis_patches/` directory in the target for developer review.
    """
    logger.info(f"Tool Call: Proposing code patch for {file_path}")
    try:
        qa = QAEngineer(target_dir=target_dir)
        result = qa.propose_code_patch(file_path, proposed_code)
        return result
    except Exception as e:
        return f"Patch generation error: {e}"

@tool
def save_vulnerability_report(report_markdown: str, filename: str = "agent_report.md", run_folder: str = "") -> str:
    """
    Saves a synthesized vulnerability and chaos engineering report.
    If run_folder is provided (the per-run output directory), the report is written there.
    Otherwise it falls back to the top-level reports/ directory.
    """
    logger.info("Tool Call: Saving Vulnerability Report to %s", filename)
    try:
        out_dir = Path(run_folder) if run_folder else Path("reports")
        out_dir.mkdir(parents=True, exist_ok=True)
        filepath = out_dir / filename
        filepath.write_text(report_markdown, encoding="utf-8")

        # Also append synthesis to the live report (sync — see record_finding rationale)
        try:
            from core.context import live_report_var
            _lr = live_report_var.get(None)
            if _lr is not None:
                _lr.append_section_sync("Agent Synthesis", report_markdown)
        except Exception as _e:
            logger.warning("live_report synthesis append failed: %s", _e)

        return f"Successfully saved report to {filepath}"
    except Exception as e:
        return f"Error saving report: {e}"


_BOILERPLATE_ALIASES = {
    "api": "api_test_boilerplate.py",
    "model": "model_test_boilerplate.py",
    "auth": "api_auth_test_boilerplate.py",
    "crud": "api_crud_test_boilerplate.py",
    "validation": "api_input_validation_test_boilerplate.py",
    "idor": "api_idor_test_boilerplate.py",
    "django_model": "django_model_advanced_test_boilerplate.py",
    "load": "load_test_boilerplate.py",
    "e2e_journey": "e2e_user_journey_test_boilerplate.py",
    "e2e_react": "e2e_react_ui_test_boilerplate.py",
    # Multi-endpoint sweep — tests 5-10 endpoints per file (preferred for coverage)
    "endpoint_sweep": "api_endpoint_sweep_boilerplate.py",
    "sweep": "api_endpoint_sweep_boilerplate.py",
    # Security-specific boilerplates
    "security_jwt": "security_jwt_test_boilerplate.py",
    "security_cors": "security_cors_test_boilerplate.py",
    "security_headers": "security_headers_test_boilerplate.py",
}

_BOILERPLATE_DIR = Path(__file__).resolve().parent.parent / "agent_assets" / "test_boilerplates"


@tool
def copy_test_boilerplate(boilerplate_name: str, run_folder: str) -> str:
    """
    Copies a test boilerplate into the run folder's testcases/ directory.

    boilerplate_name: "api" or "model" (or the exact filename without extension).
    run_folder: the per-run output directory (absolute path), e.g. reports/myproject_20240501_120000.

    Returns the absolute path to the copied file so you can read it, edit it in-place
    with propose_code_patch, and then execute it with run_functional_test.
    """
    logger.info("Tool Call: copy_test_boilerplate(%s) → %s", boilerplate_name, run_folder)
    try:
        filename = _BOILERPLATE_ALIASES.get(boilerplate_name.lower(), boilerplate_name)
        if not filename.endswith(".py"):
            filename += ".py"

        src = _BOILERPLATE_DIR / filename
        if not src.exists():
            available = [p.name for p in _BOILERPLATE_DIR.glob("*.py")]
            return (
                f"Error: boilerplate '{filename}' not found in {_BOILERPLATE_DIR}. "
                f"Available: {available}"
            )

        dest_dir = Path(run_folder) / "testcases"
        dest_dir.mkdir(parents=True, exist_ok=True)

        stamp = datetime.now().strftime("%H%M%S")
        stem = Path(filename).stem
        dest = dest_dir / f"{stem}_{stamp}.py"
        shutil.copy2(src, dest)

        return str(dest.resolve())
    except Exception as e:
        return f"Error copying boilerplate: {e}"

@tool
def execute_claude_code_task(task: str, target_dir: str) -> str:
    """
    Delegates a complex coding or investigation task to the 'claude' CLI (Claude Code).
    Use this for high-level refactoring or multi-file architectural changes.
    """
    logger.info(f"Tool Call: Delegating to Claude Code: {task}")
    try:
        manager = ProviderManager(target_dir=target_dir)
        return manager.run("claude", task)
    except Exception as e:
        return f"Execution error: {e}"

@tool
def execute_gemini_cli_task(prompt: str, target_dir: str) -> str:
    """
    Delegates a reasoning or code analysis task to the 'gemini' CLI.
    """
    logger.info(f"Tool Call: Delegating to Gemini CLI: {prompt}")
    try:
        manager = ProviderManager(target_dir=target_dir)
        return manager.run("gemini", prompt)
    except Exception as e:
        return f"Execution error: {e}"

@tool
def generate_dependency_graph(target_dir: str, output_path: str = "") -> str:
    """
    Generates an interactive Plotly Dash dependency graph of the project structure,
    including file→file import edges, function call edges, and class inheritance edges.
    Saves a Python script to the run folder. Run it with: python <path>
    """
    logger.info(f"Tool Call: Generating dependency graph for {target_dir}")
    try:
        from skills.graph_3d import Graph3DGenerator
        grapher = ASTGrapher(root_dir=target_dir)
        ast_data = grapher.analyze_project()
        dest = output_path or str(Path("reports") / "dependency_graph.py")
        path = Graph3DGenerator().generate(ast_data, dest)
        n = len(ast_data.get("files", {}))
        c = len(ast_data.get("call_edges", []))
        i = len(ast_data.get("inheritance_edges", []))
        return (
            f"Dependency graph saved to {path}\n"
            f"  {n} files · {c} call edges · {i} inheritance edges\n"
            f"Launch viewer: python {path}"
        )
    except Exception as e:
        return f"Error generating dependency graph: {e}"

@tool
def calculate_project_quality(findings_json: str, tests_passed: int, tests_total: int) -> str:
    """
    Calculates a project quality score (0-100) and endpoint risk scores.
    findings_json: A JSON list of findings.
    """
    logger.info("Tool Call: Calculating quality scores")
    try:
        findings = json.loads(findings_json)
        viz = Visualizer()
        scores = viz.calculate_scores(findings, tests_passed, tests_total)
        return json.dumps(scores, indent=2)
    except Exception as e:
        return f"Error calculating scores: {e}"

@tool
def generate_campaign_visuals(campaign_id: str, tool_runs_json: str, findings_json: str) -> str:
    """
    Generates failure rate charts and vulnerability maps for a campaign.
    Returns paths to the generated HTML report files.
    """
    logger.info(f"Tool Call: Generating visuals for campaign {campaign_id}")
    try:
        tool_runs = json.loads(tool_runs_json)
        findings = json.loads(findings_json)
        viz = Visualizer()
        paths = viz.generate_campaign_charts(campaign_id, tool_runs, findings)
        return json.dumps(paths, indent=2)
    except Exception as e:
        return f"Error generating visuals: {e}"

@tool
def execute_codex_cli_task(task: str, target_dir: str) -> str:
    """
    Delegates a coding task to the 'codex' CLI.
    """
    logger.info(f"Tool Call: Delegating to Codex CLI: {task}")
    try:
        manager = ProviderManager(target_dir=target_dir)
        return manager.run("codex", task)
    except Exception as e:
        return f"Execution error: {e}"

@tool
def record_finding(
    campaign_id: str,
    vision_step: int,
    title: str,
    category: str,
    severity: str,
    summary: str,
    evidence: str = "",
    reproduction_steps: str = "",
    suggested_fix: str = "",
    file_path: str = "",
    line_number: int = None
) -> str:
    """
    Records a finding discovered by an AI agent during a campaign explicitly tagged with a vision_step (1-7).
    Use this to persist identified vulnerabilities or issues into the final campaign report.
    """
    logger.info(f"Tool Call: Recording finding '{title}' for step {vision_step}")

    # Normalise category/severity without requiring Django models
    _VALID_CATS = {"syntax", "runtime", "api", "semantic", "security_candidate"}
    _VALID_SEVS = {"critical", "high", "medium", "low"}
    cat = category if category in _VALID_CATS else "runtime"
    sev = severity if severity in _VALID_SEVS else "medium"

    _finding_data = {
        "title": title, "category": cat, "severity": sev,
        "summary": summary, "evidence": evidence,
        "reproduction_steps": reproduction_steps,
        "suggested_fix": suggested_fix,
        "file_path": file_path, "line_number": line_number,
        "vision_step": vision_step,
    }

    # ── 1. Live report + findings.jsonl (always runs, no Django needed) ──
    try:
        from core.context import live_report_var
        _lr = live_report_var.get(None)
        if _lr is not None:
            _lr.append_finding_sync(_finding_data)
    except Exception as _e:
        logger.warning("live_report append failed: %s", _e)

    # ── 2. Django ORM (optional — only works when Campaign DB is available) ──
    try:
        from campaigns.models import Campaign, Finding
        campaign = Campaign.objects.get(id=campaign_id)
        cat_db = category if category in Finding.Category.values else Finding.Category.RUNTIME
        sev_db = severity if severity in Finding.Severity.values else Finding.Severity.MEDIUM
        Finding.objects.create(
            campaign=campaign,
            title=title[:255],
            category=cat_db,
            severity=sev_db,
            status="open",
            summary=summary,
            evidence=evidence,
            reproduction_steps=reproduction_steps,
            suggested_fix=suggested_fix,
            file_path=file_path,
            line_number=line_number,
            vision_step=vision_step,
        )
    except Exception as _db_e:
        # CLI mode: no Campaign DB row — that's expected; finding already written above
        logger.debug("Django ORM record skipped (CLI mode likely): %s", _db_e)

    return f"Successfully recorded finding '{title}' for vision step {vision_step}."

@tool
def request_user_input(question: str, input_type: str = "text") -> str:
    """
    Pause and ask the human operator for input during the campaign.

    Use this when you encounter an authentication challenge, missing API key,
    ambiguous configuration, or any decision that requires a human in the loop.

    input_type:
      - "credential": prompts for a sensitive value with masked input (passwords, API keys, tokens). 
                     You can also ask the user for a path to a credentials file (e.g. .toml) 
                     and then read it using read_project_file.
      - "text": prompts for a plain string (usernames, URLs, free-form clarification)

    Returns the value the user typed. Returns an empty string if HITL mode is
    not enabled (headless execution); detect this and either skip the action
    or report a configuration error in your finding.
    """
    logger.info("Tool Call: request_user_input — %s (type=%s)", question, input_type)
    try:
        from core.hitl import hitl
        sensitive = (input_type or "text").lower() == "credential"
        return hitl.request_credential(
            name=question,
            hint="The value will be returned to the agent.",
            sensitive=sensitive,
        )
    except Exception as exc:
        return f"HITL request failed: {exc}"


@tool
def get_credential(role: str = "default") -> str:
    """
    Retrieve a named credential for the target application under test.

    Resolution order:
      0. session_headers_var already set at startup      → return immediately (fastest path)
      1. token present in .faultline/credentials.toml    → use directly
      2. refresh_token in file                           → exchange for access token
      3. username+password in file + login_url configured → auto-login (bearer/api_key/cookie)
      4. basic auth with username+password               → encode directly, no login needed
      5. Unavailable — skip auth-dependent tests or call request_user_input explicitly

    role: the named credential set — e.g. "default", "admin", "user", "readonly".

    Returns a JSON string:
        {
          "role":       "admin",
          "token":      "eyJ...",        # empty for basic auth
          "username":   "admin@example.com",
          "password":   "...",
          "auth_header": {"Authorization": "Bearer eyJ..."},
          "auth_type":  "bearer",
          "source":     "session" | "file" | "refresh_token" | "file_login" | "basic" | "unavailable",
          "login_note": "..."            # present when auto-login was attempted
        }
    """
    logger.info("Tool Call: get_credential(role=%s)", role)
    import json as _json
    try:
        from core.credential_store import get_store
        from core.context import session_headers_var

        store = get_store()

        # ── 0. Session headers already populated at startup ──────────────────
        # faultline.py runs the full resolution chain before the agent starts.
        # For the default role we return those headers directly — no login needed.
        if role == "default":
            existing = session_headers_var.get()
            if existing:
                auth_type = store.auth_type() if store and store.loaded else "bearer"
                token = ""
                if "Authorization" in existing:
                    val = existing["Authorization"]
                    if val.startswith("Bearer "):
                        token = val[7:]
                elif "X-API-Key" in existing:
                    token = existing["X-API-Key"]
                username = ""
                if store and store.loaded:
                    cred = store.get(role)
                    if cred:
                        username = cred.get("username", "").strip() or cred.get("email", "").strip()
                return _json.dumps({
                    "role": role,
                    "token": token,
                    "username": username,
                    "password": "",
                    "auth_header": existing,
                    "auth_type": auth_type,
                    "source": "session",
                    "login_note": "Auth header pre-populated at startup from credentials.",
                })

        # ── 0.5 Vault Dynamic Auth Flows ─────────────────────────────────────
        try:
            from vault.models import AuthFlow
            from vault.services import Authenticator

            # Look for an AuthFlow by name (role)
            flow = AuthFlow.objects.filter(name__iexact=role).first()
            if flow:
                base_url = store.target_url() if store else os.environ.get("FAULTLINE_TARGET_URL", "")
                if base_url:
                    authenticator = Authenticator(base_url, flow)
                    vault_res = authenticator.execute_flow()
                    if vault_res["headers"] or vault_res["cookies"]:
                        auth_type = flow.auth_type
                        header = {**vault_res["headers"]}
                        if vault_res["cookies"]:
                            c_str = "; ".join(f"{k}={v}" for k, v in vault_res["cookies"].items())
                            header["Cookie"] = c_str

                        return _json.dumps({
                            "role": role,
                            "token": "[VAULT_MANAGED]",
                            "username": "[VAULT_MANAGED]",
                            "password": "",
                            "auth_header": header,
                            "auth_type": auth_type,
                            "source": "vault",
                            "login_note": f"Auth flow '{flow.name}' executed via Vault service.",
                        })
        except Exception as vault_err:
            logger.debug("Vault integration skipped or failed: %s", vault_err)

        # ── 1. Credentials file ──────────────────────────────────────────────
        if store and store.loaded:
            cred = store.get(role)
            if cred is not None:
                auth_type = store.auth_type()
                token = cred.get("token", "").strip()
                username = cred.get("username", "").strip()
                email = cred.get("email", "").strip()
                password = cred.get("password", "").strip()

                refresh_token = cred.get("refresh_token", "").strip()

                # ── 1a. Token already present in file ────────────────────────
                if token:
                    header = store.get_auth_header(role) or {}
                    return _json.dumps({
                        "role": role,
                        "token": token,
                        "username": username,
                        "password": "",
                        "auth_header": header,
                        "auth_type": auth_type,
                        "source": "file",
                    })

                # ── 1b. Refresh token → exchange for a fresh access token ────
                # Works for Google OAuth accounts and any JWT setup where
                # username/password login is unavailable or impractical.
                if refresh_token:
                    base_url = store.target_url().rstrip("/")
                    refresh_url = store.token_refresh_url()
                    access, note = _attempt_token_refresh(base_url, refresh_url, refresh_token)
                    if access:
                        header = store.get_auth_header(role, token_override=access) or {
                            "Authorization": f"Bearer {access}"
                        }
                        return _json.dumps({
                            "role": role,
                            "token": access,
                            "username": username,
                            "password": "",
                            "auth_header": header,
                            "auth_type": auth_type,
                            "source": "refresh_token",
                            "login_note": note,
                        })
                    else:
                        logger.warning(
                            "Token refresh failed for role '%s': %s — trying login flow",
                            role, note,
                        )

                # ── 1c. Basic auth — encode username:password directly ────────
                if auth_type == "basic":
                    header = store.get_auth_header(role) or {}
                    if header:
                        return _json.dumps({
                            "role": role,
                            "token": "",
                            "username": username,
                            "password": password,
                            "auth_header": header,
                            "auth_type": auth_type,
                            "source": "basic",
                        })

                # ── 1d. No token — attempt login with username/password ───────
                has_creds = password and (username or email)
                if has_creds and store.login_url():
                    base_url = store.target_url().rstrip("/")
                    login_path = store.login_url()
                    token_obtained, login_note = _attempt_login(
                        base_url, login_path, username, password, email=email
                    )
                    if token_obtained:
                        header = store.get_auth_header(role, token_override=token_obtained) or {
                            "Authorization": f"Bearer {token_obtained}"
                        }
                        return _json.dumps({
                            "role": role,
                            "token": token_obtained,
                            "username": username,
                            "password": "",
                            "auth_header": header,
                            "auth_type": auth_type,
                            "source": "file_login",
                            "login_note": login_note,
                        })
                    else:
                        logger.warning(
                            "Auto-login failed for role '%s': %s — returning unavailable",
                            role, login_note,
                        )

        # ── 2. Nothing available — do NOT auto-prompt via HITL ──────────────────
        # If you need a token interactively, call request_user_input explicitly.
        auth_type = store.auth_type() if store and store.loaded else "unknown"
        logger.warning(
            "get_credential: role '%s' could not be resolved from file or login flow. "
            "Call request_user_input to ask the operator for a token.",
            role,
        )
        return _json.dumps({
            "role": role,
            "token": "",
            "username": "",
            "password": "",
            "auth_header": {},
            "auth_type": auth_type,
            "source": "unavailable",
            "login_note": (
                f"Role '{role}' could not be resolved — token missing, "
                "login failed, or no credentials file found. "
                "Call request_user_input(input_type='credential') to ask the operator."
            ),
        })

    except Exception as exc:
        return f"get_credential error: {exc}"


def _attempt_token_refresh(
    base_url: str,
    refresh_url: str,
    refresh_token: str,
) -> tuple[str, str]:
    """
    Exchange a refresh token for a fresh access token.

    Handles both response styles:
      - Token in JSON body  (simplejwt default): {"access": "eyJ..."}
      - Token in Set-Cookie (dj_rest_auth HttpOnly mode): access_token cookie

    Returns (access_token, note). access_token is "" on failure.
    """
    import httpx

    url = base_url.rstrip("/") + "/" + refresh_url.lstrip("/")
    try:
        resp = httpx.post(
            url,
            json={"refresh": refresh_token},
            headers={"Accept": "application/json"},
            timeout=15,
            follow_redirects=True,
        )
        if resp.status_code in (200, 201):
            # Strategy A: token in JSON body
            try:
                data = resp.json()
                for key in ("access", "access_token", "token"):
                    if data.get(key):
                        note = f"Token refreshed via POST {url} (key='{key}')"
                        logger.info(note)
                        return str(data[key]), note
            except Exception:
                pass
            # Strategy B: token in HttpOnly Set-Cookie
            for name in ("access_token", "access", "token"):
                val = resp.cookies.get(name, "")
                if val:
                    note = f"Token refreshed via POST {url} (cookie='{name}')"
                    logger.info(note)
                    return val, note
            try:
                body_preview = resp.text[:300]
            except Exception:
                body_preview = "(unreadable)"
            return "", f"Refresh endpoint returned {resp.status_code} but no access token found. Response: {body_preview}"
        else:
            try:
                err = resp.json()
            except Exception:
                err = resp.text[:300]
            msg = f"Refresh endpoint returned HTTP {resp.status_code}: {err}"
            logger.warning("_attempt_token_refresh: %s", msg)
            return "", msg
    except Exception as exc:
        msg = f"Request to {url} failed: {exc}"
        logger.warning("_attempt_token_refresh: %s", msg)
        return "", msg


def _attempt_login(
    base_url: str,
    login_path: str,
    username: str,
    password: str,
    email: str = "",
) -> tuple[str, str]:
    """
    POST credentials to the login endpoint and extract the token from the response.

    Handles three authentication styles automatically:
      1. simplejwt style       — {"username": ..., "password": ...}  → token in JSON body
      2. allauth/dj-rest-auth  — {"email": ..., "password": ...}     → token in JSON body
      3. HttpOnly cookie auth  — any of the above                    → token in Set-Cookie header
         (used when REST_AUTH = {"USE_JWT": True, "JWT_AUTH_HTTPONLY": True})

    When `email` is provided it is used as the email field value; otherwise
    `username` is tried as both the username and email field values.

    Returns (token_string, note). token_string is "" on all failures.
    """
    import httpx

    login_url = base_url.rstrip("/") + "/" + login_path.lstrip("/")
    token_keys = ["token", "access_token", "access", "key", "auth_token", "jwt"]
    # Cookie names used by dj_rest_auth JWT_AUTH_COOKIE / REFRESH_COOKIE settings
    cookie_names = ["access_token", "access", "token", "jwt", "auth_token"]

    # Build attempt list — most specific first
    bodies: list[dict] = []
    if username:
        bodies.append({"username": username, "password": password})
    if email:
        bodies.append({"email": email, "password": password})
        if username:
            # Combined — some dj-rest-auth setups accept either field
            bodies.append({"username": username, "email": email, "password": password})
    elif username and "@" in username:
        # username looks like an email — try it as the email field too
        bodies.append({"email": username, "password": password})

    last_error = "no login attempts configured (missing username/email)"
    for body in bodies:
        try:
            resp = httpx.post(
                login_url,
                json=body,
                headers={"Accept": "application/json"},
                timeout=15,
                follow_redirects=True,
            )

            if resp.status_code in (200, 201):
                # ── Strategy A: token in JSON response body ──────────────────
                try:
                    data = resp.json()
                    # Top-level keys
                    for key in token_keys:
                        if data.get(key):
                            note = (
                                f"Login OK via POST {login_url} "
                                f"(body={list(body.keys())}, token_key='{key}')"
                            )
                            logger.info(note)
                            return str(data[key]), note
                    # One level deep — e.g. {"data": {"access_token": "..."}}
                    for v in data.values():
                        if isinstance(v, dict):
                            for key in token_keys:
                                if v.get(key):
                                    note = f"Login OK via POST {login_url} (nested key '{key}')"
                                    logger.info(note)
                                    return str(v[key]), note
                except Exception:
                    pass

                # ── Strategy B: token in HttpOnly Set-Cookie header ──────────
                # Used when REST_AUTH = {"USE_JWT": True, "JWT_AUTH_HTTPONLY": True}
                for name in cookie_names:
                    cookie_val = resp.cookies.get(name, "")
                    if cookie_val:
                        note = (
                            f"Login OK via POST {login_url} "
                            f"(body={list(body.keys())}, cookie='{name}')"
                        )
                        logger.info(note)
                        return cookie_val, note

                # 200 but no token found anywhere — log full response for diagnosis
                try:
                    body_preview = resp.text[:300]
                except Exception:
                    body_preview = "(unreadable)"
                last_error = (
                    f"HTTP {resp.status_code} but no token in body or cookies. "
                    f"Response: {body_preview}"
                )
                logger.warning("_attempt_login: %s", last_error)

            else:
                # Log the actual server error so the user can diagnose credential issues
                try:
                    err_detail = resp.json()
                except Exception:
                    err_detail = resp.text[:300]
                last_error = (
                    f"HTTP {resp.status_code} from {login_url} "
                    f"(body={list(body.keys())}): {err_detail}"
                )
                logger.warning("_attempt_login: %s", last_error)

        except Exception as exc:
            last_error = f"Request to {login_url} failed: {exc}"
            logger.warning("_attempt_login: %s", last_error)

    logger.warning("_attempt_login: all attempts failed. Last error: %s", last_error)
    return "", last_error


@tool
def retrieve_stored_content(run_folder: str, ref_id: str) -> str:
    """
    Retrieves the full content of a previously summarised tool result.
    Use this whenever you see a [REF:<id>] marker in the conversation
    and need the complete output to continue your analysis.
    All content is stored in the run_folder for this session.
    """
    store_path = Path(run_folder) / "content_store" / f"{ref_id}.txt"
    if not store_path.exists():
        return f"Error: No stored content found for ref_id '{ref_id}' in {run_folder}/content_store/"
    return store_path.read_text(encoding="utf-8")


@tool
def summarize_to_report(heading: str, content: str, run_folder: str) -> str:
    """
    Appends a freeform section to the live_report.md for the current run.
    Use this as an intermediate step to document progress, partial findings,
    or narrative summaries BEFORE you have enough information for a full
    record_finding() call.

    heading: A short section title, e.g. "Auth Testing Complete" or "Step 3 Summary".
    content: Freeform markdown content to append under the heading.
    run_folder: The per-run output directory (same value you use for other tools).
    """
    logger.info("Tool Call: summarize_to_report — heading='%s'", heading)
    try:
        from core.context import live_report_var
        _lr = live_report_var.get(None)
        if _lr is not None:
            _lr.append_section_sync(heading, content)
            return f"Section '{heading}' appended to live_report.md."

        # Fallback: write directly if live_report context is not set
        report_path = Path(run_folder) / "live_report.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)
        block = f"\n## {heading}\n\n{content}\n\n---\n"
        with open(report_path, "a", encoding="utf-8") as f:
            f.write(block)
            f.flush()
        return f"Section '{heading}' appended to {report_path}."
    except Exception as e:
        return f"Error appending to report: {e}"


@tool
def list_run_folder_files(run_folder: str) -> str:
    """
    Lists all files inside the per-run output directory (run_folder).
    Use this to discover what test scripts, schemas, logs, and reports have
    been generated for the current session. Returns a JSON array of relative paths.
    """
    logger.info("Tool Call: list_run_folder_files — %s", run_folder)
    try:
        base = Path(run_folder)
        if not base.exists():
            return f"Error: run_folder '{run_folder}' does not exist."
        files = sorted(
            str(p.relative_to(base))
            for p in base.rglob("*")
            if p.is_file()
        )
        return json.dumps(files, indent=2)
    except Exception as e:
        return f"Error listing run folder: {e}"


@tool
def record_decision(
    situation: str,
    decision: str,
    rationale: str,
    run_folder: str,
    expected_outcome: str = "",
) -> str:
    """
    Record a decision you are about to make into agent_flow.md.
    Call this BEFORE each significant tool call to document your reasoning.
    This gives the operator a clear narrative of why each action was taken.

    situation: What you observed / what prompted this decision.
    decision: What you decided to do next (e.g. "run auth happy-path test").
    rationale: Why this is the right next step.
    expected_outcome: What you expect to happen (optional).
    run_folder: Per-run output directory.
    """
    logger.info("Tool Call: record_decision — %s", decision[:80])
    try:
        flow_path = Path(run_folder) / "agent_flow.md"
        ts = datetime.now().strftime("%H:%M:%S")

        # Write header if file is new
        if not flow_path.exists():
            flow_path.parent.mkdir(parents=True, exist_ok=True)
            flow_path.write_text(
                "# Agent Decision Flow\n\n"
                "Each block below captures one decision the agent made: "
                "what situation it saw, what it chose to do, and why.\n\n---\n",
                encoding="utf-8",
            )

        block = (
            f"\n### {ts} — {decision[:80]}\n\n"
            f"**Situation:** {situation}\n\n"
            f"**Decision:** {decision}\n\n"
            f"**Why:** {rationale}\n\n"
        )
        if expected_outcome:
            block += f"**Expected outcome:** {expected_outcome}\n\n"
        block += "---\n"

        with open(flow_path, "a", encoding="utf-8") as f:
            f.write(block)
            f.flush()
        return f"Decision logged to agent_flow.md."
    except Exception as e:
        return f"Error writing decision: {e}"


@tool
def read_run_folder_file(run_folder: str, relative_path: str, max_chars: int = 8000, skip_cache: bool = False) -> str:
    """
    Reads a file from the per-run output directory (run_folder).
    Use this to inspect api_schemas.json, api_test_data.json, test scripts,
    generated test results, or any other file created during this session.

    relative_path: Path relative to run_folder, e.g. "api_schemas.json" or "testcases/test_auth.py".
    max_chars: Maximum characters to return (default 8000). Pass 0 for unlimited.
    skip_cache: Set True to force a fresh read even if the file is cached (e.g. after write_run_folder_file).

    NOTE: For reading multiple run-folder files at once use read_many_run_folder_files.
    """
    logger.info("Tool Call: read_run_folder_file — %s / %s", run_folder, relative_path)
    try:
        base = Path(run_folder).resolve()
        target = (base / relative_path).resolve()
        if not str(target).startswith(str(base)):
            return "Error: Path traversal outside run_folder is not allowed."
        if not target.exists():
            return f"Error: '{relative_path}' not found in run_folder '{run_folder}'."
        if not target.is_file():
            return f"Error: '{relative_path}' is not a file."

        text = target.read_text(encoding="utf-8", errors="replace")
        sha = _file_sha(text)

        # Dedup check — only for mutable artifacts worth caching
        if not skip_cache:
            cache = _get_read_cache(run_folder)
            cache_key = f"runfolder::{relative_path}"
            cached = cache.get(cache_key)
            if cached and cached.get("sha") == sha:
                excerpt = text[:300].rstrip()
                return json.dumps({
                    "cached": True,
                    "path": relative_path,
                    "ref_id": cached.get("ref_id", ""),
                    "turn": cached.get("turn", ""),
                    "excerpt": excerpt,
                    "note": (
                        f"Already read this session (content unchanged) → "
                        f"REF:{cached.get('ref_id','')}. "
                        "Pass skip_cache=true to force a fresh read after write_run_folder_file."
                    ),
                }, indent=2)
            ref_id = f"run_folder__{relative_path.replace('/', '_').replace(chr(92), '_').replace('.', '_')}"
            _update_read_cache(run_folder, cache_key, ref_id, "current", sha)

        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncated at {max_chars} chars ...]"
        return text
    except Exception as e:
        return f"Error reading run folder file: {e}"


# ---------------------------------------------------------------------------
# Bulk read tools — fetch multiple files in a single LLM round-trip
# ---------------------------------------------------------------------------

_READ_CACHE_FILE = "read_cache.json"
_MAX_BULK_FILES = 30


def _get_read_cache(run_folder: str) -> dict:
    """Load per-run read cache from disk. Returns {} on any error."""
    if not run_folder:
        return {}
    try:
        p = Path(run_folder) / _READ_CACHE_FILE
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _update_read_cache(run_folder: str, path_key: str, ref_id: str, turn_hint: str, sha: str) -> None:
    """Upsert one entry in the read cache."""
    if not run_folder:
        return
    try:
        p = Path(run_folder) / _READ_CACHE_FILE
        cache = _get_read_cache(run_folder)
        cache[path_key] = {"ref_id": ref_id, "turn": turn_hint, "sha": sha}
        p.write_text(json.dumps(cache, indent=2), encoding="utf-8")
    except Exception:
        pass


def _file_sha(content: str) -> str:
    import hashlib
    return hashlib.md5(content.encode("utf-8", errors="replace")).hexdigest()[:8]


@tool
def read_many_project_files(
    target_dir: str,
    paths: list,
    run_folder: str = "",
    max_lines_each: int = 200,
) -> str:
    """
    Reads multiple project files in a single call, returning a combined JSON map.
    Use this whenever you need to read 2+ independent files — it replaces N separate
    read_project_file calls with 1, saving N-1 LLM round-trips.

    paths: list of relative paths inside target_dir, e.g. ["core/urls.py", "orchestrator/urls.py"].
    run_folder: optional — when provided, updates the read cache so repeated reads are skipped.
    max_lines_each: line limit applied per file (default 200).

    Returns: JSON object keyed by relative path, each value:
      {"content": "...", "total_lines": N, "truncated": bool, "sha": "..."}
    Already-cached unchanged files are noted as "cached" with their ref_id.
    """
    logger.info("Tool Call: read_many_project_files — %d paths in %s", len(paths or []), target_dir)
    if not paths:
        return json.dumps({"error": "paths list is empty"})

    cache = _get_read_cache(run_folder) if run_folder else {}
    reader = ProjectFileReader(target_dir)
    result: dict = {}
    cached_count = 0

    for rel_path in paths[:_MAX_BULK_FILES]:
        try:
            raw = reader.read_file(rel_path, start_line=1, max_lines=max_lines_each)
            content = raw.get("content", "") if isinstance(raw, dict) else str(raw)
            total = raw.get("total_lines", content.count("\n") + 1) if isinstance(raw, dict) else content.count("\n") + 1
            truncated = total > max_lines_each
            sha = _file_sha(content)

            cached = cache.get(rel_path)
            if cached and cached.get("sha") == sha:
                cached_count += 1
                result[rel_path] = {
                    "cached": True,
                    "ref_id": cached.get("ref_id", ""),
                    "turn": cached.get("turn", ""),
                    "sha": sha,
                    "note": (
                        f"Already read (sha unchanged) → REF:{cached.get('ref_id','')}. "
                        "Call retrieve_stored_content if you need the full content."
                    ),
                }
            else:
                ref_id = f"bulk_read__{rel_path.replace('/', '_').replace(chr(92), '_').replace('.', '_')}__new"
                if run_folder:
                    _update_read_cache(run_folder, rel_path, ref_id, "current", sha)
                result[rel_path] = {
                    "content": content,
                    "total_lines": total,
                    "truncated": truncated,
                    "sha": sha,
                }
        except Exception as exc:
            result[rel_path] = {"error": str(exc)}

    if cached_count:
        result["_cache_note"] = f"{cached_count}/{len(paths)} file(s) unchanged since last read — returned cache hints only."

    return json.dumps(result, indent=2)


@tool
def read_many_run_folder_files(
    run_folder: str,
    relative_paths: list,
    max_chars_each: int = 6000,
) -> str:
    """
    Reads multiple run-folder files in a single call.
    Use instead of repeated read_run_folder_file calls whenever you need 2+ artifacts
    (e.g., api_test_data.json + generated_tests.json + api_schemas.json together).

    relative_paths: list of paths relative to run_folder.
    max_chars_each: character limit per file (default 6000).

    Returns: JSON object keyed by relative path.
    """
    logger.info("Tool Call: read_many_run_folder_files — %d paths", len(relative_paths or []))
    if not relative_paths:
        return json.dumps({"error": "relative_paths list is empty"})

    base = Path(run_folder).resolve()
    result: dict = {}

    for rel in relative_paths[:_MAX_BULK_FILES]:
        try:
            target = (base / rel).resolve()
            if not str(target).startswith(str(base)):
                result[rel] = {"error": "path traversal not allowed"}
                continue
            if not target.exists():
                result[rel] = {"error": f"not found in run_folder"}
                continue
            if not target.is_file():
                result[rel] = {"error": "not a file"}
                continue
            text = target.read_text(encoding="utf-8", errors="replace")
            truncated = len(text) > max_chars_each
            if truncated:
                text = text[:max_chars_each] + f"\n\n[... truncated at {max_chars_each} chars ...]"
            result[rel] = {"content": text, "truncated": truncated}
        except Exception as exc:
            result[rel] = {"error": str(exc)}

    return json.dumps(result, indent=2)


@tool
def glob_and_read(
    target_dir: str,
    glob: str,
    run_folder: str = "",
    max_files: int = 20,
    max_lines_each: int = 150,
) -> str:
    """
    Finds all files matching a glob pattern inside target_dir and reads them all at once.
    This is the single most efficient way to collect a category of files (e.g., all urls.py,
    all serializers.py, all models.py) in one LLM round-trip.

    Examples:
      glob_and_read(target_dir, "**/urls.py")       — all URL configs
      glob_and_read(target_dir, "**/serializers.py") — all serializers
      glob_and_read(target_dir, "**/models.py")      — all models
      glob_and_read(target_dir, "**/views.py")       — all view files

    Returns: JSON with two keys:
      "files_found": list of matched relative paths
      "contents": {path: {content, total_lines, truncated, sha}} map
    Also writes a parsed endpoint_map.json to run_folder when glob is "**/urls.py".
    """
    logger.info("Tool Call: glob_and_read — glob='%s' in %s", glob, target_dir)
    try:
        reader = ProjectFileReader(target_dir)
        matched = reader.list_files(glob=glob, limit=max_files)
        if not matched:
            return json.dumps({"files_found": [], "contents": {}, "note": f"No files matched '{glob}'"})

        cache = _get_read_cache(run_folder) if run_folder else {}
        contents: dict = {}

        for rel_path in matched[:max_files]:
            try:
                raw = reader.read_file(rel_path, start_line=1, max_lines=max_lines_each)
                content = raw.get("content", "") if isinstance(raw, dict) else str(raw)
                total = raw.get("total_lines", content.count("\n") + 1) if isinstance(raw, dict) else content.count("\n") + 1
                truncated = total > max_lines_each
                sha = _file_sha(content)

                cached = cache.get(rel_path)
                if cached and cached.get("sha") == sha:
                    contents[rel_path] = {
                        "cached": True,
                        "ref_id": cached.get("ref_id", ""),
                        "sha": sha,
                        "note": f"Unchanged since last read → REF:{cached.get('ref_id','')}",
                    }
                else:
                    slug = rel_path.replace("/", "_").replace("\\", "_").replace(".", "_")
                    ref_id = f"glob_read__{slug}"
                    if run_folder:
                        _update_read_cache(run_folder, rel_path, ref_id, "current", sha)
                    contents[rel_path] = {
                        "content": content,
                        "total_lines": total,
                        "truncated": truncated,
                        "sha": sha,
                    }
            except Exception as exc:
                contents[rel_path] = {"error": str(exc)}

        # Auto-parse endpoint_map when reading urls.py files
        if "urls.py" in glob.lower() and run_folder:
            _extract_and_save_endpoint_map(contents, run_folder)

        return json.dumps({"files_found": matched, "contents": contents}, indent=2)
    except Exception as e:
        return f"Error in glob_and_read: {e}"


def _extract_and_save_endpoint_map(contents: dict, run_folder: str) -> None:
    """Parse url patterns from urls.py content blobs and save endpoint_map.json."""
    import re as _re
    endpoints: list = []
    for file_path, data in contents.items():
        if isinstance(data, dict) and "content" in data:
            text = data["content"]
            for m in _re.finditer(
                r"path\(\s*['\"]([^'\"]+)['\"].*?(?:name=['\"]([^'\"]+)['\"])?",
                text
            ):
                endpoints.append({
                    "path": m.group(1),
                    "name": m.group(2) or "",
                    "file": file_path,
                })
    if not endpoints:
        return
    try:
        ep_path = Path(run_folder) / "endpoint_map.json"
        existing: list = []
        if ep_path.exists():
            try:
                existing = json.loads(ep_path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        known = {e["path"] for e in existing}
        new_entries = [e for e in endpoints if e["path"] not in known]
        if new_entries:
            existing.extend(new_entries)
            ep_path.write_text(json.dumps(existing, indent=2), encoding="utf-8")
    except Exception:
        pass


@tool
def write_run_folder_file(run_folder: str, relative_path: str, content: str) -> str:
    """
    Writes (or overwrites) a file inside the per-run output directory.
    Use this to update api_test_data.json with correct endpoints, save notes,
    or correct any run-folder artifact that the agent has discovered is wrong.

    Allowed paths (sandboxed to prevent unintended writes):
      - api_test_data.json
      - endpoint_map.json
      - notes/<any>.md
      - testcases/<any>.py

    relative_path: path relative to run_folder.
    content: full file content to write (UTF-8).
    """
    logger.info("Tool Call: write_run_folder_file — %s / %s", run_folder, relative_path)
    _ALLOWLIST_PREFIXES = ("api_test_data.json", "endpoint_map.json", "notes/", "testcases/")
    _ALLOWLIST_EXACT = {"api_test_data.json", "endpoint_map.json"}

    norm = relative_path.replace("\\", "/").lstrip("/")
    allowed = norm in _ALLOWLIST_EXACT or any(norm.startswith(p) for p in ("notes/", "testcases/"))
    if not allowed:
        return (
            f"Error: '{relative_path}' is not in the write allowlist. "
            f"Allowed: api_test_data.json, endpoint_map.json, notes/*.md, testcases/*.py"
        )

    try:
        base = Path(run_folder).resolve()
        target = (base / norm).resolve()
        if not str(target).startswith(str(base)):
            return "Error: Path traversal outside run_folder is not allowed."
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return f"Written {len(content)} chars to {target}."
    except Exception as e:
        return f"Error writing file: {e}"


def resolve_credential_at_startup(store) -> "dict | None":
    """
    Run the full credential resolution chain for the 'default' role.
    Called from faultline.py before the agent starts so session_headers_var
    is pre-populated and the agent never needs to trigger HITL for auth.

    Returns the auth header dict (e.g. {"Authorization": "Bearer eyJ..."})
    or None if resolution fails.
    """
    # 0. Vault Dynamic Auth Flows
    try:
        from vault.models import AuthFlow
        from vault.services import Authenticator
        flow = AuthFlow.objects.filter(name__iexact="default").first()
        if flow:
            base_url = store.target_url().rstrip("/") if store else os.environ.get("FAULTLINE_TARGET_URL", "")
            if base_url:
                authenticator = Authenticator(base_url, flow)
                vault_res = authenticator.execute_flow()
                if vault_res["headers"] or vault_res["cookies"]:
                    header = {**vault_res["headers"]}
                    if vault_res["cookies"]:
                        c_str = "; ".join(f"{k}={v}" for k, v in vault_res["cookies"].items())
                        header["Cookie"] = c_str
                    logger.info("Startup auth: resolved via Vault flow 'default'")
                    return header
    except Exception as vault_err:
        logger.debug("Vault startup auth skipped: %s", vault_err)

    if not store or not store.loaded:
        return None
    cred = store.get("default")
    if not cred:
        return None

    auth_type = store.auth_type()
    token = cred.get("token", "").strip()
    username = cred.get("username", "").strip()
    email = cred.get("email", "").strip()
    password = cred.get("password", "").strip()
    refresh_token = cred.get("refresh_token", "").strip()

    # 1. Static token in file
    if token:
        return store.get_auth_header("default")

    # 2. Refresh token → exchange for access token
    if refresh_token:
        base_url = store.target_url().rstrip("/")
        refresh_url = store.token_refresh_url()
        access, note = _attempt_token_refresh(base_url, refresh_url, refresh_token)
        if access:
            logger.info("Startup auth: token obtained via refresh — %s", note)
            return store.get_auth_header("default", token_override=access) or {
                "Authorization": f"Bearer {access}"
            }
        logger.warning("Startup auth: refresh token failed — %s", note)

    # 3. Basic auth — no HTTP call needed
    if auth_type == "basic":
        header = store.get_auth_header("default")
        if header:
            return header

    # 4. Login flow
    if password and (username or email) and store.login_url():
        base_url = store.target_url().rstrip("/")
        login_path = store.login_url()
        token_obtained, note = _attempt_login(
            base_url, login_path, username, password, email=email
        )
        if token_obtained:
            logger.info("Startup auth: token obtained via login — %s", note)
            return store.get_auth_header("default", token_override=token_obtained) or {
                "Authorization": f"Bearer {token_obtained}"
            }
        logger.warning("Startup auth: login failed — %s", note)

    return None


@tool
def fetch_endpoint_bundle(target_dir: str, run_folder: str = "") -> str:
    """
    Single-call endpoint discovery: reads ALL urls.py files, ALL serializers.py files,
    and the top-level project urls.py in one shot.

    Returns a JSON bundle with:
      - "endpoint_map": parsed list of {path, name, file} route entries
      - "urls_contents": {relative_path: content} for every urls.py found
      - "serializers_summary": {relative_path: first 60 lines} for every serializers.py
      - "files_indexed": total count of files read
      - "endpoint_map_saved": true if endpoint_map.json was written to run_folder

    Use this ONCE at the start of Discovery instead of calling glob_and_read or
    read_project_file repeatedly for URL and serializer files.
    """
    logger.info("Tool Call: fetch_endpoint_bundle for %s", target_dir)
    try:
        reader = ProjectFileReader(target_dir)
        bundle: dict = {"endpoint_map": [], "urls_contents": {}, "serializers_summary": {}, "files_indexed": 0}
        cache = _get_read_cache(run_folder) if run_folder else {}

        # ── Read all urls.py files ────────────────────────────────────────
        url_files = reader.list_files(glob="**/urls.py", limit=40)
        urls_contents: dict = {}
        for rel in url_files:
            try:
                sha_key = rel
                raw = reader.read_file(rel, start_line=1, max_lines=120)
                content = raw.get("content", "") if isinstance(raw, dict) else str(raw)
                sha = _file_sha(content)
                cached = cache.get(sha_key)
                if cached and cached.get("sha") == sha:
                    urls_contents[rel] = {"cached": True, "ref_id": cached["ref_id"], "sha": sha}
                else:
                    ref_id = f"urls__{rel.replace('/', '_').replace(chr(92), '_').replace('.', '_')}"
                    if run_folder:
                        _update_read_cache(run_folder, sha_key, ref_id, "bundle", sha)
                    urls_contents[rel] = {"content": content, "sha": sha}
                    bundle["files_indexed"] += 1
            except Exception as exc:
                urls_contents[rel] = {"error": str(exc)}

        # ── Parse endpoint map ────────────────────────────────────────────
        _extract_and_save_endpoint_map(urls_contents, run_folder)
        if run_folder:
            try:
                ep_path = Path(run_folder) / "endpoint_map.json"
                if ep_path.exists():
                    bundle["endpoint_map"] = json.loads(ep_path.read_text(encoding="utf-8"))
                    bundle["endpoint_map_saved"] = True
            except Exception:
                pass

        bundle["urls_contents"] = urls_contents

        # ── Read serializers.py summaries (first 60 lines each) ──────────
        ser_files = reader.list_files(glob="**/serializers.py", limit=20)
        for rel in ser_files:
            try:
                raw = reader.read_file(rel, start_line=1, max_lines=60)
                content = raw.get("content", "") if isinstance(raw, dict) else str(raw)
                bundle["serializers_summary"][rel] = content
                bundle["files_indexed"] += 1
            except Exception as exc:
                bundle["serializers_summary"][rel] = f"Error: {exc}"

        n_routes = len(bundle["endpoint_map"])
        n_ser = len(bundle["serializers_summary"])
        bundle["_summary"] = (
            f"Discovered {n_routes} route(s) across {len(url_files)} urls.py file(s) "
            f"and {n_ser} serializer file(s). "
            f"endpoint_map.json {'saved to run_folder' if bundle.get('endpoint_map_saved') else 'not saved (no run_folder)'}."
        )
        return json.dumps(bundle, indent=2)
    except Exception as e:
        return f"Error in fetch_endpoint_bundle: {e}"


# ---------------------------------------------------------------------------
# Security campaign auto-fan + tools
# ---------------------------------------------------------------------------

def _auto_fan_security_findings(
    results: list,
    campaign_type: str,
    target_url: str,
    payloads: list = None,
) -> int:
    """
    Classify security campaign results and auto-write findings to live_report.
    Returns the count of findings written.
    """
    try:
        from core.context import live_report_var
        _lr = live_report_var.get(None)
        if _lr is None:
            return 0

        from skills.security_payloads import OWASP_MAP
        _REQUIRED_HEADERS = [
            "Content-Security-Policy", "Strict-Transport-Security",
            "X-Frame-Options", "X-Content-Type-Options", "Referrer-Policy",
        ]
        owasp = OWASP_MAP.get(campaign_type, "")
        count = 0

        for r in results:
            status = r.get("status_code")
            endpoint = r.get("endpoint", "")
            payload = r.get("payload", {})
            response_text = r.get("response_text", "") or ""
            attack_type = (payload or {}).get("_attack_type", campaign_type)

            if campaign_type == "idor_sweep":
                if status == 200:
                    _lr.append_finding_sync({
                        "title": f"[AUTO] Potential IDOR at {endpoint}",
                        "category": "security_candidate",
                        "severity": "high",
                        "summary": (
                            f"GET {endpoint} returned 200 with an alternate user's token. "
                            "Object-level authorization may not be enforced."
                        ),
                        "evidence": response_text[:400],
                        "reproduction_steps": f"Request: {json.dumps(payload)[:200]}",
                        "vision_step": 4, "auto": True,
                    })
                    count += 1

            elif campaign_type == "cors_probe":
                # Check for reflected evil origin in response headers (stored in response_text)
                evil_origin = "evil-attacker.com"
                if evil_origin in response_text and "Access-Control" in response_text:
                    _lr.append_finding_sync({
                        "title": f"[AUTO] CORS misconfiguration at {endpoint}",
                        "category": "security_candidate",
                        "severity": "high",
                        "summary": (
                            f"Response at {endpoint} reflected the evil Origin header. "
                            "Check Access-Control-Allow-Credentials: true + reflected origin."
                        ),
                        "evidence": response_text[:400],
                        "vision_step": 4, "auto": True,
                    })
                    count += 1

            elif campaign_type == "header_audit":
                missing = [h for h in _REQUIRED_HEADERS if h.lower() not in response_text.lower()]
                for h in missing:
                    _lr.append_finding_sync({
                        "title": f"[AUTO] Missing security header: {h} at {endpoint}",
                        "category": "security_candidate",
                        "severity": "medium",
                        "summary": f"Response from {endpoint} is missing the {h} header.",
                        "evidence": f"Response headers snippet: {response_text[:300]}",
                        "suggested_fix": f"Add {h} to Django's SECURE_* settings or middleware.",
                        "vision_step": 4, "auto": True,
                    })
                    count += 1

            elif campaign_type == "jwt_attacks":
                if status == 200:
                    variant = (payload or {}).get("_jwt_variant", "unknown")
                    _lr.append_finding_sync({
                        "title": f"[AUTO] JWT auth bypass ({variant}) at {endpoint}",
                        "category": "security_candidate",
                        "severity": "critical",
                        "summary": (
                            f"Endpoint {endpoint} returned 200 with a {variant} token. "
                            "JWT validation may be absent or bypassable."
                        ),
                        "evidence": response_text[:400],
                        "vision_step": 4, "auto": True,
                    })
                    count += 1

            elif campaign_type == "verb_tamper":
                if status in (200, 201):
                    advertised = (payload or {}).get("_advertised_methods", [])
                    _lr.append_finding_sync({
                        "title": f"[AUTO] Unexpected {r.get('method','?')} accepted at {endpoint}",
                        "category": "security_candidate",
                        "severity": "medium",
                        "summary": (
                            f"Endpoint {endpoint} accepted HTTP method {r.get('method')} "
                            f"which is not in its advertised methods {advertised}."
                        ),
                        "evidence": response_text[:300],
                        "vision_step": 4, "auto": True,
                    })
                    count += 1

            elif campaign_type == "injection_probe":
                # SSTI detection: 49 = 7*7
                if status == 500 or "49" in response_text or "traceback" in response_text.lower():
                    sev = "critical" if "traceback" in response_text.lower() else "high"
                    _lr.append_finding_sync({
                        "title": f"[AUTO] Possible injection at {endpoint}",
                        "category": "security_candidate",
                        "severity": sev,
                        "summary": (
                            f"Injection payload caused HTTP {status} or reflected output at {endpoint}. "
                            "Potential SQLi, SSTI, or command injection."
                        ),
                        "evidence": response_text[:400],
                        "reproduction_steps": f"Payload: {json.dumps(payload)[:300]}",
                        "vision_step": 4, "auto": True,
                    })
                    count += 1

            elif campaign_type == "mass_assignment":
                if status in (200, 201) and any(
                    f in response_text for f in ("is_staff", "is_admin", "is_superuser", "admin")
                ):
                    _lr.append_finding_sync({
                        "title": f"[AUTO] Mass assignment accepted at {endpoint}",
                        "category": "security_candidate",
                        "severity": "high",
                        "summary": (
                            f"Endpoint {endpoint} returned 200/201 with privilege fields in the payload. "
                            "Check if is_staff/is_admin persisted."
                        ),
                        "evidence": response_text[:400],
                        "vision_step": 4, "auto": True,
                    })
                    count += 1

            elif campaign_type == "rate_limit_probe":
                if status == 429:
                    return count  # rate limiting is working — no finding needed

        # If rate_limit_probe ran with no 429 at all, that's the finding
        if campaign_type == "rate_limit_probe" and count == 0 and results:
            statuses = [r.get("status_code") for r in results]
            if 429 not in statuses:
                _lr.append_finding_sync({
                    "title": "[AUTO] No rate limiting detected on login endpoint",
                    "category": "security_candidate",
                    "severity": "high",
                    "summary": (
                        f"Sent {len(results)} requests to the login endpoint with no 429 response. "
                        "Brute-force attacks are not rate-limited."
                    ),
                    "evidence": f"Status codes seen: {sorted(set(statuses))}",
                    "suggested_fix": "Add Django REST Framework throttling: DEFAULT_THROTTLE_CLASSES + DEFAULT_THROTTLE_RATES.",
                    "vision_step": 4, "auto": True,
                })
                count += 1

        return count
    except Exception as _e:
        logger.debug("_auto_fan_security_findings error: %s", _e)
        return 0


@tool
async def execute_security_campaign(
    campaign_type: str,
    target_url: str,
    log_file: str,
    endpoint_map_json: str = "[]",
    auth_token: str = "",
    alt_token: str = "",
    run_folder: str = "",
) -> str:
    """
    Runs one of 8 named HTTP security campaigns against the target.
    Each campaign maps to an OWASP API Security Top 10 (2023) risk.

    campaign_type — one of:
      idor_sweep       (API1:2023) — iterate IDs with alternate token
      jwt_attacks      (API2:2023) — alg:none, expired, malformed JWT
      mass_assignment  (API3:2023) — inject is_staff/is_admin in POST bodies
      rate_limit_probe (API4:2023) — 60 rapid login requests
      verb_tamper      (API5:2023) — send unadvertised HTTP verbs
      cors_probe       (API7:2023) — evil Origin header probe
      header_audit     (API7:2023) — check for missing security headers
      injection_probe  (API8:2023) — SQLi, SSTI, cmdi, path traversal

    endpoint_map_json: JSON string of [{path, methods, fields?}] from endpoint_map.json
    auth_token: primary user bearer token
    alt_token: second user token (required for idor_sweep)
    run_folder: per-run output directory (for logging)
    """
    from skills.security_payloads import (
        ALL_CAMPAIGNS, OWASP_MAP,
        idor_sweep, cors_probe, header_audit, jwt_attacks,
        verb_tamper, injection_probe, mass_assignment, rate_limit_probe,
    )
    from skills.attacker import SiegeEngine
    from skills.log_correlator import LogCorrelator

    logger.info("Tool Call: execute_security_campaign(type=%s) against %s", campaign_type, target_url)

    if campaign_type not in ALL_CAMPAIGNS:
        return json.dumps({
            "error": f"Unknown campaign_type '{campaign_type}'. Valid: {ALL_CAMPAIGNS}"
        })

    try:
        endpoints = json.loads(endpoint_map_json or "[]")
        if not isinstance(endpoints, list):
            endpoints = []
    except json.JSONDecodeError:
        endpoints = []

    # Generate payloads for the chosen campaign
    try:
        if campaign_type == "idor_sweep":
            payloads = idor_sweep(endpoints, auth_token, alt_token)
        elif campaign_type == "cors_probe":
            payloads = cors_probe(endpoints, auth_token)
        elif campaign_type == "header_audit":
            payloads = header_audit(endpoints, auth_token)
        elif campaign_type == "jwt_attacks":
            auth_eps = [ep.get("path", "") for ep in endpoints if "auth" in ep.get("path", "").lower()]
            if not auth_eps:
                auth_eps = [ep.get("path", "") for ep in endpoints]
            payloads = jwt_attacks(auth_eps, auth_token)
        elif campaign_type == "verb_tamper":
            payloads = verb_tamper(endpoints, auth_token)
        elif campaign_type == "injection_probe":
            payloads = injection_probe(endpoints, auth_token)
        elif campaign_type == "mass_assignment":
            payloads = mass_assignment(endpoints, auth_token)
        elif campaign_type == "rate_limit_probe":
            login_url = next(
                (ep.get("path", "") for ep in endpoints if "login" in ep.get("path", "").lower()),
                "/api/auth/login/"
            )
            payloads = rate_limit_probe(login_url)
        else:
            payloads = []
    except Exception as e:
        return json.dumps({"error": f"Payload generation failed: {e}"})

    if not payloads:
        return json.dumps({
            "campaign_type": campaign_type,
            "owasp_ref": OWASP_MAP.get(campaign_type, ""),
            "total_requests": 0,
            "message": "No endpoints matched this campaign type.",
        })

    # Run the assault
    try:
        from core.context import session_headers_var
        session_headers = session_headers_var.get({})
    except Exception:
        session_headers = {}

    engine = SiegeEngine(target_url, session_headers=session_headers)
    correlator = LogCorrelator(log_file) if log_file else None
    if correlator:
        correlator.start_watching()
    results = await engine.execute_assault(payloads)
    if correlator:
        import asyncio as _asyncio
        await _asyncio.sleep(1)
        correlator.stop_watching()

    findings_count = _auto_fan_security_findings(results, campaign_type, target_url, payloads)

    statuses: dict = {}
    for r in results:
        sc = str(r.get("status_code", "err"))
        statuses[sc] = statuses.get(sc, 0) + 1

    return json.dumps({
        "campaign_type": campaign_type,
        "owasp_ref": OWASP_MAP.get(campaign_type, ""),
        "total_requests": len(results),
        "findings_auto_written": findings_count,
        "status_distribution": statuses,
    }, indent=2)


@tool
def audit_file_for_vulnerabilities(
    file_path: str,
    target_dir: str,
    run_folder: str = "",
) -> str:
    """
    Reads a source file and returns it alongside a security review checklist.
    Call during Discovery on views.py, serializers.py, models.py, settings.py.

    The LLM reviews the returned source and calls record_finding for each issue
    found. Does NOT run any external tool — the LLM itself is the reviewer.

    file_path: path relative to target_dir (e.g. "app/views.py")
    target_dir: root of the target project
    run_folder: per-run output directory (for read-cache dedup)

    Returns JSON: {"file", "source", "security_review_checklist"}
    """
    logger.info("Tool Call: audit_file_for_vulnerabilities(%s)", file_path)
    try:
        full_path = Path(target_dir) / file_path
        if not full_path.exists():
            return json.dumps({"error": f"File not found: {file_path}"})

        # Dedup via read_cache
        if run_folder:
            cache = _get_read_cache(run_folder)
            import hashlib as _hl
            raw = full_path.read_text(encoding="utf-8", errors="replace")
            sha = _hl.md5(raw.encode()).hexdigest()[:8]
            cache_key = f"audit__{file_path.replace(os.sep, '_')}"
            cached = cache.get(cache_key, {})
            if cached.get("sha") == sha and cached.get("ref_id"):
                ref_id = cached["ref_id"]
                return json.dumps({
                    "cached": True,
                    "file": file_path,
                    "ref_id": ref_id,
                    "note": f"File unchanged since last audit. Call retrieve_stored_content(run_folder, '{ref_id}') if needed.",
                })
        else:
            raw = full_path.read_text(encoding="utf-8", errors="replace")
            sha = ""

        # Cap very large files
        MAX_CHARS = 80_000
        truncated = len(raw) > MAX_CHARS
        source = raw[:MAX_CHARS] + ("\n... [TRUNCATED]" if truncated else "")

        checklist = (
            "You are reviewing the source code above for security vulnerabilities. "
            "For EACH issue you find, call record_finding with vision_step=2, "
            "category='security_candidate', and include the exact line number and a suggested fix. "
            "Check for ALL of the following:\n"
            "1. Hardcoded secrets, API keys, passwords, or tokens (grep for =, key=, token=, secret=)\n"
            "2. Missing @permission_classes / @login_required / IsAuthenticated on views\n"
            "3. Raw SQL via cursor.execute() or QuerySet.raw() with f-strings or % formatting\n"
            "4. Mass assignment: DRF serializers with sensitive fields (is_staff, password, role) "
            "   not marked read_only=True\n"
            "5. DEBUG=True, ALLOWED_HOSTS=['*'], weak SECRET_KEY in settings files\n"
            "6. eval(), exec(), __import__(), compile() called with user-controlled input\n"
            "7. File path construction with user input (path traversal risk)\n"
            "8. Template rendering with unsanitised user content (mark_safe, format_html misuse)\n"
            "9. Use of pickle, yaml.load (not yaml.safe_load), or other unsafe deserialisers\n"
            "10. Missing CSRF protection (@csrf_exempt without justification)\n"
            "Record a finding for every confirmed issue. If the file is clean, record one finding "
            "with severity='low' and title='Security review: no issues found in <filename>'."
        )

        result = {"file": file_path, "source": source, "security_review_checklist": checklist}

        # Store in content cache for future dedup
        if run_folder and sha:
            try:
                from core.content_manager import store_and_summarize
                _, ref_id = store_and_summarize(
                    json.dumps(result), "audit_file_for_vulnerabilities",
                    run_folder, 0, source_hint=file_path.replace(os.sep, "_"), turn=-1,
                )
                _update_read_cache(run_folder, cache_key, ref_id, "current", sha)
            except Exception:
                pass

        return json.dumps(result)
    except Exception as e:
        return json.dumps({"error": f"audit_file_for_vulnerabilities failed: {e}"})


# Expose tools for the agent to bind
FAULTLINE_TOOLS = [
    record_finding,
    list_project_files,
    read_project_file,
    read_many_project_files,
    read_many_run_folder_files,
    glob_and_read,
    fetch_endpoint_bundle,
    write_run_folder_file,
    run_deterministic_checks,
    analyze_project_structure,
    index_project_documentation,
    query_knowledge_base,
    validate_python_code,
    copy_test_boilerplate,
    run_functional_test,
    execute_chaos_campaign,
    execute_security_campaign,
    audit_file_for_vulnerabilities,
    propose_code_patch,
    save_vulnerability_report,
    execute_claude_code_task,
    execute_gemini_cli_task,
    execute_codex_cli_task,
    generate_dependency_graph,
    calculate_project_quality,
    generate_campaign_visuals,
    request_user_input,
    get_credential,
    retrieve_stored_content,
    summarize_to_report,
    list_run_folder_files,
    read_run_folder_file,
    record_decision,
]
