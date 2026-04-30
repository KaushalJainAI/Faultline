import uuid
from unittest.mock import patch, MagicMock

from django.test import TestCase
from django.urls import reverse

from vault.models import AuthFlow
from vault.services import Authenticator

class VaultModelTests(TestCase):
    def test_auth_flow_str(self):
        flow = AuthFlow.objects.create(name="Test Flow")
        self.assertEqual(str(flow), "Test Flow")

class VaultAPITests(TestCase):
    def test_list_auth_flows(self):
        flow = AuthFlow.objects.create(name="API Flow", auth_type=AuthFlow.AuthType.STATIC_TOKEN)
        response = self.client.get(reverse("authflow-list"))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["name"], "API Flow")

    def test_create_auth_flow(self):
        data = {
            "name": "New Flow",
            "auth_type": AuthFlow.AuthType.LOGIN_ENDPOINT,
            "auth_url": "/api/v1/login",
            "auth_method": "POST",
            "auth_payload": {"username": "admin", "password": "password"},
            "token_extraction_path": "data.token",
            "injection_type": AuthFlow.InjectionType.HEADER,
            "injection_key": "Authorization",
            "injection_format": "Bearer {token}"
        }
        response = self.client.post(reverse("authflow-list"), data, content_type="application/json")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(AuthFlow.objects.count(), 1)
        self.assertEqual(AuthFlow.objects.first().name, "New Flow")

    def test_create_auth_flow_missing_url(self):
        # auth_url is required for LOGIN_ENDPOINT
        data = {
            "name": "Invalid Flow",
            "auth_type": AuthFlow.AuthType.LOGIN_ENDPOINT,
            "auth_method": "POST"
        }
        response = self.client.post(reverse("authflow-list"), data, content_type="application/json")
        # Django REST framework will return 400 Bad Request
        self.assertEqual(response.status_code, 400)
        self.assertIn("auth_url", response.json())

class AuthenticatorTests(TestCase):
    def test_static_token_extraction(self):
        flow = AuthFlow(
            auth_type=AuthFlow.AuthType.STATIC_TOKEN,
            auth_payload="super-secret-token",
            injection_type=AuthFlow.InjectionType.HEADER,
            injection_key="Authorization",
            injection_format="Bearer {token}"
        )
        authenticator = Authenticator("http://example.com", flow)
        result = authenticator.execute_flow()
        self.assertEqual(result["headers"]["Authorization"], "Bearer super-secret-token")

    def test_static_token_dict_extraction(self):
        flow = AuthFlow(
            auth_type=AuthFlow.AuthType.STATIC_TOKEN,
            auth_payload={"token": "dict-token"},
            token_extraction_path="token",
            injection_type=AuthFlow.InjectionType.COOKIE,
            injection_key="session_id",
            injection_format="{token}"
        )
        authenticator = Authenticator("http://example.com", flow)
        result = authenticator.execute_flow()
        self.assertEqual(result["cookies"]["session_id"], "dict-token")

    @patch("vault.services.httpx.Client")
    def test_login_endpoint_success(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        
        mock_response = MagicMock()
        mock_response.json.return_value = {"data": {"access_token": "api-token-123"}}
        mock_client.post.return_value = mock_response

        flow = AuthFlow(
            auth_type=AuthFlow.AuthType.LOGIN_ENDPOINT,
            auth_url="/login",
            auth_method="POST",
            auth_payload={"user": "test"},
            token_extraction_path="data.access_token",
            injection_type=AuthFlow.InjectionType.HEADER,
            injection_key="X-Auth-Token",
            injection_format="{token}"
        )
        authenticator = Authenticator("http://example.com", flow)
        result = authenticator.execute_flow()
        
        mock_client.post.assert_called_once_with("http://example.com/login", json={"user": "test"})
        self.assertEqual(result["headers"]["X-Auth-Token"], "api-token-123")

    @patch("vault.services.httpx.Client")
    def test_login_endpoint_extraction_failure(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        
        mock_response = MagicMock()
        # Invalid response format according to token_extraction_path
        mock_response.json.return_value = {"wrong": "format"}
        mock_client.post.return_value = mock_response

        flow = AuthFlow(
            auth_type=AuthFlow.AuthType.LOGIN_ENDPOINT,
            auth_url="/login",
            auth_method="POST",
            token_extraction_path="data.access_token",
            injection_type=AuthFlow.InjectionType.HEADER
        )
        authenticator = Authenticator("http://example.com", flow)
        result = authenticator.execute_flow()
        
        # Should return empty headers/cookies if token extraction fails
        self.assertNotIn("Authorization", result["headers"])

    @patch("vault.services.httpx.Client")
    def test_login_endpoint_http_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        
        import httpx
        mock_client.post.side_effect = httpx.RequestError("Connection timeout")

        flow = AuthFlow(
            auth_type=AuthFlow.AuthType.LOGIN_ENDPOINT,
            auth_url="/login",
            auth_method="POST",
            auth_payload={"user": "test"},
            token_extraction_path="data.access_token",
            injection_type=AuthFlow.InjectionType.HEADER
        )
        authenticator = Authenticator("http://example.com", flow)
        result = authenticator.execute_flow()
        
        self.assertEqual(result["headers"], {})

    @patch("vault.services.httpx.Client")
    def test_login_endpoint_json_error(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client_cls.return_value.__enter__.return_value = mock_client
        
        mock_response = MagicMock()
        import json
        mock_response.json.side_effect = json.JSONDecodeError("Expecting value", "", 0)
        mock_client.post.return_value = mock_response

        flow = AuthFlow(
            auth_type=AuthFlow.AuthType.LOGIN_ENDPOINT,
            auth_url="/login",
            auth_method="POST",
            token_extraction_path="token",
            injection_type=AuthFlow.InjectionType.HEADER
        )
        authenticator = Authenticator("http://example.com", flow)
        result = authenticator.execute_flow()
        
        self.assertEqual(result["headers"], {})
