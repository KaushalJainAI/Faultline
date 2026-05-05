# Faultline Pipeline Vision

**Date**: 2026-05-01
**Description**: The architectural roadmap and 7-step vision for the Faultline platform, from static analysis to cyber security chaos engineering.

The ultimate goal of the Faultline project is to provide a comprehensive, 7-step pipeline that progresses from basic static analysis to deep semantic understanding, production readiness profiling, and finally, cyber security chaos testing — delivered through an **interactive CLI agent** that operates like Claude Code or OpenCode.

The user experience is a single command (`python faultline.py`) that drops the operator into a live session with the Faultline agent. The terminal shows the agent's reasoning as it works, every tool call and result, every file generated, and pauses to ask the operator for credentials or destructive-action permission whenever the agent needs them. This is the primary surface; the Django REST control plane remains available for headless / CI use.

This document outlines that "Grand Vision" and evaluates the project's current alignment with these objectives.

## The 7-Step Pipeline

### 1. Syntax & Hardcoded Runtime Checks
**Goal:** Programmatically identify syntax errors and fundamental issues that guarantee runtime failure. This is less "agentic" and more hardcoded, but it is the foundational step without which any software is bound to fail.

### 2. Deterministic & Dependency Checks
**Goal:** Programmatically identify common developer mistakes using standard regex and pre-made functions. This includes:
- Import errors.
- Division by zero errors.
- Incorrect argument passing.
- Missing dependencies or dependency clashes.
- Deprecation warnings.

### 3. AST Dependency Failure Analysis
**Goal:** Use Abstract Syntax Trees (AST) to identify the "core" or root-cause nodes that fail and subsequently cause many other dependent nodes to fail. This helps in debugging by tracing failures back to their source.

### 4. Agentic API Testing & DB Log Analysis
**Goal:** Move beyond static issues and use LLMs to write dynamic tests for the target's APIs. 
- The system must handle authentication (using real or dummy credentials, prompting the user if necessary).
- The system must analyze database and server logs and generate a report based on these logs.

### 5. Semantic Intent vs. Implementation
**Goal:** Verify that the system behaves the way its documentation says it should — not just syntactically, but in actual purpose and responsibility.

The `docs/` folder is the ground truth of intent. Every function has a docstring that describes its responsibility. The bridge between the two is built as follows:

1. **Semantic Mapping via FAISS.** The `SemanticIndexer` embeds all content in `docs/` and all function docstrings into the same vector space. For each function, FAISS retrieves the most semantically similar documentation passage — this is the "contract" that function is responsible for fulfilling.

2. **LLM Contract Verification.** The agent is given three things: the doc passage (what the system should do), the function docstring (what the function claims to do), and the function body (what it actually does). It is asked: *"Does this implementation faithfully fulfill the requirement described in the documentation?"* Mismatches, missing logic, or contradictions are raised as findings.

3. **Coverage gaps.** Any doc passage that has no function mapping above a similarity threshold is flagged as an *unimplemented requirement*. Any function with no docstring is flagged as an *unmapped responsibility* — it does something, but no one has claimed what.

The result is a two-way accountability map: every doc requirement is traced to the code that owns it, and every piece of code is traceable back to the intent that justifies its existence.

### 6. End-to-End & Production Readiness Testing
**Goal:** Test the system as a whole under conditions that mirror real usage — validating that all the pieces work together, and that the system will survive real traffic without degrading.

Step 4 tested individual API endpoints in isolation. Step 6 goes further: it assembles full user journeys and stresses the system as a live, connected whole.

1. **End-to-End Flow Testing.** The agent reads the `docs/` to understand the intended user journeys (e.g. "a user registers, logs in, creates a resource, and retrieves it"). It then writes and executes multi-step test scripts that follow these flows against the running server — validating not just that each endpoint works, but that they work *in sequence*, with real state persisting between calls.

2. **Load & Degradation Testing.** Using the discovered endpoints, the agent ramps up concurrent traffic to identify at what point the system starts returning errors, slowing down, or dropping requests. The goal is not to crash the system — it is to find the breaking point and report it.

3. **Production Anti-Pattern Detection (Static).** In parallel, a static AST pass flags patterns that are known to fail at scale but are invisible during development: N+1 ORM queries inside loops, list endpoints with no pagination, blocking synchronous calls inside async handlers, and unclosed database or file handles.

4. **Regression Baseline.** After a successful run, the step stores response time percentiles and error rates as a baseline. Future runs compare against this baseline and flag regressions automatically.

### 7. Cyber Security Chaos Engineering
**Goal:** Actively attempt to break, bypass, and exploit the running system — then produce a structured report of every vulnerability found so the team can fix them.

This step treats the system as an adversary would. The `SiegeEngine` runs a scripted attack campaign against the live server and generates a findings report ranked by severity.

**Attack vectors executed:**
- **Broken Authentication.** Replay authenticated requests with stripped, expired, or forged tokens. Any endpoint that responds with `2xx` is a critical finding.
- **Injection Attacks.** For every input parameter discovered in steps 4 and 6, send SQL injection payloads (`' OR '1'='1`, `; DROP TABLE`), command injection strings, and template injection probes. Flag any response that leaks a stack trace, changes behavior, or shows query output.
- **Insecure Direct Object Reference (IDOR).** For every resource endpoint with a numeric or UUID identifier, attempt to access another user's resource by ID-walking with a different session. Flag any `2xx` response that returns data belonging to a different account.
- **Privilege Escalation.** Attempt to call admin-only or elevated endpoints using a standard user token. Flag any endpoint that does not enforce role boundaries.
- **Input Boundary Abuse.** Send oversized payloads, null bytes, deeply nested JSON, and unicode edge cases to every input field. Flag crashes, hangs, or unexpectedly permissive responses.

**Output:** A ranked security report — Critical, High, Medium, Low — with the exact request that triggered each finding, the response received, and a plain-language description of the risk. The report is the primary deliverable: it is designed to be handed directly to a developer or security team to act on.

---

## Current Project Alignment Assessment

Faultline was built with this exact vision in mind. Here is how the current implementation aligns with the 7 steps:

### ✅ Steps 1 & 2: Deterministic Baseline
**Status: Aligned (with room for expansion)**
The `DeterministicChecker` (`skills/deterministic_checker.py`) handles syntax parsing, missing imports, zero-division hazards, and integrates with `ruff` and `pip check`. 
*Future expansion:* Deepen the checks for argument passing mismatches and deprecation warnings.

### ✅ Step 3: Dependency Analysis
**Status: Aligned**
The `ASTGrapher` (`skills/ast_grapher.py`) creates the project map, and the `DeterministicChecker` uses this map to perform root-cause dependency failure propagation, identifying exactly which core files break the most dependents.

### ✅ Step 4: Agentic API Testing
**Status: Aligned (Cost-Optimized)**
The `QAEngineer` writes functional Pytest scripts, while the `LogCorrelator` maps crashes directly to payloads using trace IDs. The newly added **Vault Authentication System** dynamically handles credentials.
*Cost Optimization & Debugging:* The agent now utilizes a **Copy and Edit** boilerplate methodology (`agent_assets/test_boilerplates/`) to radically reduce output token costs. Furthermore, all agent step-by-step reasoning is streamed to `reports/campaign_<id>_agent.log` ensuring complete debug visibility for human reviewers.

### 🟡 Step 5: Semantic Intent
**Status: Partially Aligned**
The `SemanticIndexer` (`skills/semantic_indexer.py`) builds the FAISS index over `docs/` — the indexing half is done.
*Next implementation tasks:*
- Extend the indexer to also embed function docstrings from the AST graph, placing docs and code in the same vector space.
- Add the LLM contract verification pass: retrieve the top doc match per function, then send (doc passage, docstring, function body) to the agent for fulfillment judgment.
- Surface coverage gaps: unmatched doc passages and undocumented functions.

### ❌ Step 6: End-to-End & Production Readiness
**Status: Gap**
Step 4's `QAEngineer` tests individual endpoints. Nothing currently assembles multi-step user journeys, runs load testing, or captures a performance baseline.
*Next implementation tasks:*
- Teach the agent to read `docs/` for user journey descriptions and generate sequential flow test scripts.
- Integrate a lightweight load runner (e.g. `httpx` async concurrency or `locust`) to find the degradation threshold.
- Add the static AST pass for N+1, missing pagination, and sync-in-async patterns.

### 🟡 Step 7: Cyber Security
**Status: Partially Aligned**
The `SiegeEngine` (`skills/attacker.py`) handles async HTTP load but does not yet run structured attack campaigns.
*Next implementation tasks:*
- Implement the five attack vectors (auth bypass, injection, IDOR, privilege escalation, input boundary abuse) as discrete, reusable attack scripts.
- Add the ranked findings report generator — this is the primary output of the step.
- Gate all live attacks behind the Vault authorization check so the system cannot be pointed at an unauthorized target.

## Per-Run Audit Trail & Production-Readiness Tracking

Every `python faultline.py` invocation creates a unique, isolated output folder:

```
reports/
  <project_name>_<YYYYMMDD>_<HHMMSS>/
    pipeline_report.md      ← deterministic findings: syntax, imports, deps, AST roots
    dependency_graph.py     ← interactive 3D dependency visualization (Dash app)
    campaign_agent.log      ← full step-by-step agent reasoning (debug trail)
    vulnerability_report.md ← final vulnerability report (agent-written or fallback)
    testcases/
      api_test_boilerplate.py   ← automatically deployed at startup
      model_test_boilerplate.py  ← edited in-place by the agent
```

### Production-Readiness Score

The pipeline report opens with a **Production-Readiness Score (0–100)**. The formula is purely deterministic — no LLM text anywhere in the calculation:

```
score = 100 − Σ(penalty per finding)
  critical → −20   high → −10   medium → −4   low → −2
  (capped at 0)
```

The score is displayed as an ASCII gauge: `` `████████░░  84/100` ``

### Tracking Improvement Over Time

Because each run gets its own timestamped folder, operators can:

1. Run Faultline, see the score and findings.
2. Fix the issues that the report flags (syntax errors, missing imports, security candidates).
3. Re-run Faultline. The new score in the new report shows measurable progress.
4. Continue iterating until the score reaches an acceptable threshold and the Next Steps checklist is clear.

The `reports/` directory becomes a time-series log of the project's health, making it straightforward to hand off to a team, include in a PR, or track across sprints.

### Boilerplate-Driven Test Generation

Rather than having the agent write test files from scratch or manually copy them, Faultline automatically deploys all boilerplate scripts from `agent_assets/test_boilerplates/` into the `testcases/` directory at the start of every campaign. The agent then directly edits these pre-existing templates. This "Edit-Run" workflow keeps generated files readable, minimizes token usage, and prevents structural hallucinations.

---

## Conclusion

Faultline's architecture (Pipeline Mode + Agent Mode) successfully provides the scaffolding for this 7-step vision. The deterministic pipeline handles Steps 1-3 perfectly, while the LangGraph agent orchestrates Steps 4, 5, and 7. The per-run folder and production-readiness score give operators a clear, repeatable loop for tracking improvement over time.

The next three concrete milestones are:
1. **Extend `SemanticIndexer` to embed docstrings** — places code and docs in the same vector space, enabling the contract verification pass that closes Step 5.
2. **E2E flow test generation in `QAEngineer`** — agent reads `docs/` for user journeys and generates sequential test scripts, closing the core gap in Step 6.
3. **Structured attack campaigns in `SiegeEngine`** — implement the five attack vectors and the ranked findings report, making Step 7 a complete self-hacking audit tool.
