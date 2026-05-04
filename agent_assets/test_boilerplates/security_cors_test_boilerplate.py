"""
Security CORS Test Boilerplate — OWASP API7:2023 Security Misconfiguration
Tests: evil origin reflection, ACAO wildcard with credentials, preflight.
Replace BASE_URL and TOKEN with values from api_test_data.json.
"""
import json
import httpx
import pytest

BASE_URL = "http://localhost:8000"
AUTH_TOKEN = "REPLACE_WITH_VALID_TOKEN"
TEST_ENDPOINT = "/api/REPLACE_WITH_ENDPOINT/"
EVIL_ORIGIN = "https://evil-attacker.com"


class TestCORSSecurity:
    def test_evil_origin_not_reflected(self):
        """
        Server must NOT echo back an arbitrary evil Origin in ACAO header.
        If it does AND returns Allow-Credentials: true → critical misconfiguration.
        """
        resp = httpx.get(
            BASE_URL + TEST_ENDPOINT,
            headers={
                "Authorization": f"Bearer {AUTH_TOKEN}",
                "Origin": EVIL_ORIGIN,
            },
            timeout=10,
        )
        acao = resp.headers.get("Access-Control-Allow-Origin", "")
        acac = resp.headers.get("Access-Control-Allow-Credentials", "")
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": TEST_ENDPOINT,
            "payload": {"origin": EVIL_ORIGIN},
            "status": resp.status_code,
            "response": {
                "Access-Control-Allow-Origin": acao,
                "Access-Control-Allow-Credentials": acac,
            },
        }))
        # Fail if evil origin is reflected AND credentials are allowed
        assert not (EVIL_ORIGIN in acao and acac.lower() == "true"), (
            f"CORS misconfiguration: server reflects evil origin '{acao}' with credentials allowed"
        )

    def test_wildcard_cors_without_credentials(self):
        """ACAO=* is acceptable only if Allow-Credentials is NOT true."""
        resp = httpx.get(
            BASE_URL + TEST_ENDPOINT,
            headers={"Origin": EVIL_ORIGIN},
            timeout=10,
        )
        acao = resp.headers.get("Access-Control-Allow-Origin", "")
        acac = resp.headers.get("Access-Control-Allow-Credentials", "")
        print("AEGIS_RESULT:", json.dumps({
            "method": "GET", "url": TEST_ENDPOINT,
            "payload": {"origin": EVIL_ORIGIN, "no_auth": True},
            "status": resp.status_code,
            "response": {"ACAO": acao, "ACAC": acac},
        }))
        if acao == "*":
            assert acac.lower() != "true", (
                "Wildcard CORS (ACAO=*) combined with Allow-Credentials:true is invalid and dangerous"
            )

    def test_preflight_restricts_methods(self):
        """OPTIONS preflight must not allow arbitrary methods from evil origin."""
        resp = httpx.options(
            BASE_URL + TEST_ENDPOINT,
            headers={
                "Origin": EVIL_ORIGIN,
                "Access-Control-Request-Method": "DELETE",
                "Access-Control-Request-Headers": "Authorization",
            },
            timeout=10,
        )
        acam = resp.headers.get("Access-Control-Allow-Methods", "")
        acao = resp.headers.get("Access-Control-Allow-Origin", "")
        print("AEGIS_RESULT:", json.dumps({
            "method": "OPTIONS", "url": TEST_ENDPOINT,
            "payload": {"preflight": True, "origin": EVIL_ORIGIN},
            "status": resp.status_code,
            "response": {"ACAM": acam, "ACAO": acao},
        }))
        # If evil origin is reflected, dangerous methods should not be allowed
        if EVIL_ORIGIN in acao:
            assert "DELETE" not in acam or "PUT" not in acam, (
                f"Preflight allows destructive methods ({acam}) from evil origin"
            )
