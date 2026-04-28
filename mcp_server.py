import asyncio
import json
import logging
from typing import Any
import sys
import os

# Add app to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP
from core.tools import (
    analyze_project_structure,
    run_functional_test,
    propose_code_patch,
    execute_chaos_campaign
)

logger = logging.getLogger("FaultlineMCP")
mcp = FastMCP("Faultline-Agent", description="Autonomous QA and Chaos Engineering Testing Platform")

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
