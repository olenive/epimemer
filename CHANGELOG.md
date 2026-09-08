# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] — 2026-09-08

**"Frame" and "hub" are gone as words.** Both read as general graph
vocabulary: a frame sounds like any context you care to name, and a hub like
any well-connected node. So users, agents and the tool descriptions themselves
drifted into using them loosely, and two precise concepts blurred into two
vague ones. A metacontext is the world a claim is made in, and a topic node or
a source node is a node other nodes point at. Warning about the looser reading
would have left both words in play; removing them is what actually ends it.

The graph rewrites its own stored strings when 0.2.0 opens it, so no manual
step is needed. That migration is one-way: a graph opened by 0.2.0 holds the
new spellings and should not be reopened by 0.1.x, which has no step that puts
them back.

**One metacontext could bleed into another through a shared topic node.** A
topic node created from a tag asserts nothing, so it stands in no metacontext,
and every node tagged with that name points at the same one whatever world it
was claimed in. A fact from a novel and a fact about real history therefore sit
one hop apart, through a node neither of them is a claim about, and the read
tools walked straight across it. A search scoped to `the-real` filtered its
node list correctly and then returned edges naming the fiction nodes it had
just removed, plus the fiction document's own raw passages. `query_graph`
returned every neighbour whatever world it belonged to, and did not even say
which world that was.

### Changed

- The MCP tool `reframe` is now `reassign_metacontext`, with the same
  arguments and the same behaviour.
- Response keys: `same_frame` → `same_metacontext`, `nodes_without_frame` →
  `nodes_without_metacontext` (in `graph_stats`), `already_framed` →
  `already_in_metacontext`, `frames_now` → `metacontexts_now`.
- Advisory kinds, as they appear in a response's `warnings` and as keys of the
  policy `configure_warnings` stores: `cross_frame` → `cross_metacontext`,
  `same_frame_variant` → `same_metacontext_variant`,
  `same_frame_contradiction` → `same_metacontext_contradiction`.
- Decision journal kinds, as `review` and `query_decisions` return them:
  `frame_declaration` → `metacontext_declaration`, `reframe` →
  `metacontext_reassignment`. A journal row's `frame` field is now
  `metacontext`.
- The CLI command `epimemer frames declare --frame` is now
  `epimemer metacontexts declare --metacontext`.
- The storage protocol method `count_nodes_without_frame` is now
  `count_nodes_without_metacontext`, on both backends and the instrumented
  wrapper. `epimemer/pipelines/frames.py` is now
  `epimemer/pipelines/metacontexts.py`, and the names in it move the same way:
  `declare_frames` → `declare_metacontext`, `shared_frame_set` →
  `shared_metacontext_set`, `frame_edges` → `metacontext_edges`,
  `is_tag_topic` → `created_from_tag`, and so on through the package.
- A tag is the string passed in `tags=[...]`; the node it resolves to is a
  topic node created from that tag. "Tag topic" is not used.
- **Schema version 4, applied when a graph is opened.** It rewrites the two
  journal kinds, moves each journal row's `frame` field to `metacontext`,
  rewrites the renamed advisory kinds inside the prose of every
  `proceeded_despite_advisory` row and in the graph's own warning policy, and
  moves the metacontext-reassignment trail on each node it finds one on.
- **A Terminology section** in `docs/README.md` and in
  `epimemer_prompts/DEFAULT.md`, naming one word per concept, plus a guard in
  `tests/test_docs.py` that fails when a retired word reappears in the
  documentation or the code.

- `find_nodes`, `query_graph` and `topic_tree` take an optional `metacontexts`
  list, meaning on each what it means on `search`: results are the nodes
  standing in any of the metacontexts named, no metacontext inherits another,
  and an id that resolves nowhere is refused with the graph's own list rather
  than silently narrowing the answer. The node the caller named by id is
  returned whether or not it stands in one of them, which is what keeps a
  topic node created from a tag usable as a starting point; its neighbours are
  filtered. On `find_nodes` the filter runs before the `limit` cut. On
  `topic_tree` an ancestor outside the scope is left out and the rest are
  still ancestors, and a subtopic outside it takes its branch with it.
- `find_nodes`, `query_graph` and `topic_tree` label every node they return
  with the `metacontexts` it stands in, as `search` already did. Reading a
  traversal used to mean asking again, node by node, which world each answer
  was a claim about.

### Security

- Every dependency is updated to a version with no published advisory. The
  installed set went from 19 packages carrying advisories to none. The ones
  reachable from this code were `starlette`, whose `StaticFiles` served the
  visualization hub, and `aiohttp`, which `surrealdb` uses for every graph
  operation. The rest sat behind FastMCP's OAuth and OpenAPI features, which
  this server does not use, or behind the `notebooks` extra.
- The `fastmcp` and `starlette` floors rise to the releases that fixed those
  advisories, so an install resolving to the oldest permitted version no longer
  gets a vulnerable one.

### Fixed

- A metacontext-scoped `search` now scopes the whole response. `edges` keeps
  only edges whose two endpoints are both returned nodes, so no returned edge
  names a node the scope removed. `segments` keeps only passages with at least
  one node extracted from them standing in a named metacontext, matching each
  passage to what was concluded from it. A passage nothing was extracted from
  is dropped from a scoped search and still returned by an unscoped one, which
  goes on answering *where did I read that?* for every passage that matched.

- **A tag resolves to one node however its name is spelled.** `claim_kind` and
  `claim-kind` were two topic nodes, and so were `design-decisions` and `design
  decisions`, because a tag was matched on its exact content. Names are now
  compared with case and separators collapsed, at ingest and at
  `find_nodes(tagged_with_topic=...)`. Dates are untouched, which is the point:
  the similarity bar cannot separate `claim-kind` from `claim_kind` at 0.9196
  without also fusing two different days of work at 0.9935. Where the spelling
  asked for is not the one stored, `store_decomposition` reports it under
  `tags_resolved_to` rather than resolving it silently.
- **A tag name whose node was retired now resolves to the node carrying its
  content.** Enrichment rewrites a topic's content and retires the old node, and
  a merge retires its sources; either left the old name resolving to nothing, so
  the next document carrying it created a second topic node while
  `find_nodes(tagged_with_topic=...)` returned an empty list rather than an
  error. Resolution follows `merged_into` and `superseded_by` forward. A
  historical node is deliberately not followed: its claim is still right of its
  period and its name has not moved.
- Two tags whose names are equal once spelling is collapsed may be merged
  without clearing the topic similarity threshold. An identical normalised name
  is a stronger proof that two tags are one tag than a cosine between their
  spellings, which is what let `claim-kind` and `claim_kind` sit below the bar
  at 0.9196 while pairs that must never merge sat above it. The exemption is
  narrow: every source must be a tag, and every key must match.

## [0.1.3] — 2026-09-06

A patch release on the same reasoning as 0.1.2: no feature changes, and
existing graphs migrate themselves when opened.

### Changed

- A keep verdict now lives only in the decision journal. It used to be written
  twice, as a `retention` row whose anchors were prose at the end of
  `certainty_basis` and as one edge per anchor; the anchors are now a `covers`
  field on the row, and an empty `covers` means the node was kept for its own
  sake. Nothing changes in the `apply_reflection(retained=...)` call: the same
  `covers` goes in, and a later change to the evidence is still a reason the old
  keep does not cover.
- `query_decisions` takes `subject_ids`, matching rows that name any of them.
  One query serves a whole population of nodes, which is what reading keep
  verdicts back needs.
- **Graphs migrate themselves to schema version 3 on open**, wherever a graph is
  opened: embedded and remote alike, on connect and on every graph switch. The
  step parses each retention row's anchors out of its prose into `covers`,
  writes a row for any keep that existed only as edges, and deletes the edges.
  There is nothing to run by hand. **It is one-way**: 0.1.2 reads the anchors
  from the edges, which are gone. To go back, re-derive one
  `review_confirmed` edge from each retention row's `covers` (`src_id` the
  anchor, `dst_id` the subject, and the subject's own id where `covers` is
  empty), then `DELETE schema_version;` so the next upgrade migrates again.

### Removed

- The `review_confirmed` edge type. It was a hand-built index over verdicts the
  journal could not be queried for, and the shape with no anchor had to be faked
  as an edge from a node to itself, which drew as a self-loop in the visualiser
  and was indistinguishable from a real relation.

## [0.1.2] — 2026-09-06

A patch release rather than a minor one, on purpose: no feature changes, and
an existing graph opens under this version and updates itself. Two things to
know before upgrading are in the **Changed** entries below: one tool argument
is renamed, and a graph this version has opened cannot be read by 0.1.1
without a manual step.

### Changed

- `tagged_with` is now `tagged_with_topic`. The old name read as a string stuck
  on the node; the new one says what is at the far end. The vocabulary that goes
  with it: a *tag* is the name passed in `tags=`, a *tag topic* is the Topic it
  becomes. **`find_nodes(tagged_with=...)` is now
  `find_nodes(tagged_with_topic=...)`**, and a call using the old argument name
  is refused. An agent picks the new name up from the tool schema and from the
  guidance shipped in the wheel; only a script or a custom prompt that spells
  out the old argument needs editing.
- The fact → topic reading of `supports` is now its own edge type,
  `extracted_under_topic`. `supports` means fact → inference and nothing else,
  which is what corroboration and the soundness check already took it for; the
  new type is what `reflect` reads when it gathers a topic's material. Nothing
  in the API changes: a caller writing `link(..., edge_type="supports")` between
  a fact and a topic is still accepted, and should now write
  `extracted_under_topic`, which is what the readers of that pair look for.
- **Graphs migrate themselves.** Opening a graph written before this release
  renames both edge types in place, once, and stamps a `schema_version` record
  so later opens skip the work. It runs wherever a graph is opened: embedded
  (`mem://`, `rocksdb:`) and remote (`ws://`) alike, on connect and on every
  graph switch. There is nothing to run by hand, and the migration can be
  deleted from the code once no graph older than this release is expected.
  **It is one-way.** 0.1.1 does not know the new names, so a graph this
  version has opened will not load its renamed edges under the old version.
  To go back, run these against the graph's database first:
  `UPDATE node_edge SET type = 'tagged_with' WHERE type = 'tagged_with_topic';`
  and `UPDATE node_edge SET type = 'supports' WHERE type = 'extracted_under_topic';`,
  then `DELETE schema_version;` so the next upgrade migrates again.

### Removed

- The `associated_timeline` edge type. Nothing wrote it and nothing read it; a
  node reaches its timeline through the `timelink` edge, which points at the
  timeline and names the timepoint in its metadata.

### Fixed

- An inference flagged `evidence_merged` can now be kept. The flag asked for a
  re-read but nothing recorded one, so every such inference came back on every
  `reflect`. `apply_reflection(retained=...)` now anchors a keep to the absorbed
  ids, and `reflect` stops listing the node once they are covered. The archival
  nominator still never reads the flag.
- `retained` measures `covers` against what is still open on a node, not
  against every reason it has ever carried. A node kept against one premise and
  later flagged on another no longer demands the first premise be named again,
  and naming it is refused as already covered rather than written twice.
- `similarity_edges_written` in the `apply_reflection` response counts the
  `similarity` edges a batch wrote, where it previously counted every edge the
  decisions wrote. A `distinct` verdict now reports zero rather than reporting
  the `assessed` edge that records the decline. **A caller comparing this
  number across versions will see it change**: one `one_claim` reports 1 where
  it reported 2.
- Tag Topics merge with one another whatever frame stamps they carry. A tag
  names something rather than asserting it, so it stands in no frame, and the
  frame-equality gate on topic merge left a tag written before
  `epimemer frames declare` permanently unmergeable with one written after.
  The gate still applies wherever any source is a statement, and the survivor
  of an all-tag merge stays a tag.

## [0.1.1] — 2026-08-31

### Fixed

- The wheel now carries `epimemer_prompts/DEFAULT.md`. `INTEGRATION.md` tells a
  reader to open the agent guidance and add it to their agent's instructions,
  and 0.1.0 shipped without the file, so anyone installing from PyPI was
  pointed at something they did not have.


