# Proposed features

Work that does not exist yet. This is a backlog, not a set of designs: each
entry carries what it is, why it is worth doing, roughly what it costs, and what
has to be true before it can start. A feature gets a real design when it is
picked up, in its own document, as `VISUALISATION.md`,
`TIMELINE_VISUALISATION.md` and `REVIEW_EPISTEMIC.md` all did. Designs written
far ahead of building are usually wrong by the time anyone reads them.

Three files divide the work:

| File | Holds |
|---|---|
| `ISSUES.md` | Things that are **wrong**: bugs, and fixes deferred with a stated trigger |
| **This file** | Things that **do not exist** and might be worth building |
| Per-feature design docs | **How** a specific thing gets built, written when it is picked up |

Nothing is duplicated across them. An item here that turns out to be a defect
moves to `ISSUES.md`; an item that gets picked up grows a design document and
is reduced here to a pointer; an item that ships is deleted, and the CHANGELOG
and git history are the record.

Entries are written to be picked up cold. The six things an actionable entry
carries are listed in `ISSUES.md` under *Working an issue*, and they apply here
too, with **Blockers** standing in for that list's *decision* row. The
difference in kind: an issue must say what breaks, and an entry here must say
what does not exist. An entry that can only be justified as "this is currently
wrong" belongs in `ISSUES.md` instead.

Claim an entry by name in your commit message.

---

## Needs a decision before it needs code

### Sharing a graph between users

**The state, 2026-10-08.** A graph lives on a served SurrealDB or, since the
Postgres backend, in a Postgres database, and several servers can open it at
once: each person runs their own server against the shared database with
their own credential, and the two-instance tests on both backends show one
server seeing another's writes. What is still missing is everything finer
than "can open the database": read-only access, a grant on one graph and not
its neighbours, and knowing which person was behind a judge binding.

**What.** A way for one user to grant another read, or read and write, access
to a named graph and nothing else: the project's own development history, for
example, is kept in a graph its maintainers can read and its users cannot.

**Why.** A graph is a shared understanding, and today it can be shared only by
sharing a database.

**Decisions taken, 2026-09-12.** Two tiers. The read-only tier is the graph
bundle: `epimemer graphs export`, hand the file over or publish it, they run
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
  nothing (`search` stamps `retrieved_at` and `claim_agent` writes
  `last_seen_at`, so a viewer cannot search today, on any backend); permission
  errors turned into plain refusals naming the access level; `list_graphs`
  under a credential that cannot see every schema or namespace; a CLI that
  wraps the grant; and the database user recorded on a judge binding, since
  two people sharing one picker will pick the same judge and review would
  merge their sessions.
- Nothing finer than a whole graph, and nothing about reaching the database
  across machines, which is the database's own configuration.

**Blockers.** A team that needs it. Two servers on one database is now an
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
both storage backends (SurrealDB stores dates as strings, and numbers compared
as strings sort wrongly, the trap the design rules name), extraction, and the
dashboard axis. `TIMELINES.md` keeps this cheap by forbidding anything that
assumes a date where a number would do.

**Blockers.** A graph that needs one. Decided on 2026-09-16 not to build it
alongside `TIMELINES.md`.

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
- **Contradiction resolution strategy.** Contradictions surface and are
  recorded; whether and how the system should help resolve rather than merely
  hold them needs design.
- **Per-source support levels.** Confidence today is one prior on the node; a
  per-source level on the `sourced_from` edge would let a level die with the
  source it describes, and is also what a source-discredit sweep needs
  (`ISSUES.md`, *Older carry-overs*).
- **Metacontext inheritance scope.** If a metacontext is inherited from a document,
  do inferences derived from those facts inherit it too? Probably yes, but the
  edge cases need thought.
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
- **Native HNSW vector indexes**: the `TODO` in `surrealdb_adapter.py`'s
  vector search. Waiting on SurrealDB, not on us.
