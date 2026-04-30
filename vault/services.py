import httpx
import logging
from typing import Dict, Any, Optional
from urllib.parse import urljoin
from vault.models import AuthFlow

logger = logging.getLogger("VaultAuthenticator")

class Authenticator:
    def __init__(self, target_base_url: str, auth_flow: AuthFlow):
        self.target_base_url = target_base_url.rstrip("/") + "/"
        self.auth_flow = auth_flow

    def _extract_token(self, data: Dict[str, Any], path: str) -> Optional[str]:
        """Simple extractor. E.g., 'data.token' or 'access_token'"""
        if not path:
            return None
        parts = path.split('.')
        current = data
        try:
            for part in parts:
                current = current[part]
            return str(current)
        except (KeyError, TypeError):
            return None

    def execute_flow(self) -> Dict[str, Dict[str, str]]:
        """
        Executes the AuthFlow and returns a dictionary with 'headers' and 'cookies'.
        """
        result = {"headers": {}, "cookies": {}}
        
        token = None

        if self.auth_flow.auth_type == AuthFlow.AuthType.STATIC_TOKEN:
            # If static token, auth_payload might be a dict with the token, or just the token string
            if isinstance(self.auth_flow.auth_payload, dict):
                # Try to extract it if path is provided, otherwise assume it's just the dict
                token = self._extract_token(self.auth_flow.auth_payload, self.auth_flow.token_extraction_path)
                if not token:
                     # fallback: just take the first value if it's a simple dict
                     token = list(self.auth_flow.auth_payload.values())[0] if self.auth_flow.auth_payload else None
            else:
                token = str(self.auth_flow.auth_payload)
        
        elif self.auth_flow.auth_type == AuthFlow.AuthType.LOGIN_ENDPOINT:
            url = urljoin(self.target_base_url, self.auth_flow.auth_url.lstrip("/"))
            method = self.auth_flow.auth_method.upper()
            
            logger.info(f"Executing AuthFlow '{self.auth_flow.name}' against {url} via {method}")
            
            try:
                # Synchronous request for pre-flight authentication
                with httpx.Client(timeout=10.0) as client:
                    if method == "POST":
                        response = client.post(url, json=self.auth_flow.auth_payload)
                    elif method == "GET":
                        response = client.get(url, params=self.auth_flow.auth_payload)
                    else:
                        raise ValueError(f"Unsupported auth method: {method}")
                    
                    response.raise_for_status()
                    data = response.json()
                    
                    token = self._extract_token(data, self.auth_flow.token_extraction_path)
                    
                    if not token:
                        logger.error(f"Failed to extract token using path '{self.auth_flow.token_extraction_path}' from response: {data}")
                        
            except Exception as e:
                logger.error(f"AuthFlow failed: {str(e)}")
                return result

        if token:
            # Format the injected value
            injected_value = self.auth_flow.injection_format.replace("{token}", token)
            
            if self.auth_flow.injection_type == AuthFlow.InjectionType.HEADER:
                result["headers"][self.auth_flow.injection_key] = injected_value
            elif self.auth_flow.injection_type == AuthFlow.InjectionType.COOKIE:
                result["cookies"][self.auth_flow.injection_key] = injected_value

        return result
