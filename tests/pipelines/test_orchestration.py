"""Tests for the orchestration Petri net."""

import pytest

from petritype.core.executable_graph_components import ExecutableGraphOperations

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import segment_text, store_decomposition
from epimemer.pipelines.orchestration.orchestration_net import (
    MemoryRequest,
    MemoryResult,
    OrchestrationState,
    execute_with_auto_reflect,
    orchestration_net,
    should_auto_reflect,
)
from epimemer.storage.memory import InMemoryStorage


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def embedding_provider() -> MockEmbeddingProvider:
    return MockEmbeddingProvider(model_id="mock-embed", dimension=8)


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        storage_backend="memory",
        embedding_provider="mock",
        decomposition_provider="mock",
    )


async def _prepare_store_payload(storage, embedding_provider, config, content="Test content about AI."):
    """Segment content and build a store_decomposition payload."""
    seg_result, _ = await segment_text(content, storage, embedding_provider, config)
    return {
        "document_id": seg_result["document_id"],
        "segments": [
            {
                "segment_id": s["segment_id"],
                "topics": [f"Topic: {s['text'][:40]}"],
                "facts": [f"Fact: {s['text'][:40]}"],
                "inferences": [f"Inference: {s['text'][:40]}"],
            }
            for s in seg_result["segments"]
        ],
    }


class TestOrchestrationRouting:

    async def test_segment_routes_correctly(
        self, storage, embedding_provider, config
    ):
        request = MemoryRequest(
            action="segment",
            payload={"content": "Test content about AI."},
        )
        graph = orchestration_net(request, storage, embedding_provider, config)
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

        assert fired == 2  # route_request + run_segment
        results = graph.place_named("MemoryResult").tokens
        assert len(results) == 1
        result = results[0]
        assert isinstance(result, MemoryResult)
        assert result.action == "segment"
        assert len(result.result["segments"]) >= 1

    async def test_store_decomposition_routes_correctly(
        self, storage, embedding_provider, config
    ):
        payload = await _prepare_store_payload(storage, embedding_provider, config)

        request = MemoryRequest(action="store_decomposition", payload=payload)
        graph = orchestration_net(request, storage, embedding_provider, config)
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

        assert fired == 2  # route + run_store_decomposition
        result = graph.place_named("MemoryResult").tokens[0]
        assert result.action == "store_decomposition"
        assert result.result["nodes_created"]["topics"] >= 1

    async def test_search_routes_correctly(
        self, storage, embedding_provider, config
    ):
        # Store some content first
        payload = await _prepare_store_payload(
            storage, embedding_provider, config, "Neural networks learn from data."
        )
        await store_decomposition(
            document_id=payload["document_id"],
            segments=payload["segments"],
            storage=storage,
            embedding_provider=embedding_provider,
        )

        search_req = MemoryRequest(
            action="search",
            payload={"query": "Neural networks learn from data.", "k": 5},
        )
        graph = orchestration_net(search_req, storage, embedding_provider, config)
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

        assert fired == 2  # route + run_search
        result = graph.place_named("MemoryResult").tokens[0]
        assert result.action == "search"
        assert len(result.result["nodes"]) > 0

    async def test_reflect_routes_correctly(
        self, storage, embedding_provider, config
    ):
        request = MemoryRequest(action="reflect", payload={})
        graph = orchestration_net(request, storage, embedding_provider, config)
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

        assert fired == 2  # route + run_reflect
        result = graph.place_named("MemoryResult").tokens[0]
        assert result.action == "reflect"
        assert "similar_pairs" in result.result

    async def test_tokens_flow_through_places(
        self, storage, embedding_provider, config
    ):
        request = MemoryRequest(
            action="segment",
            payload={"content": "Some text."},
        )
        graph = orchestration_net(request, storage, embedding_provider, config)

        # Before execution
        assert len(graph.place_named("MemoryRequest").tokens) == 1
        assert len(graph.place_named("SegmentInput").tokens) == 0
        assert len(graph.place_named("MemoryResult").tokens) == 0

        # After routing
        graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
        assert len(graph.place_named("MemoryRequest").tokens) == 0
        assert len(graph.place_named("SegmentInput").tokens) == 1

        # After segment
        graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
        assert len(graph.place_named("SegmentInput").tokens) == 0
        assert len(graph.place_named("MemoryResult").tokens) == 1


class TestAutoReflect:

    def test_should_not_reflect_below_threshold(self):
        state = OrchestrationState(stores_since_reflect=5, auto_reflect_threshold=10)
        assert not should_auto_reflect(state)

    def test_should_reflect_at_threshold(self):
        state = OrchestrationState(stores_since_reflect=10, auto_reflect_threshold=10)
        assert should_auto_reflect(state)

    async def test_auto_reflect_triggers_after_n_stores(
        self, storage, embedding_provider, config
    ):
        state = OrchestrationState(auto_reflect_threshold=2)

        # First store — no reflect
        payload1 = await _prepare_store_payload(storage, embedding_provider, config, "First doc.")
        req1 = MemoryRequest(action="store_decomposition", payload=payload1)
        result1, state, reflect1 = await execute_with_auto_reflect(
            req1, state, storage, embedding_provider, config,
        )
        assert result1.action == "store_decomposition"
        assert reflect1 is None
        assert state.stores_since_reflect == 1

        # Second store — triggers auto-reflect
        payload2 = await _prepare_store_payload(storage, embedding_provider, config, "Second doc.")
        req2 = MemoryRequest(action="store_decomposition", payload=payload2)
        result2, state, reflect2 = await execute_with_auto_reflect(
            req2, state, storage, embedding_provider, config,
        )
        assert result2.action == "store_decomposition"
        assert reflect2 is not None
        assert reflect2.action == "reflect"
        assert state.stores_since_reflect == 0  # Reset after reflect

    async def test_search_does_not_increment_counter(
        self, storage, embedding_provider, config
    ):
        state = OrchestrationState(auto_reflect_threshold=10)

        req = MemoryRequest(action="search", payload={"query": "test"})
        _, state, reflect = await execute_with_auto_reflect(
            req, state, storage, embedding_provider, config,
        )
        assert state.stores_since_reflect == 0
        assert reflect is None
