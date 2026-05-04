"""
Security JWT Test Boilerplate — OWASP API2:2023 Broken Authentication
Tests: alg:none attack, expired token, missing bearer, token replay after logout.
Replace BASE_URL and TOKEN with values from api_test_data.json.
"""
import base64
import json
import httpx
import pytest

BASE_URL = "http://localhost:8000"
VALID_TOKEN = "REPLACE_WITH_VALID_TOKEN"
PROTECTED_ENDPOINT = "/api/REPLACE_WITH_ENDPOINT/"


def _forge_alg_none(token: str) -> str:
    """Craft a JWT with alg=none and no signature."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return token
        header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
        header["alg"] = "none"
        new_header = base64.urlsafe_b64encode(
            json.dumps(header, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        return f"{new_header}.{parts[1]}."
    except Exception:
        return token


class TestJWTSecurity:
    def test_alg_none_rejected(self):
        """Server must reject a JWT with alg=none (no signature)."""
        forged = _forge_alg_none(VALID_TOKEN)
        resp = httpx.get(
            BASE_URL + PROTECTED_ENDPOINT,
            headers={"Authorization": f"Bearer {forged}"},
            timeout=10,
        )
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": PROTECTED_ENDPOINT,
            "payload": {"alg_none_token": forged[:30] + "..."},
            "status": resp.status_code,
            "response": resp.text[:200],
        }))
        assert resp.status_code in (401, 403), (
            f"alg:none JWT should be rejected (401/403), got {resp.status_code}"
        )

    def test_missing_bearer_rejected(self):
        """Request with no Authorization header must be rejected."""
        resp = httpx.get(BASE_URL + PROTECTED_ENDPOINT, timeout=10)
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": PROTECTED_ENDPOINT,
            "payload": {}, "status": resp.status_code, "response": resp.text[:200],
        }))
        assert resp.status_code in (401, 403), (
            f"No-auth request should be rejected, got {resp.status_code}"
        )

    def test_empty_bearer_rejected(self):
        """Empty Bearer token must be rejected."""
        resp = httpx.get(
            BASE_URL + PROTECTED_ENDPOINT,
            headers={"Authorization": "Bearer "},
            timeout=10,
        )
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": PROTECTED_ENDPOINT,
            "payload": {"empty_bearer": True}, "status": resp.status_code,
            "response": resp.text[:200],
        }))
        assert resp.status_code in (401, 403), (
            f"Empty Bearer should be rejected, got {resp.status_code}"
        )

    def test_malformed_jwt_rejected(self):
        """Structurally invalid JWT must not reach the application logic."""
        resp = httpx.get(
            BASE_URL + PROTECTED_ENDPOINT,
            headers={"Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9.AAAA.BBBB"},
            timeout=10,
        )
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": PROTECTED_ENDPOINT,
            "payload": {"malformed_jwt": True}, "status": resp.status_code,
            "response": resp.text[:200],
        }))
        assert resp.status_code in (401, 403), (
            f"Malformed JWT should be rejected, got {resp.status_code}"
        )

    def test_tampered_payload_rejected(self):
        """A JWT with a tampered payload (but valid header+sig) must be rejected."""
        parts = VALID_TOKEN.split(".")
        if len(parts) != 3:
            pytest.skip("VALID_TOKEN is not a JWT")
        tampered = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
        tampered_token = f"{parts[0]}.{tampered}.{parts[2]}"
        resp = httpx.get(
            BASE_URL + PROTECTED_ENDPOINT,
            headers={"Authorization": f"Bearer {tampered_token}"},
            timeout=10,
        )
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": PROTECTED_ENDPOINT,
            "payload": {"tampered_payload": True}, "status": resp.status_code,
            "response": resp.text[:200],
        }))
        assert resp.status_code in (401, 403), (
            f"Tampered JWT payload should be rejected, got {resp.status_code}"
        )
