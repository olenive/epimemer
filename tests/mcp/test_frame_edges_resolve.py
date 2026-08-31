"""No `has_metacontext` edge may point at a metacontext that does not exist.

**The invariant, and why it is worth a file of its own.** A node framed by an id
that resolves nowhere is worse off than one stating no frame at all: it shares a
frame with *no other node*, so it is never compared, never merged, and missing
from every scoped search including the frame the author meant. Nothing raises,
nothing is logged, and the node sits there unreachable by every mechanism that
would have questioned it.

Each entry point checked it. What was missing was anything checking that the set
of entry points was still the set somebody had checked — so when
`epimemer frames declare` shipped as a fourth writer, it wrote frame edges with
no validation at all, and would have stamped 208 of them on a real graph whose
`the-real` row did not exist. It was caught by running it, not by the suite.

So this file has two halves, and the second is the one that matters:

1. Every path a caller can name a frame on refuses an id that resolves nowhere.
2. **Those are all the paths there are** — a package scan, so a new writer fails
   here rather than shipping unguarded. An exception list written by whoever
   adds a writer is not a check; a list that fails when it goes stale is.

Nothing defends this at the storage layer on purpose. `store_edge` could refuse
a dangling target, but that puts a read on every frame-edge write and moves a
policy question into the layer that is meant not to have opinions — and the
entry points are few, named below, and cheap to keep honest.
"""

import ast
import pathlib

import pytest

import epimemer
from epimemer.core.types import (
    BASE_METACONTEXT_ID,
    EdgeType,
    EmbeddingRecord,
    JudgeRef,
    NodeEdge,
    Topic,
)
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.frames import declare_frames, reframe_node

DECLARER = JudgeRef(agent_id="the-user", digest="d1")

# Every module that constructs a `has_metacontext` edge, and what makes each one
# safe. Adding a writer means adding a line here *and* a refusal test above —
# which is the point: the scan below fails until you have done both.
FRAME_EDGE_WRITERS: dict[str, str] = {
    # `store_decomposition`, guarded by `require_metacontext(writing=True)`.
    "epimemer/mcp/tools.py": "require_metacontext",
    # `reframe_node`'s `assign`, guarded inline; and `frame_edges`, the shared
    # builder whose callers either validate first (`declare_frames`) or derive
    # the frame from a node that already states it (splits, synthesis, merge).
    "epimemer/pipelines/frames.py": "get_metacontext",
}


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(storage_backend="memory", embedding_provider="mock")


async def _topic(storage, embedder, content, *, frames=()):
    topic = Topic(content=content, source_id="seg1")
    await storage.store_node(topic)
    vectors = await embedder.embed([content])
    await storage.store_embedding(
        EmbeddingRecord(
            item_id=topic.id,
            model_id=embedder.model_id,
            vector=vectors[0],
        )
    )
    for frame in frames:
        await storage.store_edge(
            NodeEdge(
                src_id=topic.id,
                dst_id=frame,
                type=EdgeType.HAS_METACONTEXT,
            )
        )
    return topic


async def _frames_of(storage, node_id) -> set[str]:
    edges = await storage.get_edges_from(node_id, edge_type=EdgeType.HAS_METACONTEXT)
    return {edge.dst_id for edge in edges}


class TestEveryPathThatNamesAFrameChecksIt:
    """One test per entry point, and the list is closed by the scan below."""

    async def test_ingest_refuses_and_writes_nothing(self, storage, embedder, config):
        seg, _ = await tools.segment_text("A doc.", storage, embedder, config)

        with pytest.raises(ValueError, match="does not exist in graph"):
            await tools.store_decomposition(
                document_id=seg["document_id"],
                segments=[
                    {
                        "segment_id": seg["segments"][0]["segment_id"],
                        "topics": ["a topic"],
                        "facts": [],
                        "inferences": [],
                    }
                ],
                storage=storage,
                embedding_provider=embedder,
                metacontext_id="nowhere",
            )

        assert [n for n in await storage.query_nodes() if isinstance(n, Topic)] == []

    async def test_reframe_refuses_an_assignment_that_resolves_nowhere(self, storage, embedder):
        fiction, _ = await tools.create_metacontext("The novel", storage)
        node = await _topic(storage, embedder, "the council", frames=[fiction["metacontext_id"]])

        outcome = await reframe_node(
            storage,
            node_id=node.id,
            withdraw=fiction["metacontext_id"],
            assign="nowhere",
            because="mis-filed",
        )

        assert "no metacontext 'nowhere'" in outcome.reason
        assert await _frames_of(storage, node.id) == {fiction["metacontext_id"]}

    async def test_the_declaration_sweep_refuses_one_too(self, storage, embedder):
        """The gap that shipped. A sweep is the worst place to lose this: it
        stamps in bulk, on exactly the nodes with nothing else to fall back on.
        """
        legacy = await _topic(storage, embedder, "written before the rule")

        with pytest.raises(ValueError, match="no metacontext 'nowhere'"):
            await declare_frames(storage, frame="nowhere", judge=DECLARER)

        assert await _frames_of(storage, legacy.id) == set()


class TestTheDerivedPathsCannotIntroduceOne:
    """Splits, synthesis and merge do not take a frame from the caller — they
    re-state what their inputs already say. So closing the entry points above
    closes the graph, and these assert the derivation rather than a check.
    """

    async def test_a_split_states_only_what_the_parent_states(self, storage, embedder):
        fiction, _ = await tools.create_metacontext("The novel", storage)
        parent = await _topic(
            storage,
            embedder,
            "the novel's politics",
            frames=[fiction["metacontext_id"]],
        )

        await tools.apply_reflection(
            storage,
            embedder,
            splits=[{"topic_id": parent.id, "subtopics": ["the council"]}],
            judge=DECLARER,
        )

        child = next(
            node
            for node in await storage.query_nodes()
            if node.metadata.get("split_from") == parent.id
        )
        assert await _frames_of(storage, child.id) <= await _frames_of(storage, parent.id)

    async def test_a_synthesis_states_only_what_its_children_state(self, storage, embedder):
        a = await _topic(storage, embedder, "the council", frames=[BASE_METACONTEXT_ID])
        b = await _topic(storage, embedder, "the war", frames=[BASE_METACONTEXT_ID])

        await tools.apply_reflection(
            storage,
            embedder,
            parents=[{"children_ids": [a.id, b.id], "content": "politics"}],
            judge=DECLARER,
        )

        parent = next(
            node for node in await storage.query_nodes() if node.metadata.get("synthesized_from")
        )
        assert await _frames_of(storage, parent.id) == {BASE_METACONTEXT_ID}


def _modules_writing_frame_edges() -> set[str]:
    """Every module constructing a `NodeEdge(type=EdgeType.HAS_METACONTEXT)`.

    Parsed rather than grepped, so a mention in a comment or a comparison
    against the edge type is not mistaken for a write — the difference between
    a guard that fails when it should and one that cries wolf until somebody
    loosens it.
    """
    root = pathlib.Path(epimemer.__file__).parent
    found: set[str] = set()
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            callee = node.func
            name = callee.attr if isinstance(callee, ast.Attribute) else getattr(callee, "id", None)
            if name != "NodeEdge":
                continue
            for keyword in node.keywords:
                value = keyword.value
                if (
                    keyword.arg == "type"
                    and isinstance(value, ast.Attribute)
                    and value.attr == "HAS_METACONTEXT"
                ):
                    found.add(str(path.relative_to(root.parent)))
    return found


class TestTheListOfWritersIsClosed:
    """The half that was missing, and the reason the defect shipped.

    Each writer above checked its own frame. Nothing checked that the writers
    were still the ones somebody had checked, so a fourth arrived unguarded and
    only a real graph noticed. This reads the package, because a guard whose
    reach is an accident of where the code happens to sit fails open.
    """

    def test_no_module_writes_a_frame_edge_unaccounted_for(self):
        assert _modules_writing_frame_edges() == set(FRAME_EDGE_WRITERS)

    def test_each_named_writer_still_validates(self):
        """A companion to the list, so an entry cannot go stale by having its
        check quietly deleted while the file keeps writing edges."""
        root = pathlib.Path(epimemer.__file__).parent.parent
        for module, guard in FRAME_EDGE_WRITERS.items():
            source = (root / module).read_text()
            assert guard in source, f"{module} no longer calls {guard}"
