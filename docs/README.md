# Epimemer documentation

How the built system works, and why it works that way.

| Page | Covers |
|---|---|
| [RETRIEVAL.md](RETRIEVAL.md) | How `search` is answered: the vector and keyword arms, rank fusion, segment hits, result provenance, lineage collapse, valid-time buckets |
| [VALIDITY.md](VALIDITY.md) | When a claim was true: intervals per source, the two clocks, correction against world-change, recurrence, the soundness check, boundary proposals, and correcting a period that is present and wrong |
| [REFLECTION.md](REFLECTION.md) | The review loop: the verdict taxonomy, what `reflect` nominates, what `apply_reflection` writes, archival |
| [ATTRIBUTION.md](ATTRIBUTION.md) | Who judged this: the agent registry, why the user assigns the id, how approval reaches them, what a self-description is worth, the append-only journal that answers *what did this agent judge*, reading it back with `review`, `apply_review` and `rejudge`, and the two revisions with their own tools, `reframe` and `correct_interval` |

Start with [SUMMARY.md](../SUMMARY.md) for the architecture as a whole; these
pages are the detail behind the sections that point here.

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
