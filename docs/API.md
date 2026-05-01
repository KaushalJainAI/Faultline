# Faultline API

**Date**: 2026-05-01
**Description**: Reference documentation for the Faultline Control Plane REST API, including campaign management, project mapping, and Vault authentication.

Base URL:

```text
http://localhost:8000/api/v1
```

## Start Campaign

Starts a background Aegis-Breaker campaign. The API requires a configured provider, creates a database campaign record, starts the target process with `start_command`, runs the agent, writes findings/tool runs, generates a Markdown report, and stops the target process when the background task exits.

```http
POST /campaign/start/
```

Request:

```json
{
  "target_path": "C:/path/to/project",
  "target_url": "http://127.0.0.1:9000",
  "start_command": "python manage.py runserver 9000",
  "health_url": "http://127.0.0.1:9000/health/",
  "log_file": "server.log",
  "auth_flow": "123e4567-e89b-12d3-a456-426614174000"
}
```

Fields:

- `target_path`: Existing directory containing the target project.
- `target_url`: Base URL for HTTP attacks.
- `start_command`: Command used by the Medic to start the target application from `target_path`.
- `health_url`: Optional URL used by the Medic for health checks.
- `log_file`: Optional log file path watched during chaos execution. Defaults to `server.log`.
- `auth_flow`: Optional UUID of a Vault AuthFlow to execute before testing to acquire session credentials.

Success response:

```json
{
  "message": "Chaos campaign initiated successfully in the background.",
  "target": "C:/path/to/project",
  "campaign_id": "uuid-v4-string",
  "tasks": [
    "Start target",
    "Index documentation",
    "Map structure",
    "Generate payloads",
    "Execute chaos run",
    "Write report"
  ]
}
```

Validation failures return `400 Bad Request` with serializer errors.

If the selected provider is not configured, the endpoint returns:

```json
{
  "error": "OPENROUTER_API_KEY is required when FAULTLINE_PROVIDER=openrouter."
}
```

## Get Campaign

Returns campaign status and metadata.

```http
GET /campaign/{campaign_id}/
```

Success response:

```json
{
  "id": "uuid-v4-string",
  "status": "running",
  "target_path": "C:/path/to/project",
  "target_url": "http://127.0.0.1:9000",
  "start_command": "python manage.py runserver 9000",
  "health_url": "http://127.0.0.1:9000/health/",
  "log_file": "server.log",
  "created_at": "2026-04-28T09:00:00Z",
  "started_at": "2026-04-28T09:00:01Z",
  "finished_at": null,
  "error_message": "",
  "report_path": "",
  "finding_count": 0
}
```

Campaign statuses are `queued`, `running`, `passed`, `failed`, and `error`.

## Get Campaign Findings

Returns all findings stored for a campaign.

```http
GET /campaign/{campaign_id}/findings/
```

Finding categories are `syntax`, `semantic`, `runtime`, `api`, and `security_candidate`.

Finding severities are `low`, `medium`, `high`, and `critical`.

## Get Campaign Report

Returns the generated Markdown report content.

```http
GET /campaign/{campaign_id}/report/
```

If the report is not ready or the file is missing, the endpoint returns `404`.

## Get Project Map

Runs static AST analysis against a target Python project.

```http
GET /campaign/map/?path=C:/path/to/project
```

Success response:

```json
{
  "files": {
    "app/views.py": {
      "classes": [
        {
          "name": "ChatView",
          "methods": ["post"],
          "lineno": 10
        }
      ],
      "functions": [],
      "imports": ["django.views.View", "app.serializers.ChatSerializer"]
    }
  },
  "dependencies": []
}
```

The mapper skips generated caches, virtual environments, Git metadata, and `.aegis_patches`. For Django/DRF projects, it also includes basic route, view, and serializer hints when simple `path(...)` or `router.register(...)` calls are discoverable.

## Vault Authentication Flows

Manages configurations for acquiring and injecting session credentials.

### List AuthFlows

```http
GET /vault/auth-flows/
```

### Create AuthFlow

```http
POST /vault/auth-flows/
```

Request (Static Token Example):

```json
{
  "name": "My API Key",
  "auth_type": "static",
  "auth_payload": "secret-key",
  "injection_type": "header",
  "injection_key": "X-API-Key",
  "injection_format": "{token}"
}
```

Request (Login Endpoint Example):

```json
{
  "name": "User Login",
  "auth_type": "login",
  "auth_url": "/api/login",
  "auth_method": "POST",
  "auth_payload": {"username": "admin", "password": "password"},
  "token_extraction_path": "access_token",
  "injection_type": "header",
  "injection_key": "Authorization",
  "injection_format": "Bearer {token}"
}
```
