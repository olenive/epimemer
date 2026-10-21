# The SQLite and Postgres backends

A design, built: the code is `epimemer/storage/sqlite_adapter.py` and
`epimemer/storage/postgres_adapter.py`, beside the in-memory store and
SurrealDB. How a user sets either up is in the README, under *Persistence*.

---

## 1. What each is for

**SQLite** is a database file a user can copy, inspect with a tool they
already have and back up by hand, with no server to run and nothing to
install: Python ships the binding.

**Postgres** is the shared graph on the database most teams already operate,
back up and secure: several Epimemer servers, on one machine or many, reading
and writing one graph. A team that already runs SurrealDB can keep it.

Both sit beside SurrealDB rather than replacing it. Its licence is no reason
to avoid it: the Business Source License covers the SurrealDB server binary,
which Epimemer does not ship, and the `surrealdb` Python package is Apache-2.0
(read from package metadata, which is not a legal opinion).

## 2. What they share

**One table per record kind, one shape for every table**: a primary key, the
record as JSON in `data`, and a copy of each field a query filters or orders
on. `data` is the truth, and a filter column disagreeing with it is a bug a
parity test can check. A column nothing filters on is not added, since
reading it out of `data` costs nothing an index would save.

**One `node` table, not three.** A `node_type` column answers a node's type in
one indexed read, where SurrealDB, which keeps topics, facts and inferences
apart, probes up to three tables for an id.

**No foreign keys.** Edges to retired nodes, decisions about deleted judges and
verdicts about renamed labels are all meaningful on an append-only graph, and
every backend treats these ids as soft references.

**Vectors as a blob of little-endian float64.** Float64 because the other
backends hand back exactly the floats that were stored and a parity test is
entitled to expect the same; it is twice the size of float32 and still a
fraction of the text form. The width is not stored: the model id implies it.

**Search is brute force**, as on every backend, with the in-memory backend's
ranking and its order among equal scores. Lexical search scores the pulled
corpus with `storage/bm25.py`, the in-memory backend's scorer, and neither
engine's own full-text search is used for ranking: the rare-term set parity
`LEXICAL_SEARCH.md` §4 promises rests on every backend tokenising an
identifier such as `JIRA-4417` the same way, and a third analyzer would end it.
If scoring the whole corpus per call ever shows up in `BENCHMARKS.md`, the
engine's index can serve as a candidate pre-filter in front of the same
scorer, which keeps the ranking shared. Measure first.

**Timestamps** follow `DEVELOPER_GUIDE.md`, *Comparing timestamps*: SQLite
pads every timestamp column and every bound with `iso_micros`, and Postgres
stores `timestamptz` and binds datetimes. `data` keeps Pydantic's rendering,
because nothing compares it.

**A graph arrives through a bundle**: `epimemer graphs export` and `import`,
or `epimemer graphs convert <graph> --to sqlite|postgres` in one step. So
neither backend ports SurrealDB's migrations, which correct graphs that only
ever existed there. Each has its own version stepper, empty so far, and a
graph stamped newer than the code raises `SchemaTooNew` before anything is
written. The promise that an import rebuilds a graph on any backend rests on
`write_verbatim_tx` and the whole-section reads being exact here.

**Every per-graph setting and counter is read on each call**, never cached in
a process, so a write made by the CLI or by another server is never masked.

## 3. SQLite

**One file per graph**, `<graph>.sqlite` with its `-wal` and `-shm` sidecars,
in `EPIMEMER_SQLITE_DIR`. `validate_graph_name` already limits a name to
characters that are a safe file name everywhere, so there is no escaping
layer. Reading another graph opens its file read-only on a second connection,
which borrows nothing, so it takes no mover turn.

**`~/.epimemer/graphs` by default.** Long-running tools in this space keep a
per-user directory with an environment override (Prefect, Letta, mem0, Simon
Willison's `llm`), and the working directory, the usual alternative, is
whatever the MCP client chose. The worry that a default hides where the data
went is answered by telling the user: `list_graphs`, `epimemer graphs list`
and the startup log name the backend and its location.

**Transactions are `BEGIN IMMEDIATE`**, so the write lock is taken at the
start: a transaction that read first and then met another process's lock could
only fail.

**WAL lets a second process share the file**, so the `epimemer agents`
commands reach a SQLite graph while the server holds it open.

**Deleting a graph another process holds open loses that process's writes,
silently.** The open connection keeps writing to the unlinked file until it
closes, and an idle SQLite connection holds no lock, so the deleting side has
nothing to detect. A test in `test_sqlite_storage.py` pins it and the README
warns users.

## 4. Postgres

**One schema per graph**, and every statement names its schema
(`"<graph>".node`) instead of relying on `search_path`. Switching graphs then
changes an attribute and nothing on the connection, a reconnect cannot lose
the active graph, and reading another graph is an ordinary query against
another schema, with no mover turn.

**A graph is a schema holding the marker table `epimemer_graph`**, which
records the graph's name and schema version. It is not called
`schema_version` because Flyway before version 5, and many hand-written
scripts, use that name: a team's own schema would then list as a graph, and an
agent's `delete_graph` would drop it with `CASCADE`. A schema that holds tables
but no marker belongs to another application and is refused; an empty one is
adopted.

**A graph name Postgres cannot hold as an identifier** (longer than 63 bytes,
or starting `pg_`) lives in a schema named from its first characters and a
hash, in a form no graph name can take, and the marker records the real name.
Without it a legal graph name could not be imported here from another backend.

**The database must already exist** and the role must be allowed to create
schemas in it: an application should not create databases on a server it was
pointed at. There is no default DSN, since there is no local place a database
could default to.

**It owns reconnection.** On `OperationalError` outside a transaction, the unit
of work is retried once on a fresh connection, and only when the connection is
actually closed: a cancelled statement or a lock timeout raises the same class
and is not retried. That is safe because the autocommit writes are upserts and
deletes by id, which repeat to the same state, and insert-only appends, which
refuse a repeat. The one write that can count twice is a counter bump whose
reply was lost after it committed. Inside a transaction the error propagates,
since the transaction went with the socket.

**Known limits.** `jsonb` normalises numbers inside free-form dicts (`1e16`
comes back as an integer and `-0.0` as `0.0`, pinned by `TestNumbersInJsonb`).
Postgres text cannot hold a NUL character, so such a record fails here where
the other backends accept it, untested. On Windows psycopg's async mode needs a
selector event loop, also untested.

## 5. Rejected

- **Full-text search for ranking** (SQLite FTS5, Postgres full-text): a third
  analyzer, and the end of rare-term parity (§2).
- **Floats as JSON.** Measured at the real embedding width (384): a gigabyte
  and eleven seconds at 100,000 nodes.
- **Three node tables.** The SurrealDB shape, and the source of its extra round
  trips.
- **Porting the SurrealDB migrations.** They correct graphs these backends
  never hold; bundles are the migration path.
- **One SQLite file for every graph, with `ATTACH`.** A graph would stop being
  a file a user can copy, and deleting one would become a schema operation
  instead of an unlink.
- **A thread pool around `sqlite3`.** Statements run inline on the event
  loop: a local file operation takes microseconds to low milliseconds, and a
  pool would cost the ordering the graph guard assumes.
- **A vector extension such as `sqlite-vec`.** A compiled dependency, and
  loadable-extension support is not guaranteed in every Python build. pgvector
  is a later option on Postgres (`PROPOSED_FEATURES.md`, *A vector index*).
- **Refusing to start without `EPIMEMER_SQLITE_DIR`.** Reporting the location
  gives the same visibility without a failed first run.
- **A Postgres connection pool.** One connection per server process, with one
  lock around each unit of work, is what every backend does and what the graph
  guard assumes. A pool buys concurrency the server does not use.
- **`search_path`.** Connection state a reconnect would have to restore and a
  cross-graph read would have to work around.
- **One Postgres database per graph.** Needs `CREATE DATABASE`, which cannot
  run inside a transaction, and a connection per graph. Schemas are what
  Postgres has for this.
- **`schema_version` as the marker table.** It would offer other applications'
  schemas as graphs to be dropped (§4).
