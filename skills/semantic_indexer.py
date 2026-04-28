import faiss
import numpy as np
import logging
import pickle
import os
import asyncio
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("SemanticIndexer")

EMBEDDING_DIM = 1024
EMBEDDING_MODEL = 'Qwen/Qwen3-Embedding-0.6B'

class QwenEmbedder:
    """
    Wraps Qwen3-Embedding-0.6B with PyTorch dynamic int8 quantization on CPU.
    """
    def __init__(self):
        self._model = None
        self._tokenizer = None
        self._initialized = False

    def _initialize(self):
        if self._initialized:
            return
        import torch
        from transformers import AutoTokenizer, AutoModel
        
        logger.info(f"[Embedder] Loading {EMBEDDING_MODEL} | device=cpu | quant=int8")
        self._tokenizer = AutoTokenizer.from_pretrained(EMBEDDING_MODEL, trust_remote_code=True)
        model = AutoModel.from_pretrained(EMBEDDING_MODEL, trust_remote_code=True)
        self._model = torch.quantization.quantize_dynamic(model, {torch.nn.Linear}, dtype=torch.qint8)
        self._model.eval()
        self._initialized = True

    def _last_token_pool(self, last_hidden_state, attention_mask):
        import torch
        seq_len = attention_mask.sum(dim=1) - 1
        batch_idx = torch.arange(last_hidden_state.size(0))
        return last_hidden_state[batch_idx, seq_len]

    def _normalise(self, vec: np.ndarray) -> np.ndarray:
        norm = np.linalg.norm(vec)
        return vec / norm if norm > 0 else vec

    def encode(self, texts: List[str], batch_size: int = 32) -> List[np.ndarray]:
        if not self._initialized: self._initialize()
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
        hsnw_m: Number of bi-directional links created for every new element during index construction.
        Higher M = higher accuracy but larger memory/construction time. 48 is optimized for high-speed retrieval.
        """
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
            # Optimized HNSW index construction
            # efConstruction: Controls index quality (higher = better search accuracy but slower construction)
            self.index = faiss.IndexHNSWFlat(self.dim, hsnw_m)
            self.index.hnsw.efConstruction = 128 
            self.index.hnsw.efSearch = 64 # High-speed search parameter
            self.metadata = []

    def index_text(self, text: str, meta: Dict[str, Any]):
        vecs = self.embedder.encode([text])
        self.index.add(np.array(vecs).astype('float32'))
        self.metadata.append({"content": text, "meta": meta})
        self._save()

    def index_project_docs(self, root_dir: str):
        root = Path(root_dir)
        texts, metas = [], []
        for path in root.rglob("*.md"):
            if "venv" in str(path): continue
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
        faiss.write_index(self.index, str(self.index_file))
        with open(self.metadata_file, "wb") as f:
            pickle.dump(self.metadata, f)
