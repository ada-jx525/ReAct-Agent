"""Build the policy vector index or inspect retrieval independently of chat."""

import argparse
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

PROJECT = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT / ".env")
sys.path.insert(0, str(PROJECT / "src"))

from react_agent.knowledge.retrieval import retrieve_evidence
from react_agent.knowledge.store import (
    PolicyStore,
    collect_chunks,
    collect_parent_children,
)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", help="Search an existing index instead of building")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="With --query, inspect baseline search without coverage recovery",
    )
    parser.add_argument(
        "--preview", action="store_true", help="Inspect chunks without embedding"
    )
    parser.add_argument("--strategy", choices=["adjacent", "parent_child"])
    args = parser.parse_args()
    store = PolicyStore(strategy=args.strategy)
    try:
        if args.preview:
            if store.strategy == "parent_child":
                chunks, _, manifest = collect_parent_children(store.source)
            else:
                chunks, manifest = collect_chunks(store.source)
            result = {
                "chunks": len(chunks),
                "manifest": manifest,
                "chunk_preview": [
                    {
                        "source_file": c["source_file"],
                        "chunk_index": c["chunk_index"],
                        "chunk_id": c["chunk_id"],
                        "characters": len(c["text"]),
                        "text": c["text"],
                    }
                    for c in chunks
                ],
            }
        else:
            result = (
                (
                    store.safe_search(args.query)
                    if args.raw
                    else retrieve_evidence(args.query, store)
                )
                if args.query
                else store.build()
            )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if result.get("ok") is False:
            raise SystemExit(1)
    except Exception as exc:
        print(
            f"Knowledge operation failed ({type(exc).__name__}); check documents, index and embedding model.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
