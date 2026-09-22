"""Recursive chunks, Ollama embeddings, FAISS vectors and JSON provenance."""

import hashlib
import json
import logging
import math
import os
import tempfile
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.request import Request, urlopen

import yaml

# On macOS, loading FAISS before PyTorch can crash cross-encoder inference
# because their native OpenMP runtimes initialize in an incompatible order.
# Initialize PyTorch first; do not mask this with KMP_DUPLICATE_LIB_OK.
# isort: off
import torch  # noqa: F401
import faiss

# isort: on
import numpy as np
from langchain_text_splitters import RecursiveCharacterTextSplitter

from react_agent.paths import PROJECT_ROOT

from .reranker import RerankerError, rerank_candidates

PROJECT = PROJECT_ROOT
DEFAULT_SOURCE = PROJECT / "knowledge" / "policies"
DEFAULT_INDEX = PROJECT / "knowledge" / "index" / "policies-faiss"
SPLITTER_CONFIG = {
    "chunk_size": 1000,
    "chunk_overlap": 150,
    "separators": ["\n\n", "\n", ". ", " ", ""],
}
# Changing splitter parameters invalidates an existing index automatically.
_splitter_digest = hashlib.sha256(
    json.dumps(SPLITTER_CONFIG, sort_keys=True).encode()
).hexdigest()[:12]
FORMAT = f"recursive-v1-{_splitter_digest}-gemma-retrieval"
PARENT_CONFIG = {**SPLITTER_CONFIG, "chunk_size": 1800, "chunk_overlap": 0}
CHILD_CONFIG = {**SPLITTER_CONFIG, "chunk_size": 400, "chunk_overlap": 80}
PARENT_FORMAT = (
    FORMAT
    + "-parent-child-"
    + hashlib.sha256(
        json.dumps([PARENT_CONFIG, CHILD_CONFIG], sort_keys=True).encode()
    ).hexdigest()[:12]
)
logger = logging.getLogger(__name__)


class KnowledgeError(Exception):
    """Safe operational error for CLI and tools."""

    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def split_text(text: str) -> list[str]:
    """Recursively split paragraphs, lines, sentences, words, then characters."""
    splitter = RecursiveCharacterTextSplitter(
        **SPLITTER_CONFIG,
        length_function=len,
        is_separator_regex=False,
    )
    return splitter.split_text(text)


def collect_chunks(source: Path, splitter_config=None):
    """Preserve document metadata/filtering while splitting its entire body."""
    chunks, digests = [], {}
    for path in sorted(source.glob("*.md")):  # 找出 source 目录下所有 .md 文件。
        if path.name == "README.md":
            continue
        raw = path.read_text(encoding="utf-8")
        digests[path.name] = hashlib.sha256(
            raw.encode()
        ).hexdigest()  # 存文档状态进哈希表，方便知道文档有没有被改动过
        parts = raw.split("---", 2)
        if len(parts) != 3 or parts[0].strip():  # 检查front matter前面还有没有别的内容
            raise KnowledgeError(
                "invalid_document", f"Missing front matter: {path.name}"
            )
        metadata = yaml.safe_load(parts[1])  # 解析成python字典
        required = (
            "policy_id",
            "title",
            "version",
            "effective_date",
            "status",
            "source_type",
            "is_demo",
        )
        if not isinstance(metadata, dict) or any(
            key not in metadata for key in required
        ):
            raise KnowledgeError("invalid_document", f"Missing metadata: {path.name}")
        metadata["effective_date"] = str(metadata["effective_date"])
        if (
            metadata["status"] != "active"
            or date.fromisoformat(metadata["effective_date"]) > datetime.now(UTC).date()
        ):
            continue
        texts = (
            RecursiveCharacterTextSplitter(**splitter_config).split_text(parts[2])
            if splitter_config
            else split_text(parts[2])
        )
        for i, text in enumerate(texts):
            # chunk的身份字符串
            identity = f"{path.name}|{metadata['version']}|{i}|{text}"
            chunks.append(
                {
                    **metadata,
                    "source_file": path.name,
                    "chunk_index": i,
                    # Compatibility locator, not an inferred Markdown heading.
                    "section": f"chunk_index={i}",
                    "chunk_id": hashlib.sha256(identity.encode()).hexdigest(),
                    "text": text,
                }
            )
    if not chunks:
        raise KnowledgeError("empty_corpus", "No active policy chunks found.")
    manifest = hashlib.sha256(json.dumps(digests, sort_keys=True).encode()).hexdigest()
    return chunks, manifest  # 返回所有的chunk和指纹


def collect_parent_children(source: Path):
    """Split each parent independently so a child never crosses its boundary."""
    parents, manifest = collect_chunks(source, PARENT_CONFIG)
    children = []
    splitter = RecursiveCharacterTextSplitter(**CHILD_CONFIG)
    for parent in parents:
        for i, text in enumerate(splitter.split_text(parent["text"])):
            identity = f"{parent['chunk_id']}|child|{i}|{text}"
            children.append(
                {
                    **parent,
                    "text": text,
                    "chunk_index": i,
                    "section": f"parent={parent['chunk_index']}, child={i}",
                    "parent_chunk_id": parent["chunk_id"],
                    "chunk_id": hashlib.sha256(identity.encode()).hexdigest(),
                }
            )
    return children, parents, manifest


def normalize(vector):
    """Validate and normalize a nonzero vector for cosine similarity."""
    if (
        not isinstance(vector, list)
        or not vector
        or any(not isinstance(v, (int, float)) or not math.isfinite(v) for v in vector)
    ):
        raise KnowledgeError(
            "invalid_embedding", "Embedding response contains an invalid vector."
        )
    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0:
        raise KnowledgeError("invalid_embedding", "Embedding vector is zero.")
    return [v / norm for v in vector]


def merge_adjacent(matches):
    """Combine selected adjacent chunks without deleting distinct policy clauses.

    Only exact suffix/prefix overlap is removed. Bilingual semantic equivalents
    are retained: similarity alone cannot prove their exceptions are identical.
    Every original chunk ID/index is preserved for attribution.
    """
    groups = []
    for match in sorted(
        matches,
        key=lambda c: (
            c["source_file"],
            c["policy_id"],
            c["version"],
            c["chunk_index"],
        ),
    ):
        ref = {"chunk_index": match["chunk_index"], "chunk_id": match["chunk_id"]}
        if groups:
            previous = groups[-1]
            same_document = all(
                previous[k] == match[k]
                for k in ("source_file", "policy_id", "version", "effective_date")
            )
            if (
                same_document
                and previous["source_chunks"][-1]["chunk_index"] + 1
                == match["chunk_index"]
            ):
                left, right = previous["text"], match["text"]
                # A shared punctuation mark/short word is not reliable overlap.
                overlap = next(
                    (
                        size
                        for size in range(min(len(left), len(right)), 31, -1)
                        if left[-size:] == right[:size]
                    ),
                    0,
                )
                previous["text"] = left + (
                    right[overlap:] if overlap else "\n\n" + right
                )
                previous["source_chunks"].append(ref)
                previous["score"] = max(previous["score"], match["score"])
                previous["merged"] = True
                if "rerank_score" in match:
                    previous["rerank_score"] = max(
                        previous["rerank_score"], match["rerank_score"]
                    )
                    previous["rerank_relevance"] = max(
                        previous["rerank_relevance"], match["rerank_relevance"]
                    )
                continue
        groups.append({**match, "source_chunks": [ref], "merged": False})
    return sorted(
        groups,
        key=lambda c: (
            -c.get("rerank_relevance", c["score"]),
            c["source_file"],
            c["chunk_index"],
        ),
    )


class PolicyStore:
    """Persistent vectors for a small corpus; no ANN server or model-selected paths."""

    def __init__(self, source=None, index=None, model=None, strategy=None):
        self.strategy = strategy or os.environ.get("KNOWLEDGE_STRATEGY", "adjacent")
        if self.strategy not in ("adjacent", "parent_child"):
            raise KnowledgeError(
                "invalid_configuration",
                "KNOWLEDGE_STRATEGY must be adjacent or parent_child.",
            )
        self.source = Path(
            source or os.environ.get("KNOWLEDGE_SOURCE_PATH", DEFAULT_SOURCE)
        ).resolve()
        default_index = (
            DEFAULT_INDEX
            if self.strategy == "adjacent"
            else DEFAULT_INDEX.with_name("policies-faiss-parent-child")
        )
        self.index = Path(
            index or os.environ.get("KNOWLEDGE_INDEX_PATH", default_index)
        ).resolve()
        self.format = FORMAT if self.strategy == "adjacent" else PARENT_FORMAT
        self.model = model or os.environ.get("EMBEDDING_MODEL", "embeddinggemma")
        self.base_url = os.environ.get(
            "OLLAMA_BASE_URL", "http://127.0.0.1:11434"
        ).rstrip("/")

    def embed(self, texts):
        """Use Ollama embed API; never silently truncate input or download a model."""
        keep_alive = os.environ.get("EMBEDDING_KEEP_ALIVE", "10m").strip()
        try:
            timeout = float(os.environ.get("EMBEDDING_TIMEOUT_SECONDS", "60"))
        except ValueError as exc:
            raise KnowledgeError(
                "invalid_configuration", "Invalid embedding timeout."
            ) from exc
        if not keep_alive or len(keep_alive) > 32 or not 1 <= timeout <= 300:
            raise KnowledgeError(
                "invalid_configuration", "Invalid embedding runtime configuration."
            )
        body = {
            "model": self.model,
            "input": texts,
            "truncate": False,
            "keep_alive": keep_alive,
        }
        request = Request(
            self.base_url + "/api/embed",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request, timeout=timeout) as response:
            vectors = json.load(response)["embeddings"]
        if len(vectors) != len(texts):
            raise KnowledgeError("invalid_embedding", "Embedding count mismatch.")
        return [normalize(vector) for vector in vectors]

    def build(self):
        """Publish a complete FAISS/JSON generation through an atomic pointer.

        Previous generations are retained; a failed build cannot replace the
        active pair. Only load these locally generated, trusted index files.
        """
        if self.index.exists() and not self.index.is_dir():
            raise KnowledgeError(
                "invalid_configuration",
                "FAISS index path must be a directory, not a SQLite file.",
            )
        if self.strategy == "parent_child":
            chunks, parents, manifest = collect_parent_children(self.source)
        else:
            chunks, manifest = collect_chunks(self.source)
            parents = []
        vectors = []
        for start in range(0, len(chunks), 8):
            batch = chunks[start : start + 8]
            vectors.extend(
                self.embed(
                    [
                        f"title: {c['title']} / {c['section']} | text: {c['text']}"
                        for c in batch
                    ]
                )
            )
        dimension = len(vectors[0])
        if any(len(v) != dimension for v in vectors):
            raise KnowledgeError("invalid_embedding", "Embedding dimensions differ.")
        matrix = np.asarray([normalize(v) for v in vectors], dtype="float32")
        if not np.isfinite(matrix).all():
            raise KnowledgeError(
                "invalid_embedding", "Embedding cannot be represented as float32."
            )
        faiss.normalize_L2(matrix)
        index = faiss.IndexFlatIP(dimension)
        index.add(matrix)
        self.index.mkdir(parents=True, exist_ok=True)
        generation = uuid.uuid4().hex
        destination = self.index / generation
        with tempfile.TemporaryDirectory(
            prefix=".building-", dir=self.index
        ) as staging:
            stage = Path(staging)
            faiss.write_index(index, str(stage / "index.faiss"))
            meta = {
                "model": self.model,
                "dimension": dimension,
                "manifest": manifest,
                "format": self.format,
                "backend": "faiss-flat-ip-v1",
                "parents": parents,
                "built_at": datetime.now(UTC).isoformat(),
                "chunks": chunks,
            }
            (stage / "metadata.json").write_text(
                json.dumps(meta, ensure_ascii=False), encoding="utf-8"
            )
            checksums = {}
            for name in ("index.faiss", "metadata.json"):
                checksums[name] = hashlib.sha256(
                    (stage / name).read_bytes()
                ).hexdigest()
                with (stage / name).open("rb") as handle:
                    os.fsync(handle.fileno())
            stage.rename(destination)
        # Unique temporary pointer also makes concurrent builders safe.
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=self.index, prefix=".pointer-", delete=False
        ) as handle:
            pointer = Path(handle.name)
            json.dump({"generation": generation, "checksums": checksums}, handle)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.replace(pointer, self.index / "current.json")
        finally:
            pointer.unlink(missing_ok=True)
        return {
            "chunks": len(chunks),
            "parents": len(parents),
            "strategy": self.strategy,
            "dimension": dimension,
            "index": str(self.index),
            "index_file": str(destination / "index.faiss"),
            "metadata_file": str(destination / "metadata.json"),
        }

    def _load_index(self):
        """Resolve one immutable generation and validate its paired artifacts."""
        if self.index.is_file():
            raise KnowledgeError(
                "invalid_configuration",
                "FAISS index path must be a directory; rebuild using the new default path.",
            )
        if not (self.index / "current.json").is_file():
            raise KnowledgeError(
                "index_missing",
                "Build the policy index with scripts/build_knowledge.py first.",
            )
        try:
            pointer = json.loads(
                (self.index / "current.json").read_text(encoding="utf-8")
            )
            generation = pointer["generation"]
            if (
                not isinstance(generation, str)
                or len(generation) != 32
                or any(c not in "0123456789abcdef" for c in generation)
            ):
                raise ValueError("Invalid generation")
            directory = self.index / generation
            for name in ("index.faiss", "metadata.json"):
                if (
                    hashlib.sha256((directory / name).read_bytes()).hexdigest()
                    != pointer["checksums"][name]
                ):
                    raise ValueError("Artifact checksum mismatch")
            meta = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
            index = faiss.read_index(str(directory / "index.faiss"))
            if (
                not isinstance(index, faiss.IndexFlatIP)
                or index.metric_type != faiss.METRIC_INNER_PRODUCT
                or meta["backend"] != "faiss-flat-ip-v1"
                or index.d != meta["dimension"]
                or not isinstance(meta["chunks"], list)
                or index.ntotal != len(meta["chunks"])
            ):
                raise ValueError("Index and metadata differ")
            return index, meta
        except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
            raise KnowledgeError(
                "invalid_index",
                "Policy index is incomplete or inconsistent; rebuild it.",
            ) from exc

    def search(self, query, top_k=3):
        """Return cited evidence, not an approval; detect changed source/model."""
        if not isinstance(query, str) or not query.strip() or len(query) > 1000:
            raise KnowledgeError(
                "invalid_argument", "Query must contain 1-1000 characters."
            )
        if not isinstance(top_k, int) or not 1 <= top_k <= 5:
            raise KnowledgeError("invalid_argument", "top_k must be between 1 and 5.")
        if self.strategy == "parent_child":
            current_chunks, parents, manifest = collect_parent_children(self.source)
        else:
            current_chunks, manifest = collect_chunks(self.source)
            parents = []
        index, meta = self._load_index()
        if (
            meta.get("model") != self.model
            or meta.get("manifest") != manifest
            or meta.get("format") != self.format
        ):
            raise KnowledgeError(
                "index_stale",
                "Documents or embedding configuration changed; rebuild the index.",
            )
        if meta["chunks"] != current_chunks:
            raise KnowledgeError(
                "index_stale", "Active policy chunks changed; rebuild the index."
            )
        if self.strategy == "parent_child" and meta.get("parents") != parents:
            raise KnowledgeError(
                "invalid_index", "Parent metadata differs; rebuild the index."
            )
        query_vector = self.embed(["task: search result | query: " + query.strip()])[0]
        if len(query_vector) != int(meta["dimension"]):
            raise KnowledgeError(
                "index_stale", "Embedding dimensions changed; rebuild the index."
            )
        query_matrix = np.asarray([normalize(query_vector)], dtype="float32")
        faiss.normalize_L2(query_matrix)
        scores, positions = index.search(query_matrix, index.ntotal)
        scored = [
            (
                meta["chunks"][int(position)],
                index.reconstruct(int(position)).tolist(),
                float(score),
            )
            for score, position in zip(scores[0], positions[0])
            if position >= 0
        ]
        scored.sort(key=lambda c: (-c[2], c[0]["chunk_id"]))
        threshold = float(os.environ.get("KNOWLEDGE_MIN_SCORE", "0.35"))
        if not math.isfinite(threshold) or not -1 <= threshold <= 1:
            raise KnowledgeError("invalid_configuration", "Invalid cosine threshold.")
        try:
            fetch_k = int(os.environ.get("KNOWLEDGE_FETCH_K", "10"))
            relevance_gap = float(os.environ.get("KNOWLEDGE_RELEVANCE_GAP", "0.10"))
        except ValueError as exc:
            raise KnowledgeError(
                "invalid_configuration", "Invalid retrieval configuration."
            ) from exc
        if not top_k <= fetch_k <= 100:
            raise KnowledgeError(
                "invalid_configuration", "fetch_k must cover top_k and be at most 100."
            )
        if not math.isfinite(relevance_gap) or not 0 <= relevance_gap <= 2:
            raise KnowledgeError(
                "invalid_configuration", "Relevance gap must be in [0, 2]."
            )
        candidates = [c for c in scored if c[2] >= threshold][:fetch_k]
        enabled = os.environ.get("RERANK_ENABLED", "true").lower()
        if enabled not in ("true", "false"):
            raise KnowledgeError(
                "invalid_configuration", "RERANK_ENABLED must be true or false."
            )
        # Conservative query-relative cutoff: do not fill slots with candidates
        # far below the best hit. This is a heuristic, not a trained reranker.
        relative_cutoff = (
            max(threshold, candidates[0][2] - relevance_gap)
            if candidates
            else threshold
        )
        rerank_meta = {}
        if enabled == "true":
            try:
                ranked = rerank_candidates(query.strip(), candidates)
            except RerankerError as exc:
                raise KnowledgeError("reranker_unavailable", str(exc)) from exc
            # Raw logits order passages; they cannot prove answer sufficiency.
            relevant = ranked
            rerank_meta = {
                "rerank_filter_enabled": False,
                "rerank_score_type": "uncalibrated_logit",
            }
        else:
            relevant = [c for c in candidates if c[2] >= relative_cutoff]
        # Preserve relevance order; no diversity penalty may replace a policy clause.
        selected = [
            {**chunk, "score": round(chunk.get("score", relevance), 6)}
            for chunk, _vector, relevance in relevant[:top_k]
        ]
        budget_skipped = 0
        if self.strategy == "parent_child":
            parent_map = {p["chunk_id"]: p for p in parents}
            matches, seen, characters = [], set(), 0
            try:
                budget = int(os.environ.get("KNOWLEDGE_CONTEXT_CHAR_BUDGET", "6000"))
            except ValueError as exc:
                raise KnowledgeError(
                    "invalid_configuration", "Invalid context budget."
                ) from exc
            if not 1800 <= budget <= 20000:
                raise KnowledgeError(
                    "invalid_configuration",
                    "Context character budget must be 1800-20000.",
                )
            # top_k counts unique parents, not repeated children of one parent.
            selected = []
            for child, _vector, relevance in relevant:
                parent_id = child["parent_chunk_id"]
                if parent_id in seen:
                    continue
                seen.add(parent_id)
                parent = parent_map[parent_id]
                if characters + len(parent["text"]) > budget:
                    budget_skipped += 1
                    continue
                selected.append(child)
                match = {
                    **parent,
                    "score": round(child.get("score", relevance), 6),
                    "source_chunks": [
                        {"chunk_id": parent_id, "chunk_index": parent["chunk_index"]}
                    ],
                    "retrieved_child_chunk_ids": [child["chunk_id"]],
                    "expanded_from_child": True,
                    "merged": False,
                }
                for key in ("rerank_score", "rerank_relevance"):
                    if key in child:
                        match[key] = child[key]
                matches.append(match)
                characters += len(parent["text"])
                if len(matches) == top_k:
                    break
        else:
            matches = merge_adjacent(selected)
        return {
            "ok": True,
            "data": {
                "matches": matches,
                "is_demo": True,
                "no_evidence": not matches,
                "score_type": "cosine_not_probability",
                "vector_backend": "faiss-flat-ip",
                "retrieval_method": (
                    "child_topk_parent_expansion"
                    if self.strategy == "parent_child"
                    else "cross_encoder_topk_merge"
                    if enabled == "true"
                    else "relative_cutoff_topk_merge"
                ),
                "chunk_strategy": self.strategy,
                "context_budget_skipped_parents": budget_skipped,
                "selection_method": "relevance_topk",
                "candidate_count": len(candidates),
                "rerank_enabled": enabled == "true",
                "relative_cutoff": None
                if enabled == "true"
                else round(relative_cutoff, 6),
                **rerank_meta,
                "relevant_count": len(relevant),
                "selected_chunk_count": len(selected),
                "fetch_k": fetch_k,
                "notice": "Evidence only; does not establish order eligibility or authorize actions.",
            },
            "error": None,
        }

    def safe_search(self, query, top_k=3):
        """Expose sanitized errors without filesystem paths or network details."""
        try:
            return self.search(query, top_k)
        except KnowledgeError as exc:
            return {
                "ok": False,
                "data": None,
                "error": {"code": exc.code, "message": str(exc)},
            }
        except Exception as exc:
            logger.error("Policy retrieval failed: %s", type(exc).__name__)
            return {
                "ok": False,
                "data": None,
                "error": {
                    "code": "retrieval_unavailable",
                    "message": "Check the policy index, Ollama service and installed embedding model.",
                },
            }
