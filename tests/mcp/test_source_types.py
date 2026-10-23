"""`_meta.source_types` names node types the way `graph_stats` does.

`nodes_by_type` counts `topic`, `fact` and `inference`, and so does
`source_types` on every tool that fills it. A tool that wrote `facts` instead
gave a client two spellings of one breakdown, and a client reading one of them
saw nothing from the tools that wrote the other.
"""

import pytest

from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    ClaimKind,
    EdgeType,
    EmbeddingRecord,
    Fact,
    Inference,
    NodeEdge,
    NodeType,
    ValueSignal,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig

_TWIN = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
_NODE_TYPES = {node_type.value for node_type in NodeType}


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


async def _embedded(storage, embedding_provider, node):
    await storage.store_node(node)
    await storage.store_embedding(
        EmbeddingRecord(item_id=node.id, model_id=embedding_provider.model_id, vector=_TWIN)
    )
    return node


async def _facts(storage, embedding_provider) -> list[Fact]:
    return [
        await _embedded(
            storage,
            embedding_provider,
            Fact(
                content=content,
                source_id="seg-1",
                claim_kind=ClaimKind.STATE,
                value=ValueSignal(),
            ),
        )
        for content in ("Bonn is the capital.", "The capital is Bonn.")
    ]


async def _inferences(storage, embedding_provider) -> list[Inference]:
    nodes = []
    for content in ("The release is unsafe", "The release cannot be trusted"):
        node = await _embedded(
            storage, embedding_provider, Inference(content=content, source_id="seg-1")
        )
        await storage.store_edge(
            NodeEdge(src_id=node.id, dst_id=BASE_METACONTEXT_ID, type=EdgeType.HAS_METACONTEXT)
        )
        nodes.append(node)
    return nodes


async def test_store_decomposition(storage, embedding_provider):
    config = ServerConfig(storage_backend="memory", embedding_provider="mock")
    segmented, _ = await tools.segment_text(
        "Machine learning needs data.", storage, embedding_provider, config
    )

    _, meta = await tools.store_decomposition(
        document_id=segmented["document_id"],
        segments=[
            {
                "segment_id": segmented["segments"][0]["segment_id"],
                "topics": ["Machine learning"],
                "facts": ["Machine learning needs data."],
                "inferences": ["Data is the bottleneck."],
            }
        ],
        storage=storage,
        embedding_provider=embedding_provider,
        metacontext_id=BASE_METACONTEXT_ID,
    )

    assert meta.source_types == {"topic": 1, "fact": 1, "inference": 1}


async def test_merge_facts_and_its_reversal(storage, embedding_provider):
    sources = await _facts(storage, embedding_provider)

    merged, merge_meta = await tools.merge_facts(
        source_ids=[node.id for node in sources],
        content="Bonn is the capital city.",
        storage=storage,
        embedding_provider=embedding_provider,
    )
    _, reverse_meta = await tools.reverse_merge(merged["fact_id"], storage)

    assert merge_meta.source_types == {"fact": 1}
    assert reverse_meta.source_types == {"fact": 2}


async def test_merge_inferences_and_its_reversal(storage, embedding_provider):
    """A reversal restores whatever kind of node was merged, and says so."""
    sources = await _inferences(storage, embedding_provider)

    merged, merge_meta = await tools.merge_inferences(
        source_ids=[node.id for node in sources],
        content="One reading.",
        storage=storage,
        embedding_provider=embedding_provider,
    )
    _, reverse_meta = await tools.reverse_merge(merged["inference_id"], storage)

    assert merge_meta.source_types == {"inference": 1}
    assert reverse_meta.source_types == {"inference": 2}


async def test_the_readers_use_the_same_names(storage, embedding_provider):
    await _facts(storage, embedding_provider)
    await _inferences(storage, embedding_provider)

    _, searched = await tools.search("Bonn", storage, embedding_provider, record_retrieval=False)
    _, stats = await tools.graph_stats(
        storage, default_reflect_threshold=10, default_backup_threshold=50
    )

    assert set(searched.source_types) <= _NODE_TYPES
    assert set(stats.source_types) == _NODE_TYPES
