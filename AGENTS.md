# Calude Code Usage Insturctions
If there are large or easily abstractable chunks of coding, as well as routine tasks such as running tests and parsing their outputs, please hand these off to a Opus 5 subagent to save on context in the main thread.

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
The server sends the per-call rules (`epimemer_prompts/RULES.md`) as its MCP
instructions and serves the full guide (`epimemer_prompts/DEFAULT.md`) as the
MCP prompt `guide`; read the guide before nontrivial memory work. Those two
files are the single home for that material, so change them rather than
restating rules here.
