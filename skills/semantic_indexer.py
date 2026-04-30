try:
    import faiss
except ImportError:
    faiss = None

import numpy as np
import logging
import pickle
from pathlib import Path
from typing import Any, Dict, List

logger = logging.getLogger("SemanticIndexer")

EMBEDDING_DIM = 1024
EMBEDDING_MODEL = 'Qwen/Qwen3-Embedding-0.6B'
LOCAL_MODEL_DIR = Path("./models/qwen_embedding")

class QwenEmbedder:
    """
    Wraps Qwen3-Embedding-0.6B with local caching support.
    """
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
            
            # Check if we have a local cache
            if LOCAL_MODEL_DIR.exists():
                logger.info(f"[Embedder] Loading {EMBEDDING_MODEL} from local cache: {LOCAL_MODEL_DIR}")
                self._tokenizer = AutoTokenizer.from_pretrained(str(LOCAL_MODEL_DIR), trust_remote_code=True)
                self._model = AutoModel.from_pretrained(str(LOCAL_MODEL_DIR), trust_remote_code=True)
            else:
                logger.info(f"[Embedder] Downloading {EMBEDDING_MODEL} from HuggingFace...")
                self._tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL, trust_remote_code=True)
                self._model = AutoModel.from_pretrained(EMBEDDING_MODEL, trust_remote_code=True)
                
                # Save locally for next time
                logger.info(f"[Embedder] Saving model to local cache: {LOCAL_MODEL_DIR}")
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

class SemanticIndexer:
    def __init__(self, db_path: str, hsnw_m: int = 48):
        """
        Standardized Semantic Indexer using FAISS HNSW.
        """
        if faiss is None:
            logger.error("faiss-cpu not installed. SemanticIndexer disabled.")
            self.index = None
            return

        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)
        
        self.index_file = self.db_path / "faiss.index"
        self.metadata_file = self.db_path / "metadata.pkl"
        
        self.embedder = QwenEmbedder()
        self.dim = EMBEDDING_DIM
        
        if self.index_file.exists():
            self.index = faiss.read_index(str(self.index_file))
            with open(self.metadata_file, "rb") as f:
                self.metadata = pickle.load(f)
        else:
            self.index = faiss.IndexHNSWFlat(self.dim, hsnw_m)
            self.index.hnsw.efConstruction = 128 
            self.index.hnsw.efSearch = 64
            self.metadata = []

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
        root = Path(root_dir)
        texts, metas = [], []
        for path in root.rglob("*.md"):
            if "venv" in str(path):
                continue
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
                texts.append(content)
                metas.append({"path": str(path.relative_to(root)), "type": "documentation"})
        if texts:
            vecs = self.embedder.encode(texts)
            self.index.add(np.array(vecs).astype('float32'))
            for text, meta in zip(texts, metas):
                self.metadata.append({"content": text, "meta": meta})
            self._save()

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
