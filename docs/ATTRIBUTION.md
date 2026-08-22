# Attribution — who judged this

**Built so far: the registry.** An agent can be given an identity, the user
assigns it, and a session can be bound to it. Nothing yet *records* that
identity against a decision — that is the next step, and until it lands this
page describes a registry with nothing pointing at it. The design for the rest
is `dev-docs/REVIEW_MODE.md`.

## The problem it exists for

No decision in this system used to record who made it. Not nodes, not edges, not
`LifecycleEpisode`, not `NodeChangeEvent`. A second agent could see what was
decided and when; it could not see that a *different* agent did it, and on its
own second pass it could not tell its own decisions from the first agent's.

That is the whole motivation: **using a different agent to review the decisions
previously made by the first agent.** Without identity, `reviewed_by ==
judged_by` is unfalsifiable and self-review is indistinguishable from
independent review.

## Identity is assigned, not minted

An agent **proposes** an id and describes itself; the **user** approves, edits,
or names a different one. The approved pair is what gets recorded.

```
claim_agent(agent_id="olegs-critic", description="Claude Opus, running as the reviewer pass")
```

Three things follow from the id being the user's:

- **An unapproved id is refused.** The refusal is the prompt — there is no
  separate startup handshake, so the message the agent gets is what it puts to
  the user, and it names every channel they can approve through.
- **The user owns the semantics.** Whether ids track a model (*"my llama
  agent"*), a role (*"my critic"*), or a task (*"my editor reviewer"*) is their
  scheme. Two harnesses running the same model are one judge or two exactly as
  they decide.
- **The same id can appear in two graphs**, because the user can assign it in
  both. Correlating them is a human act.

Hashing the description to get an id was rejected: reword it and you become a
different judge; paste someone else's and you become the same one. The hash
survives one level down, as the **digest** of a description *version*.

## Descriptions append, and are never edited

`Agent.descriptions` is an append-only list of dated `AgentDescription`s — the
same shape as `LifecycleEpisode` on a node, for the same reason: a scalar plus a
timestamp cannot express *changed, and here is what it was before*. A decision
made last week was made by whatever the agent claimed to be last week, and that
claim has to stay readable after it changes its mind.

Re-claiming with **identical** text is not a new version. Only changed wording
appends.

## A description is a claim, not a credential

Nothing verifies it. It is self-reported prose, exactly like a fact the agent
ingests, and it must never be read as a trust signal. Two rules keep it honest:

- **`confirmed_at` is the only part with human weight.** It is set only through
  a channel that terminates at the user, and `None` means *self-described,
  unconfirmed* — a different object, never collapsed into the same field.
- **The judge gates nothing automatically.** No ranking, no corroboration
  weighting, no default filter. Review will *select* on it; nothing *decides* on
  it.

## Approval reaches the user, not the agent

No MCP tool can approve an id: a tool the agent calls cannot establish that the
*user* called it. Three channels can, and all three terminate at a person.

| Channel | When it works | What `confirmed_at` then means |
|---|---|---|
| the client's elicitation prompt | the client supports elicitation | the user answered through their own UI |
| `EPIMEMER_APPROVED_AGENTS` | always; read when the backend connects and when the server lands on a graph | the user configured the server before starting it |
| `epimemer agents confirm <id>` | **served SurrealDB only** | the user ran a command the agent cannot run |

The CLI's limit is not an oversight. Approvals live in per-graph settings inside
the backend, and an embedded store (`mem://`, `file://`, `surrealkv://`, or the
in-memory backend) lives inside the server process — a second connection to it
is a *separate store*, not a second view of one. Writing there would report
success into a store the running server never reads, so the command refuses and
names the environment variable instead.

`epimemer agents list` shows a graph's approved ids and what each agent has said
about itself, marked confirmed or self-reported.

## Approval is per graph

Graphs are isolated, and so are their approved-id lists. A session binds **one**
judge, so `use_graph` re-checks it: a judge the new graph has not approved is
unbound, and the response says so. Carrying a judge approved for graph A into
every write on graph B is how attribution starts recording something nobody
approved.

Ids from `EPIMEMER_APPROVED_AGENTS` are applied to whatever graph the server
lands on, and applied *before* the judge is re-checked — otherwise configuration
would clear a judge it was about to admit.

## Where it lives

A per-graph `agent` table beside `fact` / `topic` / `inference`, with the
approved-id list in per-graph settings beside the reflect counter. Agents are
deliberately **not** graph nodes: as nodes they would surface in `search` and be
swept by `reflect`, and two agents with similar descriptions are not a topic to
merge.
