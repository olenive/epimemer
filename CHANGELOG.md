# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- `tagged_with` is now `tagged_with_topic`. The old name read as a string stuck
  on the node; the new one says what is at the far end. The vocabulary that goes
  with it: a *tag* is the name passed in `tags=`, a *tag topic* is the Topic it
  becomes. **`find_nodes(tagged_with=...)` is now
  `find_nodes(tagged_with_topic=...)`**, and a call using the old argument name
  is refused.
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


