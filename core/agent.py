import os
import json
import logging
from typing import Dict, TypedDict, List, Annotated
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage

# OpenRouter configuration
try:
    from langchain_openai import ChatOpenAI
    import os
    
    # Model can be any OpenRouter supported model (e.g., 'google/gemini-pro-1.5-exp', 'anthropic/claude-3.5-sonnet')
    llm = ChatOpenAI(
        model="google/gemini-flash-1.5", 
        openai_api_key=os.environ.get("OPENROUTER_API_KEY", "dummy_key"),
        openai_api_base="https://openrouter.ai/api/v1",
        default_headers={
            "HTTP-Referer": "https://github.com/faultline-chaos", # Required by OpenRouter
            "X-Title": "Faultline Aegis-Breaker",               # Required by OpenRouter
        },
        temperature=0.7
    )
except ImportError:
    llm = None

from core.prompts import SYSTEM_PROMPT
from core.tools import FAULTLINE_TOOLS

logger = logging.getLogger("AegisAgent")

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
        if not llm:
            logger.warning("LLM not configured. Agent returning dummy response.")
            return {"messages": [AIMessage(content="LLM is not installed. Please configure ChatOpenAI.")]}

        # Bind tools to the model
        model_with_tools = llm.bind_tools(FAULTLINE_TOOLS)
        
        # Add dynamic system context
        context_msg = SystemMessage(
            content=f"{SYSTEM_PROMPT}\n\nTarget Config:\n- Directory: {state.get('target_dir')}\n- URL: {state.get('target_url')}\n- Log File: {state.get('log_file')}\n\nYou must aggressively investigate the structure, validate attacks, and fire them."
        )
        
        messages = [context_msg] + state["messages"]
        
        response = await model_with_tools.ainvoke(messages)
        return {"messages": [response]}

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
