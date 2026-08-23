# Attribution — who judged this

**Built: the registry, the judge on every write, the setting that can require
one, the journal, and the review loop over it.** An agent can be given an
identity, the user assigns it, a session is bound to it, every decision that
session makes — during review and at ingest — carries it, every decision is also
appended to a journal, so *what did this agent judge* is one query. `review`
reads that journal back shakiest-first, `apply_review` records that somebody
checked a decision, and `rejudge` revises a judgment made at ingest without
touching the claim. The design is `dev-docs/REVIEW_MODE.md`.

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

## What a decision records

Once a session has claimed an identity, these writers record it, each on the
thing the decision landed on:

| Decision | Recorded on |
|---|---|
| `update`, `supersede_by`, `merge_facts`, `apply_reflection`'s merges and supersessions and archivals | the retired node's lifecycle episode, as `retired_by` |
| `restore`, `reverse_merge` | the same episode, as `restored_by` — a separate field, because returning is a separate decision |
| `record_contradiction`, `record_variant`, `link`, `apply_reflection`'s similarity verdicts | the edge, as `judged_by` |
| `judge_importance` | the value signal, as the latest judge — and every entry in the node's reinforcement trail names its own |
| content written during reflect: synthesised parents, splits, enrichments, merge survivors, and `update`'s replacement | the new node, as `judged_by` |
| `segment`, `store_decomposition` | every node and edge the ingest creates, as `judged_by` — including the priors `claim_kind`, `confidence` and `importance`, which nothing downstream re-makes |

Three things follow that are easy to get wrong:

- **A correction does not inherit the previous author.** The replacement is the
  correcting agent's wording, and crediting the earlier one would attribute a
  sentence they never wrote.
- **Re-recording a pair does not restamp its edge.** A second agent calling
  `record_contradiction` on a pair that already has one has *confirmed*, not
  decided. That is a review, and a review gets its own record rather than
  overwriting somebody else's name.
- **Approval is re-checked on every write.** An id the graph no longer approves
  records as unknown rather than failing the call: writing the name would assert
  an approval that no longer exists.

A document and its segments carry **no** judge. They are the material rather
than a claim about it — *who pasted this text* is a different question from *who
judged what it says*. And reusing an existing entity or tag topic does not
restamp it: mentioning a name again is not introducing it.

One decision is still **not** attributed: merging relation labels. Every subject
in the journal is a node id and this judgment's subjects are labels, so the row
has nowhere honest to put them — filed as `ISSUES.md` #69. Accepting a boundary
proposal, the other gap, is closed: it edits an existing edge rather than adding
one, which is exactly the case the journal is for, and both of its subjects are
nodes.

## The journal — what did this agent judge

Attribution on the rows answers *who judged this node*. Review asks the inverse,
and over fields scattered across facts, edges, lifecycle episodes and value
signals that is five scans and a reassembly. So every decision is **also**
appended to a journal, and the inline fields are the immutable copy.

A row carries what was decided (`kind`), what it was about (`subject_ids`), who
decided it, when, and — where an agent supplies one — how certain they were.

| Rule | Why |
|---|---|
| **Append is the only write.** No update path exists, on either backend or on the protocol | A reversal, a confirmation and an overturn are all new rows. An edit here would make review state mutable in one place and derived in another |
| **`reviewed` is derived**, from a row whose `reviews` names another | A flag on an append-only row would have to stay in sync with a copy on the node, across two backends |
| **One judgment, one row** | Forty-four facts out of one document is one reading of one document; an archival sweep is one pass over one nomination list. Reflect's other decisions get a row each, because those are independent verdicts that happen to be batched |
| **The row is written after the decision lands** | A refused write leaves no row. The other order would have review chasing a decision the graph never made |

Two kinds are worth knowing about by name. A **correction** and a
**world_change** are separate kinds rather than one supersession with a reason
attached, because they are opposite claims about what happened and a reviewer
asking for one does not want the other. And a **reversal** — undoing a merge —
is the one row that both `reviews` and `supersedes` the record it overturns.

**Re-recording a verdict writes a confirmation.** A second agent calling
`record_contradiction` on a pair that already has one leaves the edge alone and
writes a row pointing at the original decision. That is what stops a third agent
doing the same work again. Where the original predates the journal there is
nothing to point at, and the pointer stays blank rather than inventing a target.

**`certainty` is supplied by the review writers and nowhere else.**
`apply_review` and `rejudge` are calls whose whole point is a declared judgment,
so the ladder is stated there rather than added to a dozen other tool schemas.
Every other decision is unrated, which is deliberately different from a rated
0.5.

## Reading it back — `review`

`review` returns this graph's decisions **shakiest first**, so a reviewer can
start at the top and stop when it stops repaying the attention. It writes
nothing; acting on what you find goes through the ordinary decision tools.

The order is **two tiers that never blend into one score**. A decision whose
agent declared a low `certainty` comes first, ascending. Everything unrated
follows, ordered by how many *derived* signals it carries:

| Signal | What it saw |
|---|---|
| `thin_source` | a subject's own `confidence` is below 0.5 |
| `wide_merge` | three or more sources collapsed into one node |
| `open_contradiction` | recorded, and both sides are still active |
| `ground_moved` | a subject was retired *after* the decision was made |

**An unrated decision never outranks a flagged one.** Blank means *unrated*, not
*doubtful* — the same rule `confidence` follows — so no amount of derived
evidence lets absence read as a claim of doubt.

Read `unrated_count` and `unattributed_count` beside the results: three shaky
rows out of four hundred unrated is not the same answer as three out of four.
`truncated` says the list was cut; act on what came back and ask again rather
than raising the cap.

**The answer covers one graph and `graph` names which** — the journal is per
graph because a row's `subject_ids` resolve only where those nodes live. For
another, `use_graph` and ask again.

**`elsewhere` says where else there is something to see.** One count per other
graph — zeros included — plus a `total`, the filters those counts used
(`counted_with`), and any graph that could not be read (`unreadable`). No rows
and no ids: an id from another graph dereferences nowhere here, and every write
path is single-graph anyway. Going there is still `use_graph` then ask again,
which is safer than a fan-out would be because each switch is the active graph
rather than one borrowed mid-call.

The reviewer this is for is a **later, different agent**. The one that made the
decisions switched those graphs itself and never needed telling.

**It counts wider than it reads, deliberately.** Only `agent_id`, `since` and
`until` narrow the counts; `mode` and `certainty_ceiling` do not. So a graph
counted at 12 can list fewer than 12 once you get there, and `counted_with` is
what tells you that is scope rather than a defect. A count too high costs a
wasted look; a count too low costs the look entirely.

### Modes and filters

A **mode** names the selection; every other argument narrows whatever it
selected. So *"what did agent-1 decide yesterday that nobody has checked"* is
one call rather than three vocabularies.

| Mode | Selects | Needs |
|---|---|---|
| `all` | every row | — |
| `by_agent` | one judge's decisions | `agent_id` |
| `since` | a time window; `until` is exclusive | `since` |
| `unreviewed` | rows no other record points back at | — |

`by_agent` and `since` are the same filters the other modes accept, made
**mandatory**. That is their whole value: asking for `all` with an `agent_id`
you forgot to pass returns the entire journal, which reads as an answer rather
than as a missing filter.

`certainty_ceiling` is for **counting**, not browsing — the ordering already
covers browsing. *"Is anything below 0.5 still outstanding before I stop?"* is a
gate, and a gate wants a number. It is inclusive, and it leaves unrated rows out
entirely, since blank cannot be told from ordinary.

**It sees only what was decided after the journal existed.** Anything the graph
was told before then left no row, and nothing can reconstruct one.

## Recording that somebody checked — `apply_review`

If an agent checks a decision and agrees, and nothing records that, the next
agent does the same work again. So a review is a row pointing back, and
`apply_review` is the only thing that writes one.

It takes `confirmations` and `dissents`, each entry `{decision_id, because,
subject_ids?, certainty?, certainty_basis?}`. `because` is required in both:
a review with no reason is a rubber stamp, and it costs more than nothing —
it marks the decision checked, so the next reviewer skips it without being able
to tell whether it was examined or waved through.

**Neither list changes the graph, and the dissent least of all.** Undoing a
merge is `reverse_merge`; an archival, `restore`; a `one_claim` verdict, a
`distinct` through `apply_reflection`; a wrong ingest prior, `rejudge`. A
dissent records the *finding* and sets only `reviews` — never `supersedes` —
because a row claiming to have overturned a decision whose effect still stands
would put the journal in disagreement with the graph. It is most useful exactly
where the undo was **refused**: a merge whose survivor has since been
contradicted cannot be reversed at all, and this is where that finding goes.

`subject_ids` narrows a review to what was actually looked at. One pointer at an
ingest record covering forty-four facts otherwise tells the graph a reviewer
checked forty-four when it checked six.

**A retry must not read as a second opinion.** The same judge confirming the
same decision twice is refused, naming the row that already says it. A
*different* judge is not a retry — it is the second independent check this whole
design exists to make possible. On a graph that does not require a judge, two
blanks cannot be told apart, so a retry there writes a second row: one more
thing `require_judge` buys.

## Revising a judgment — `rejudge`

`claim_kind`, `confidence` and `confidence_basis` are supplied by an agent that
read the material, and nothing downstream re-makes them. Until `rejudge` existed,
review could find every ingest-time mistake and fix none of them.

**It is not a correction and not a supersession.** `update` is for a claim that
was wrong or a world that moved. This is for a claim that is fine where the
*judgment about* it was wrong — a fact labelled `state` that is really an
`event`, a confidence set too high. Nothing here retires a node, moves an edge
or writes lineage, and the node keeps its `judged_by`: that field records who
wrote the wording, which is unchanged.

**The value it replaces is kept**, appended to the node's `rejudgments` trail
with what it was, what it became and why — otherwise this would be the one call
in the system that destroys a judgment rather than superseding it.

`importance` is not here. `judge_importance` is already this tool for that one
field, and two writers for one value is how it ends up depending on which ran
last.

**Two gaps it deliberately does not cover** (`ISSUES.md` #66): a metacontext
assignment still cannot be withdrawn, and a validity interval still cannot be
corrected. Both are the same shape, and both are load-bearing enough that
answering them inside a tool named for ingest priors would bury an epistemic
move in a metadata utility.

## Requiring a judge

By default a write may name a judge or not, and a blank means *unknown*. A user
who wants every write tied to an agent or a person turns the requirement on:

```
epimemer agents require on            # this graph
EPIMEMER_REQUIRE_JUDGE=true           # every graph this server opens
```

With it on, a write from a session that has not claimed an approved identity is
**refused**, with a message naming `claim_agent` and this graph's approved ids.
It covers the twelve tools that create or retire epistemic content; timelines and
metacontexts are scaffolding rather than claims and stay outside it.

Three things about it are deliberate:

- **No MCP tool can change it.** `configure_reflection` and `configure_merge` are
  agent-callable because they tune how eagerly the system nominates things. This
  is a gate on the agent itself, and a gate the agent can open is decoration.
- **It is not retroactive.** Turning it on says nothing about earlier writes:
  the rows that had no judge still have none, and still mean unknown.
- **Turning it on before approving an id refuses everything**, so the command
  says so at the moment you switch it on rather than letting the next write
  explain it.

A client whose connection cannot hold a session binding is not locked out: a
claim that could not bind to a session is held for the process instead, which is
safe precisely because a transport with no sessions has one client.

**One cost of *blank means unknown*, stated rather than discovered.** On a graph
that never turns the requirement on, a row written before attribution existed
and a row written yesterday by an agent that did not name itself are
**permanently indistinguishable** — both are blank, and nothing else separates
them. That is the price of the rule, and it was paid deliberately: the
alternative read blank as *"written before a date"*, which asserts something
about every unattributed row that nobody checked. Turning the requirement on
draws the line from that moment forward; it cannot draw one backwards.

## Where it lives

A per-graph `agent` table beside `fact` / `topic` / `inference`, with the
approved-id list in per-graph settings beside the reflect counter. Agents are
deliberately **not** graph nodes: as nodes they would surface in `search` and be
swept by `reflect`, and two agents with similar descriptions are not a topic to
merge.

The journal is a per-graph `decision` table beside them, indexed on the judge's
id, the date, the kind, the subjects and what a row reviews — the five reads
review mode needs. Its rows are not nodes either, for the same reason and one
more: a judgment about the graph is not a claim the graph holds.

**Per graph, and that follows from the ids.** `subject_ids` holds node ids, and
a node id resolves only in the graph that holds it — so a row filed anywhere
else would carry ids that dereference nowhere. The row lives with its subjects,
which is also true when a write lands somewhere unintended: the material and the
record of it stay together. *What did this agent decide* is therefore asked once
per graph, with `list_graphs` and `use_graph` between the asks, and deliberately
not by a fan-out — a switch is the active state, while a fan-out has to borrow it
mid-call and give it back.
