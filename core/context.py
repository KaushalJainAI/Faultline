import contextvars
from typing import Dict

# Context variable to hold session headers for the current campaign run
session_headers_var: contextvars.ContextVar[Dict[str, str]] = contextvars.ContextVar("session_headers", default={})

# Set to True by the CLI HITL gate when the operator denies a chaos campaign.
# Checked by execute_chaos_campaign to short-circuit the attack.
chaos_vetoed_var: contextvars.ContextVar[bool] = contextvars.ContextVar("chaos_vetoed", default=False)
