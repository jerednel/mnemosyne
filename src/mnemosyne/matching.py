"""Pluggable matcher seam for identity resolution.

The default RapidFuzzMatcher reproduces the original inline fuzz.WRatio scoring
exactly. An optional embedding matcher layers semantic similarity on top via
MaxMatcher — activated only when MNEMOSYNE_EMBEDDINGS is configured, so the
default path has no new dependencies and identical behavior.

Scores are on the 0-100 scale shared with AUTO_ACCEPT_SCORE / PROPOSE_SCORE."""

from __future__ import annotations

import math
import os
from collections.abc import Sequence
from typing import Protocol, runtime_checkable

import httpx
from rapidfuzz import fuzz


@runtime_checkable
class Matcher(Protocol):
    def score(self, mention: str, candidates: Sequence[str]) -> list[float]: ...


class RapidFuzzMatcher:
    def score(self, mention: str, candidates: Sequence[str]) -> list[float]:
        return [fuzz.WRatio(mention, candidate) for candidate in candidates]


@runtime_checkable
class EmbeddingBackend(Protocol):
    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / norm if norm else 0.0


class EmbeddingMatcher:
    """Cosine similarity mapped to 0-100. Embeddings are memoized per text —
    the candidate pool (canonical names/aliases) is stable across calls."""

    def __init__(self, backend: EmbeddingBackend):
        self.backend = backend
        self._memo: dict[str, list[float]] = {}

    def _embed_all(self, texts: Sequence[str]) -> list[list[float]]:
        missing = [t for t in dict.fromkeys(texts) if t not in self._memo]
        if missing:
            for text, vector in zip(missing, self.backend.embed(missing), strict=True):
                self._memo[text] = vector
        return [self._memo[t] for t in texts]

    def score(self, mention: str, candidates: Sequence[str]) -> list[float]:
        if not candidates:
            return []
        vectors = self._embed_all([mention, *candidates])
        mention_vector, candidate_vectors = vectors[0], vectors[1:]
        return [max(0.0, _cosine(mention_vector, v)) * 100.0 for v in candidate_vectors]


class MaxMatcher:
    """Elementwise max over child matchers — layers semantic similarity on top
    of lexical similarity without ever scoring below either."""

    def __init__(self, matchers: Sequence[Matcher]):
        if not matchers:
            raise ValueError("MaxMatcher requires at least one matcher")
        self.matchers = list(matchers)

    def score(self, mention: str, candidates: Sequence[str]) -> list[float]:
        all_scores = [m.score(mention, candidates) for m in self.matchers]
        return [max(scores) for scores in zip(*all_scores, strict=True)] if candidates else []


class HttpEmbeddingBackend:
    """Thin embeddings-API client (no SDK dependency). Supports the OpenAI and
    Voyage embedding endpoints, which share the {model, input} request shape."""

    def __init__(self, url: str, api_key: str, model: str):
        self.url = url
        self.model = model
        self._client = httpx.Client(headers={"Authorization": f"Bearer {api_key}"}, timeout=30.0)

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        response = self._client.post(self.url, json={"model": self.model, "input": list(texts)})
        response.raise_for_status()
        data = response.json()["data"]
        return [item["embedding"] for item in data]


_PROVIDERS = {
    "openai": ("https://api.openai.com/v1/embeddings", "OPENAI_API_KEY"),
    "voyage": ("https://api.voyageai.com/v1/embeddings", "VOYAGE_API_KEY"),
}


def matcher_from_env() -> Matcher:
    """MNEMOSYNE_EMBEDDINGS unset/empty -> plain RapidFuzzMatcher (default).
    'openai:<model>' or 'voyage:<model>' -> fuzzy + embedding layered via max."""
    spec = os.environ.get("MNEMOSYNE_EMBEDDINGS", "").strip()
    fuzzy = RapidFuzzMatcher()
    if not spec:
        return fuzzy
    provider, _, model = spec.partition(":")
    if provider == "local":
        raise RuntimeError(
            "Local embedding backend not yet bundled — install mnemosyne[embeddings-local] "
            "in a future release, or use openai:<model> / voyage:<model>."
        )
    if provider not in _PROVIDERS or not model:
        raise ValueError(
            f"MNEMOSYNE_EMBEDDINGS must be openai:<model> or voyage:<model>, got {spec!r}"
        )
    url, key_var = _PROVIDERS[provider]
    api_key = os.environ.get(key_var)
    if not api_key:
        raise ValueError(f"MNEMOSYNE_EMBEDDINGS={spec} requires {key_var} to be set")
    return MaxMatcher([fuzzy, EmbeddingMatcher(HttpEmbeddingBackend(url, api_key, model))])
