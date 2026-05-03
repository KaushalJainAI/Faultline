import sys
from pathlib import Path
from skills.deprecation_guard import DeprecationGuard

target_dir = Path("scratch/test_deprecation").resolve()
target_python = sys.executable

guard = DeprecationGuard(target_dir, target_python)
findings = guard.check_runtime_deprecations()

print(f"Target Dir: {target_dir}")
print(f"Target Python: {target_python}")
print(f"Findings found: {len(findings)}")
for f in findings:
    print(f" - {f['title']}: {f['file_path']}:{f['line_number']} -> {f['summary']}")

# Also print the raw output of the command for debugging
import subprocess
import os
env = os.environ.copy()
env["PYTHONWARNINGS"] = "always::DeprecationWarning,always::FutureWarning"
result = subprocess.run(
    [target_python, "-m", "pytest", "--collect-only", "-q", "-p", "no:django"],
    cwd=target_dir,
    capture_output=True,
    text=True,
    encoding="utf-8",
    errors="replace",
    env=env
)
print("\n--- RAW STDERR ---")
print(result.stderr)
