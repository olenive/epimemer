## Epimemer: what holds on every call

Epimemer is an append-only epistemic memory: topic nodes, facts and inferences
in a graph, a judge credited for every judgment, and a journal of every
decision. Nothing is destroyed; corrections create new versions and leave the
history linked. The full guide, when to ingest, search, reflect and review, is
the MCP prompt `guide`; read it before nontrivial memory work. The rules below
hold on every call:

- Pass `expected_graph` on every tool call, reads included. Say the graph you
  mean, from what the user asked for or the `use_graph` you made; never paste
  the name out of a refusal. Only `list_graphs`, `use_graph`, `delete_graph`
  and `viz_status` take none.
- Claim a judge with `claim_agent` once per session before writing, and again
  after `use_graph` or a reconnect. Use whatever judge the user hands back. A
  refusal here goes to the user, never worked around.
- Carry the `judge_token` your claim returns as `judge_token` on every write.
  Several agents can share one connection, a subagent beside the agent that
  spawned it, and each claims its own judge and carries its own token; a write
  without one is credited to the most recent claim on the connection.
- Every `store_decomposition` names its metacontext (`metacontext_id`):
  `the-real` for real-world claims, one metacontext per call, so a mixed
  document is two calls.
- Omit `confidence` and `claim_kind` rather than guess; give a one-line
  `confidence_basis` with any confidence you do supply.
- Record a verdict on every pair reflect nominates (`similarities`,
  `relation_verdicts`, `retained`); a verdict stops the pair being offered
  again, and an unjudged pair comes back on every reflect, for ever. `reopen`
  is how a verdict you think was wrong is put back in front of a judge.
- Read `warnings` before deciding what to write, not after; `notify_user: true`
  means raise it with the user.
- One word per concept: **metacontext** (the world a claim is made in),
  **topic node**, **topic node created from a tag**, **source node**. A **tag**
  is only the string in `tags=[...]`; it resolves to a topic node and is never
  a node itself.
- Ingest after learning something worth keeping; search before answering
  anything prior context could improve; reflect when a response suggests it;
  call `backup_graph` when a response says `backup_suggested`.
