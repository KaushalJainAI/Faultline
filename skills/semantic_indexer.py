import warnings

try:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="builtin type SwigPyPacked has no __module__ attribute",
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="builtin type SwigPyObject has no __module__ attribute",
            category=DeprecationWarning,
        )
        warnings.filterwarnings(
            "ignore",
            message="builtin type swigvarlink has no __module__ attribute",
            category=DeprecationWarning,
        )
        import faiss
except Exception:
    # faiss is an optional heavy dep. A missing package raises ImportError,
    # but a binary built against a different NumPy ABI raises AttributeError
    # ("_ARRAY_API not found"). Either way, degrade gracefully to no
    # semantic indexing instead of crashing the whole application.
    faiss = None

import hashlib
import json
import logging
import pickle
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SemanticIndexer")

EMBEDDING_DIM = 1024
EMBEDDING_MODEL = 'Qwen/Qwen3-Embedding-0.6B'
LOCAL_MODEL_DIR = Path("./models/qwen_embedding")
BASE_DB_DIR = "./db/faiss_store"


# ── Per-project path helpers ────────────────────────────────────────────────

def project_db_path(base_dir: str, target_dir: str) -> Path:
    """
    Return the per-project FAISS store path under base_dir.

    Format: <base_dir>/<sanitized_name>_<8-char-md5>/
    Example: ./db/faiss_store/Backend_3f2a1b9c/
    """
    abs_target = str(Path(target_dir).resolve())
    slug = re.sub(r'[^a-zA-Z0-9_-]', '_', Path(abs_target).name)[:24]
    h = hashlib.md5(abs_target.encode()).hexdigest()[:8]
    return Path(base_dir) / f"{slug}_{h}"


def compute_fingerprint(root_dir: str) -> str:
    """
    Fast stat-based fingerprint of all *.md files in root_dir.
    Uses file size + mtime_ns — no content reads required.
    Catches adds, deletes, renames, and content edits.
    """
    root = Path(root_dir)
    entries = []
    for path in sorted(root.rglob("*.md")):
        if "venv" in path.parts:
            continue
        s = path.stat()
        entries.append(f"{path.relative_to(root)}:{s.st_size}:{s.st_mtime_ns}")
    return hashlib.md5("\n".join(entries).encode()).hexdigest()


# ── Embedder ────────────────────────────────────────────────────────────────

import numpy as np


class QwenEmbedder:
    """Wraps Qwen3-Embedding-0.6B with local caching support."""

    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._initialized = False

    def _initialize(self):
        if self._initialized:
            return
        try:
            import torch
            from transformers import AutoTokenizer, AutoModel

            if LOCAL_MODEL_DIR.exists():
                logger.info("[Embedder] Loading %s from local cache: %s", EMBEDDING_MODEL, LOCAL_MODEL_DIR)
                self._tokenizer = AutoTokenizer.from_pretrained(str(LOCAL_MODEL_DIR), trust_remote_code=True)
                self._model = AutoModel.from_pretrained(str(LOCAL_MODEL_DIR), trust_remote_code=True)
            else:
                logger.info("[Embedder] Downloading %s from HuggingFace...", EMBEDDING_MODEL)
                self._tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL, trust_remote_code=True)
                self._model = AutoModel.from_pretrained(EMBEDDING_MODEL, trust_remote_code=True)
                logger.info("[Embedder] Saving model to local cache: %s", LOCAL_MODEL_DIR)
                LOCAL_MODEL_DIR.mkdir(parents=True, exist_ok=True)
                self._tokenizer.save_pretrained(str(LOCAL_MODEL_DIR))
                self._model.save_pretrained(str(LOCAL_MODEL_DIR))

            self._model.eval()
            self._initialized = True
        except ImportError:
            logger.error("ML dependencies (torch, transformers) not installed. QwenEmbedder disabled.")
            raise ImportError("Please install torch and transformers to use semantic search.")

    def _last_token_pool(self, last_hidden_state, attention_mask):
        import torch
        seq_len = attention_mask.sum(dim=1) - 1
        batch_idx = torch.arange(last_hidden_state.size(0))
        return last_hidden_state[batch_idx, seq_len]

    def _normalise(self, vec: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def encode(self, texts: List[str], batch_size: int = 32) -> List[np.ndarray]:
        if not self._initialized:
            self._initialize()
        import torch
        all_vecs: List[np.ndarray] = []
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            inputs = self._tokenizer(batch, return_tensors='pt', padding=True, truncation=True, max_length=512)
            with torch.no_grad():
                out = self._model(**inputs)
                pooled = self._last_token_pool(out.last_hidden_state, inputs['attention_mask'])
            arr = pooled.float().numpy()
            all_vecs.extend(self._normalise(row) for row in arr)
        return all_vecs


# ── Indexer ─────────────────────────────────────────────────────────────────

class SemanticIndexer:
    def __init__(self, db_path: str, hsnw_m: int = 48):
        """
        Standardized Semantic Indexer using FAISS HNSW.
        Each target project gets its own db_path (use project_db_path() to derive it).
        """
        if faiss is None:
            logger.error("faiss-cpu not installed. SemanticIndexer disabled.")
            self.index = None
            return

        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        self.index_file = self.db_path / "faiss.index"
        self.metadata_file = self.db_path / "metadata.pkl"
        self.cache_meta_file = self.db_path / "cache_meta.json"

        self.embedder = QwenEmbedder()
        self.dim = EMBEDDING_DIM
        self._hnsw_m = hsnw_m

        if self.index_file.exists():
            self.index = faiss.read_index(str(self.index_file))
            with open(self.metadata_file, "rb") as f:
                self.metadata = pickle.load(f)
        else:
            self.index = self._fresh_index()
            self.metadata = []

    def _fresh_index(self):
        idx = faiss.IndexHNSWFlat(self.dim, self._hnsw_m)
        idx.hnsw.efConstruction = 128
        idx.hnsw.efSearch = 64
        return idx

    def is_cache_valid(self, root_dir: str) -> bool:
        """
        Return True if the stored fingerprint matches the current state of
        *.md files in root_dir — meaning a re-index is unnecessary.
        """
        if not self.cache_meta_file.exists() or not self.index_file.exists():
            return False
        try:
            meta = json.loads(self.cache_meta_file.read_text(encoding="utf-8"))
            stored_fp = meta.get("content_fingerprint", "")
            current_fp = compute_fingerprint(root_dir)
            return stored_fp == current_fp
        except Exception as exc:
            logger.warning("SemanticIndexer: could not read cache_meta.json: %s", exc)
            return False

    def index_text(self, text: str, meta: Dict[str, Any]):
        if not self.index:
            return
        vecs = self.embedder.encode([text])
        self.index.add(np.array(vecs).astype('float32'))
        self.metadata.append({"content": text, "meta": meta})
        self._save()

    def index_project_docs(self, root_dir: str):
        if not self.index:
            return

        if self.is_cache_valid(root_dir):
            logger.info(
                "SemanticIndexer: cache valid for %s — skipping re-index", root_dir
            )
            return

        logger.info("SemanticIndexer: fingerprint changed (or first run) — re-indexing %s", root_dir)

        # Reset to avoid accumulating duplicate vectors
        self.index = self._fresh_index()
        self.metadata = []

        root = Path(root_dir)
        texts, metas = [], []
        for path in root.rglob("*.md"):
            if "venv" in path.parts:
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
                texts.append(content)
                metas.append({"path": str(path.relative_to(root)), "type": "documentation"})
            except Exception as exc:
                logger.warning("SemanticIndexer: skipping %s: %s", path, exc)

        if not texts:
            logger.info("SemanticIndexer: no *.md files found in %s", root_dir)
            return

        vecs = self.embedder.encode(texts)
        self.index.add(np.array(vecs).astype('float32'))
        for text, meta in zip(texts, metas):
            self.metadata.append({"content": text, "meta": meta})
        self._save()

        # Write cache metadata so next run can skip this work
        try:
            self.cache_meta_file.write_text(json.dumps({
                "target_dir": str(Path(root_dir).resolve()),
                "content_fingerprint": compute_fingerprint(root_dir),
                "indexed_at": datetime.now().isoformat(timespec="seconds"),
                "doc_count": len(texts),
            }), encoding="utf-8")
            logger.info(
                "SemanticIndexer: indexed %d doc(s) → %s", len(texts), self.db_path
            )
        except Exception as exc:
            logger.warning("SemanticIndexer: could not write cache_meta.json: %s", exc)

    def query(self, query_text: str, n_results: int = 3) -> List[Dict]:
        if not self.index:
            return []
        vecs = self.embedder.encode([query_text])
        distances, indices = self.index.search(np.array(vecs).astype('float32'), n_results)
        results = []
        for i, idx in enumerate(indices[0]):
            if idx != -1 and idx < len(self.metadata):
                res = self.metadata[idx].copy()
                res["distance"] = float(distances[0][i])
                results.append(res)
        return results

    def _save(self):
        if not self.index:
            return
        faiss.write_index(self.index, str(self.index_file))
        with open(self.metadata_file, "wb") as f:
            pickle.dump(self.metadata, f)
