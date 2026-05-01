"""
skills/target_discovery.py

Pre-agent target discovery: health check and OpenAPI/DRF schema auto-detection.

Called before the agent loop begins to:
1. Verify the target server is reachable (fail fast, save LLM budget).
2. Auto-discover API schema endpoints (OpenAPI, DRF, Swagger).
3. Merge discovered schema with AST-derived api_schemas.json.

All results are surfaced to the agent as pre-populated context.
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger("TargetDiscovery")

# Common paths where API schemas are exposed
_SCHEMA_ENDPOINTS = [
    "/api/schema/",           # DRF default
    "/api/schema/?format=json",
    "/swagger.json",          # drf-yasg
    "/openapi.json",          # FastAPI / drf-spectacular
    "/api/docs/?format=openapi",  # drf-spectacular alternate
    "/redoc/",                # redoc UI (check for schema link)
]

# Request timeout for discovery probes
_DISCOVERY_TIMEOUT = 8.0


def health_check(target_url: str, headers: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
    """
    Verify target server is reachable.

    Returns:
        (is_alive: bool, message: str)
    """
    if not target_url:
        return False, "No target URL configured."

    try:
        r = httpx.get(
            target_url.rstrip("/") + "/",
            headers=headers or {},
            timeout=_DISCOVERY_TIMEOUT,
            follow_redirects=True,
        )
        return True, (
            f"Target is live: {r.status_code} "
            f"({r.headers.get('server', 'unknown server')}, "
            f"{len(r.content)} bytes, "
            f"{r.elapsed.total_seconds():.1f}s)"
        )
    except httpx.ConnectError:
        return False, f"Connection refused — is the server running at {target_url}?"
    except httpx.TimeoutException:
        return False, f"Connection timed out after {_DISCOVERY_TIMEOUT}s — server may be slow or unreachable."
    except httpx.RequestError as exc:
        return False, f"Request failed: {exc}"


def discover_api_schema(
    target_url: str,
    headers: Optional[Dict[str, str]] = None,
) -> Optional[Dict[str, Any]]:
    """
    Probe common schema endpoints and return the first valid OpenAPI/JSON schema found.

    Returns:
        Parsed schema dict, or None if no schema endpoint was found.
    """
    if not target_url:
        return None

    base = target_url.rstrip("/")
    req_headers = {**(headers or {}), "Accept": "application/json"}

    for path in _SCHEMA_ENDPOINTS:
        url = base + path
        try:
            r = httpx.get(url, headers=req_headers, timeout=_DISCOVERY_TIMEOUT, follow_redirects=True)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("application/json"):
                schema = r.json()
                # Basic validation — OpenAPI schemas have "paths" or "openapi"/"swagger" keys
                if isinstance(schema, dict) and ("paths" in schema or "openapi" in schema or "swagger" in schema):
                    logger.info("OpenAPI schema discovered at %s", url)
                    return schema
        except (httpx.RequestError, json.JSONDecodeError):
            continue

    logger.info("No OpenAPI schema found at any standard endpoint.")
    return None


def extract_endpoints_from_schema(schema: Dict[str, Any]) -> List[Dict[str, Any]]:
    """
    Extract a flat list of endpoint definitions from an OpenAPI schema.

    Returns:
        [
            {"path": "/api/users/", "method": "GET", "summary": "...", "auth_required": True},
            ...
        ]
    """
    endpoints = []
    paths = schema.get("paths", {})

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, details in methods.items():
            if method.lower() in ("get", "post", "put", "patch", "delete", "options", "head"):
                endpoint = {
                    "path": path,
                    "method": method.upper(),
                    "summary": "",
                    "auth_required": False,
                    "parameters": [],
                    "request_body": None,
                }
                if isinstance(details, dict):
                    endpoint["summary"] = details.get("summary", details.get("description", ""))[:200]
                    # Check for security requirements
                    if details.get("security") or schema.get("security"):
                        endpoint["auth_required"] = True
                    # Extract parameter names
                    params = details.get("parameters", [])
                    if isinstance(params, list):
                        endpoint["parameters"] = [
                            p.get("name", "") for p in params
                            if isinstance(p, dict)
                        ]
                    # Note request body presence
                    if details.get("requestBody"):
                        endpoint["request_body"] = True

                endpoints.append(endpoint)

    return endpoints


def save_discovered_schema(
    schema: Dict[str, Any],
    endpoints: List[Dict[str, Any]],
    run_folder: str,
) -> str:
    """
    Save discovered schema and endpoint summary to the run folder.
    Returns the path to the saved endpoints file.
    """
    rf = Path(run_folder)
    rf.mkdir(parents=True, exist_ok=True)

    # Save full schema
    schema_path = rf / "discovered_openapi_schema.json"
    schema_path.write_text(json.dumps(schema, indent=2), encoding="utf-8")

    # Save flat endpoint list for agent consumption
    endpoints_path = rf / "discovered_endpoints.json"
    endpoints_path.write_text(json.dumps(endpoints, indent=2), encoding="utf-8")

    logger.info(
        "Schema saved: %d endpoints → %s",
        len(endpoints), endpoints_path,
    )
    return str(endpoints_path)


def run_discovery(
    target_url: str,
    run_folder: str,
    headers: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    """
    Full discovery pipeline: health check → schema probe → save results.

    Returns:
        {
            "healthy": bool,
            "health_message": str,
            "schema_found": bool,
            "endpoint_count": int,
            "endpoints_file": str | None,
            "schema_summary": str,
        }
    """
    result: Dict[str, Any] = {
        "healthy": False,
        "health_message": "",
        "schema_found": False,
        "endpoint_count": 0,
        "endpoints_file": None,
        "schema_summary": "",
    }

    # 1. Health check
    is_alive, msg = health_check(target_url, headers)
    result["healthy"] = is_alive
    result["health_message"] = msg

    if not is_alive:
        return result

    # 2. Schema discovery
    schema = discover_api_schema(target_url, headers)
    if schema:
        endpoints = extract_endpoints_from_schema(schema)
        result["schema_found"] = True
        result["endpoint_count"] = len(endpoints)

        # Summary for agent context
        methods_count = {}
        auth_count = 0
        for ep in endpoints:
            m = ep["method"]
            methods_count[m] = methods_count.get(m, 0) + 1
            if ep.get("auth_required"):
                auth_count += 1

        method_str = ", ".join(f"{m}:{c}" for m, c in sorted(methods_count.items()))
        result["schema_summary"] = (
            f"Discovered {len(endpoints)} endpoints ({method_str}). "
            f"{auth_count} require authentication."
        )

        # Save to disk
        result["endpoints_file"] = save_discovered_schema(schema, endpoints, run_folder)
    else:
        result["schema_summary"] = "No OpenAPI/Swagger schema found at standard endpoints."

    return result
