# Epimemer documentation

How the built system works, and why it works that way.

| Page | Covers |
|---|---|
| [RETRIEVAL.md](RETRIEVAL.md) | How `search` is answered: the vector and keyword arms, rank fusion, segment hits, result provenance, lineage collapse, valid-time buckets |
| [VALIDITY.md](VALIDITY.md) | When a claim was true: intervals per source, the two clocks, correction against world-change, recurrence, the soundness check, boundary proposals, and correcting a period that is present and wrong |
| [REFLECTION.md](REFLECTION.md) | The review loop: the verdict taxonomy, what `reflect` nominates, what `apply_reflection` writes, archival |
| [ATTRIBUTION.md](ATTRIBUTION.md) | Who judged this: the agent registry, why the user assigns the id, how approval reaches them, what a self-description is worth, the append-only journal that answers *what did this agent judge*, reading it back with `review`, `apply_review` and `rejudge`, and the two revisions with their own tools, `reassign_metacontext` and `correct_interval` |

Start with [SUMMARY.md](../SUMMARY.md) for the architecture as a whole; these
pages are the detail behind the sections that point here.

## Terminology

One word per concept, used the same way in the code, the tool descriptions and
every page here.

| Term | What it is |
|---|---|
| **metacontext** | The world a claim is made in: real history, a novel, one outlet's reporting. A node **stands in** a metacontext, or is **without a metacontext**. Metacontexts are nodes, and `create_metacontext` makes one |
| **topic node** | A Topic: a paragraph-length statement of a theme, or a name that gathers nodes for retrieval |
| **topic node created from a tag** | A topic node whose content is a tag name. It asserts nothing, so it stands in no metacontext, and topic merge exempts it |
| **tag** | The string passed in `tags=[...]`. It resolves, by name, to a topic node; the string itself is never a node |
| **source node** / **document node** | The node a claim came from, reached by a `sourced_from` edge. A document node is a `RawDocument`; a publisher or author is an entity Topic |
| **`tagged_with_topic` edge** | Node to topic node, written when a tag is applied. A retrieval index for `find_nodes`, carrying no evidential weight |
| **`has_metacontext` edge** | Node to metacontext, written at ingest. Absence names no metacontext |
| **judge** | The agent credited with a judgment, as a `JudgeRef` of agent id plus the digest of the self-description in force |
| **decision journal** | The append-only record of every judgment made, one `DecisionRecord` per decision, read back with `review` |
| **fact** | A claim the material states |
| **inference** | A claim derived from facts, carrying the premises it rests on |

<!-- terminology-guard: off -->
**"Frame" and "hub" are not used.** A frame is a metacontext; a hub is a topic
node or a source node. The one exception is the visualisation **hub**, the
websocket relay the dashboard connects to, which is a process rather than a
node.
<!-- terminology-guard: on -->

## Where everything else lives

| | |
|---|---|
| [README.md](../README.md) | Install, run, configure, the dashboard |
| [INTEGRATION.md](../INTEGRATION.md) | MCP setup and the canonical tool table |
| [SUMMARY.md](../SUMMARY.md) | The architecture: the concepts and the reasons for them |
| `dev-docs/` | Design notes per subsystem, benchmarks, known issues, and the backlog |

**`docs/` and `dev-docs/` answer different questions.** These pages describe
what the system does now, for someone using it. `dev-docs/` describes how each
subsystem is designed and why, for someone changing it: the current design,
the alternatives that were rejected and the measurements behind the choices.
Both are rewritten when the design changes; neither is a record of how a
decision was reached.
When a page here disagrees with the code about behaviour, the code is right
and the page is the bug.
