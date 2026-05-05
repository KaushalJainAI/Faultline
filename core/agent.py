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
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypedDict, Annotated, Optional, List

from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage, BaseMessage, ToolMessage
from core.intelligence.content_manager import build_tiered_context, estimate_tokens

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

from core.intelligence.prompts import SYSTEM_PROMPT, VISION_REMINDER
from core.tools import FAULTLINE_TOOLS
from core.providers.cli_provider import ProviderManager
from core.providers.provider_config import get_cli_provider_name, get_provider
from core.orchestration.checkpoint import save_checkpoint, load_checkpoint
from core.providers.model_registry import get_active_model, set_active_model, find_model
from core.intelligence.progress_tracker import ProgressTracker
from langchain_core.runnables import RunnableConfig
from core.harness import HarnessRuntime

logger = logging.getLogger("AegisAgent")


class ParallelToolNode(ToolNode):
    """
    A ToolNode that executes multiple tool calls concurrently using asyncio.gather.
    Significantly speeds up multi-file reads and parallel security probing.
    """
    def __init__(self, tools, harness: Optional[HarnessRuntime] = None):
        super().__init__(tools)
        self._harness = harness

    async def invoke(self, state: "CampaignState", config: RunnableConfig = None) -> "CampaignState":
        # Get tool calls from the last message
        last_message = state["messages"][-1]
        if not hasattr(last_message, "tool_calls") or not last_message.tool_calls:
            return {"messages": []}

        # Build list of coroutines
        coros = []
        for tool_call in last_message.tool_calls:
            coros.append(self.ainvoke_tool(tool_call, config))

        # Run all in parallel
        try:
            results = await asyncio.gather(*coros)
        except Exception as e:
            logger.error("Parallel execution failed: %s", e)
            # Fallback to sequential if gather totally bombs (unlikely but safe)
            results = []
            for tool_call in last_message.tool_calls:
                results.append(await self.ainvoke_tool(tool_call, config))
        
        return {"messages": results}

    async def ainvoke_tool(self, tool_call, config):
        try:
            tool_name = tool_call["name"]
            tool_args = tool_call.get("args", {}) or {}
            if self._harness:
                required = self._harness.registry.get_permission(tool_name)
                decision = self._harness.permissions.authorize(required)
                if not decision.allowed:
                    return ToolMessage(
                        content=f"Permission denied for tool '{tool_name}': {decision.reason}",
                        tool_call_id=tool_call["id"],
                        status="error",
                    )
                pre = self._harness.hooks.run_pre(tool_name, tool_args)
                if not pre.allowed:
                    return ToolMessage(
                        content=f"Tool '{tool_name}' blocked by pre-hook: {pre.reason}",
                        tool_call_id=tool_call["id"],
                        status="error",
                    )
                tool_args = pre.args_override if pre.args_override is not None else tool_args

            tool = self.tools_by_name.get(tool_call["name"])
            if not tool:
                return ToolMessage(
                    content=f"Error: Tool '{tool_call['name']}' not found.",
                    tool_call_id=tool_call["id"],
                    status="error",
                )
            # Use LangChain's ainvoke which handles sync/async tools properly
            result = await tool.ainvoke(tool_args, config)
            
            # THE GOVERNOR: Batch Context Protection
            # If a parallel result is massive, offload it immediately to prevent turn-crash.
            content = str(result.content) if hasattr(result, "content") else str(result)
            from core.intelligence.content_manager import estimate_tokens, store_and_summarize
            if estimate_tokens(content) > 50000:
                logger.info(f"Parallel tool '{tool_call['name']}' result too large. Auto-summarizing.")
                # We need the run_folder from state, but for now we use an empty string
                # or try to extract it from the tool arguments if available.
                run_folder = tool_call.get("args", {}).get("run_folder", "")
                summary, _ = store_and_summarize(
                    content, 
                    tool_call["name"], 
                    run_folder, 
                    counter=999,
                    source_hint=f"parallel_{tool_call['name']}",
                    turn=-1
                )
                if hasattr(result, "content"):
                    result.content = summary
                else:
                    result = ToolMessage(content=summary, tool_call_id=tool_call["id"], name=tool_call["name"])

            if hasattr(result, "tool_call_id"):
                result.tool_call_id = tool_call["id"]
            if self._harness:
                result = self._harness.hooks.run_post(tool_name, tool_args, result)
            return result
        except Exception as e:
            logger.error("Error executing tool '%s' in parallel: %s", tool_call.get("name"), e)
            return ToolMessage(
                content=f"Error executing tool '{tool_call['name']}': {e}",
                tool_call_id=tool_call["id"],
                status="error",
            )

MAX_RPM: int = int(os.environ.get("FAULTLINE_MAX_RPM", "36"))
CALL_TIMEOUT_S: int = int(os.environ.get("FAULTLINE_CALL_TIMEOUT", "600"))  # Default 10m timeout for LLM calls






class LLMRateLimiter:
    """
    Thread-safe, async rate limiter using a sliding window (queue of timestamps).
    Enforces a strict Requests Per Minute (RPM) limit.
    """
    def __init__(self, rpm: int):
        self.rpm = rpm
        self.window_size = 60.0  # seconds
        self.calls = deque()
        self.lock = asyncio.Lock()

    async def wait(self):
        """Blocks until a call can be made without exceeding the RPM limit."""
        if self.rpm <= 0:
            return

        async with self.lock:
            now = time.monotonic()
            
            # Remove timestamps outside the 60s window
            while self.calls and now - self.calls[0] > self.window_size:
                self.calls.popleft()

            if len(self.calls) >= self.rpm:
                # Calculate sleep time based on the oldest call in the current window
                sleep_time = self.window_size - (now - self.calls[0])
                if sleep_time > 0:
                    logger.info(f"Rate limit reached ({self.rpm} RPM). Sleeping for {sleep_time:.2f}s...")
                    await asyncio.sleep(sleep_time)
                
                # After sleeping, re-clean the window
                now = time.monotonic()
                while self.calls and now - self.calls[0] > self.window_size:
                    self.calls.popleft()

            self.calls.append(time.monotonic())


# Global rate limiter instance
_rate_limiter = LLMRateLimiter(MAX_RPM)


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


def _is_context_overflow_error(err_lower: str) -> bool:
    """Detect provider errors caused by prompt/context length overflow."""
    patterns = (
        "maximum context length",
        "context length",
        "input_tokens",
        "prompt contains at least",
        "please reduce the length of the input prompt",
        "context_window_exceeded",
    )
    return any(p in err_lower for p in patterns)


def _tail_by_token_budget(text: str, max_tokens: int) -> str:
    """
    Keep the most recent lines under a token cap.
    Useful for large rolling logs like live_report.md.
    """
    if max_tokens <= 0:
        return ""
    if estimate_tokens(text) <= max_tokens:
        return text

    used = 0
    keep: list[str] = []
    for line in reversed(text.splitlines()):
        line_cost = max(1, estimate_tokens(line) + 1)
        if used + line_cost > max_tokens:
            break
        keep.append(line)
        used += line_cost

    tail = "\n".join(reversed(keep))
    return (
        f"[TRUNCATED live_report.md to last ~{max_tokens} tokens]\n"
        f"{tail}"
    )


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
    Enforces global RPM limit before starting the call.
    """
    from langchain_core.messages.ai import AIMessageChunk

    # Enforce rate limit
    await _rate_limiter.wait()

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

    # Merge chunks â€” prefer LangChain's built-in reducer, fall back to manual
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
            "SPEED MODE: Be extremely concise. One sentence of reasoning max. "
            "Do NOT overthink. You MUST finish your response in under 10 minutes."
        ),
    },
    "normal": {
        "max_output_tokens": 4096,
        "instruction": (
            "NORMAL MODE: Balance thoroughness with efficiency. "
            "Reasoning should be 2â€“3 sentences. Avoid long-winded analysis. "
            "You MUST finish your response in under 10 minutes."
        ),
    },
    "deep": {
        "max_output_tokens": 8192,
        "instruction": (
            "DEEP MODE: Show your reasoning chain, but stay focused. "
            "Do NOT overthink or repeat yourself. The operator is waiting. "
            "You MUST finish your response in under 10 minutes."
        ),
    },
}


@dataclass
class BudgetConfig:
    """Runtime spending limits for a single campaign run."""
    max_llm_calls: int = int(os.environ.get("FAULTLINE_MAX_LLM_CALLS") or os.environ.get("FAULTLINE_MAX_TURNS") or "120")
    max_tool_calls: int = int(os.environ.get("FAULTLINE_MAX_TOOL_CALLS", "400"))
    # Keep context budget fixed at 200k unless explicitly lowered via env.
    # We do not allow increasing above 200k to keep prompt size predictable.
    max_input_tokens: int = int(os.environ.get("FAULTLINE_MAX_INPUT_TOKENS") or os.environ.get("FAULTLINE_MAX_TOKENS") or "200000")
    max_output_tokens: int = int(os.environ.get("FAULTLINE_MAX_OUTPUT_TOKENS", "4096"))
    max_rpm: int = int(os.environ.get("FAULTLINE_MAX_RPM", "36"))
    reasoning_level: str = os.environ.get("FAULTLINE_REASONING_LEVEL", "normal")
    context_ratio: float = float(os.environ.get("FAULTLINE_CONTEXT_RATIO", "0.8"))

    def __post_init__(self):
        if self.reasoning_level not in REASONING_PROFILES:
            self.reasoning_level = "normal"
        profile_tokens = REASONING_PROFILES[self.reasoning_level]["max_output_tokens"]
        # If user didn't explicitly override output tokens via env, use profile default
        env_override = "FAULTLINE_MAX_OUTPUT_TOKENS" in os.environ
        if not env_override:
            self.max_output_tokens = profile_tokens
        # Hard ceiling for per-call context budget.
        self.max_input_tokens = min(self.max_input_tokens, 200_000)

    @property
    def reasoning_instruction(self) -> str:
        return REASONING_PROFILES[self.reasoning_level]["instruction"]

    def budget_prompt_block(self, llm_used: int, tool_used: int, active_model_value: Optional[str] = None) -> str:
        # Context budget is fixed to preserve predictable compaction behavior.
        display_limit = self.max_input_tokens

        return (
            "\nâ•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•\n"
            "REAL-WORLD BUDGET CONSTRAINTS  â† read this before every action\n"
            "â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•\n\n"
            f"You are operating under a HARD budget and strict time limit.\n\n"
            f"  Reasoning level : {self.reasoning_level.upper()}\n"
            f"  Time Limit      : 10 MINUTES (600s) MAX per turn\n"
            f"  LLM calls used  : {llm_used} / {self.max_llm_calls}  "
            f"({'STOP NOW â€” over budget!' if llm_used >= self.max_llm_calls else f'{self.max_llm_calls - llm_used} remaining'})\n"
            f"  Tool calls used : {tool_used} / {self.max_tool_calls}  "
            f"({'STOP NOW â€” over budget!' if tool_used >= self.max_tool_calls else f'{self.max_tool_calls - tool_used} remaining'})\n"
            f"  Max output/call : {self.max_output_tokens} tokens\n"
            f"  Max context     : {display_limit:,} tokens (fixed)\n\n"
            f"{self.reasoning_instruction}\n\n"
            "RULES:\n"
            "- DO NOT OVERTHINK. Be decisive.\n"
            "- If you cannot find a solution in 1-2 steps, summarize and ask the operator.\n"
            "- Avoid deeply nested reasoning chains that cause high latency.\n"
            "- When you hit [DONE] or the budget/time runs out, stop immediately.\n"
            "â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•\n"
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

    # Build an httpx.Timeout aligned with CALL_TIMEOUT_S so that the SDK's
    # internal per-chunk stream timeout never fires before our asyncio guard.
    # connect=30s is generous; read=CALL_TIMEOUT_S covers slow TTFT on large
    # context payloads (the root cause of the stream_chunk_timeout warning).
    try:
        import httpx as _httpx
        _http_timeout = _httpx.Timeout(
            connect=30.0,
            read=float(CALL_TIMEOUT_S),
            write=30.0,
            pool=30.0,
        )
        _http_client = _httpx.Client(timeout=_http_timeout)
        _async_http_client = _httpx.AsyncClient(timeout=_http_timeout)
    except ImportError:
        _http_client = None
        _async_http_client = None
        _http_timeout = None

    if provider == "anthropic":
        if not ChatAnthropic:
            logger.error("langchain-anthropic not installed")
            return None
        kwargs = dict(
            model=model_name or "claude-sonnet-4-5",
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            temperature=0.2,
            timeout=float(CALL_TIMEOUT_S),
        )
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if _async_http_client is not None:
            try:
                kwargs["http_client"] = _async_http_client
            except Exception:
                pass
        return ChatAnthropic(**kwargs)

    if provider == "google":
        if not ChatGoogleGenerativeAI:
            logger.error("langchain-google-genai not installed")
            return None
        kwargs = dict(
            model=model_name or "gemini-2.0-flash-001",
            google_api_key=os.environ.get("GOOGLE_API_KEY"),
            temperature=0.2,
            request_timeout=float(CALL_TIMEOUT_S),
        )
        if max_tokens:
            kwargs["max_output_tokens"] = max_tokens
        return ChatGoogleGenerativeAI(**kwargs)

    if provider == "nvidia":
        if not ChatOpenAI:
            logger.error("langchain-openai not installed â€” cannot use provider 'nvidia'")
            return None
        kwargs = {
            "model": model_name or "nvidia/llama-3.3-nemotron-super-49b-v1",
            "openai_api_key": os.environ.get("NVIDIA_API_KEY", ""),
            "openai_api_base": "https://integrate.api.nvidia.com/v1",
            "temperature": 0.2,
            "request_timeout": float(CALL_TIMEOUT_S),
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if _async_http_client is not None:
            try:
                kwargs["http_async_client"] = _async_http_client
            except Exception:
                pass
        return ChatOpenAI(**kwargs)

    if provider in {"openai", "openrouter"}:
        if not ChatOpenAI:
            logger.error("langchain-openai not installed â€” cannot use provider '%s'", provider)
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
            # request_timeout sets the httpx read timeout used during streaming,
            # preventing stream_chunk_timeout from firing before CALL_TIMEOUT_S.
            "request_timeout": float(CALL_TIMEOUT_S),
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if provider == "openrouter":
            kwargs["default_headers"] = {
                "HTTP-Referer": "https://github.com/faultline-chaos",
                "X-Title": "Faultline Aegis-Breaker",
            }
        if _async_http_client is not None:
            try:
                kwargs["http_async_client"] = _async_http_client
            except Exception:
                pass
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






def _seed_api_test_data(run_folder: str) -> None:
    """
    Populate api_test_data.json by matching discovered endpoints with serializer schemas.
    Ensures 100% project coverage by seeding fixtures for every known route.
    """
    rf = Path(run_folder)
    schema_path = rf / "api_schemas.json"
    map_path = rf / "endpoint_map.json"
    
    if not schema_path.exists() or not map_path.exists():
        logger.warning("Seeding skipped: missing api_schemas.json or endpoint_map.json")
        return

    try:
        schemas = json.loads(schema_path.read_text(encoding="utf-8"))
        endpoints = json.loads(map_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to load seeding data: %s", e)
        return

    # Index schemas by a clean name for fuzzy matching
    schema_idx = {s["name"].lower(): s for s in schemas}
    
    # Type â†’ sensible sample value
    _SAMPLE: dict = {
        "CharField": "example_value",
        "EmailField": "user@example.com",
        "URLField": "https://example.com",
        "IntegerField": 1,
        "FloatField": 1.0,
        "BooleanField": True,
        "DateField": "2026-01-01",
        "DateTimeField": "2026-01-01T00:00:00Z",
        "UUIDField": "00000000-0000-0000-0000-000000000001",
        "ListField": [],
        "DictField": {},
        "JSONField": {},
    }

    def _sample(ftype: str) -> object:
        for k, v in _SAMPLE.items():
            if k.lower() in ftype.lower():
                return v
        if "password" in ftype.lower():
            return "TestPass123!"
        return "value"

    seeded_endpoints = {}

    for ep in endpoints:
        path = ep.get("path", "")
        method = ep.get("method", "GET")
        view = str(ep.get("view", "")).lower()
        
        # 1. Path normalization
        is_traced = ep.get("traced", False)
        
        # If it's already an absolute URL, leave it alone
        if path.startswith("http"):
            pass
        else:
            # Ensure leading slash
            if not path.startswith("/"):
                path = "/" + path
            
            # Heuristics for untraced routes (fallback logic)
            if not is_traced:
                # If discovery didn't find /api/ but we know it's a DRF project
                if not path.startswith("/api/") and not path.startswith("/admin/") and not path.startswith("/static/"):
                     has_api_elsewhere = any(str(e.get("path", "")).startswith("/api/") for e in endpoints if e.get("traced"))
                     if has_api_elsewhere or "viewset" in view or "api" in view:
                         path = "/api" + path
                
                # Ensure trailing slash for DRF style if it looks like a directory
                if not path.endswith("/") and "." not in path.split("/")[-1]:
                    path += "/"

        # 2. Match with serializer
        payload = {}
        matched_schema = None
        
        # Try direct name match
        for s_name, s_data in schema_idx.items():
            clean_s = s_name.replace("serializer", "").replace("list", "").replace("detail", "")
            if clean_s and (clean_s in view or clean_s in path.lower()):
                matched_schema = s_data
                break
        
        if matched_schema:
            for field in matched_schema.get("fields", []):
                f_name = field["name"]
                payload[f_name] = _sample(field.get("type", "string"))

        # 3. Add to seed data
        key = f"{method} {path}"
        seeded_endpoints[key] = {
            "method": method,
            "url": path,
            "payload": payload,
            "auth_required": ep.get("auth_required", False) or "login" in path or "profile" in path,
            "description": f"Seeded from {ep.get('file', 'unknown')}: {ep.get('view', 'view')}",
            "status": "Untested"
        }

    # Special-case auth
    auth_fixture = {}
    for s in schemas:
        name = s.get("name", "").lower()
        if "register" in name or "signup" in name:
            auth_fixture["register_payload"] = {f["name"]: _sample(f.get("type", "")) for f in s.get("fields", []) if f.get("required")}
        if "login" in name:
            auth_fixture["login_payload"] = {f["name"]: _sample(f.get("type", "")) for f in s.get("fields", []) if f.get("required")}

    data = {
        "project_name": "Discovered Project",
        "base_url": "{{TARGET_URL}}",
        "auth": {
            "register_url": "/api/auth/register/",
            "login_url": "/api/auth/login/",
            "token_field": "access",
            **auth_fixture
        },
        "endpoints": seeded_endpoints
    }
    
    target = rf / "api_test_data.json"
    target.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Successfully seeded %d endpoints for testing.", len(seeded_endpoints))


class AegisAgent:
    # â”€â”€ Turn Timeout Constants â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
    _TURN_TIMEOUT_S = int(os.environ.get("FAULTLINE_TURN_TIMEOUT", "600"))       # 10 minutes (hard abort)
    _INTERRUPT_WARNING_S = int(os.environ.get("FAULTLINE_INTERRUPT_WARNING", "300"))  # 5 minutes (yellow)
    _CRITICAL_WARNING_S = int(os.environ.get("FAULTLINE_CRITICAL_WARNING", "480"))   # 8 minutes (orange)
    _AUTO_ABORT_WARNING_S = int(os.environ.get("FAULTLINE_AUTO_ABORT_WARNING", "570")) # 9.5 minutes (red)
    _TOKEN_BUDGET_DEFAULT = int(os.environ.get("FAULTLINE_TOKEN_BUDGET_DEFAULT", "1000000"))
    _TOKEN_BUDGET_HARD_CAP = int(os.environ.get("FAULTLINE_TOKEN_BUDGET_HARD_CAP", "2500000"))

    def __init__(self, budget: Optional[BudgetConfig] = None):
        self._renderer = None
        self._budget = budget or BudgetConfig()
        _rate_limiter.rpm = self._budget.max_rpm
        self._llm_calls_used = 0
        self._tool_calls_used = 0
        self._tracker = None
        self._harness = HarnessRuntime.from_tools(FAULTLINE_TOOLS, permission="workspace")
        self.workflow = StateGraph(CampaignState)
        self._build_graph()

    def _sync_state_from_history(self, messages: List[BaseMessage]) -> int:
        """
        Scan message history to re-calculate LLM calls, tool calls, and turn count.
        Returns the inferred turn count (iteration).
        """
        llm_calls = 0
        tool_calls = 0
        turns = 0

        for msg in messages:
            if isinstance(msg, AIMessage):
                llm_calls += 1
                turns += 1
                if msg.tool_calls:
                    tool_calls += len(msg.tool_calls)
        
        self._llm_calls_used = llm_calls
        self._tool_calls_used = tool_calls
        return turns

    def _build_graph(self):
        self.workflow.add_node("agent", self.agent_node)
        self.workflow.add_node("tools", ParallelToolNode(self._harness.registry.tools(), harness=self._harness))
        self.workflow.set_entry_point("agent")
        self.workflow.add_conditional_edges(
            "agent",
            self.should_continue,
            {"continue": "tools", "end": END},
        )
        self.workflow.add_edge("tools", "agent")
        self.app = self.workflow.compile()

    def _init_live_report(self, run_folder: str, target_dir: str, target_url: str, mode: str):
        from core.orchestration.context import live_report_var
        from core.orchestration.live_report import LiveReport

        pipeline_report_path = (
            str(Path(run_folder) / "pipeline_report.md")
            if mode in ("pipeline", "hybrid")
            else ""
        )
        live_report = LiveReport(
            run_folder=run_folder,
            target_dir=target_dir,
            target_url=target_url,
            mode=mode,
            pipeline_report_path=pipeline_report_path,
        )
        live_report_var.set(live_report)
        return live_report

    def _setup_test_boilerplates(self, run_folder: str) -> list[str]:
        testcases_dir = Path(run_folder) / "testcases"
        testcases_dir.mkdir(parents=True, exist_ok=True)
        _seed_api_test_data(run_folder)

        copied_paths: list[str] = []
        bp_src_dir = Path(__file__).resolve().parent.parent / "agent_assets" / "test_boilerplates"
        if not bp_src_dir.exists():
            return copied_paths

        import shutil
        for src in bp_src_dir.glob("*.py"):
            dest = testcases_dir / src.name
            if not dest.exists():
                shutil.copy2(src, dest)
            copied_paths.append(str(dest.resolve()))
        return copied_paths

    def _build_initial_state(
        self,
        target_dir: str,
        target_url: str,
        log_file: str,
        run_folder: str,
        session_headers: Optional[dict],
        initial_prompt: str,
        resumed_messages: Optional[list],
    ) -> dict:
        initial_messages = resumed_messages or [HumanMessage(content=initial_prompt)]
        return {
            "messages": initial_messages,
            "target_dir": target_dir,
            "target_url": target_url,
            "log_file": log_file,
            "run_folder": run_folder,
            "session_headers": session_headers or {},
        }

    def _resolve_resume_counters(
        self,
        accumulated_messages: list,
        resumed_messages: Optional[list],
        resumed_turn: int,
        resumed_findings: int,
    ) -> tuple[int, int]:
        iteration = 0
        findings_count = 0
        if not resumed_messages:
            return iteration, findings_count

        iteration = resumed_turn or self._sync_state_from_history(accumulated_messages)
        if resumed_findings > 0:
            return iteration, resumed_findings

        for msg in accumulated_messages:
            if isinstance(msg, AIMessage):
                for tc in (getattr(msg, "tool_calls", None) or []):
                    if tc.get("name") == "record_finding":
                        findings_count += 1
        return iteration, findings_count

    def _init_progress_tracker(self, agent_start: float, iteration: int) -> ProgressTracker:
        max_turns = int(os.environ.get("FAULTLINE_MAX_TURNS") or os.environ.get("FAULTLINE_MAX_LLM_CALLS") or "40")
        raw_budget = int(os.environ.get("FAULTLINE_TOKEN_BUDGET", str(self._TOKEN_BUDGET_DEFAULT)))
        token_budget = max(1_000_000, min(raw_budget, self._TOKEN_BUDGET_HARD_CAP))
        self._tracker = ProgressTracker(
            max_turns=max_turns,
            token_budget=token_budget,
            start_time=agent_start,
            turn=iteration,
        )
        return self._tracker

    def _write_transcript_header(self, transcript_path: Path, campaign_id: str, target_url: str, target_dir: str) -> None:
        with open(transcript_path, "a", encoding="utf-8") as f:
            f.write(
                f"{'=' * 60}\n"
                f"Faultline Transcript - Campaign: {campaign_id}\n"
                f"Started: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"Target: {target_url or target_dir}\n"
                f"{'=' * 60}\n"
            )

    def _write_transcript_line(self, transcript_path: Path, role: str, text: str) -> None:
        try:
            ts = time.strftime("%H:%M:%S")
            header = f"[{ts}] {role}"
            body = str(text or "").strip()
            with open(transcript_path, "a", encoding="utf-8") as f:
                f.write(f"\n{'-' * 60}\n{header}\n{'-' * 60}\n{body}\n")
                f.flush()
        except Exception:
            pass

    def _session_vars_snapshot(self) -> dict:
        tracker = self._tracker
        return {
            "max_tool_calls": self._budget.max_tool_calls,
            "max_llm_calls": self._budget.max_llm_calls,
            "max_rpm": self._budget.max_rpm,
            "max_turns": tracker.max_turns if tracker else "",
            "token_budget": tracker.token_budget if tracker else "",
            "reasoning_level": self._budget.reasoning_level,
        }

    def _apply_session_var(self, key: str, value: str) -> tuple[bool, str]:
        k = (key or "").strip().lower()
        v = (value or "").strip()
        if not k:
            return False, "empty key"
        int_vars = {"max_tool_calls", "max_llm_calls", "max_turns", "token_budget", "max_rpm"}
        if k in int_vars:
            try:
                iv = int(v)
                if iv <= 0:
                    return False, f"{k} must be > 0"
            except Exception:
                return False, f"{k} expects an integer"
            if k == "max_tool_calls":
                self._budget.max_tool_calls = iv
            elif k == "max_llm_calls":
                self._budget.max_llm_calls = iv
            elif k == "max_rpm":
                self._budget.max_rpm = iv
                _rate_limiter.rpm = iv
            elif k == "max_turns" and self._tracker:
                self._tracker.max_turns = iv
            elif k == "token_budget" and self._tracker:
                self._tracker.token_budget = iv
            return True, f"{k} set to {iv}"

        if k == "reasoning_level":
            level = v.lower()
            if level not in REASONING_PROFILES:
                return False, f"reasoning_level must be one of: {', '.join(REASONING_PROFILES.keys())}"
            self._budget.reasoning_level = level
            return True, f"reasoning_level set to {level}"

        return False, f"unknown var '{k}'"

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
                renderer.show_message(f"  [Budget] LLM call limit hit â€” stopping.", style="bold red")
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
                        # Show warnings based on elapsed time
                        if elapsed >= self._AUTO_ABORT_WARNING_S:
                            renderer.show_message(
                                f"  ðŸ”´ [bold red]AUTO-INTERRUPT IN {int(self._TURN_TIMEOUT_S - elapsed)}s[/bold red]",
                                style="red"
                            )
                        elif elapsed >= self._CRITICAL_WARNING_S:
                            renderer.show_message(
                                f"  ðŸŸ  [orange3]Turn approaching timeout ({elapsed/60:.1f}m elapsed)[/orange3]",
                                style="orange3"
                            )
                        elif elapsed >= self._INTERRUPT_WARNING_S:
                            renderer.show_message(
                                f"  ðŸŸ¡ [dim]Thinking takes longer than usual ({elapsed/60:.1f}m)...[/dim]",
                                style="yellow"
                            )
                        else:
                            renderer.show_cli_waiting(elapsed, cli_provider)
                    
                    if elapsed >= self._TURN_TIMEOUT_S:
                        logger.warning(f"Turn timeout reached ({self._TURN_TIMEOUT_S}s). Signalling interrupt.")
                        if input_handler:
                            input_handler.pause_requested.set()
                        break

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
                            f"  [Timeout] CLI provider did not respond in {CALL_TIMEOUT_S}s â€” skipping turn.",
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
                                f"  Turn {turn} failed â€” keeping results from {completed_turns} earlier turn(s).",
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

        # â”€â”€ Tool Filtering & Graceful Exit â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
        is_critical = self._llm_calls_used >= (budget.max_llm_calls - 5)
        is_exhausted = self._llm_calls_used >= budget.max_llm_calls

        active_tools = self._harness.registry.tools()
        if is_critical:
            # Prune to only reporting and essential read tools
            active_tools = [
                t for t in self._harness.registry.tools()
                if (getattr(t, "name", None) or getattr(t, "__name__", "")) in (
                    "record_finding", "save_vulnerability_report",
                    "summarize_to_report", "list_run_folder_files",
                    "read_run_folder_file", "record_decision"
                )
            ]
            logger.info("Budget Critical: pruning toolset to reporting-only")
            
            critical_prompt = (
                "\n[SYSTEM] CRITICAL BUDGET WARNING: You are within 5 turns of the hard turn limit. "
                "Exploratory tools have been disabled. You MUST now synthesize your final findings, "
                "ensure all vulnerabilities are recorded with `record_finding`, and call "
                "`save_vulnerability_report` to generate the final campaign artifact. "
                "Once the report is saved, end with [DONE]."
            )
            # Only inject if not recently warned
            if not any("CRITICAL BUDGET WARNING" in str(m.content) for m in state["messages"][-3:]):
                state["messages"].append(SystemMessage(content=critical_prompt))

        model_with_tools = llm.bind_tools(active_tools)

        session_headers = state.get("session_headers", {})
        run_folder = state.get("run_folder", "reports/")
        header_str = "\n- Session Headers: " + str(session_headers) if session_headers else ""



        # Resolve active model for budget display and context limit
        active_model_value = getattr(llm, "model_name", None) or getattr(llm, "model", None)
        budget_block = budget.budget_prompt_block(self._llm_calls_used, self._tool_calls_used, active_model_value)

        # Context limit is fixed to 200k max. We only reduce from that baseline
        # to leave space for output/schema overhead.
        # Provider token accounting includes every bound tool schema. Faultline
        # has many tools with rich docstrings, so a small fixed reserve lets the
        # local preflight pass while OpenAI/OpenRouter still rejects the request.
        configured_schema_reserve = os.environ.get("FAULTLINE_TOOL_SCHEMA_RESERVE_TOKENS")
        if configured_schema_reserve:
            tool_schema_reserve = int(configured_schema_reserve)
        else:
            tool_schema_reserve = max(80_000, len(active_tools) * 2_500)
        preflight_margin = int(os.environ.get("FAULTLINE_PREFLIGHT_MARGIN_TOKENS", "2048"))
        min_context_floor = int(os.environ.get("FAULTLINE_MIN_CONTEXT_TOKENS", "512"))
        model_context_window: Optional[int] = None
        context_limit = budget.max_input_tokens
        if active_model_value:
            from core.providers.model_registry import get_model_info
            minfo = get_model_info(active_model_value)
            if minfo:
                model_context_window = int(minfo.context_window)
                # 1. Reserve output tokens AND a safety buffer (20k) for overhead/estimation error.
                # This ensures we NEVER hit the hard limit of the provider.
                safety_buffer = int(os.environ.get("FAULTLINE_CONTEXT_BUFFER", "20000"))
                hard_input_cap = (
                    minfo.context_window
                    - budget.max_output_tokens
                    - safety_buffer
                    - tool_schema_reserve
                )
                
                # Never exceed the fixed harness context budget.
                context_limit = min(context_limit, hard_input_cap)
                context_limit = max(min_context_floor, context_limit)
                
                logger.info(
                    "Fixed Context: using %s tokens (model=%s, safety buffer %s, tool schema reserve %s)",
                    f"{context_limit:,}",
                    minfo.name,
                    f"{safety_buffer:,}",
                    f"{tool_schema_reserve:,}",
                )
        else:
            # Provider/model unknown: still reserve room for output + tool schema overhead.
            fallback_reserve = budget.max_output_tokens + tool_schema_reserve + 20000
            context_limit = max(min_context_floor, context_limit - fallback_reserve)
                

        context_msg = SystemMessage(content=(
            f"{SYSTEM_PROMPT}\n\n"
            f"{budget_block}\n"
            f"Target Config:\n"
            f"- Directory: {state.get('target_dir')}\n"
            f"- URL: {state.get('target_url')}\n"
            f"- Log File: {state.get('log_file')}\n"
            f"- Run Folder: {run_folder}  â† write all test scripts and reports here\n"
            f"- Testcases Dir: {run_folder}/testcases/  â† boilerplate copies go here\n"
            f"- API Test Data: {run_folder}/api_test_data.json  â† read with read_run_folder_file; update as you discover endpoints\n"
            f"- Transcript: {run_folder}/transcript.txt  â† human-readable conversation log\n"
            f"{header_str}\n\n"
            "Aggressively investigate the structure, validate attacks, and fire them. "
            "If writing functional tests, use the Session Headers in your requests to bypass authentication. "
            "Use the API Serializer Schemas above to generate correctly-typed request bodies â€” "
            "required fields must always be present, optional fields may be omitted or fuzzed. "
            "Save all generated test scripts to the Testcases Dir above.\n\n"
            "Run-folder tools: use list_run_folder_files to discover what has been generated, "
            "read_run_folder_file to inspect any file (api_schemas.json, api_test_data.json, test scripts), "
            "and summarize_to_report to append intermediate progress notes to live_report.md."
        ))

        # Standard LLM (Tool-Calling) Loop with Spinner
        async def _llm_ticker() -> None:
            elapsed = 0
            label = f"Agent ({getattr(llm, 'model_name', None) or getattr(llm, 'model', 'unknown')})"
            if renderer:
                renderer.show_message(f"  {label} is thinking... (press Esc to steer)", style="dim")
            while True:
                await asyncio.sleep(15)
                elapsed += 15
                if renderer:
                    # Show warnings based on elapsed time
                    if elapsed >= self._AUTO_ABORT_WARNING_S:
                        renderer.show_message(
                            f"  ðŸ”´ [bold red]AUTO-INTERRUPT IN {int(self._TURN_TIMEOUT_S - elapsed)}s[/bold red]",
                            style="red"
                        )
                    elif elapsed >= self._CRITICAL_WARNING_S:
                        renderer.show_message(
                            f"  ðŸŸ  [orange3]Turn approaching timeout ({elapsed/60:.1f}m elapsed)[/orange3]",
                            style="orange3"
                        )
                    elif elapsed >= self._INTERRUPT_WARNING_S:
                        renderer.show_message(
                            f"  ðŸŸ¡ [dim]Thinking takes longer than usual ({elapsed/60:.1f}m)...[/dim]",
                            style="yellow"
                        )
                    else:
                        renderer.show_cli_waiting(elapsed, label)
                
                if elapsed >= self._TURN_TIMEOUT_S:
                    logger.warning(f"Standard turn timeout reached ({self._TURN_TIMEOUT_S}s). Signalling interrupt.")
                    if input_handler:
                        input_handler.pause_requested.set()
                    break

        # Prepare messages
        messages_to_process = state["messages"]

        # Vision guardrail â€” re-anchor the LLM after turn 1 so the long
        # tool-output history can't bury the original objective.
        # MOVE THIS BEFORE build_tiered_context so it's included in the token budget!
        if self._llm_calls_used >= 2:
            _coverage = _recent_step_coverage(state.get("run_folder", ""))
            _reminder_text = VISION_REMINDER + f"\nRecent step coverage: {_coverage}.\n"
            # Injected as a SystemMessage at the start of history
            messages_to_process = [SystemMessage(content=_reminder_text)] + messages_to_process

        ticker_task = asyncio.create_task(_llm_ticker())
        
        # Inject the live plan as a priority reminder
        _plan_path = Path(state.get("run_folder", "")) / "live_report.md"
        if _plan_path.exists():
            _plan_token_cap = int(os.environ.get("FAULTLINE_PLAN_REMINDER_TOKENS", "2500"))
            _plan_text_full = _plan_path.read_text(encoding="utf-8")
            _plan_text = _tail_by_token_budget(_plan_text_full, _plan_token_cap)
            _plan_reminder = f"\n### CURRENT CAMPAIGN PLAN (from live_report.md):\n{_plan_text}\n"
            messages_to_process = [SystemMessage(content=_plan_reminder)] + messages_to_process

        final_messages = list(messages_to_process)

        try:
            tiered_msgs, cm_stats = build_tiered_context(
                system_msg=context_msg,
                messages=final_messages,
                run_folder=state.get("run_folder", ""),
                max_tokens=context_limit,
                current_turn=self._llm_calls_used,
            )

            # Provider preflight: estimate total request size including output allowance
            # and tool-schema reserve, then compact once more if still too close.
            if model_context_window:
                projected_total = (
                    cm_stats.get("output_tokens_est", 0)
                    + budget.max_output_tokens
                    + tool_schema_reserve
                )
                threshold = max(1, model_context_window - preflight_margin)
                if projected_total >= threshold:
                    overflow = projected_total - threshold
                    tighter_limit = max(min_context_floor, context_limit - overflow - preflight_margin)
                    if tighter_limit < context_limit:
                        logger.warning(
                            "Preflight compaction: projected total %s >= threshold %s. Retrying with tighter context limit %s.",
                            f"{projected_total:,}",
                            f"{threshold:,}",
                            f"{tighter_limit:,}",
                        )
                        tiered_msgs, cm_stats = build_tiered_context(
                            system_msg=context_msg,
                            messages=final_messages,
                            run_folder=state.get("run_folder", ""),
                            max_tokens=tighter_limit,
                            current_turn=self._llm_calls_used,
                        )
                        context_limit = tighter_limit

            if cm_stats["windowing_applied"]:
                logger.info(
                    "content_manager: %dâ†’%d est. tokens | cycles: %d total, "
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
                                f"[content_manager] {cm_stats['total_input_tokens_est']:,}â†’"
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

            # Patterns that indicate a transient/retryable error from the provider
            _TRANSIENT = (
                "429", "529", "503", "rate_limit", "rate limit",
                "overloaded", "too many requests", "ratelimiterror",
                "overloaded_error", "service unavailable",
            )
            _MAX_RETRIES = 4
            response = None
            for _attempt in range(_MAX_RETRIES + 1):
                try:
                    response = await _stream_with_timeout(
                        model_with_tools, tiered_msgs,
                        timeout=CALL_TIMEOUT_S,
                        run_folder=_run_folder,
                    )
                    break  # success
                except asyncio.TimeoutError as te:
                    logger.warning("LLM call timed out: %s", te)
                    if renderer:
                        renderer.show_message(
                            f"  [Timeout] LLM did not respond in {CALL_TIMEOUT_S}s â€” injecting recovery hint.",
                            style="bold yellow",
                        )
                    return {"messages": [AIMessage(content=(
                        f"[TIMEOUT] The previous LLM call exceeded {CALL_TIMEOUT_S}s and was cancelled. "
                        "This usually happens when you are overthinking or providing too much reasoning. "
                        "For the next step: SKIP ALL REASONING, pick ONE action only, and use the most concise tool arguments possible."
                    ))]}
                except Exception as exc:
                    err_str = str(exc)
                    err_lower = err_str.lower()
                    is_transient = any(p in err_lower for p in _TRANSIENT)

                    if is_transient and _attempt < _MAX_RETRIES:
                        delay = 2 ** (_attempt + 1)  # 2s, 4s, 8s, 16s
                        logger.warning(
                            "Provider rate-limited (attempt %d/%d), retrying in %ds: %s",
                            _attempt + 1, _MAX_RETRIES, delay, err_str[:200],
                        )
                        if renderer:
                            renderer.show_message(
                                f"  [Retry {_attempt + 1}/{_MAX_RETRIES}] Provider rate-limited â€” "
                                f"sleeping {delay}s before retryâ€¦",
                                style="yellow",
                            )
                        if _run_folder:
                            try:
                                log_path = Path(_run_folder) / "campaign_agent.log"
                                with open(log_path, "a", encoding="utf-8") as _lf:
                                    _lf.write(
                                        f"[rate-limit retry {_attempt + 1}/{_MAX_RETRIES}] "
                                        f"sleeping {delay}s: {err_str[:200]}\n"
                                    )
                            except Exception:
                                pass
                        await asyncio.sleep(delay)
                        continue

                    # Non-transient or retries exhausted â€” surface and continue
                    logger.error("LLM call failed: %s", err_str)
                    if "401" in err_str or "authentication" in err_lower or "api key" in err_lower or "user not found" in err_lower:
                        return {"messages": [AIMessage(content=(
                            f"[LLM AUTH ERROR] The configured provider rejected the request: {err_str}\n\n"
                            "Fix: check your FAULTLINE_PROVIDER and matching API key in .env "
                            "(OPENROUTER_API_KEY / ANTHROPIC_API_KEY / OPENAI_API_KEY / GOOGLE_API_KEY). "
                            "Alternatively set FAULTLINE_PROVIDER=claude to use the Claude CLI."
                        ))]}
                    if is_transient:
                        return {"messages": [AIMessage(content=(
                            f"[RATE LIMIT EXHAUSTED] Provider remained rate-limited after "
                            f"{_MAX_RETRIES} retries: {err_str[:300]}\n\n"
                            "Record your findings so far and end the campaign."
                        ))]}
                    # Context overflow is a frequent 400 case; classify it explicitly.
                    if _is_context_overflow_error(err_lower):
                        logger.error("Context overflow from provider: %s", err_str)
                        if _run_folder:
                            try:
                                log_path = Path(_run_folder) / "campaign_agent.log"
                                with open(log_path, "a", encoding="utf-8") as _lf:
                                    _lf.write(
                                        f"[context overflow] limit={context_limit} "
                                        f"out={budget.max_output_tokens} "
                                        f"schema_reserve={tool_schema_reserve}: {err_str[:500]}\n"
                                    )
                            except Exception:
                                pass
                        return {"messages": [AIMessage(content=(
                            f"[CONTEXT OVERFLOW] Provider rejected this turn due to context size: {err_str[:300]}\n\n"
                            "For the next step: avoid broad re-reads, retrieve only specific refs from memory, "
                            "and perform one narrow tool action."
                        ))]}

                    # 400 Bad Request â€” likely malformed tool args, log and recover
                    if "400" in err_str:
                        logger.error("400 Bad Request â€” likely malformed tool payload: %s", err_str)
                        if _run_folder:
                            try:
                                log_path = Path(_run_folder) / "campaign_agent.log"
                                with open(log_path, "a", encoding="utf-8") as _lf:
                                    _lf.write(f"[400 error] {err_str[:500]}\n")
                            except Exception:
                                pass
                        return {"messages": [AIMessage(content=(
                            f"[BAD REQUEST] The previous tool call had an invalid payload (HTTP 400): "
                            f"{err_str[:300]}\n\n"
                            "Re-issue the tool call with corrected arguments. "
                            "Check required field names and types against the API schema."
                        ))]}
                    return {"messages": [AIMessage(content=(
                        f"[LLM ERROR] Provider call failed: {err_str[:300]}\n\n"
                        "The agent will attempt to continue. If this repeats, check provider status and API key."
                    ))]}
        finally:
            ticker_task.cancel()
            if renderer and hasattr(renderer, "_status"):
                try:
                    status_obj = getattr(renderer, "_status", None)
                    if status_obj is not None and hasattr(status_obj, "stop"):
                        status_obj.stop()
                except Exception:
                    # Never fail a turn due to renderer cleanup issues.
                    pass

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
        resumed_turn: int = 0,
        resumed_findings: int = 0,
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
        from core.orchestration.context import session_headers_var, chaos_vetoed_var
        from core.orchestration.cli_ui import extract_file_paths, extract_finding_title, summarize_args
        from core.orchestration.input_handler import ActionType

        self._renderer = renderer
        session_headers_var.set(session_headers or {})
        chaos_vetoed_var.set(False)

        # Initialise the live report (no-op if file already exists from a previous session)
        # Only pass pipeline_report_path when the pipeline actually runs (pipeline/hybrid).
        # In agent-only mode the file is never created, so passing it would show "not found".
        _live_report = self._init_live_report(run_folder, target_dir, target_url, mode)

        # 1. Automated Boilerplate Setup (Step 4 DNA)
        copied_paths = self._setup_test_boilerplates(run_folder)
        initial_state = self._build_initial_state(
            target_dir=target_dir,
            target_url=target_url,
            log_file=log_file,
            run_folder=run_folder,
            session_headers=session_headers,
            initial_prompt=initial_prompt,
            resumed_messages=resumed_messages,
        )

        # Agent log goes into the run folder
        agent_log_path = Path(run_folder) / "campaign_agent.log"
        agent_log_path.parent.mkdir(parents=True, exist_ok=True)

        # Authoritative accumulated message list.
        accumulated_messages: list = list(initial_state.get("messages", []))
        iteration, findings_count = self._resolve_resume_counters(
            accumulated_messages=accumulated_messages,
            resumed_messages=resumed_messages,
            resumed_turn=resumed_turn,
            resumed_findings=resumed_findings,
        )

        agent_start = time.monotonic()

        # Checkpoint debouncing â€” write every N iterations or M seconds, not every event
        _CHECKPOINT_INTERVAL_TURNS = int(os.environ.get("FAULTLINE_CHECKPOINT_INTERVAL", "5"))
        _CHECKPOINT_INTERVAL_SECS = 30.0
        _last_checkpoint_turn = 0
        _last_checkpoint_time = agent_start

        # Progress tracker â€” keeps the agent aware of its plan, budget, and progress
        tracker = self._init_progress_tracker(agent_start, iteration)

        # Start the input handler for Esc key detection
        if input_handler:
            input_handler.start()

        transcript_path = Path(run_folder) / "transcript.txt"

        self._write_transcript_header(transcript_path, campaign_id, target_url, target_dir)
        self._write_transcript_line(transcript_path, "Operator", initial_prompt)

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
                        async for event in self.app.astream(
                            initial_state,
                            config={"metadata": {"campaign_id": campaign_id}}
                        ):
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
                                session_vars=self._session_vars_snapshot(),
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
                                    findings_count=findings_count,
                                )
                                if renderer:
                                    renderer.show_message(
                                        f"  Checkpoint saved at turn {iteration}. "
                                        f"Resume with: python faultline.py --resume {run_folder}",
                                        style="green",
                                    )
                                f.write(f"\n=== Campaign paused by operator at turn {iteration} ===\n")
                                try:
                                    await _live_report.append_session_end(turn=iteration, reason="paused-by-operator")
                                except Exception:
                                    pass
                                return "Campaign paused. Checkpoint saved."

                            elif action.type == ActionType.SKIP:
                                f.write(f"\n=== Agent phase skipped by operator at turn {iteration} ===\n")
                                if renderer:
                                    renderer.show_message("  Agent phase skipped.", style="yellow")
                                try:
                                    await _live_report.append_session_end(turn=iteration, reason="skipped-by-operator")
                                except Exception:
                                    pass
                                return "Agent phase skipped."

                            elif action.type == ActionType.FINISH:
                                f.write("\n=== OPERATOR ACTION: FINISH ===\n")
                                if renderer:
                                    renderer.show_message("ðŸ Operator requested immediate finish. Forced synthesis engaged.")
                                finish_msg = HumanMessage(
                                    content=(
                                        "[SYSTEM] The operator has requested an immediate conclusion. "
                                        "STOP all testing. Summarize all findings into the final report "
                                        "now and end with [DONE]."
                                    )
                                )
                                accumulated_messages.append(finish_msg)
                                # Force turn limit to trigger soon
                                tracker.max_turns = min(tracker.max_turns, iteration + 2)
                                input_handler.resume_polling()
                                should_restart = True
                                break

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
                                    findings_count=findings_count,
                                )
                                if renderer:
                                    renderer.show_message(f"  Checkpoint saved: {ckpt_path}", style="green")
                                input_handler.resume_polling()
                                should_restart = True
                                break

                            elif action.type == ActionType.STEER:
                                steering_msg = HumanMessage(
                                    content=(
                                        "[OPERATOR_STEERING]\n"
                                        f"{action.text}\n\n"
                                        "RESPONSE REQUIREMENT: In your next response, first provide:\n"
                                        "1) Steering acknowledgment (1-2 lines)\n"
                                        "2) Plan delta (what changed)\n"
                                        "3) Next concrete action\n"
                                    )
                                )
                                accumulated_messages.append(steering_msg)
                                f.write(f"\n=== Operator steering: {action.text} ===\n")
                                self._write_transcript_line(transcript_path, "Operator (steering)", action.text)
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

                            elif action.type == ActionType.SETVAR:
                                ok, msg_set = self._apply_session_var(action.key, action.value)
                                if renderer:
                                    renderer.show_message(
                                        f"  Session var update: {msg_set}",
                                        style="green" if ok else "bold red",
                                    )
                                if ok:
                                    self._write_transcript_line(transcript_path, "Operator (setvar)", f"{action.key}={action.value}")
                                    if session_store:
                                        session_store.append_event("setvar", {
                                            "key": action.key,
                                            "value": action.value,
                                        })
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
                            # No event yet â€” loop back to check Esc
                            if stream_done.is_set() and event_queue.empty():
                                break
                            continue

                        if event is None:
                            # Stream finished
                            break

                        # â”€â”€ Process event LIVE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                        for k, v in event.items():
                            logger.info("Node '%s' executed.", k)
                            f.write(f"\n--- Node: {k} ---\n")

                            if "messages" in v:
                                new_msgs = v.get("messages", [])
                                # Extend the authoritative history â€” don't replace it.
                                # astream(updates) emits per-node deltas, not the full state.
                                accumulated_messages.extend(new_msgs)
                                initial_state["messages"] = accumulated_messages
                                for msg in new_msgs:
                                    f.write(f"[{msg.__class__.__name__}]: {msg.content}\n")
                                    if hasattr(msg, "tool_calls") and msg.tool_calls:
                                        f.write(f"Tool Calls: {json.dumps(msg.tool_calls, indent=2)}\n")
                                    if session_store:
                                        session_store.append(msg)
                                    # â”€â”€ Clean transcript â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                                    _cls = msg.__class__.__name__
                                    if _cls == "AIMessage":
                                        _tc_list = getattr(msg, "tool_calls", None) or []
                                        _tc_names = [tc.get("name", "?") for tc in _tc_list]
                                        if _tc_names:
                                            self._write_transcript_line(
                                                transcript_path,
                                                "Agent (tool calls)",
                                                "\n".join(f"â†’ {n}" for n in _tc_names),
                                            )
                                            # Auto-emit minimal decision entries for non-record_decision calls
                                            _flow_path = Path(run_folder) / "agent_flow.md"
                                            for _tc in _tc_list:
                                                _tname = _tc.get("name", "")
                                                if _tname == "record_decision":
                                                    continue  # agent logged this itself
                                                _args = _tc.get("args", {}) or {}
                                                _ts_flow = time.strftime("%H:%M:%S")
                                                _content = getattr(msg, "content", "") or ""
                                                _situation = str(_content)[:200].strip() if _content else "(see transcript)"
                                                _flow_entry = (
                                                    f"\n### {_ts_flow} â€” {_tname}\n\n"
                                                    f"**Tool:** `{_tname}`\n\n"
                                                    f"**Context:** {_situation}\n\n"
                                                    f"**Args summary:** {str(_args)[:300]}\n\n---\n"
                                                )
                                                try:
                                                    if not _flow_path.exists():
                                                        _flow_path.write_text(
                                                            "# Agent Decision Flow\n\n"
                                                            "Each block captures one tool call the agent made.\n\n---\n",
                                                            encoding="utf-8",
                                                        )
                                                    with open(_flow_path, "a", encoding="utf-8") as _ff:
                                                        _ff.write(_flow_entry)
                                                        _ff.flush()
                                                except Exception:
                                                    pass
                                        elif getattr(msg, "content", ""):
                                            self._write_transcript_line(transcript_path, "Agent", msg.content)
                                    elif _cls == "ToolMessage":
                                        _tname = getattr(msg, "name", "tool")
                                        self._write_transcript_line(
                                            transcript_path,
                                            f"Tool result [{_tname}]",
                                            str(getattr(msg, "content", ""))[:2000],
                                        )
                                    elif _cls == "HumanMessage":
                                        self._write_transcript_line(transcript_path, "Operator", str(getattr(msg, "content", "")))
                            else:
                                f.write(f"State Update: {json.dumps(v, default=str)}\n")
                            f.flush()

                            # â”€â”€ Render to terminal LIVE â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
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
                                                    f"Permission check: execute_chaos_campaign will fire {count} payload(s)\n"
                                                    f"Target: {args.get('target_url', target_url)}\n"
                                                    f"Respond in terminal prompt with: A / B / Esc"
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

                        # â”€â”€ Update progress tracker â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
                        # Detect health from recent errors
                        for msg in reversed(accumulated_messages):
                            if isinstance(msg, ToolMessage) and msg.status == "error":
                                err_text = str(msg.content).lower()
                                if any(s in err_text for s in ["connection refused", "timeout", "network unreachable", "failed to connect"]):
                                    tracker.target_health = "ðŸ”´ CRITICAL: Target Down / Unreachable"
                                    break
                            if isinstance(msg, ToolMessage) and msg.status == "success":
                                tracker.target_health = "ðŸŸ¢ Healthy"
                                break

                        tracker.target_dir = target_dir
                        tracker.target_url = target_url
                        tracker.update(accumulated_messages, iteration, findings_count)

                        # Inject progress context into agent state
                        # (remove previous progress message first to avoid stacking)
                        accumulated_messages = [
                            m for m in accumulated_messages
                            if not (
                                isinstance(m, SystemMessage)
                                and isinstance(m.content, str)
                                and m.content.startswith("â•â•â• PROGRESS STATUS")
                            )
                        ]
                        # Resolve active model for dynamic budget display
                        rt_model, _ = get_active_model()
                        active_model_val = rt_model or os.environ.get("FAULTLINE_MODEL")
                        progress_msg = tracker.build_context_message(run_folder=run_folder, active_model_value=active_model_val)
                        accumulated_messages.append(progress_msg)
                        initial_state["messages"] = accumulated_messages

                        # â”€â”€ Write heartbeat to live_report.md â”€â”€â”€â”€â”€
                        campaign_budget = tracker.token_budget
                        context_budget = self._budget.max_input_tokens

                        budget_pct = min(100, int(tracker.total_tokens_used / max(1, campaign_budget) * 100))
                        _last_tool = tracker.tools_history[-1] if tracker.tools_history else ""
                        try:
                            _live_report.write_heartbeat_sync(
                                turn=tracker.turn,
                                max_turns=tracker.max_turns,
                                llm_calls=self._llm_calls_used,
                                max_llm_calls=self._budget.max_llm_calls,
                                token_pct=budget_pct,
                                findings=tracker.findings_count,
                                last_action=_last_tool,
                            )
                        except Exception:
                            pass

                        # Show progress on CLI
                        if renderer:
                            done = sum(1 for i in tracker.checklist if i.status == "done")
                            total = len(tracker.checklist)
                            elapsed = time.monotonic() - agent_start
                            elapsed_str = f"{elapsed / 60:.1f}m" if elapsed > 60 else f"{elapsed:.0f}s"
                            renderer.show_progress_bar(
                                turn=tracker.turn,
                                max_turns=tracker.max_turns,
                                plan_done=done,
                                plan_total=total,
                                token_pct=budget_pct,
                                findings=tracker.findings_count,
                                elapsed_str=elapsed_str,
                                current_tokens=tracker.total_tokens_used,
                                max_tokens=context_budget,
                                budget_used_tokens=tracker.total_tokens_used,
                                budget_limit_tokens=campaign_budget,
                            )

                        # Phase cap guardrail â€” force phase transition when cap hit
                        _phase_cap_key = f'_phase_cap_sent_{tracker.current_phase}'
                        if tracker.is_phase_capped and not getattr(self, _phase_cap_key, False):
                            setattr(self, _phase_cap_key, True)
                            from core.intelligence.progress_tracker import PHASE_ORDER
                            _cur_idx = PHASE_ORDER.index(tracker.current_phase)
                            _next_phase = PHASE_ORDER[min(_cur_idx + 1, len(PHASE_ORDER) - 1)]
                            _cap = tracker._get_phase_caps()[tracker.current_phase]
                            phase_msg = HumanMessage(
                                content=(
                                    f"[SYSTEM] Phase cap reached: {tracker.current_phase.upper()} "
                                    f"has used {_cap}/{_cap} allocated LLM turns. "
                                    f"STOP all {tracker.current_phase} work immediately. "
                                    f"Advance to {_next_phase.upper()} phase now. "
                                    f"Do not re-read files or repeat discovery. "
                                    f"If you have no findings yet, call record_finding for any "
                                    f"issues observed so far, then proceed."
                                )
                            )
                            accumulated_messages.append(phase_msg)
                            initial_state["messages"] = accumulated_messages
                            f.write(f"\n=== PHASE CAP: {tracker.current_phase} ({_cap} turns) â€” advancing to {_next_phase} ===\n")
                            if renderer:
                                renderer.show_message(
                                    f"  âš ï¸  Phase cap reached ({tracker.current_phase}) â€” forcing transition to {_next_phase}",
                                    style="bold yellow",
                                )

                        # Budget guardrail: if critical (now 60%), force wrap-up
                        if tracker.is_budget_critical and not getattr(self, '_budget_warning_sent', False):
                            self._budget_warning_sent = True
                            budget_msg = HumanMessage(
                                content=(
                                    "[SYSTEM] Token budget has passed 60% â€” "
                                    "call save_vulnerability_report NOW to preserve all findings so far, "
                                    "then you may continue with remaining budget. "
                                    "Do not start new discovery or re-read files already in memory."
                                )
                            )
                            accumulated_messages.append(budget_msg)
                            initial_state["messages"] = accumulated_messages
                            f.write("\n=== BUDGET 60%: Requesting partial report ===\n")
                            if renderer:
                                renderer.show_message(
                                    "  âš ï¸  Token budget at 60% â€” requesting partial report",
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
                                findings_count=findings_count,
                            )
                            _last_checkpoint_turn = iteration
                            _last_checkpoint_time = _now

                except asyncio.CancelledError:
                    logger.info("Agent stream cancelled (Esc pressed)")

        # Stop the input handler
        if input_handler:
            try:
                if hasattr(input_handler, "stop"):
                    input_handler.stop()
            except Exception:
                pass

        # Append session-end marker to live report
        try:
            await _live_report.append_session_end(turn=iteration, reason="completed")
        except Exception:
            pass

        if renderer:
            renderer.show_phase_timing("Agent phase")
            renderer.show_message(
                f"\n[bold green]Campaign Completed.[/bold green]\n\n"
                f"  ðŸ“‚  [bold]Generated Artifacts:[/bold]\n"
                f"  - Final Findings : {run_folder}/vulnerability_report.md\n"
                f"  - Activity Log   : {run_folder}/live_report.md\n"
                f"  - API Index      : {run_folder}/api_test_data.json\n"
                f"  - Test Scripts   : {run_folder}/testcases/\n"
                f"  - Full Transcript: {run_folder}/transcript.txt\n\n"
                f"  ðŸ”„  [bold]Resume Command :[/bold] python faultline.py --resume {run_folder}\n",
                style="green"
            )

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
