from __future__ import annotations

import hashlib
import re

import numpy as np


class HashEmbedder:
    """Offline fallback and test embedder: hashed bag of words, L2-normalised. Lexical only."""
    dim = 512

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"[a-z0-9]+", t.lower()):
                h = int.from_bytes(hashlib.md5(w.encode()).digest()[:4], "little")
                out[i, h % self.dim] += 1.0
        n = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(n, 1e-9)


class FastEmbedder:
    """Local ONNX model (no API cost, no data leaves the machine)."""

    def __init__(self, model: str = "BAAI/bge-small-en-v1.5") -> None:
        from fastembed import TextEmbedding

        self._m = TextEmbedding(model_name=model)

    def embed(self, texts: list[str]) -> np.ndarray:
        arr = np.array(list(self._m.embed(texts)), dtype=np.float32)
        return arr / np.maximum(np.linalg.norm(arr, axis=1, keepdims=True), 1e-9)


def make_embedder(kind: str):
    if kind == "hash":
        return HashEmbedder()
    try:
        return FastEmbedder()
    except Exception as e:  # noqa: BLE001 - model download can fail offline
        print(f"[embeddings] fastembed unavailable ({e}); falling back to hash embedder")
        return HashEmbedder()
