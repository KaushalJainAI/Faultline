import pytest
import httpx
import threading

# BOILERPLATE: API CRUD Test Template with Fixtures
# Instructions for Agent:
# 1. Copy this file to reports/testcases/test_crud_<HHMMSS>.py
# 2. Replace <RESOURCE_ENDPOINT> with the resource endpoint (e.g., "/api/v1/resources")
# 3. Replace <CREATE_PAYLOAD> with a sample object (e.g., {"name": "Test", "description": "Test item"})
# 4. Replace <UPDATE_PAYLOAD> with an update payload (e.g., {"name": "Updated"})
# 5. Replace <PATCH_PAYLOAD> with a partial update (e.g., {"description": "New description"})
# 6. Replace <AUTH_TOKEN> with the auth token, or set to empty string if no auth needed
# 7. Replace <EXPECTED_FIELDS> with a list of fields the response should contain
# 8. Optionally parametrize test data and expected responses

BASE_URL = "http://localhost:8000"

class APIClient:
    """Simple API client wrapper for cleaner test code."""
    def __init__(self, base_url, token=None):
        self.base_url = base_url
        self.token = token
        self.client = httpx.Client(timeout=10.0, base_url=base_url)

    def _headers(self):
        headers = {}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def get(self, endpoint, params=None):
        return self.client.get(endpoint, headers=self._headers(), params=params)

    def post(self, endpoint, json=None):
        return self.client.post(endpoint, json=json, headers=self._headers())

    def put(self, endpoint, json=None):
        return self.client.put(endpoint, json=json, headers=self._headers())

    def patch(self, endpoint, json=None):
        return self.client.patch(endpoint, json=json, headers=self._headers())

    def delete(self, endpoint):
        return self.client.delete(endpoint, headers=self._headers())

    def close(self):
        self.client.close()


class TestResourceCRUD:
    """Test complete CRUD operations on a resource endpoint."""

    @pytest.fixture
    def api_client(self):
        token = "<AUTH_TOKEN>"
        client = APIClient(BASE_URL, token=token if token else None)
        yield client
        client.close()

    @pytest.fixture
    def sample_payload(self):
        return {
            # <CREATE_PAYLOAD>
        }

    @pytest.fixture
    def update_payload(self):
        return {
            # <UPDATE_PAYLOAD>
        }

    @pytest.fixture
    def patch_payload(self):
        return {
            # <PATCH_PAYLOAD>
        }

    def test_list_resources(self, api_client):
        """GET resource list should return 200 with array."""
        endpoint = "<RESOURCE_ENDPOINT>"
        response = api_client.get(endpoint)
        assert response.status_code == 200
        data = response.json()
        # Expect either a list or a dict with 'data'/'results'/'items' key
        if isinstance(data, dict):
            assert any(key in data for key in ['data', 'results', 'items'])
        else:
            assert isinstance(data, list)

    def test_create_resource(self, api_client, sample_payload):
        """POST to create resource should return 201 with created object."""
        endpoint = "<RESOURCE_ENDPOINT>"
        response = api_client.post(endpoint, json=sample_payload)
        assert response.status_code == 201
        created = response.json()
        assert "id" in created or "uuid" in created
        # Verify fields match what we sent
        for key, value in sample_payload.items():
            assert created.get(key) == value
        return created

    def test_read_resource(self, api_client, sample_payload):
        """GET resource by ID should return 200 with correct object."""
        endpoint = "<RESOURCE_ENDPOINT>"

        # Create first
        create_response = api_client.post(endpoint, json=sample_payload)
        assert create_response.status_code == 201
        resource_id = create_response.json().get("id") or create_response.json().get("uuid")

        # Read
        read_response = api_client.get(f"{endpoint}/{resource_id}")
        assert read_response.status_code == 200
        resource = read_response.json()
        assert resource.get("id") == resource_id or resource.get("uuid") == resource_id

    def test_update_resource_put(self, api_client, sample_payload, update_payload):
        """PUT to update entire resource should return 200 with updated object."""
        endpoint = "<RESOURCE_ENDPOINT>"

        # Create
        create_response = api_client.post(endpoint, json=sample_payload)
        resource_id = create_response.json().get("id") or create_response.json().get("uuid")

        # Update with PUT
        update_response = api_client.put(f"{endpoint}/{resource_id}", json=update_payload)
        assert update_response.status_code == 200
        updated = update_response.json()
        for key, value in update_payload.items():
            assert updated.get(key) == value

    def test_update_resource_patch(self, api_client, sample_payload, patch_payload):
        """PATCH to partially update resource should return 200."""
        endpoint = "<RESOURCE_ENDPOINT>"

        # Create
        create_response = api_client.post(endpoint, json=sample_payload)
        resource_id = create_response.json().get("id") or create_response.json().get("uuid")

        # Partial update with PATCH
        patch_response = api_client.patch(f"{endpoint}/{resource_id}", json=patch_payload)
        assert patch_response.status_code == 200
        patched = patch_response.json()
        for key, value in patch_payload.items():
            assert patched.get(key) == value

    def test_delete_resource(self, api_client, sample_payload):
        """DELETE resource should return 204."""
        endpoint = "<RESOURCE_ENDPOINT>"

        # Create
        create_response = api_client.post(endpoint, json=sample_payload)
        resource_id = create_response.json().get("id") or create_response.json().get("uuid")

        # Delete
        delete_response = api_client.delete(f"{endpoint}/{resource_id}")
        assert delete_response.status_code == 204

    def test_read_deleted_resource(self, api_client, sample_payload):
        """GET deleted resource should return 404."""
        endpoint = "<RESOURCE_ENDPOINT>"

        # Create
        create_response = api_client.post(endpoint, json=sample_payload)
        resource_id = create_response.json().get("id") or create_response.json().get("uuid")

        # Delete
        api_client.delete(f"{endpoint}/{resource_id}")

        # Try to read
        read_response = api_client.get(f"{endpoint}/{resource_id}")
        assert read_response.status_code == 404

    def test_list_with_pagination(self, api_client, sample_payload):
        """List endpoint should support pagination (page, per_page or limit/offset)."""
        endpoint = "<RESOURCE_ENDPOINT>"

        # Create a few resources
        for _ in range(3):
            api_client.post(endpoint, json=sample_payload)

        # List with pagination params
        response = api_client.get(endpoint, params={"page": 1, "per_page": 2})
        assert response.status_code == 200
        data = response.json()
        # Should have pagination or return a smaller set
        if isinstance(data, dict):
            assert "data" in data or "results" in data or "items" in data

    def test_nonexistent_resource_returns_404(self, api_client):
        """GET non-existent resource ID should return 404."""
        endpoint = "<RESOURCE_ENDPOINT>"
        fake_id = 99999
        response = api_client.get(f"{endpoint}/{fake_id}")
        assert response.status_code == 404

    def test_concurrent_list_requests(self, api_client):
        """Concurrent LIST requests should not crash or race."""
        endpoint = "<RESOURCE_ENDPOINT>"
        results = []

        def list_task():
            resp = api_client.get(endpoint)
            results.append(resp.status_code)

        threads = [threading.Thread(target=list_task) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 10
        assert all(status == 200 for status in results)

    def test_create_with_missing_required_field(self, api_client):
        """POST with missing required field should return 400."""
        endpoint = "<RESOURCE_ENDPOINT>"
        incomplete_payload = {}  # Empty, missing required fields
        response = api_client.post(endpoint, json=incomplete_payload)
        assert response.status_code == 400

    def test_create_with_wrong_field_type(self, api_client):
        """POST with wrong field type should return 400."""
        endpoint = "<RESOURCE_ENDPOINT>"
        bad_payload = {
            # Assume first field is a string, try passing an int or vice versa
            "field_name": 12345  # If expecting string, this is wrong type
        }
        response = api_client.post(endpoint, json=bad_payload)
        assert response.status_code == 400
