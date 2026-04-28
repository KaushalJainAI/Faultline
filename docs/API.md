# Faultline API Documentation

The Faultline API is the control plane for the Aegis-Breaker adversarial agent. It allows you to trigger campaigns, monitor project structure, and retrieve vulnerability reports.

## Base URL
`http://localhost:8000/api/v1`

## Endpoints

### 1. Start Campaign
Initiates an autonomous chaos engineering campaign against a target project.

- **URL:** `/campaign/start/`
- **Method:** `POST`
- **Request Body:**
  ```json
  {
    "target_path": "C:/path/to/project",
    "start_command": "python manage.py runserver",
    "health_url": "http://127.0.0.1:8000/health/",
    "log_file": "server.log"
  }
  ```
- **Success Response:** `200 OK`
  ```json
  {
    "message": "Chaos campaign initiated successfully.",
    "target": "C:/path/to/project",
    "campaign_id": "uuid-v4-string",
    "tasks": ["Indexing", "Vulnerability Mapping", "Payload Generation"]
  }
  ```

### 2. Get Project Map
Performs a static analysis (AST) of the target project and returns a dependency graph.

- **URL:** `/campaign/map/`
- **Method:** `GET`
- **Query Params:** `path=[absolute_path]`
- **Success Response:** `200 OK`
  ```json
  {
    "files": {
      "app/views.py": {
        "classes": [{"name": "ChatView", "methods": ["post"], "lineno": 10}],
        "functions": [],
        "imports": ["django.views", "app.serializers"]
      }
    },
    "dependencies": []
  }
  ```

