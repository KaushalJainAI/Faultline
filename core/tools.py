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
def read_project_file(target_dir: str, relative_path: str, start_line: int = 1, max_lines: int = 240) -> str:
    """
    Reads a bounded slice of a project-local file for agent-first investigation.
    Use this before proposing tests, payloads, or patches.
    """
    logger.info("Tool Call: Reading project file %s", relative_path)
    try:
        reader = ProjectFileReader(target_dir)
        return json.dumps(reader.read_file(relative_path, start_line, max_lines), indent=2)
    except Exception as e:
        return f"Error reading project file: {e}"

@tool
def run_deterministic_checks(target_dir: str) -> str:
    """
    Runs deterministic pre-agent checks: syntax parsing, missing imports,
    definite division-by-zero hazards, ruff, pip check, pytest collection,
    and AST dependency root-cause propagation.
    """
    logger.info("Tool Call: Running deterministic checks for %s", target_dir)
    try:
        checker = DeterministicChecker(target_dir)
        return json.dumps(checker.run_all())
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
        return json.dumps(summary, indent=2)
    except json.JSONDecodeError:
        return "Error: Invalid JSON format for payloads_json."
    except Exception as e:
        return f"Execution error: {e}"

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
    try:
        from campaigns.models import Campaign, Finding
        campaign = Campaign.objects.get(id=campaign_id)
        
        # Ensure category and severity map safely
        cat = category if category in Finding.Category.values else Finding.Category.RUNTIME
        sev = severity if severity in Finding.Severity.values else Finding.Severity.MEDIUM
        
        Finding.objects.create(
            campaign=campaign,
            title=title[:255],
            category=cat,
            severity=sev,
            status="open",
            summary=summary,
            evidence=evidence,
            reproduction_steps=reproduction_steps,
            suggested_fix=suggested_fix,
            file_path=file_path,
            line_number=line_number,
            vision_step=vision_step,
        )

        # Append to the live report immediately so progress survives a crash.
        # Use the synchronous path because @tool wrappers run on a worker thread
        # that does not own the asyncio event loop — scheduling a task there
        # silently drops the write.
        try:
            from core.context import live_report_var
            _lr = live_report_var.get(None)
            if _lr is not None:
                _finding_data = {
                    "title": title, "category": cat, "severity": sev,
                    "summary": summary, "evidence": evidence,
                    "reproduction_steps": reproduction_steps,
                    "suggested_fix": suggested_fix,
                    "file_path": file_path, "line_number": line_number,
                    "vision_step": vision_step,
                }
                _lr.append_finding_sync(_finding_data)
        except Exception as _e:
            logger.warning("live_report append failed: %s", _e)

        return f"Successfully recorded finding '{title}' for vision step {vision_step}."
    except Exception as e:
        return f"Failed to record finding: {e}"

@tool
def request_user_input(question: str, input_type: str = "text") -> str:
    """
    Pause and ask the human operator for input during the campaign.

    Use this when you encounter an authentication challenge, missing API key,
    ambiguous configuration, or any decision that requires a human in the loop.

    input_type:
      - "credential": prompts for a sensitive value with masked input (passwords, API keys, tokens)
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
def read_run_folder_file(run_folder: str, relative_path: str, max_chars: int = 8000) -> str:
    """
    Reads a file from the per-run output directory (run_folder).
    Use this to inspect api_schemas.json, api_test_data.json, test scripts,
    generated test results, or any other file created during this session.

    relative_path: Path relative to run_folder, e.g. "api_schemas.json" or "testcases/test_auth.py".
    max_chars: Maximum characters to return (default 8000). Pass 0 for unlimited.
    """
    logger.info("Tool Call: read_run_folder_file — %s / %s", run_folder, relative_path)
    try:
        target = Path(run_folder) / relative_path
        if not target.exists():
            return f"Error: '{relative_path}' not found in run_folder '{run_folder}'."
        if not target.is_file():
            return f"Error: '{relative_path}' is not a file."
        # Resolve to ensure path stays within run_folder (no directory traversal)
        base = Path(run_folder).resolve()
        resolved = target.resolve()
        if not str(resolved).startswith(str(base)):
            return "Error: Path traversal outside run_folder is not allowed."
        text = resolved.read_text(encoding="utf-8", errors="replace")
        if max_chars and len(text) > max_chars:
            text = text[:max_chars] + f"\n\n[... truncated at {max_chars} chars ...]"
        return text
    except Exception as e:
        return f"Error reading run folder file: {e}"


def resolve_credential_at_startup(store) -> "dict | None":
    """
    Run the full credential resolution chain for the 'default' role.
    Called from faultline.py before the agent starts so session_headers_var
    is pre-populated and the agent never needs to trigger HITL for auth.

    Returns the auth header dict (e.g. {"Authorization": "Bearer eyJ..."})
    or None if resolution fails.
    """
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


# Expose tools for the agent to bind
FAULTLINE_TOOLS = [
    record_finding,
    list_project_files,
    read_project_file,
    run_deterministic_checks,
    analyze_project_structure,
    index_project_documentation,
    query_knowledge_base,
    validate_python_code,
    copy_test_boilerplate,
    run_functional_test,
    execute_chaos_campaign,
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
]
