# Developer guide

## Setup

```bash
git clone https://github.com/olenive/epimemer.git
cd epimemer
uv sync --extra dev

# Verify
uv run python -m pytest tests/ -q
```

Petritype comes from PyPI. To work on both at once, install a checkout into
this venv for the session (`uv pip install -e ../petritype`) and never commit
a path source: Epimemer depends on released Petritype only, so a change that
needs new Petritype code is a Petritype release first and a pin bump here.

## Running tests

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

### Dependencies: `scripts/audit_dependencies.py`

```bash
uv run python scripts/audit_dependencies.py
```

**Dependabot cannot see the Python dependencies here.** Its `pip` ecosystem
covers pip, pipenv, pip-compile and poetry, and the dependency graph reads
`requirements.txt` and `pipfile.lock`. Nothing in either list understands
`uv.lock`, so no alert fires and no pull request is opened whatever is in the
resolved environment. Measured once, on 2026-09-07: 19 of 132 installed
packages carried an advisory, one of them critical.

`.github/workflows/audit.yml` runs the same command weekly and files what it
finds as an issue, refreshing the standing one rather than opening a second and
closing it once nothing is affected. An issue rather than a failed run because
a failed scheduled job is an email that ages out of an inbox. The run
distinguishes three outcomes, which is why the script has three exit codes: a
clean environment, an affected package, and the check itself failing, and only
the middle one is a finding.

`.github/dependabot.yml` covers the two ecosystems Dependabot can see, GitHub
Actions and the frontend's npm tree, grouped and slowed so the routine churn
arrives monthly in one pull request per ecosystem; security updates are a
separate feature that bypasses that schedule entirely. Majors are grouped
separately rather than ignored: the drift this replaced was **entirely**
majors, every action three or four behind, so ignoring them would have hidden
the whole gap.

Routine Python updates are deliberate rather than automated: `uv lock
--upgrade`, run the suite, one commit.

### Before asking for a review: `scripts/prose_drift.py`

```bash
uv run python scripts/prose_drift.py
```

Finds prose carrying a live count of something the code enumerates: the size
of `CAPPED_KEYS`, the number of reflect phases, the kinds `apply_reflection`
accepts. Every one of those is a sentence that was true when written and rots
the moment somebody adds to the list.

It is not in the suite, and that is the point. A test pinning those numbers
would detect the drift while institutionalising the duplication that causes
it: every legitimate change to a list would fail a doc test whose fix is
bumping a number, which trains the update-without-rereading habit that lets
the argument around the number go stale while the number stays fresh. The fix
is to stop writing the counts (name `CAPPED_KEYS` rather than saying how many
lists it holds), and this only finds the ones already written.

So run it by hand, at the moment the prose and the code are both in your
head. It covers `docs/`, `dev-docs/`, the top-level pages and the agent
guidance. A dated measurement is not a live count: *0.0105% of fact pairs
clear the bar* is evidence, and evidence ages rather than drifting, which is
what the date is for. It errs at missing things rather than at reporting
them, because a tool that reports thirty findings with two real ones is one
nobody runs twice.

### Against a real SurrealDB (opt-in, needs Docker)

The default suite is embedded and sequential. Two properties it cannot reach,
cross-connection atomicity and surviving a restart, live in opt-in suites that
skip themselves otherwise. Run both:

```bash
make test-integration

# Port 8000 taken? The target says so, and names the process. Then:
make test-integration SURREAL_PORT=8123
```

Run this when a change touches storage or concurrency. Either suite skipping
is reported as a pass by pytest, so check the counts: `5 passed` and
`1 passed`, not `5 skipped`.

For a persistent local server outside the test run, on macOS,
`scripts/start_local_surrealdb.sh` starts one via Colima on disk and registers
the MCP server with Claude Code against it.

## Debugging individual sections

Each pipeline is independently testable. Every pipeline is a Petri net with
typed places and transitions.

### Core types

All Pydantic models live in `epimemer/core/types.py`. To inspect a model:

```python
from epimemer.core.types import Topic, Timeline, Metacontext

t = Topic(content="test", source_id="s1")
print(t.model_dump_json(indent=2))
```

### Storage layer

Test with `InMemoryStorage` (no external dependencies):

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

For SurrealDB, use embedded mode (`mem://`); no server needed:

```python
from epimemer.storage.surrealdb_adapter import SurrealDBStorage


async def main():
    store = SurrealDBStorage(url="mem://")
    await store.connect()
    # ... use store ...
    await store.close()
```

### Segmentation

Nets are driven by the Petritype `Runner`. `Runner.step(ctx)` fires a single
transition (returns 0 or 1) for step-through debugging;
`Runner.run_to_completion(ctx)` drives to quiescence. Both mutate `ctx.graph`
in place.

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
topics, facts and inferences itself. Decomposition is the calling agent's
job, and ingest is a two-step flow: `segment` returns chunks, the agent
extracts nodes, and `store_decomposition` persists them. To exercise that
path, build the nodes by hand and call `store_decomposition` directly (see
*MCP tools* below).

### Query layer

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

Reflection is not a single net. It is a set of analysis functions in
`epimemer/pipelines/reflection/` composed by the `reflect` tool: topic
consolidation, splitting, enrichment and hierarchy; contradiction detection
and the pair scoring under it; relation consolidation; archival nomination;
inference soundness; boundary proposals; and the review-loop helpers
(`review`, and the merge gate in `fact_dedup`). Every one of them reads only.
Results come back as candidates for the agent to act on via
`apply_reflection` or a resolution tool, and `reflect` itself never changes
the graph. Debug the whole thing through the tool function:

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
    print(f"Similar topic pairs: {result['similar_pairs']}")
    print(f"Split candidates: {result['split_candidates']}")
    print(f"Contradictions: {result['contradictions']}")
    print(f"Pending review: {result['pending_review']}")


asyncio.run(main())
```

To isolate one stage, call its module function directly:
`find_similar_topic_pairs` from `topic_consolidation`, or
`nominate_archival_candidates` from `archival`.

**Two rules for any new nominator, and they are a pair.** Every sweep here is
recomputed from the graph as it stands, which makes both failures easy to
ship without noticing:

- **A sweep that records no declines is a futile cycle by construction.** It
  re-offers what an agent already refused, on every pass, and cannot know it
  is doing so. The cost is agent attention, and the pressure runs the wrong
  way: *accepting* a nomination usually removes its subject from the
  population, so acceptance is self-suppressing and refusal is not. The
  `assessed` edge is the fix for fact pairs, `RelationVerdict` for label
  pairs, and the `retention` journal row for single nodes: a suppression
  index the sweep reads, separate from the journal that audits it.
- **And its dual: a suppression with no retraction makes every wrong decline
  permanent by construction.** `assessed` is deliberately terminal
  (`similarity_decisions.py`), and the `one_claim` retraction left it
  untouched on purpose. That is a choice, not an oversight, and a new
  nominator inherits it knowingly or decides otherwise, but not by copying.
  The `one_claim` retraction is one-way because a false unification
  manufactures agreement while a withdrawal only under-counts; where neither
  failure applies, a one-way retraction buys nothing for the permanence it
  costs. `ISSUES.md` carries the open question.

**A cycle in a feature nobody has built is a precondition, not a defect.**
Record it against the thing that would create it rather than in a shared
list, or the list reads as outstanding bugs.

### Timeline functions

Timeline functions are pure: no async, no storage needed.

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

### Orchestration net

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
    print(f"Result: {result.action}: {result.result}")


asyncio.run(main())
```

### MCP tools (without server)

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

## Petri net visualisation

Any Petri net can be visualised via Petritype's built-in Graphviz support:

```python
from epimemer.core.types import RawDocument
from epimemer.pipelines.segmentation.paragraph_split import paragraph_split_segmentation_net

doc = RawDocument(content="Hello.\n\nWorld.")
graph = paragraph_split_segmentation_net(doc)
# graph.visualize()  # Opens Graphviz diagram
```

## Test structure

```
tests/
  core/           Type creation, validation, serialisation
  storage/        InMemoryStorage and SurrealDB (mem://) tests
  embeddings/     Mock embedding provider tests
  pipelines/      Petri net pipeline tests (segmentation, graph construction,
                  query, reflection, timeline, orchestration)
  mcp/            MCP tool tests (unit + e2e via FastMCP call_tool)
  integration/    Full pipeline end-to-end tests
```

Most storage and MCP tests run against both backends: a `conftest.py` fixture
parameterises `storage` over `InMemoryStorage` and `SurrealDBStorage("mem://")`.

## Adding a new pipeline

1. Create `epimemer/pipelines/your_pipeline/` with `__init__.py`
2. Define intermediate Pydantic token types
3. Write transition functions (async if needed)
4. Build the Petri net factory with the `@petri_net` decorator
5. Write tests in `tests/pipelines/test_your_pipeline.py`
6. If it needs an MCP tool: add the function to `mcp/tools.py`, register it in
   `mcp/server.py`, and give it `expected_graph` (see *The active graph holds
   still*)

## Adding a new storage backend

1. Implement **every** method from `epimemer/storage/protocol.py`. There are
   no capability flags to opt out of, so a backend implements the whole
   protocol, with a no-op where an operation has nothing to do. This covers:
   - Core storage operations (documents, segments, nodes, edges, embeddings,
     timelines, metacontexts, the decision journal)
   - The multi-graph interface: `current_database`, `list_databases()`,
     `switch_database()`, `delete_database()`. Both existing backends support
     multiple named graphs (default `"default"`); see `InMemoryStorage` for
     the in-process pattern
   - `normalize_for_storage` on write, so `None`-valued dict keys round-trip
     identically across backends
   - Timestamp comparison by instant, never by text (see *Comparing
     timestamps*)
   - `graph_guard`, with `switch_database` taking its mover turn (see *The
     active graph holds still*). A backend that resolves its graph per call
     (both of ours do) has this problem whether or not it has a connection
   - If the backend talks over a socket, it owns reconnection. An MCP server
     outlives the databases it talks to, so a dropped connection has to be
     rebuilt rather than raised forever; see `_reconnect` in
     `surrealdb_adapter.py` and the constraints in its comment
   - Any schema migration the SurrealDB adapter runs on open
     (`_migrate_schema`), if the backend persists across releases
2. Add tests in `tests/storage/test_your_backend.py`, and add the backend to
   the parity fixtures in `tests/conftest.py`
3. Add a factory branch in `epimemer/mcp/config.py`

## Comparing timestamps

**A timestamp comparison must be about instants, not about spelling.** If a
backend stores timestamps as text, as SurrealDB does, then `>=` compares two
strings, and that is chronologically correct only while every rendering has
the same shape. It is not a shape the writers guarantee: Pydantic omits the
fractional part when it is exactly zero, so a row written on a whole second
renders `…:41Z` while a bound renders `…:41.500000Z`, and `"Z" > "."` sorts
the earlier row *after* the later bound. The `Z` and `+00:00` suffixes differ
the same way. `datetime.now()` essentially never lands on a whole second, so a
test that builds its own timestamps builds safe ones by accident and never
sees this.

**The rule has two halves, and the index decides which applies.**

| The field is… | Compare how | Who guarantees correctness |
|---|---|---|
| **not indexed** | through `instant()` in `surrealdb_adapter.py`, which wraps both sides in `type::datetime` | the reader |
| **indexed** | plainly, because wrapping the field drops the index scan | the **writer**, which must render one canonical shape (`_iso_micros`) |

The measurements behind the split, on embedded SurrealDB:

- Converting costs about 2.3 µs per row scanned: 1.19× on a realistic
  `query_nodes` window over 10,000 rows, and under 2 ms on the real graphs.
- It costs nothing else at the unindexed sites, because `created_at`,
  `superseded_at` and the `lifecycle` timestamps have no index: both forms
  already plan as `Iterate Table`. Check the plan before assuming an index is
  being given up.
- Where a timestamp *is* indexed the picture inverts. A range over the
  decision journal's `decided_at` went from 6.2 ms to 281 ms at 50,000 rows,
  because wrapping the field turns `Iterate Index` into `Iterate Table`. That
  is why the journal pads on write instead, in `_decision_row`.

So: a new comparison of an unindexed timestamp goes through `instant()`. A
new indexed timestamp field takes on the writer's obligation instead, and if
you add an index to a field something already compares through `instant()`,
the comparison must change with it or the index is dead weight.

`EXPLAIN` is how you check which case you are in:

```python
await store._query("SELECT * FROM fact WHERE created_at <= $at EXPLAIN", {"at": at})
# {'operation': 'Iterate Table'}  → unindexed, use instant()
# {'operation': 'Iterate Index'}  → indexed, pad on write instead
```

## The CLI is not a remedy an agent can reach

`epimemer/cli.py` refuses every embedded backend: `mem://`, `file://`,
`surrealkv://` and the in-memory store all live inside the server process, so
a second connection is a separate store rather than a second view of one
(`is_embedded_url`, `_embedded_advice`). That refusal is right, and it has a
consequence worth stating before a design leans on it.

Two audiences cannot use the CLI, and they overlap with everyone. An agent
cannot run it at all, since it is the user's command, and nobody can run it
against the default development configuration, which is embedded. So *"the
user runs `epimemer <thing>`"* is a fallback for one deployment shape,
unavailable to the caller who hit the problem.

The rule: **a remedy the agent cannot issue, on a backend where the command
refuses, is not a remedy.** Where a design needs data to exist before
something works, create it on the write paths that already run, not in a CLI
command the blocked caller is being told to ask for. `RELATION_LABELS.md` §2.3
is the worked example. Mechanical migrations follow the same rule: they run
on open (`_migrate_schema`), and only migrations that need a person's
judgment (`epimemer frames declare`) stay in the CLI.

Settings are the exception and the reason the trap is easy to fall into: they
have environment variables read at connect, so the CLI is one of three
channels rather than the only one. Data operations have no equivalent escape
hatch.

## The active graph holds still

**The active graph is process state, and one logical operation needs it not
to move.** Both backends resolve it per call: `InMemoryStorage` indexes
`self._graphs[self._database]`, `SurrealDBStorage` sends `USE ns db` down one
shared connection. A switch landing between two steps of an operation sends
the rest of that operation somewhere else. It is not a SurrealDB problem.

Two things move it, and that is the whole list:

| Mover | Why it moves | Who takes the turn |
|---|---|---|
| `switch_database` | `use_graph`, permanently | the method itself |
| `viz_list_*` | borrowing the connection so a dashboard can snapshot a graph this session is not on | the method itself, and `hub_client.py` for the four reads of one snapshot |

`storage/active_graph.py` has the guard. Everything else takes the other side:

```python
async with storage.graph_guard.using():  # a tool call, see _run_with_timeout
    ...
```

Users do not exclude each other (the common case is an uncontended lock and
an integer), and movers are preferred, so a busy session cannot starve the
dashboard.

### And every call says which graph it means

The guard stops the graph moving *underneath* a call. It cannot tell you the
call started in the right graph: the agent's belief and `current_database` can
agree while a reconnect has put both somewhere else. That is `expected_graph`,
required on every tool except the four that are *about* graphs
(`NAMES_ITS_OWN_GRAPH` in `mcp/server.py`).

Adding a tool means adding the parameter and forwarding it; the oracle in
`tests/mcp/test_graph_gate.py` walks the live registry and fails otherwise.
The check itself is one call to `tools.wrong_graph`, made in two places and
declared in one:

| Where | Why there |
|---|---|
| `_run_with_timeout` | the choke point, **inside** the turn: outside it, a `use_graph` landing between check and call would leave a passing call running elsewhere |
| `_judge_for_write` | it runs in the tool body, and everything it reads is graph state: which graph, before who |

Do not add a third. And do not add a *setting*: a per-graph flag would be read
from whichever graph the call is actually in, disabling the guard in exactly
the case it exists for, and a gate that switched on once a second graph
existed would refuse calls that worked yesterday because of state nobody
touched.

**A refusal produced outside a tool cannot use that tool's summariser.** The
gate returns a shape no `output_summary_fn` was written against, and running
one over it raises inside `_log`. `_wrong_graph_summary` is why the refusal
survives to the agent.

Three rules for anything new:

- **Take the turn at the logical-operation boundary.** Per query is useless:
  a move only has to land between two of the several storage calls one tool
  makes. The boundaries are `_run_with_timeout` and the snapshot RPC.
- **A tool that moves the graph goes in `MOVES_THE_GRAPH`** (`mcp/server.py`).
  Taking `moving()` inside `using()` raises rather than hanging, and the
  message says so, but the tool has to be declared, because the default fails
  open.
- **A cross-graph read takes a mover's turn**, even though it only reads. It
  moves the graph to get there. `review` is the worked example: its
  `elsewhere` locator counts the journal in every other graph, so a read
  ended up in `MOVES_THE_GRAPH`. You cannot borrow the graph while being one
  of the calls using it, and the turn has to be taken at the boundary for the
  whole call, which is why `review` excludes other calls for its duration and
  reads a single instant in exchange.

**Testing concurrency here needs a real suspension point.** With in-memory
storage and the mock embedder every await completes without suspending, so
`asyncio.gather` runs the first call to completion and there is no race to
lose; a test written that way passes with the guard removed. See
`_suspending` in `tests/mcp/test_graph_turns.py`, and prove any new test
fails without the fix before trusting it.

**And run it on both backends.** The in-memory store reads another graph with
a dict lookup and borrows nothing, so a cross-graph test written against it
alone is green whether or not the mover turn was ever declared: green for the
wrong reason, in a second costume. `tests/mcp/test_review_locator.py`
parameterises its end-to-end fixture over both for exactly this.
