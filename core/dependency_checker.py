import sys
import subprocess
import logging
import os
from pathlib import Path
from typing import Dict, List, Tuple, Optional

logger = logging.getLogger("DependencyChecker")

# Candidate venv directory names to search in the target project
_VENV_NAMES = ("venv", ".venv", "env", ".env", "virtualenv")


class DependencyChecker:
    """
    Validates that required packages are installed.
    When given a target_dir, checks the target project's own venv instead
    of Faultline's venv, so dependencies are validated in the right environment.
    """

    TEST_DEPENDENCIES = {
        "api":          ["pytest", "httpx"],
        "auth":         ["pytest", "httpx"],
        "crud":         ["pytest", "httpx"],
        "validation":   ["pytest", "httpx"],
        "idor":         ["pytest", "httpx"],
        "django_model": ["pytest", "pytest-django", "django"],
        "load":         ["locust"],
        "e2e_journey":  ["pytest", "playwright"],
        "e2e_react":    ["pytest", "playwright"],
        "model":        ["pytest", "pytest-django", "django"],
    }

    def __init__(self, target_dir: Optional[str] = None):
        self.target_dir = str(Path(target_dir).resolve()) if target_dir else None
        self.python_executable = self._resolve_python()
        self.venv_path = self._detected_venv
        self.installed_packages = self._get_installed_packages()

    # ── Python resolution ─────────────────────────────────────────────────────

    def _resolve_python(self) -> str:
        """
        Find the best Python executable to use for package checks.
        Priority:
          1. Target project's venv (if target_dir given and venv found)
          2. VIRTUAL_ENV env var (current activated venv)
          3. sys.executable (whatever's running Faultline)
        """
        if self.target_dir:
            python = self._find_target_venv_python(Path(self.target_dir))
            if python:
                logger.info("DependencyChecker using target venv: %s", python)
                self._detected_venv = self._python_to_venv(python)
                return python

        # Fall back: current activated venv or system Python
        venv_env = os.getenv("VIRTUAL_ENV")
        if venv_env:
            python = self._python_in_venv(Path(venv_env))
            if python:
                self._detected_venv = Path(venv_env)
                return python

        if hasattr(sys, "base_prefix") and sys.prefix != sys.base_prefix:
            self._detected_venv = Path(sys.prefix)
        else:
            self._detected_venv = None

        return sys.executable

    def _find_target_venv_python(self, target: Path) -> Optional[str]:
        """Search target directory for a venv and return its Python executable."""
        for name in _VENV_NAMES:
            candidate = target / name
            if candidate.is_dir():
                python = self._python_in_venv(candidate)
                if python:
                    return python
        return None

    @staticmethod
    def _python_in_venv(venv_path: Path) -> Optional[str]:
        """Return the Python executable inside a venv directory, or None."""
        candidates = [
            venv_path / "Scripts" / "python.exe",   # Windows
            venv_path / "bin" / "python3",           # Unix
            venv_path / "bin" / "python",            # Unix fallback
        ]
        for p in candidates:
            if p.is_file():
                return str(p)
        return None

    @staticmethod
    def _python_to_venv(python_path: str) -> Optional[Path]:
        """Derive the venv root from a python executable path."""
        p = Path(python_path)
        # Scripts/python.exe → parent is Scripts → grandparent is venv root
        return p.parent.parent

    # ── Package inspection ────────────────────────────────────────────────────

    def _get_installed_packages(self) -> Dict[str, str]:
        try:
            result = subprocess.run(
                [self.python_executable, "-m", "pip", "list", "--format=json"],
                capture_output=True, text=True, timeout=15,
            )
            if result.returncode == 0:
                import json
                packages_list = json.loads(result.stdout)
                return {pkg["name"].lower(): pkg["version"] for pkg in packages_list}
        except Exception as e:
            logger.warning("Failed to get installed packages: %s", e)
        return {}

    # ── Public API ────────────────────────────────────────────────────────────

    def check_dependencies(
        self,
        test_type: str,
        raise_on_missing: bool = False,
    ) -> Tuple[bool, List[str], List[str]]:
        required = self.TEST_DEPENDENCIES.get(test_type, [])
        missing, installed = [], []
        for package in required:
            key = package.lower()
            if key in self.installed_packages:
                installed.append(f"{package}=={self.installed_packages[key]}")
            else:
                missing.append(package)

        all_ok = not missing
        if not all_ok and raise_on_missing:
            raise RuntimeError(
                f"Missing dependencies for {test_type}: {', '.join(missing)}\n"
                f"Install with: pip install {' '.join(missing)}"
            )
        return all_ok, installed, missing

    def check_all_dependencies(self) -> Dict[str, Tuple[bool, List[str], List[str]]]:
        return {t: self.check_dependencies(t) for t in self.TEST_DEPENDENCIES}

    def get_venv_info(self) -> str:
        lines = [
            f"Python Executable: {self.python_executable}",
            f"Python Version: {sys.version.split()[0]}",
        ]
        if self.target_dir:
            lines.append(f"Target Project:    {self.target_dir}")
        if self.venv_path:
            lines.append(f"Virtual Environment: {self.venv_path}")
        else:
            lines.append("Virtual Environment: Not detected (system Python)")
        lines.append(f"Total Packages Installed: {len(self.installed_packages)}")
        return "\n".join(lines)

    def get_installation_command(self, test_type: str) -> str:
        _, _, missing = self.check_dependencies(test_type)
        if not missing:
            return f"All dependencies for '{test_type}' are already installed."
        return f"pip install {' '.join(missing)}"

    def validate_and_report(self, test_type: str) -> Tuple[bool, str]:
        all_ok, installed, missing = self.check_dependencies(test_type)
        lines = [
            f"Dependency Check for '{test_type}':",
            "-" * 60,
            f"Environment: {self.venv_path or 'System Python'}",
            f"Python:      {self.python_executable}",
            "",
        ]
        if all_ok:
            lines.append("[OK] All dependencies installed:")
            lines.extend(f"  + {p}" for p in installed)
        else:
            lines.append("[OK] Installed:")
            lines.extend(f"  + {p}" for p in installed)
            lines.append("\n[MISSING] Not installed:")
            lines.extend(f"  - {p}" for p in missing)
            lines.append(f"\nFix: {self.get_installation_command(test_type)}")
        return all_ok, "\n".join(lines)


if __name__ == "__main__":
    checker = DependencyChecker()
    print("\n" + "=" * 70)
    print("FAULTLINE ENVIRONMENT CHECK")
    print("=" * 70 + "\n")
    print(checker.get_venv_info())
    print("\n" + "=" * 70)
    print("TEST TEMPLATE DEPENDENCIES")
    print("=" * 70 + "\n")
    for test_type in sorted(checker.TEST_DEPENDENCIES):
        is_ok, installed, missing = checker.check_dependencies(test_type)
        status = "[OK]     " if is_ok else "[MISSING]"
        print(f"{status} {test_type:20} — {len(installed):2} OK, {len(missing):2} missing")
        if missing:
            print(f"          Install: pip install {' '.join(missing)}")
    print("\n" + "=" * 70 + "\n")
