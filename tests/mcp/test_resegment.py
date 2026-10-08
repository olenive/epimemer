"""`resegment`: the agent replaces the server's cut before it decomposes.

The procedure is `dev-docs/SEGMENTATION.md` §3.3: `segment` stores the
document and a rule's passages, the agent reads them and any doubts, re-cuts
if the cut is wrong, then decomposes against the new passages. A re-cut is
possible only while no claim cites a passage; after `store_decomposition` the
passages are provenance and the call is refused with the count. Run on every
backend through `storage`, and once through the MCP boundary.
"""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastmcp import FastMCP

from epimemer.core.types import BASE_METACONTEXT_ID, JudgeRef, Metacontext
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp import tools
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.retrieval_records import new_record_log
from epimemer.mcp.server import mcp as epimemer_mcp
from epimemer.mcp.server import new_connection_state
from epimemer.storage.memory import InMemoryStorage

# Two subjects with no blank line between them, so the paragraph rule leaves
# one passage, which the agent splits where the subject changes.
NOTE = (
    "The harbour wall was repaired in March after the winter storms. The work "
    "took six weeks and closed the north quay. Fishing boats moved to the south "
    "quay for the duration. "
    "Separately, the council raised the bridge toll to two pounds. The rise "
    "takes effect in May and applies to every vehicle. Residents may apply for "
    "a permit at half the price."
)
HARBOUR = "The harbour wall was"
TOLL = "Separately, the council raised"
CUTTER = JudgeRef(agent_id="cutter", digest="d1")


@pytest.fixture
def embedder():
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config():
    return ServerConfig(
        storage_backend="memory", embedding_provider="mock", segmentation_strategy="paragraph"
    )


async def _segmented(storage, embedder, config) -> dict:
    result, _ = await tools.segment_text(NOTE, storage, embedder, config)
    assert len(result["segments"]) == 1
    return result


async def _decompose(storage, embedder, document_id: str, segment_ids: list[str]) -> dict:
    result, _ = await tools.store_decomposition(
        document_id=document_id,
        segments=[
            {"segment_id": segment_id, "facts": [f"A claim from passage {i}"]}
            for i, segment_id in enumerate(segment_ids)
        ],
        storage=storage,
        embedding_provider=embedder,
        metacontext_id=BASE_METACONTEXT_ID,
    )
    return result


class TestTheProcedure:
    async def test_segment_then_resegment_then_decompose(self, storage, embedder, config):
        first = await _segmented(storage, embedder, config)
        old_id = first["segments"][0]["segment_id"]

        recut, _ = await tools.resegment(
            first["document_id"], [HARBOUR, TOLL], storage, judge=CUTTER
        )

        assert recut["resegmented"] is True
        assert recut["replaced"] == 1
        new_ids = [s["segment_id"] for s in recut["segments"]]
        assert len(new_ids) == 2 and old_id not in new_ids
        stored = await storage.get_segments_for_document(first["document_id"])
        assert sorted(s.id for s in stored) == sorted(new_ids)
        assert [s.text.split(".")[0] for s in sorted(stored, key=lambda s: s.span_start)] == [
            "The harbour wall was repaired in March after the winter storms",
            "Separately, the council raised the bridge toll to two pounds",
        ]

        await _decompose(storage, embedder, first["document_id"], new_ids)

        by_source = await storage.get_nodes_by_source(new_ids)
        assert all(len(nodes) == 1 for nodes in by_source.values())

    async def test_the_old_passage_cannot_be_decomposed_against(self, storage, embedder, config):
        first = await _segmented(storage, embedder, config)
        old_id = first["segments"][0]["segment_id"]
        await tools.resegment(first["document_id"], [HARBOUR, TOLL], storage)

        with pytest.raises(ValueError, match="not found"):
            await _decompose(storage, embedder, first["document_id"], [old_id])

    async def test_the_document_keeps_the_cut_it_replaced(self, storage, embedder, config):
        first = await _segmented(storage, embedder, config)

        await tools.resegment(first["document_id"], [HARBOUR, TOLL], storage, judge=CUTTER)

        doc = await storage.get_document(first["document_id"])
        record = doc.metadata["segmentation"]
        assert (record["strategy"], record["passages"]) == ("agent", 2)
        (entry,) = record["history"]
        assert (entry["strategy"], entry["passages"]) == ("paragraph", 1)
        assert entry["replaced_by"] == {"agent_id": "cutter", "digest": "d1"}
        assert "replaced_at" in entry

    async def test_a_second_recut_adds_to_the_history(self, storage, embedder, config):
        first = await _segmented(storage, embedder, config)
        await tools.resegment(first["document_id"], [HARBOUR, TOLL], storage)

        await tools.resegment(first["document_id"], [HARBOUR], storage, judge=CUTTER)

        record = (await storage.get_document(first["document_id"])).metadata["segmentation"]
        assert record["passages"] == 1
        assert [(e["strategy"], e["passages"]) for e in record["history"]] == [
            ("paragraph", 1),
            ("agent", 2),
        ]

    async def test_the_lexical_arm_no_longer_returns_a_replaced_passage(
        self, storage, embedder, config
    ):
        first = await _segmented(storage, embedder, config)
        old_id = first["segments"][0]["segment_id"]
        # Other documents, so the term is rare enough in the corpus to score.
        for filler in ["The mill by the river.", "A fair in May.", "Rents rose again."]:
            await tools.segment_text(filler, storage, embedder, config)

        before, _ = await tools.search("bridge toll", storage, embedder, terms=["toll"])
        recut, _ = await tools.resegment(first["document_id"], [HARBOUR, TOLL], storage)
        after, _ = await tools.search("bridge toll", storage, embedder, terms=["toll"])

        assert [hit["segment_id"] for hit in before["segments"]] == [old_id]
        assert [hit["segment_id"] for hit in after["segments"]] == [
            recut["segments"][1]["segment_id"]
        ]


class TestRefusals:
    async def test_after_decomposition_with_the_count(self, storage, embedder, config):
        first = await _segmented(storage, embedder, config)
        old_id = first["segments"][0]["segment_id"]
        await _decompose(storage, embedder, first["document_id"], [old_id])
        cited = len((await storage.get_nodes_by_source([old_id]))[old_id])

        refused, _ = await tools.resegment(first["document_id"], [HARBOUR, TOLL], storage)

        assert refused["resegmented"] is False
        assert refused["citing_nodes"] == cited > 0
        assert "ingest it again" in refused["refused"]
        stored = await storage.get_segments_for_document(first["document_id"])
        assert [s.id for s in stored] == [old_id]
        doc = await storage.get_document(first["document_id"])
        assert doc.metadata["segmentation"]["history"] == []

    async def test_malformed_cuts_change_nothing(self, storage, embedder, config):
        first = await _segmented(storage, embedder, config)

        with pytest.raises(ValueError) as refused:
            await tools.resegment(first["document_id"], [TOLL, "the toll"], storage)

        # Uncovered opening text, an anchor too short, and the same anchor not found.
        assert str(refused.value).startswith("resegment wrote nothing: the cuts have 3 problems.")
        stored = await storage.get_segments_for_document(first["document_id"])
        assert [s.id for s in stored] == [first["segments"][0]["segment_id"]]

    async def test_an_unknown_document(self, storage):
        with pytest.raises(ValueError, match="not found"):
            await tools.resegment("no-such-document", [HARBOUR], storage)


# --- Through the MCP boundary ---


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict]:
    store = InMemoryStorage()
    store._graphs[store._database].metacontexts[BASE_METACONTEXT_ID] = Metacontext(
        id=BASE_METACONTEXT_ID, content="The Real", description="Claims about the real world."
    )
    yield {
        "storage": store,
        "embedding_provider": MockEmbeddingProvider(model_id="mock-embed", dimension=8),
        "config": ServerConfig(storage_backend="memory", embedding_provider="mock"),
        "event_bus": None,
        "viz_session": None,
        "viz_hub_url": None,
        "retrievals": new_record_log(),
        "connection_state": new_connection_state(),
        "version": "9.9.9",
    }


@pytest.fixture
async def server():
    original = epimemer_mcp._lifespan
    epimemer_mcp._lifespan = _lifespan
    async with _lifespan(epimemer_mcp) as ctx:
        epimemer_mcp._lifespan_result = ctx
        yield epimemer_mcp
        epimemer_mcp._lifespan_result = None
    epimemer_mcp._lifespan = original


async def _call(server: FastMCP, tool: str, args: dict) -> dict:
    result = await server.call_tool(tool, {"expected_graph": "default", **args})
    return json.loads(result.content[0].text)


class TestThroughTheServer:
    async def test_the_whole_flow(self, server):
        first = (await _call(server, "segment", {"content": NOTE}))["result"]
        assert [d["kind"] for d in first["doubts"]] == []  # short enough to leave whole

        recut = (
            await _call(
                server, "resegment", {"document_id": first["document_id"], "cuts": [HARBOUR, TOLL]}
            )
        )["result"]
        stored = (
            await _call(
                server,
                "store_decomposition",
                {
                    "document_id": first["document_id"],
                    "metacontext_id": BASE_METACONTEXT_ID,
                    "segments": [
                        {"segment_id": s["segment_id"], "facts": [f"Claim {i}"]}
                        for i, s in enumerate(recut["segments"])
                    ],
                },
            )
        )["result"]
        again = (
            await _call(
                server, "resegment", {"document_id": first["document_id"], "cuts": [HARBOUR]}
            )
        )["result"]

        assert recut["resegmented"] is True and len(recut["segments"]) == 2
        assert stored["active_graph"] == "default"
        assert again["resegmented"] is False
        assert again["citing_nodes"] == 2

    async def test_a_malformed_cut_comes_back_as_the_refusal(self, server):
        first = (await _call(server, "segment", {"content": NOTE}))["result"]

        response = await _call(
            server, "resegment", {"document_id": first["document_id"], "cuts": ["the toll"]}
        )

        assert response["error"].startswith("resegment wrote nothing")

    async def test_segment_refuses_cuts_beside_a_strategy(self, server):
        response = await _call(
            server,
            "segment",
            {"content": NOTE, "cuts": [HARBOUR], "segmentation_strategy": "paragraph"},
        )

        assert "`cuts` and `segmentation_strategy`" in response["error"]
