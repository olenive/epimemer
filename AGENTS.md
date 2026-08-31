# Coding
Prefer a functional style; minimise inheritance; avoid classes with `self` or
`@staticmethod`; Pydantic BaseModel for data structures is encouraged. Type
annotations where helpful, simple beats exhaustive. Prefer uv over pip. Use
Petritype (the `petritype` package on PyPI) for complex processes and
pipelines; depend on released versions only: release Petritype first, then
bump the pin (locally, `uv pip install -e ../petritype`, never a committed
path source). Marimo notebooks: a cell is a function, so return values rather
than redefining a variable in another cell. We are building a robust, secure
system, not a prototype.

# Documentation
Prefer commas, colons, or a second sentence over em-dashes. Say what is true
rather than what is not; open with a negation only when correcting a
misreading the reader would otherwise make. Plain English with the user:
technical terms yes, dense jargon no. Name the thing, never the issue number:
a bare `#63` goes stale, so describe what it was. Delete comments that only
cite; keep ones that explain.

# Design Rules
**Never design a singleton.** No module-level mutable state, no
`get_settings()`, no import-time construction; pass configuration as a value,
the way `ServerConfig` travels through `deps["config"]`. Per-graph settings
copy the `reflect_threshold` pattern: process default on `ServerConfig`,
persisted override on the backend, one pure `resolve_*`. First ask whether it
needs a setting at all: `expected_graph` is mandatory because a guard must not
be configured by the state it guards against.

**Every backend implements the full `StorageBackend` protocol** and callers
invoke it unconditionally: no `hasattr`, no proxies, no capability flags. Ship
a no-op where nothing-to-do is a valid answer; reserve `NotImplementedError`
for what a backend genuinely cannot do. Guard tests compare signatures, not
names.

**Never compare timestamps as text in a backend query**: ISO-8601 strings sort
chronologically only while both sides render identically. Use `instant()` in
`surrealdb_adapter.py` or pad on write; `dev-docs/DEVELOPER_GUIDE.md` has the
measurements.

# Git Usage
- Do not merge into the main branch without asking.
- Keep commit messages very succinct.
- Do not add "Co-Authored-By" or similar to commit messages!

# Frontend Coding Style
1. Prefer a functional programming style.
2. Prefer Typescript over plain Javascript.
3. Use Tailwind CSS.

# Memory System (Epimemer)
The full guide to the epimemer MCP tools is `epimemer_prompts/DEFAULT.md`,
the single home for that material; read it before nontrivial memory work.
The rules that must hold on every call:

- Pass `expected_graph` on every tool call, reads included. Say the graph you
  meant; never paste the name out of a refusal. Only `list_graphs`,
  `use_graph`, `delete_graph` and `viz_status` take none.
- Claim a judge with `claim_agent` once per session before writing, and use
  whatever judge the user hands back. Claim again after `use_graph`. A refusal
  here goes to the user, never worked around.
- Every `store_decomposition` names its frame (`metacontext_id`): `the-real`
  for real-world claims, one frame per call, so a mixed document is two calls.
- Omit `confidence` and `claim_kind` rather than guess; give a one-line
  `confidence_basis` with any confidence you do supply.
- Record a verdict on every pair reflect nominates (`similarities`,
  `relation_verdicts`, `retained`); suppression is permanent, and an unjudged
  pair comes back on every reflect, for ever.
- Read `warnings` before deciding what to write, not after; `notify_user:
  true` means raise it with the user.
- Ingest after learning something worth keeping; search before answering
  anything prior context could improve; reflect when the response suggests it.
