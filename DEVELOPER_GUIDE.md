# Developer Guide

## Setup

```bash
# Clone both repos as siblings
git clone <petritype-url> petritype
git clone <epimemer-url> epimemer
cd epimemer

# Install (petritype is an editable local dependency)
uv sync

# Verify
uv run python -m pytest tests/ -q
```

## Running Tests

```bash
# All tests
uv run python -m pytest tests/ -q

# Specific module
uv run python -m pytest tests/pipelines/test_segmentation.py -v

# Specific test class
uv run python -m pytest tests/mcp/test_tools.py::TestIngest -v

# With output (see Petri net token flow)
uv run python -m pytest tests/pipelines/test_orchestration.py -v -s
```

## Debugging Individual Sections

Each pipeline is independently testable. The key to debugging is understanding that every pipeline is a Petri net with typed places and transitions.

### Core Types

All Pydantic models live in `epimemer/core/types.py`. To inspect a model:

```python
from epimemer.core.types import Topic, Timeline, Metacontext
t = Topic(content="test", source_id="s1")
print(t.model_dump_json(indent=2))
```

### Storage Layer

Test with InMemoryStorage (no external dependencies):

```python
import asyncio
from epimemer.storage.memory import InMemoryStorage
from epimemer.core.types import Topic

async def main():
    store = InMemoryStorage()
    t = Topic(content="test topic", source_id="s1")
    await store.store_node(t)
    got = await store.get_node(t.id)
    print(got)

asyncio.run(main())
```

For SurrealDB, use embedded mode (`mem://`) in tests — no server needed:

```python
from epimemer.storage.surrealdb_adapter import SurrealDBStorage

async def main():
    store = SurrealDBStorage(url="mem://")
    await store.connect()
    # ... use store ...
    await store.close()
```

### Segmentation

Debug the paragraph splitter:

```python
import asyncio
from petritype.core.executable_graph_components import ExecutableGraphOperations
from epimemer.core.types import RawDocument
from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net

async def main():
    doc = RawDocument(content="First paragraph.\n\nSecond paragraph.\n\nThird.")
    graph = paragraph_split_segmentation_net(doc)

    # Step through one transition at a time
    graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
    print(f"Fired: {fired}")
    segments = graph.place_named("Segments").tokens
    print(f"Segments: {[s.text for s in segments]}")

asyncio.run(main())
```

### Decomposition

Debug LLM extraction (with mock provider):

```python
import asyncio
from petritype.core.executable_graph_components import ExecutableGraphOperations
from epimemer.core.types import Segment
from epimemer.llm.mock import MockDecompositionProvider
from epimemer.pipelines.decomposition.llm_decomposition import llm_decomposition_net

async def main():
    seg = Segment(source_id="d1", text="AI models learn from data.", span_start=0, span_end=26)
    provider = MockDecompositionProvider()
    graph = llm_decomposition_net(seg, provider)
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

    topics = graph.place_named("Topics").tokens
    facts = graph.place_named("Facts").tokens
    inferences = graph.place_named("Inferences").tokens
    print(f"Topics: {[t.content for t in topics]}")
    print(f"Facts: {[f.content for f in facts]}")
    print(f"Inferences: {[i.content for i in inferences]}")

asyncio.run(main())
```

### Query Layer

Debug hybrid retrieval:

```python
import asyncio
from petritype.core.executable_graph_components import ExecutableGraphOperations
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.query.hybrid_retrieval import hybrid_retrieval_net
from epimemer.pipelines.query.types import QueryRequest
from epimemer.storage.memory import InMemoryStorage

async def main():
    storage = InMemoryStorage()
    emb = MockEmbeddingProvider(model_id="mock", dimension=8)

    # ... populate storage with nodes and embeddings ...

    request = QueryRequest(query_text="your query", k=5, graph_hops=1, model_id="mock")
    graph = hybrid_retrieval_net(request, emb, storage)

    # Step through transitions to see intermediate state
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
    vector_results = graph.place_named("VectorResults").tokens
    print(f"Vector results: {vector_results}")

    graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
    expanded = graph.place_named("ExpandedResults").tokens
    print(f"Expanded: {expanded}")

asyncio.run(main())
```

### Reflection

Debug the reflection pipeline:

```python
import asyncio
from petritype.core.executable_graph_components import ExecutableGraphOperations
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.pipelines.reflection.reflection_net import ReflectionRequest, reflection_net
from epimemer.storage.memory import InMemoryStorage

async def main():
    storage = InMemoryStorage()
    emb = MockEmbeddingProvider(model_id="mock", dimension=8)

    # ... populate storage ...

    request = ReflectionRequest(similarity_threshold=0.85, auto_merge=True)
    graph = reflection_net(request, storage, emb)
    graph, fired = await ExecutableGraphOperations.execute_graph(graph, max_transitions=10)

    consolidation = graph.place_named("ConsolidationResult").tokens[0]
    decay = graph.place_named("DecayResult").tokens[0]
    contradiction = graph.place_named("ContradictionResult").tokens[0]
    print(f"Merged: {consolidation.topics_merged}")
    print(f"Decayed: {decay.nodes_decayed}")
    print(f"Contradictions: {contradiction.pairs_found}")

asyncio.run(main())
```

### Timeline Functions

Timeline functions are pure — no async, no storage needed:

```python
from datetime import datetime, timezone
from epimemer.core.types import Timeline
from epimemer.pipelines.timeline.functions import add_timepoint, find_nearest, get_in_range

tl = Timeline(name="History of AI")
tl, tp1 = add_timepoint(tl, start=datetime(2023, 3, 14, tzinfo=timezone.utc), label="GPT-4")
tl, tp2 = add_timepoint(tl, start=datetime(2024, 5, 13, tzinfo=timezone.utc), label="GPT-4o")
tl, tp3 = add_timepoint(tl, label="sometime in the future")

print(f"Timepoints: {[(tp.label, tp.start) for tp in tl.timepoints]}")

nearest = find_nearest(tl, datetime(2024, 1, 1, tzinfo=timezone.utc), k=1)
print(f"Nearest to 2024-01-01: {nearest[0].label}")
```

### Orchestration Net

Debug request routing:

```python
import asyncio
from petritype.core.executable_graph_components import ExecutableGraphOperations
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.llm.mock import MockDecompositionProvider
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.orchestration.orchestration_net import MemoryRequest, orchestration_net
from epimemer.storage.memory import InMemoryStorage

async def main():
    storage = InMemoryStorage()
    emb = MockEmbeddingProvider(model_id="mock", dimension=8)
    decomp = MockDecompositionProvider()
    config = ServerConfig(storage_backend="memory", embedding_provider="mock", decomposition_provider="mock")

    request = MemoryRequest(action="ingest", payload={"content": "Test content."})
    graph = orchestration_net(request, storage, emb, decomp, config)

    # Step 1: route
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
    print(f"IngestInput tokens: {graph.place_named('IngestInput').tokens}")

    # Step 2: run ingest
    graph, _ = await ExecutableGraphOperations.execute_graph(graph, max_transitions=1)
    result = graph.place_named("MemoryResult").tokens[0]
    print(f"Result: {result.action} — {result.result}")

asyncio.run(main())
```

### MCP Tools (without server)

Test tool functions directly:

```python
import asyncio
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.mcp.tools import segment_text, search
from epimemer.storage.memory import InMemoryStorage

async def main():
    storage = InMemoryStorage()
    emb = MockEmbeddingProvider(model_id="mock", dimension=8)
    config = ServerConfig(storage_backend="memory", embedding_provider="mock")

    # Step 1: segment
    result, meta = await segment_text("Some text to segment.", storage, config)
    print(f"Segments: {result}")

    # Step 2: search (after store_decomposition populates nodes)
    result, meta = await search("Some text", storage, emb, k=5)
    print(f"Search: {result}")

asyncio.run(main())
```

## Petri Net Visualization

Any Petri net can be visualized via Petritype's built-in Graphviz support:

```python
from epimemer.core.types import RawDocument
from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net

doc = RawDocument(content="Hello.\n\nWorld.")
graph = paragraph_split_segmentation_net(doc)
# graph.visualize()  # Opens Graphviz diagram
```

## Test Structure

```
tests/
  core/           — Type creation, validation, serialization
  storage/        — InMemoryStorage and SurrealDB (mem://) tests
  embeddings/     — Mock embedding provider tests
  llm/            — Mock LLM provider tests
  pipelines/      — Petri net pipeline tests (segmentation, decomposition,
                    graph construction, query, reflection, timeline, orchestration)
  mcp/            — MCP tool tests (unit + e2e via FastMCP call_tool)
  integration/    — Full pipeline end-to-end tests
```

## Adding a New Pipeline

1. Create `epimemer/pipelines/your_pipeline/` with `__init__.py`
2. Define intermediate Pydantic token types
3. Write transition functions (async if needed)
4. Build the Petri net factory with `@petri_net` decorator
5. Write tests in `tests/pipelines/test_your_pipeline.py`
6. If it needs an MCP tool: add function to `mcp/tools.py`, register in `mcp/server.py`

## Adding a New Storage Backend

1. Implement all methods from `epimemer/storage/protocol.py`, including:
   - Core storage operations (documents, segments, nodes, edges, embeddings, timelines, metacontexts)
   - Multi-graph interface: `supports_multi_graph` property, `current_database`, `list_databases()`, `switch_database()`, `delete_database()`
   - Set `supports_multi_graph = False` if the backend only supports a single graph (see `InMemoryStorage` for reference)
2. Add tests in `tests/storage/test_your_backend.py`
3. Add a factory branch in `epimemer/mcp/config.py`
