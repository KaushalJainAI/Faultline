# Faultline Testing Guide

This guide outlines the protocol for AI agents and human contributors to write tests efficiently and consistently for targets in the Faultline platform.

## The Cost-Optimized Boilerplate Methodology

To significantly reduce output token generation costs (which are 5x more expensive than input tokens) and improve the reliability of generated tests, we enforce a strict **Copy and Edit** workflow.

### Protocol for Agents

1. **Locate the Boilerplate**: Navigate to `agent_assets/test_boilerplates/`.
2. **Select the Template**: Choose the appropriate boilerplate file (e.g., `api_test_boilerplate.py` for API endpoints, `model_test_boilerplate.py` for database models).
3. **Copy and Edit In-Place**: Read the boilerplate into context, and generate a mutated version of it. Replace the placeholder variables (`<TARGET_URL>`, `<PAYLOAD>`, etc.) with the findings discovered during the campaign.
4. **Save the Result**: Save the newly edited test script directly into the `reports/testcases/` directory. Use a descriptive filename (e.g., `test_auth_bypass.py` or `test_sql_injection_login.py`).

### Why We Do This
- **Lower Costs**: The LLM outputs significantly fewer tokens by modifying a well-structured template than by writing a complex testing script from scratch.
- **Proven Code**: Boilerplates contain proven HTTP retry logic, assertion paradigms, and authentication scaffolding, reducing the chance of runtime errors in the generated tests.
- **Traceability**: All generated test cases are automatically archived alongside the 7-step campaign reports in the `reports/testcases/` directory, providing a full audit trail.

## Agent Step-by-Step Logging

During automated campaigns, the Aegis agent will log its step-by-step reasoning, tool calls, and issues encountered. Reviewers can find this log at:
`reports/campaign_<id>_agent.log`

If an agent fails to generate a correct test case, reviewers should:
1. Open the agent log.
2. Track the AST or endpoint traversal path.
3. Identify where the agent hallucinated or encountered an application error.
4. Update the boilerplates or target application as necessary.
