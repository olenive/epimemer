# A SQLite storage backend

A third backend beside the in-memory store and SurrealDB: one file per graph,
no server, no new dependency. The code will be `epimemer/storage/sqlite_adapter.py`.
This note says what it touches, how the data is laid out, the decisions already
taken and the ones still open, so the implementation can be judged against
something written down rather than reconstructed from the diff.

---

## 1. Why, and why now

Users running Epimemer as a local MCP server have two choices today: the
in-memory store, which forgets everything when the process exits, and
SurrealDB, either as a server they have to run or as the embedded engine. Both
work. Neither is what most people reach for when they want a database file
they can copy, inspect with a tool they already have, and back up by hand.
SQLite is that file, and Python ships the binding.

The question was looked at on 2026-08-26 under "The in-memory store cannot
persist" in `ISSUES.md`, and deprioritised: the licensing worry behind it did
not hold, and embedded SurrealDB already runs in-process. That note left
SQLite open "on its own merits, as an addition rather than an escape". This
is that addition. Nothing in it changes how SurrealDB or the in-memory store
behave, and neither becomes less supported.

## 2. What it touches

The protocol in `epimemer/storage/protocol.py` has 98 methods, callers invoke
them unconditionally, and nothing in the pipelines, the MCP tools or the server
branches on which backend is behind them. So the footprint outside the new
adapter is small and known:

| Where | Change |
|---|---|
| `epimemer/storage/sqlite_adapter.py` | New. The whole backend |
| `epimemer/storage/timestamps.py` | New, small: `iso_micros` moves here from the SurrealDB adapter, which imports it back. Two backends must not each own the canonical shape |
| `epimemer/mcp/config.py` | `storage_backend` gains `"sqlite"`; one factory branch; one new setting, `sqlite_dir`, read from `EPIMEMER_SQLITE_DIR`, default `~/.epimemer/graphs` |
| `epimemer/storage/protocol.py`, `memory.py`, `surrealdb_adapter.py`, `epimemer/visualization/instrumented_storage.py` | A `location` property on every backend: where its graphs live, as prose a user can act on (§9) |
| `epimemer/mcp/tools.py`, `epimemer/mcp/server.py` | `list_graphs` reports the backend and its location; the startup log line names them |
| `epimemer/cli.py` | `unreachable_store` returns `None` for SQLite, so the `agents` commands work against a file-backed graph beside a running server (§7) |
| `epimemer/visualization/protocol.py` | The `backend` field's comment names the third value. The frontend treats it as a string already |
| `tests/conftest.py`, `tests/storage/test_storage_parity.py` | `"sqlite"` added to both fixture parameter lists |
| `tests/storage/test_sqlite_storage.py` | New: what is specific to this backend (§10) |
| `README.md`, `INTEGRATION.md`, `dev-docs/DEVELOPER_GUIDE.md`, `CHANGELOG.md`, `dev-docs/ISSUES.md` | The env table and a setup section after SurrealDB's, the setup page, the backend list in *Adding a new storage backend*, the release note, a pointer from the entry on the in-memory store |

Everything else reads the backend through the protocol and stays as it is.

## 3. On disk

`EPIMEMER_SQLITE_DIR` names a directory, `~/.epimemer/graphs` unless set, the
tilde expanded and the directory created on first use. A path that exists and
is not a directory is refused at connect with a message naming the variable.
Each graph is one file in it,
`<graph>.sqlite`, plus the `-wal` and `-shm` sidecars SQLite keeps beside a
file in WAL mode. A graph name is already restricted by `validate_graph_name`
to 1 to 64 characters of letters, digits, hyphen and underscore, which is
exactly a safe file name on every platform, so the name is the file name with
no escaping layer in between.

The multi-graph interface falls out of that:

| Method | Does |
|---|---|
| `current_database` | The name of the open file |
| `list_databases()` | The `*.sqlite` files in the directory, sorted, by name without the suffix |
| `switch_database(name)` | Inside the guard's `moving()` turn: close the open connection, open `<name>.sqlite`, creating and initialising it if absent, as SurrealDB creates a database on use |
| `delete_database(name)` | Refuses the active graph, as both backends do. Otherwise removes the file and its two sidecars, and refuses a name with no file |
| `viz_list_*(name)` | Open that graph's file on a second, read-only connection (`file:...?mode=ro`), read, close. No `moving()` turn, for the reason the in-memory backend gives: nothing about the active graph is borrowed |

The server lands on `EPIMEMER_GRAPH`, falling back to `"default"`, exactly as
the other backends resolve it in `create_storage`. The lesson recorded on that
default stands: it is a name nobody would give a real graph, so a server
started without `EPIMEMER_GRAPH` lands somewhere visibly empty.

## 4. Schema

One table per record kind, and the same shape for every table: a primary key,
the record as JSON, and a column for each field a query filters or orders on.

```
<kind>(uid TEXT PRIMARY KEY, <filter columns>, data TEXT NOT NULL)
```

`data` is the record, `normalize_for_storage` applied and then
`model_dump(mode="json")`, read back through `model_validate`. The filter
columns are copies of fields inside `data`, written in the same statement, and
they exist only to carry an index or a `WHERE`. A column that nothing filters
on is not added: reading it out of `data` costs nothing SQLite would have
saved. So `data` is the truth and a column disagreeing with it is a bug, which
is the invariant a parity test can check.

| Table | Filter columns | Indexes |
|---|---|---|
| `document` | `source`, `created_at` | `source` |
| `segment` | `document_id` | `document_id` |
| `node` | `node_type`, `status`, `content`, `created_at`, `superseded_at` | `(node_type, status)`, `content` |
| `node_edge` | `src_id`, `dst_id`, `type` | `(src_id, type)`, `(dst_id, type)` |
| `embedding` | `item_id`, `model_id`, `vector BLOB` | `(item_id, model_id)`, `model_id` |
| `timeline` | | |
| `metacontext` | `status` | |
| `relation_label` | `name`, `kind` | `(name, kind)`, unique |
| `relation_verdict` | `label_a`, `label_b`: the pair's lower and higher id | the pair |
| `agent` | | |
| `decision` | `agent_id`, `kind`, `subject_ids` (the list as JSON, read through `json_each`), `reviews`, `decided_at` | `agent_id`, `kind`, `reviews`, `decided_at` |
| `graph_state` | `key TEXT PRIMARY KEY, value TEXT` | |
| `schema_version` | `version INTEGER` | |

Three choices in that table are deliberate:

- **One `node` table, not three.** SurrealDB keeps topics, facts and
  inferences in separate tables, and the reflection pipelines carry comments
  about `get_node` costing one to three round trips because the table is not
  known from the id. Here a `node_type` column answers that in one indexed
  read. `_record_to_node` already dispatches on the type name, so rehydration
  is the same code.
- **Vectors as a BLOB of little-endian float64**, never JSON. The measurement
  in `ISSUES.md` rules the text form out: floats as JSON cost a gigabyte and
  eleven seconds at 100,000 nodes. Float64 rather than float32 because the
  other two backends hand back exactly the floats that were stored, and a
  parity test is entitled to expect the same here; it is twice the size of
  float32 and still a fraction of the text form. `numpy.frombuffer` reads a
  blob straight into the search (§6). The width is not stored per row: the
  model id implies it, and a mismatch is already a configuration error on
  every backend.
- **`graph_state` is a key-value table.** The reflect counter, the backup
  counter, both threshold overrides, the merge and warning overrides, the
  approved agent ids, the judge confirmations and `require_judge` are each one
  row, JSON in `value`. About thirty protocol methods are getters and setters
  over these and they all become the same two statements.

No foreign keys. Edges to retired nodes, decisions about deleted judges and
verdicts about renamed labels are all meaningful on an append-only graph, and
every backend treats these ids as soft references already.

## 5. Timestamps

The rule in `AGENTS.md` and `DEVELOPER_GUIDE.md` is that a comparison is about
instants, not spelling, and that the index decides who guarantees it: the
reader through `instant()` on an unindexed field, the writer through one
canonical rendering on an indexed one.

SQLite has no `type::datetime`, so this backend takes the writer's half for
every timestamp column. Every value written to `created_at`, `superseded_at`,
`decided_at` and any later timestamp column goes through `iso_micros`, and so
does every bound a query compares against. The whole-second trap the guide
describes, where `…:41Z` sorts after `…:41.500000Z`, cannot arise when both
sides always carry the microseconds. `data` keeps Pydantic's own rendering,
because nothing compares it.

Moving `iso_micros` into `storage/timestamps.py` is the one change inside the
SurrealDB adapter, and it is an import.

## 6. Search

**Vector search** is brute force on both existing backends: SurrealDB runs
`vector::similarity::cosine` over every row, the in-memory store loops in
Python. Here: select the vectors for the model id, joined to `node` for the
type and status filter, stack the blobs into one `(n, d)` array, one
normalised matrix product against the query, a stable sort for the top k so
equal scores keep the order the in-memory backend gives them.
Same ranking as today, and faster than either at the sizes the real graphs
have. A per-model matrix cache invalidated on write is the obvious next step
if a graph ever makes the per-call load visible, and it is not part of the
first version.

**Lexical search reuses `storage/bm25.py`**, scoring the corpus on every call
exactly as the in-memory backend does: pull `(id, text)` for the node type and
statuses, or for segments, then `bm25_scores` and `containment_first`. SQLite's
FTS5 is present in Python's bundled library and is **not** used for ranking.
`LEXICAL_SEARCH.md` §4 says exact score parity between backends is out of
reach because the analyzers differ, and the guarantee the protocol does make,
set parity on rare terms, rests on both backends tokenising an identifier the
same way. FTS5 would be a third tokenizer with its own rules for `JIRA-4417`,
and a third set of edge cases to carry in `test_bm25.py`. If scoring the whole
corpus per call ever shows up in `BENCHMARKS.md`, FTS5 can serve as a
candidate pre-filter in front of the same scorer, which keeps the ranking
shared. Measure first.

## 7. One connection, one thread, real transactions

`sqlite3` is synchronous and the protocol is `async`. The backend runs each
statement inline on the event loop. A local file operation takes microseconds
to low milliseconds, the SurrealDB adapter already serialises every call
through one connection, and a thread pool would buy nothing while costing the
ordering the graph guard assumes. This is stated in the module docstring so
nobody "fixes" it later.

The connection is opened with `autocommit=True`, which Python has offered
since 3.12 and which turns off the legacy implicit-transaction behaviour where
the binding opens a transaction before a write and leaves it open. With it
on, every statement commits on its own, and a transaction exists only where
the code says so: the ten `*_tx` methods, each `BEGIN IMMEDIATE`, the
statements, `COMMIT`, and `ROLLBACK` on any exception. That is a cleaner
story than SurrealDB's, where the adapter has to concatenate the statements
into one query string because a transaction split across round trips is not
reliable.

Pragmas set on open: `journal_mode=WAL`, `synchronous=NORMAL`,
`busy_timeout=5000`. WAL is what makes the next paragraph true.

**A second process can see the store.** The in-memory backend lives inside the
server process, and a second connection to embedded SurrealDB is a separate
store, which is why `unreachable_store` in `cli.py` refuses the `agents`
commands against both and sends the user to `EPIMEMER_APPROVED_AGENTS`. A
SQLite file in WAL mode is readable and writable from another process while
the server holds it open, so `epimemer agents confirm` against a file-backed
graph can work the way it does against a served SurrealDB. Checked on
2026-10-08: every per-graph setting the server consults is read from the
backend on each call, and the approved-agent seeding at connect only writes.

**Deleting a graph another process holds open loses that process's writes,
silently.** `delete_database` unlinks the file; a connection already open on
it keeps reading and writing the unlinked inode until it closes, and then
everything it wrote is gone, with nothing raised anywhere. An idle SQLite
connection holds no lock, so there is nothing for the deleting side to detect.
Pinned by a test in `test_sqlite_storage.py`, and said in the README: close
every other server on a graph before deleting it.

## 8. Schema version, and moving a graph here

The file is created with `schema_version` at 1 and the stepper from the
SurrealDB adapter's `_migrate_schema` copied in shape with no steps in it, so
the first real migration has a place to go. None of the six SurrealDB
migrations is ported: they rewrite graphs that only ever existed in SurrealDB.

A file stamped with a higher version than the code knows was written by a
newer Epimemer, and opening it raises `SchemaTooNew` (`storage/protocol.py`)
naming both versions, before anything is written. `connect()` closes the
connection it was opening; `switch_database` leaves the active graph in place.
Every persistent backend refuses this way, so the server's startup log and the
`epimemer` commands say the same thing whichever is configured.

A graph moves to SQLite through the bundle format and nothing else:
`epimemer graphs export` against the old backend, `epimemer graphs import`
with `EPIMEMER_STORAGE_BACKEND=sqlite`, or the two in one step with
`epimemer graphs convert <graph> --to sqlite`. `GRAPH_BUNDLES.md` already promises
that an import rebuilds a graph on any backend, and `write_verbatim_tx` plus
the three whole-section reads are what this backend has to get exactly right
for that promise to extend to it.

## 9. Configuration and the user's first run

```
EPIMEMER_STORAGE_BACKEND=sqlite
EPIMEMER_GRAPH=my-graph
```

That is the whole setup. The graphs live in `~/.epimemer/graphs`, under the
directory Epimemer already keeps client state in, and `EPIMEMER_SQLITE_DIR`
moves them. The choice follows what long-running tools in this space do, a
per-user directory with an environment override (Prefect, Letta, mem0, Simon
Willison's `llm`), and avoids the trap frameworks fall into when they default
to the working directory, which for an MCP server is whatever the client
chose. The worry that a default hides where the data went is answered by
telling the user rather than by refusing: every backend gets a `location`
property, `list_graphs` reports the backend and its location beside the graph
names, and the startup log line names them. On the in-memory backend the
location is the process; on SurrealDB it is the URL and namespace.

The `surrealdb_*` settings stay as they are and are ignored on this backend,
as they are on the in-memory one.

## 10. Tests

Adding `"sqlite"` to the two fixture parameter lists runs the behavioural
suite against the new backend: on 2026-10-08 that is about 1,800 collected
tests per backend, which is the real acceptance test. Each test gets its own
directory from `tmp_path`, so nothing is shared between tests and nothing
touches a user's files.

`tests/storage/test_sqlite_storage.py` holds what only this backend can get
wrong:

- A graph written, closed and reopened reads back whole, embeddings included.
- Two connections to one file see each other's writes, which is the WAL
  promise §7 leans on.
- `delete_database` removes the file and both sidecars, refuses the active
  graph, and refuses a name with no file.
- A 384-wide vector round-trips through the blob to the same floats.
- The whole-second timestamp case: a node created on an exact second is
  returned by `query_nodes(at_time=...)` with a bound half a second later.
- A `*_tx` method that fails half way leaves nothing behind.
- A filter column always agrees with the field inside `data`, checked over a
  populated graph.

The guard in `tests/visualization/test_instrumented_storage.py` compares the
instrumentation wrapper against every protocol member, and will fail on any
method the new backend adds by mistake rather than implements. No new guard
is needed for the backend itself: a missing method fails the parity suite on
its first call.

## 11. Size and sequence

The in-memory backend is about 1,500 lines, the SurrealDB adapter about 3,050
of which roughly 600 are migrations and reconnection, neither of which applies
here. Expect 1,600 to 2,000 lines, most of them mechanical: one short
statement and one rehydration per method.

A feature branch, in this order, each step green before the next:

1. `timestamps.py`, the adapter, and `"sqlite"` in both fixture lists. The
   unit suite across three backends is the gate.
2. `test_sqlite_storage.py`.
3. Config, CLI, the visualization comment, README and the developer guide.
4. A changelog entry, and a pointer from the `ISSUES.md` entry on the
   in-memory store to this note.

The branch is squashed to main as one commit on release, as usual.

## 12. Decisions

**Decided on 2026-10-08: `~/.epimemer/graphs` by default, `EPIMEMER_SQLITE_DIR`
to move it.** The reasoning is in §9. The first draft's position, no default
and refuse to start, was dropped in favour of reporting the location.

**Decided on 2026-10-08: the README keeps SurrealDB as its first
suggestion.** SQLite is documented beside it as the server-free alternative,
and the question of which to lead with is revisited once the backend has been
in a release.

## 13. Rejected

- **FTS5 for ranking.** A third analyzer, and the end of rare-term parity
  (§6).
- **One file for every graph, with `ATTACH`.** Loses the property that a graph
  is a thing a user can copy, and makes `delete_database` a schema operation
  instead of an unlink.
- **A thread pool around `sqlite3`.** Nothing to gain at these latencies, and
  the graph guard's ordering to lose (§7).
- **A vector extension such as `sqlite-vec`.** A compiled dependency, and
  loadable-extension support is not guaranteed in every Python build. Brute
  force matches the other two backends and is fast enough; a cache comes first
  if anything does.
- **No default directory, refusing to start without `EPIMEMER_SQLITE_DIR`.**
  The first draft's position. Reporting the location gives the same
  visibility without a failed first run.
- **Floats as JSON.** Measured and ruled out before this note existed.
- **Three node tables.** The SurrealDB shape, and the source of its extra
  round trips.
- **Porting the SurrealDB migrations.** They correct graphs this backend will
  never hold; bundles are the migration path.
