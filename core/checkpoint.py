"""
core/checkpoint.py

Checkpoint serialization for Faultline agent campaigns.
Saves and restores the full agent state (messages, metadata, turn count)
so runs can be interrupted and resumed without losing progress.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

logger = logging.getLogger("FaultlineCheckpoint")

# ---------------------------------------------------------------------------
# Message serialization — LangChain messages ↔ JSON-safe dicts
# ---------------------------------------------------------------------------

_TYPE_MAP = {
    "human": HumanMessage,
    "ai": AIMessage,
    "system": SystemMessage,
    "tool": ToolMessage,
}


def _serialize_message(msg: BaseMessage) -> Dict[str, Any]:
    """Convert a single LangChain message to a JSON-safe dict."""
    data: Dict[str, Any] = {
        "type": msg.type,
        "content": msg.content,
    }

    # AIMessage with tool calls
    if isinstance(msg, AIMessage):
        if msg.tool_calls:
            data["tool_calls"] = msg.tool_calls
        if hasattr(msg, "response_metadata") and msg.response_metadata:
            data["response_metadata"] = msg.response_metadata

    # ToolMessage fields
    if isinstance(msg, ToolMessage):
        data["tool_call_id"] = msg.tool_call_id
        data["name"] = msg.name
        if hasattr(msg, "status"):
            data["status"] = msg.status

    return data


def _deserialize_message(data: Dict[str, Any]) -> BaseMessage:
    """Reconstruct a LangChain message from a serialized dict."""
    msg_type = data.get("type", "human")
    content = data.get("content", "")

    if msg_type == "ai":
        kwargs: Dict[str, Any] = {"content": content}
        if data.get("tool_calls"):
            kwargs["tool_calls"] = data["tool_calls"]
        if data.get("response_metadata"):
            kwargs["response_metadata"] = data["response_metadata"]
        return AIMessage(**kwargs)

    if msg_type == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=data.get("tool_call_id", ""),
            name=data.get("name", ""),
            status=data.get("status", "success"),
        )

    if msg_type == "system":
        return SystemMessage(content=content)

    return HumanMessage(content=content)


def serialize_messages(messages: List[BaseMessage]) -> List[Dict[str, Any]]:
    """Convert a list of LangChain messages to JSON-safe dicts."""
    return [_serialize_message(m) for m in messages]


def deserialize_messages(data: List[Dict[str, Any]]) -> List[BaseMessage]:
    """Reconstruct LangChain messages from serialized dicts."""
    return [_deserialize_message(d) for d in data]


# ---------------------------------------------------------------------------
# Checkpoint save / load
# ---------------------------------------------------------------------------

def save_checkpoint(
    run_folder: str,
    messages: List[BaseMessage],
    turn: int,
    target_dir: str = "",
    target_url: str = "",
    log_file: str = "",
    mode: str = "hybrid",
    pipeline_completed: bool = False,
    session_headers: Optional[Dict] = None,
    active_model: Optional[str] = None,
    active_provider: Optional[str] = None,
    findings_count: int = 0,
) -> str:
    """
    Serialize the full campaign state to <run_folder>/checkpoint.json.
    Returns the path to the saved file.
    """
    checkpoint = {
        "version": 1,
        "timestamp": datetime.now().isoformat(),
        "turn": turn,
        "findings_count": findings_count,
        "target_dir": target_dir,
        "target_url": target_url,
        "log_file": log_file,
        "run_folder": run_folder,
        "mode": mode,
        "pipeline_completed": pipeline_completed,
        "active_model": active_model,
        "active_provider": active_provider,
        "session_headers": session_headers or {},
        "messages": serialize_messages(messages),
    }

    path = Path(run_folder) / "checkpoint.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(checkpoint, indent=2, default=str), encoding="utf-8")
    logger.info("Checkpoint saved: turn %d → %s", turn, path)
    return str(path)


def load_checkpoint(run_folder: str) -> Optional[Dict[str, Any]]:
    """
    Load a checkpoint from <run_folder>/checkpoint.json.
    Returns the full checkpoint dict with deserialized messages, or None.
    """
    path = Path(run_folder) / "checkpoint.json"
    if not path.exists():
        return None

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        data["messages"] = deserialize_messages(data.get("messages", []))
        logger.info("Checkpoint loaded: turn %d from %s", data.get("turn", 0), path)
        return data
    except Exception as exc:
        logger.error("Failed to load checkpoint from %s: %s", path, exc)
        return None
