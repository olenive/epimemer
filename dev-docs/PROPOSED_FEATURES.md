# Proposed features

A backlog of proposals, none of them built: what each is, why, roughly what it
costs and what has to be true before it starts. Defects go in `ISSUES.md`.

---

## Needs a decision before it needs code

### Sharing a graph between users

**What.** A way for one user to grant another read, or read and write, access
to a named graph and nothing else: the project's own development history, for
example, is kept in a graph its maintainers can read and its users cannot.

**Why.** A graph is a shared understanding, and today it can be shared only by
sharing a database.

**Decisions taken.** Two tiers. The read-only tier is the graph bundle:
`epimemer graphs export`, hand the file over or publish it, they run
`epimemer graphs import`. That is what the project's own development history
needs, and it needs no notion of a user. The live tier, people working on one
graph together, is parked until a team needs it, with the shape decided:

- The server is a stdio process the user runs, so there is no client to
  authenticate: whoever launched it is the user. Any Epimemer-level user or
  access list would be enforced by code the same person controls against a
  database they can already open. So identity is the database credential, one
  per person, and a grant is a database-level permission on one graph: on
  Postgres a role per person with privileges on the graph's schema, on
  SurrealDB a database-level user (`OWNER`, `EDITOR` or `VIEWER`). Postgres is
  the natural home for this tier, since teams already administer roles there.
  The judge registry is a separate thing: a judge is an agent identity a
  person approves, not the person.
- What Epimemer then owes a per-graph credential: a read path that writes
  nothing (`search` stamps `retrieved_at` unless the server runs with
  `EPIMEMER_RECORD_RETRIEVAL=false`, and `claim_agent` writes `last_seen_at`,
  so a viewer cannot work through the tools today, on any backend); permission
  errors turned into plain refusals naming the access level; `list_graphs`
  under a credential that cannot see every schema or namespace; a CLI that
  wraps the grant; and the database user recorded on a judge binding, since
  two people sharing one picker will pick the same judge and review would
  merge their sessions.
- A write from the dashboard waits on the same question. Every change is
  journalled against a session and a judge, and a browser has neither, which
  is why the dashboard's warning-settings panel is read-only.
- Nothing finer than a whole graph, and nothing about reaching the database
  across machines, which is the database's own configuration.

**Blockers.** A team that needs it. Two servers on one database is an
ordinary test fixture on Postgres and on a served SurrealDB, so the read-only
path and the per-graph grant can be built and tested when that team appears.

---

### Timelines on other clocks

**The state.** A timepoint's coordinate is a Python `datetime`: Gregorian,
years 1 to 9999, microsecond resolution. Fiction set in a real-shaped calendar
works. Geological time ("2.1 million years ago"), astronomical time,
sub-microsecond events (a reaction intermediate at 40 nanoseconds), and
invented calendars (a thirteen-month year; the 40k notation `0.123.456.M41`,
whose year is past 9999) cannot be stored.

**What.** Separate the coordinate from the calendar. A timeline gets a
*clock*: a unit (seconds, days, years, millions of years), an epoch (what zero
means: "present day", "start of the reaction", "year 0 of M1"), and a
precision. A timepoint's coordinate becomes a number on that clock. The
default clock is Gregorian, so ordinary timelines change nothing. Parsing and
formatting a notation is the agent's job, not the server's: the agent reads
"0.123.456.M41", writes the coordinate, and the original text stays as the
point's label, which is the "store what the source said" rule already in
force. A per-timeline description of the convention lets a fresh agent recover
it from the graph.

**Why.** Assuming linear time, everything in `TIMELINES.md` (ordering,
bounds, periodic recurrence, contradictions) is arithmetic on coordinates and
works unchanged on any clock. Only `CalendarRule` is about months and weekdays
and stays Gregorian.

**Cost.** Medium. The coordinate type touches every timeline query function,
the timeline record every storage backend stores whole, extraction, and the
dashboard axis. No backend query compares a coordinate today; one that did
would meet the trap the design rules name, numbers compared as text sorting
wrongly. `TIMELINES.md` keeps this cheap by forbidding anything that assumes a
date where a number would do.

**Blockers.** A graph that needs one. It was deliberately left out of the
`TIMELINES.md` build.

---

### Saving the in-memory store to a file

**The state.** `InMemoryStorage` has no save or load path: a graph lives in
`_GraphStore` for exactly as long as the process does. A graph that has to
outlive the process lives on SQLite, SurrealDB or Postgres instead, and SQLite
gives a command-line tool or a CI step a file per graph with no server to run.

**What.** Serialise `_GraphStore` and reload it. Persistence for an existing
backend rather than a new backend, so the full-protocol rule does not apply.

**Why.** A convenience for tools that want the in-memory store's speed and a
file at the end, worth having and not worth prioritising over designed work.

**Decisions first.**

1. **What is saved.** Nodes, edges and lifecycle records; embeddings with
   them, since they are expensive to recompute. There is no BM25 index to
   save: `memory.py` scores the corpus on every call.
2. **When it writes.** A snapshot on clean shutdown loses everything on a
   crash; writing on every mutation costs the speed that makes the in-memory
   store worth having. The choice should be stated rather than defaulted into.
3. **Format.** Measured on a synthetic corpus at the real embedding width
   (384): floats as JSON text are not viable (a gigabyte and eleven seconds at
   100,000 nodes, for 153 MB of float32), and the parts want opposite
   treatment, since node and edge records compress by an order of magnitude
   while vectors are near-incompressible. So: one zip container, structural
   records deflated, vectors stored uncompressed as a `.npy` member. One file
   cannot desync the way a sidecar pair can, `zipfile` sets compression per
   member, and nothing beyond the standard library and numpy is needed. A
   version field in the container, because a stale snapshot silently loading
   into a newer type is the failure hardest to notice.

**Blockers.** A tool that needs it and cannot use SQLite.

---

### A demo walkthrough: `epimemer demo`

**What.** One command that starts from an empty, throwaway graph, loads a small
corpus through the tool functions an agent calls, and narrates in the terminal
while the dashboard shows the graph being built, searched, corrected and
reflected over. It is the introduction for a new user and the thing to put on a
screen in a presentation.

**Decisions taken with the owner.**

- The decompositions are a fixture shipped with the package, so the demo runs
  the same every time with no model in the loop. A companion prompt runs the
  same story through Claude Code for the live version.
- Sentence-transformers when that extra is installed, so similarity means
  something; otherwise the mock embedder, with a line on screen saying so.
- A CLI subcommand with the corpus inside the package, so it works from an
  installed tool and shows up in `epimemer --help`.

**The subject.** The history of computing, 1822 to 1991, from Babbage to the
first website: four short summaries written for the purpose, real history
stored in `the-real`, with firm dates and public primary sources.

**What the corpus must show.** Dated events across the whole span, so the
timeline has a shape; a world-change (Shannon's 1948 paper retitled as the
1949 book); a correction that was honestly stated and honestly wrong (ENIAC as
the first electronic computer, retired once Colossus is declassified);
corroboration (two documents stating one claim independently); and a held
contradiction (which machine was the first programmable computer, a
disagreement of definitions).

**Cost.** Small to medium. The demo calls the tool functions directly, as
`scripts/bench.py` does, and needs the server lifespan's visualisation setup
factored into one function both can call.

---

### Open design questions

Each is a question, not a design; whichever is picked up gets its own document
first.

- **Incremental clustering.** Online HDBSCAN, centroid drift detection, split
  heuristics for topic evolution.
- **Topic evolution.** The input a split wants is *surprise*: how unlike a
  topic's existing material a new member is. That is a read-time question over
  embeddings, not a stored field, and it is nearly free where it would be
  asked: `reflect` already builds the block-wise similarity matrix over every
  topic and fact, and a per-row max over that matrix is one reduction on data
  already in hand.
- **Value-driven consolidation thresholds.** Archival thresholds are settled
  (importance ceiling, judgment age); merge and split still key off embedding
  similarity alone.
- **Contradiction resolution strategy.** A contradiction between claims is
  recorded and held, and a judge can retire one side with `supersede_by`; a
  dispute about order has `resolve_temporal_contradiction`. Whether and how
  the system should help resolve a contradiction between claims, rather than
  hold it, needs design.
- **Per-source support levels.** Confidence today is one prior on the node; a
  per-source level on the `sourced_from` edge would let a level die with the
  source it describes. It is also what a source-discredit sweep needs: when a
  document turns out fabricated, every prior derived from it overstates, and
  nothing can sweep per source today.
- **Cross-metacontext retrieval composition.** When a query straddles metacontexts
  ("compare real AI with sci-fi AI"), how should results from several metacontexts
  compose? Search takes a list of metacontexts today; composition beyond the union is
  undesigned.

---

## Tracked elsewhere, listed so the backlog is complete

- **Colour customisation, phase C4**: export/import, preset themes, and making
  the semantic hues settable. `VISUALISATION.md` Part C.
- **A dedicated read connection for viz snapshots**: deferred until the server
  gains concurrent clients.
- **A vector index**: every backend ranks vectors by brute force, and
  SurrealDB declares no vector index (`vector_search`'s docstring). The
  candidates are SurrealDB's own vector index and pgvector on Postgres, as an
  opt-in with the `bytea` path as fallback, since not every Postgres has the
  extension. A per-model matrix cache, invalidated on write, comes first if
  anything does. Each waits until brute force is measured to hurt.
