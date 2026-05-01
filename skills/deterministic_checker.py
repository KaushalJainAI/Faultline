import ast
import importlib.util
import json
import os
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from skills.ast_grapher import ASTGrapher, SKIPPED_DIRS


@dataclass
class CheckFinding:
    title: str
    category: str
    severity: str
    summary: str
    evidence: str = ""
    file_path: str = ""
    line_number: Optional[int] = None
    suggested_fix: str = ""


class DeterministicChecker:
    """Programmatic checks that should run before LLM-heavy analysis."""

    def __init__(self, target_dir: str, timeout: int = 60):
        self.target_dir = Path(target_dir).expanduser().resolve()
        self.timeout = timeout

    def run_all(self) -> Dict:
        findings: List[CheckFinding] = []
        findings.extend(self.check_syntax())
        findings.extend(self.check_imports())
        findings.extend(self.check_static_runtime_hazards())
        findings.extend(self.run_ruff())
        findings.extend(self.run_pip_check())
        findings.extend(self.run_pytest_collect())

        # Build the AST graph once; reuse for both call-sig checks and dep analysis
        graph = ASTGrapher(self.target_dir).analyze_project()
        findings.extend(self.check_call_signatures(graph))
        root_causes = self.analyze_dependency_failures(graph, findings)

        return {
            "summary": {
                "target_dir": str(self.target_dir),
                "total_findings": len(findings),
                "high_or_critical": sum(1 for f in findings if f.severity in {"high", "critical"}),
            },
            "findings": [asdict(f) for f in findings],
            "dependency_root_causes": root_causes,
            "serializer_schemas": graph.get("serializer_schemas", []),
        }

    def _python_files(self) -> List[Path]:
        files = []
        for path in self.target_dir.rglob("*.py"):
            rel_parts = path.relative_to(self.target_dir).parts
            if any(part in SKIPPED_DIRS for part in rel_parts):
                continue
            files.append(path)
        return files

    def check_syntax(self) -> List[CheckFinding]:
        findings = []
        for path in self._python_files():
            try:
                ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
            except SyntaxError as e:
                findings.append(CheckFinding(
                    title="Python syntax error",
                    category="syntax",
                    severity="critical",
                    summary=e.msg,
                    evidence=str(e),
                    file_path=str(path.relative_to(self.target_dir)),
                    line_number=e.lineno,
                    suggested_fix="Fix the syntax error before running runtime or agentic checks.",
                ))
        return findings

    def check_imports(self) -> List[CheckFinding]:
        findings = []
        local_modules = self._local_module_names()
        for path in self._python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                module_name = None
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        module_name = alias.name.split(".")[0]
                        self._append_missing_import(findings, module_name, local_modules, path, node.lineno)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    if node.level:
                        continue
                    module_name = node.module.split(".")[0]
                    self._append_missing_import(findings, module_name, local_modules, path, node.lineno)
        return findings

    def _append_missing_import(self, findings, module_name, local_modules, path, lineno):
        if module_name in local_modules or module_name in sys.builtin_module_names:
            return
        try:
            exists = importlib.util.find_spec(module_name) is not None
        except Exception:
            exists = False
        if not exists:
            findings.append(CheckFinding(
                title=f"Missing import: {module_name}",
                category="runtime",
                severity="high",
                summary=f"`{module_name}` could not be resolved as a local, built-in, or installed module.",
                file_path=str(path.relative_to(self.target_dir)),
                line_number=lineno,
                suggested_fix="Install the dependency, fix requirements, or correct the import path.",
            ))

    def _local_module_names(self) -> set:
        names = set()
        for path in self._python_files():
            rel = path.relative_to(self.target_dir)
            names.add(rel.parts[0].replace(".py", ""))
        return names

    def check_static_runtime_hazards(self) -> List[CheckFinding]:
        findings = []
        for path in self._python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"), filename=str(path))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)):
                    if isinstance(node.right, ast.Constant) and node.right.value == 0:
                        findings.append(CheckFinding(
                            title="Definite division by zero",
                            category="runtime",
                            severity="high",
                            summary="A division/modulo operation uses literal zero as the denominator.",
                            file_path=str(path.relative_to(self.target_dir)),
                            line_number=node.lineno,
                            suggested_fix="Guard the denominator or remove the impossible arithmetic path.",
                        ))
        return findings

    def run_ruff(self) -> List[CheckFinding]:
        ruff = self._find_executable("ruff")
        if not ruff:
            return [CheckFinding(
                title="Ruff unavailable",
                category="runtime",
                severity="low",
                summary="Ruff is not installed, so deterministic lint checks were skipped.",
                suggested_fix="Install ruff in the target environment.",
            )]
        result = self._run([ruff, "check", ".", "--output-format=json"])
        if result.returncode == 0:
            return []
        try:
            issues = json.loads(result.stdout or "[]")
        except json.JSONDecodeError:
            return [self._command_failure("Ruff failed", result, "runtime")]
        findings = []
        for issue in issues:
            findings.append(CheckFinding(
                title=f"Ruff {issue.get('code')}: {issue.get('message')}",
                category="runtime",
                severity="medium",
                summary=issue.get("message", "Ruff reported an issue."),
                evidence=json.dumps(issue, indent=2),
                file_path=issue.get("filename", "").replace(str(self.target_dir) + os.sep, ""),
                line_number=(issue.get("location") or {}).get("row"),
                suggested_fix="Apply the Ruff recommendation or suppress it with a clear project-local reason.",
            ))
        return findings

    def run_pip_check(self) -> List[CheckFinding]:
        # Only run pip check when the target looks like a Python project with a
        # dependency manifest, and prefer its own venv interpreter so we report
        # on the target's environment rather than Faultline's.
        manifests = ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "setup.cfg")
        if not any((self.target_dir / m).exists() for m in manifests):
            return []
        python = self._target_python() or sys.executable
        result = self._run([python, "-m", "pip", "check"])
        if result.returncode == 0:
            return []
        return [CheckFinding(
            title="Dependency conflict detected",
            category="runtime",
            severity="high",
            summary="`pip check` reported incompatible or missing installed dependencies.",
            evidence=result.stdout + result.stderr,
            suggested_fix="Resolve package versions in requirements/lock files and reinstall dependencies.",
        )]

    def run_pytest_collect(self) -> List[CheckFinding]:
        if not (self.target_dir / "pytest.ini").exists() and not any(self.target_dir.rglob("test*.py")):
            return []
        result = self._run([sys.executable, "-m", "pytest", "--collect-only", "-q"])
        if result.returncode == 0:
            return []
        return [CheckFinding(
            title="Pytest collection failed",
            category="runtime",
            severity="high",
            summary="Tests could not be collected, usually because imports or test module setup failed.",
            evidence=(result.stdout + "\n" + result.stderr)[-8000:],
            suggested_fix="Fix import-time failures and test configuration before generated API tests run.",
        )]

    def check_call_signatures(self, graph: Dict) -> List[CheckFinding]:
        """
        Validate every resolved call-site against the callee's signature.
        Flags: too few required arguments, too many positional arguments.
        Skips calls that use *args / **kwargs unpacking (can't be checked statically).
        """
        signatures = graph.get("signatures", {})
        findings: List[CheckFinding] = []
        seen: set = set()

        for edge in graph.get("call_edges", []):
            info = edge.get("call_info")
            if not info:
                continue
            # Skip star-unpack calls — we can't know actual arg count
            if info.get("has_star") or info.get("has_dstar"):
                continue

            target_id = edge["target"]
            sig = signatures.get(target_id)
            if not sig:
                continue
            # Skip variadic functions (accept any number of args)
            if sig.get("has_var_positional") or sig.get("has_var_keyword"):
                continue

            pos_args  = info.get("pos_args", 0)
            kw_names  = set(info.get("kw_names", []))
            min_pos   = sig.get("min_positional", 0)
            max_pos   = sig.get("max_positional", 0)
            file_path = info.get("file", "")
            lineno    = info.get("lineno")

            # How many required positional params are still unmet after keywords cover them
            required_params = [
                p for p in sig.get("params", [])
                if p.get("required") and not p.get("kwonly") and p["name"] not in kw_names
            ]
            unmet = max(0, len(required_params) - pos_args)

            func_label = target_id.split(":")[-1]

            if unmet > 0:
                key = (target_id, file_path, lineno, "few")
                if key not in seen:
                    seen.add(key)
                    required_names = ", ".join(
                        p["name"] for p in sig["params"] if p.get("required")
                    )
                    findings.append(CheckFinding(
                        title=f"Too few arguments: {func_label}()",
                        category="api",
                        severity="high",
                        summary=(
                            f"Called with {pos_args} positional arg(s) but "
                            f"{min_pos} required. Missing: {required_names}."
                        ),
                        evidence=f"Signature: {sig.get('sig_str', '')}",
                        file_path=file_path,
                        line_number=lineno,
                        suggested_fix=(
                            f"Pass all required args: {required_names}."
                        ),
                    ))

            elif pos_args > max_pos:
                key = (target_id, file_path, lineno, "many")
                if key not in seen:
                    seen.add(key)
                    findings.append(CheckFinding(
                        title=f"Too many arguments: {func_label}()",
                        category="api",
                        severity="medium",
                        summary=(
                            f"Called with {pos_args} positional arg(s) but "
                            f"function accepts at most {max_pos}."
                        ),
                        evidence=f"Signature: {sig.get('sig_str', '')}",
                        file_path=file_path,
                        line_number=lineno,
                        suggested_fix="Remove extra arguments or check if the function signature changed.",
                    ))

        return findings

    def analyze_dependency_failures(self, graph: Dict, findings: List[CheckFinding]) -> List[Dict]:
        failed_files = {f.file_path for f in findings if f.file_path}
        dependents: Dict[str, List[str]] = {}
        for source, target in graph.get("dependencies", []):
            dependents.setdefault(target, []).append(source)

        results = []
        for failed in failed_files:
            impacted = set()
            stack = list(dependents.get(failed, []))
            while stack:
                current = stack.pop()
                if current in impacted:
                    continue
                impacted.add(current)
                stack.extend(dependents.get(current, []))
            if impacted:
                results.append({
                    "root_file": failed,
                    "impacted_files": sorted(impacted),
                    "impact_count": len(impacted),
                })
        return sorted(results, key=lambda item: item["impact_count"], reverse=True)

    def _find_executable(self, name: str) -> Optional[str]:
        local = self.target_dir / "venv" / ("Scripts" if os.name == "nt" else "bin") / (name + (".exe" if os.name == "nt" else ""))
        if local.exists():
            return str(local)
        return name

    def _target_python(self) -> Optional[str]:
        for venv_dir in ("venv", ".venv"):
            python = self.target_dir / venv_dir / ("Scripts" if os.name == "nt" else "bin") / ("python.exe" if os.name == "nt" else "python")
            if python.exists():
                return str(python)
        return None

    def _run(self, command: List[str]) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(command, cwd=self.target_dir, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=self.timeout)
        except FileNotFoundError as e:
            return subprocess.CompletedProcess(command, 127, "", str(e))
        except subprocess.TimeoutExpired as e:
            return subprocess.CompletedProcess(command, 124, e.stdout or "", e.stderr or "Command timed out.")

    def _command_failure(self, title: str, result: subprocess.CompletedProcess, category: str) -> CheckFinding:
        return CheckFinding(
            title=title,
            category=category,
            severity="medium",
            summary=f"Command failed: {' '.join(result.args)}",
            evidence=(result.stdout or "") + (result.stderr or ""),
        )
