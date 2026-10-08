"""`segment` with the agent's own cuts.

Given `cuts`, the programmatic strategy does not run: the passages are the
ones the anchors state, the document records `agent` as its strategy, and no
doubt is raised, because the agent chose the cut. A malformed list, or `cuts`
beside `segmentation_strategy`, refuses the call before anything is written
(`dev-docs/SEGMENTATION.md` §5.1). Run on every backend through `storage`.
"""

import pytest

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig

# A transcript with no blank lines. The paragraph rule leaves it as one passage
# and doubts the result; the agent cuts it at a change of subject.
TRANSCRIPT = "\n".join(
    [
        *(
            f"INTERVIEWER: Question {i} about the night of the fire, and the hours before it?"
            for i in range(8)
        ),
        "INTERVIEWER: Let us turn to the insurance claim now.",
        *(
            f"WITNESS: Answer {i} about the claim, which was filed the following week."
            for i in range(8)
        ),
    ]
)

FIRE = "INTERVIEWER: Question 0 about"
CLAIM = "INTERVIEWER: Let us turn"


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


class TestTheAgentsCut:
    async def test_the_passages_are_where_the_anchors_say(self, storage, embedder, config):
        result, _ = await tools.segment_text(
            TRANSCRIPT, storage, embedder, config, cuts=[FIRE, CLAIM]
        )

        stored = sorted(
            await storage.get_segments_for_document(result["document_id"]),
            key=lambda s: s.span_start,
        )

        boundary = TRANSCRIPT.index(CLAIM)
        assert [(s.span_start, s.span_end) for s in stored] == [
            (0, boundary - 1),  # the newline before the second anchor is trimmed
            (boundary, len(TRANSCRIPT)),
        ]
        assert [s.id for s in stored] == [s["segment_id"] for s in result["segments"]]

    async def test_the_document_records_the_agent_as_the_strategy(self, storage, embedder, config):
        result, _ = await tools.segment_text(
            TRANSCRIPT, storage, embedder, config, cuts=[FIRE, CLAIM]
        )

        doc = await storage.get_document(result["document_id"])

        assert doc.metadata["segmentation"] == {"strategy": "agent", "passages": 2, "history": []}

    async def test_the_agents_cut_is_not_doubted(self, storage, embedder, config):
        """One anchor leaves this long transcript whole, which the paragraph
        rule's cut is doubted for; the agent's judgment that it is one passage is
        not second-guessed."""
        doubted, _ = await tools.segment_text(TRANSCRIPT, storage, embedder, config)
        chosen, _ = await tools.segment_text(TRANSCRIPT, storage, embedder, config, cuts=[FIRE])

        assert [d["kind"] for d in doubted["doubts"]] == ["single_passage"]
        assert len(chosen["segments"]) == 1
        assert chosen["doubts"] == []

    async def test_the_rest_of_the_call_is_unchanged(self, storage, embedder, config):
        result, _ = await tools.segment_text(
            TRANSCRIPT,
            storage,
            embedder,
            config,
            cuts=[FIRE],
            source="interview-7",
            published_by="County Records",
            metadata={"tape": 3},
        )

        doc = await storage.get_document(result["document_id"])
        edges = await storage.get_edges_from(result["document_id"])

        assert doc.source == "interview-7"
        assert doc.metadata["tape"] == 3
        assert any(e.label == "published_by" for e in edges)
        assert result["active_graph"] == storage.current_database


class TestRefusedBeforeAnythingIsWritten:
    async def test_cuts_with_a_strategy(self, storage, embedder, config):
        with pytest.raises(ValueError, match="`cuts` and `segmentation_strategy`"):
            await tools.segment_text(
                TRANSCRIPT,
                storage,
                embedder,
                config,
                cuts=[FIRE],
                segmentation_strategy="paragraph",
            )

        assert list(await storage.query_documents()) == []

    async def test_a_malformed_list_names_every_problem(self, storage, embedder, config):
        with pytest.raises(ValueError) as refused:
            await tools.segment_text(
                TRANSCRIPT,
                storage,
                embedder,
                config,
                cuts=[CLAIM, "WITNESS:", "INTERVIEWER: never asked this"],
                published_by="County Records",
            )

        message = str(refused.value)
        assert message.startswith("segment wrote nothing: the cuts have 3 problems.")
        assert "before cuts[0]" in message
        assert "cuts[1]" in message and "too short" in message
        assert "cuts[2]" in message and "not found" in message
        assert list(await storage.query_documents()) == []
        assert list(await storage.query_segments()) == []
        assert await storage.get_node_by_content("County Records") is None

    async def test_an_empty_list_is_no_cut_at_all(self, storage, embedder, config):
        with pytest.raises(ValueError, match="no cuts were given"):
            await tools.segment_text(TRANSCRIPT, storage, embedder, config, cuts=[])

        assert list(await storage.query_documents()) == []
