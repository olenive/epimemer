"""Tests for the orchestration Petri net."""

import pytest

from petritype.core.executable_graph_components import ExecutableGraphOperations

from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.llm.mock import MockDecompositionProvider
from epimemer.mcp.config import ServerConfig
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
def decomposition_provider() -> MockDecompositionProvider:
    return MockDecompositionProvider()


@pytest.fixture
def config() -> ServerConfig:
    return ServerConfig(
        storage_backend="memory",
        embedding_provider="mock",
        decomposition_provider="mock",
    )


class TestOrchestrationRouting:

    async def test_ingest_routes_correctly(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        request = MemoryRequest(
            action="ingest",
            payload={"content": "Test content about AI."},
        )
        graph = orchestration_net(request, storage, embedding_provider, decomposition_provider, config)
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

        # Should have fired: route_request + run_ingest = 2
        assert fired == 2

        results = graph.place_named("MemoryResult").tokens
        assert len(results) == 1
        result = results[0]
        assert isinstance(result, MemoryResult)
        assert result.action == "ingest"
        assert result.result["segments_created"] >= 1

    async def test_search_routes_correctly(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        # Ingest something first so search has data
        ingest_req = MemoryRequest(
            action="ingest",
            payload={"content": "Neural networks learn from data."},
        )
        graph = orchestration_net(ingest_req, storage, embedding_provider, decomposition_provider, config)
        await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

        # Now search
        search_req = MemoryRequest(
            action="search",
            payload={"query": "Neural networks learn from data.", "k": 5},
        )
        graph = orchestration_net(search_req, storage, embedding_provider, decomposition_provider, config)
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

        assert fired == 2  # route + run_search
        result = graph.place_named("MemoryResult").tokens[0]
        assert result.action == "search"
        assert len(result.result["nodes"]) > 0

    async def test_reflect_routes_correctly(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        request = MemoryRequest(action="reflect", payload={})
        graph = orchestration_net(request, storage, embedding_provider, decomposition_provider, config)
        graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

        assert fired == 2  # route + run_reflect
        result = graph.place_named("MemoryResult").tokens[0]
        assert result.action == "reflect"
        assert "topics_merged" in result.result

    async def test_tokens_flow_through_places(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        request = MemoryRequest(
            action="ingest",
            payload={"content": "Some text."},
        )
        graph = orchestration_net(request, storage, embedding_provider, decomposition_provider, config)

        # Before execution
        assert len(graph.place_named("MemoryRequest").tokens) == 1
        assert len(graph.place_named("IngestInput").tokens) == 0
        assert len(graph.place_named("MemoryResult").tokens) == 0

        # After routing
        graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
        assert len(graph.place_named("MemoryRequest").tokens) == 0
        assert len(graph.place_named("IngestInput").tokens) == 1

        # After ingest
        graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
        assert len(graph.place_named("IngestInput").tokens) == 0
        assert len(graph.place_named("MemoryResult").tokens) == 1


class TestAutoReflect:

    def test_should_not_reflect_below_threshold(self):
        state = OrchestrationState(ingestions_since_reflect=5, auto_reflect_threshold=10)
        assert not should_auto_reflect(state)

    def test_should_reflect_at_threshold(self):
        state = OrchestrationState(ingestions_since_reflect=10, auto_reflect_threshold=10)
        assert should_auto_reflect(state)

    async def test_auto_reflect_triggers_after_n_ingestions(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        state = OrchestrationState(auto_reflect_threshold=2)

        # First ingest — no reflect
        req1 = MemoryRequest(action="ingest", payload={"content": "First doc."})
        result1, state, reflect1 = await execute_with_auto_reflect(
            req1, state, storage, embedding_provider, decomposition_provider, config,
        )
        assert result1.action == "ingest"
        assert reflect1 is None
        assert state.ingestions_since_reflect == 1

        # Second ingest — triggers auto-reflect
        req2 = MemoryRequest(action="ingest", payload={"content": "Second doc."})
        result2, state, reflect2 = await execute_with_auto_reflect(
            req2, state, storage, embedding_provider, decomposition_provider, config,
        )
        assert result2.action == "ingest"
        assert reflect2 is not None
        assert reflect2.action == "reflect"
        assert state.ingestions_since_reflect == 0  # Reset after reflect

    async def test_search_does_not_increment_counter(
        self, storage, embedding_provider, decomposition_provider, config
    ):
        state = OrchestrationState(auto_reflect_threshold=10)

        req = MemoryRequest(action="search", payload={"query": "test"})
        _, state, reflect = await execute_with_auto_reflect(
            req, state, storage, embedding_provider, decomposition_provider, config,
        )
        assert state.ingestions_since_reflect == 0
        assert reflect is None
