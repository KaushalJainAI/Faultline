# Tutorial: Running Your First Campaign

**Date**: 2026-05-01
**Description**: Step-by-step walkthrough for setting up Faultline, configuring your environment, and running your first campaign.

This tutorial walks you through the process of setting up a Faultline campaign to analyze and test a target Python application.

## Prerequisites

1.  Faultline installed and configured (see [README.md](../README.md)).
2.  A target Python application to test. For this tutorial, we assume a Django project located at `C:/projects/my-app`.
3.  An authenticated LLM provider (e.g., `OPENROUTER_API_KEY` set).

## Step 1: Start the Faultline Control Plane

Open a terminal in the Faultline directory and start the Django server:

```bash
python manage.py runserver
```

The control plane is now listening at `http://localhost:8000`.

## Step 2: Configure Your Environment

Ensure your chosen LLM provider is active. For example, to use OpenRouter:

```bash
set OPENROUTER_API_KEY=sk-or-v1-...
```

Or to use a local CLI provider (requires no API key, only an active subscription/login):

```bash
set FAULTLINE_PROVIDER=gemini_cli
```

## Step 3: Run the Interactive CLI (Recommended)

The easiest way to use Faultline is the interactive agent. It will guide you through the process, ask for missing credentials, and show you real-time progress with animations and ETA tracking.

```bash
python faultline.py
```

Follow the on-screen prompts to:
1.  **Select a Project Directory**: The path to the source code you want to test.
2.  **Select a Mode**: Usually `hybrid` for a full analysis.
3.  **Provide Target URL**: The base URL of your running application (e.g., `http://localhost:8000`).

## Step 4: Alternative - Trigger via REST API

Use `curl` or a tool like Postman to start a campaign. You need to provide:
-   `target_path`: Absolute path to the source code.
-   `target_url`: The URL where the target app will be running.
-   `start_command`: How Faultline should start the target app.
-   `log_file`: Where the target app writes its logs (Faultline will watch this).

```bash
curl -X POST http://localhost:8000/api/v1/campaign/start/ ^
  -H "Content-Type: application/json" ^
  -d "{\"target_path\":\"C:/projects/my-app\",\"target_url\":\"http://127.0.0.1:9000\",\"start_command\":\"python manage.py runserver 9000\",\"log_file\":\"server.log\"}"
```

## Step 4: Monitor Progress

You can check the status of the campaign via the API:

```bash
curl http://localhost:8000/api/v1/campaign/
```

Or watch the terminal logs where `runserver` is running. You will see the agent:
1.  **Mapping the project**: Parsing AST to find views and models.
2.  **Starting the target**: Running your `start_command`.
3.  **Executing tests**: Generating and running Pytest scripts.
4.  **Attacking endpoints**: Sending chaos payloads to find 500 errors.

## Step 5: Review the Results

Once the campaign completes, check the `reports/` directory. Each run creates a unique timestamped folder (e.g., `reports/my-app_20240501_103000/`).

1.  **`pipeline_report.md`**: View your Production-Readiness Score and static findings.
2.  **`dependency_graph.py`**: Run `python reports/.../dependency_graph.py` to explore the project structure in 3D.
3.  **`agent_report.md`**: Review AI-authored vulnerability findings and chaos test results.
4.  **`testcases/`**: Inspect the functional tests the agent generated and ran.

### Example Finding

If the agent found a SQL injection vulnerability, the report will show:
-   **Summary**: A description of how the injection was triggered.
-   **Evidence**: The exact HTTP request and the resulting database error log.
-   **Suggested Fix**: A snippet of code using Django's ORM properly instead of raw string formatting.

## Next Steps

-   **MCP Integration**: Use Faultline's tools directly from your IDE. See [MCP Guide](MCP.md).
-   **Custom Skills**: Add new testing capabilities to the library. See [Contributing](CONTRIBUTING.md).
