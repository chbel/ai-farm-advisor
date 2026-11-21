"""Text embeddings with intfloat/multilingual-e5-small (MIT) on CPU.

Runs the model's official ONNX export with ONNX Runtime and the HF `tokenizers`
library, so no PyTorch is needed. Model files are downloaded once (pinned
revision) into DATA_DIR and reused.

E5 models require prefixes: "query: " for questions, "passage: " for documents.
Embeddings are mean-pooled over tokens and L2-normalized (cosine = dot product).
"""

import logging
import threading
from pathlib import Path

import httpx
import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

logger = logging.getLogger("advisor.embeddings")

MAX_TOKENS = 512  # model limit


def download_model(repo: str, revision: str, target: Path) -> Path:
    """Fetch tokenizer.json and onnx/model.onnx for a pinned revision (idempotent)."""
    target.mkdir(parents=True, exist_ok=True)
    for remote, local in (("tokenizer.json", "tokenizer.json"), ("onnx/model.onnx", "model.onnx")):
        path = target / local
        if path.exists() and path.stat().st_size > 0:
            continue
        url = f"https://huggingface.co/{repo}/resolve/{revision}/{remote}"
        logger.info("Downloading %s", url)
        tmp = path.with_suffix(".part")
        with httpx.stream("GET", url, follow_redirects=True, timeout=600) as response:
            response.raise_for_status()
            with tmp.open("wb") as fh:
                for block in response.iter_bytes(1 << 20):
                    fh.write(block)
        tmp.replace(path)
    return target


class E5Embedder:
    def __init__(self, model_dir: Path, threads: int = 2) -> None:
        self.tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self.tokenizer.enable_truncation(MAX_TOKENS)
        self.tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        self.session = ort.InferenceSession(
            str(model_dir / "model.onnx"), options, providers=["CPUExecutionProvider"]
        )
        self._input_names = {i.name for i in self.session.get_inputs()}
        self._lock = threading.Lock()  # tokenizer padding state is not thread-safe

    def count_tokens(self, text: str) -> int:
        return len(self.tokenizer.encode(text, add_special_tokens=False).ids)

    def token_ids(self, text: str) -> list[int]:
        return self.tokenizer.encode(text, add_special_tokens=False).ids

    def _embed(self, texts: list[str]) -> np.ndarray:
        with self._lock:
            encodings = self.tokenizer.encode_batch(texts)
        ids = np.array([e.ids for e in encodings], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
        feeds = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(ids)
        hidden = self.session.run(None, feeds)[0]  # [batch, tokens, dim]
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(axis=1) / np.clip(weights.sum(axis=1), 1e-9, None)
        return pooled / np.linalg.norm(pooled, axis=1, keepdims=True)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([f"query: {text}"])[0].tolist()

    def embed_passages(self, texts: list[str], batch_size: int = 16) -> list[list[float]]:
        out: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = [f"passage: {t}" for t in texts[start:start + batch_size]]
            out.extend(self._embed(batch).tolist())
        return out
