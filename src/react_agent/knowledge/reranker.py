"""CPU-only local cross-encoder reranking with explicit model preparation."""

import math
import os
from functools import lru_cache
from pathlib import Path
from threading import Lock

from react_agent.paths import PROJECT_ROOT

MODEL_ID = "cross-encoder/mmarco-mMiniLMv2-L12-H384-v1"
MODEL_REVISION = "1427fd652930e4ba29e8149678df786c240d8825"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "knowledge" / "models" / "reranker"
_lock = Lock()


class RerankerError(Exception):
    """Sanitized error; never authorize fallback after a failed rerank."""


@lru_cache(maxsize=1)
def _load_model(path: str):
    if not Path(path).is_dir():
        raise RerankerError(
            "Prepare the local model with scripts/prepare_reranker.py first."
        )
    try:
        import torch
        from sentence_transformers import CrossEncoder

        torch.set_num_threads(2)
        return CrossEncoder(
            path,
            device="cpu",
            max_length=512,
            local_files_only=True,
            trust_remote_code=False,
            model_kwargs={"use_safetensors": True},
            activation_fn=torch.nn.Identity(),
        )
    except Exception as exc:
        raise RerankerError(
            "Local reranker could not load; check the prepared model and dependencies."
        ) from exc


def rerank_candidates(query, candidates):
    """Score query/passage pairs; preserve cosine and expose uncalibrated logits."""
    if not candidates:
        return []
    path = str(Path(os.environ.get("RERANK_MODEL_PATH", DEFAULT_MODEL_PATH)).resolve())
    with _lock:
        model = _load_model(path)
        texts = [f"{c[0]['title']}\n{c[0]['text']}" for c in candidates]
        try:
            tokenized = model.tokenizer(
                [query] * len(texts), texts, truncation=False, padding=False
            )
            if any(len(ids) > 512 for ids in tokenized["input_ids"]):
                raise RerankerError(
                    "A query/passage pair exceeds 512 tokens; shorten the query or reduce chunk size."
                )
            scores = model.predict(
                [(query, text) for text in texts],
                batch_size=4,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        except RerankerError:
            raise
        except Exception as exc:
            raise RerankerError("Local cross-encoder inference failed.") from exc
    if len(scores) != len(candidates):
        raise RerankerError("Reranker returned an inconsistent score count.")
    ranked = []
    for (chunk, vector, cosine), raw in zip(candidates, scores):
        logit = float(raw)
        if not math.isfinite(logit):
            raise RerankerError("Reranker returned a nonfinite score.")
        relevance = (
            1 / (1 + math.exp(-logit))
            if logit >= 0
            else math.exp(logit) / (1 + math.exp(logit))
        )
        ranked.append(
            (
                {
                    **chunk,
                    "score": round(cosine, 6),
                    "rerank_score": logit,
                    "rerank_relevance": relevance,
                },
                vector,
                relevance,
            )
        )
    ranked.sort(key=lambda c: (-c[0]["rerank_score"], c[0]["chunk_id"]))
    return ranked
