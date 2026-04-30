import os
import subprocess
import logging
import uuid
from typing import Tuple
from pathlib import Path

logger = logging.getLogger("QAEngineer")

class QAEngineer:
    """
    Provides Functional Testing and Auto-Healing capabilities (TestSprite DNA).
    """
    def __init__(self, target_dir: str):
        self.target_dir = str(Path(target_dir).resolve())

    def _resolve_target_path(self, file_path: str) -> Path:
        target_root = Path(self.target_dir).resolve()
        resolved = (target_root / file_path).resolve()
        if target_root != resolved and target_root not in resolved.parents:
            raise ValueError("Target file must be inside target_dir.")
        return resolved

    def run_functional_test(self, test_code: str) -> Tuple[bool, str]:
        """
        Writes a pytest script to the target directory, runs it, and returns the output.
        """
        logger.info("Executing functional Pytest script...")
        
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
