from pathlib import Path
from typing import Dict, List


SKIPPED_DIRS = {
    ".git",
    ".hg",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    ".aegis_patches",
    "venv",
    ".venv",
    "env",
    "node_modules",
}

MAX_FILE_BYTES = 128_000


class ProjectFileReader:
    """Safe project-local file discovery and reading for agent-first workflows."""

    def __init__(self, target_dir: str):
        self.target_dir = Path(target_dir).expanduser().resolve()

    def _resolve(self, relative_path: str) -> Path:
        resolved = (self.target_dir / relative_path).resolve()
        if self.target_dir != resolved and self.target_dir not in resolved.parents:
            raise ValueError("Path must stay inside target_dir.")
        if any(part in SKIPPED_DIRS for part in resolved.relative_to(self.target_dir).parts):
            raise ValueError("Path is inside a skipped directory.")
        return resolved

    def list_files(self, glob: str = "**/*.py", limit: int = 250) -> List[str]:
        files: List[str] = []
        for path in self.target_dir.glob(glob):
            if not path.is_file():
                continue
            rel = path.relative_to(self.target_dir)
            if any(part in SKIPPED_DIRS for part in rel.parts):
                continue
            files.append(str(rel))
            if len(files) >= limit:
                break
        return files

    def read_file(self, relative_path: str, start_line: int = 1, max_lines: int = 240) -> Dict:
        path = self._resolve(relative_path)
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(relative_path)
        if path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"File is too large to read safely ({path.stat().st_size} bytes).")

        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        start = max(start_line, 1) - 1
        end = min(start + max_lines, len(lines))
        return {
            "path": str(path.relative_to(self.target_dir)),
            "start_line": start + 1,
            "end_line": end,
            "total_lines": len(lines),
            "content": "\n".join(lines[start:end]),
        }
