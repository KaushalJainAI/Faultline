import pytest
import httpx

# BOILERPLATE: API Input Validation & Boundary Testing
# Instructions for Agent:
# 1. Copy this file to reports/testcases/test_validation_<HHMMSS>.py
# 2. Replace <TARGET_ENDPOINT> with the endpoint that accepts input (e.g., "/api/v1/users")
# 3. Replace <REQUIRED_FIELD> with a required field name (e.g., "email")
# 4. Replace <NUMERIC_FIELD> with a numeric field (e.g., "age")
# 5. Replace <TEXT_FIELD> with a text field (e.g., "description")
# 6. Replace <AUTH_TOKEN> if the endpoint requires auth, or leave empty
# 7. Each test can be adapted to your target's actual payload and behavior

BASE_URL = "http://localhost:8000"

class TestInputValidation:
    """Test input validation and boundary abuse — Step 7 security checks."""

    @pytest.fixture
    def http_client(self):
        with httpx.Client(timeout=10.0, base_url=BASE_URL) as client:
            yield client

    @pytest.fixture
    def headers(self):
        token = "<AUTH_TOKEN>"
        if token:
            return {"Authorization": f"Bearer {token}"}
        return {}

    def test_missing_required_field(self, http_client, headers):
        """POST without required field should return 400, not 500."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {}  # Missing <REQUIRED_FIELD>
        response = http_client.post(endpoint, json=payload, headers=headers)
        assert response.status_code == 400
        assert response.status_code != 500

    def test_empty_string_required_field(self, http_client, headers):
        """POST with empty string for required field should return 400."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "<REQUIRED_FIELD>": ""
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        assert response.status_code == 400

    def test_null_required_field(self, http_client, headers):
        """POST with null/None for required field should return 400."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "<REQUIRED_FIELD>": None
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        assert response.status_code == 400

    def test_wrong_field_type_string_to_number(self, http_client, headers):
        """POST with string where number expected should return 400."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "<NUMERIC_FIELD>": "not_a_number"
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        assert response.status_code == 400

    def test_wrong_field_type_number_to_string(self, http_client, headers):
        """POST with number where string expected should return 400."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "<TEXT_FIELD>": 12345  # If expecting string
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # Some APIs coerce, some reject — either 200 or 400 is acceptable, not 500
        assert response.status_code != 500

    def test_negative_value_positive_only_field(self, http_client, headers):
        """POST with negative value for positive-only field should return 400."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "<NUMERIC_FIELD>": -1  # Assuming field should be >= 0
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        assert response.status_code in [400, 422]

    def test_oversized_string_payload(self, http_client, headers):
        """POST with 10,000 char string should not crash (400 or 413, never 500)."""
        endpoint = "<TARGET_ENDPOINT>"
        huge_string = "A" * 10000
        payload = {
            "<TEXT_FIELD>": huge_string
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # Should reject gracefully, not crash
        assert response.status_code != 500
        assert response.status_code in [400, 413, 414, 422]

    def test_sql_injection_attempt_in_text_field(self, http_client, headers):
        """POST with SQL injection string should not crash or leak data."""
        endpoint = "<TARGET_ENDPOINT>"
        sql_injection = "'; DROP TABLE users; --"
        payload = {
            "<TEXT_FIELD>": sql_injection
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # Must not return 500, must not leak query output
        assert response.status_code != 500
        assert "SQL" not in response.text.upper() or response.status_code != 200

    def test_command_injection_attempt(self, http_client, headers):
        """POST with command injection string should be safely rejected."""
        endpoint = "<TARGET_ENDPOINT>"
        command_injection = "$(whoami); cat /etc/passwd"
        payload = {
            "<TEXT_FIELD>": command_injection
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # Must not crash or execute
        assert response.status_code != 500

    def test_unicode_and_emoji_in_text_field(self, http_client, headers):
        """POST with unicode and emoji should be handled gracefully."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "<TEXT_FIELD>": "Hello 世界 🚀 ñoño"
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # Should not crash
        assert response.status_code != 500
        # Should either accept (200/201) or reject cleanly (400)
        assert response.status_code in [200, 201, 400, 422]

    def test_null_bytes_in_string(self, http_client, headers):
        """POST with null byte in string should be handled safely."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "<TEXT_FIELD>": "Hello\x00World"
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        assert response.status_code != 500

    def test_deeply_nested_json(self, http_client, headers):
        """POST with 50-level nested JSON should not crash."""
        endpoint = "<TARGET_ENDPOINT>"
        # Build deeply nested structure
        nested = {"level": 0}
        current = nested
        for i in range(50):
            current["nested"] = {"level": i + 1}
            current = current["nested"]
        payload = {
            "<TEXT_FIELD>": "test",
            "metadata": nested
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        assert response.status_code != 500

    def test_extremely_large_json_payload(self, http_client, headers):
        """POST with 1MB payload should be rejected gracefully."""
        endpoint = "<TARGET_ENDPOINT>"
        large_array = ["x"] * 100000
        payload = {
            "<TEXT_FIELD>": "test",
            "data": large_array
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # Should reject or timeout, not crash with 500
        assert response.status_code in [400, 413, 414, 422] or response.status_code == 408

    def test_special_characters_in_text_field(self, http_client, headers):
        """POST with special characters should be handled."""
        endpoint = "<TARGET_ENDPOINT>"
        special = "<script>alert('xss')</script>"
        payload = {
            "<TEXT_FIELD>": special
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # Should not crash
        assert response.status_code != 500
        # If accepted (200/201), should not execute as code (but we can't test that from API level)

    def test_boolean_field_with_string(self, http_client, headers):
        """POST with string for boolean field should be handled."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "is_active": "yes"  # Assuming boolean field; many APIs coerce "yes"/"true"
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # Either coerce or reject cleanly
        assert response.status_code != 500

    def test_float_where_integer_expected(self, http_client, headers):
        """POST with float where integer expected — some APIs accept, some reject."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "<NUMERIC_FIELD>": 3.14
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # Should not crash
        assert response.status_code != 500

    def test_zero_value_numeric_field(self, http_client, headers):
        """POST with 0 for numeric field — should be valid unless field explicitly non-zero."""
        endpoint = "<TARGET_ENDPOINT>"
        payload = {
            "<NUMERIC_FIELD>": 0
        }
        response = http_client.post(endpoint, json=payload, headers=headers)
        # 0 is often valid; if not, should reject with 400, not 500
        assert response.status_code != 500
