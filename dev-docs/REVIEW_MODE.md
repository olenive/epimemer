# Review mode: who judged this, and can someone else check it

A design, built: the code is `epimemer/pipelines/review/`, the judge registry and decision journal on every storage backend, and merge reversal in `epimemer/pipelines/graph_construction/versioning.py`.

The motivating case, in the user's words: *"using a different agent to review
the decisions previously made by the first agent"*. That needs every decision
to record who made it, a registry that makes "a different agent" provable
rather than self-asserted, a journal that answers *what did this agent
judge* in one query, a reader over that journal, and a bounded, guarded way
to undo the one operation whose undo information is destroyed at the moment
it runs. This document covers those five parts together because none works
alone. `docs/ATTRIBUTION.md` is the behaviour a caller sees.

Design it depends on: `REVIEW_EPISTEMIC.md` §3 (the verdict taxonomy),
`EVENT_LOG.md` (the durable change path this extends), and
`WARNINGS_AND_SETTINGS.md` (warnings, whose journal rows this reads).

---

## 1. Recording a decline

### 1.1 Every verdict has a writer

`reflect` nominates pairs and the agent classifies each one. Every verdict in
`REVIEW_EPISTEMIC.md` §3 has an action, and `compatible` is recorded with
`apply_reflection(similarities=[…])`. A decline with no record is
re-nominated on every reflect, to an agent who cannot know it was refused.

### 1.2 A decline is two populations, not one

A single `similarity` edge for every decline would serve two readers that
want opposite breadth. The nomination sweeps want it broad: suppress every
pair anybody assessed. Corroboration wants it narrow: its neighbourhood
is restatements of one claim, and a wrong `similarity` edge overstates the
count. Write one edge for both and *"these
two are different claims"* starts corroborating, which is manufactured
support, the worst failure this system can produce: a false merge does not
lose information, it inverts the quantity corroboration measures.

| Why the agent declined | It is | Records |
|---|---|---|
| `redundant`, but `merge_facts` refused (an event, or an unjudged `claim_kind`) | genuinely one claim | `similarity` **and** `assessed` |
| `compatible`: different claims that merely look alike | not one claim | `assessed` only |

The other refusals need no new action: a cross-metacontext pair is
`record_variant`, and a retired twin is `recurs` and `restore`.

`assessed` is a denormalised suppression index and passes §3.4's rule: it is
immutable and append-only, so it cannot drift from the journal that also
records the decision. Every pair sweep skips a pair carrying any of
`similarity`, `contradiction`, `variant_of` or `assessed`, through one shared
reader.

### 1.3 The action

`docs/REFLECTION.md` §6 has the verdicts and what each writes. The rules whose
reasons live here:

- **Both sides must be `ACTIVE` or `HISTORICAL`.** An `assessed` edge earns
  its place by suppressing a nomination, so it belongs exactly where a
  nomination could have happened; `CORRECTED`, `ARCHIVED` and `MERGED` are
  refused because a judgment there suppresses nothing and a `similarity` edge
  would still be counted as support.
- **`one_claim` is refused across metacontexts.** A `similarity` edge across
  two of them is a fiction corroborating a fact.
- **Withdrawal is one-way.** `distinct` over a standing `one_claim` writes a
  `retracted_similarity` edge that disqualifies it, the mechanism
  corroboration already runs for `contradiction`. Nothing re-asserts
  `one_claim` over a withdrawal, because withholding support costs a count
  while inventing it inverts the quantity.
- **Judgment edges never migrate on retirement.** `similarity`,
  `contradiction`, `variant_of` and `assessed` stay anchored on every
  retirement, correction and merge included. The replacement starts with no
  judgments and is correctly re-nominated (`REVIEW_EPISTEMIC.md` §4.2).
- **The edges record a judgment.** A sweep that wrote them for every pair
  over the bar would fill the graph with assertions nobody made and suppress
  its own future nominations. Similarity nominates; the agent judges.
- **A suppression is taken back only by `reopen`**, which asserts nothing
  about the answer. A standing `one_claim` is withdrawn by `distinct`, never
  by `reopen`.

---

## 2. The registry: what an agent is

### 2.1 Identity is minted, not derived

Hashing the self-description into an id fails in both directions: reword the
description and become a different judge, so one judge's history fragments;
paste the same description and two genuinely different agents are
indistinguishable. So identity has three layers: an opaque key the server
mints and nobody is shown, a name the user can change freely, and an
append-only list of dated self-descriptions, each pinned by a digest.

A decision records a `JudgeRef`, the pair `(agent_id, digest)`: the id says
who, the digest pins what the judge claimed to be at the moment it decided,
with no as-of query needed. The name and the description resolve by opposite
rules on purpose: *which judge is this* wants the name the user knows it by
now, so a rename carries old rows with it; *what did this judge claim to be
when it decided this* wants the claim as it stood.

`former_ids` is aliasing, migration and repair in one list. Consolidating two
records that were always one judge rewrites nothing and deletes nothing: the
survivor takes the other's keys and both description histories, and old
journal rows keep the key they were written with. After a consolidation a
judge is a set of keys, so every lookup by judge takes the set.

The append-only list of dated descriptions is the same shape
`LifecycleEpisode` uses: a scalar plus a timestamp cannot express *changed,
and here is what it was before*.

### 2.2 The user assigns identity, and that is what makes review provable

An agent that mints or claims its own id cannot establish that it is a
different agent from the one that decided yesterday; `reviewed_by ==
judged_by` and self-review is indistinguishable from independent review. So
identity is **proposed, not claimed**: the agent offers a name and a
description through `claim_agent`, the server puts it to the user, and the
approved pair is what gets recorded.

**The question goes up on every bind**, as a pick from the judges this graph
already knows rather than a name to type, because once an id is admitted
every later session would otherwise bind to it with no user involvement, and
a refusal naming the approved ids would make a wrong guess a directory
lookup. What makes asking every time affordable is that the answer is usually
the first line offered. Asked once per session, per graph, per identity,
keyed on the identity, because a memo meaning *this session confirmed
something* would let an agent be approved as one judge and bind silently as
another. A changed description is still put to the user.

**The prompt says which agent is asking.** One connection carries several
agents, a subagent beside the agent that spawned it, so the prompt names the
judge being proposed and, where this connection already judges as somebody,
names them too. The self-description comes last, because a terminal cuts the
end and the proposed name is the part that must survive the cut.

**The proposed name is a line in the picker** rather than a blank field,
because a client may draw free text as a required field (Claude Code does),
and then the only way to take the proposal would be to retype it. It is left
off where it resolves to a judge already listed, since offering a listed
judge's name as new is how a history gets split.

Two states are distinct: **declined** (the question reached a person and they
said no) refuses even a pre-approved id; **unavailable** (no elicitation
channel exists) falls back to the approved list, since that approval is the
user's involvement happening earlier rather than not at all.

**Renaming lives on the same channel**, the elicitation prompt and the CLI,
because a handle an agent could rename is a handle an agent could point at
another judge's history. A name collision is a question, not a refusal: two
records that should be one is the commonest reason to be renaming, so the
collision asks whether they are the same judge, and yes consolidates.

**Retiring lives there too, and for the same reason**: a handle an agent could
retire is a handle an agent could use to take a rival judge off the roster. A
retired judge cannot be bound by any route, while its record, history and
decisions stay readable, which is what separates retiring from deleting. The
trail is an append-only list of episodes, on `LifecycleEpisode`'s argument.
Reinstating takes two gestures, because one keystroke undoing a deliberate
removal is the mistake retiring exists to prevent.

**Deleting is gated on a scan and is CLI-only.** A record goes only where
nothing in the graph names any of its keys and it has absorbed nothing;
anything else is retired, because `judged_by` holds a key and the key has to
keep resolving to a name for as long as anything carries it. `node_judge_ids`
is the one enumeration of where a `JudgeRef` sits on a node, and the SQLite,
Postgres and SurrealDB clauses named `_NODE_NAMES_JUDGE` are its counterparts:
a field added to one and missed in another would let the command delete a
record something still points at.

**The user owns the semantics.** Whether ids track a model, a role or a task
is the user's scheme. The server can detect a new session or a different
client; it can never detect a different model inside one harness, because the
model is never on the wire. That question is the user's to answer, which is
what the flow assumes. A client hook can tell the server which model is
seated, and §2.6 uses that to decide when the question has to be put again;
it still never decides the answer.

### 2.3 Confirmation reaches the user, not the agent

`ctx.elicit` inverts the direction of an MCP call: the server asks, and the
answer comes back from the user through the client's own UI. Where the
client cannot elicit, `epimemer agents confirm <id>` is a command the agent
cannot run, and `EPIMEMER_APPROVED_AGENTS` seeds approval at connect for
embedded stores the CLI cannot reach. No path exists by which the
agent alone sets `confirmed_at`; a `confirm_agent` tool the agent could call
is refused on principle, since a tool the agent calls cannot establish that
the user called it. `epimemer agents confirm` stamps the current
description too, since the user vouches for the wording in front of them, and
`agents list` sits beside it because approving an id blind is the same gap in
miniature.

A tool that waits on a person cannot share the tool timeout, or *the user was
still reading* would read as *the client cannot elicit*, the one direction
this must not fail in.

### 2.4 Self-description is a claim, not a credential

An agent describing itself is making an assertion, exactly like a fact it
ingests. Nothing verifies it. That is fine for an audit trail and not fine as
a trust signal, and the risk grows with adoption: on five decisions a human
eyeballs the field; on six hundred thousand somebody builds a filter like
*"only count facts judged by agent X"* and forgets what the field is made of.
Two rules follow: `confirmed_at` is the only part with human weight, and
**the judge never gates anything automatically**. No ranking, no
corroboration weighting, no default filter. Review mode selects on it;
nothing decides on it.

### 2.5 Where it lives

A per-graph `agent` table, with the approved-id list in per-graph settings,
because graphs are isolated by design and the user can approve the same
judge in two graphs when they want correlation. Rejected: a shared registry
(breaks the isolation every other table follows) and agents as graph nodes
(they would surface in search and be swept by `reflect`; two agents with
similar descriptions are not a topic to merge). `list_agents` is
protocol-only, not an MCP tool: the roster is for the user and for review
mode, and handing the agent a list of judges is one step from the filtering
§8 refuses.

### 2.6 The seat: when a confirmation carries over

**The seat** is where a claim is made from: the conversation it is made in,
the client that carries it, and the model behind it. A judge is confirmed in a
seat; change any part and it is a different seat, and the user is asked again.
A seat is complete with the conversation and the model alone: client info is
optional in the MCP handshake and Claude Code sends none, so the client fields
count when compared and are never required.

**Why a confirmation is kept per seat rather than per server process.** The
picker goes up on every fresh bind, and an in-process memo keeps a re-claim of
the same judge in the same graph quiet only while the process lives. A `/mcp`
reconnect in Claude Code restarts the stdio server, so an in-process memo dies
and the next Epimemer call asks again; when that call comes from an unattended
hourly loop, the session waits on the picker until the user comes back. A
restart is a boundary the person never asked for, nothing about the agent
changed across it, and nobody may be watching. A new conversation, a new client
or a new model is where *who is behind the name* can change, so that is where
the question belongs. A confirmation from a complete seat is therefore
persisted per graph on the backend, beside the approved-id list, and a claim
whose seat matches one for the resolved judge binds without asking, while
that judge is still approved and not retired, and says so.

**A seat that is not fully known asks**, and every claim that asks says why.
The in-process memo remains for the incomplete seat only.

**The model needs the harness.** The model is never on the MCP wire, so the
server cannot see it. Claude Code's hooks can, and `epimemer client-state
record` runs as those hooks and writes what they report to a file the server
reads on every claim and every write; `epimemer/client_state.py` describes the
files. A missing or unreadable file reads as *model unknown*, never as an
error. Whether `/fast` or spawning a subagent fires `PostModelSwitch` is
undocumented; where it does not, the files keep the model the conversation
started with.

**A write from a changed seat is refused.** The judge token and the session
binding each record the seat their claim was made from, and the seat is
resolved again on every write. Any difference refuses the write and
names it, known to unknown and unknown to known included, because
the user vouched for whoever sat in the old seat. Claiming again asks the user
which judge the new seat is. Reads are not gated.

**An accepted hole: subagents.** A subagent shares its parent's MCP connection
and conversation id, so the file names the parent's model and a subagent on a
different model sits in the parent's seat. Tokens still keep the two judges'
writes apart (§3.2); what the seat cannot do is tell their models apart.

**A bound, not an expiry.** Nothing times out. A graph keeps the newest 500
confirmations, ordered by the instant rather than its text, and a seat that
falls off the end simply asks again. Client-state files not written for 30
days are pruned, and a conversation resumed after that records itself again.

---

## 3. Attribution: everywhere, including ingest

### 3.1 Scope

Every write path carries a judge, ingest included. Ingest is where the
unreviewable judgments live: `claim_kind`, `confidence` and `importance` are
supplied by the agent that read the material, frozen at that moment, and
re-examined by nothing. `docs/ATTRIBUTION.md` lists where each decision
records its judge. Retire and restore name two judges on one episode, because
they are two decisions often months and sometimes two agents apart; each
importance reinforcement names its own, because three judgments by three
agents compose into one number and the trail is the only place they stay
separable.

A correction does not inherit the previous node's judge: the replacement is
this agent's wording. Reusing an entity or a topic node created from a tag does
not restamp it, and
re-recording a pair that already has its edge
leaves the judge alone: a second agent calling the same tool has confirmed,
which is a review with a record of its own (§6.4), not an overwrite. The
document and its segments carry no judge: *who pasted this text* is a
different question from *who judged what it says*. Approval is re-checked on
every write, and a revoked id records as unknown rather than raising.

### 3.2 Threading it, without a singleton

The obvious implementation is an ambient "current agent". That is a
singleton and this project does not have those. `storage` is already passed
explicitly into every tool; `claim_agent` binds the session and hands back a
`judge_token` naming that claim, which the agent passes on every write; the
resolved judge rides down as one more explicit parameter,
`judge: JudgeRef | None`, resolved once at the tool boundary and never a module
global. The token is what lets one connection carry several agents: a subagent
is given the connection its parent is already using, so both claim, and a write
that names its own token is credited to the judge that claimed it rather than
to whichever claimed last. A token the session never issued refuses the write,
since falling back to the binding is the misattribution the token prevents.
Whether a blank is accepted is the graph's policy, not the signature's (§3.3).
The binding, the tokens and the confirmed-judge memo live on the lifespan,
created fresh by each server process. The server speaks stdio only, so one
process serves one client and a token means nothing on any other connection.
FastMCP session state cannot hold them: on the 2026-07-28 protocol era each
request arrives on a new connection object and FastMCP keys its state store on
it, so a token minted by one call was unknown to the next.

### 3.3 What absence means

**Blank means unknown. That is the whole of it.** It carries no date and
asserts nothing about why nobody is named. Reading anything more into it
(*written before attribution existed*, say) would give a blank a meaning
nobody asserted, for the same reason an unrated confidence is stored as
absent rather than as 0.5.

**No backfill, ever.** Stamping a synthetic legacy agent asserts that an
agent existed and made a judgment. The positive half: a review of an
unattributed decision writes a new record naming the reviewer, pointing back
at the old one (§4), and the reviewed record is untouched. So a graph that
ran unattributed for months can still take a fully attributed review pass,
and the result reads honestly: *judged by unknown, reviewed by this agent on
this date*.

An unknown judge is dropped from retrieval responses rather than sent as
null. A missing confidence is a caveat about the claim; a missing judge says
only that this graph does not record one, which is true of every node in it.

### 3.3.1 The setting

`require_judge` is one per-graph setting, off by default; with it on, a write
without a judge is refused (`docs/ATTRIBUTION.md`, *Requiring a judge*).
It is not an MCP tool. `configure_reflection` and `configure_merge` are
agent-callable because they tune how eagerly the system nominates things;
this is a gate on the agent itself, and a gate the agent can open is
decoration. So it takes the same channel as the approved-id list:
configuration read at connect, and `epimemer agents require`. The
gate is one check at the tool boundary over every write tool that records a
claim. Turning it on affects only writes after that moment; nothing is
reinterpreted.

### 3.4 Immutable facts may be denormalised; mutable state may not

| On the row, inline | In the journal only |
|---|---|
| `judged_by`, which never changes | review state, which changes |

The inline fields are the original judge and nothing else. Nothing on a node
or an edge ever records that it was reviewed; §4 does, and derives it. Two
homes for one mutable fact, kept in sync across every backend, is the shape
this repository has hit too often to build again.

### 3.5 Linkage is inline, not an edge

Each decision record carries the ids as fields. A traversable `judged_by`
edge was rejected on a structural point: edges cannot originate from edges,
so similarity, assessed, contradiction and variant decisions, the ones review
most wants, would need the inline form regardless. The consequence is
accepted: *everything agent X decided* is a scan, and §4 makes it a
single-table one.

---

## 4. The decision journal

Attribution on the rows answers *who judged this node*. Review asks the
inverse, *what did this agent judge*, and over rows scattered across facts,
edges, lifecycle episodes and value signals that is five scans and a
reassembly. So every decision is also appended to a journal, and §3.4's
inline fields are the immutable denormalised copy.

A record is reviewed when another record's `reviews` names it, and that is
derived, never stored. A confirmation reviews without superseding, so
`reviews` and `supersedes` are separate fields; collapsing them breaks a
derived-only scheme, since a confirmation supersedes nothing. A reversal
appends a record with both set and deletes nothing.

**`kind` carries `because`.** A correction and a world-change are opposite
claims about what happened and a reviewer asking for one does not want the
other, so they are two kinds. **Every member of `DecisionKind` has a writer**,
enforced by a test that reads the whole package: review selects on kind, so an
unwritten kind is a filter that silently returns nothing and reads as a clean
graph.

**The journal write never raises**, except where the row is the act. It
lands after the decision and outside its transaction, which is the safe
direction: raising
would fail the tool call after the graph write succeeded, and the retry is
worse than the missing row every time (a retried `merge_facts` refuses
because its sources are retired, a retried `store_decomposition` ingests the
document twice). The failure is logged for the operator. The exceptions are
the `retention`, `boundary_declined` and `reopened` rows, where the row *is*
the act rather than a note about one.

**Timestamps are padded on write**, because `decided_at` is the one timestamp
whose index a range query actually uses, and wrapping the field in a
conversion costs 45× at 50,000 rows (`DEVELOPER_GUIDE.md`, *Comparing
timestamps*).

### 4.1 Granularity: one judgment, one row

An act is not always a call. One `store_decomposition` is one reading of one
document, so it is one row whatever the fact count; an archival sweep is one
row; a reactivation is one row; `apply_reflection`'s other lists get a row
each, because those are independent verdicts about unrelated nodes that
happen to be batched. The batching is the request's shape rather than the
judgment's.

The same coarseness applies to confirmation, and a confirmation names the
subjects it actually covers: one `reviews` pointer against an ingest record
would otherwise mark all forty-four facts reviewed when six were checked, and
confirmation is what stops the next reviewer looking. The alternative (a
record stays unreviewed until every subject is confirmed) requires the same
machinery plus bookkeeping.

### 4.2 Which record is primary

| Question | Primary |
|---|---|
| *what happened to this node* | `LifecycleEpisode`, via `query_changes` |
| *who decided it, how sure were they, has anyone checked* | `DecisionRecord` |

A status change with no judgment behind it is possible (`restore` after an
archival sweep); a judgment that changes no status is common (a declined
pair). Neither can be reconstructed from the other, so both stay. A row must
never claim to supersede a decision whose effect still stands, or the journal
disagrees with the graph; that is why a dissent (§6.4) sets only `reviews`.

---

## 5. Uncertainty: declared and derived, kept apart

Two sources of difficulty, never blended into one number.

**Declared.** The deciding agent supplies `certainty`, on the same ladder as
`confidence` (0.3, 0.5, 0.7, 0.9; absent means unrated, deliberately not a
rated 0.5), with a basis asked for whenever the value is not 0.5. Stated
once in the shared guidance. Only the review writers (`apply_review`,
`rejudge`) take one, since a declared judgment is the whole point of those
calls; every other writer leaves it blank rather than adding a rung to a
dozen tool schemas.

**Derived.** Properties of the decision, computable after the fact:

| Signal | Why it suggests difficulty |
|---|---|
| source `confidence` below 0.5 | the material was thin |
| a merge over three or more sources | more ways to be wrong |
| a contradiction with no resolution | left open, by decision or by neglect |
| a decision whose subject has since been retired | the ground moved under it |

**Unrated confidence is not a difficulty signal.** Absent is the ordinary
case and the majority state; treating absence as thinness would flood the
mode with ordinary decisions and assign a meaning to absence that nobody
asserted. Rows written before confidence became optional carry a literal
`0.5` and pass a below-0.5 filter as rated-ordinary; nothing can separate
them now.

**No similarity band.** *Just cleared the bar*, `[0.80, 0.85)`, would mint an
unnamed constant with no referent in the system, and the above-bar
distribution is unmeasured (the two above-bar scores on record sat above
such a band). Measuring it is the precondition for adding one.

---

## 6. The modes

`review(...)` is read-only, like `reflect`, and for the same reason: it
nominates, and every change goes through a writer. Three things are
separate: *which* decisions you are looking at, *what order* they arrive in,
and *whether* the list is narrowed further. `docs/ATTRIBUTION.md` has the
modes and filters as a caller sees them.

### 6.1 Modes: which decisions

**A mode names the selection; every argument narrows whatever it selected.**
`by_agent` and `since` are sugar over a required argument, and the refusal is
their whole value: `all` with an `agent_id` the caller forgot to pass returns
the entire journal and reads as an answer. There is no `between` mode, since
it is `since` with an `until`, and two names for one selection is two shapes
for one question.

### 6.2 Ordering: shakiest first, always

Every mode returns its results least-confident first. Nobody wants only the
doubtful decisions: a reviewer checking yesterday's work wants all of
yesterday, ordered so the doubtful calls are at the top, and stops reading
when it stops repaying attention. That is why *uncertain* is an ordering and
not a mode.

| Tier | Contains | Ordered by |
|---|---|---|
| 1 | decisions with a declared `certainty` | that value, ascending |
| 2 | decisions with none | the derived signals of §5, most signals first |

Tier 1 before tier 2, and that ordering is itself a rule: absence is not a
claim of doubt, so an unrated decision never sorts above one an agent
actually flagged. On a graph where nothing carries a certainty the order is
entirely derived and still works; as certainties accumulate, tier 1 fills
from the top. It also makes the cap benign: ordered worst-first, a cut list
loses the end nobody was going to read.

The read is linear in journal size, since a caller that re-sorts has to fetch
enough rows to sort; `decisions_scanned` is in every response so the cost is
visible, and `since` bounds it.

### 6.3 Filters: optional narrowing

`certainty_ceiling` is for counting rather than browsing, which ordering
covers: *"is anything below 0.5 still outstanding before I stop?"* is a gate,
and a gate wants a number. 0.5 is a legitimate constant where a similarity
band was not: it is a labelled anchor on the confidence ladder that tool
guidance teaches, so it means something before anybody measures anything.
Inclusive, because the guidance says to omit at 0.5 and an agent that typed
it anyway was making a point. Unrated decisions are excluded, since blank
cannot be distinguished from ordinary.

### 6.4 The response, and the writer

**Every mode is capped and reports `truncated`**, because `all` over an
append-only journal fed by every ingest is precisely the unbounded response
the nomination cap exists for.

**Every mode reports how many rows had no judge**, and only `by_agent`
excludes them, because it cannot answer without one. A blanket exclusion
would hide most of the corpus on exactly the graphs that chose not to require
a judge.

**Confirming costs something, or the treadmill moves up a level.** If agent 2
reviews a decision and agrees, and nothing records that, agent 3 does the
same work again. So confirmation writes a `DecisionRecord` with `reviews`
set, through `apply_review`, the only writer; `review()` stays read-only.

**A dissent records a finding and performs no undo.** Every undo in this
system already has a tool (`reverse_merge`, `restore`, `apply_reflection`
with `distinct`, `rejudge`, `reassign_metacontext`, `correct_interval`), each
with its own refusals and its own row that legitimately sets `supersedes`. A
dispatcher over them would be a convenience less safe than the sequence it
replaces. So a dissent sets only `reviews`, and its real use is where the undo
was refused: a merge whose survivor has since been contradicted cannot be
reversed (§7), and the dissent is where that finding goes.

**A retry must not read as a second opinion.** Two confirmations over one
decision is exactly the evidence a later reviewer weighs, so an identical
judgment by the same judge is refused naming the row that already says it,
while a different judge is the second independent check this design exists
for. The refusal is subject-scoped per §4.1. Two blank judges cannot be told
apart, so a retry on a graph that does not require one writes a second row;
one more thing `require_judge` buys.

`apply_review` is not one transaction: each entry is an independent judgment
about an unrelated decision, so refusals are per entry and one bad
`decision_id` does not lose the good ones. The reviewed set covers the whole
selection rather than the page, or every row past `max_results` would read as
unreviewed.

### 6.5 Every verdict needs a writer, including the ones review invents

*"Agent-1 called 44 facts `state`; two look like events"* is what review
recovers, and `rejudge` is what acts on it. Supersession
is not the missing path and must not be pressed into it: a mislabelled
`claim_kind` is neither *it was wrong* nor *the world changed*, and filing it
as a correction retires a true node and re-points its edges for a metadata
mistake.

`rejudge` revises an agent-supplied judgment about a node without touching the
claim: no status, edge or lineage moves, and the replaced values are kept on
the node's trail, because without a trail it would be the one call in the
system that destroys a judgment rather than superseding it. Restating a
judgment is a confirmation, not a rejudgment, and is pointed at
`apply_review`. The re-judgment is checkable against the material because the
segments are still stored.

**Scope, and what is deliberately elsewhere.** `importance` stays with
`judge_importance`, which already is this tool for one field. Metacontexts and
validity intervals have their own writers, `reassign_metacontext` and
`correct_interval`, and the split is about addressing rather than naming: `rejudge` takes a
`node_id` and promises no status, edge or lineage moves, while a metacontext
revision moves an edge and changes what merges, what corroborates and what a
scoped search returns, and an interval belongs to a `(node, source)` pair.
`reassign_metacontext` takes an optional `assign`, so moving a claim from
metacontext A to metacontext B never passes through the state of holding
none. A withdrawal that would leave a node with no metacontext is refused,
since such a node shares a metacontext with nothing and is never compared,
merged or returned by a scoped search. The withdrawal deletes the edge rather
than marking it, because every reader that derives metacontexts would have to
subtract a marker, and one that missed it would fail open.

### 6.6 Review is per graph, and says so

The journal is a per-graph table because `subject_ids` holds node ids, and a
node id resolves only in the graph that holds it. Where a write landed in the
wrong graph, its journal row landed there too, with the material it
describes, which is where somebody who found the material is already looking.

`review()` takes no `graphs=` list. A fan-out would borrow the active
database mid-call; doing it by hand (`list_graphs`, `use_graph`, `review()`,
per graph) is safer, and a convenience less safe than the sequence it
replaces is not a convenience. Every response names the graph it answered
from, so silent scoping becomes stated scoping.

**The `elsewhere` locator** is one count per other graph, zeros included, no
rows and no ids, so *there is more elsewhere* is something the reviewer is
told rather than has to think of. **A locator may overcount and must never
undercount**: only the judge and time filters are mirrored into the count, and
the response says which ran. A count too high costs a wasted look; a count too
low costs the look. The graph is the tag on a count read from it, never a field
stored on a row, which would be free to disagree with where the row actually
lives. Reading another graph on SurrealDB means borrowing the connection, so
`review` is in `MOVES_THE_GRAPH` and takes the graph guard's mover turn on
every backend: it excludes other tool calls for its duration and reads a
single instant in exchange. The SQLite, Postgres and in-memory backends read
another graph's own file, schema or store and borrow nothing, but take the
turn all the same.

---

## 7. Reversing a merge

The information a reversal needs is destroyed at merge time and is not
reconstructible afterwards, so the decision was never *build undo or not*
but *capture or lose*, made before the next merge rather than before the
undo.

### 7.1 What a merge destroys

`merge_nodes` migrates every knowledge edge onto the survivor, collapsing
duplicates by `(src, dst, type)`. Nothing else records which source each
migrated edge came from, and where two sources cited the same document the
two `sourced_from` edges genuinely collapse into one. `metadata.merged_from`
names the nodes that merged, not their edges.

### 7.2 Where merge information lives

What grows is the chain: `A+B→S1`, then `S1+C→S2`, so unwinding `S2` back to
`A, B, C` needs both partitions. Depth is a property of the lineage, not of
any one node's list.

### 7.3 The split: audit in the journal, payload on the node

The audit (who, when, why, how certain) is small and permanent, and lives in
the `DecisionRecord`. The payload (the pre-merge edge partition, about 1.6 KB
per merge measured) lives on the survivor, bounded by §7.4. On the node
rather than in a global ring because a working session touches distinct
survivors, one entry each, so under a per-node bound nothing evicts and the
whole session stays reversible, where a global ring of the same nominal size
would throw away its tail. It also settles the archival interaction: archive
the survivor and its payload goes with it, no undo buffer pinning nodes
against the graph's own cleanup.

### 7.4 The bound

`merge_undo_depth`, default 10, a per-graph setting (`configure_merge`),
bounds how far back along the `merged_into` chain partitions are retained.
Eviction runs after the merge transaction, so a merge that fails evicts
nothing, and it is idempotent.

Ten, because the bound targets the single claim that keeps absorbing
restatements (document 3's phrasing into the survivor, then document 4's,
then document 5's), whose chain would otherwise grow without limit. It is not
about storage: a merge already retains about 4 KB of husk and vector per
source for ever, more than the payload it adds, and sources are retired, not
deleted. The payload is stored whole rather than with `exclude_defaults`,
because an omitted field would be re-supplied from today's default when
replayed, and a default changed next year would silently alter a replay of an
old merge.

What eviction discards is reversal capability, never a claim: every merged
source node, its content, its provenance, its lifecycle episode and its
`merged_into` edge remain. The graph forgets how to replay an edge migration
automatically. It forgets nothing it knows. This is the one structure in the
system that deliberately forgets, and that is why it is said plainly.

### 7.5 The guard that is not a setting

Depth bounds *how far back*; it says nothing about *whether it is safe*.
Reversal is refused, with a reason, when the survivor has itself been merged
again or superseded, or when it **carries any edge that is neither in the
undo payload nor written by the merge itself**. That second check is a set
difference, not a list of edge types: reversal ends in a hard delete, so an
edge the guard does not notice is an edge the reversal destroys, and a
contested claim losing its contest record is precisely the loss *nothing is
destroyed* exists to prevent. Stated as a difference, an edge type invented
next year is refused by default rather than deleted by omission. No
configured value raises past this.

### 7.6 What a reversal restores

Reversing returns the graph to the state it had before the merge, and
reversing back and forth N times is indistinguishable from doing it once. The
reversal's own journal row carries the survivor's content, so the withdrawn
wording stays quotable after the node holding it is gone.

No new flag is raised on the dependents: the merge re-pointed each
dependent's `derived_from` onto S and flagged it `evidence_merged`; reversal
re-points it back to the premise it was actually drawn from, so there is
nothing to re-read, and a flag would assert a change the reversal has just
undone. The one place exactness does not hold: status is restored, history is
appended. A merge/reverse cycle leaves a closed episode behind and the
journal keeps both decisions.

### 7.7 The survivor is deleted, and a node delete is never exposed

Reversal deletes S rather than retiring it, for two reasons, the second
stronger. Exactness: retiring leaves one husk per cycle, each keeping its own
vector, visible to `include_corrected` searches and archival nomination, so N
cycles would stop equalling one. And a later re-merge must synthesise afresh
from what is known then; resurrecting the old S would import a previous
agent's wording into a decision nobody made with it, which also rules out
reusing a retired survivor on re-merge. Nothing knowable is lost: S's
content is `extraction_method: "agent:merge"`, a synthesis sourced from no
document, and the merge stays in the journal.

**There is no `delete_node` method.** The deletion lives inside
`reverse_merge_tx`, on the protocol and every backend, with the never-expose
note on each. Atomicity: a standalone delete after the reversal
transaction would put the one irreversible step outside the rollback. And the
safest way to never expose a hard delete is not to have one: a public method
guarded by a comment is a capability plus a request not to use it. Every
backend refuses a survivor that still has edges rather than dropping them, so
a guard bug fails loudly inside a transaction that rolls back. The
`EmbeddingRecord` goes with the node, because the vector is stored per item
and deleting the node alone would strand an entry the index still returns.

### 7.8 Futile cycles

A merge reversed, re-made, reversed again is an agent burning tokens on an
oscillation nobody wants. The signal already exists: every merge appends a
`LifecycleEpisode` with `because: MERGED`, and every reversal closes it with
`restored_at`, so one completed cycle leaves one closed `merged` episode on
each source, in a list that is never trimmed.

Counted per node, not per pair, because pair matching would miss `A+B`,
then `A+C`, then `A+D`. Free at the point of use, since `merge_facts`
already loads every source. `merge_cycle_limit`, default 2, a per-graph
setting: one merge-then-reverse is an ordinary correction, two can be two
judges disagreeing, the third attempt is oscillation and is refused,
ordered after the permanent refusals (cross-metacontext, event) and
before the similarity bar, since it is fixable by a human decision. The
refusal says the limit is configurable, so the setting has to be real or a
legitimate third merge is blocked with no recourse. Refusal rather than a
warning, because a warning is something an agent reads and proceeds past.
Accepted gap: an agent could evade the check by merging a different source
set; a system that tried to detect deliberate evasion here would be solving a
problem nobody has.

### 7.9 The payload

Each moved edge is stored whole, as values rather than references, since the
original rows may no longer exist, and never as a hand-listed subset, which
drops whatever it forgets to name: leaving out `metadata` would strip
`judged_by` from every edge a reversal replays. It lives in the survivor's
`metadata` rather than a typed field, because `Topic`, `Fact` and `Inference`
share no base class and merge is generic over all three.

---

## 8. What this deliberately does not do

- **It does not verify anybody** (§2.4). Descriptions are self-reported and
  the system says so wherever it shows one.
- **It does not weight anything by judge.** *"Score facts higher when a
  trusted agent judged them"* is the natural next request and is refused:
  the input is self-reported prose, so a ranking built on it is one any agent
  can move by describing itself differently. Corroboration counts publishers
  because a publisher is a property of the document; a judge is a property
  of the claimant.
- **It does not re-open applied changes as a matter of course.** Review
  nominates; reversing is a separate, explicit act (§7), bounded by depth and
  refused where something has come to depend on the result.
- **It does not backfill** (§3.3).
- **It does not run on its own.** A review nobody asked for, over a graph
  nobody was looking at, is consolidation by timer.

---

## 9. Warnings are decision records

"I was warned and proceeded anyway" is a judgment with a judge, a date and a
subject, so it is a `DecisionRecord(kind="proceeded_despite_warning")` and
nothing else: no per-node note list, no second *what has nobody looked at*
scan, no `reviewed_at`. A node's notes are a derived view over records whose
`subject_ids` contain it, and the contested-decisions worklist is
`review(mode="warning")`. Two review-state machines would both be written
to by an agent proceeding past a warning, which is two shapes for one
question (`WARNINGS_AND_SETTINGS.md`).

---

## 11. Rejected

- **One user-assigned string as id, name and handle.** A name could never be
  corrected, and one character's difference made a second judge with a
  permanently separate history.
- **Session-per-mint.** Makes self-review impossible by construction, and
  fragments one judge across sessions.
- **A mandatory judge from a fixed release, with blank meaning legacy**
  (§3.3). It bought one meaning by making the field mandatory for ever, a
  large permanent cost to date-stamp a population that needed no date.
