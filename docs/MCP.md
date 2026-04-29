# MCP Integration Guide

Faultline can function as a **Model Context Protocol (MCP)** server, allowing you to use its specialized QA and chaos engineering tools directly within AI assistants like Claude Desktop, Cursor, or any other MCP-compliant client.

## Why use Faultline via MCP?

While the Faultline Control Plane automates full campaigns, the MCP server gives you "on-demand" access to specific skills during manual development:
-   **Analyze project structure** on the fly.
-   **Run specific functional tests** without setting up a full campaign.
-   **Execute targeted chaos attacks** against a local endpoint you are debugging.

## Setup

### 1. Configure the Server

The MCP server is defined in `mcp_server.py`. It uses the `FastMCP` framework.

To register Faultline in **Claude Desktop**, add it to your `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "faultline": {
      "command": "python",
      "args": ["C:/path/to/Faultline/mcp_server.py"],
      "env": {
        "DJANGO_SETTINGS_MODULE": "config.settings",
        "PYTHONPATH": "C:/path/to/Faultline"
      }
    }
  }
}
```

### 2. Available Tools

Once connected, the following tools are available to your assistant:

| Tool Name | Description |
| --- | --- |
| `faultline_analyze_project` | Parses AST to describe classes, functions, and imports in a target directory. |
| `faultline_run_functional_test` | Writes and executes a Pytest script in the target directory. |
| `faultline_propose_code_patch` | Saves a proposed fix to the `.aegis_patches/` folder in the target. |
| `faultline_execute_chaos_campaign` | Runs a sequence of adversarial HTTP payloads against a target URL. |

## Usage Examples

### Analyzing a Project
You can ask your assistant:
> "Use Faultline to analyze the project structure at `C:/projects/my-api` and tell me which views handle user authentication."

### Running a Test
> "Write a functional test for the `/api/login` endpoint that checks for 401 on wrong password, and run it using Faultline."

## Connecting to the Control Plane

The MCP server shares the same database and configuration as the Django control plane. Any tool runs triggered via MCP will **not** automatically show up as a "Campaign" in the Django UI/API unless explicitly wrapped in a campaign session (this is a planned feature).
