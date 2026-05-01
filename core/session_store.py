"""
core/session_store.py

Persistent session storage for the Faultline CLI, modelled after Claude Code
and OpenCode's local session management.

Storage Layout:
    ~/.faultline/
        sessions/
            <project-slug>/                  ← one directory per target project
                <session-id>.jsonl           ← full conversation log (append-only)
                sessions-index.json          ← metadata index for all sessions
        memory/
            <project-slug>/
                MEMORY.md                    ← persistent cross-session context
        history.jsonl                        ← global session index across all projects

Session JSONL Format (one JSON object per line):
    {"ts": "...", "role": "human", "content": "...", "meta": {...}}
    {"ts": "...", "role": "ai", "content": "...", "tool_calls": [...]}
    {"ts": "...", "role": "tool", "name": "...", "content": "...", "tool_call_id": "..."}
    {"ts": "...", "role": "system", "type": "steering", "content": "[OPERATOR] ..."}
    {"ts": "...", "role": "system", "type": "checkpoint", "turn": 12, "model": "..."}

Design Principles:
    - Append-only: JSONL files are never rewritten, only appended to
    - Project isolation: Sessions are scoped by target project path
    - Resumable: Any session can be resumed by loading the JSONL + index entry
    - Memory persistence: MEMORY.md survives across sessions for project familiarity
"""

import json
import logging
import os
import re
import uuid
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

logger = logging.getLogger("FaultlineSession")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

def _faultline_home() -> Path:
    """Return the Faultline data directory (~/.faultline)."""
    if os.name == "nt":
        base = Path(os.environ.get("USERPROFILE", "~"))
    else:
        base = Path.home()
    return base / ".faultline"


def _project_slug(target_dir: str) -> str:
    """
    Convert a project path to a filesystem-safe slug.
    e.g., C:\\Users\\kj\\Desktop\\AIAAS\\Backend → C-Users-kj-Desktop-AIAAS-Backend
    """
    p = str(Path(target_dir).resolve())
    # Replace path separators and colons with dashes
    slug = re.sub(r'[:/\\]+', '-', p).strip('-')
    # Collapse multiple dashes
    slug = re.sub(r'-+', '-', slug)
    return slug


def sessions_dir(target_dir: str) -> Path:
    """Return the sessions directory for a specific project."""
    return _faultline_home() / "sessions" / _project_slug(target_dir)


def memory_dir(target_dir: str) -> Path:
    """Return the memory directory for a specific project."""
    return _faultline_home() / "memory" / _project_slug(target_dir)


def global_history_path() -> Path:
    """Return the path to the global history.jsonl file."""
    return _faultline_home() / "history.jsonl"


# ---------------------------------------------------------------------------
# Session ID generation
# ---------------------------------------------------------------------------

def generate_session_id() -> str:
    """Generate a unique session identifier."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    short_uuid = uuid.uuid4().hex[:8]
    return f"{ts}_{short_uuid}"


# ---------------------------------------------------------------------------
# JSONL message serialization
# ---------------------------------------------------------------------------

def _msg_to_jsonl_entry(msg: BaseMessage) -> Dict[str, Any]:
    """Convert a LangChain message to a JSONL-compatible dict."""
    entry: Dict[str, Any] = {
        "ts": datetime.now().isoformat(),
        "role": msg.type,
        "content": msg.content,
    }

    if isinstance(msg, AIMessage):
        if msg.tool_calls:
            entry["tool_calls"] = msg.tool_calls
        if hasattr(msg, "response_metadata") and msg.response_metadata:
            entry["response_metadata"] = msg.response_metadata

    if isinstance(msg, ToolMessage):
        entry["tool_call_id"] = msg.tool_call_id
        entry["name"] = msg.name
        if hasattr(msg, "status"):
            entry["status"] = msg.status

    return entry


def _jsonl_entry_to_msg(entry: Dict[str, Any]) -> BaseMessage:
    """Reconstruct a LangChain message from a JSONL entry."""
    role = entry.get("role", "human")
    content = entry.get("content", "")

    if role == "ai":
        kwargs: Dict[str, Any] = {"content": content}
        if entry.get("tool_calls"):
            kwargs["tool_calls"] = entry["tool_calls"]
        if entry.get("response_metadata"):
            kwargs["response_metadata"] = entry["response_metadata"]
        return AIMessage(**kwargs)

    if role == "tool":
        return ToolMessage(
            content=content,
            tool_call_id=entry.get("tool_call_id", ""),
            name=entry.get("name", ""),
            status=entry.get("status", "success"),
        )

    if role == "system":
        return SystemMessage(content=content)

    return HumanMessage(content=content)


# ---------------------------------------------------------------------------
# SessionStore — the main interface
# ---------------------------------------------------------------------------

class SessionStore:
    """
    Append-only session store for a specific project and session.

    Usage:
        store = SessionStore(target_dir="/path/to/project")
        store.create_session(mode="hybrid", target_url="http://localhost:8000")
        store.append(HumanMessage(content="Begin campaign"))
        store.append(AIMessage(content="Starting..."))
        # ...later...
        messages = store.load_messages()  # reconstruct full history
    """

    def __init__(self, target_dir: str, session_id: Optional[str] = None):
        self.target_dir = str(Path(target_dir).resolve())
        self.session_id = session_id or generate_session_id()
        self._sessions_dir = sessions_dir(target_dir)
        self._session_file = self._sessions_dir / f"{self.session_id}.jsonl"
        self._index_file = self._sessions_dir / "sessions-index.json"
        self._memory_dir = memory_dir(target_dir)

    @property
    def session_path(self) -> Path:
        return self._session_file

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(
        self,
        mode: str = "hybrid",
        target_url: str = "",
        log_file: str = "",
        run_folder: str = "",
        model: str = "",
        git_branch: str = "",
    ) -> str:
        """
        Create a new session: initialize the JSONL file and add to the index.
        Returns the session ID.
        """
        self._sessions_dir.mkdir(parents=True, exist_ok=True)

        # Write session header as first line
        header = {
            "ts": datetime.now().isoformat(),
            "role": "system",
            "type": "session_start",
            "content": f"Session {self.session_id} started",
            "meta": {
                "session_id": self.session_id,
                "target_dir": self.target_dir,
                "target_url": target_url,
                "mode": mode,
                "log_file": log_file,
                "run_folder": run_folder,
                "model": model,
                "git_branch": git_branch or self._get_git_branch(),
            },
        }
        self._append_line(header)

        # Update sessions-index.json
        self._update_index(
            status="active",
            mode=mode,
            target_url=target_url,
            run_folder=run_folder,
            model=model,
            git_branch=header["meta"]["git_branch"],
        )

        # Append to global history
        self._append_global_history(
            event="session_start",
            mode=mode,
            target_url=target_url,
        )

        logger.info("Session created: %s at %s", self.session_id, self._session_file)
        return self.session_id

    def finalize_session(
        self,
        status: str = "completed",
        turn: int = 0,
        findings_count: int = 0,
        summary: str = "",
    ) -> None:
        """Mark the session as completed/paused in the index."""
        # Write a session footer
        footer = {
            "ts": datetime.now().isoformat(),
            "role": "system",
            "type": "session_end",
            "content": summary or f"Session ended ({status})",
            "meta": {
                "status": status,
                "turn": turn,
                "findings_count": findings_count,
            },
        }
        self._append_line(footer)

        # Update index
        self._update_index(
            status=status,
            turn=turn,
            findings_count=findings_count,
            summary=summary,
        )

        # Global history
        self._append_global_history(
            event=f"session_{status}",
            turn=turn,
            findings_count=findings_count,
        )

    # ------------------------------------------------------------------
    # Append messages
    # ------------------------------------------------------------------

    def append(self, msg: BaseMessage) -> None:
        """Append a single message to the session JSONL."""
        entry = _msg_to_jsonl_entry(msg)
        self._append_line(entry)

    def append_batch(self, messages: List[BaseMessage]) -> None:
        """Append multiple messages at once."""
        for msg in messages:
            self.append(msg)

    def append_event(self, event_type: str, data: Optional[Dict] = None) -> None:
        """Append a system event (steering, checkpoint, model_switch, etc.)."""
        entry = {
            "ts": datetime.now().isoformat(),
            "role": "system",
            "type": event_type,
            "content": json.dumps(data or {}, default=str),
        }
        self._append_line(entry)

    # ------------------------------------------------------------------
    # Load / resume
    # ------------------------------------------------------------------

    def load_messages(self) -> List[BaseMessage]:
        """Load all messages from the session JSONL."""
        if not self._session_file.exists():
            return []

        messages = []
        for line in self._session_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                # Skip system events (session_start, session_end, checkpoint, etc.)
                if entry.get("role") == "system" and entry.get("type") in {
                    "session_start", "session_end", "checkpoint", "model_switch",
                }:
                    continue
                messages.append(_jsonl_entry_to_msg(entry))
            except (json.JSONDecodeError, Exception) as exc:
                logger.warning("Skipping malformed JSONL line: %s", exc)

        return messages

    def load_meta(self) -> Optional[Dict[str, Any]]:
        """Load the session metadata from the index."""
        index = self._read_index()
        return index.get(self.session_id)

    # ------------------------------------------------------------------
    # Memory persistence (MEMORY.md)
    # ------------------------------------------------------------------

    def read_memory(self) -> str:
        """Read the project's persistent MEMORY.md file."""
        memory_file = self._memory_dir / "MEMORY.md"
        if memory_file.exists():
            return memory_file.read_text(encoding="utf-8")
        return ""

    def write_memory(self, content: str) -> None:
        """Write to the project's persistent MEMORY.md file."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = self._memory_dir / "MEMORY.md"
        memory_file.write_text(content, encoding="utf-8")
        logger.info("Memory written: %s", memory_file)

    def append_memory(self, note: str) -> None:
        """Append a note to the project's MEMORY.md."""
        self._memory_dir.mkdir(parents=True, exist_ok=True)
        memory_file = self._memory_dir / "MEMORY.md"
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        line = f"\n## [{ts}]\n{note}\n"
        with open(memory_file, "a", encoding="utf-8") as f:
            f.write(line)

    # ------------------------------------------------------------------
    # Session listing / search
    # ------------------------------------------------------------------

    @classmethod
    def list_sessions(cls, target_dir: str) -> List[Dict[str, Any]]:
        """List all sessions for a project, sorted by most recent first."""
        index = cls._read_index_static(target_dir)
        sessions = list(index.values())
        sessions.sort(key=lambda s: s.get("updated_at", ""), reverse=True)
        return sessions

    @classmethod
    def find_latest_session(cls, target_dir: str, status: Optional[str] = None) -> Optional[str]:
        """Find the most recent session ID, optionally filtered by status."""
        sessions = cls.list_sessions(target_dir)
        for s in sessions:
            if status is None or s.get("status") == status:
                return s.get("session_id")
        return None

    @classmethod
    def list_all_projects(cls) -> List[Dict[str, Any]]:
        """List all projects that have session data."""
        sessions_root = _faultline_home() / "sessions"
        if not sessions_root.exists():
            return []

        projects = []
        for project_dir in sessions_root.iterdir():
            if project_dir.is_dir():
                index_file = project_dir / "sessions-index.json"
                if index_file.exists():
                    try:
                        index = json.loads(index_file.read_text(encoding="utf-8"))
                        session_count = len(index)
                        # Get latest session info
                        latest = max(index.values(), key=lambda s: s.get("updated_at", ""), default={})
                        projects.append({
                            "slug": project_dir.name,
                            "target_dir": latest.get("target_dir", ""),
                            "session_count": session_count,
                            "latest_status": latest.get("status", "unknown"),
                            "latest_updated": latest.get("updated_at", ""),
                        })
                    except Exception:
                        pass
        return projects

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_line(self, data: Dict[str, Any]) -> None:
        """Append a single JSON line to the session file."""
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        with open(self._session_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(data, default=str) + "\n")

    def _read_index(self) -> Dict[str, Dict[str, Any]]:
        """Read the sessions-index.json for this project."""
        if not self._index_file.exists():
            return {}
        try:
            return json.loads(self._index_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    @staticmethod
    def _read_index_static(target_dir: str) -> Dict[str, Dict[str, Any]]:
        """Static version of _read_index for classmethods."""
        index_file = sessions_dir(target_dir) / "sessions-index.json"
        if not index_file.exists():
            return {}
        try:
            return json.loads(index_file.read_text(encoding="utf-8"))
        except Exception:
            return {}

    def _update_index(self, **kwargs) -> None:
        """Update this session's entry in sessions-index.json."""
        index = self._read_index()
        entry = index.get(self.session_id, {
            "session_id": self.session_id,
            "target_dir": self.target_dir,
            "created_at": datetime.now().isoformat(),
        })
        entry["updated_at"] = datetime.now().isoformat()
        entry.update(kwargs)

        # Count messages
        if self._session_file.exists():
            try:
                lines = self._session_file.read_text(encoding="utf-8").strip().splitlines()
                entry["message_count"] = len(lines)
            except Exception:
                pass

        index[self.session_id] = entry
        self._sessions_dir.mkdir(parents=True, exist_ok=True)
        self._index_file.write_text(
            json.dumps(index, indent=2, default=str),
            encoding="utf-8",
        )

    def _append_global_history(self, **kwargs) -> None:
        """Append an entry to the global history.jsonl."""
        history_path = global_history_path()
        history_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "ts": datetime.now().isoformat(),
            "session_id": self.session_id,
            "target_dir": self.target_dir,
            "project_slug": _project_slug(self.target_dir),
            **kwargs,
        }
        with open(history_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, default=str) + "\n")

    def _get_git_branch(self) -> str:
        """Try to get the current git branch of the target project."""
        try:
            import subprocess
            result = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=self.target_dir,
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                return result.stdout.strip()
        except Exception:
            pass
        return ""
