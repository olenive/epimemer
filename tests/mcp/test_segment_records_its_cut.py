"""`segment` records how it cut a document, and says when it doubts the cut.

The document's metadata names the strategy and the passage count, so a reader
of a passage can later tell a rule's boundaries from a judgment's. `doubts`
is the server's own unease about a programmatic cut: it refuses nothing, and
it is a separate key from `warnings` because it argues with nothing the agent
did.
"""

import pytest

from epimemer.core.types import EdgeType
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig

PROSE = (
    "The committee met on Tuesday to review the budget. Several members raised "
    "concerns about the cost of the new building.\n\n"
    "After a long discussion, the proposal was sent back to the finance office "
    "for revision. A vote is expected next month."
)

# A transcript with no blank lines: the paragraph rule leaves it whole.
TRANSCRIPT = "\n".join(
    f"Speaker {i % 2 + 1}: this is line {i} of a long conversation about the harbour works."
    for i in range(20)
)


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


class TestTheDocumentRecordsItsCut:
    @pytest.mark.parametrize("strategy", ["paragraph", "semantic"])
    async def test_strategy_and_count_are_on_the_stored_document(
        self, storage, embedder, config, strategy
    ):
        result, _ = await tools.segment_text(
            PROSE, storage, embedder, config, segmentation_strategy=strategy
        )

        doc = await storage.get_document(result["document_id"])

        assert doc.metadata["segmentation"] == {
            "strategy": strategy,
            "passages": len(result["segments"]),
            "history": [],
        }

    async def test_the_server_default_is_named_rather_than_left_blank(
        self, storage, embedder, config
    ):
        result, _ = await tools.segment_text(PROSE, storage, embedder, config)

        doc = await storage.get_document(result["document_id"])

        assert doc.metadata["segmentation"]["strategy"] == config.segmentation_strategy
        assert doc.metadata["segmentation"]["passages"] == 2

    async def test_caller_metadata_is_kept_alongside(self, storage, embedder, config):
        result, _ = await tools.segment_text(
            PROSE, storage, embedder, config, metadata={"channel": "minutes", "page": 3}
        )

        doc = await storage.get_document(result["document_id"])

        assert doc.metadata["channel"] == "minutes"
        assert doc.metadata["page"] == 3
        assert doc.metadata["segmentation"]["passages"] == 2

    async def test_the_passages_are_stored_under_the_document(self, storage, embedder, config):
        result, _ = await tools.segment_text(PROSE, storage, embedder, config)

        stored = await storage.get_segments_for_document(result["document_id"])

        assert sorted(s.id for s in stored) == sorted(s["segment_id"] for s in result["segments"])

    async def test_the_publisher_is_still_linked(self, storage, embedder, config):
        result, _ = await tools.segment_text(
            PROSE, storage, embedder, config, published_by="Town Gazette"
        )

        edges = await storage.get_edges_from(result["document_id"])

        assert any(e.type == EdgeType.RELATED and e.label == "published_by" for e in edges), edges


class TestSegmentReportsDoubts:
    async def test_clean_prose_raises_none(self, storage, embedder, config):
        result, _ = await tools.segment_text(PROSE, storage, embedder, config)

        assert result["doubts"] == []

    async def test_a_long_document_with_no_blank_lines_is_doubted(self, storage, embedder, config):
        result, _ = await tools.segment_text(TRANSCRIPT, storage, embedder, config)

        assert len(result["segments"]) == 1
        assert result["doubts"] == [
            {"kind": "single_passage", "detail": result["doubts"][0]["detail"]}
        ]
        assert "passage 0" in result["doubts"][0]["detail"]

    async def test_a_doubt_refuses_nothing(self, storage, embedder, config):
        result, _ = await tools.segment_text(TRANSCRIPT, storage, embedder, config)

        stored = await storage.get_segments_for_document(result["document_id"])

        assert len(stored) == 1
        assert "warnings" not in result
