import contextvars
from typing import Dict

# Context variable to hold session headers for the current campaign run
session_headers_var: contextvars.ContextVar[Dict[str, str]] = contextvars.ContextVar("session_headers", default={})
