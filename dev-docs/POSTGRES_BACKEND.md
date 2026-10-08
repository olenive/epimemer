# A Postgres storage backend

The fourth backend, and the one for a graph several people or several servers
write at once. It has the shape of the SQLite backend (`SQLITE_BACKEND.md`),
so this note is about what differs: a server over a socket, schemas instead of
files, real timestamp types, a driver dependency, and a test suite that needs a
database running. The code will be `epimemer/storage/postgres_adapter.py`.

---

## 1. What it is for

A SQLite file and an embedded SurrealDB store belong to one machine. A served
SurrealDB can be shared, and a team that already runs SurrealDB should keep
doing so. Most teams run Postgres, and will try a tool that stores its graph
in the database they already operate, back up and secure, before they stand
up a new one. That is the whole case: the shared-graph story, offered on the
database people have.

What "shared" means here is several Epimemer server processes, on one machine
or many, reading and writing one graph. Each process keeps its own active
graph, its own judge claims and its own visualization session, exactly as it
does against a served SurrealDB today; the graph, the journal, the counters
and the per-graph settings live in Postgres and are read on every call, never
cached in a process (`SQLITE_BACKEND.md` §7 checked this for the judge
approvals).

## 2. What it touches

Beyond the adapter: the same list as the SQLite backend's §2, plus

| Where | Change |
|---|---|
| `pyproject.toml`, `uv.lock` | An optional extra `postgres` carrying `psycopg[binary]`. Not a default dependency: a compiled driver nobody asked for is how a plain install breaks |
| `epimemer/mcp/config.py` | `storage_backend` gains `"postgres"`; `postgres_dsn` from `EPIMEMER_POSTGRES_DSN`; a factory branch that refuses without the extra, naming it, as the embedding provider does |
| `tests/conftest.py`, `tests/storage/test_storage_parity.py` | A fourth parameter that is **skipped** unless `EPIMEMER_TEST_POSTGRES_DSN` is set |
| `tests/storage/test_postgres_storage.py` | What only this backend can get wrong (§9) |
| `Makefile`, `.github/workflows/ci.yml` | `make test-postgres`: a throwaway container, the suite against it, torn down after. A CI job that runs it |
| `epimemer/cli.py` | `unreachable_store` returns `None`: a served store the CLI reaches like a served SurrealDB. `graphs convert` gains `--to-dsn` |
| `README.md`, `INTEGRATION.md`, `dev-docs/DEVELOPER_GUIDE.md`, `CHANGELOG.md` | A Postgres section after SQLite's, the env row, the install extra, the opt-in test section |

## 3. Driver and connection

`psycopg` 3, async, with the `binary` wheel. One `AsyncConnection` per backend
instance, opened in `connect()` with `autocommit=True`, so a transaction exists
only where the code says so: the ten `*_tx` methods, the two
read-modify-writes and `store_timeline`'s version check (`TIMELINES.md` §2.6),
each `async with connection.transaction()`.

**Statements on one connection must not interleave.** psycopg serialises
operations on a connection, but two tool calls in flight in one process could
still have their statements alternate, which is the hazard the SurrealDB
adapter documents. Every multi-statement unit here runs under one
`asyncio.Lock`, and a transaction never awaits anything but its own
statements.

**It owns reconnection.** A server outlives the databases it talks to. On
`OperationalError` outside a transaction, reconnect once and retry the
statement; inside a transaction, let the error propagate, since the
transaction is gone with the socket. The constraints in `_reconnect` in the
SurrealDB adapter apply: never re-enter from the task already holding the lock.

## 4. Schemas, not files

One Postgres database holds many graphs, **one schema per graph**, the schema
named after the graph. `validate_graph_name` already restricts names to a safe
identifier set, and every identifier still goes through `psycopg.sql.Identifier`.

**Every statement names its schema explicitly**, `"<graph>".node`, rather than
relying on `search_path`. Then `switch_database` changes a Python attribute
under the guard's mover turn and nothing on the connection, a reconnect cannot
lose the active graph, and cross-graph reads (`viz_list_*`,
`count_decisions_by_graph`) are ordinary queries against another schema with
no second connection and no mover turn.

| Method | Does |
|---|---|
| `list_databases()` | The schemas holding a `schema_version` table, sorted. Other schemas in the database are not graphs and are not offered |
| `switch_database(name)` | Under `moving()`: `CREATE SCHEMA IF NOT EXISTS`, create the tables if absent, run the version stepper, set the attribute. Creates on use, as every backend does |
| `delete_database(name)` | Refuses the active graph; `DROP SCHEMA ... CASCADE`; a name with no schema is `KeyError` |
| `location` | Host, port and database from the DSN, "one schema per graph". **Never the password**: parse with `psycopg.conninfo.conninfo_to_dict` and render only those three |

A database the DSN names must exist and the role must be allowed to create
schemas in it. Creating databases is not something an application should do
to a server it was pointed at.

## 5. Tables

The SQLite table set and filter columns, with Postgres types:

- `uid text primary key`, filter columns as `text`, `data jsonb`.
- Timestamps as `timestamptz`: `created_at`, `superseded_at`, `decided_at`.
  The rule about comparing instants is met by the type, so bounds are passed
  as datetimes and nothing is rendered to text for comparison. `data` keeps
  Pydantic's rendering, as everywhere.
- Vectors as `bytea`, little-endian float64, the same blob the SQLite backend
  writes, read by the same numpy path. Not pgvector, yet (§8).
- `graph_state(key text primary key, value jsonb)`, `schema_version(version
  integer not null)` at 1, with the empty stepper.
- The same indexes as SQLite, and `decision.subject_ids` as `jsonb` filtered
  with the containment operator, which a GIN index can serve later if the
  journal grows.

Upserts are `INSERT ... ON CONFLICT (uid) DO UPDATE`. Insert-only writes
(`write_verbatim_tx`, `write_batch_tx`, verdicts) are plain inserts and a
reused id fails the transaction, as on SQLite. Counters bump with one
`UPDATE ... RETURNING` so two processes bumping at once both count.

## 6. Search

Brute force, as on every backend: pull the model's vectors with the node
filter, one normalised matrix product, stable sort. Lexical search reuses
`storage/bm25.py` over the pulled corpus; Postgres full-text search would be a
third analyzer and is not used, for the reason `SQLITE_BACKEND.md` §6 gives.

## 7. Configuration

```
EPIMEMER_STORAGE_BACKEND=postgres
EPIMEMER_POSTGRES_DSN=postgresql://epimemer:secret@db.example.org:5432/epimemer
EPIMEMER_GRAPH=team
```

Installed with `pip install "epimemer[postgres]"`. The DSN is anything libpq
accepts, a URL or `key=value` pairs, so `sslmode`, a connection timeout and a
password file all work without Epimemer knowing about them. There is no
default DSN: a server asked for Postgres without one refuses at startup and
names the variable, since there is no local place a database could default to.

## 8. Later, and rejected

- **pgvector.** The first chance any backend has of a real vector index. It
  needs the extension installed, which not every Postgres has, so it comes as
  an opt-in once brute force is measured to hurt, with a fallback to the
  `bytea` path. Not in the first version.
- **A connection pool.** One connection per server process is what every
  other backend does and what the graph guard assumes. A pool buys concurrency
  the server does not use and makes the lock story harder to reason about.
- **`search_path`.** Connection state that a reconnect would have to restore
  and a cross-graph read would have to work around. Explicit schema names
  cost nothing.
- **One database per graph.** Needs `CREATE DATABASE`, cannot run inside a
  transaction, and means a connection per graph. Schemas are what Postgres
  has for this.
- **Postgres full-text search for ranking.** A third analyzer (§6).

## 9. Tests

**Parity first.** The fourth fixture parameter puts the behavioural suite on
Postgres, about 1,800 tests. It is skipped unless `EPIMEMER_TEST_POSTGRES_DSN`
is set, so a bare `pytest` stays green with no database, and the skip count
says so. Under the DSN, each test gets a fresh default graph and the fixture
drops every graph schema it finds at teardown, so tests isolate as long as
they run in sequence, which they do. Expect a minute or two, schema creation
being the cost.

`make test-postgres` starts `postgres:17-alpine` on a port of its own
(`PG_PORT`, default 5433, refused if in use, as `SURREAL_PORT` is), waits for
`pg_isready`, exports the DSN, runs the parity suite selected to the Postgres
parameter plus `test_postgres_storage.py`, and stops the container on exit.
CI gets a job for it beside the SurrealDB integration job.

`tests/storage/test_postgres_storage.py` holds what only this backend can get
wrong:

- Two instances on one DSN and graph see each other's writes, and one's
  `set_approved_agent_ids` is the other's next `get_approved_agent_ids`.
- Two instances bumping the reflect counter at once end at the sum.
- Two instances writing one timeline from one version: the second is refused
  with `TimelineConflict`, and of two writes at once exactly one lands.
- `delete_database` drops the schema and refuses the active graph;
  `list_databases` ignores a schema with no marker table.
- `location` names host, port and database and never the password.
- A `*_tx` method that fails half way leaves nothing behind.
- A dropped connection outside a transaction is rebuilt on the next call.
- The whole-second timestamp case still holds with `timestamptz` bounds.

**A two-instance test for SurrealDB** goes into
`test_surrealdb_integration.py` at the same time, since the shared-graph claim
is made for a served SurrealDB too and nothing exercised it.

## 10. As built

The adapter landed on 8 October 2026 at about 2,400 lines, with these
departures from the sections above, each for a reason worth keeping:

- **The marker table is `epimemer_graph(name, version)`, not
  `schema_version`.** Flyway before version 5 and many hand-written scripts
  name a table `schema_version`, often in `public`. With that name a team's
  own schema would list as a graph, and an agent's `delete_graph` would drop
  it with `CASCADE`.
- **Derived schema names.** A graph name Postgres cannot hold as an
  identifier (64 characters, or one starting `pg_`) lives in a schema named
  from its first characters and a hash, and the marker records the real name.
  The derived form uses a character a graph name cannot, so it never collides
  with a plain one. Without this a legal graph name could not be imported here
  from another backend.
- **Another application's schema is refused.** `switch_database` will not
  create tables in a schema that already holds tables but no marker; an
  empty, pre-made schema is adopted. `delete_database` treats a schema without
  the marker as absent and never drops it.
- **Insertion order and long text.** Each table has an identity column
  standing in for SQLite's `rowid`, kept across upserts, so unordered reads
  come back in insertion order on every backend. `node.content` and
  `document.source` use hash indexes, because a B-tree entry is capped at
  about 2.7 kB and a long fact would fail to store.
- **Every method holds the lock**, not only the multi-statement ones.
- **Two servers at once.** A lifecycle change locks the node row it starts
  from, merging a relation label locks that table for its transaction, and
  creating or deleting a graph runs under an advisory lock keyed on the name,
  so two servers opening a new graph at once create it once.
- **Reconnection** retries a unit of work once, and only when the connection
  is actually closed after the error. A cancelled statement or a lock timeout
  is the same exception class and is not retried. A bump whose reply was lost
  after it committed could count twice, which the code says beside the bump.
- **Collation.** `uid` columns use the `C` collation, so the journal's id
  tie-break is code-point order whatever locale the database has.
- **A graph a newer Epimemer wrote is refused.** A marker whose version is
  higher than the code knows raises `SchemaTooNew` inside the opening
  transaction, which rolls back having written nothing. On a team's shared
  database this is the case to expect: one server upgraded before another.

Known limits, none exercised by a test: `jsonb` normalises numbers inside
free-form dicts (`1e16` comes back as an integer, `-0.0` as `0.0`), and
Postgres text cannot hold a NUL character, so such a record fails here where
the other backends accept it. On Windows psycopg's async mode needs a selector
event loop, which is untested.

## 11. Size and sequence

About the SQLite adapter's size plus reconnection, so 2,000 to 2,300 lines.
The same branch as the SQLite work, after `graphs convert`, in this order:

1. The extra, the adapter, the fixtures with the skip, `make test-postgres`.
   The parity suite against a local container is the gate.
2. `test_postgres_storage.py`, and the SurrealDB two-instance test.
3. Config, CLI, convert's `--to-dsn`, docs, the CI job, the changelog entry.
