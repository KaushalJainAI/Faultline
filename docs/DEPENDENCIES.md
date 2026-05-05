# Faultline Dependencies & Environment

**Date**: 2026-05-01
**Description**: Documentation for the required libraries, system dependencies, and environment configuration for running Faultline.

## Overview

Faultline's test boilerplate system requires different dependencies for different test types. The system includes automated dependency checking to ensure tests can run successfully before execution.

---

## Environment Detection

The dependency checker automatically detects:
- **Virtual Environment Status**: Whether running in venv, conda, or system Python
- **Python Version**: Current Python version
- **Installed Packages**: All pip-installed packages and versions

**Run dependency check:**
```bash
python core/dependency_checker.py
```

---

## Test Template Dependencies

| Test Type | Required Packages | Purpose |
|-----------|-------------------|---------|
| `api` | pytest, httpx | Basic API endpoint testing |
| `auth` | pytest, httpx | Authentication/authorization boundaries |
| `crud` | pytest, httpx | CRUD operations with fixtures |
| `validation` | pytest, httpx | Input validation and boundary testing |
| `idor` | pytest, httpx | IDOR/access control testing |
| `django_model` | pytest, pytest-django, django | Django ORM and model testing |
| `load` | locust | Load testing & degradation threshold |
| `e2e_journey` | pytest, playwright | End-to-end user journey testing |
| `e2e_react` | pytest, playwright | React UI correctness testing |

---

## Setting Up Virtual Environment

### Option 1: Python venv (Recommended)

```bash
# Create virtual environment
python -m venv faultline-env

# Activate venv
# On Windows:
faultline-env\Scripts\activate

# On macOS/Linux:
source faultline-env/bin/activate

# Install core dependencies
pip install pytest httpx pytest-django django

# Install load testing (optional)
pip install locust

# Install E2E testing (optional)
pip install playwright
playwright install  # Download browsers
```

### Option 2: Conda

```bash
# Create conda environment
conda create -n faultline python=3.11

# Activate
conda activate faultline

# Install dependencies
conda install pytest httpx pytest-django django locust playwright
playwright install
```

### Option 3: System Python (Not Recommended)

```bash
# Install globally (may require sudo)
pip install pytest httpx pytest-django django locust playwright
playwright install
```

---

## Checking Dependencies Programmatically

### Python API

```python
from core.dependency_checker import DependencyChecker

checker = DependencyChecker()

# Check specific test type
is_valid, report = checker.validate_and_report("load")
print(report)

# Get environment info
print(checker.get_venv_info())

# Get installation command
print(checker.get_installation_command("e2e_journey"))
```

### From QAEngineer

```python
from skills.qa_engineer import QAEngineer

qa = QAEngineer(target_dir=".")

# Check before running tests
ok, report = qa.check_test_dependencies("auth")
if ok:
    passed, output = qa.run_functional_test(test_code, test_type="auth")
else:
    print(f"Cannot run test: {report}")
```

---

## Automated Dependency Checking in Test Execution

When you call `run_functional_test()`, it automatically:

1. **Detects the test type** (api, load, e2e_react, etc.)
2. **Checks all required dependencies** are installed
3. **Returns a detailed report** if dependencies are missing
4. **Only runs the test** if all dependencies are satisfied

**Example:**
```python
from core.tools import run_functional_test

# This will check dependencies before running
result = run_functional_test(
    test_code="import pytest\ndef test_example(): pass",
    target_dir="reports/my_test",
    test_type="api"  # <-- automatically checks pytest, httpx
)
print(result)
```

---

## Agent Workflow with Dependency Checking

When the agent generates tests:

1. **Agent calls**: `copy_test_boilerplate("auth", run_folder)`
   - Returns: `reports/project_TIMESTAMP/testcases/api_auth_test_boilerplate_HHMMSS.py`

2. **Agent edits placeholders**: Uses `propose_code_patch()` to fill in test-specific values

3. **Agent runs test**: Calls `run_functional_test(test_code, test_type="auth")`
   - **Step A**: DependencyChecker validates pytest + httpx are installed
   - **Step B**: If missing, returns error report with `pip install` command
   - **Step C**: If OK, executes pytest on the test file

4. **Agent reports**: Summarizes findings in `vulnerability_report.md`
   - If dependencies were missing: "Cannot run auth tests - install: pip install httpx"
   - If test passed: "Auth tests passed: 12/12 passed"
   - If test failed: Shows pytest output with specific failures

---

## Handling Missing Dependencies

### User Perspective

If a test cannot run due to missing dependencies:

```
Status: FAILED
Output:
DEPENDENCY CHECK FAILED:

Dependency Check for 'load' Test Template:
------------------------------------------------------------
Virtual Environment: /path/to/venv
Python: 3.11.0

[OK] Installed:

[MISSING] Not installed:
  - locust

Fix with: pip install locust
```

**Solution:**
```bash
# Activate your venv
source faultline-env/bin/activate

# Install missing package
pip install locust

# Re-run the test
python faultline.py --target /path/to/project
```

### Agent Perspective

The agent receives the dependency report and can:
1. **Inform the user**: "Cannot run load tests - Locust is not installed"
2. **Skip the test**: Move to other Steps (security, E2E, etc.)
3. **Suggest installation**: "Run: pip install locust"

---

## Common Issues

### Issue 1: "pytest: command not found"
**Cause**: pytest not installed or running without activating venv
**Fix**: `pip install pytest` and ensure venv is activated

### Issue 2: "ModuleNotFoundError: No module named 'playwright'"
**Cause**: playwright not installed
**Fix**: `pip install playwright && playwright install`

### Issue 3: "Django not found" (for django_model tests)
**Cause**: Django/pytest-django not installed
**Fix**: `pip install django pytest-django`

### Issue 4: Dependency checker shows "Not detected (system Python)"
**Cause**: Running system Python, not a venv
**Fix**: Create and activate a venv: `python -m venv env && source env/bin/activate`

---

## CI/CD Integration

For CI/CD pipelines (GitHub Actions, GitLab CI, etc.):

```yaml
# Example GitHub Actions workflow
jobs:
  faultline-test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Create venv and install dependencies
        run: |
          python -m venv venv
          source venv/bin/activate
          pip install pytest httpx pytest-django django locust playwright
          playwright install

      - name: Run Faultline
        run: |
          source venv/bin/activate
          python faultline.py --target /path/to/target
```

---

## Requirements Files

### `requirements.txt` (Core)
```
pytest>=7.0
httpx>=0.23.0
pytest-django>=4.5
django>=3.2
```

### `requirements-optional.txt` (Load + E2E)
```
locust>=2.0
playwright>=1.40
```

### `requirements-all.txt` (Everything)
```
-r requirements.txt
-r requirements-optional.txt
```

**Install all:**
```bash
pip install -r requirements-all.txt
playwright install
```

---

## Dependency Architecture

```
DependencyChecker
├── _detect_venv()              # Check if in virtual environment
├── _get_installed_packages()   # Query pip list --format=json
├── check_dependencies()         # Validate test type requirements
├── check_all_dependencies()    # Summary of all test types
├── validate_and_report()       # Generate report + installation command
└── get_installation_command()  # Auto-generate pip install string

QAEngineer
├── _init_dependency_checker()  # Lazy load DependencyChecker
├── check_test_dependencies()   # Wrapper for validation
└── run_functional_test()       # Check deps BEFORE pytest.run()

core/tools.py
└── run_functional_test()       # Tool binding (accepts test_type param)
```

---

## Troubleshooting

**Check environment:**
```bash
python -c "import sys; print(f'Python: {sys.executable}')"
python -c "import sys; print(f'Venv: {sys.prefix}')"
```

**Verify specific package:**
```bash
python -c "import httpx; print(httpx.__version__)"
```

**Reinstall package:**
```bash
pip uninstall -y httpx && pip install httpx
```

**Update pip itself:**
```bash
python -m pip install --upgrade pip
```
