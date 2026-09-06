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

## Ready to build

### Specialized timelines

**What.** Only the base `Timeline` / `Timepoint` model exists. Three
specialised backing structures are envisaged and do not:

- **`PreciseTimeline`**: a datetime interval index supporting range and
  proximity queries.
- **`VagueTimeline`**: labelled points ordered by relative before/after
  constraints rather than coordinates.
- **`CyclicalTimeline`**: templates such as "every Monday", mapped to concrete
  instances when something links to them.

**Why.** The timeline panel currently plots what curation and extraction happen
to produce. These are what would let a caller *ask questions* of a timeline
(what happened near this, what overlaps this, what recurs) rather than only
render it.

**Cost.** The largest item in this file. Each needs add/remove/reorder with
stable UUIDs, its own query surface (proximity, overlap detection), and a
storage round-trip on both backends per the parity rule. `VagueTimeline` is the
awkward one: relative ordering is a constraint graph, and it has to stay
coherent when constraints conflict.

**Blockers.** None technical, but worth a decision first: whether these are
three types or one type with a mode. The current `Timepoint` already spans
concrete, interval, and label-only, so the case for three separate models is
not obvious and should be argued before any of it is built.

One constraint is already decided (`VALIDITY_DESIGN.md`, T2): recurrence-rule
facts such as "Christmas is Dec 24–26, annually" are `CyclicalTimeline`'s case
and never route through supersession or restore. They never stop being true, so
they have no lifecycle, and their occurrences are separate event facts.

---

## Needs a decision before it needs code

### Sharing a graph between users

**The state.** A graph is reachable only through the server that opened it, and
that server has one set of credentials. Two people who want to work on one
graph share a server, and share everything else on it.

**What.** A way for one user to grant another read, or read and write, access
to a named graph and nothing else: the project's own development history, for
example, is kept in a graph its maintainers can read and its users cannot.

**Why.** A graph is a shared understanding, and today it can be shared only by
sharing a database.

**Blockers.** Everything. This needs a notion of a user the server does not
have, an authorisation model, and a decision about whether the judge registry
(`claim_agent`) is the right seed for identity or a separate thing. Do not
start from the storage layer.

---

### Serving the agent guidance over MCP

**The state.** `epimemer_prompts/DEFAULT.md` holds the full guidance on using
the tools well, and `INTEGRATION.md` tells users to copy it into their agent's
instructions. The server's own MCP `instructions` string is three sentences.

**What.** Serve the guidance through MCP itself, so nothing needs copying:
expose `DEFAULT.md` as an MCP prompt (FastMCP supports prompt registration, and
clients such as Claude Code surface prompts to the user), and keep the server
`instructions` string as the short orientation it is. The file stays the single
source; the prompt reads it.

**Why.** A copy pasted into a project's CLAUDE.md goes stale the day the
guidance changes, and the MCP protocol already has a channel for exactly this.

**Cost.** Small: one prompt registration reading the file, a test that it
matches the file, and the `INTEGRATION.md` section updated to name the prompt.

**Blockers.** None. One decision worth making at the same time: whether a
shorter always-loaded variant (the per-call rules only) should be offered
beside the full guide, since 43 KB is a lot to hold in every context window.

---

### Advisories on the dashboard

**The state.** Advisories exist, are recorded, and reach the agent. Nothing
shows them to a person watching the graph.

**The obstacle is one decision, not the work.** The event bus emits at the five
`_tx` boundaries (`EVENT_LOG.md`), and an advisory is not a transaction: it is
computed before one and may accompany a call that writes nothing. So this needs
either a new event kind or a deliberate choice to carry the advisory on the act
that triggered it. The second is cheaper and couples the two; the first is
honest about what an advisory is.

**Also worth deciding at the same time**: the settings panel for
`configure_warnings`. It is per graph and has to say so on its face; the
dashboard follows a `use_graph` switch, and a panel that looks global while
writing per-graph state is a trap. *Inherited* is a fourth visual state beside
the two actions, because a kind following the process default is not the same
as one explicitly set to the same value: only the first tracks a changed
default, and without showing which, clearing an override is impossible through
the UI. Reuse `SemanticPalette` in `theme.ts` rather than minting colours.

**Cost.** Small once the event decision is made.

---

### Similar-inference edges

**The state.** `reflect` nominates near-identical inferences that share a
premise. Ones that do not are nominated by nothing, and the proposal is to
offer `similarity` edges between them. Nothing enforces node types on
`SIMILARITY` (`link` checks only that both nodes exist) and retrieval already
traverses it, so the expansion benefit arrives for free.

**One consequence has to be decided first, and it is a live change to a number
callers read.** `corroboration` walks `SIMILARITY` neighbours, so
inference-to-inference edges would make agreeing inferences corroborate each
other. Defensible as independent support, and not a retrieval nicety.

**The decision is narrower than it looks.** The corroboration walk treats a
`SIMILARITY` neighbour three ways: *counted*, *excluded* (contradiction,
variant, corrected) or *reported without counting* (`adjacent_periods`). So
this is a choice among three existing treatments rather than a yes/no. Agreeing
inferences most likely want the third.

*Files*: a nominee list in `pipelines/reflection/` beside the existing ones,
capped as the pair-built lists are, and `pipelines/query/corroboration.py` for
the consequence.

---

### LLM-guided and hybrid segmentation

**The state.** Paragraph and semantic-similarity segmentation are built and
cover the current use cases. LLM-guided splitting is designed nowhere and
buildable only after an architectural decision.

**The question.** The server makes no LLM calls of its own (`SUMMARY.md`), and
that is load-bearing: it is why the system has no API keys, no provider
configuration, no per-call cost, and no opinion about which model you use. Two
ways out, and they are not close:

1. **Delegate the split to the calling agent.** The agent segments and passes
   the result to `store_decomposition`, exactly as it already does for
   decomposition. Preserves the property entirely. Costs a round trip and makes
   segmentation quality the agent's problem.
2. **Re-introduce a provider abstraction.** The server calls a model itself.
   More capable and self-contained; gives up the property and everything that
   follows from it.

**Recommendation, for whoever picks this up.** Option 1, unless something
concrete turns out to be impossible that way. The no-LLM-calls property is
worth more than the convenience, and it is far easier to give up later than to
win back.

**Blockers.** The decision above. No code should be written before it.

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
- **Metacontext inheritance scope.** If a frame is inherited from a document,
  do inferences derived from those facts inherit it too? Probably yes, but the
  edge cases need thought.
- **Cross-frame retrieval composition.** When a query straddles frames
  ("compare real AI with sci-fi AI"), how should results from several frames
  compose? Search takes a list of frames today; composition beyond the union is
  undesigned.

---

## Tracked elsewhere, listed so the backlog is complete

- **Colour customisation, phase C4**: export/import, preset themes, and making
  the semantic hues settable. `VISUALISATION.md` Part C.
- **A dedicated read connection for viz snapshots**: deferred until the server
  gains concurrent clients.
- **Native HNSW vector indexes**: the `TODO` in `surrealdb_adapter.py`'s
  vector search. Waiting on SurrealDB, not on us.
- **Valid-time rendering on the timeline panel**: designed in
  `TIMELINE_VISUALISATION.md` §13, with a checked-in visual reference at
  `dev-docs/mockups/valid-time-grammar.html`. The validity model it renders is
  built, but the viz snapshot carries no validity yet, so two parts remain:
  per-source intervals into `snapshot.py`, then the SVG grammar. The grammar was
  designed early because it pins two decisions (gaps are never styled as false,
  bars fade through the now-line) that would otherwise be made by accident in
  the first renderer. Its colour set is already shared with the graph panel as
  `SemanticPalette` in `theme.ts` (`VISUALISATION.md` C.6).
