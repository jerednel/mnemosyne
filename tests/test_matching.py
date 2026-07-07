"""Matcher seam tests: default equivalence, embedding scoring with a
deterministic fake backend, semantic layering, and env configuration."""

import pytest
from rapidfuzz import fuzz

from mnemosyne.matching import (
    EmbeddingMatcher,
    Matcher,
    MaxMatcher,
    RapidFuzzMatcher,
    matcher_from_env,
)
from mnemosyne.resolution import IdentityResolver


class FakeBackend:
    """Deterministic embeddings: known texts get fixed vectors, unknown texts
    get a distinct orthogonal-ish vector so cosines are predictable."""

    def __init__(self, vectors: dict[str, list[float]]):
        self.vectors = vectors
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return [self.vectors.get(t, [0.0, 0.0, 1.0]) for t in texts]


def test_rapidfuzz_matcher_equals_raw_wratio():
    matcher = RapidFuzzMatcher()
    mentions_and_candidates = ("databrics", ["databricks", "snowflake", "dbx"])
    assert matcher.score(*mentions_and_candidates) == [
        fuzz.WRatio("databrics", c) for c in mentions_and_candidates[1]
    ]
    assert isinstance(matcher, Matcher)


def test_embedding_matcher_exact_cosines():
    backend = FakeBackend(
        {
            "mention": [1.0, 0.0, 0.0],
            "same": [1.0, 0.0, 0.0],  # cosine 1.0 -> 100
            "orthogonal": [0.0, 1.0, 0.0],  # cosine 0.0 -> 0
            "diagonal": [1.0, 1.0, 0.0],  # cosine ~0.7071 -> ~70.7
        }
    )
    matcher = EmbeddingMatcher(backend)
    scores = matcher.score("mention", ["same", "orthogonal", "diagonal"])
    assert scores[0] == pytest.approx(100.0)
    assert scores[1] == pytest.approx(0.0)
    assert scores[2] == pytest.approx(70.71, abs=0.01)


def test_embedding_matcher_memoizes():
    backend = FakeBackend({"a": [1.0, 0.0, 0.0], "b": [0.0, 1.0, 0.0]})
    matcher = EmbeddingMatcher(backend)
    matcher.score("a", ["b"])
    matcher.score("a", ["b"])
    assert backend.calls == 1  # second call fully memoized


def test_max_matcher_takes_elementwise_max():
    class Constant:
        def __init__(self, values):
            self.values = values

        def score(self, mention, candidates):
            return list(self.values)

    combined = MaxMatcher([Constant([10, 90]), Constant([50, 20])])
    assert combined.score("x", ["a", "b"]) == [50, 90]
    assert combined.score("x", []) == []


def test_semantic_match_resolves_what_fuzzy_cannot(fabric, provenance):
    """A paraphrase ("the ml notebooks vendor") scores far below PROPOSE_SCORE
    lexically but resolves via a semantic backend layered with MaxMatcher."""
    mention_norm = "ml notebooks vendor"
    fuzzy_only = fabric.resolver.resolve("the ml notebooks vendor")
    assert fuzzy_only.status == "not_found"

    backend = FakeBackend(
        {
            mention_norm: [1.0, 0.0, 0.0],
            "databricks": [0.99, 0.1, 0.0],  # semantically near the mention
        }
    )
    semantic_resolver = IdentityResolver(
        fabric.canonical,
        fabric.overlay,
        fabric.ontology,
        matcher=MaxMatcher([RapidFuzzMatcher(), EmbeddingMatcher(backend)]),
    )
    outcome = semantic_resolver.resolve("the ml notebooks vendor")
    assert outcome.status == "auto_matched"
    assert outcome.entity.id == "canon:company/databricks"
    assert outcome.score >= 92


def test_default_resolver_unchanged_without_config(fabric):
    """No-config equivalence: explicit RapidFuzzMatcher produces identical
    outcomes to the default for a table of mentions."""
    explicit = IdentityResolver(
        fabric.canonical, fabric.overlay, fabric.ontology, matcher=RapidFuzzMatcher()
    )
    for mention in ["Databricks", "DBX", "Databrics", "Datbrcks", "Zorbcorp", "postgre"]:
        default_outcome = fabric.resolver.resolve(mention)
        explicit_outcome = explicit.resolve(mention)
        assert default_outcome.status == explicit_outcome.status, mention
        assert default_outcome.score == explicit_outcome.score, mention


def test_matcher_from_env_default_off(monkeypatch):
    monkeypatch.delenv("MNEMOSYNE_EMBEDDINGS", raising=False)
    assert isinstance(matcher_from_env(), RapidFuzzMatcher)
    monkeypatch.setenv("MNEMOSYNE_EMBEDDINGS", "")
    assert isinstance(matcher_from_env(), RapidFuzzMatcher)


def test_matcher_from_env_validation(monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_EMBEDDINGS", "banana:model")
    with pytest.raises(ValueError, match="openai:<model> or voyage:<model>"):
        matcher_from_env()
    monkeypatch.setenv("MNEMOSYNE_EMBEDDINGS", "openai:text-embedding-3-small")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        matcher_from_env()
    monkeypatch.setenv("MNEMOSYNE_EMBEDDINGS", "local:some-model")
    with pytest.raises(RuntimeError, match="embeddings-local"):
        matcher_from_env()


def test_no_network_backend_constructed_when_unset(monkeypatch):
    import mnemosyne.matching as matching

    def boom(*args, **kwargs):
        raise AssertionError("HttpEmbeddingBackend constructed without config")

    monkeypatch.setattr(matching, "HttpEmbeddingBackend", boom)
    monkeypatch.delenv("MNEMOSYNE_EMBEDDINGS", raising=False)
    assert isinstance(matcher_from_env(), RapidFuzzMatcher)


def test_configured_backend_wired(monkeypatch):
    monkeypatch.setenv("MNEMOSYNE_EMBEDDINGS", "openai:text-embedding-3-small")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    matcher = matcher_from_env()
    assert isinstance(matcher, MaxMatcher)
    assert isinstance(matcher.matchers[0], RapidFuzzMatcher)
    assert isinstance(matcher.matchers[1], EmbeddingMatcher)
    # No network call happens at construction time.
