import os
import re
import subprocess
from pathlib import Path
from typing import List
import json

# Avoid circular import with DeterministicChecker if needed, 
# but we only need CheckFinding which is in deterministic_checker.py
# For now, we'll redefine a simple structure or import it carefully.

class DeprecationGuard:
    """
    Skill to capture runtime DeprecationWarnings and FutureWarnings 
    by executing safe 'dry-run' commands in the target environment.
    """

    def __init__(self, target_dir: Path, target_python: str, timeout: int = 30):
        self.target_dir = target_dir
        self.target_python = target_python
        self.timeout = timeout

    def check_runtime_deprecations(self) -> List[dict]:
        """
        Runs safe collection/check commands with PYTHONWARNINGS enabled.
        Returns a list of finding-like dictionaries.
        """
        findings = []
        
        # 1. Try pytest collection (triggers imports)
        findings.extend(self._run_warning_check([
            self.target_python, "-m", "pytest", "--collect-only", "-q", "-p", "no:django", "."
        ], "Pytest Collection"))

        # 2. If Django is detected, run manage.py check
        if (self.target_dir / "manage.py").exists():
            findings.extend(self._run_warning_check([
                self.target_python, "manage.py", "check"
            ], "Django System Check"))

        return findings

    def _run_warning_check(self, command: List[str], source_name: str) -> List[dict]:
        findings = []
        env = os.environ.copy()
        # Ensure we see ALL deprecation and future warnings
        env["PYTHONWARNINGS"] = "always::DeprecationWarning,always::FutureWarning"
        # Ensure local modules are importable during collection
        env["PYTHONPATH"] = str(self.target_dir) + os.pathsep + env.get("PYTHONPATH", "")
        
        try:
            result = subprocess.run(
                command,
                cwd=self.target_dir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                env=env
            )
            
            # Warnings can go to either stdout or stderr depending on 
            # the tool's capture settings (e.g. pytest summary is on stdout)
            findings.extend(self._parse_warnings(result.stdout, source_name))
            findings.extend(self._parse_warnings(result.stderr, source_name))
            return findings
        except Exception:
            return []

    def _parse_warnings(self, output: str, source_name: str) -> List[dict]:
        findings = []
        pattern = re.compile(r"^\s*(.+):(\d+): (\w*Warning): (.*)", re.MULTILINE)
        
        for match in pattern.finditer(output):
            file_path = match.group(1)
            line_no = int(match.group(2))
            category = match.group(3)
            message = match.group(4)
            
            # Clean up absolute path to relative if possible
            try:
                rel_path = str(Path(file_path).relative_to(self.target_dir))
            except ValueError:
                rel_path = file_path

            findings.append({
                "title": f"Runtime {category}: {source_name}",
                "category": "deprecation",
                "severity": "medium" if category == "DeprecationWarning" else "high",
                "summary": message,
                "file_path": rel_path,
                "line_number": line_no,
                "evidence": f"Captured during: {source_name}",
                "suggested_fix": "Review the warning message and upgrade to the recommended API."
            })
            
        # Deduplicate by message and location
        unique_findings = []
        seen = set()
        for f in findings:
            key = (f["file_path"], f["line_number"], f["summary"])
            if key not in seen:
                unique_findings.append(f)
                seen.add(key)
                
        return unique_findings
