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
            model=model_name or "claude-3-5-sonnet-20240620",
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

class AegisAgent:
    def __init__(self):
        self.workflow = StateGraph(CampaignState)
        self._build_graph()

    def _build_graph(self):
        # 1. Define the Nodes
        self.workflow.add_node("agent", self.agent_node)
        
        # We use standard LangGraph prebuilt ToolNode
        tool_node = ToolNode(FAULTLINE_TOOLS)
        self.workflow.add_node("tools", tool_node)

        # 2. Define the Edges
        self.workflow.set_entry_point("agent")
        
        # Route between agent and tools
        self.workflow.add_conditional_edges(
            "agent",
            self.should_continue,
            {
                "continue": "tools",
                "end": END
            }
        )
        
        self.workflow.add_edge("tools", "agent")

        # 3. Compile the Graph
        self.app = self.workflow.compile()

    def should_continue(self, state: CampaignState) -> str:
        """
        Determines whether the agent needs to call a tool or if it's finished.
        """
        last_message = state["messages"][-1]
        # If there are tool calls, route to "tools"
        if getattr(last_message, "tool_calls", None):
            return "continue"
        # Otherwise, end
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
            # We return a message that will trigger 'end' in should_continue
            return {"messages": [AIMessage(content="LLM is not configured. Set FAULTLINE_PROVIDER and the matching API key or CLI login before running campaigns.", additional_kwargs={"finish_reason": "stop"})]}

        # Bind tools to the model
        model_with_tools = llm.bind_tools(FAULTLINE_TOOLS)
        
        # Add dynamic system context
        context_msg = SystemMessage(
            content=f"{SYSTEM_PROMPT}\n\nTarget Config:\n- Directory: {state.get('target_dir')}\n- URL: {state.get('target_url')}\n- Log File: {state.get('log_file')}\n\nYou must aggressively investigate the structure, validate attacks, and fire them."
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

    async def run_campaign(self, target_dir: str, target_url: str, log_file: str, initial_prompt: str = "Begin the chaos campaign against the target."):
        """
        Entry point to start the campaign stream.
        """
        initial_state = {
            "messages": [HumanMessage(content=initial_prompt)],
            "target_dir": target_dir,
            "target_url": target_url,
            "log_file": log_file,
        }
        
        async for event in self.app.astream(initial_state):
            for k, v in event.items():
                logger.info(f"Node '{k}' executed.")
        
        return "Campaign Completed."

if __name__ == "__main__":
    # Example usage:
    # import asyncio
    # agent = AegisAgent()
    # asyncio.run(agent.run_campaign(".", "http://localhost:8000", "server.log"))
    pass
