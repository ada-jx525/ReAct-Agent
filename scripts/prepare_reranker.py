"""Explicitly download the pinned reranker; queries use local files only."""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import snapshot_download

PROJECT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT / ".env")
sys.path.insert(0, str(PROJECT / "src"))

from react_agent.knowledge.reranker import DEFAULT_MODEL_PATH, MODEL_ID, MODEL_REVISION

if __name__ == "__main__":
    destination = Path(
        os.environ.get("RERANK_MODEL_PATH", DEFAULT_MODEL_PATH)
    ).resolve()
    snapshot_download(
        repo_id=MODEL_ID,
        revision=MODEL_REVISION,
        local_dir=str(destination),
        allow_patterns=[
            "config.json",
            "model.safetensors",
            "tokenizer.json",
            "tokenizer_config.json",
            "special_tokens_map.json",
            "sentencepiece.bpe.model",
        ],
    )
    print(
        f"Prepared pinned reranker: {MODEL_ID} @ {MODEL_REVISION}\nLocal path: {destination}"
    )
