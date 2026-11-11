## Epimemer: what holds on every call

Epimemer is an append-only epistemic memory: topic nodes, facts and inferences
in a graph, a judge credited for every judgment, a journal of every decision.
Corrections create new versions and nothing is destroyed. The full guide is
the tool `guide`: call it for the list of sections, and read the one for the
work at hand before nontrivial memory work.

- Pass `expected_graph` on every call, reads included: the graph you mean,
  from what the user asked for or the `use_graph` you made, never a name
  pasted out of a refusal. Only `list_graphs`, `use_graph`, `delete_graph`,
  `viz_status` and `guide` take none.
- Claim a judge with `claim_agent` before writing, and again after
  `use_graph`, a reconnect, or a write refused because the seat changed (a new
  model, client, client version or conversation). Use whatever judge the user
  hands back; a refusal goes to the user, never worked around.
- Carry the `judge_token` your claim returns on every write that takes one.
  Agents sharing a connection, a subagent beside the agent that spawned it,
  each claim their own judge and carry their own token; a write without one
  is credited to the most recent claim on the connection.
- Every `store_decomposition` names one metacontext (`metacontext_id`):
  `the-real` for real-world claims, and a mixed document is two calls.
- Omit `confidence` and `claim_kind` rather than guess; give a one-line
  `confidence_basis` with any confidence you do supply.
- Answer every nomination reflect puts to you, the ones you decline included:
  an unanswered one comes back on every reflect. `apply_reflection` records a
  decline (`similarities`, `relation_verdicts`, `retained`, `deferred`,
  `descriptions_confirmed`, `splits_declined`, `boundaries_declined`).
  `deferred` only puts a `never_retrieved` nominee off, for a number of
  reflect rounds that doubles each time. `reopen` withdraws a decline, a keep
  or a deferral you think was wrong.
- Read `warnings` before deciding what to write; `notify_user: true` means
  raise it with the user.
- One word per concept: **metacontext** (the world a claim is made in),
  **topic node**, **topic node created from a tag**, **source node**. A **tag**
  is only the string in `tags=[...]`; it resolves to a topic node and is never
  a node itself.
- Ingest after learning something worth keeping; search before answering
  anything prior context could improve; reflect when a response suggests it.
  On `backup_suggested`, ask the user and call `backup_graph` only if they
  agree.
