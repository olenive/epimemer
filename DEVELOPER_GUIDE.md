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

### Against a real SurrealDB (opt-in, needs Docker)

The default suite is embedded and sequential. Two properties it cannot reach —
cross-connection atomicity and surviving a restart — live in opt-in suites that
skip themselves otherwise. Run both:

```bash
make test-integration

# Port 8000 taken? The target says so, and names the process. Then:
make test-integration SURREAL_PORT=8123
```

Run this when a change touches storage or concurrency. Either suite skipping is
reported as a pass by pytest, so check the counts — `5 passed` and `1 passed`,
not `5 skipped`.

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

Nets are driven by the Petritype `Runner`. `Runner.step(ctx)` fires a single
transition (returns 0 or 1) for step-through debugging; `Runner.run_to_completion(ctx)`
drives to quiescence. Both mutate `ctx.graph` in place.

```python
import asyncio
from petritype.runtime import RunContext, Runner
from epimemer.core.types import RawDocument
from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net

async def main():
    doc = RawDocument(content="First paragraph.\n\nSecond paragraph.\n\nThird.")
    graph = paragraph_split_segmentation_net(doc)
    ctx = RunContext(graph=graph)

    # Step through one transition at a time
    fired = await Runner.step(ctx)
    print(f"Fired: {fired}")
    segments = graph.place_named("Segments").tokens
    print(f"Segments: {[s.text for s in segments]}")

asyncio.run(main())
```

### Decomposition

There is no in-process decomposition net to debug: Epimemer does not extract
topics/facts/inferences itself. Decomposition is the calling agent's job, and
ingest is a two-step flow — `segment` returns chunks, the agent extracts nodes,
and `store_decomposition` persists them. To exercise that path, build the nodes
by hand and call `store_decomposition` directly (see *MCP Tools* below).

### Query Layer

Debug hybrid retrieval:

```python
import asyncio
from petritype.runtime import RunContext, Runner
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
    ctx = RunContext(graph=graph)

    # Step through transitions to see intermediate state
    await Runner.step(ctx)
    vector_results = graph.place_named("VectorResults").tokens
    print(f"Vector results: {vector_results}")

    await Runner.step(ctx)
    expanded = graph.place_named("ExpandedResults").tokens
    print(f"Expanded: {expanded}")

asyncio.run(main())
```

### Reflection

Reflection is not a single net — it is a set of analysis functions in
`epimemer/pipelines/reflection/` (topic consolidation, splitting, enrichment,
contradiction detection, relation consolidation, value decay) composed by the
`reflect` tool. Decay is applied immediately; everything else is returned as
candidates for the agent to act on via `apply_reflection`. Debug the whole thing
through the tool function:

```python
import asyncio
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.tools import reflect
from epimemer.storage.memory import InMemoryStorage

async def main():
    storage = InMemoryStorage()
    emb = MockEmbeddingProvider(model_id="mock", dimension=8)

    # ... populate storage ...

    result, meta = await reflect(storage, emb, similarity_threshold=0.85)
    print(f"Decayed: {result['nodes_decayed']}")
    print(f"Similar topic pairs: {result['similar_pairs']}")
    print(f"Split candidates: {result['split_candidates']}")
    print(f"Contradictions: {result['contradictions']}")
    print(f"Pending review: {result['pending_review']}")

asyncio.run(main())
```

To isolate one stage, call its module function directly — e.g.
`find_similar_topic_pairs` from `topic_consolidation`, or `apply_decay` from
`value_decay`.

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
from petritype.runtime import RunContext, Runner
from epimemer.embeddings.mock import MockEmbeddingProvider
from epimemer.mcp.config import ServerConfig
from epimemer.pipelines.orchestration.orchestration_net import MemoryRequest, orchestration_net
from epimemer.storage.memory import InMemoryStorage

async def main():
    storage = InMemoryStorage()
    emb = MockEmbeddingProvider(model_id="mock", dimension=8)
    config = ServerConfig(storage_backend="memory", embedding_provider="mock")

    request = MemoryRequest(action="segment", payload={"content": "Test content."})
    graph = orchestration_net(request, storage, emb, config)
    ctx = RunContext(graph=graph)

    # Step 1: route to the SegmentInput place
    await Runner.step(ctx)
    print(f"SegmentInput tokens: {graph.place_named('SegmentInput').tokens}")

    # Step 2: run the routed sub-pipeline
    await Runner.step(ctx)
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
    result, meta = await segment_text("Some text to segment.", storage, emb, config)
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
  pipelines/      — Petri net pipeline tests (segmentation, graph construction,
                    query, reflection, timeline, orchestration)
  mcp/            — MCP tool tests (unit + e2e via FastMCP call_tool)
  integration/    — Full pipeline end-to-end tests
```

Most storage and MCP tests run against both backends: a `conftest.py` fixture
parameterizes `storage` over `InMemoryStorage` and `SurrealDBStorage("mem://")`.

## Adding a New Pipeline

1. Create `epimemer/pipelines/your_pipeline/` with `__init__.py`
2. Define intermediate Pydantic token types
3. Write transition functions (async if needed)
4. Build the Petri net factory with `@petri_net` decorator
5. Write tests in `tests/pipelines/test_your_pipeline.py`
6. If it needs an MCP tool: add function to `mcp/tools.py`, register in `mcp/server.py`

## Adding a New Storage Backend

1. Implement **every** method from `epimemer/storage/protocol.py` — there are no
   capability flags to opt out of, so a backend implements the whole protocol
   (use a no-op where an operation has nothing to do). This covers:
   - Core storage operations (documents, segments, nodes, edges, embeddings, timelines, metacontexts)
   - Multi-graph interface: `current_database`, `list_databases()`, `switch_database()`, `delete_database()` — both existing backends support multiple named graphs (default `"default"`); see `InMemoryStorage` for the in-process pattern
   - Apply `normalize_for_storage` on write so `None`-valued dict keys round-trip identically across backends
2. Add tests in `tests/storage/test_your_backend.py`, and add the backend to the parity fixtures in `tests/conftest.py`
3. Add a factory branch in `epimemer/mcp/config.py`
