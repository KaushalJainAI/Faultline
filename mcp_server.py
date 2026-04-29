import asyncio
import json
import logging
from typing import Any
import sys
import os

# Add app to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Configure Django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
try:
    import django
    django.setup()
except Exception:
    pass

from mcp.server.fastmcp import FastMCP
from core.tools import (
    analyze_project_structure,
    list_project_files,
    read_project_file,
    run_deterministic_checks,
    run_functional_test,
    propose_code_patch,
    execute_chaos_campaign
)

logger = logging.getLogger("FaultlineMCP")
mcp = FastMCP("Faultline-Agent", description="Autonomous QA and Chaos Engineering Testing Platform")

@mcp.tool()
async def faultline_list_project_files(target_dir: str, glob: str = "**/*.py", limit: int = 250) -> str:
    """
    Lists project-local files for agent-first investigation.
    """
    try:
        return list_project_files.invoke({"target_dir": target_dir, "glob": glob, "limit": limit})
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
async def faultline_read_project_file(target_dir: str, relative_path: str, start_line: int = 1, max_lines: int = 240) -> str:
    """
    Reads a bounded slice of a project-local file.
    """
    try:
        return read_project_file.invoke({
            "target_dir": target_dir,
            "relative_path": relative_path,
            "start_line": start_line,
            "max_lines": max_lines,
        })
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
async def faultline_run_deterministic_checks(target_dir: str) -> str:
    """
    Runs the pipeline-first deterministic check suite.
    """
    try:
        return run_deterministic_checks.invoke({"target_dir": target_dir})
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
async def faultline_analyze_project(target_dir: str) -> str:
    """
    Analyzes the Python project structure at target_dir using AST parsing.
    Returns a JSON string describing classes, functions, and imports per file.
    Use this to understand the project architecture and identify vulnerable endpoints.
    """
    try:
        # Langchain tools expose .invoke()
        return analyze_project_structure.invoke(target_dir)
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
async def faultline_run_functional_test(test_code: str, target_dir: str) -> str:
    """
    Writes a Pytest script to the target directory and executes it.
    Use this to perform functional verification (TestSprite DNA) before or after chaos testing.
    """
    try:
        return run_functional_test.invoke({"test_code": test_code, "target_dir": target_dir})
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
async def faultline_propose_code_patch(file_path: str, proposed_code: str, target_dir: str) -> str:
    """
    Proposes a fix to the main application code if a crash or bug is found.
    This saves the patched file to a `.aegis_patches/` directory in the target for developer review.
    """
    try:
        return propose_code_patch.invoke({"file_path": file_path, "proposed_code": proposed_code, "target_dir": target_dir})
    except Exception as e:
        return f"Error: {e}"

@mcp.tool()
async def faultline_execute_chaos_campaign(payloads_json: str, target_url: str, log_file: str) -> str:
    """
    Executes an asynchronous chaos assault using the provided JSON list of attack payloads.
    payloads_json must be a JSON array.
    """
    try:
        # Await the execution since it's an async langchain tool
        return await execute_chaos_campaign.ainvoke({"payloads_json": payloads_json, "target_url": target_url, "log_file": log_file})
    except Exception as e:
        return f"Error: {e}"

if __name__ == "__main__":
    # Use standard stdio transport for IDE integration (Cursor, Claude Desktop)
    mcp.run()
