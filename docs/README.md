# Epimemer documentation

How the built system works, and why it works that way.

| Page | Covers |
|---|---|
| [RETRIEVAL.md](RETRIEVAL.md) | How `search` is answered — the vector and keyword arms, rank fusion, segment hits, result provenance, lineage collapse, valid-time buckets |
| [VALIDITY.md](VALIDITY.md) | When a claim was true — intervals per source, the two clocks, correction vs world-change, recurrence, the soundness check, boundary proposals |
| [REFLECTION.md](REFLECTION.md) | The review loop — the verdict taxonomy, what `reflect` nominates, what `apply_reflection` writes, archival |
| [ATTRIBUTION.md](ATTRIBUTION.md) | Who judged this — the agent registry, why the user assigns the id, how approval reaches them, what a self-description is worth, the append-only journal that answers *what did this agent judge*, and reading it back with `review` / `apply_review` / `rejudge` |

Start with [SUMMARY.md](../SUMMARY.md) for the architecture as a whole; these
pages are the detail behind the sections that point here.

## Where everything else lives

| | |
|---|---|
| [README.md](../README.md) | Install, run, configure, the dashboard |
| [INTEGRATION.md](../INTEGRATION.md) | MCP setup and the canonical tool table |
| [SUMMARY.md](../SUMMARY.md) | Architectural design — the concepts and their rationale |
| `dev-docs/` | Design history, benchmarks, known issues, and the backlog |

**`docs/` and `dev-docs/` are not the same thing.** These pages describe what the
system does now, and are rewritten when the behaviour changes. `dev-docs/` is a
record of how decisions were reached — dated notes, rejected alternatives,
measurements, review amendments — and is appended to rather than rewritten. When
the two disagree about behaviour, the code is right and this directory is the bug.
