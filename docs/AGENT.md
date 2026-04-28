# The Aegis-Breaker Agent (Core Orchestration)

The "Brain" of Faultline is a state machine powered by **LangGraph**. It orchestrates the flow from initial project discovery to final vulnerability reporting.

## 🔄 The State Machine Workflow

1.  **`index_project`**: The agent uses the Cartographer skill to build a JSON representation of the target project's topology.
2.  **`select_target`**: The agent analyzes the project map and documentation (via Semantic Indexer) to identify high-risk endpoints (e.g., chat processing, database commits).
3.  **`draft_attack`**: The LLM generates a series of adversarial payloads (Type confusion, SQLi, Null injections) tailored to the target's schema.
4.  **`validate_payloads`**: The generated attacks are passed through the Guardrail skill. If the AI hallucinations invalid code or non-existent endpoints, the flow loops back to `draft_attack` for a fix.
5.  **`execute_attack`**: The agent triggers the Siege Engine (high-concurrency bursts) while the Coroner monitors the logs for tracebacks.
6.  **`synthesize_report`**: All data is consolidated into a Markdown report, linking each crash traceback to the specific payload and code line that caused it.

## 🧠 Memory & Context
The agent maintains a `CampaignState` dictionary that tracks:
- **`project_graph`**: Structural map.
- **`generated_payloads`**: List of planned attacks.
- **`attack_results`**: Status codes and response times.
- **`crashes`**: Correlated tracebacks from the server logs.

## ⚖️ Self-Healing Loop
If the agent detects that it has crashed the target server, it pauses the attack, waits for the **Medic** skill to resurrect the server, and then continues the campaign from where it left off.
