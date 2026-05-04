import ast
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, List, Optional

from skills.ast_grapher import ASTGrapher, SKIPPED_DIRS
from skills.deprecation_guard import DeprecationGuard
from skills.container_grapher import ContainerGrapher


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
        self.target_python = self._target_python() or sys.executable
        self.target_modules, self.target_builtins = self._get_target_env_info()

    def run_all(self) -> Dict:
        findings: List[CheckFinding] = []
        findings.extend(self.check_syntax())
        findings.extend(self.check_imports())
        findings.extend(self.check_static_runtime_hazards())
        findings.extend(self.run_ruff())
        findings.extend(self.run_bandit())
        findings.extend(self.run_semgrep())
        findings.extend(self.run_pip_audit())
        findings.extend(self.run_gitleaks())
        findings.extend(self.run_django_deploy_check())
        findings.extend(self.run_pip_check())
        findings.extend(self.run_pytest_collect())
        
        # Level 2 Deprecation Guard (Runtime)
        dep_guard = DeprecationGuard(self.target_dir, self.target_python, self.timeout)
        runtime_deps = dep_guard.check_runtime_deprecations()
        for d in runtime_deps:
            findings.append(CheckFinding(**d))

        # Build the AST graph once; reuse for both call-sig checks and modularity
        graph = ASTGrapher(self.target_dir).analyze_project()
        findings.extend(self.check_call_signatures(graph))
        
        # Modularity Assessment
        mod_assessor = ContainerGrapher(self.target_dir)
        mod_report = mod_assessor.analyze_modularity(graph)
        findings.extend(self.check_modularity_violations(mod_report))
        
        root_causes = self.analyze_dependency_failures(graph, findings)

        return {
            "summary": {
                "target_dir": str(self.target_dir),
                "total_findings": len(findings),
                "high_or_critical": sum(1 for f in findings if f.severity in {"high", "critical"}),
                "modularity_score": self._calculate_overall_modularity(mod_report),
            },
            "findings": [asdict(f) for f in findings],
            "dependency_root_causes": root_causes,
            "modularity_report": mod_report,
            "serializer_schemas": graph.get("serializer_schemas", []),
        }

    # Hard cap: ignore targets with unreasonably many Python files (e.g. vendored
    # runtimes or WASM stdlib bundles).  Configurable via FAULTLINE_MAX_PY_FILES.
    MAX_PY_FILES: int = int(os.environ.get("FAULTLINE_MAX_PY_FILES", "1000"))

    def _python_files(self) -> List[Path]:
        files = []
        for path in self.target_dir.rglob("*.py"):
            rel_parts = path.relative_to(self.target_dir).parts
            if any(part in SKIPPED_DIRS for part in rel_parts):
                continue
            files.append(path)
            if len(files) >= self.MAX_PY_FILES:
                break
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

    def _get_target_env_info(self) -> (set, set):
        """Discovers available modules and builtins in the target project's environment."""
        script = (
            "import pkgutil, sys, json; "
            "print(json.dumps({"
            "\"modules\": [m.name for m in pkgutil.iter_modules()],"
            "\"builtins\": list(sys.builtin_module_names)"
            "}))"
        )
        result = self._run([self.target_python, "-c", script])
        if result.returncode == 0:
            try:
                data = json.loads(result.stdout)
                return set(data.get("modules", [])), set(data.get("builtins", []))
            except:
                pass
        return set(), set(sys.builtin_module_names)

    def _append_missing_import(self, findings, module_name, local_modules, path, lineno):
        if (module_name in local_modules or 
            module_name in self.target_builtins or 
            module_name in self.target_modules):
            return
        
        # Final fallback: check if it's importable in the current process (unlikely if not in target)
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
        result = self._run([ruff, "check", ".", "--extend-select=UP", "--output-format=json"])
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

    def run_bandit(self) -> List[CheckFinding]:
        """Run bandit SAST scanner for common Python security issues."""
        bandit = self._find_executable("bandit")
        result = self._run([bandit, "-r", ".", "-f", "json", "-q", "-ll"])
        if result.returncode == 127:
            return []  # not installed — not a blocker
        if result.returncode not in (0, 1):
            return [self._command_failure("Bandit scan failed", result, "security_candidate")]
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return []
        _sev_map = {"HIGH": "high", "MEDIUM": "medium", "LOW": "low"}
        findings = []
        for issue in data.get("results", []):
            sev = _sev_map.get(issue.get("issue_severity", "").upper(), "medium")
            fp = issue.get("filename", "")
            try:
                fp = str(Path(fp).relative_to(self.target_dir))
            except ValueError:
                pass
            findings.append(CheckFinding(
                title=f"Bandit {issue.get('test_id', '')}: {issue.get('test_name', '')}",
                category="security_candidate",
                severity=sev,
                summary=issue.get("issue_text", ""),
                evidence=json.dumps(issue, indent=2),
                file_path=fp,
                line_number=issue.get("line_number"),
                suggested_fix=(
                    f"See CWE-{issue.get('issue_cwe', {}).get('id', '?')} and "
                    f"https://bandit.readthedocs.io/en/latest/plugins/{issue.get('test_id','').lower()}.html"
                ),
            ))
        return findings

    def run_semgrep(self) -> List[CheckFinding]:
        """Run semgrep with django + python rule packs for semantic security issues."""
        semgrep = self._find_executable("semgrep")
        result = self._run([
            semgrep, "--config", "p/django", "--config", "p/python",
            "--json", "--quiet", "--no-rewrite-rule-ids", ".",
        ])
        if result.returncode == 127:
            return []
        if result.returncode not in (0, 1):
            return [self._command_failure("Semgrep scan failed", result, "security_candidate")]
        try:
            data = json.loads(result.stdout or "{}")
        except json.JSONDecodeError:
            return []
        _sev_map = {"ERROR": "high", "WARNING": "medium", "INFO": "low"}
        findings = []
        for issue in data.get("results", []):
            extra = issue.get("extra", {})
            sev = _sev_map.get((extra.get("severity") or "WARNING").upper(), "medium")
            fp = issue.get("path", "")
            try:
                fp = str(Path(fp).relative_to(self.target_dir))
            except ValueError:
                pass
            findings.append(CheckFinding(
                title=f"Semgrep {issue.get('check_id', '')}",
                category="security_candidate",
                severity=sev,
                summary=extra.get("message", ""),
                evidence=json.dumps(issue, indent=2),
                file_path=fp,
                line_number=(issue.get("start") or {}).get("line"),
                suggested_fix=extra.get("metadata", {}).get("fix", "Review the semgrep rule documentation."),
            ))
        return findings

    def run_pip_audit(self) -> List[CheckFinding]:
        """Scan installed dependencies for known CVEs via pip-audit (PyPA advisory DB)."""
        manifests = ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "setup.cfg")
        if not any((self.target_dir / m).exists() for m in manifests):
            return []
        result = self._run([self.target_python, "-m", "pip_audit", "--format", "json", "--skip-editable"])
        if result.returncode == 127:
            # pip_audit not available — try the standalone executable
            result = self._run([self._find_executable("pip-audit"), "--format", "json", "--skip-editable"])
        if result.returncode == 127:
            return []
        try:
            data = json.loads(result.stdout or "[]")
            # pip-audit returns either a list or {"dependencies": [...]}
            deps = data if isinstance(data, list) else data.get("dependencies", [])
        except json.JSONDecodeError:
            return []
        findings = []
        for dep in deps:
            for vuln in dep.get("vulns", []):
                fix_versions = vuln.get("fix_versions", [])
                sev = "high" if fix_versions else "medium"
                findings.append(CheckFinding(
                    title=f"CVE {vuln.get('id', '?')} in {dep.get('name', '?')}=={dep.get('version', '?')}",
                    category="security_candidate",
                    severity=sev,
                    summary=vuln.get("description", "Known vulnerability in dependency."),
                    evidence=json.dumps(vuln, indent=2),
                    suggested_fix=(
                        f"Upgrade {dep.get('name')} to {fix_versions[0]}" if fix_versions
                        else f"No fix available yet for {vuln.get('id')}. Monitor advisories."
                    ),
                ))
        return findings

    def run_gitleaks(self) -> List[CheckFinding]:
        """Scan for hardcoded secrets using gitleaks (binary, not pip-installable)."""
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
            tmp_path = tmp.name
        try:
            result = self._run([
                "gitleaks", "detect", "--source", ".", "--report-format", "json",
                "--report-path", tmp_path, "--no-git", "--exit-code", "0",
            ])
            if result.returncode == 127:
                return []  # gitleaks not installed
            try:
                raw = Path(tmp_path).read_text(encoding="utf-8", errors="replace")
                leaks = json.loads(raw or "[]") or []
            except Exception:
                return []
            findings = []
            for leak in leaks:
                fp = leak.get("File", "")
                try:
                    fp = str(Path(fp).relative_to(self.target_dir))
                except ValueError:
                    pass
                secret_snip = (leak.get("Secret") or "")[:8] + "..." if leak.get("Secret") else "?"
                findings.append(CheckFinding(
                    title=f"Secret detected [{leak.get('RuleID', '?')}]: {leak.get('Description', '')}",
                    category="security_candidate",
                    severity="high",
                    summary=f"Possible secret in {fp} — value starts with: {secret_snip}",
                    evidence=json.dumps({k: v for k, v in leak.items() if k != "Secret"}, indent=2),
                    file_path=fp,
                    line_number=leak.get("StartLine"),
                    suggested_fix=(
                        "Remove the secret from source code immediately. "
                        "Rotate the credential, add the file to .gitignore, "
                        "and use environment variables or a secrets manager instead."
                    ),
                ))
            return findings
        finally:
            try:
                Path(tmp_path).unlink(missing_ok=True)
            except Exception:
                pass

    def run_django_deploy_check(self) -> List[CheckFinding]:
        """Run `manage.py check --deploy` to surface Django deployment security issues."""
        manage = self.target_dir / "manage.py"
        if not manage.exists():
            return []
        result = self._run([self.target_python, "manage.py", "check", "--deploy"])
        combined = (result.stdout or "") + (result.stderr or "")
        if not combined.strip():
            return []
        findings = []
        for line in combined.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("ERRORS:") or ": (security." in line and "ERROR" in line:
                sev = "high"
            elif line.startswith("WARNINGS:") or ": (security." in line:
                sev = "medium"
            elif "(security." in line:
                sev = "medium"
            else:
                continue
            findings.append(CheckFinding(
                title="Django deploy check: security misconfiguration",
                category="security_candidate",
                severity=sev,
                summary=line[:300],
                evidence=combined[:800],
                suggested_fix=(
                    "Run `python manage.py check --deploy` locally and address each "
                    "HTTPS, HSTS, CSRF, SECRET_KEY, and cookie security warning."
                ),
            ))
        if not findings and result.returncode != 0:
            findings.append(CheckFinding(
                title="Django deploy check failed",
                category="security_candidate",
                severity="medium",
                summary="`manage.py check --deploy` returned a non-zero exit code.",
                evidence=combined[:800],
            ))
        return findings

    def run_pip_check(self) -> List[CheckFinding]:
        # Only run pip check when the target looks like a Python project with a
        # dependency manifest, and prefer its own venv interpreter so we report
        # on the target's environment rather than Faultline's.
        manifests = ("requirements.txt", "pyproject.toml", "Pipfile", "setup.py", "setup.cfg")
        if not any((self.target_dir / m).exists() for m in manifests):
            return []
        result = self._run([self.target_python, "-m", "pip", "check"])
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
        # -p no:django: prevents pytest-django from picking up Faultline's inherited
        # DJANGO_SETTINGS_MODULE and failing when the target has a different Django setup.
        result = self._run([
            self.target_python, "-m", "pytest", "--collect-only", "-q", "-p", "no:django",
        ])
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
        # Strip Faultline's own Django env from subprocesses so the target project's
        # pytest/ruff/pip commands aren't confused by the wrong DJANGO_SETTINGS_MODULE.
        env = os.environ.copy()
        env.pop("DJANGO_SETTINGS_MODULE", None)
        env.pop("DJANGO_CONFIGURATION", None)
        try:
            return subprocess.run(
                command, cwd=self.target_dir, capture_output=True,
                text=True, encoding="utf-8", errors="replace",
                timeout=self.timeout, env=env,
            )
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
    def check_modularity_violations(self, mod_report: Dict) -> List[CheckFinding]:
        """Detects circular dependencies between containers and 'Wrong' status modules."""
        findings = []
        edges = mod_report.get("edges", [])
        adj = {}
        for edge in edges:
            adj.setdefault(edge["source"], set()).add(edge["target"])

        # Simple DFS for circularity
        def find_cycle(node, visited, stack, path):
            visited.add(node)
            stack.add(node)
            path.append(node)
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    res = find_cycle(neighbor, visited, stack, path)
                    if res: return res
                elif neighbor in stack:
                    idx = path.index(neighbor)
                    return path[idx:] + [neighbor]
            stack.remove(node)
            path.pop()
            return None

        visited = set()
        for container in mod_report.get("containers", {}):
            if container not in visited:
                cycle = find_cycle(container, visited, set(), [])
                if cycle:
                    cycle_str = " -> ".join(cycle)
                    findings.append(CheckFinding(
                        title="Circular Container Dependency",
                        category="semantic",
                        severity="medium",
                        summary=f"A circular dependency exists between containers: {cycle_str}",
                        evidence=f"Path: {cycle_str}",
                        suggested_fix="Refactor the interfaces to ensure a one-way flow of control between modules."
                    ))

        # Flag 'Wrong' status modules
        for cid, data in mod_report.get("containers", {}).items():
            if "Wrong" in data.get("status", ""):
                findings.append(CheckFinding(
                    title=f"Poor Modularity: {cid}",
                    category="semantic",
                    severity="low",
                    summary=f"Container '{cid}' is heavily entangled with other modules.",
                    evidence=f"Independence Score: {data['metrics'].get('independence_score', 0)}%",
                    suggested_fix="Reduce external dependencies and ensure the module has a clear, single responsibility."
                ))
        return findings

    def _calculate_overall_modularity(self, mod_report: Dict) -> int:
        scores = [data["metrics"]["independence_score"] for data in mod_report.get("containers", {}).values()]
        if not scores: return 100
        return int(sum(scores) / len(scores))
