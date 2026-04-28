import importlib.util
import ast
import subprocess
import os
from pathlib import Path

class GuardrailValidator:
    def __init__(self, target_dir):
        self.target_dir = target_dir

    def check_imports(self, code_string):
        """Extracts imports from the code string and verifies they exist."""
        try:
            tree = ast.parse(code_string)
        except SyntaxError as e:
            return False, f"Syntax error in generated code: {e}"

        missing_modules = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if not self._module_exists(alias.name):
                        missing_modules.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and not self._module_exists(node.module):
                    missing_modules.append(node.module)

        if missing_modules:
            return False, f"Hallucinated or missing modules: {', '.join(missing_modules)}"
        return True, "Imports verified."

    def _module_exists(self, module_name):
        """Checks if a module can be imported."""
        # Split to handle things like 'django.db.models'
        base_module = module_name.split('.')[0]
        local_module = Path(self.target_dir, f"{base_module}.py")
        local_package = Path(self.target_dir, base_module, "__init__.py")
        if local_module.exists() or local_package.exists():
            return True
        try:
            return importlib.util.find_spec(base_module) is not None
        except Exception:
            return False

    def lint_code(self, file_path):
        """Runs ruff to catch obvious logic or syntax errors."""
        try:
            ruff_cmd = "ruff"
            local_ruff = Path(self.target_dir) / "venv" / ("Scripts" if os.name == "nt" else "bin") / ("ruff.exe" if os.name == "nt" else "ruff")
            if local_ruff.exists():
                ruff_cmd = str(local_ruff)
            result = subprocess.run(
                [ruff_cmd, "check", file_path],
                capture_output=True,
                text=True,
                cwd=self.target_dir
            )
            if result.returncode != 0:
                return False, result.stdout or result.stderr
            return True, "Linting passed."
        except FileNotFoundError:
            return False, "Ruff is not installed or not in PATH."

    def _is_safe_path(self, target_path: str) -> bool:
        """
        Check if path contains sensitive directories or files.
        Mirroring AIAAS ToolExecutor._is_safe_path logic.
        """
        path_obj = Path(target_path)
        sensitive_names = {'.env', '.git', '.ssh', '.aws', 'secrets', 'credentials.json', 'db.sqlite3', 'venv'}
        for part in path_obj.parts:
            if part in sensitive_names or part.endswith('.pem') or part.endswith('.key'):
                return False
        return True

    def validate_code(self, code_string, file_path=None):
        """Comprehensive validation of AI-generated code."""
        # Check Path Safety first
        if file_path and not self._is_safe_path(file_path):
            return False, "Security Violation: Attempted to access or modify a restricted path."
        
        # 1. Check Imports & Syntax
        is_valid, msg = self.check_imports(code_string)
        if not is_valid:
            return False, msg

        # 2. Check Linting (if written to a file)
        if file_path and os.path.exists(file_path):
            is_valid, msg = self.lint_code(file_path)
            if not is_valid:
                return False, msg

        return True, "Code passed all guardrails."
