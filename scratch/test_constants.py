import os
from core.progress_tracker import PHASE_CAPS, WRAP_UP_BUDGET_PCT
from core.content_manager import TIER2_CYCLES, SUMMARIZATION_THRESHOLD_TOKENS

print("--- Current Configuration (from .env) ---")
print(f"Discovery Cap: {PHASE_CAPS['discovery']}")
print(f"Test Cap:      {PHASE_CAPS['test']}")
print(f"Chaos Cap:     {PHASE_CAPS['chaos']}")
print(f"Report Cap:    {PHASE_CAPS['report']}")
print(f"Wrap Up Thresh: {WRAP_UP_BUDGET_PCT}")
print(f"Tier 2 Cycles: {TIER2_CYCLES}")
print(f"Summary Thresh: {SUMMARIZATION_THRESHOLD_TOKENS}")

# Test override
os.environ["FAULTLINE_PHASE_TEST_CAP"] = "100"
# Note: Since constants are evaluated at import time, we'd need to reload modules to see this change 
# if we were testing the environment reading logic itself in a single process.
# But since we just added them to .env, the next run will pick them up correctly.
