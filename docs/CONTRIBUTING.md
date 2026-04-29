# Contributing to Faultline

We welcome contributions to Faultline! This guide covers how to extend the system by adding new skills or modifying the agent workflow.

## Project Structure Recap

-   `core/`: Agent orchestration (`agent.py`), prompts (`prompts.py`), and tool definitions (`tools.py`).
-   `skills/`: The implementation logic for specific capabilities.
-   `campaigns/`: Django app for API and persistence.
-   `mcp_server.py`: MCP wrapper for the tools.

## Adding a New Skill

Skills are the modular building blocks of Faultline. To add a new one:

1.  **Implement the logic**: Create a new file in `skills/` (e.g., `skills/perf_analyzer.py`).
    ```python
    class PerfAnalyzer:
        def measure_latency(self, url: str):
            # implementation here
            return "Latency is 200ms"
    ```
2.  **Expose as a LangChain Tool**: Add the tool definition in `core/tools.py`.
    ```python
    from langchain.tools import tool
    from skills.perf_analyzer import PerfAnalyzer

    @tool
    def measure_endpoint_latency(url: str):
        """Measures the response latency of a given URL."""
        return PerfAnalyzer().measure_latency(url)
    ```
3.  **Register with the Agent**: Add the new tool to the `AegisAgent` tools list in `core/agent.py`.
4.  **Register with MCP (Optional)**: If you want the skill available via MCP, add a wrapper in `mcp_server.py`.

## Modifying the Agent Workflow

The `AegisAgent` uses **LangGraph** to define its state machine.

-   **State**: Defined in `AegisState` in `core/agent.py`.
-   **Nodes**: Individual steps like `map_project`, `run_tests`, `analyze_results`.
-   **Edges**: Logic that determines the next node based on current state (e.g., "if finding found, go to heal").

To change the agent's behavior, modify the graph definition in `AegisAgent.__init__`.

## Development Workflow

1.  **Install dev dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
2.  **Run Tests**:
    ```bash
    python manage.py test
    ```
3.  **Run Tool Smoke Tests**: Use the script to verify your new tool works in isolation.
    ```bash
    python scripts/test_tools.py
    ```

## Coding Standards

-   **Type Hints**: Use Python type hints for all function signatures.
-   **Docstrings**: All tools MUST have descriptive docstrings, as these are used by the LLM to understand when to call them.
-   **Logging**: Use the standard `logging` library. Use the component name as the logger name (e.g., `logger = logging.getLogger("MySkill")`).
