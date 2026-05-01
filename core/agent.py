import json
import logging
import os
import asyncio
import re
import time
from pathlib import Path
from typing import TypedDict, Annotated, Optional

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage

try:
    from langchain_openai import ChatOpenAI
except ImportError:
    ChatOpenAI = None

try:
    from langchain_anthropic import ChatAnthropic
except ImportError:
    ChatAnthropic = None

try:
    from langchain_google_genai import ChatGoogleGenerativeAI
except ImportError:
    ChatGoogleGenerativeAI = None

from core.prompts import SYSTEM_PROMPT
from core.tools import FAULTLINE_TOOLS
from core.cli_provider import ProviderManager
from core.provider_config import get_cli_provider_name, get_provider

logger = logging.getLogger("AegisAgent")


def build_llm():
    provider = get_provider()
    model_name = os.environ.get("FAULTLINE_MODEL")

    if get_cli_provider_name(provider):
        return None

    if provider == "anthropic":
        if not ChatAnthropic:
            logger.error("langchain-anthropic not installed")
            return None
        return ChatAnthropic(
            model=model_name or "claude-sonnet-4-5",
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            temperature=0.2,
        )

    if provider == "google":
        if not ChatGoogleGenerativeAI:
            logger.error("langchain-google-genai not installed")
            return None
        return ChatGoogleGenerativeAI(
            model=model_name or "gemini-2.0-flash-001",
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
            temperature=0.2,
        )

    if provider in {"openai", "openrouter"}:
        if not ChatOpenAI:
            logger.error("langchain-openai not installed")
            return None
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        base_url = os.environ.get("OPENAI_API_BASE")
        if provider == "openrouter":
            api_key = os.environ.get("OPENROUTER_API_KEY")
            base_url = base_url or "https://openrouter.ai/api/v1"
        kwargs = {
            "model": model_name or ("google/gemini-2.0-flash-001" if provider == "openrouter" else "gpt-4o"),
            "openai_api_key": api_key,
            "openai_api_base": base_url,
            "temperature": 0.2,
        }
        if provider == "openrouter":
            kwargs["default_headers"] = {
                "HTTP-Referer": "https://github.com/faultline-chaos",
                "X-Title": "Faultline Aegis-Breaker",
            }
        return ChatOpenAI(**kwargs)

    logger.error("Unknown provider: %s", provider)
    return None


class CampaignState(TypedDict):
    """LangGraph state: messages + campaign context passed between nodes."""
    messages: Annotated[list[BaseMessage], add_messages]
    target_dir: str
    target_url: str
    log_file: str
    run_folder: str      # per-run output directory (absolute path string)
    session_headers: dict


def _load_api_schemas(run_folder: str) -> str:
    """
    Read api_schemas.json saved by the pipeline and return a compact
    system-prompt block the agent can use to write correctly-typed API tests.
    Returns an empty string if the file is missing or unreadable.
    """
    schema_path = Path(run_folder) / "api_schemas.json"
    if not schema_path.exists():
        return ""
    try:
        import json as _json
        schemas = _json.loads(schema_path.read_text(encoding="utf-8"))
        if not schemas:
            return ""
        lines = ["API Serializer Schemas (extracted from AST — use these to build request bodies):\n"]
        for s in schemas[:30]:   # cap at 30 serializers to stay within token budget
            fields = s.get("fields", [])
            req_fields = [f for f in fields if f.get("required")]
            opt_fields = [f for f in fields if not f.get("required")]
            lines.append(f"  {s['name']} ({s['file']}):")
            for f in req_fields:
                kw = ", ".join(f"{k}={v}" for k, v in (f.get("kwargs") or {}).items())
                lines.append(f"    [required] {f['name']}: {f['type']}" + (f" ({kw})" if kw else ""))
            for f in opt_fields:
                kw = ", ".join(f"{k}={v}" for k, v in (f.get("kwargs") or {}).items())
                lines.append(f"    [optional] {f['name']}: {f['type']}" + (f" ({kw})" if kw else ""))
            lines.append("")
        return "\n".join(lines) + "\n"
    except Exception:
        return ""


class AegisAgent:
    def __init__(self):
        self._renderer = None
        self.workflow = StateGraph(CampaignState)
        self._build_graph()

    def _build_graph(self):
        self.workflow.add_node("agent", self.agent_node)
        self.workflow.add_node("tools", ToolNode(FAULTLINE_TOOLS))
        self.workflow.set_entry_point("agent")
        self.workflow.add_conditional_edges(
            "agent",
            self.should_continue,
            {"continue": "tools", "end": END},
        )
        self.workflow.add_edge("tools", "agent")
        self.app = self.workflow.compile()

    def should_continue(self, state: CampaignState) -> str:
        last_message = state["messages"][-1]
        if getattr(last_message, "tool_calls", None):
            return "continue"
        return "end"

    async def agent_node(self, state: CampaignState):
        """Core reasoning node: delegates to CLI provider (multi-turn loop) or API provider (tool loop)."""
        logger.info("Phase: Agent Reasoning")
        renderer = self._renderer
        cli_provider = get_cli_provider_name()
        if cli_provider:
            manager = ProviderManager(target_dir=state.get("target_dir") or ".")
            conversation: list[dict] = []
            final_response = ""
            max_turns = int(os.environ.get("FAULTLINE_CLI_MAX_TURNS", "12"))
            completed_turns = 0

            async def _ticker(turn: int) -> None:
                """Print a live 'still working' line every 15 s while the CLI runs."""
                elapsed = 0
                while True:
                    await asyncio.sleep(15)
                    elapsed += 15
                    if renderer:
                        renderer.show_cli_waiting(elapsed, cli_provider)

            for turn in range(1, max_turns + 1):
                if renderer:
                    renderer.show_cli_turn(turn, max_turns, cli_provider)

                prompt = self._build_cli_prompt(state, conversation, turn - 1)

                # Run CLI call + live ticker in parallel; cancel ticker when call returns
                ticker_task = asyncio.create_task(_ticker(turn))
                try:
                    response = await asyncio.to_thread(manager.run, cli_provider, prompt)
                finally:
                    ticker_task.cancel()

                if response.startswith("Error:"):
                    logger.error("CLI provider error on turn %d: %s", turn, response)
                    if renderer:
                        renderer.show_cli_turn_error(turn, response)
                    if final_response:
                        if renderer:
                            renderer.show_message(
                                f"  Turn {turn} failed — keeping results from {completed_turns} earlier turn(s).",
                                style="yellow",
                            )
                    else:
                        final_response = (
                            f"CLI provider error on turn {turn}:\n{response}\n\n"
                            "Check that the CLI is installed and authenticated, "
                            "or set FAULTLINE_PROVIDER=anthropic with ANTHROPIC_API_KEY."
                        )
                    break

                conversation.append({"role": "assistant", "content": response})
                final_response = response
                completed_turns += 1
                done = "[DONE]" in response

                if renderer:
                    renderer.show_cli_turn_done(turn, done)
                    # Surface every markdown heading so the user can see what was covered
                    for heading in re.findall(r"^#{1,3}\s+(.+)", response, re.MULTILINE):
                        renderer.show_cli_section(heading.strip())
                    # Show a short prose excerpt
                    excerpt = response.replace("[DONE]", "").strip()
                    if excerpt:
                        renderer.show_agent_thinking(excerpt)

                logger.info("CLI turn %d complete (done=%s).", turn, done)

                if done or turn == max_turns:
                    break

                conversation.append({
                    "role": "user",
                    "content": (
                        "Continue your analysis. Go deeper on any areas you flagged. "
                        "End with [DONE] when fully complete."
                    ),
                })

            return {"messages": [AIMessage(content=final_response)]}

        llm = build_llm()
        if not llm:
            logger.warning("LLM not configured.")
            return {"messages": [AIMessage(content=(
                "LLM is not configured. Set FAULTLINE_PROVIDER and the matching API key "
                "or CLI login before running campaigns."
            ))]}

        model_with_tools = llm.bind_tools(FAULTLINE_TOOLS)

        session_headers = state.get("session_headers", {})
        run_folder = state.get("run_folder", "reports/")
        header_str = "\n- Session Headers: " + str(session_headers) if session_headers else ""

        # Inject API serializer schemas extracted by the pipeline (Step 4 feed)
        schema_str = _load_api_schemas(run_folder)

        context_msg = SystemMessage(content=(
            f"{SYSTEM_PROMPT}\n\n"
            f"Target Config:\n"
            f"- Directory: {state.get('target_dir')}\n"
            f"- URL: {state.get('target_url')}\n"
            f"- Log File: {state.get('log_file')}\n"
            f"- Run Folder: {run_folder}  ← write all test scripts and reports here\n"
            f"- Testcases Dir: {run_folder}/testcases/  ← boilerplate copies go here"
            f"{header_str}\n\n"
            f"{schema_str}"
            "Aggressively investigate the structure, validate attacks, and fire them. "
            "If writing functional tests, use the Session Headers in your requests to bypass authentication. "
            "Use the API Serializer Schemas above to generate correctly-typed request bodies — "
            "required fields must always be present, optional fields may be omitted or fuzzed. "
            "Save all generated test scripts to the Testcases Dir above."
        ))

        try:
            response = await model_with_tools.ainvoke([context_msg] + state["messages"])
            return {"messages": [response]}
        except Exception as exc:
            err_str = str(exc)
            logger.error("LLM call failed: %s", err_str)
            # Surface auth errors with clear guidance instead of crashing the run
            if "401" in err_str or "authentication" in err_str.lower() or "api key" in err_str.lower() or "user not found" in err_str.lower():
                return {"messages": [AIMessage(content=(
                    f"[LLM AUTH ERROR] The configured provider rejected the request: {err_str}\n\n"
                    "Fix: check your FAULTLINE_PROVIDER and matching API key in .env "
                    "(OPENROUTER_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY). "
                    "Alternatively set FAULTLINE_PROVIDER=claude to use the Claude CLI."
                ))]}
            # For rate limits / transient errors, surface and continue rather than crash
            return {"messages": [AIMessage(content=(
                f"[LLM ERROR] Provider call failed: {err_str}\n\n"
                "The agent will attempt to continue. If this repeats, check provider status and API key."
            ))]}

    def _build_cli_prompt(self, state: CampaignState, conversation: list, turn: int = 0) -> str:
        """Build a prompt for the current CLI turn, embedding conversation history."""
        latest = state["messages"][-1].content if state.get("messages") else "Begin the campaign."

        header = (
            f"{SYSTEM_PROMPT}\n\n"
            f"Target Config:\n"
            f"- Directory: {state.get('target_dir')}\n"
            f"- URL: {state.get('target_url')}\n"
            f"- Log File: {state.get('log_file')}\n\n"
        )

        if turn == 0:
            return (
                header
                + f"Campaign request: {latest}\n\n"
                "Respond conversationally as an expert security tester. "
                "Analyse the project, surface findings, and explain your reasoning. "
                "You may produce multiple sections. "
                "When you have covered everything, end your response with the token [DONE]."
            )

        # Build conversation history block
        history_parts: list[str] = []
        for msg in conversation:
            role = "You" if msg["role"] == "assistant" else "Operator"
            history_parts.append(f"[{role}]: {msg['content']}")
        history = "\n\n".join(history_parts)

        return (
            header
            + f"Campaign request: {latest}\n\n"
            "--- Conversation so far ---\n"
            f"{history}\n"
            "--- End of conversation ---\n\n"
            "Continue where you left off. Go deeper on any areas you flagged. "
            "End with [DONE] when you are fully satisfied with the analysis."
        )

    async def run_campaign(
        self,
        target_dir: str,
        target_url: str,
        log_file: str,
        run_folder: str = "reports/",
        session_headers: Optional[dict] = None,
        initial_prompt: str = "Begin the chaos campaign against the target.",
        campaign_id: str = "local",
        renderer=None,
        hitl_manager=None,
    ):
        """
        Streams the LangGraph agent loop, writing every event to a log file
        and forwarding relevant events to the CLIRenderer if provided.
        """
        from core.context import session_headers_var, chaos_vetoed_var
        from core.cli_ui import extract_file_paths, extract_finding_title, summarize_args

        self._renderer = renderer
        session_headers_var.set(session_headers or {})
        chaos_vetoed_var.set(False)

        # 1. Automated Boilerplate Setup (Step 4 DNA)
        # Instead of the LLM calling a tool, we copy core boilerplates to the run folder 
        # immediately so the agent has a starting point for editing.
        testcases_dir = Path(run_folder) / "testcases"
        testcases_dir.mkdir(parents=True, exist_ok=True)
        
        bp_src_dir = Path(__file__).resolve().parent.parent / "agent_assets" / "test_boilerplates"
        
        copied_paths = []
        if bp_src_dir.exists():
            import shutil
            for src in bp_src_dir.glob("*.py"):
                dest = testcases_dir / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
                copied_paths.append(str(dest.resolve()))

        initial_state = {
            "messages": [HumanMessage(content=initial_prompt)],
            "target_dir": target_dir,
            "target_url": target_url,
            "log_file": log_file,
            "run_folder": run_folder,
            "session_headers": session_headers or {},
        }

        # Agent log goes into the run folder
        agent_log_path = Path(run_folder) / "campaign_agent.log"
        agent_log_path.parent.mkdir(parents=True, exist_ok=True)

        iteration = 0
        agent_start = time.monotonic()

        with open(agent_log_path, "a", encoding="utf-8") as f:
            f.write(f"=== Agent Campaign Started: {campaign_id} ===\n")
            f.write(f"Initial Prompt: {initial_prompt}\n")
            f.flush()

            async for event in self.app.astream(initial_state):
                for k, v in event.items():
                    logger.info("Node '%s' executed.", k)

                    f.write(f"\n--- Node: {k} ---\n")
                    if "messages" in v:
                        for msg in v["messages"]:
                            f.write(f"[{msg.__class__.__name__}]: {msg.content}\n")
                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                f.write(f"Tool Calls: {json.dumps(msg.tool_calls, indent=2)}\n")
                    else:
                        f.write(f"State Update: {json.dumps(v, default=str)}\n")
                    f.flush()

                    if renderer and "messages" in v:
                        for msg in v["messages"]:
                            if k == "agent":
                                iteration += 1
                                renderer.show_agent_iteration(iteration)

                                if getattr(msg, "content", None):
                                    renderer.show_agent_thinking(msg.content)

                                for tc in (getattr(msg, "tool_calls", None) or []):
                                    tool_name = tc.get("name", "unknown")
                                    args = tc.get("args", {}) or {}
                                    renderer.show_tool_call(tool_name, summarize_args(args))

                                    if tool_name == "execute_chaos_campaign" and hitl_manager:
                                        from core.hitl import async_request_permission
                                        try:
                                            payloads = json.loads(args.get("payloads_json", "[]"))
                                            count = len(payloads) if isinstance(payloads, list) else 0
                                        except Exception:
                                            count = 0
                                        renderer.show_hitl_request(
                                            f"execute_chaos_campaign will fire {count} payload(s) "
                                            f"at {args.get('target_url', target_url)}"
                                        )
                                        approved = await async_request_permission(
                                            "execute_chaos_campaign",
                                            f"Fire {count} HTTP attack payload(s) at "
                                            f"{args.get('target_url', target_url)}"
                                        )
                                        if not approved:
                                            chaos_vetoed_var.set(True)
                                            renderer.show_message(
                                                "  Chaos campaign vetoed by operator.",
                                                style="bold red",
                                            )

                            elif k == "tools":
                                tool_name = getattr(msg, "name", "tool")
                                result_text = str(getattr(msg, "content", ""))
                                renderer.show_tool_result(tool_name, result_text)
                                for path in extract_file_paths(result_text):
                                    renderer.show_file_generated(path)
                                if tool_name == "record_finding":
                                    title = extract_finding_title(result_text)
                                    if title:
                                        renderer.show_finding("medium", title)

        if renderer:
            renderer.show_phase_timing("Agent phase")

        return "Campaign Completed."
