"""
Credential store for Faultline.

Reads  <target_dir>/.faultline/credentials.toml  and exposes named
credential roles to the agent.

Resolution order per role:
  1. token present in file                  → use directly
  2. username + password + login_url set    → caller must do login flow
  3. basic auth                             → encode username:password directly
  4. HITL prompt to operator
  5. Mark unavailable, skip auth-dependent tests

File layout:

    [target]
    url       = "http://localhost:8000"
    auth_type = "bearer"          # bearer | api_key | basic | cookie | none
    login_url = "/api/auth/login/"  # omit if not needed

    [credentials.default]
    username = "kj@7000"
    password = "lets code 69"
    token    = ""   # leave blank → auto-login via login_url or HITL

    [credentials.admin]
    username = "admin@example.com"
    password = "adminpass"
    token    = ""
"""

import base64
import logging
from pathlib import Path
from typing import Dict, Optional, Tuple

logger = logging.getLogger("FaultlineCredentialStore")

CONFIG_DIR = ".faultline"
CREDENTIALS_FILE = "credentials.toml"


class CredentialStore:
    def __init__(self, target_dir: str) -> None:
        self._path = Path(target_dir) / CONFIG_DIR / CREDENTIALS_FILE
        self._data: Dict = {}
        self._loaded = False

    def load(self) -> bool:
        """Parse the credentials file. Returns True if found and valid."""
        if not self._path.exists():
            logger.debug("No credentials file at %s", self._path)
            return False
        try:
            import tomllib  # Python 3.11+
        except ImportError:
            try:
                import tomli as tomllib  # pip install tomli
            except ImportError:
                logger.warning(
                    "tomllib/tomli not available — cannot read credentials.toml. "
                    "Install tomli: pip install tomli"
                )
                return False
        try:
            with open(self._path, "rb") as fh:
                self._data = tomllib.load(fh)
            self._loaded = True
            roles = list(self._data.get("credentials", {}).keys())
            logger.info("Loaded credentials from %s (roles: %s)", self._path, roles)
            return True
        except Exception as exc:
            logger.error("Failed to parse credentials file %s: %s", self._path, exc)
            return False

    # ── Public API ──────────────────────────────────────────────────────────

    @property
    def loaded(self) -> bool:
        return self._loaded

    @property
    def path(self) -> Path:
        return self._path

    def target_url(self) -> str:
        return self._data.get("target", {}).get("url", "")

    def auth_type(self) -> str:
        return self._data.get("target", {}).get("auth_type", "bearer").lower()

    def login_url(self) -> str:
        """Relative path to the login endpoint, e.g. '/api/auth/login/'."""
        return self._data.get("target", {}).get("login_url", "")

    def token_refresh_url(self) -> str:
        """
        Relative path to the JWT refresh endpoint.
        Defaults to /api/auth/token/refresh/ (simplejwt standard) if not set.
        """
        return self._data.get("target", {}).get(
            "token_refresh_url", "/api/auth/token/refresh/"
        )

    def get(self, role: str = "default") -> Optional[Dict[str, str]]:
        """
        Return the raw credential dict for *role*, or None if not found.
        Keys: username, email, password, token (all strings, may be empty).
        `email` is used for allauth / dj-rest-auth style logins.
        `username` is used for simplejwt / basic auth style logins.
        """
        return self._data.get("credentials", {}).get(role)

    def needs_login(self, role: str = "default") -> bool:
        """
        True when the role has username+password but no token AND a
        login_url is configured — i.e. an auto-login flow is needed.
        Does NOT apply to basic auth (which uses u/p directly).
        """
        cred = self.get(role)
        if not cred:
            return False
        if self.auth_type() == "basic":
            return False
        token = cred.get("token", "").strip()
        username = cred.get("username", "").strip()
        password = cred.get("password", "").strip()
        return (not token) and bool(username) and bool(password) and bool(self.login_url())

    def get_auth_header(self, role: str = "default", token_override: str = "") -> Optional[Dict[str, str]]:
        """
        Return a ready-to-use HTTP header dict for *role*, or None if the
        credential cannot be resolved from the file alone.

        Pass *token_override* when the caller has already obtained a token
        via the login flow and just needs the header formatted.

        Supports:
          bearer   →  {"Authorization": "Bearer <token>"}
          api_key  →  {"X-API-Key": "<token>"}
          basic    →  {"Authorization": "Basic <b64(user:pass)>"}  (no token needed)
          cookie   →  {"Cookie": "session=<token>"}
          none     →  {}

        Returns None when the required credential is genuinely missing
        (e.g. bearer with no token and no login_url — needs HITL).
        """
        cred = self.get(role)
        if cred is None:
            return None

        auth_type = self.auth_type()
        token = token_override or cred.get("token", "").strip()
        username = cred.get("username", "").strip()
        password = cred.get("password", "").strip()

        if auth_type == "none":
            return {}

        if auth_type == "basic":
            if not (username and password):
                return None
            encoded = base64.b64encode(f"{username}:{password}".encode()).decode()
            return {"Authorization": f"Basic {encoded}"}

        if auth_type == "bearer":
            if not token:
                return None  # caller must run login flow or HITL
            return {"Authorization": f"Bearer {token}"}

        if auth_type == "api_key":
            if not token:
                return None
            return {"X-API-Key": token}

        if auth_type == "cookie":
            if not token:
                return None
            return {"Cookie": f"session={token}"}

        return None

    def list_roles(self) -> list:
        return list(self._data.get("credentials", {}).keys())

    def summary(self) -> str:
        if not self._loaded:
            return f"No credentials file found at {self._path}"
        roles = self.list_roles()
        auth = self.auth_type()
        url = self.target_url()
        login = self.login_url()
        return (
            f"Credentials loaded from {self._path}\n"
            f"  target url : {url or '(not set)'}\n"
            f"  auth type  : {auth}\n"
            f"  login url  : {login or '(not set)'}\n"
            f"  roles      : {', '.join(roles) or '(none)'}"
        )


# ── Module-level store — populated by faultline.py before the agent runs ────

_store: Optional[CredentialStore] = None


def init_store(target_dir: str, credentials_path: Optional[str] = None) -> "CredentialStore":
    """
    Initialise the module-level credential store.

    credentials_path — explicit path to a credentials.toml file (e.g.
    "media/aiaas_credentials.toml").  When supplied it takes priority over
    the default <target_dir>/.faultline/credentials.toml location.
    """
    global _store
    if credentials_path:
        _store = CredentialStore.__new__(CredentialStore)
        _store._path = Path(credentials_path).resolve()
        _store._data = {}
        _store._loaded = False
        _store.load()
    else:
        _store = CredentialStore(target_dir)
        _store.load()
    return _store


def get_store() -> Optional["CredentialStore"]:
    return _store
