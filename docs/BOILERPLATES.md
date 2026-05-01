# Faultline Test Boilerplates

**Date**: 2026-05-01
**Description**: This document describes the available test boilerplate templates in Faultline and how the agent uses them in the "Edit-Run" methodology.

Faultline uses a **Copy and Edit** methodology to generate reliable, cost-effective functional tests. Rather than writing tests from scratch, the agent identifies the correct template from `agent_assets/test_boilerplates/`, copies it to the run-specific `testcases/` directory, and edits it in-place.

## Available Templates

### API Testing
- **`api_test_boilerplate.py`**: A generic template for testing HTTP endpoints.
- **`api_auth_test_boilerplate.py`**: Scaffolding for testing authentication flows, token validation, and logout.
- **`api_crud_test_boilerplate.py`**: A complete template for Create, Read, Update, and Delete (CRUD) operations on a resource.
- **`api_idor_test_boilerplate.py`**: Specialized template for testing Insecure Direct Object Reference (IDOR) vulnerabilities by attempting to access resources with different user tokens.
- **`api_input_validation_test_boilerplate.py`**: Template for fuzzing inputs and testing boundary conditions, character encoding, and schema validation.

### Database & Models
- **`model_test_boilerplate.py`**: Simple template for testing basic Django model persistence.
- **`django_model_advanced_test_boilerplate.py`**: Advanced template for testing model relationships, signals, constraints, and manager methods.

### End-to-End (E2E)
- **`e2e_react_ui_test_boilerplate.py`**: Template for testing frontend-to-backend integration, specifically for React-based UIs.
- **`e2e_user_journey_test_boilerplate.py`**: Scaffolding for multi-step user journey tests (e.g., register -> login -> profile update -> logout).

### Performance
- **`load_test_boilerplate.py`**: Template for concurrent HTTP load testing to identify performance bottlenecks and degradation thresholds.

## Usage Protocol

1. **Deployment**: At the start of every campaign, all boilerplates are automatically copied from `agent_assets/test_boilerplates/` to `reports/<project>_<timestamp>/testcases/`.
2. **Identification**: The agent analyzes the project structure to decide which boilerplate is most relevant to the current testing goal.
3. **Modification**: The agent uses the `copy_test_boilerplate` tool or direct file edits to replace placeholders (like `<TARGET_URL>`, `<AUTH_HEADERS>`, etc.) with project-specific values.
4. **Execution**: The agent runs the modified test using the `run_functional_test` tool.
5. **Reporting**: Pass/fail results and logs are captured and included in the campaign findings.

For more details on the agent's testing logic, see [TESTING_GUIDE.md](TESTING_GUIDE.md).
