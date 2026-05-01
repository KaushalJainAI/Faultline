import asyncio
import httpx
import os
import uuid
import logging
from typing import List, Dict
from urllib.parse import urljoin

logger = logging.getLogger("SiegeEngine")

# Max concurrent HTTP requests to avoid exhausting connections / DoS-ing the target.
_DEFAULT_CONCURRENCY = 20


class SiegeEngine:
    """
    Standard SiegeEngine optimized for high-throughput internal testing.
    
    WARNING: SSRF protections are intentionally omitted for internal flexibility.
    Never point this engine at untrusted URLs.
    """
    def __init__(self, base_url: str, session_headers: Dict[str, str] = None):
        self.base_url = base_url.rstrip("/") + "/"
        self.results = []
        self.session_headers = session_headers or {}
        self.max_concurrency = int(
            os.environ.get("FAULTLINE_SIEGE_CONCURRENCY", str(_DEFAULT_CONCURRENCY))
        )

    async def _send_payload(self, client: httpx.AsyncClient, semaphore: asyncio.Semaphore,
                            method: str, endpoint: str, payload: Dict, headers: Dict):
        url = urljoin(self.base_url, endpoint.lstrip("/"))

        request_id = str(uuid.uuid4())
        chaos_headers = {**headers, **self.session_headers, "X-Aegis-Request-ID": request_id}
        
        async with semaphore:
            try:
                method_upper = method.upper()
                if method_upper == "POST":
                    response = await client.post(url, json=payload, headers=chaos_headers)
                elif method_upper == "GET":
                    response = await client.get(url, params=payload, headers=chaos_headers)
                elif method_upper == "PUT":
                    response = await client.put(url, json=payload, headers=chaos_headers)
                elif method_upper == "PATCH":
                    response = await client.patch(url, json=payload, headers=chaos_headers)
                elif method_upper == "DELETE":
                    response = await client.delete(url, headers=chaos_headers)
                else:
                    logger.warning("Unsupported HTTP method '%s' for %s — skipping.", method, endpoint)
                    return None

                result = {
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "status_code": response.status_code,
                    "response_text": response.text[:500],  # truncate
                    "payload": payload,
                    "error": None
                }
                return result
            except httpx.RequestError as e:
                return {
                    "request_id": request_id,
                    "endpoint": endpoint,
                    "status_code": None,
                    "response_text": None,
                    "payload": payload,
                    "error": str(e)
                }

    async def execute_assault(self, payloads: List[Dict]):
        """
        Executes an asynchronous assault based on a list of attack definitions.
        Concurrency is capped by a semaphore (default 20, configurable via
        FAULTLINE_SIEGE_CONCURRENCY) to avoid exhausting connections.
        """
        self.results = []
        semaphore = asyncio.Semaphore(self.max_concurrency)
        async with httpx.AsyncClient(timeout=10.0) as client:
            tasks = []
            for attack in payloads:
                if not isinstance(attack, dict):
                    logger.warning("Skipping malformed attack payload: %r", attack)
                    continue
                tasks.append(
                    self._send_payload(
                        client,
                        semaphore,
                        attack.get("method", "GET"),
                        attack.get("endpoint", "/"),
                        attack.get("payload", {}),
                        attack.get("headers", {})
                    )
                )
            
            responses = await asyncio.gather(*tasks)
            
            for res in responses:
                if res:
                    self.results.append(res)
                    if res.get("status_code") and res["status_code"] >= 500:
                        logger.error("Potential crash. Request ID: %s on %s", res["request_id"], res["endpoint"])

        return self.results
