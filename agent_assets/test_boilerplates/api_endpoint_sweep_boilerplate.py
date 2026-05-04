"""
BOILERPLATE: Multi-Endpoint Domain Sweep
=========================================
Tests 5-10 related endpoints in one pytest file.
One call to run_functional_test → covers an entire domain group.

Instructions for Agent:
1. Copy this file via copy_test_boilerplate("endpoint_sweep")
2. Set BASE_URL to the campaign target URL
3. Set AUTH_TOKEN to a valid token (from session_headers or a fresh login)
4. Replace the placeholder endpoint groups with real endpoints from api_test_data.json
5. Keep the AEGIS_RESULT print on every HTTP call — the harness parses it
6. Run with run_functional_test(test_code, target_dir, test_type="api", case_kind="happy")

NEVER import Django, TestCase, or any target-project module here.
ALWAYS use httpx pointing at BASE_URL.
"""

import json
import httpx
import pytest

BASE_URL = "http://localhost:8000"   # Replace with campaign target_url
TIMEOUT = 15.0

# ---------------------------------------------------------------------------
# Auth fixture — obtain a token once for the entire sweep
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def auth_token():
    """Login and return a Bearer token for authenticated calls."""
    resp = httpx.post(
        f"{BASE_URL}/api/auth/login/",
        json={"email": "testuser@example.com", "password": "testpassword123"},
        timeout=TIMEOUT,
    )
    print("AEGIS_RESULT:", json.dumps({
        "method": "POST",
        "url": "/api/auth/login/",
        "payload": {"email": "testuser@example.com", "password": "***"},
        "status": resp.status_code,
        "response": resp.json() if "application/json" in resp.headers.get("content-type", "") else resp.text[:200],
    }))
    if resp.status_code == 200:
        data = resp.json()
        # Try common token field names
        token = (
            data.get("access")
            or data.get("token")
            or data.get("access_token")
            or data.get("key")
            or ""
        )
        return token
    return ""


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"} if token else {}


# ---------------------------------------------------------------------------
# Group 1: Public / health endpoints (no auth required)
# ---------------------------------------------------------------------------

def test_public_endpoints():
    """Sweep all public endpoints that require no authentication."""

    # GET /api/health/
    r = httpx.get(f"{BASE_URL}/api/health/", timeout=TIMEOUT)
    print("AEGIS_RESULT:", json.dumps({
        "method": "GET", "url": "/api/health/",
        "payload": None, "status": r.status_code,
        "response": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:200],
    }))
    assert r.status_code in (200, 204), f"Health check failed: {r.status_code}"

    # GET /api/schema/  (OpenAPI schema, may require auth)
    r = httpx.get(f"{BASE_URL}/api/schema/", timeout=TIMEOUT)
    print("AEGIS_RESULT:", json.dumps({
        "method": "GET", "url": "/api/schema/",
        "payload": None, "status": r.status_code,
        "response": r.text[:200],
    }))
    assert r.status_code in (200, 401, 403), f"Schema endpoint unexpected: {r.status_code}"


# ---------------------------------------------------------------------------
# Group 2: Auth endpoints
# ---------------------------------------------------------------------------

def test_auth_register_happy():
    """Register a fresh user — happy path."""
    import uuid
    email = f"sweep_{uuid.uuid4().hex[:8]}@test.com"
    payload = {"email": email, "password": "StrongPass123!", "password2": "StrongPass123!"}
    r = httpx.post(f"{BASE_URL}/api/auth/register/", json=payload, timeout=TIMEOUT)
    print("AEGIS_RESULT:", json.dumps({
        "method": "POST", "url": "/api/auth/register/",
        "payload": {**payload, "password": "***", "password2": "***"},
        "status": r.status_code,
        "response": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:300],
    }))
    assert r.status_code in (200, 201), f"Register failed: {r.status_code} {r.text[:200]}"


def test_auth_register_sad_missing_fields():
    """Register with missing required fields — sad path (expect 400)."""
    payload = {"email": "bad@test.com"}   # missing password
    r = httpx.post(f"{BASE_URL}/api/auth/register/", json=payload, timeout=TIMEOUT)
    print("AEGIS_RESULT:", json.dumps({
        "method": "POST", "url": "/api/auth/register/",
        "payload": payload, "status": r.status_code,
        "response": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:200],
    }))
    assert r.status_code == 400, f"Expected 400 for missing fields, got {r.status_code}"


def test_auth_login_sad_wrong_password():
    """Login with wrong password — sad path (expect 400/401)."""
    payload = {"email": "nobody@example.com", "password": "wrongpass"}
    r = httpx.post(f"{BASE_URL}/api/auth/login/", json=payload, timeout=TIMEOUT)
    print("AEGIS_RESULT:", json.dumps({
        "method": "POST", "url": "/api/auth/login/",
        "payload": {**payload, "password": "***"},
        "status": r.status_code,
        "response": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:200],
    }))
    assert r.status_code in (400, 401), f"Expected 400/401 for bad credentials, got {r.status_code}"


# ---------------------------------------------------------------------------
# Group 3: Authenticated endpoints — replace with real endpoint paths
# ---------------------------------------------------------------------------

def test_authenticated_user_profile(auth_token):
    """GET /api/auth/user/ — requires valid token."""
    h = _headers(auth_token)
    r = httpx.get(f"{BASE_URL}/api/auth/user/", headers=h, timeout=TIMEOUT)
    print("AEGIS_RESULT:", json.dumps({
        "method": "GET", "url": "/api/auth/user/",
        "payload": None, "status": r.status_code,
        "response": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:200],
    }))
    # 200 if token valid, 401 if token expired/empty
    assert r.status_code in (200, 401), f"Unexpected status: {r.status_code}"


def test_unauthenticated_access_blocked(auth_token):
    """Authenticated endpoints should return 401 with no token — IDOR/auth gate check."""
    # Replace /api/auth/user/ with any endpoint that REQUIRES auth
    r = httpx.get(f"{BASE_URL}/api/auth/user/", timeout=TIMEOUT)
    print("AEGIS_RESULT:", json.dumps({
        "method": "GET", "url": "/api/auth/user/",
        "payload": None, "status": r.status_code,
        "response": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:200],
    }))
    assert r.status_code in (401, 403), f"Auth gate missing — got {r.status_code} without token"


# ---------------------------------------------------------------------------
# Group 4: Domain-specific endpoints (replace with real endpoints from api_test_data.json)
# ---------------------------------------------------------------------------

def test_domain_group_list(auth_token):
    """
    Sweep a group of list/GET endpoints from a single domain.
    Replace the URL list below with real endpoints from api_test_data.json.
    """
    h = _headers(auth_token)
    endpoints_to_check = [
        # ("METHOD", "/api/endpoint/", payload_or_None),
        ("GET", "/api/usage/", None),
        # Add more from api_test_data.json:
        # ("GET", "/api/workflows/", None),
        # ("GET", "/api/nodes/", None),
    ]

    for method, path, payload in endpoints_to_check:
        url = f"{BASE_URL}{path}"
        if method == "GET":
            r = httpx.get(url, headers=h, timeout=TIMEOUT)
        elif method == "POST":
            r = httpx.post(url, json=payload or {}, headers=h, timeout=TIMEOUT)
        elif method == "DELETE":
            r = httpx.delete(url, headers=h, timeout=TIMEOUT)
        else:
            r = httpx.request(method, url, json=payload, headers=h, timeout=TIMEOUT)

        print("AEGIS_RESULT:", json.dumps({
            "method": method, "url": path,
            "payload": payload, "status": r.status_code,
            "response": r.json() if "application/json" in r.headers.get("content-type", "") else r.text[:200],
        }))
        assert r.status_code < 500, f"{method} {path} returned server error {r.status_code}: {r.text[:200]}"
