# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [Unreleased]

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

## [0.1.1] — 2026-08-31

### Fixed

- The wheel now carries `epimemer_prompts/DEFAULT.md`. `INTEGRATION.md` tells a
  reader to open the agent guidance and add it to their agent's instructions,
  and 0.1.0 shipped without the file, so anyone installing from PyPI was
  pointed at something they did not have.


