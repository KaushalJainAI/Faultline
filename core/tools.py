import json
import logging
import asyncio
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
        return json.dumps(reader.list_files(glob=glob, limit=limit), indent=2)
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
        return json.dumps(checker.run_all(), indent=2)
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
        return json.dumps(graph, indent=2)
    except Exception as e:
        return f"Error analyzing project structure: {e}"

@tool
def query_knowledge_base(query_text: str, db_path: str = "./db/faiss_store") -> str:
    """
    Queries the FAISS semantic knowledge base for documentation intent.
    Helps in understanding the business logic and intended behavior of the system.
    """
    logger.info(f"Tool Call: Querying knowledge base for: {query_text}")
    try:
        indexer = SemanticIndexer(db_path=db_path)
        results = indexer.query(query_text)
        return json.dumps(results, indent=2)
    except Exception as e:
        return f"Error querying knowledge base: {e}"

@tool
def index_project_documentation(target_dir: str, db_path: str = "./db/faiss_store") -> str:
    """
    Indexes all project documentation (*.md) into the FAISS semantic index.
    Should be run at the beginning of a campaign to populate the knowledge base.
    """
    logger.info(f"Tool Call: Indexing documentation for {target_dir}")
    try:
        indexer = SemanticIndexer(db_path=db_path)
        indexer.index_project_docs(target_dir)
        return "Documentation successfully indexed into FAISS."
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
        
        summary = {
            "total_executed": len(results),
            "total_crashes_found": len(crashes),
            "crash_details": crashes
        }
        return json.dumps(summary, indent=2)
    except json.JSONDecodeError:
        return "Error: Invalid JSON format for payloads_json."
    except Exception as e:
        return f"Execution error: {e}"

@tool
def run_functional_test(test_code: str, target_dir: str) -> str:
    """
    Writes a Pytest script to the target directory and executes it.
    Use this to perform functional verification (TestSprite DNA) before or after chaos testing.
    """
    logger.info("Tool Call: Executing Pytest functional test")
    try:
        qa = QAEngineer(target_dir=target_dir)
        passed, output = qa.run_functional_test(test_code)
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
def save_vulnerability_report(report_markdown: str, filename: str = "latest_report.md") -> str:
    """
    Saves a synthesized vulnerability and chaos engineering report to the reports/ directory.
    """
    logger.info(f"Tool Call: Saving Vulnerability Report to {filename}")
    import os
    try:
        os.makedirs("reports", exist_ok=True)
        filepath = os.path.join("reports", filename)
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(report_markdown)
        return f"Successfully saved report to {filepath}"
    except Exception as e:
        return f"Error saving report: {e}"

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
def generate_dependency_graph(target_dir: str) -> str:
    """
    Generates a Mermaid.js dependency graph of the project structure.
    Saves the result to reports/dependency_graph.md.
    """
    logger.info(f"Tool Call: Generating dependency graph for {target_dir}")
    try:
        grapher = ASTGrapher(root_dir=target_dir)
        ast_data = grapher.analyze_project()
        viz = Visualizer()
        path = viz.generate_mermaid_dependency_graph(ast_data)
        return f"Dependency graph generated and saved to {path}"
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
]
