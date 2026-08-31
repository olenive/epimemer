# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.1.1] — 2026-08-31

### Fixed

- The wheel now carries `epimemer_prompts/DEFAULT.md`. `INTEGRATION.md` tells a
  reader to open the agent guidance and add it to their agent's instructions,
  and 0.1.0 shipped without the file, so anyone installing from PyPI was
  pointed at something they did not have.

## [0.1.0] — 2026-08-31

First public release. **Removed from PyPI**: the wheel was built without
`epimemer_prompts/DEFAULT.md`, which the documentation tells readers to open.
Use 0.1.1.

- An MCP server holding an agent's knowledge as a typed graph of topics, facts
  and inferences, with provenance edges to every source.
- Epistemic frames (metacontexts) that say which world a claim is about.
- Validity intervals per source, with correction and world-change kept
  distinct.
- A review loop: `reflect` nominates near-duplicates, contradictions, stale
  evidence and never-retrieved nodes; `apply_reflection` records a verdict on
  each, and a verdict once recorded is never asked again.
- A decision journal naming the judge behind every judgment, read back
  shakiest-first by `review`.
- Two storage backends behind one protocol: in-memory, and SurrealDB (embedded
  or served).
- A standalone visualization hub for the graph, pipeline execution and
  timelines.
- The `epimemer` CLI for the acts reserved for a person: approving, renaming
  and requiring judges.
