"""
core/index_state.py

Tracks the background semantic indexing thread so query_knowledge_base
can wait for completion without blocking the rest of the pipeline.

Usage:
    # pipeline.py kicks it off:
    index_state.start_background_index(target_dir, db_path)

    # query_knowledge_base waits before querying:
    index_state.wait_for_index()
"""

import logging
import threading
from typing import Optional

logger = logging.getLogger("AegisAgent")

_thread: Optional[threading.Thread] = None
_done_event = threading.Event()
_done_event.set()   # starts in "done" state â€” no indexing in progress
_db_path: str = ""
_error: Optional[str] = None


def start_background_index(target_dir: str, db_path: str) -> None:
    """Start semantic indexing in a daemon thread. Returns immediately."""
    global _thread, _done_event, _db_path, _error

    _db_path = db_path
    _error = None
    _done_event.clear()

    def _run() -> None:
        global _error
        try:
            from skills.semantic_indexer import SemanticIndexer
            indexer = SemanticIndexer(db_path=db_path)
            indexer.index_project_docs(target_dir)
            logger.info("index_state: background indexing complete â†’ %s", db_path)
        except Exception as exc:
            _error = str(exc)
            logger.error("index_state: background indexing failed: %s", exc)
        finally:
            _done_event.set()

    _thread = threading.Thread(target=_run, name="FaultlineIndexer", daemon=True)
    _thread.start()
    logger.info(
        "index_state: background indexing started for %s â†’ %s", target_dir, db_path
    )


def wait_for_index(timeout: float = 300.0) -> Optional[str]:
    """
    Block until indexing finishes or timeout expires.
    Returns the error string if indexing failed, None on success.
    """
    _done_event.wait(timeout=timeout)
    return _error


def is_indexing() -> bool:
    """True while the background thread is still running."""
    return not _done_event.is_set()


def current_db_path() -> str:
    """The db_path used by the most recently started background index."""
    return _db_path

