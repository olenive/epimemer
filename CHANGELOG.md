# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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


