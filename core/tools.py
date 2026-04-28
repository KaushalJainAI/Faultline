import json
import logging
import asyncio
from typing import List, Dict, Any
from langchain_core.tools import tool

from skills.ast_grapher import ASTGrapher
from skills.attacker import SiegeEngine
from skills.guardrails import GuardrailValidator
from skills.log_correlator import LogCorrelator
from skills.medic import Medic
from skills.semantic_indexer import SemanticIndexer
from skills.qa_engineer import QAEngineer

logger = logging.getLogger("FaultlineTools")

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
    logger.info(f"Tool Call: Validating generated Python code")
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
        payloads = json.loads(payloads_json)
        if not isinstance(payloads, list):
            return "Error: payloads_json must be a JSON array."
        
        engine = SiegeEngine(target_url)
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

# Expose tools for the agent to bind
FAULTLINE_TOOLS = [
    analyze_project_structure,
    index_project_documentation,
    query_knowledge_base,
    validate_python_code,
    run_functional_test,
    execute_chaos_campaign,
    propose_code_patch,
    save_vulnerability_report
]
