# Faultline Pipeline Vision

The ultimate goal of the Faultline project is to provide a comprehensive, 7-step pipeline that progresses from basic static analysis to deep semantic understanding, production readiness profiling, and finally, cyber security chaos testing.

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
**Goal:** Ensure the code actually does what it was intended to do. 
- The LLM compares the documentation (intent) with the actual code (implementation).
- This is achieved by creating a semantic graph using FAISS and HNSW to link documents and code descriptions.
- This is a critical, time-consuming step where the LLM is allowed to "think hard" and scrutinize the architecture.

### 6. Production Readiness Profiling
**Goal:** Identify issues that will cause the server to fail under load or over time. This includes:
- Potentially slow running queries.
- Missing rate limiting.
- Inefficient data batching (N+1 queries).
- Missing or improper caching.
- Potential race conditions and sync/async issues.
- MRO (Method Resolution Order) issues.
- Memory and database connection leaks.

### 7. Cyber Security Chaos Engineering
**Goal:** Prevent attackers from exploiting the system.
- Create attack scripts to assault the system.
- Analyze the limitations of each endpoint.
- Attempt to hack the database, gain system access, or leak credentials.

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
**Status: Aligned**
The `QAEngineer` writes functional Pytest scripts, while the `LogCorrelator` maps crashes directly to payloads using trace IDs. The newly added **Vault Authentication System** dynamically handles credentials (both static and login-based).

### 🟡 Step 5: Semantic Intent
**Status: Partially Aligned**
The `SemanticIndexer` uses FAISS and Qwen embeddings to index Markdown documentation. The `Visualizer` has a placeholder for "Intent Correlation". 
*Future expansion:* We need to explicitly implement the "diffing" phase where the LLM deeply compares the FAISS index against the AST graph to find logic mismatches.

### ❌ Step 6: Production Readiness
**Status: Gap Identified**
Faultline does not currently have explicit skills for profiling memory leaks, race conditions, or slow queries. 
*Future expansion:* Integrate load testing tools (like locust/k6) and memory profilers into the `skills/` directory.

### ✅ Step 7: Cyber Security
**Status: Aligned**
The `SiegeEngine` (`skills/attacker.py`) handles asynchronous HTTP assaults and fuzzing against endpoints.
*Future expansion:* Expand the SiegeEngine to include specific database injection and credential brute-forcing payloads.

## Conclusion

Faultline's architecture (Pipeline Mode + Agent Mode) successfully provides the scaffolding for this 7-step vision. The deterministic pipeline handles Steps 1-3 perfectly, while the LangGraph agent orchestrates Steps 4, 5, and 7. The primary area for future development is Step 6 (Production Readiness profiling).
