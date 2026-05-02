"""
Faultline Aegis-Breaker Agent Orchestration.
This module defines the LangGraph-based agent workflow, LLM provider management,
budget enforcement, and campaign execution logic.
"""
import json
import logging
import os
import asyncio
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, Annotated, Optional

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage
from core.content_manager import build_tiered_context

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

from core.prompts import SYSTEM_PROMPT, VISION_REMINDER
from core.tools import FAULTLINE_TOOLS
from core.cli_provider import ProviderManager
from core.provider_config import get_cli_provider_name, get_provider
from core.checkpoint import save_checkpoint, load_checkpoint
from core.model_registry import get_active_model, set_active_model, find_model
from core.progress_tracker import ProgressTracker

logger = logging.getLogger("AegisAgent")

CALL_TIMEOUT_S: int = int(os.environ.get("FAULTLINE_CALL_TIMEOUT", "300"))


def _recent_step_coverage(run_folder: str) -> str:
    """Read vision_step values from findings.jsonl to feed back to the agent."""
    if not run_folder:
        return "none yet"
    p = Path(run_folder) / "findings.jsonl"
    if not p.exists():
        return "none yet"
    steps = set()
    try:
        for line in p.read_text(encoding="utf-8").splitlines()[-100:]:
            try:
                d = json.loads(line)
                step = d.get("vision_step")
                if isinstance(step, int):
                    steps.add(step)
            except Exception:
                pass
    except Exception:
        return "none yet"
    return ", ".join(str(s) for s in sorted(steps)) if steps else "none yet"


# ---------------------------------------------------------------------------
# LLM call helpers: streaming accumulator + call log
# ---------------------------------------------------------------------------

def _log_llm_call(run_folder: str, content: str, timed_out: bool, elapsed: float) -> None:
    """Append one LLM call record to <run_folder>/llm_calls.log."""
    if not run_folder:
        return
    try:
        log_path = Path(run_folder) / "llm_calls.log"
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        token_est = len(content) // 4
        status = "TIMEOUT" if timed_out else "OK"
        header = f"[{ts}] status={status} elapsed={elapsed:.1f}s tokens_est={token_est}\n"
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(header)
            f.write(content[:4000])   # cap per-entry size
            f.write("\n---\n")
    except Exception:
        pass


async def _stream_with_timeout(model, messages, timeout: int, run_folder: str):
    """
    Stream model tokens into RAM and return a merged AIMessage.
    Writes to llm_calls.log on completion or timeout.
    On timeout, raises asyncio.TimeoutError so the caller can handle recovery.
    """
    from langchain_core.messages.ai import AIMessageChunk

    chunks: list[AIMessageChunk] = []
    start = time.monotonic()

    try:
        async with asyncio.timeout(timeout):
            async for chunk in model.astream(messages):
                chunks.append(chunk)
    except (asyncio.TimeoutError, TimeoutError):
        elapsed = time.monotonic() - start
        # Merge whatever we have so far into a partial message
        partial_content = "".join(
            c.content for c in chunks if isinstance(c.content, str)
        )
        _log_llm_call(run_folder, partial_content, timed_out=True, elapsed=elapsed)
        raise asyncio.TimeoutError(
            f"LLM call timed out after {elapsed:.0f}s"
        )

    elapsed = time.monotonic() - start

    # Merge chunks — prefer LangChain's built-in reducer, fall back to manual
    if chunks:
        try:
            merged = chunks[0]
            for c in chunks[1:]:
                merged = merged + c
        except Exception:
            from langchain_core.messages import AIMessage as _AIMsg
            merged = _AIMsg(content="".join(
                c.content for c in chunks if isinstance(c.content, str)
            ))
    else:
        from langchain_core.messages import AIMessage as _AIMsg
        merged = _AIMsg(content="")

    # SiliconFlow (and some OpenRouter) thinking-mode models return
    # reasoning_content alongside content. When present, it MUST be echoed
    # back verbatim on the next API call or SiliconFlow raises error 20015.
    # The LangChain chunk reducer may mis-merge it (string concat vs. dict
    # merge semantics differ across versions), so we reconstruct it manually
    # from all chunks that carry it.
    _reasoning_parts: list[str] = []
    for _c in chunks:
        _rc = (getattr(_c, "additional_kwargs", None) or {}).get("reasoning_content")
        if isinstance(_rc, str) and _rc:
            _reasoning_parts.append(_rc)
    if _reasoning_parts:
        if not isinstance(getattr(merged, "additional_kwargs", None), dict):
            merged.additional_kwargs = {}
        merged.additional_kwargs["reasoning_content"] = "".join(_reasoning_parts)

    content_str = merged.content if isinstance(merged.content, str) else str(merged.content)
    _log_llm_call(run_folder, content_str, timed_out=False, elapsed=elapsed)
    return merged


# ---------------------------------------------------------------------------
# Budget configuration
# ---------------------------------------------------------------------------

REASONING_PROFILES = {
    "fast": {
        "max_output_tokens": 1024,
        "instruction": (
            "SPEED MODE: You have a tight token budget. Be extremely concise — "
            "one sentence of reasoning max per step. Skip sub-tasks you can infer. "
            "Prioritize the 2 most impactful actions and stop."
        ),
    },
    "normal": {
        "max_output_tokens": 4096,
        "instruction": (
            "NORMAL MODE: Balance thoroughness with efficiency. "
            "Keep reasoning to 2–3 sentences per step."
        ),
    },
    "deep": {
        "max_output_tokens": 8192,
        "instruction": (
            "DEEP MODE: Think carefully before each step. "
            "Show your full reasoning chain and cover edge cases."
        ),
    },
}


@dataclass
class BudgetConfig:
    """Runtime spending limits for a single campaign run."""
    max_llm_calls: int = int(os.environ.get("FAULTLINE_MAX_LLM_CALLS", "40"))
    max_tool_calls: int = int(os.environ.get("FAULTLINE_MAX_TOOL_CALLS", "120"))
    max_input_tokens: int = int(os.environ.get("FAULTLINE_MAX_TOKENS", "500000"))
    max_output_tokens: int = int(os.environ.get("FAULTLINE_MAX_OUTPUT_TOKENS", "4096"))
    reasoning_level: str = os.environ.get("FAULTLINE_REASONING_LEVEL", "normal")

    def __post_init__(self):
        if self.reasoning_level not in REASONING_PROFILES:
            self.reasoning_level = "normal"
        profile_tokens = REASONING_PROFILES[self.reasoning_level]["max_output_tokens"]
        # If user didn't explicitly override output tokens via env, use profile default
        env_override = "FAULTLINE_MAX_OUTPUT_TOKENS" in os.environ
        if not env_override:
            self.max_output_tokens = profile_tokens

    @property
    def reasoning_instruction(self) -> str:
        return REASONING_PROFILES[self.reasoning_level]["instruction"]

    def budget_prompt_block(self, llm_used: int, tool_used: int) -> str:
        return (
            "\n═══════════════════════════════════════════════════════════════════════════════\n"
            "REAL-WORLD BUDGET CONSTRAINTS  ← read this before every action\n"
            "═══════════════════════════════════════════════════════════════════════════════\n\n"
            f"You are operating under a HARD budget. Every LLM call and tool call costs real money and time.\n\n"
            f"  Reasoning level : {self.reasoning_level.upper()}\n"
            f"  LLM calls used  : {llm_used} / {self.max_llm_calls}  "
            f"({'STOP NOW — over budget!' if llm_used >= self.max_llm_calls else f'{self.max_llm_calls - llm_used} remaining'})\n"
            f"  Tool calls used : {tool_used} / {self.max_tool_calls}  "
            f"({'STOP NOW — over budget!' if tool_used >= self.max_tool_calls else f'{self.max_tool_calls - tool_used} remaining'})\n"
            f"  Max output/call : {self.max_output_tokens} tokens\n"
            f"  Max context     : {self.max_input_tokens:,} tokens\n\n"
            f"{self.reasoning_instruction}\n\n"
            "RULES:\n"
            "- Do NOT repeat work already done.\n"
            "- Do NOT call a tool just to confirm something you already know.\n"
            "- If you are close to the LLM or tool call limit, finish the most critical task and write a short summary.\n"
            "- When you hit [DONE] or the budget runs out, stop immediately.\n"
            "═══════════════════════════════════════════════════════════════════════════════\n"
        )


def build_llm(model_override: Optional[str] = None, provider_override: Optional[str] = None,
              max_tokens: Optional[int] = None):
    """Build the LLM instance. Supports runtime overrides from /model command."""
    # Check for runtime model override first
    rt_model, rt_provider = get_active_model()
    model_name = model_override or rt_model or os.environ.get("FAULTLINE_MODEL")
    provider = provider_override or rt_provider or get_provider()

    if get_cli_provider_name(provider):
        return None

    if provider == "anthropic":
        if not ChatAnthropic:
            logger.error("langchain-anthropic not installed")
            return None
        kwargs = dict(
            model=model_name or "claude-sonnet-4-5",
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            temperature=0.2,
        )
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        return ChatAnthropic(**kwargs)

    if provider == "google":
        if not ChatGoogleGenerativeAI:
            logger.error("langchain-google-genai not installed")
            return None
        kwargs = dict(
            model=model_name or "gemini-2.0-flash-001",
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
            temperature=0.2,
        )
        if max_tokens:
            kwargs["max_output_tokens"] = max_tokens
        return ChatGoogleGenerativeAI(**kwargs)

    if provider in {"openai", "openrouter"}:
        if not ChatOpenAI:
            logger.error("langchain-openai not installed — cannot use provider '%s'", provider)
            return None
        api_key = os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        base_url = (os.environ.get("OPENAI_API_BASE") or "").strip()
        if provider == "openrouter":
            api_key = os.environ.get("OPENROUTER_API_KEY")
            base_url = base_url or "https://openrouter.ai/api/v1"
        kwargs = {
            "model": model_name or ("moonshotai/kimi-k2.6" if provider == "openrouter" else "gpt-4o"),
            "openai_api_key": api_key,
            "openai_api_base": base_url,
            "temperature": 0.2,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
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
        max_schemas = int(os.environ.get("FAULTLINE_MAX_SCHEMAS", "30"))
        for s in schemas[:max_schemas]:
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
    def __init__(self, budget: Optional[BudgetConfig] = None):
        self._renderer = None
        self._budget = budget or BudgetConfig()
        self._llm_calls_used = 0
        self._tool_calls_used = 0
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
        tool_calls = getattr(last_message, "tool_calls", None)
        if not tool_calls:
            return "end"

        # Count tool calls and enforce limit
        n = len(tool_calls)
        if self._tool_calls_used + n > self._budget.max_tool_calls:
            remaining = self._budget.max_tool_calls - self._tool_calls_used
            logger.warning(
                "Budget: tool call limit reached (%d/%d). Stopping.",
                self._tool_calls_used, self._budget.max_tool_calls,
            )
            if self._renderer and remaining <= 0:
                self._renderer.show_message(
                    f"  [Budget] Tool call limit reached "
                    f"({self._tool_calls_used}/{self._budget.max_tool_calls}). Stopping.",
                    style="bold red",
                )
            return "end"

        self._tool_calls_used += n
        if self._renderer:
            self._renderer.show_message(
                f"  [Budget] Tool calls: {self._tool_calls_used}/{self._budget.max_tool_calls}  "
                f"LLM calls: {self._llm_calls_used}/{self._budget.max_llm_calls}",
                style="dim",
            )
        return "continue"

    async def agent_node(self, state: CampaignState):
        """Core reasoning node: delegates to CLI provider (multi-turn loop) or API provider (tool loop)."""
        logger.info("Phase: Agent Reasoning")
        renderer = self._renderer
        budget = self._budget

        # Hard-stop on LLM call budget
        self._llm_calls_used += 1
        if self._llm_calls_used > budget.max_llm_calls:
            msg = (
                f"[Budget Exhausted] LLM call limit reached "
                f"({budget.max_llm_calls}). Campaign stopped to stay within budget.\n\n"
                "Tip: re-run with --max-llm-calls N or --reasoning-level fast to get more out of fewer calls."
            )
            if renderer:
                renderer.show_message(f"  [Budget] LLM call limit hit — stopping.", style="bold red")
            return {"messages": [AIMessage(content=msg)]}

        cli_provider = get_cli_provider_name()
        if cli_provider:
            manager = ProviderManager(target_dir=state.get("target_dir") or ".")
            conversation: list[dict] = []
            final_response = ""
            # Respect max_llm_calls: remaining CLI turns = budget minus calls already spent
            max_turns = min(
                int(os.environ.get("FAULTLINE_CLI_MAX_TURNS", "12")),
                max(1, budget.max_llm_calls - self._llm_calls_used + 1),
            )
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
                _cli_run_folder = state.get("run_folder", "")
                _turn_start = time.monotonic()
                try:
                    response = await asyncio.wait_for(
                        asyncio.to_thread(manager.run, cli_provider, prompt),
                        timeout=CALL_TIMEOUT_S,
                    )
                    _log_llm_call(
                        _cli_run_folder, response,
                        timed_out=False, elapsed=time.monotonic() - _turn_start,
                    )
                except asyncio.TimeoutError:
                    elapsed = time.monotonic() - _turn_start
                    _log_llm_call(_cli_run_folder, "[TIMEOUT]", timed_out=True, elapsed=elapsed)
                    logger.warning("CLI provider timed out on turn %d after %.0fs.", turn, elapsed)
                    if renderer:
                        renderer.show_message(
                            f"  [Timeout] CLI provider did not respond in {CALL_TIMEOUT_S}s — skipping turn.",
                            style="bold yellow",
                        )
                    response = (
                        f"[TIMEOUT] CLI provider did not respond in {CALL_TIMEOUT_S}s. "
                        "Please be more concise next turn."
                    )
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

        llm = build_llm(max_tokens=budget.max_output_tokens)
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

        budget_block = budget.budget_prompt_block(self._llm_calls_used, self._tool_calls_used)

        context_msg = SystemMessage(content=(
            f"{SYSTEM_PROMPT}\n\n"
            f"{budget_block}\n"
            f"Target Config:\n"
            f"- Directory: {state.get('target_dir')}\n"
            f"- URL: {state.get('target_url')}\n"
            f"- Log File: {state.get('log_file')}\n"
            f"- Run Folder: {run_folder}  ← write all test scripts and reports here\n"
            f"- Testcases Dir: {run_folder}/testcases/  ← boilerplate copies go here\n"
            f"- API Test Data: {run_folder}/api_test_data.json  ← read with read_run_folder_file; update as you discover endpoints\n"
            f"- Transcript: {run_folder}/transcript.txt  ← human-readable conversation log\n"
            f"{header_str}\n\n"
            f"{schema_str}"
            "Aggressively investigate the structure, validate attacks, and fire them. "
            "If writing functional tests, use the Session Headers in your requests to bypass authentication. "
            "Use the API Serializer Schemas above to generate correctly-typed request bodies — "
            "required fields must always be present, optional fields may be omitted or fuzzed. "
            "Save all generated test scripts to the Testcases Dir above.\n\n"
            "Run-folder tools: use list_run_folder_files to discover what has been generated, "
            "read_run_folder_file to inspect any file (api_schemas.json, api_test_data.json, test scripts), "
            "and summarize_to_report to append intermediate progress notes to live_report.md."
        ))

        # Standard LLM (Tool-Calling) Loop with Spinner
        async def _llm_ticker() -> None:
            elapsed = 0
            while True:
                await asyncio.sleep(10)
                elapsed += 10
                if renderer:
                    renderer.show_cli_waiting(elapsed, f"Agent ({llm.model_name})")

        ticker_task = asyncio.create_task(_llm_ticker())
        try:
            tiered_msgs, cm_stats = build_tiered_context(
                system_msg=context_msg,
                messages=state["messages"],
                run_folder=state.get("run_folder", ""),
                max_tokens=budget.max_input_tokens,
            )

            # Vision guardrail — re-anchor the LLM after turn 1 so the long
            # tool-output history can't bury the original objective.
            if self._llm_calls_used >= 2:
                _coverage = _recent_step_coverage(state.get("run_folder", ""))
                _reminder = VISION_REMINDER + (
                    f"\nRecent step coverage: {_coverage}.\n"
                )
                tiered_msgs = [SystemMessage(content=_reminder), *tiered_msgs]
            if cm_stats["windowing_applied"]:
                logger.info(
                    "content_manager: %d→%d est. tokens | cycles: %d total, "
                    "%d t1, %d t2, %d compressed, %d dropped",
                    cm_stats["total_input_tokens_est"],
                    cm_stats["output_tokens_est"],
                    cm_stats["cycles_total"],
                    cm_stats["cycles_in_tier1"],
                    cm_stats["cycles_in_tier2"],
                    cm_stats["cycles_compressed"],
                    cm_stats["cycles_dropped"],
                )
                _run_folder = state.get("run_folder", "")
                if _run_folder:
                    try:
                        _agent_log = Path(_run_folder) / "campaign_agent.log"
                        with open(_agent_log, "a", encoding="utf-8") as _f:
                            _f.write(
                                f"[content_manager] {cm_stats['total_input_tokens_est']:,}→"
                                f"{cm_stats['output_tokens_est']:,} tokens | "
                                f"cycles {cm_stats['cycles_total']} total, "
                                f"{cm_stats['cycles_in_tier1']} t1, "
                                f"{cm_stats['cycles_in_tier2']} t2, "
                                f"{cm_stats['cycles_compressed']} compressed, "
                                f"{cm_stats['cycles_dropped']} dropped\n"
                            )
                    except Exception:
                        pass
            _run_folder = state.get("run_folder", "")
            try:
                response = await _stream_with_timeout(
                    model_with_tools, tiered_msgs,
                    timeout=CALL_TIMEOUT_S,
                    run_folder=_run_folder,
                )
            except asyncio.TimeoutError as te:
                logger.warning("LLM call timed out: %s", te)
                if renderer:
                    renderer.show_message(
                        f"  [Timeout] LLM did not respond in {CALL_TIMEOUT_S}s — injecting recovery hint.",
                        style="bold yellow",
                    )
                return {"messages": [AIMessage(content=(
                    f"[TIMEOUT] The previous LLM call exceeded {CALL_TIMEOUT_S}s and was cancelled. "
                    "For the next step: be extremely concise, pick ONE action only, and avoid large outputs."
                ))]}
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
        finally:
            ticker_task.cancel()
            if renderer and hasattr(renderer, "_status"):
                renderer._status.stop()

        return {"messages": [response]}

    def _build_cli_prompt(self, state: CampaignState, conversation: list, turn: int = 0) -> str:
        """Build a prompt for the current CLI turn, embedding conversation history."""
        latest = state["messages"][-1].content if state.get("messages") else "Begin the campaign."
        budget_block = self._budget.budget_prompt_block(self._llm_calls_used, self._tool_calls_used)

        header = (
            f"{SYSTEM_PROMPT}\n\n"
            f"{budget_block}\n"
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
        input_handler=None,
        resumed_messages: Optional[list] = None,
        mode: str = "hybrid",
        session_store=None,
    ):
        """
        Streams the LangGraph agent loop, writing every event to a log file
        and forwarding relevant events to the CLIRenderer if provided.

        Supports:
        - Esc-to-pause with steering room (via input_handler)
        - Checkpoint after every turn (auto-save to run_folder)
        - Model hot-swap via /model command
        - Resume from checkpoint (via resumed_messages)
        - Session JSONL logging (via session_store)
        """
        from core.context import session_headers_var, chaos_vetoed_var, live_report_var
        from core.cli_ui import extract_file_paths, extract_finding_title, summarize_args
        from core.input_handler import ActionType
        from core.live_report import LiveReport

        self._renderer = renderer
        session_headers_var.set(session_headers or {})
        chaos_vetoed_var.set(False)

        # Initialise the live report (no-op if file already exists from a previous session)
        # Only pass pipeline_report_path when the pipeline actually runs (pipeline/hybrid).
        # In agent-only mode the file is never created, so passing it would show "not found".
        _pipeline_report_path = (
            str(Path(run_folder) / "pipeline_report.md")
            if mode in ("pipeline", "hybrid")
            else ""
        )
        _live_report = LiveReport(
            run_folder=run_folder,
            target_dir=target_dir,
            target_url=target_url,
            mode=mode,
            pipeline_report_path=_pipeline_report_path,
        )
        live_report_var.set(_live_report)

        # 1. Automated Boilerplate Setup (Step 4 DNA)
        testcases_dir = Path(run_folder) / "testcases"
        testcases_dir.mkdir(parents=True, exist_ok=True)

        # Create api_test_data.json template if it doesn't exist.
        # The agent can read this with read_run_folder_file and populate it
        # with discovered POST payloads and expected GET responses.
        _api_test_data_path = Path(run_folder) / "api_test_data.json"
        if not _api_test_data_path.exists():
            _api_test_data_template = {
                "_instructions": (
                    "Populate this file with endpoint-specific test fixtures. "
                    "Use read_run_folder_file to read it and update via summarize_to_report "
                    "or by writing a test script that references these values."
                ),
                "endpoints": [
                    {
                        "endpoint": "/api/example/",
                        "method": "POST",
                        "post_data": {"field1": "value1", "field2": "value2"},
                        "expected_status": 201,
                        "expected_get_response": {"id": 1, "field1": "value1"},
                    }
                ],
            }
            _api_test_data_path.write_text(
                json.dumps(_api_test_data_template, indent=2), encoding="utf-8"
            )

        bp_src_dir = Path(__file__).resolve().parent.parent / "agent_assets" / "test_boilerplates"

        copied_paths = []
        if bp_src_dir.exists():
            import shutil
            for src in bp_src_dir.glob("*.py"):
                dest = testcases_dir / src.name
                if not dest.exists():
                    shutil.copy2(src, dest)
                copied_paths.append(str(dest.resolve()))

        # Use resumed messages if available, otherwise start fresh
        if resumed_messages:
            initial_messages = resumed_messages
        else:
            initial_messages = [HumanMessage(content=initial_prompt)]

        initial_state = {
            "messages": initial_messages,
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
        findings_count = 0
        agent_start = time.monotonic()

        # Checkpoint debouncing — write every N iterations or M seconds, not every event
        _CHECKPOINT_INTERVAL_TURNS = int(os.environ.get("FAULTLINE_CHECKPOINT_INTERVAL", "5"))
        _CHECKPOINT_INTERVAL_SECS = 30.0
        _last_checkpoint_turn = 0
        _last_checkpoint_time = agent_start

        # Progress tracker — keeps the agent aware of its plan, budget, and progress
        max_turns = int(os.environ.get("FAULTLINE_MAX_TURNS", "40"))
        token_budget = int(os.environ.get("FAULTLINE_TOKEN_BUDGET", "120000"))
        tracker = ProgressTracker(
            max_turns=max_turns,
            token_budget=token_budget,
            start_time=agent_start,
        )

        # Authoritative accumulated message list.
        # LangGraph's astream(updates mode) emits per-node deltas, not the full
        # accumulated state.  We maintain this list separately so checkpoints
        # and steering-restarts always have the complete conversation history.
        accumulated_messages: list = list(initial_state.get("messages", []))

        # Start the input handler for Esc key detection
        if input_handler:
            input_handler.start()

        transcript_path = Path(run_folder) / "transcript.txt"

        def _write_transcript(role: str, text: str) -> None:
            """Append one clean, human-readable line to transcript.txt."""
            try:
                ts = time.strftime("%H:%M:%S")
                header = f"[{ts}] {role}"
                body = str(text or "").strip()
                with open(transcript_path, "a", encoding="utf-8") as _tf:
                    _tf.write(f"\n{'─' * 60}\n{header}\n{'─' * 60}\n{body}\n")
                    _tf.flush()
            except Exception:
                pass

        # Write transcript header before opening the log (header is one-shot)
        with open(transcript_path, "a", encoding="utf-8") as _tf:
            _tf.write(
                f"{'═' * 60}\n"
                f"Faultline Transcript — Campaign: {campaign_id}\n"
                f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Target: {target_url or target_dir}\n"
                f"{'═' * 60}\n"
            )
        _write_transcript("Operator", initial_prompt)

        with open(agent_log_path, "a", encoding="utf-8") as f:
            f.write(f"=== Agent Campaign Started: {campaign_id} ===\n")
            f.write(f"Initial Prompt: {initial_prompt}\n")
            f.flush()

            should_restart = True
            while should_restart:
                should_restart = False

                # Sync initial_state from the authoritative accumulated list so
                # each restart (steering, model-swap, resume) begins with the
                # full conversation history rather than just the last event delta.
                initial_state["messages"] = list(accumulated_messages)

                # Queue-based real-time streaming:
                # - Producer: pushes events from astream into a queue
                # - Consumer: processes events live (render + log + session)
                # - Esc check: runs concurrently to detect pause requests
                event_queue: asyncio.Queue = asyncio.Queue()
                stream_done = asyncio.Event()

                async def _producer():
                    """Push LangGraph events into the queue as they arrive."""
                    try:
                        async for event in self.app.astream(initial_state):
                            await event_queue.put(event)
                    except asyncio.CancelledError:
                        pass
                    finally:
                        stream_done.set()
                        await event_queue.put(None)  # Sentinel

                producer_task = asyncio.create_task(_producer())

                try:
                    while True:
                        # Check for Esc pause
                        if input_handler and input_handler.pause_requested.is_set():
                            producer_task.cancel()
                            try:
                                await producer_task
                            except (asyncio.CancelledError, Exception):
                                pass

                            elapsed = time.monotonic() - agent_start
                            active_model = ""
                            rt_model, _ = get_active_model()
                            if rt_model:
                                active_model = rt_model

                            action = await input_handler.enter_steering_room(
                                turn=iteration,
                                findings_count=findings_count,
                                elapsed_seconds=elapsed,
                                active_model=active_model,
                            )

                            if action.type == ActionType.QUIT:
                                save_checkpoint(
                                    run_folder=run_folder,
                                    messages=accumulated_messages,
                                    turn=iteration,
                                    target_dir=target_dir,
                                    target_url=target_url,
                                    log_file=log_file,
                                    mode=mode,
                                    pipeline_completed=True,
                                    session_headers=session_headers,
                                )
                                if renderer:
                                    renderer.show_message(
                                        f"  Checkpoint saved at turn {iteration}. "
                                        f"Resume with: python faultline.py --resume {run_folder}",
                                        style="green",
                                    )
                                f.write(f"\n=== Campaign paused by operator at turn {iteration} ===\n")
                                return "Campaign paused. Checkpoint saved."

                            elif action.type == ActionType.SKIP:
                                f.write(f"\n=== Agent phase skipped by operator at turn {iteration} ===\n")
                                if renderer:
                                    renderer.show_message("  Agent phase skipped.", style="yellow")
                                return "Agent phase skipped."

                            elif action.type == ActionType.SAVE:
                                ckpt_path = save_checkpoint(
                                    run_folder=run_folder,
                                    messages=accumulated_messages,
                                    turn=iteration,
                                    target_dir=target_dir,
                                    target_url=target_url,
                                    log_file=log_file,
                                    mode=mode,
                                    pipeline_completed=True,
                                    session_headers=session_headers,
                                )
                                if renderer:
                                    renderer.show_message(f"  Checkpoint saved: {ckpt_path}", style="green")
                                input_handler.resume_polling()
                                should_restart = True
                                break

                            elif action.type == ActionType.STEER:
                                steering_msg = HumanMessage(
                                    content=f"[OPERATOR] {action.text}"
                                )
                                accumulated_messages.append(steering_msg)
                                f.write(f"\n=== Operator steering: {action.text} ===\n")
                                _write_transcript("Operator (steering)", action.text)
                                if session_store:
                                    session_store.append(steering_msg)
                                    session_store.append_event("steering", {"text": action.text})
                                if renderer:
                                    renderer.show_message(
                                        f"  Steering injected: {action.text[:80]}",
                                        style="cyan",
                                    )
                                input_handler.resume_polling()
                                should_restart = True
                                break

                            elif action.type == ActionType.MODEL:
                                m = find_model(action.model_value)
                                if m:
                                    set_active_model(m.value, m.provider)
                                    self._rebuild_graph()
                                    f.write(f"\n=== Model switched to {m.value} ===\n")
                                    if session_store:
                                        session_store.append_event("model_switch", {
                                            "model": m.value, "provider": m.provider, "name": m.name,
                                        })
                                    if renderer:
                                        renderer.show_message(
                                            f"  Model switched to {m.name} ({m.value})",
                                            style="green",
                                        )
                                input_handler.resume_polling()
                                should_restart = True
                                break

                            else:
                                # Resume
                                input_handler.resume_polling()
                                should_restart = True
                                break

                        # Try to get an event from the queue (non-blocking with timeout)
                        try:
                            event = await asyncio.wait_for(event_queue.get(), timeout=0.2)
                        except asyncio.TimeoutError:
                            # No event yet — loop back to check Esc
                            if stream_done.is_set() and event_queue.empty():
                                break
                            continue

                        if event is None:
                            # Stream finished
                            break

                        # ── Process event LIVE ────────────────────────
                        for k, v in event.items():
                            logger.info("Node '%s' executed.", k)
                            f.write(f"\n--- Node: {k} ---\n")

                            if "messages" in v:
                                new_msgs = v.get("messages", [])
                                # Extend the authoritative history — don't replace it.
                                # astream(updates) emits per-node deltas, not the full state.
                                accumulated_messages.extend(new_msgs)
                                initial_state["messages"] = accumulated_messages
                                for msg in new_msgs:
                                    f.write(f"[{msg.__class__.__name__}]: {msg.content}\n")
                                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                                        f.write(f"Tool Calls: {json.dumps(msg.tool_calls, indent=2)}\n")
                                    if session_store:
                                        session_store.append(msg)
                                    # ── Clean transcript ───────────────────────
                                    _cls = msg.__class__.__name__
                                    if _cls == "AIMessage":
                                        _tc_names = [
                                            tc.get("name", "?")
                                            for tc in (getattr(msg, "tool_calls", None) or [])
                                        ]
                                        if _tc_names:
                                            _write_transcript(
                                                "Agent (tool calls)",
                                                "\n".join(f"→ {n}" for n in _tc_names),
                                            )
                                        elif getattr(msg, "content", ""):
                                            _write_transcript("Agent", msg.content)
                                    elif _cls == "ToolMessage":
                                        _tname = getattr(msg, "name", "tool")
                                        _write_transcript(
                                            f"Tool result [{_tname}]",
                                            str(getattr(msg, "content", ""))[:2000],
                                        )
                                    elif _cls == "HumanMessage":
                                        _write_transcript("Operator", str(getattr(msg, "content", "")))
                            else:
                                f.write(f"State Update: {json.dumps(v, default=str)}\n")
                            f.flush()

                            # ── Render to terminal LIVE ───────────────
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

                                            if tool_name == "record_finding":
                                                findings_count += 1

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

                        # ── Update progress tracker ────────────────
                        tracker.update(accumulated_messages, iteration, findings_count)

                        # Inject progress context into agent state
                        # (remove previous progress message first to avoid stacking)
                        accumulated_messages = [
                            m for m in accumulated_messages
                            if not (
                                isinstance(m, SystemMessage)
                                and isinstance(m.content, str)
                                and m.content.startswith("═══ PROGRESS STATUS")
                            )
                        ]
                        progress_msg = tracker.build_context_message()
                        accumulated_messages.append(progress_msg)
                        initial_state["messages"] = accumulated_messages

                        # Show progress on CLI
                        if renderer:
                            done = sum(1 for i in tracker.checklist if i.status == "done")
                            total = len(tracker.checklist)
                            budget = max(1, tracker.token_budget)
                            pct = min(100, int(tracker.total_tokens_used / budget * 100))
                            elapsed = time.monotonic() - agent_start
                            elapsed_str = f"{elapsed / 60:.1f}m" if elapsed > 60 else f"{elapsed:.0f}s"
                            renderer.show_progress_bar(
                                turn=tracker.turn,
                                max_turns=tracker.max_turns,
                                plan_done=done,
                                plan_total=total,
                                token_pct=pct,
                                findings=tracker.findings_count,
                                elapsed_str=elapsed_str,
                            )

                        # Budget guardrail: if critical, force wrap-up
                        if tracker.is_budget_critical and not getattr(self, '_budget_warning_sent', False):
                            self._budget_warning_sent = True
                            budget_msg = HumanMessage(
                                content=(
                                    "[SYSTEM] Token budget is nearly exhausted (>85%). "
                                    "You MUST wrap up now: record all findings via record_finding, "
                                    "then generate the final vulnerability report. "
                                    "Do NOT start new analysis or tool calls."
                                )
                            )
                            accumulated_messages.append(budget_msg)
                            initial_state["messages"] = accumulated_messages
                            f.write("\n=== BUDGET CRITICAL: Forcing wrap-up ===\n")
                            if renderer:
                                renderer.show_message(
                                    "  ⚠️  Token budget critical — forcing agent to wrap up",
                                    style="bold yellow",
                                )

                        # Turn limit guardrail
                        if tracker.is_over_turns and not getattr(self, '_turn_limit_sent', False):
                            self._turn_limit_sent = True
                            turn_msg = HumanMessage(
                                content=(
                                    f"[SYSTEM] You have reached the maximum turn limit ({tracker.max_turns}). "
                                    "Finalize your report and end the campaign with [DONE]."
                                )
                            )
                            accumulated_messages.append(turn_msg)
                            initial_state["messages"] = accumulated_messages
                            f.write(f"\n=== TURN LIMIT REACHED: {tracker.max_turns} ===\n")

                        # Debounced auto-checkpoint: save every N turns or M seconds
                        _now = time.monotonic()
                        _turns_since = iteration - _last_checkpoint_turn
                        _secs_since = _now - _last_checkpoint_time
                        if _turns_since >= _CHECKPOINT_INTERVAL_TURNS or _secs_since >= _CHECKPOINT_INTERVAL_SECS:
                            save_checkpoint(
                                run_folder=run_folder,
                                messages=accumulated_messages,
                                turn=iteration,
                                target_dir=target_dir,
                                target_url=target_url,
                                log_file=log_file,
                                mode=mode,
                                pipeline_completed=True,
                                session_headers=session_headers,
                            )
                            _last_checkpoint_turn = iteration
                            _last_checkpoint_time = _now

                except asyncio.CancelledError:
                    logger.info("Agent stream cancelled (Esc pressed)")

        # Stop the input handler
        if input_handler:
            input_handler.stop()

        # Append session-end marker to live report
        try:
            await _live_report.append_session_end(turn=iteration, reason="completed")
        except Exception:
            pass

        if renderer:
            renderer.show_phase_timing("Agent phase")

        return "Campaign Completed."

    async def _collect_stream(self, state: dict) -> list:
        """Collect all events from the LangGraph stream into a list (legacy fallback)."""
        events = []
        async for event in self.app.astream(state):
            events.append(event)
        return events

    def _rebuild_graph(self) -> None:
        """Rebuild the LangGraph with updated LLM (for model hot-swap)."""
        self._build_graph()
