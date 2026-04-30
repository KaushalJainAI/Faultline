import os
import logging
import asyncio
from typing import TypedDict, Annotated
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
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        return ChatAnthropic(
            model=model_name or "claude-sonnet-4-5",
            anthropic_api_key=api_key,
            temperature=0.2
        )

    elif provider == "google":
        if not ChatGoogleGenerativeAI:
            logger.error("langchain-google-genai not installed")
            return None
        api_key = os.environ.get("GOOGLE_API_KEY")
        return ChatGoogleGenerativeAI(
            model=model_name or "gemini-2.0-flash-001",
            google_api_key=api_key,
            temperature=0.2
        )

    elif provider in {"openai", "openrouter"}:
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
    
    logger.error(f"Unknown provider: {provider}")
    return None

class CampaignState(TypedDict):
    """
    Standard LangGraph State containing messages and campaign context.
    add_messages ensures we append to the list rather than overwrite.
    """
    messages: Annotated[list[BaseMessage], add_messages]
    target_dir: str
    target_url: str
    log_file: str
    session_headers: dict

class AegisAgent:
    def __init__(self):
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
        """
        The main intelligence node. Binds tools to the LLM and generates a response.
        """
        logger.info("Phase: Agent Reasoning")
        cli_provider = get_cli_provider_name()
        if cli_provider:
            prompt = self._build_cli_prompt(state)
            manager = ProviderManager(target_dir=state.get("target_dir") or ".")
            response = await asyncio.to_thread(manager.run, cli_provider, prompt)
            return {"messages": [AIMessage(content=response)]}

        llm = build_llm()
        if not llm:
            logger.warning("LLM not configured. Agent returning dummy response.")
            return {"messages": [AIMessage(content="LLM is not configured. Set FAULTLINE_PROVIDER and the matching API key or CLI login before running campaigns.")]}

        # Bind tools to the model
        model_with_tools = llm.bind_tools(FAULTLINE_TOOLS)
        
        # Add dynamic system context
        session_headers = state.get('session_headers', {})
        header_str = "\n- Session Headers: " + str(session_headers) if session_headers else ""
        
        context_msg = SystemMessage(
            content=f"{SYSTEM_PROMPT}\n\nTarget Config:\n- Directory: {state.get('target_dir')}\n- URL: {state.get('target_url')}\n- Log File: {state.get('log_file')}{header_str}\n\nYou must aggressively investigate the structure, validate attacks, and fire them. If writing functional tests, include the Session Headers in your requests to bypass authentication."
        )
        
        messages = [context_msg] + state["messages"]
        
        response = await model_with_tools.ainvoke(messages)
        return {"messages": [response]}

    def _build_cli_prompt(self, state: CampaignState) -> str:
        latest_user_message = state["messages"][-1].content if state.get("messages") else "Begin the campaign."
        return (
            f"{SYSTEM_PROMPT}\n\n"
            "Target Config:\n"
            f"- Directory: {state.get('target_dir')}\n"
            f"- URL: {state.get('target_url')}\n"
            f"- Log File: {state.get('log_file')}\n\n"
            "Run the investigation from the target directory. Inspect the project, execute safe tests when feasible, "
            "summarize findings with evidence, and recommend fixes.\n\n"
            f"Campaign request: {latest_user_message}"
        )

    async def run_campaign(
        self,
        target_dir: str,
        target_url: str,
        log_file: str,
        session_headers: dict = None,
        initial_prompt: str = "Begin the chaos campaign against the target.",
        campaign_id: str = "local",
        renderer=None,
        hitl_manager=None,
    ):
        """
        Entry point to start the campaign stream.

        Optional CLI integration:
          renderer: a core.cli_ui.CLIRenderer instance — receives streamed
                    agent reasoning, tool calls, results, and findings.
          hitl_manager: a core.hitl.HITLManager instance — when provided,
                    destructive tools (e.g. execute_chaos_campaign) are gated
                    behind a synchronous permission prompt before they run.
        """
        from core.context import session_headers_var, chaos_vetoed_var
        from core.cli_ui import (
            extract_file_paths,
            extract_finding_title,
            summarize_args,
        )
        import json
        from pathlib import Path

        session_headers_var.set(session_headers or {})
        chaos_vetoed_var.set(False)

        initial_state = {
            "messages": [HumanMessage(content=initial_prompt)],
            "target_dir": target_dir,
            "target_url": target_url,
            "log_file": log_file,
            "session_headers": session_headers or {},
        }

        agent_log_path = Path("reports") / f"campaign_{campaign_id}_agent.log"
        agent_log_path.parent.mkdir(exist_ok=True)

        with open(agent_log_path, "a", encoding="utf-8") as f:
            f.write(f"=== Agent Campaign Started: {campaign_id} ===\n")
            f.write(f"Initial Prompt: {initial_prompt}\n")
            f.flush()

            async for event in self.app.astream(initial_state):
                for k, v in event.items():
                    logger.info(f"Node '{k}' executed.")

                    # ---- existing log-file write (unchanged behavior) ----
                    f.write(f"\n--- Node: {k} ---\n")
                    if "messages" in v:
                        for msg in v["messages"]:
                            f.write(f"[{msg.__class__.__name__}]: {msg.content}\n")
                            if hasattr(msg, "tool_calls") and msg.tool_calls:
                                f.write(f"Tool Calls: {json.dumps(msg.tool_calls, indent=2)}\n")
                    else:
                        f.write(f"State Update: {json.dumps(v, default=str)}\n")
                    f.flush()

                    # ---- new: CLI rendering + HITL gates ----
                    if renderer and "messages" in v:
                        for msg in v["messages"]:
                            if k == "agent":
                                if getattr(msg, "content", None):
                                    renderer.show_agent_thinking(msg.content)
                                tool_calls = getattr(msg, "tool_calls", None) or []
                                for tc in tool_calls:
                                    tool_name = tc.get("name", "unknown")
                                    args = tc.get("args", {}) or {}
                                    renderer.show_tool_call(
                                        tool_name, summarize_args(args)
                                    )
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

        return "Campaign Completed."
