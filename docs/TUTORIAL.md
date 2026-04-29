# Tutorial: Running Your First Campaign

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

## Step 3: Trigger the Campaign

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

Once the campaign status moves to `passed` or `failed`, check the `reports/` directory.

1.  Locate `reports/campaign_<id>.md`.
2.  Open it in a Markdown viewer.
3.  Review the **Findings Table** for any discovered vulnerabilities.
4.  Check the **Detailed Findings** for logs, stack traces, and suggested code fixes.

### Example Finding

If the agent found a SQL injection vulnerability, the report will show:
-   **Summary**: A description of how the injection was triggered.
-   **Evidence**: The exact HTTP request and the resulting database error log.
-   **Suggested Fix**: A snippet of code using Django's ORM properly instead of raw string formatting.

## Next Steps

-   **MCP Integration**: Use Faultline's tools directly from your IDE. See [MCP Guide](MCP.md).
-   **Custom Skills**: Add new testing capabilities to the library. See [Contributing](CONTRIBUTING.md).
