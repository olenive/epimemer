# Epimemer documentation

How each part of Epimemer behaves for someone using it.

| Page | Covers |
|---|---|
| [INGEST.md](INGEST.md) | How `segment` cuts a document into passages, the doubts the server raises about its own cut, and stating or redoing a cut yourself |
| [RETRIEVAL.md](RETRIEVAL.md) | How `search` is answered: the two arms, rank fusion, result provenance, history and valid-time buckets |
| [VALIDITY.md](VALIDITY.md) | When a claim was true: intervals per source, correction against world-change, a retired claim becoming true again, the soundness check, and correcting a period |
| [TIMELINES.md](TIMELINES.md) | When something happened and in what order: dated points, stated order and its contradictions, and recurrence rules |
| [REFLECTION.md](REFLECTION.md) | The review loop: verdicts, what `reflect` nominates, what `apply_reflection` writes, archival and `reopen` |
| [ATTRIBUTION.md](ATTRIBUTION.md) | Who judged this: the judge registry, approval, the decision journal, and reading it back with `review`, `apply_review`, `rejudge` and `reassign_metacontext` |

Start with [SUMMARY.md](../SUMMARY.md) for the architecture as a whole; these
pages are the detail behind the sections that point here.

## Terminology

One word per concept, used the same way in the code, the tool descriptions and
every page here.

| Term | What it is |
|---|---|
| **metacontext** | The world a claim is made in: real history, a novel, one outlet's reporting. A node **stands in** a metacontext, or is **without a metacontext**. Metacontexts are nodes, and `create_metacontext` makes one |
| **topic node** | A Topic: a paragraph-length statement of a theme, or a name that gathers nodes for retrieval |
| **topic node created from a tag** | A topic node whose content is a tag name. It asserts nothing, so it stands in every metacontext it is used from: the union of the metacontexts of the nodes tagged with it |
| **tag** | The string passed in `tags=[...]`. It resolves, by name, to a topic node; the string itself is never a node |
| **source node** / **document node** | The node a claim came from, reached by a `sourced_from` edge. A document node is a `RawDocument`; a publisher or author is an entity Topic |
| **`tagged_with_topic` edge** | Node to topic node, written when a tag is applied. A retrieval index for `find_nodes`, carrying no evidential weight |
| **`has_metacontext` edge** | Node to metacontext, written at ingest. Absence names no metacontext |
| **judge** | The agent credited with a judgment, under the name the user approved and the self-description it gave at the time |
| **decision journal** | The append-only record of every judgment made, read back with `review` |
| **fact** | A claim the material states |
| **inference** | A claim derived from facts, carrying the premises it rests on |

<!-- terminology-guard: off -->
**"Frame" and "hub" are not used.** A frame is a metacontext; a hub is a topic
node or a source node. The one exception is the visualisation **hub**, the
websocket relay the dashboard connects to, which is a process rather than a
node.
<!-- terminology-guard: on -->

## Where everything else lives

[README.md](../README.md) covers install, configuration and the dashboard;
[INTEGRATION.md](../INTEGRATION.md) MCP setup and the canonical tool table;
`dev-docs/` the design of each subsystem, for someone changing it (see
[CONTRIBUTING.md](../CONTRIBUTING.md#where-the-design-lives)). These pages
describe what the system does now, for someone using it. When one disagrees
with the code about behaviour, the code is right and the page is the bug.
