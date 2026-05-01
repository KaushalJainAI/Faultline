import pytest
import httpx
import threading

# BOILERPLATE: API Authentication & Authorization Test Template
# Instructions for Agent:
# 1. Copy this file to reports/testcases/test_auth_<HHMMSS>.py
# 2. Replace <LOGIN_ENDPOINT> with the login endpoint (e.g., "/api/v1/auth/login")
# 3. Replace <PROTECTED_ENDPOINT> with a resource endpoint that requires auth (e.g., "/api/v1/users/me")
# 4. Replace <ADMIN_ENDPOINT> with an admin-only endpoint (e.g., "/api/v1/admin/users")
# 5. Define <VALID_CREDENTIALS> dict (e.g., {"email": "user@example.com", "password": "password123"})
# 6. Define <WRONG_PASSWORD_CREDENTIALS> dict (e.g., {"email": "user@example.com", "password": "wrong"})
# 7. Set <AUTH_TOKEN_FIELD> to the JSON path for the token in login response (e.g., "token" or "data.access_token")
# 8. For role-based tests, define <USER_ROLE_TOKEN> and <ADMIN_ROLE_TOKEN>

BASE_URL = "http://localhost:8000"

class TestAuthBoundaries:
    """Test authentication and authorization boundaries."""

    @pytest.fixture
    def http_client(self):
        with httpx.Client(timeout=10.0, base_url=BASE_URL) as client:
            yield client

    def test_login_with_valid_credentials(self, http_client):
        """Login with valid credentials should return 200 and a token."""
        endpoint = "<LOGIN_ENDPOINT>"
        payload = {
            # <VALID_CREDENTIALS>
        }
        response = http_client.post(endpoint, json=payload)
        assert response.status_code == 200
        response_data = response.json()
        # Token should be in response under <AUTH_TOKEN_FIELD> path
        # assert "<AUTH_TOKEN_FIELD>" in response_data

    def test_login_with_wrong_password(self, http_client):
        """Login with wrong password should return 400/401."""
        endpoint = "<LOGIN_ENDPOINT>"
        payload = {
            # <WRONG_PASSWORD_CREDENTIALS>
        }
        response = http_client.post(endpoint, json=payload)
        assert response.status_code in [400, 401]

    def test_login_nonexistent_user(self, http_client):
        """Login with non-existent user should return 400/404."""
        endpoint = "<LOGIN_ENDPOINT>"
        payload = {"email": "nonexistent@example.com", "password": "anypassword"}
        response = http_client.post(endpoint, json=payload)
        assert response.status_code in [400, 404]

    def test_unauthenticated_access_to_protected_endpoint(self, http_client):
        """Unauthenticated request to protected endpoint should return 401."""
        endpoint = "<PROTECTED_ENDPOINT>"
        response = http_client.get(endpoint)
        assert response.status_code == 401

    def test_authenticated_access_to_protected_endpoint(self, http_client):
        """Authenticated request to protected endpoint should return 200."""
        login_endpoint = "<LOGIN_ENDPOINT>"
        protected_endpoint = "<PROTECTED_ENDPOINT>"

        # Login to get token
        login_payload = {
            # <VALID_CREDENTIALS>
        }
        login_response = http_client.post(login_endpoint, json=login_payload)
        assert login_response.status_code == 200

        # Extract token from response (adjust path as needed)
        token = login_response.json().get("<AUTH_TOKEN_FIELD>")
        assert token is not None

        # Access protected endpoint with token
        headers = {"Authorization": f"Bearer {token}"}
        response = http_client.get(protected_endpoint, headers=headers)
        assert response.status_code == 200

    def test_expired_token(self, http_client):
        """Request with expired token should return 401."""
        endpoint = "<PROTECTED_ENDPOINT>"
        expired_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJleHAiOjE1MTYyMzkwMjJ9.invalid"
        headers = {"Authorization": f"Bearer {expired_token}"}
        response = http_client.get(endpoint, headers=headers)
        assert response.status_code == 401

    def test_malformed_token(self, http_client):
        """Request with malformed token should return 401."""
        endpoint = "<PROTECTED_ENDPOINT>"
        headers = {"Authorization": "Bearer malformed_token_xyz"}
        response = http_client.get(endpoint, headers=headers)
        assert response.status_code == 401

    def test_missing_authorization_header(self, http_client):
        """Request missing Authorization header should return 401."""
        endpoint = "<PROTECTED_ENDPOINT>"
        response = http_client.get(endpoint)
        assert response.status_code == 401

    def test_insufficient_permissions_user_to_admin_endpoint(self, http_client):
        """Regular user token cannot access admin endpoint — should return 403."""
        login_endpoint = "<LOGIN_ENDPOINT>"
        admin_endpoint = "<ADMIN_ENDPOINT>"

        login_payload = {
            # <VALID_CREDENTIALS>  (regular user, not admin)
        }
        login_response = http_client.post(login_endpoint, json=login_payload)
        token = login_response.json().get("<AUTH_TOKEN_FIELD>")

        headers = {"Authorization": f"Bearer {token}"}
        response = http_client.get(admin_endpoint, headers=headers)
        assert response.status_code == 403

    def test_admin_token_can_access_admin_endpoint(self, http_client):
        """Admin token can access admin endpoint — should return 200."""
        admin_endpoint = "<ADMIN_ENDPOINT>"
        # This assumes you have an <ADMIN_ROLE_TOKEN> placeholder for a pre-generated admin token
        # or you login with admin credentials
        admin_token = "<ADMIN_ROLE_TOKEN>"
        headers = {"Authorization": f"Bearer {admin_token}"}
        response = http_client.get(admin_endpoint, headers=headers)
        assert response.status_code == 200

    def test_concurrent_login_attempts(self, http_client):
        """Concurrent login requests should not crash or race."""
        endpoint = "<LOGIN_ENDPOINT>"
        payload = {
            # <VALID_CREDENTIALS>
        }

        results = []
        def login_task():
            resp = http_client.post(endpoint, json=payload)
            results.append(resp.status_code)

        threads = [threading.Thread(target=login_task) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All should succeed (200) or at least not crash (no exceptions)
        assert len(results) == 10
        assert all(status == 200 for status in results)

    def test_empty_login_credentials(self, http_client):
        """Login with empty credentials should return 400."""
        endpoint = "<LOGIN_ENDPOINT>"
        payload = {}
        response = http_client.post(endpoint, json=payload)
        assert response.status_code == 400

    def test_missing_required_login_field(self, http_client):
        """Login missing required field should return 400."""
        endpoint = "<LOGIN_ENDPOINT>"
        payload = {"email": "user@example.com"}  # missing password
        response = http_client.post(endpoint, json=payload)
        assert response.status_code == 400
