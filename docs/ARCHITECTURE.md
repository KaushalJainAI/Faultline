# Faultline Architecture

Faultline is a control plane for AI-assisted QA and chaos engineering. This document provides a high-level overview of how the system's components interact to analyze, test, and report on target applications.

## System Overview

Faultline is divided into three primary domains:
1.  **Control Plane (Django)**: Manages campaign metadata, findings, tool run history, and the REST API.
2.  **Vault Authentication (Django)**: Dynamically manages authentication workflows to acquire session credentials for testing.
3.  **Execution Engine (LangGraph)**: Orchestrates the Aegis-Breaker agent, which uses tools to interact with the target application.

```mermaid
graph TD
    User([User / CLI]) -->|POST /api/v1/campaign/start/| API[REST API]
    API -->|Create| CampaignDB[(SQLite Database)]
    API -->|Trigger| Service[Campaign Service]
    
    subgraph "Execution Engine"
        Service -->|Execute AuthFlow| Vault[Vault Authenticator]
        Vault -->|Pre-flight Auth| Target
        Vault -->|Session Headers| Agent
        Service -->|Initialize| Agent[Aegis Agent]
        Agent -->|Invoke| Tools[LangChain Tools]
        Tools -->|Execute| Skills[Skills Library]
        Skills -->|Interact (Authenticated)| Target[Target Application]
        Skills -->|Read Logs| LogCorrelator[Log Correlator]
    end
    
    subgraph "Persistence"
        Service -->|Record| ToolRun[ToolRun Records]
        Agent -->|Report| Findings[Finding Records]
        Service -->|Generate| Report[Markdown Report]
    end
    
    ToolRun --> CampaignDB
    Findings --> CampaignDB
    Report --> FileSystem[Reports Directory]
```

## Core Components

### 1. Control Plane (Django)
The control plane provides the administrative interface and persistence layer.
-   **Campaigns**: Represent a single execution run against a target. They track status, configuration (target path, URL, start command), and timing.
-   **Findings**: Specific vulnerabilities or bugs discovered during a campaign. Each finding includes a summary, evidence (logs/crashes), and a suggested fix.
-   **ToolRuns**: Audit logs of every individual tool invocation during a campaign, including inputs, outputs, and execution duration.

### 2. Vault Authentication System
The Vault ensures that Faultline's tools can interact with secured endpoints.
-   **AuthFlows**: Configurations that define how to obtain credentials (either via a static token or by dynamically executing a login request).
-   **Extraction & Injection**: The Vault parses authentication responses, extracts tokens, and formats them for injection into HTTP headers or cookies during campaign execution.

### 3. Aegis Agent (LangGraph)
The heart of Faultline is the `AegisAgent`, built on LangGraph. It follows a cyclic workflow:
1.  **Observe**: Analyze the project structure and documentation.
2.  **Plan**: Decide which endpoints to test or attack based on the project map.
3.  **Execute**: Run functional tests or chaos payloads.
4.  **Analyze**: Correlate crashes with server logs and index new findings.
5.  **Heal**: Propose code patches for identified issues.

### 3. Skills Library
Skills are standalone Python modules that provide the "hands" for the agent. They are wrapped as LangChain tools for agent consumption.
-   **ASTGrapher**: Parses Python source code to build a semantic map of the target.
-   **Attacker**: Executes asynchronous HTTP chaos payloads with request ID tracing.
-   **Medic**: Manages the lifecycle of the target application (start/stop/health checks).
-   **SemanticIndexer**: Uses FAISS to index and search project documentation.

### 4. MCP Server
Faultline exposes its skills via a **Model Context Protocol (MCP)** server (`mcp_server.py`). This allows external agents (like Claude Desktop or Cursor) to leverage Faultline's specialized QA tools directly without going through the Django API.

## Data Flow: Starting a Campaign

1.  **Initiation**: A user sends a POST request to `/api/v1/campaign/start/`.
2.  **Setup**: The `CampaignService` starts the target application using `Medic` and runs the `ASTGrapher` to map the codebase.
3.  **Authentication**: If an `AuthFlow` is specified, the `Vault` executes it against the target application to retrieve session headers.
4.  **Agent Loop**: The `AegisAgent` is started, equipped with the session headers. It iteratively calls tools to explore the target.
5.  **Finding Recording**: When a tool detects an issue (e.g., a 500 error or a failed assertion), the agent creates a `Finding` record in the database.
6.  **Finalization**: Once the agent finishes, the service stops the target application and generates a comprehensive Markdown report summarizing the campaign.
