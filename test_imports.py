import ast
import importlib
import pkgutil
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
SOURCE_MODULES = ("faultline",)
SOURCE_PACKAGES = ("campaigns", "config", "core", "scripts", "skills", "vault")

REMOVED_CORE_SHIMS = {
    "api_knowledge",
    "checkpoint",
    "cli_provider",
    "cli_ui",
    "content_manager",
    "context",
    "credential_store",
    "index_state",
    "input_handler",
    "live_report",
    "model_registry",
    "pipeline",
    "progress_tracker",
    "prompts",
    "provider_config",
    "run_context",
    "session_store",
}


def _source_files():
    for package in SOURCE_PACKAGES:
        root = PROJECT_ROOT / package
        for path in root.rglob("*.py"):
            yield path
    for module in SOURCE_MODULES:
        yield PROJECT_ROOT / f"{module}.py"


def _iter_module_names():
    yield from SOURCE_MODULES
    for package in SOURCE_PACKAGES:
        package_path = PROJECT_ROOT / package
        yield package
        for module in pkgutil.walk_packages([str(package_path)], prefix=f"{package}."):
            yield module.name


def test_no_imports_point_at_removed_core_shims():
    stale_imports = []

    for path in _source_files():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        rel_path = path.relative_to(PROJECT_ROOT)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    parts = alias.name.split(".")
                    if (
                        len(parts) >= 2
                        and parts[0] == "core"
                        and parts[1] in REMOVED_CORE_SHIMS
                    ):
                        stale_imports.append(f"{rel_path}:{node.lineno} import {alias.name}")

            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                module_parts = module.split(".")
                if module == "core":
                    for alias in node.names:
                        if alias.name in REMOVED_CORE_SHIMS:
                            stale_imports.append(
                                f"{rel_path}:{node.lineno} from core import {alias.name}"
                            )
                elif (
                    len(module_parts) >= 2
                    and module_parts[0] == "core"
                    and module_parts[1] in REMOVED_CORE_SHIMS
                ):
                    stale_imports.append(f"{rel_path}:{node.lineno} from {module} import ...")

    assert stale_imports == []


def test_source_modules_import_cleanly():
    failures = []

    for module_name in sorted(set(_iter_module_names())):
        try:
            importlib.import_module(module_name)
        except Exception as exc:
            failures.append(f"{module_name}: {type(exc).__name__}: {exc}")

    assert failures == []
