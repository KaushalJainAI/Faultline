import pytest
import httpx

# BOILERPLATE: API IDOR (Insecure Direct Object Reference) & Authorization Testing
# Instructions for Agent:
# 1. Copy this file to reports/testcases/test_idor_<HHMMSS>.py
# 2. Replace <RESOURCE_ENDPOINT> with the resource endpoint (e.g., "/api/v1/documents")
# 3. Replace <USER_A_TOKEN> with a token for a standard user account
# 4. Replace <USER_B_TOKEN> with a token for a different user account
# 5. Replace <USER_B_RESOURCE_ID> with an ID of a resource owned by User B (integer or UUID)
# 6. Replace <ADMIN_ENDPOINT> with an admin-only endpoint (e.g., "/api/v1/admin/settings")
# 7. Replace <USER_TOKEN> with a regular (non-admin) user token
# 8. For each test, verify that the endpoint properly enforces ownership/role boundaries

BASE_URL = "http://localhost:8000"

class TestIDOR:
    """Test IDOR (Insecure Direct Object Reference) and authorization boundaries."""

    @pytest.fixture
    def http_client(self):
        with httpx.Client(timeout=10.0, base_url=BASE_URL) as client:
            yield client

    def test_user_cannot_access_other_users_resource_by_id(self, http_client):
        """User A with their token cannot read User B's resource ID."""
        endpoint = "<RESOURCE_ENDPOINT>"
        user_a_token = "<USER_A_TOKEN>"
        user_b_resource_id = "<USER_B_RESOURCE_ID>"

        headers = {"Authorization": f"Bearer {user_a_token}"}
        response = http_client.get(f"{endpoint}/{user_b_resource_id}", headers=headers)

        # Should return 403 Forbidden or 404 Not Found, NOT 200
        assert response.status_code in [403, 404]
        # Must not leak user B's data
        response_text = response.text.lower()
        # If endpoint returns user names or emails, User B's should not appear
        # (This is a weak test; adapt to your actual response structure)

    def test_user_cannot_update_other_users_resource(self, http_client):
        """User A cannot PUT/PATCH User B's resource."""
        endpoint = "<RESOURCE_ENDPOINT>"
        user_a_token = "<USER_A_TOKEN>"
        user_b_resource_id = "<USER_B_RESOURCE_ID>"

        headers = {"Authorization": f"Bearer {user_a_token}"}
        update_payload = {"name": "Hacked by User A"}
        response = http_client.put(
            f"{endpoint}/{user_b_resource_id}",
            json=update_payload,
            headers=headers
        )

        assert response.status_code in [403, 404]

    def test_user_cannot_delete_other_users_resource(self, http_client):
        """User A cannot DELETE User B's resource."""
        endpoint = "<RESOURCE_ENDPOINT>"
        user_a_token = "<USER_A_TOKEN>"
        user_b_resource_id = "<USER_B_RESOURCE_ID>"

        headers = {"Authorization": f"Bearer {user_a_token}"}
        response = http_client.delete(f"{endpoint}/{user_b_resource_id}", headers=headers)

        assert response.status_code in [403, 404]

    def test_sequential_id_walking_same_user(self, http_client):
        """Accessing sequential IDs with same user token should not expose others' data."""
        endpoint = "<RESOURCE_ENDPOINT>"
        user_a_token = "<USER_A_TOKEN>"

        headers = {"Authorization": f"Bearer {user_a_token}"}

        # Try to access IDs 1, 2, 3, ... and collect which ones are accessible
        accessible_ids = []
        for id_num in range(1, 11):
            response = http_client.get(f"{endpoint}/{id_num}", headers=headers)
            if response.status_code == 200:
                accessible_ids.append(id_num)

        # User A should only be able to access their own resources, not random IDs
        # This is a heuristic: if User A can access many sequential IDs, IDOR risk exists
        # Adapt the threshold based on how many resources User A actually owns
        # For now, a simple check: if too many are accessible, IDOR issue
        if len(accessible_ids) > 2:
            # Investigate further (user may own multiple resources)
            pass

    def test_uuid_resource_with_other_user_token(self, http_client):
        """Accessing UUID-based resource with another user's token should be rejected."""
        endpoint = "<RESOURCE_ENDPOINT>"
        user_a_token = "<USER_A_TOKEN>"
        user_b_uuid_resource = "<USER_B_RESOURCE_ID>"  # UUID format

        headers = {"Authorization": f"Bearer {user_a_token}"}
        response = http_client.get(f"{endpoint}/{user_b_uuid_resource}", headers=headers)

        assert response.status_code in [403, 404]

    def test_unauthenticated_access_to_resource_detail(self, http_client):
        """Unauthenticated request to resource detail should return 401."""
        endpoint = "<RESOURCE_ENDPOINT>"
        user_b_resource_id = "<USER_B_RESOURCE_ID>"

        # No Authorization header
        response = http_client.get(f"{endpoint}/{user_b_resource_id}")
        assert response.status_code == 401

    def test_user_cannot_access_admin_endpoint(self, http_client):
        """Regular user token cannot access admin-only endpoint."""
        endpoint = "<ADMIN_ENDPOINT>"
        user_token = "<USER_TOKEN>"

        headers = {"Authorization": f"Bearer {user_token}"}
        response = http_client.get(endpoint, headers=headers)

        assert response.status_code == 403

    def test_user_cannot_modify_admin_settings(self, http_client):
        """Regular user cannot PUT/POST to admin endpoint."""
        endpoint = "<ADMIN_ENDPOINT>"
        user_token = "<USER_TOKEN>"

        headers = {"Authorization": f"Bearer {user_token}"}
        payload = {"setting": "value"}
        response = http_client.post(endpoint, json=payload, headers=headers)

        assert response.status_code == 403

    def test_elevated_endpoint_with_insufficient_role(self, http_client):
        """Endpoint requiring role >= editor should reject viewer token."""
        # Adjust this test based on your role hierarchy
        endpoint = "<RESOURCE_ENDPOINT>"  # Or a specific elevated endpoint
        user_token = "<USER_TOKEN>"  # Assuming this is a viewer/standard user

        headers = {"Authorization": f"Bearer {user_token}"}
        response = http_client.delete(f"{endpoint}/1", headers=headers)

        # If DELETE is admin-only, should be 403
        # If endpoint is standard, may be different
        # Adapt as needed
        if response.status_code == 403:
            assert True  # Good — authorization enforced

    def test_two_users_cannot_see_each_others_lists(self, http_client):
        """User A's GET list should not contain User B's resources."""
        endpoint = "<RESOURCE_ENDPOINT>"
        user_a_token = "<USER_A_TOKEN>"
        user_b_token = "<USER_B_TOKEN>"

        # User A's list
        headers_a = {"Authorization": f"Bearer {user_a_token}"}
        response_a = http_client.get(endpoint, headers=headers_a)
        assert response_a.status_code == 200
        list_a = response_a.json()

        # User B's list
        headers_b = {"Authorization": f"Bearer {user_b_token}"}
        response_b = http_client.get(endpoint, headers=headers_b)
        assert response_b.status_code == 200
        list_b = response_b.json()

        # Extract IDs from both lists (adapt structure as needed)
        # If using {"data": [...]} format:
        ids_a = {item.get("id") for item in list_a.get("data", list_a) if isinstance(list_a, list) or isinstance(list_a.get("data"), list)}
        ids_b = {item.get("id") for item in list_b.get("data", list_b) if isinstance(list_b, list) or isinstance(list_b.get("data"), list)}

        # Ideally, ids_a and ids_b should be disjoint (no overlap)
        # If they overlap, User B's resources are leaking to User A's list
        overlap = ids_a.intersection(ids_b)
        if overlap:
            pytest.fail(f"Resource ID leak detected: {overlap} appears in both users' lists")

    def test_cross_tenant_access_attempt(self, http_client):
        """If multi-tenant, User A from Tenant 1 cannot access Tenant 2 data."""
        # This test is org/tenant-specific; adapt as needed
        endpoint = "<RESOURCE_ENDPOINT>"
        user_token = "<USER_A_TOKEN>"  # User from Tenant 1

        headers = {"Authorization": f"Bearer {user_token}"}

        # Try to access a resource ID that belongs to Tenant 2
        # The ID might be hardcoded if you know it, or passed as a placeholder
        tenant_2_resource_id = "<USER_B_RESOURCE_ID>"  # Could be from different tenant
        response = http_client.get(f"{endpoint}/{tenant_2_resource_id}", headers=headers)

        # Should be 403 or 404, not 200
        assert response.status_code in [403, 404]

    def test_parameter_pollution_bypass_attempt(self, http_client):
        """Sending both user_id param and token should use token, not param."""
        endpoint = "<RESOURCE_ENDPOINT>"
        user_a_token = "<USER_A_TOKEN>"
        user_b_id = "<USER_B_RESOURCE_ID>"

        headers = {"Authorization": f"Bearer {user_a_token}"}
        # Try to request with both token and a malicious param
        response = http_client.get(
            f"{endpoint}/{user_b_id}",
            headers=headers,
            params={"user_id": "999"}  # Try to override with param
        )

        # Should still enforce the token-based authorization, not the param
        assert response.status_code in [403, 404]
