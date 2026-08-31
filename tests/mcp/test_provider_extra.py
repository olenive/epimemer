"""Selecting an embedding provider whose extra is not installed.

`sentence-transformers` is the default provider and an optional extra, which
is a combination that can only work if the refusal is good: a bare install
that starts the server gets this error first, and it has to name the fix.
"""

import sys

import pytest

from epimemer.mcp.config import ServerConfig, create_embedding_provider


def test_a_missing_extra_is_named_not_stack_traced(monkeypatch):
    # A `None` entry makes the import raise ImportError, and dropping the
    # cached provider module forces it to re-run that import.
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    monkeypatch.delitem(sys.modules, "epimemer.embeddings.sentence_transformers", raising=False)

    with pytest.raises(RuntimeError) as refused:
        create_embedding_provider(ServerConfig(embedding_provider="sentence-transformers"))

    message = str(refused.value)
    assert "epimemer[sentence-transformers]" in message
    assert "EPIMEMER_EMBEDDING_PROVIDER" in message
    assert isinstance(refused.value.__cause__, ImportError)


def test_the_mock_provider_needs_no_extra(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)
    provider = create_embedding_provider(
        ServerConfig(embedding_provider="mock", embedding_dimension=8)
    )
    assert provider.dimension == 8
