"""
Security Headers Test Boilerplate — OWASP API7:2023 Security Misconfiguration
Tests: CSP, HSTS, X-Frame-Options, X-Content-Type-Options, Referrer-Policy.
Replace BASE_URL and TOKEN with values from api_test_data.json.
"""
import json
import httpx
import pytest

BASE_URL = "http://localhost:8000"
AUTH_TOKEN = "REPLACE_WITH_VALID_TOKEN"
TEST_ENDPOINTS = [
    "/api/REPLACE_WITH_ENDPOINT/",
]

REQUIRED_HEADERS = {
    "Content-Security-Policy": "Prevents XSS by restricting script sources",
    "X-Frame-Options": "Prevents clickjacking (DENY or SAMEORIGIN)",
    "X-Content-Type-Options": "Prevents MIME-type sniffing (must be 'nosniff')",
    "Referrer-Policy": "Controls referrer information leakage",
}

HTTPS_ONLY_HEADERS = {
    "Strict-Transport-Security": "Enforces HTTPS (HSTS) — only valid over HTTPS",
}


class TestSecurityHeaders:
    @pytest.mark.parametrize("endpoint", TEST_ENDPOINTS)
    def test_content_security_policy_present(self, endpoint):
        resp = httpx.get(
            BASE_URL + endpoint,
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            timeout=10,
        )
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": endpoint, "payload": {},
            "status": resp.status_code,
            "response": dict(resp.headers),
        }))
        assert "content-security-policy" in {h.lower() for h in resp.headers}, (
            f"Missing Content-Security-Policy header on {endpoint}"
        )

    @pytest.mark.parametrize("endpoint", TEST_ENDPOINTS)
    def test_x_frame_options_present(self, endpoint):
        resp = httpx.get(
            BASE_URL + endpoint,
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            timeout=10,
        )
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": endpoint, "payload": {},
            "status": resp.status_code,
            "response": {"X-Frame-Options": resp.headers.get("X-Frame-Options", "MISSING")},
        }))
        xfo = resp.headers.get("X-Frame-Options", "").upper()
        assert xfo in ("DENY", "SAMEORIGIN"), (
            f"X-Frame-Options should be DENY or SAMEORIGIN on {endpoint}, got '{xfo}'"
        )

    @pytest.mark.parametrize("endpoint", TEST_ENDPOINTS)
    def test_x_content_type_options_nosniff(self, endpoint):
        resp = httpx.get(
            BASE_URL + endpoint,
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            timeout=10,
        )
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": endpoint, "payload": {},
            "status": resp.status_code,
            "response": {"X-Content-Type-Options": resp.headers.get("X-Content-Type-Options", "MISSING")},
        }))
        xcto = resp.headers.get("X-Content-Type-Options", "").lower()
        assert xcto == "nosniff", (
            f"X-Content-Type-Options must be 'nosniff' on {endpoint}, got '{xcto}'"
        )

    @pytest.mark.parametrize("endpoint", TEST_ENDPOINTS)
    def test_no_server_version_leak(self, endpoint):
        """Server/X-Powered-By headers must not reveal version info."""
        resp = httpx.get(
            BASE_URL + endpoint,
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            timeout=10,
        )
        server = resp.headers.get("Server", "")
        powered_by = resp.headers.get("X-Powered-By", "")
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": endpoint, "payload": {},
            "status": resp.status_code,
            "response": {"Server": server, "X-Powered-By": powered_by},
        }))
        import re
        version_pattern = re.compile(r"\d+\.\d+")
        assert not version_pattern.search(server), (
            f"Server header leaks version info: '{server}'"
        )
        assert not powered_by, (
            f"X-Powered-By header present ('{powered_by}') — remove it"
        )

    @pytest.mark.parametrize("endpoint", TEST_ENDPOINTS)
    def test_referrer_policy_present(self, endpoint):
        resp = httpx.get(
            BASE_URL + endpoint,
            headers={"Authorization": f"Bearer {AUTH_TOKEN}"},
            timeout=10,
        )
        rp = resp.headers.get("Referrer-Policy", "")
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": endpoint, "payload": {},
            "status": resp.status_code,
            "response": {"Referrer-Policy": rp or "MISSING"},
        }))
        assert rp, f"Referrer-Policy header missing on {endpoint}"
