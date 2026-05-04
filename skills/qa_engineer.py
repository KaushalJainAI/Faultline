import json
import logging
import os
import re
import subprocess
import time
import uuid
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional, Tuple

logger = logging.getLogger("QAEngineer")

_SAD_PATTERNS = re.compile(
    r"(invalid|fail|error|missing|unauthorized|forbidden|denied|"
    r"reject|bad_request|400|401|403|404|422|500|negative|sad)",
    re.IGNORECASE,
)


def _infer_case_kind(test_code: str) -> str:
    """Heuristic: scan the test source for sad-path keywords."""
    return "sad" if _SAD_PATTERNS.search(test_code) else "happy"


class QAEngineer:
    """
    Provides Functional Testing and Auto-Healing capabilities (TestSprite DNA).
    Includes dependency checking before test execution.
    """

    _ledger_lock = Lock()  # class-level: serialise writes to generated_tests.json

    def __init__(self, target_dir: str, run_folder: Optional[str] = None):
        self.target_dir = str(Path(target_dir).resolve())
        self.run_folder = Path(run_folder).resolve() if run_folder else None
        self.dependency_checker = self._init_dependency_checker()

    def _init_dependency_checker(self):
        """Initialize dependency checker using the target project's own venv."""
        try:
            from core.dependency_checker import DependencyChecker
            return DependencyChecker(
                target_dir=self.target_dir,
                target_venv=os.environ.get("FAULTLINE_TARGET_VENV") or None,
            )
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

    def run_functional_test(
        self,
        test_code: str,
        test_type: str = "api",
        case_kind: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """
        Writes a pytest script to the target directory, runs it, and returns the output.
        Validates dependencies before execution. The generated source and execution
        result are also persisted to the run folder so the suite survives the run.

        Args:
            test_code: The test code to execute
            test_type: Type of test ('api', 'auth', 'crud', 'django_model', 'load', 'e2e_*')
            case_kind: Optional explicit "happy" or "sad". Auto-inferred when omitted.

        Returns:
            Tuple of (passed: bool, output: str)
        """
        logger.info("Executing functional Pytest script (type=%s)...", test_type)

        # Check dependencies first
        deps_ok, deps_report = self.check_test_dependencies(test_type)
        if not deps_ok:
            logger.warning("Missing dependencies for %s test:\n%s", test_type, deps_report)
            self._record_test(
                test_id=uuid.uuid4().hex,
                test_type=test_type,
                case_kind=case_kind or _infer_case_kind(test_code),
                test_code=test_code,
                passed=False,
                stdout="",
                stderr=deps_report,
                duration_s=0.0,
                source_path=None,
                skipped_reason="dependency-check-failed",
            )
            return False, f"DEPENDENCY CHECK FAILED:\n\n{deps_report}"

        # Warn if test code imports Django — this causes ImproperlyConfigured crashes.
        # We still run it, but the warning surfaces in the output so the agent can fix it.
        _django_import_re = re.compile(
            r"^\s*(from\s+django\.|import\s+django|from\s+rest_framework\.|"
            r"from\s+\w+\.models\s+import|django\.setup\(\))",
            re.MULTILINE,
        )
        if _django_import_re.search(test_code):
            logger.warning(
                "⚠️  Test code contains Django/project imports. This will cause "
                "ImproperlyConfigured errors. Use pure httpx/requests HTTP calls instead."
            )

        kind = case_kind if case_kind in ("happy", "sad") else _infer_case_kind(test_code)
        test_id = uuid.uuid4().hex
        short = test_id[:8]
        test_filename = f"test_aegis_generated_{short}.py"
        test_filepath = str(self._resolve_target_path(test_filename))

        # Persist source to run_folder/testcases/ before execution so a crash mid-run
        # cannot lose it.
        source_copy = self._save_source_copy(test_filename, test_code, kind, test_type)

        passed = False
        stdout = ""
        stderr = ""
        t0 = time.monotonic()
        try:
            with open(test_filepath, "w", encoding="utf-8") as f:
                f.write(test_code)

            # Strip Django-specific env vars so pytest-django doesn't try to boot
            # Faultline's own Django app when running tests against the target project.
            # HTTP-level tests (api, auth, crud, etc.) need no Django setup at all.
            env = os.environ.copy()
            env.pop("DJANGO_SETTINGS_MODULE", None)
            env.pop("DJANGO_CONFIGURATION", None)

            # Write a safety conftest.py next to the test file that explicitly clears
            # DJANGO_SETTINGS_MODULE so any accidental Django import inside the test
            # gets a clean environment rather than crashing with ImproperlyConfigured.
            conftest_path = os.path.join(self.target_dir, "conftest_aegis_safety.py")
            if test_type != "django_model" and not os.path.exists(conftest_path):
                try:
                    with open(conftest_path, "w", encoding="utf-8") as _cf:
                        _cf.write(
                            "# Auto-generated by Faultline QAEngineer — safe to delete\n"
                            "import os\n"
                            "os.environ.pop('DJANGO_SETTINGS_MODULE', None)\n"
                            "os.environ.pop('DJANGO_CONFIGURATION', None)\n"
                        )
                except Exception as _e:
                    logger.debug("Could not write safety conftest: %s", _e)

            # -p no:django: prevent pytest-django from auto-detecting manage.py in
            # the target directory and crashing with ImportError on the wrong config.
            django_flag = [] if test_type == "django_model" else ["-p", "no:django"]

            result = subprocess.run(
                ["pytest", test_filename, "-v", "--tb=short"] + django_flag,
                cwd=self.target_dir,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
            )

            passed = result.returncode == 0
            stdout = result.stdout
            stderr = result.stderr
            output = stdout if passed else stdout + "\n" + stderr
            return passed, output

        except subprocess.TimeoutExpired:
            stderr = "Test execution timed out after 30 seconds."
            return False, stderr
        except Exception as e:
            stderr = f"Failed to execute test: {str(e)}"
            return False, stderr
        finally:
            duration = time.monotonic() - t0
            if os.path.exists(test_filepath):
                os.remove(test_filepath)
            # Remove the safety conftest after the run so it doesn't persist
            conftest_path = os.path.join(self.target_dir, "conftest_aegis_safety.py")
            if os.path.exists(conftest_path):
                try:
                    os.remove(conftest_path)
                except Exception:
                    pass
            self._record_test(
                test_id=test_id,
                test_type=test_type,
                case_kind=kind,
                test_code=test_code,
                passed=passed,
                stdout=stdout,
                stderr=stderr,
                duration_s=round(duration, 3),
                source_path=str(source_copy) if source_copy else None,
            )

    # ------------------------------------------------------------------
    # Test persistence
    # ------------------------------------------------------------------

    def _save_source_copy(
        self,
        test_filename: str,
        test_code: str,
        case_kind: str,
        test_type: str,
    ) -> Optional[Path]:
        """Save a copy of the generated test source under <run_folder>/testcases/."""
        if not self.run_folder:
            return None
        try:
            tc_dir = self.run_folder / "testcases"
            tc_dir.mkdir(parents=True, exist_ok=True)
            stem = Path(test_filename).stem
            dest = tc_dir / f"{stem}_{case_kind}_{test_type}.py"
            with open(dest, "w", encoding="utf-8") as f:
                f.write(test_code)
            return dest
        except Exception as e:
            logger.warning("Could not save test source copy: %s", e)
            return None

    def _extract_http_calls(self, stdout: str, test_id: str) -> list:
        """
        Parse pytest stdout for API call records.

        Priority 1 — structured AEGIS_RESULT lines written by the test itself:
          AEGIS_RESULT: {"method":"POST","url":"/api/...","payload":{...},"status":201,"response":{...}}

        Priority 2 — heuristic regex for informal print patterns:
          POST /api/auth/ → 201   |   response: 201 {...}
        """
        calls = []
        seen_aegis = set()

        for line in stdout.splitlines():
            stripped = line.strip()

            # Priority 1: structured contract line
            if stripped.startswith("AEGIS_RESULT:"):
                try:
                    raw = stripped[len("AEGIS_RESULT:"):].strip()
                    rec = json.loads(raw)
                    key = (rec.get("url", ""), rec.get("method", ""), rec.get("status", ""))
                    if key not in seen_aegis:
                        seen_aegis.add(key)
                        calls.append({
                            "test_id": test_id[:8],
                            "method": rec.get("method", "?"),
                            "path": rec.get("url", ""),
                            "payload": rec.get("payload"),
                            "status": rec.get("status"),
                            "response_snippet": json.dumps(rec.get("response", ""))[:400]
                            if rec.get("response") is not None else rec.get("response_snippet", ""),
                        })
                except Exception:
                    pass
                continue  # don't double-count with regex

            # Priority 2: heuristic regex
            _call_re = re.compile(
                r"(?:(?P<method>GET|POST|PUT|PATCH|DELETE|HEAD)\s+(?P<path>/[^\s]*)\s*(?:→|-+>)?\s*(?P<status>\d{3}))"
                r"|(?:response[:\s]+(?P<status2>\d{3})\s+(?P<body>.{0,200}))",
                re.IGNORECASE,
            )
            m = _call_re.search(line)
            if not m:
                continue
            status = m.group("status") or m.group("status2") or ""
            method = m.group("method") or "?"
            path = m.group("path") or ""
            body_snip = (m.group("body") or "").strip()[:200]
            if status:
                calls.append({
                    "test_id": test_id[:8],
                    "method": method,
                    "path": path,
                    "payload": None,
                    "status": int(status) if status.isdigit() else status,
                    "response_snippet": body_snip,
                })
        return calls

    def _log_api_calls(self, calls: list, test_type: str = "", case_kind: str = "", passed: bool = True) -> None:
        """
        Append per-HTTP-call records to api_calls_log.jsonl (compact) and
        api_results_log.jsonl (full payload + unexpected-status flag).
        Also updates the status of discovered endpoints in api_test_data.json.
        """
        if not self.run_folder or not calls:
            return
        log_path = self.run_folder / "api_calls_log.jsonl"
        results_path = self.run_folder / "api_results_log.jsonl"
        test_data_path = self.run_folder / "api_test_data.json"
        
        ts = datetime.now().isoformat(timespec="seconds")
        try:
            with self._ledger_lock:
                # 1. Update status in api_test_data.json
                if test_data_path.exists():
                    try:
                        data = json.loads(test_data_path.read_text(encoding="utf-8"))
                        eps = data.get("endpoints", {})
                        for c in calls:
                            c_path = c.get("path", "").strip("/")
                            c_method = c.get("method", "GET")
                            
                            found = False
                            # Try exact match first
                            match_key = f"{c_method} /{c_path}"
                            if match_key in eps:
                                eps[match_key]["status"] = "Tested"
                                found = True
                            elif f"{c_method} /{c_path}/" in eps:
                                eps[f"{c_method} /{c_path}/"]["status"] = "Tested"
                                found = True
                            
                            if not found:
                                # Fallback: search for path match regardless of prefix or trailing slash
                                for k, v in eps.items():
                                    v_url = v.get("url", "").strip("/")
                                    v_method = v.get("method", "GET")
                                    if v_method == c_method and (v_url == c_path or v_url == c_path + "/"):
                                        v["status"] = "Tested"
                                        break
                        test_data_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                    except Exception as ex:
                        logger.warning("Failed to update api_test_data coverage: %s", ex)

                # 2. Append logs
                with open(log_path, "a", encoding="utf-8") as f:
                    for c in calls:
                        f.write(json.dumps({"ts": ts, **c}, ensure_ascii=False) + "\n")
                    f.flush()
                with open(results_path, "a", encoding="utf-8") as f:
                    for c in calls:
                        status = c.get("status")
                        # Flag unexpected: 4xx/5xx on happy path, 2xx on sad path
                        unexpected = False
                        if isinstance(status, int):
                            if case_kind == "happy" and status >= 400:
                                unexpected = True
                            elif case_kind == "sad" and 200 <= status < 300:
                                unexpected = True
                        record = {
                            "ts": ts,
                            "test_type": test_type,
                            "case_kind": case_kind,
                            "passed": passed,
                            "unexpected_status": unexpected,
                            **c,
                        }
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    f.flush()
        except Exception as e:
            logger.warning("Could not write api call logs: %s", e)

    def _record_test(
        self,
        test_id: str,
        test_type: str,
        case_kind: str,
        test_code: str,
        passed: bool,
        stdout: str,
        stderr: str,
        duration_s: float,
        source_path: Optional[str],
        skipped_reason: Optional[str] = None,
    ) -> None:
        """Append a data-only entry to <run_folder>/generated_tests.json (atomic write).

        The code is intentionally excluded — it lives in source_path on disk.
        This keeps the ledger diffable and machine-readable without embedding source blobs.
        """
        if not self.run_folder:
            return

        # Log per-HTTP-call records parsed from stdout
        calls = self._extract_http_calls(stdout or "", test_id)
        self._log_api_calls(calls, test_type=test_type, case_kind=case_kind, passed=passed)

        try:
            ledger = self.run_folder / "generated_tests.json"
            unexpected_calls = [c for c in calls if c.get("unexpected_status")]
            entry = {
                "id": test_id,
                "ts": datetime.now().isoformat(timespec="seconds"),
                "test_type": test_type,
                "case_kind": case_kind,
                "passed": passed,
                "duration_s": duration_s,
                "source_path": source_path,
                "http_calls_count": len(calls),
                "http_calls": [
                    {
                        "method": c.get("method", "?"),
                        "path": c.get("path", ""),
                        "payload": c.get("payload"),
                        "status": c.get("status"),
                        "response_snippet": c.get("response_snippet", ""),
                        "unexpected_status": c.get("unexpected_status", False),
                    }
                    for c in calls
                ],
                "unexpected_calls_count": len(unexpected_calls),
                "result_summary": (stdout or "")[-1500:].strip() or (stderr or "")[-500:].strip(),
            }
            if skipped_reason:
                entry["skipped_reason"] = skipped_reason
            with self._ledger_lock:
                existing = []
                if ledger.exists():
                    try:
                        existing = json.loads(ledger.read_text(encoding="utf-8"))
                        if not isinstance(existing, list):
                            existing = []
                    except Exception:
                        existing = []
                existing.append(entry)
                tmp = ledger.with_suffix(".json.tmp")
                tmp.write_text(
                    json.dumps(existing, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                os.replace(tmp, ledger)
        except Exception as e:
            logger.warning("Could not append to generated_tests.json: %s", e)

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

    def get_coverage_report(self) -> dict:
        """
        Calculate API test coverage metrics based on api_test_data.json.
        Returns a summary of total, tested, and untested endpoints with grouping.
        """
        if not self.run_folder:
            return {"error": "No run_folder defined"}
            
        path = self.run_folder / "api_test_data.json"
        if not path.exists():
            return {"error": "api_test_data.json not found"}
            
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            endpoints = data.get("endpoints", {})
            total = len(endpoints)
            tested = sum(1 for e in endpoints.values() if e.get("status") == "Tested")
            traced = sum(1 for e in endpoints.values() if e.get("traced"))
            
            # Group by top-level prefix (e.g. /api/auth -> /api/auth)
            area_stats = {}
            for k, v in endpoints.items():
                parts = k.split(" ")
                url = parts[1] if len(parts) > 1 else k
                prefix = "/" + url.strip("/").split("/")[0] if url.strip("/") else "/"
                if url.startswith("/api/"):
                    prefix = "/api/" + url[5:].split("/")[0]
                
                stats = area_stats.setdefault(prefix, {"total": 0, "tested": 0})
                stats["total"] += 1
                if v.get("status") == "Tested":
                    stats["tested"] += 1
            
            # Sort areas by total endpoints
            sorted_areas = dict(sorted(area_stats.items(), key=lambda item: item[1]["total"], reverse=True))
            
            untested_list = [k for k, v in endpoints.items() if v.get("status") != "Tested"]
            
            return {
                "total_endpoints": total,
                "tested_count": tested,
                "untested_count": total - tested,
                "coverage_pct": round((tested / total * 100), 1) if total > 0 else 0,
                "discovery_confidence": round((traced / total * 100), 1) if total > 0 else 0,
                "area_coverage": sorted_areas,
                "untested_samples": untested_list[:10]
            }
        except Exception as e:
            return {"error": f"Failed to parse coverage: {str(e)}"}
