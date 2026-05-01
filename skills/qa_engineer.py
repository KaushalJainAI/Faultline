import os
import subprocess
import logging
import uuid
from typing import Tuple, Optional
from pathlib import Path

logger = logging.getLogger("QAEngineer")

class QAEngineer:
    """
    Provides Functional Testing and Auto-Healing capabilities (TestSprite DNA).
    Includes dependency checking before test execution.
    """
    def __init__(self, target_dir: str):
        self.target_dir = str(Path(target_dir).resolve())
        self.dependency_checker = self._init_dependency_checker()

    def _init_dependency_checker(self):
        """Initialize dependency checker using the target project's own venv."""
        try:
            from core.dependency_checker import DependencyChecker
            return DependencyChecker(target_dir=self.target_dir)
        except ImportError:
            logger.warning("DependencyChecker not available, skipping dependency checks")
            return None

    def _resolve_target_path(self, file_path: str) -> Path:
        target_root = Path(self.target_dir).resolve()
        resolved = (target_root / file_path).resolve()
        if target_root != resolved and target_root not in resolved.parents:
            raise ValueError("Target file must be inside target_dir.")
        return resolved

    def check_test_dependencies(self, test_type: str) -> Tuple[bool, str]:
        """
        Check if dependencies are installed for a test type.

        Args:
            test_type: 'api', 'auth', 'crud', 'django_model', 'load', 'e2e_*', etc.

        Returns:
            Tuple of (dependencies_ok: bool, report: str)
        """
        if not self.dependency_checker:
            return True, "Dependency checking disabled (DependencyChecker not available)"

        is_valid, report = self.dependency_checker.validate_and_report(test_type)
        return is_valid, report

    def run_functional_test(self, test_code: str, test_type: str = "api") -> Tuple[bool, str]:
        """
        Writes a pytest script to the target directory, runs it, and returns the output.
        Validates dependencies before execution.

        Args:
            test_code: The test code to execute
            test_type: Type of test ('api', 'auth', 'crud', 'django_model', 'load', 'e2e_*')

        Returns:
            Tuple of (passed: bool, output: str)
        """
        logger.info("Executing functional Pytest script (type=%s)...", test_type)

        # Check dependencies first
        deps_ok, deps_report = self.check_test_dependencies(test_type)
        if not deps_ok:
            logger.warning("Missing dependencies for %s test:\n%s", test_type, deps_report)
            return False, f"DEPENDENCY CHECK FAILED:\n\n{deps_report}"

        # Create a temporary test file in the target directory
        test_filename = f"test_aegis_generated_{uuid.uuid4().hex[:8]}.py"
        test_filepath = str(self._resolve_target_path(test_filename))

        try:
            with open(test_filepath, "w", encoding="utf-8") as f:
                f.write(test_code)

            # Run pytest on the generated file
            result = subprocess.run(
                ["pytest", test_filename, "-v", "--tb=short"],
                cwd=self.target_dir,
                capture_output=True,
                text=True,
                timeout=30 # 30 seconds max per test
            )

            passed = result.returncode == 0
            output = result.stdout if passed else result.stdout + "\n" + result.stderr
            return passed, output

        except subprocess.TimeoutExpired:
            return False, "Test execution timed out after 30 seconds."
        except Exception as e:
            return False, f"Failed to execute test: {str(e)}"
        finally:
            if os.path.exists(test_filepath):
                os.remove(test_filepath)

    def propose_code_patch(self, file_path: str, proposed_code: str) -> str:
        """
        Creates a 'proposed fix' file without immediately overwriting the target.
        This allows the developer to review the patch before applying.
        """
        logger.info(f"Generating proposed patch for {file_path}")
        
        # Ensure the file exists
        try:
            full_target_path = self._resolve_target_path(file_path)
        except ValueError as e:
            return f"Error: {e}"
        if not os.path.exists(full_target_path):
            return f"Error: Target file {file_path} does not exist."
            
        patch_dir = os.path.join(self.target_dir, ".aegis_patches")
        os.makedirs(patch_dir, exist_ok=True)
        
        patch_filename = f"{os.path.basename(file_path)}_fix_{uuid.uuid4().hex[:6]}.py"
        patch_filepath = os.path.join(patch_dir, patch_filename)
        
        try:
            with open(patch_filepath, "w", encoding="utf-8") as f:
                f.write(proposed_code)
            
            return f"Patch successfully generated and saved to .aegis_patches/{patch_filename}. Please review and apply."
        except Exception as e:
            return f"Failed to generate patch: {str(e)}"
