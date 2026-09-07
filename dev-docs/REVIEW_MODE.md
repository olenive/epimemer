# Review mode: who judged this, and can someone else check it

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
`WARNINGS_AND_SETTINGS.md` (advisories, whose journal rows this reads).

---

## 1. Recording a decline

### 1.1 Every verdict has a writer

`reflect` nominates pairs and the agent classifies each one. Every verdict in
`REVIEW_EPISTEMIC.md` §3 has an action, and the last to get one was
`compatible`: `apply_reflection(similarities=[…])`. Before it existed, a
declined pair left no record, so the sweep re-nominated it on every reflect;
on one real graph thirteen of the first eighteen nominated pairs were
declined and returned every time.

### 1.2 A decline is two populations, not one

A single `similarity` edge for every decline would serve two readers that
want opposite breadth. The nomination sweep's `already_linked` wants it
broad: suppress every pair anybody assessed. `corroboration.py` wants it
narrow: its neighbourhood is restatements of one claim, and a wrong
`similarity` edge overstates the count. Write one edge for both and *"these
two are different claims"* starts corroborating, which is manufactured
support, the worst failure this system can produce: a false merge does not
lose information, it inverts the quantity corroboration measures.

| Why the agent declined | It is | Records |
|---|---|---|
| `redundant`, but `merge_facts` refused (an event, or an unjudged `claim_kind`) | genuinely one claim | `similarity` **and** `assessed` |
| `compatible`: different claims that merely look alike | not one claim | `assessed` only |

The other refusals need no new action: a cross-frame pair is
`record_variant`, and a retired twin is `recurs` and `restore`.

`assessed` is a denormalised suppression index and passes §3.4's rule: it is
immutable and append-only, so it cannot drift from the journal that also
records the decision. `already_linked` reads
`SIMILARITY ∪ CONTRADICTION ∪ VARIANT_OF ∪ ASSESSED`.

### 1.3 The action

```python
similarities = [
    {
        "pair": [a_id, b_id],
        "verdict": "one_claim",
        "because": "same claim; merge refused, both are events",
    },  # similarity + assessed
    {
        "pair": [c_id, d_id],
        "verdict": "distinct",
        "because": "both about validity intervals, different assertions",
    },  # assessed only
]
```

The agent is already in `apply_reflection` having made every other decision;
declining becomes an outcome applied in the same batch rather than a
separate errand. Rules that decide behaviour:

- **Both sides must be in `NOMINATED_STATUSES`** (`ACTIVE`, `HISTORICAL`).
  An `assessed` edge earns its place by suppressing a nomination, so it
  belongs exactly where a nomination could have happened; `CORRECTED`,
  `ARCHIVED` and `MERGED` are refused because a judgment there suppresses
  nothing and a `similarity` edge would still be counted as support.
- **`one_claim` is refused across frames.** A `similarity` edge across frames
  is a fiction corroborating a fact. `distinct` across frames is accepted,
  since `assessed` corroborates nothing.
- **`distinct` over a standing `one_claim` is a retraction.** It writes a
  `retracted_similarity` edge that disqualifies the standing one, the
  mechanism corroboration already runs for `contradiction`; the `similarity`
  edge is not deleted. The other direction is refused: nothing re-asserts
  `one_claim` over a withdrawal, because withholding support costs a count
  while inventing it inverts the quantity. `one_claim` after `distinct` on a
  pair that was never `one_claim` is additive.
- **Similarities are applied first in `apply_reflection`**, before any
  argument that can retire a node: a judgment is about the wording it was
  made against, and a supersession later in the same batch would otherwise
  turn it into a skip.
- **Judgment edges never migrate on retirement.** `similarity`,
  `contradiction` and `variant_of` are in `JUDGMENT_EDGE_TYPES`, and
  `migration_disposition` anchors them on every retirement, correction and
  merge included; `assessed` gets the same anchoring through
  `REVIEW_EDGE_TYPES`. The replacement starts with no judgments and is
  correctly re-nominated (`REVIEW_EPISTEMIC.md` §4.2).
- **The edges record a judgment.** A sweep that wrote them for every pair
  over the bar would fill the graph with assertions nobody made and suppress
  its own future nominations. Similarity nominates; the agent judges.

The frontend draws `assessed` as the similarity hue drained of saturation:
same subject, no assertion of support.

---

## 2. The registry: what an agent is

### 2.1 Identity is minted, not derived

Hashing the self-description into an id fails in both directions: reword the
description and become a different judge, so one judge's history fragments;
paste the same description and two genuinely different agents are
indistinguishable. So identity has three layers:

```python
class AgentDescription(BaseModel):
    """One thing an agent said about itself, and when it said it. Append-only."""

    digest: str  # sha256 of `text`, truncated: identifies the *version*
    text: str
    recorded_at: datetime
    # Set only through a channel that terminates at the user (§2.3). `None`
    # is *self-described, unconfirmed*, a different epistemic object.
    confirmed_at: datetime | None = None


class Agent(BaseModel):
    """A judge: something that made decisions in this graph. Not a user
    account and not a credential (§2.4)."""

    id: str  # opaque, never displayed, minted by `new_agent_id()`
    name: str  # freely renamable, resolved at read time, unique per graph
    former_ids: list[str]  # keys this judge's rows may already record
    descriptions: list[AgentDescription]
    authorised_at: datetime
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
```

A decision records a `JudgeRef`, the pair `(agent_id, digest)`: the id says
who, the digest pins what the judge claimed to be at the moment it decided,
with no as-of query needed. The name and the description resolve by opposite
rules on purpose: *which judge is this* wants the name the user knows it by
now, so a rename carries old rows with it; *what did this judge claim to be
when it decided this* wants the claim as it stood.

`former_ids` is aliasing, migration and repair in one list. Consolidating two
records that were always one judge rewrites nothing and deletes nothing: the
survivor takes the other's keys and both description histories, and old
journal rows keep the key they were written with. An absorbed record stops
being live, derived by `live_agents` rather than stored as a flag.
`judge_aliases` is the one place *which judge did the caller mean* is
answered, and `query_decisions` takes `agent_ids` rather than one id because
after a consolidation a judge is a set of keys.

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

Two states are distinct: **declined** (the question reached a person and they
said no) refuses even a pre-approved id; **unavailable** (no elicitation
channel exists) falls back to the approved list, since that approval is the
user's involvement happening earlier rather than not at all.

**Renaming lives on the same channel**, the elicitation prompt and the CLI,
because a handle an agent could rename is a handle an agent could point at
another judge's history. A name collision is a question, not a refusal: two
records that should be one is the commonest reason to be renaming, so the
collision asks whether they are the same judge, and yes consolidates. The CLI
answers it with `--same-judge`. Typing the name of an existing judge on the
free-text path joins it rather than minting a second record.

**The user owns the semantics.** Whether ids track a model, a role or a task
is the user's scheme. The server can detect a new session or a different
client (`ctx.session_id`, `ctx.client_id`); it can never detect a different
model inside one harness, because the model is never on the wire. That
question is the user's to answer, which is what the flow assumes.

### 2.3 Confirmation reaches the user, not the agent

`ctx.elicit` inverts the direction of an MCP call: the server asks, and the
answer comes back from the user through the client's own UI. Where the
client cannot elicit, `epimemer agents confirm <id>` is a command the agent
cannot run, and `EPIMEMER_APPROVED_AGENTS` seeds approval at connect for
embedded stores the CLI cannot reach (§10.3). No path exists by which the
agent alone sets `confirmed_at`; a `confirm_agent` tool the agent could call
is refused on principle. `epimemer agents confirm` stamps the current
description too, since the user vouches for the wording in front of them, and
`agents list` sits beside it because approving an id blind is the same gap in
miniature.

A tool that waits on a person cannot share the tool timeout: `claim_agent`
sets `waits_for_user` on `_run_with_timeout`, or *the user was still reading*
would read as *the client cannot elicit*, the one direction this must not
fail in.

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

---

## 3. Attribution: everywhere, including ingest

### 3.1 Scope

Every write path carries a judge, ingest included. Ingest is where the
unreviewable judgments live: `claim_kind`, `confidence` and `importance` are
supplied by the agent that read the material, frozen at that moment, and
re-examined by nothing.

| Decision | Recorded on |
|---|---|
| `claim_kind`, `confidence` and basis at ingest | the node (`judged_by`), `ValueSignal`, `metadata` |
| similarity, assessed, contradiction, variant, link | `NodeEdge.judged_by`, a field rather than a `metadata` key |
| retire and restore | `LifecycleEpisode.retired_by` and `restored_by`, two judges because they are two decisions often months and sometimes two agents apart |
| `judge_importance` | `ValueSignal.importance_judged_by` (the latest) and each `reinforcements` entry (its own), because three judgments by three agents compose into one number and the trail is the only place they stay separable |
| synthesised topics, splits, enrichments, merge survivors | the node |

A correction does not inherit the previous node's judge: the replacement is
this agent's wording. Reusing an entity or tag topic does not restamp it, and
re-recording a pair that already has its edge returns `created: False` and
leaves the judge alone: a second agent calling the same tool has confirmed,
which is a review with a record of its own (§6.4), not an overwrite. The
document and its segments carry no judge: *who pasted this text* is a
different question from *who judged what it says*. Approval is re-checked on
every write, and a revoked id records as unknown rather than raising.

### 3.2 Threading it, without a singleton

The obvious implementation is an ambient "current agent". That is a
singleton and this project does not have those. `storage` is already passed
explicitly into every tool; `claim_agent` binds the session; the resolved
judge rides down as one more explicit parameter, `judge: JudgeRef | None`,
resolved once at the tool boundary and never a module global. Whether a
blank is accepted is the graph's policy, not the signature's (§3.3). Where a
transport has no session concept at all, a fallback binding held on the
lifespan is used, per-server state reached through `ctx.lifespan_context`,
which a successful session binding clears. Ingest stamps its edges once,
after the batch is assembled, rather than at each place it builds them.

### 3.3 What absence means

**Blank means unknown. That is the whole of it.** It carries no date and
asserts nothing about why nobody is named. Reading anything more into it
(*written before attribution existed*, say) would give a blank a meaning
nobody asserted, which is the mistake this project made with a literal `0.5`
confidence and stores unrated as absent to avoid.

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

`require_judge`, one per-graph setting stored beside the reflect counter and
the merge overrides:

| | |
|---|---|
| **Off (default)** | a write may carry a judge or not; blank is recorded as unknown |
| **On** | a write without a judge is refused, with prose naming `claim_agent` |

It is not an MCP tool. `configure_reflection` and `configure_merge` are
agent-callable because they tune how eagerly the system nominates things;
this is a gate on the agent itself, and a gate the agent can open is
decoration. So it takes the same channel as the approved-id list:
`EPIMEMER_REQUIRE_JUDGE` read at connect, and `epimemer agents require`. The
gate is one check at the tool boundary over every write tool that records a
claim; timelines and metacontexts are scaffolding rather than claims and sit
outside it. Turning it on affects only writes after that moment; nothing is
reinterpreted. `epimemer agents require on` warns when no id is approved yet,
since it is the one setting that can make a working graph refuse everything.

### 3.4 Immutable facts may be denormalised; mutable state may not

| On the row, inline | In the journal only |
|---|---|
| `judged_by`, which never changes | review state, which changes |

The inline fields are the original judge and nothing else. Nothing on a node
or an edge ever records that it was reviewed; §4 does, and derives it. Two
homes for one mutable fact, kept in sync across two backends, is the shape
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

```python
class DecisionRecord(BaseModel):
    """One judgment, as an append-only row. Never edited, with no exceptions."""

    id: str
    kind: DecisionKind
    subject_ids: list[str]
    covers: list[str]  # the reasons a retention answers; empty means kept for its own sake
    judged_by: JudgeRef | None  # absent means unknown (§3.3)
    decided_at: datetime
    certainty: float | None  # §5; bounded 0.0–1.0 because the ordering sorts on it
    certainty_basis: str | None
    reviews: str | None  # the record this one is about
    supersedes: str | None
    frame: str | None
```

`reviewed` is `EXISTS(record WHERE reviews = this.id)`, and `unreviewed` is
its complement. A confirmation reviews without superseding, so `reviews` and
`supersedes` are separate fields; collapsing them breaks a derived-only
scheme, since a confirmation supersedes nothing. A reversal appends a record
with both set and deletes nothing.

**`kind` carries `because`.** A correction and a world-change are opposite
claims about what happened and a reviewer asking for one does not want the
other, so they are two kinds, `supersession_kind(status)` being their single
declaration. **Every member of `DecisionKind` has a writer**, enforced by a
test that reads the whole package: review selects on kind, so an unwritten
kind is a filter that silently returns nothing and reads as a clean graph.

**The journal write never raises**, with one exception. It lands after the
decision and outside its transaction, which is the safe direction: raising
would fail the tool call after the graph write succeeded, and the retry is
worse than the missing row every time (a retried `merge_facts` refuses
because its sources are retired, a retried `store_decomposition` ingests the
document twice). The failure is logged for the operator. The exception is
the `retention` row, where the row *is* the act rather than a note about one
(`epimemer/pipelines/reflection/retention.py`).

**Timestamps are padded on write.** `decided_at` is the one timestamp with
an index a range actually uses, and wrapping the field in a conversion costs
45× at 50,000 rows, so the writer renders one canonical shape
(`DEVELOPER_GUIDE.md`, *Comparing timestamps*).

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

Re-recording a pair verdict (`record_contradiction`, `record_variant`, a
relation verdict) on a pair that already carries the edge writes a
confirmation row whose `reviews` names the oldest record for that pair.
Where the verdict predates the journal the pointer is blank, because the
journal cannot cite a row that does not exist.

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

The merge subject convention is a trap: `merge_facts` journals `[survivor,
*sources]`, so *three or more sources* read off `len(subject_ids)` calls
every two-source merge wide. It has a named function and a test.

---

## 6. The modes

`review(...)` is read-only, like `reflect`, and for the same reason: it
nominates, and every change goes through a writer. Three things are
separate: *which* decisions you are looking at, *what order* they arrive in,
and *whether* the list is narrowed further.

### 6.1 Modes: which decisions

| Mode | Selects | Answers |
|---|---|---|
| `by_agent` | `judged_by` among a judge's keys | *"check everything this judge did"* |
| `since` | `decided_at` in range | *"review yesterday's session"* |
| `unreviewed` | no record `reviews` this one | *"what has nobody looked at"* |
| `advisory` | `kind` is `proceeded_despite_advisory` | the contested-decisions worklist |
| `all` | every record | the full audit |

**A mode names the selection; every argument narrows whatever it selected.**
`agent_id`, `since`, `until` and `certainty_ceiling` work under every mode,
so `mode="unreviewed", agent_id=…, since=…` is one call. `by_agent` and
`since` are sugar over a required argument, and the refusal is their whole
value: `all` with an `agent_id` the caller forgot to pass returns the entire
journal and reads as an answer. There is no `between` mode, since it is
`since` with an `until`, and two names for one selection is two shapes for
one question. `mode` is validated against the modes that exist and refuses
by name.

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

`certainty_ceiling`, off by default, keeps only decisions whose declared
certainty is at or below it; unrated decisions are excluded, since blank
cannot be distinguished from ordinary. Its use is not browsing, which
ordering covers, but counting: *"is anything below 0.5 still outstanding
before I stop?"* is a gate, and a gate wants a number. 0.5 is a legitimate
constant where a similarity band was not: it is a labelled anchor on the
confidence ladder that tool guidance teaches, so it means something before
anybody measures anything. Inclusive, because the guidance says to omit at
0.5 and an agent that typed it anyway was making a point.

The response names the ceiling this call used, never asserting what the
graph would have done, because a caller can pass their own. Always reported:
how many decisions were unrated, since three results out of four hundred
blanks is not the same answer as three out of four.

### 6.4 The response, and the writer

**Every mode is capped and reports `truncated`.** `all` over an append-only
journal fed by every ingest is precisely the unbounded response the
nomination cap exists for. When a list is named in `truncated`, act on what
came back and review again rather than raising the number.

**Every mode reports how many rows had no judge**, and only `by_agent`
excludes them, because it cannot answer without one. A blanket exclusion
would hide most of the corpus on exactly the graphs that chose not to require
a judge.

**Confirming costs something, or the treadmill moves up a level.** If agent 2
reviews a decision and agrees, and nothing records that, agent 3 does the
same work again. So confirmation writes a `DecisionRecord` with `reviews`
set, through `apply_review(confirmations=[…], dissents=[…])`, the only
writer; `review()` stays read-only.

**A dissent records a finding and performs no undo.** Every undo in this
system already has a tool (`reverse_merge`, `restore`, `apply_reflection`
with `distinct`, `rejudge`, `reframe`, `correct_interval`), each with its own
refusals and its own row that legitimately sets `supersedes`. A dispatcher
over them would be a convenience less safe than the sequence it replaces. So
a dissent sets only `reviews`, and its real use is where the undo was
refused: a merge whose survivor has since been contradicted cannot be
reversed (§7), and before the dissent existed there was nowhere to put that
finding.

**A retry must not read as a second opinion.** Two confirmations over one
decision is exactly the evidence a later reviewer weighs, so an identical
judgment by the same judge is refused naming the row that already says it,
while a different judge is the second independent check this design exists
for. The refusal is subject-scoped per §4.1. Two blank judges cannot be told
apart, so a retry on a graph that does not require one writes a second row;
one more thing `require_judge` buys.

`apply_review` is not one transaction: each entry is an independent judgment
about an unrelated decision, per-entry refusals, and one bad `decision_id`
does not lose the good ones. The reviewed set covers the whole selection
rather than the page, or every row past `max_results` would read as
unreviewed; `unreviewed` filters before ordering, and `unreviewed_count` is
over what was selected.

### 6.5 Every verdict needs a writer, including the ones review invents

*"Agent-1 called 44 facts `state`; two look like events"* is what review
recovers, and nothing could act on it until `rejudge` existed. Supersession
is not the missing path and must not be pressed into it: a mislabelled
`claim_kind` is neither *it was wrong* nor *the world changed*, and filing it
as a correction retires a true node and re-points its edges for a metadata
mistake.

`rejudge(node_id, because, claim_kind=, confidence=, confidence_basis=,
certainty=, certainty_basis=)` revises an agent-supplied judgment about a
node without touching the claim: no status, edge or lineage moves, a
`DecisionRecord` whose `reviews` points at the record that made the original
judgment, and the replaced values appended to the node's `rejudgments` as
`{because, was, now, judged_by}`, because without a trail it would be the one
call in the system that destroys a judgment rather than superseding it.
Restating a judgment is a confirmation, not a rejudgment: a call where every
value supplied is what the node already carries is refused and pointed at
`apply_review`. The re-judgment is checkable against the material because
the segments are still stored.

**Scope, and what is deliberately elsewhere.** `importance` stays with
`judge_importance`, which already is this tool for one field. Frames and
validity intervals have their own writers, `reframe` and `correct_interval`,
and the split is about addressing rather than naming: `rejudge` takes a
`node_id` and promises no status, edge or lineage moves, while a frame
revision moves an edge and changes what merges, what corroborates and what a
scoped search returns, and an interval belongs to a `(node, source)` pair.
`reframe` takes an optional `assign`, so moving a claim from frame A to frame
B never passes through frameless; withdrawing a node's last frame is a
promotion taken with `to_base_reality=True` as an acknowledgment, because a
flat refusal would have left the tool unable to fix the case it was built
for. A refusal that blocks the motivating example is a design error, not a
safety feature.

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
told rather than has to think of. Reading another graph on SurrealDB means
borrowing the connection, which takes the graph guard's mover turn, so
`review` is in `MOVES_THE_GRAPH`: a read that declares itself a mover,
excludes other tool calls for its duration, and reads a single instant in
exchange. **A locator may overcount and must never undercount**: only the
filters `query_decisions` implements are mirrored into the sweep (`agent_id`,
`since`, `until`), `certainty_ceiling` and `mode="unreviewed"` are not, and
`elsewhere.counted_with` says which ran. A count too high costs a wasted
look; a count too low costs the look. The graph is the tag on a count read
from it, never a field stored on a row, which would be free to disagree with
where the row actually lives.

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

| Where | What |
|---|---|
| survivor node, `metadata.merged_from` | the ids that merged into this survivor, set once at construction |
| survivor node, `metadata.merge_undo` | the pre-merge edge partition (§7.3), bounded (§7.4) |
| each retired source, `lifecycle` | `{because: merged, counterpart: <survivor>, retired_at, restored_at}` |
| graph edges | `merged_into` (source → survivor), `evidence_merged` (to dependents) |
| the journal | who merged, when, why, how certain |

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
On each merge, the chain is walked back from the new survivor and
`merge_undo` cleared on any ancestor deeper than the limit. Eviction runs
after the transaction, so a merge that fails evicts nothing, and it is
idempotent.

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
reversing back and forth N times is indistinguishable from doing it once.

| The merge did | Reversal does |
|---|---|
| moved A and B's knowledge edges onto S | replays the captured partition, splitting an edge that collapsed when both cited one document |
| A, B → `MERGED` | → `ACTIVE` |
| deleted an edge between A and B outright (a self-loop after migration) | recreates it (`intra_set`) |
| wrote `merged_into` A→S, B→S and `evidence_merged` on dependents | deletes them |
| appended a lifecycle episode to A and B | closes it with `restored_at` and `restored_by` |
| created survivor S | deletes it, and its `EmbeddingRecord` (§7.7) |
| | appends a reversal `DecisionRecord` with `reviews` and `supersedes` set, carrying `survivor_content` so the withdrawn wording stays quotable |

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
`reverse_merge_tx`, on the protocol and both backends, with the never-expose
note on all three. Atomicity: a standalone delete after the reversal
transaction would put the one irreversible step outside the rollback. And the
safest way to never expose a hard delete is not to have one: a public method
guarded by a comment is a capability plus a request not to use it. Both
backends refuse a survivor that still has edges rather than dropping them, so
a guard bug fails loudly inside a transaction that rolls back. The
`EmbeddingRecord` goes with the node, because the vector is stored per item
and deleting the node alone would strand an entry the index still returns.

### 7.8 Futile cycles

A merge reversed, re-made, reversed again is an agent burning tokens on an
oscillation nobody wants. The signal already exists: every merge appends a
`LifecycleEpisode` with `because: MERGED`, and every reversal closes it with
`restored_at`, so one completed cycle leaves one closed `merged` episode on
each source, in a list that is never trimmed.

```python
def completed_merge_cycles(node: EpistemicNode) -> int:
    return sum(
        1
        for episode in node.lifecycle
        if episode.because is NodeStatus.MERGED and episode.restored_at is not None
    )
```

Counted per node, not per pair, because pair matching would miss `A+B`,
then `A+C`, then `A+D`. Free at the point of use, since `merge_facts`
already loads every source. `merge_cycle_limit`, default 2, a per-graph
setting: one merge-then-reverse is an ordinary correction, two can be two
judges disagreeing, the third attempt is oscillation and `merge_refusal`
refuses it, ordered after the permanent refusals (cross-frame, event) and
before the similarity bar, since it is fixable by a human decision. The
refusal says the limit is configurable, so the setting has to be real or a
legitimate third merge is blocked with no recourse. Refusal rather than a
warning, because a warning is something an agent reads and proceeds past.
Accepted gap: an agent could evade the check by merging a different source
set; a system that tried to detect deliberate evasion here would be solving a
problem nobody has.

### 7.9 The mechanics

**The payload type.** Edge values, not references, since the original rows
may no longer exist:

```python
class MergedEdge(BaseModel):
    """One edge exactly as it stood before the merge moved it. The whole
    edge, `edge.model_dump(exclude={"id"})`, never a hand-listed subset."""

    owner_id: str
    edge: dict
    intra_set: bool = False


class MergeUndo(BaseModel):
    source_ids: list[str]
    edges: list[MergedEdge]
    merged_at: datetime
    decision_id: str | None = None
    survivor_content: str = ""
```

The whole edge, because a hand-listed subset omitted `metadata` and
`created_at` in a first draft and would have stripped `judged_by` from every
edge it replayed. A partial copy of a model is a bug with a delay on it.

**Where it lives.** `metadata["merge_undo"]`, parsed through `MergeUndo`.
`Topic`, `Fact` and `Inference` share no base class, so a typed field would
be added three times for a payload that only exists on merge survivors, and
`merge_nodes` is generic over `EpistemicNode`, so merged topics get the same
treatment.

**Capture point.** `merge_nodes` in `pipelines/graph_construction/versioning.py`
reads each source's edges before migration (two reads per source,
pre-transaction), builds `MergeUndo`, and lets `merge_nodes_tx` persist it in
the same transaction as the merge. Judgment edges are skipped by the
migration loop before it reaches the self-loop branch and survive intact on
the sources, so `intra_set` covers the rest: a user `related` edge, a
`supports` edge between two merging facts.

**The protocol.** `reverse_merge_tx(survivor, source_nodes, restored_edges, *,
restored_at, delete_edge_ids)` on both backends applies a plan; `reverse_merge`
in `versioning.py` builds it, runs the guard, replays the payload and
collects the edges to delete, so the two backends cannot develop different
opinions about what a reversal means. The refusal for a survivor with no
`merge_undo` says whether the payload aged past `merge_undo_depth` or the
node was never a merge survivor, since one is permanent and the other is a
mistake.

**Tests**, `tests/pipelines/test_merge_reversal.py` and
`test_merge_undo_capture.py`: merge then reverse restores every source with
its original edges, including two sources citing one document; two cycles
leave the active graph identical to one and `lifecycle` two episodes longer;
the survivor is gone from `get_node` and from search on both backends; each
guard refuses with a distinguishable reason; an evicted payload refuses
differently from a node that never had one; the third merge of an
oscillating pair refuses on `merge_cycle_limit` and raising the setting lets
it through; an intra-set edge survives the cycle; edge `metadata` and
`created_at` survive, `judged_by` specifically; reversal refuses when S
carries a post-merge contradiction, assessed verdict, tag or user edge; the
survivor's embedding is gone; a partial failure mid-transaction leaves the
graph as it was.

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

## 9. Advisories are decision records

"I was warned and proceeded anyway" is a judgment with a judge, a date and a
subject, so it is a `DecisionRecord(kind="proceeded_despite_advisory")` and
nothing else: no per-node note list, no second *what has nobody looked at*
scan, no `reviewed_at`. A node's notes are a derived view over records whose
`subject_ids` contain it, and the contested-decisions worklist is
`review(mode="advisory")`. Two review-state machines would both be written
to by an agent proceeding past an advisory, which is two shapes for one
question (`WARNINGS_AND_SETTINGS.md`).

---

## 10. Where the mechanics live

### 10.1 Merge capture, cycle limit, reversal

§7.9. `merge_nodes` captures; `merge_refusal` in
`pipelines/reflection/fact_dedup.py` counts cycles; `reverse_merge` and
`reverse_merge_tx` reverse; `configure_merge` holds `merge_undo_depth` and
`merge_cycle_limit` as per-graph overrides with injectable defaults
(`DEFAULT_MERGE_UNDO_DEPTH`, `DEFAULT_MERGE_CYCLE_LIMIT`).

### 10.2 Similarities and the `assessed` edge

`EdgeType.ASSESSED` in `REVIEW_EDGE_TYPES`;
`pipelines/reflection/similarity_decisions.py`;
`apply_reflection(similarities=[…])`; `ALREADY_JUDGED_EDGE_TYPES` in
`contradiction_detection`; `JUDGMENT_EDGE_TYPES` consulted by
`migration_disposition` before the status branch; `retracted_similarity` in
`DISQUALIFYING_EDGE_TYPES`, read by corroboration. Corroboration reads
`SIMILARITY` only, with a test that an `assessed`-only pair does not
corroborate.

### 10.3 The registry and its approval channels

`Agent`, `AgentDescription`, `JudgeRef` in `core/types.py`; the `agent`
table and the `approved_agent_ids` graph-state field on both backends;
`claim_agent` over elicitation; `EPIMEMER_APPROVED_AGENTS`; the `epimemer`
CLI (`agents confirm`, `agents list`, `agents rename`, `agents require`).

**Config seeding runs on every graph the server lands on**, not only at
connect. Approval is per graph, so connect-time seeding alone would leave
every other graph unapprovable on an embedded backend, one `use_graph` later.
Seeding is applied before the judge is re-checked, or configuration would
clear a judge it was about to admit. `use_graph` re-validates the judge, and
a claim made where no session exists is reported as unbound
(`session_bound`) rather than raised, because a graph switch must not fail
over an identity feature the caller never used. *Unreachable by the agent*
and *unreachable by the user* are different failures, and the CLI's refusal
against an embedded store is action-specific, since two settings live behind
that wall with different environment variables.

### 10.4 Threading the judge

`JudgeRef` reaches every write tool that records a claim, the storage
transactions on both backends, and four carriers: `LifecycleEpisode`,
`NodeEdge`, `ValueSignal` and the node types (§3.1). One nested pair rather
than two columns, because an agent id without the description version says
*who* but not *what they claimed to be at the time*. `tools.archive` needs
no judge because it only exports; the status flip is
`apply_reflection(archivals=…)`, which is attributed. `require_judge` is one
gate at the boundary (§3.3.1).

### 10.5 The journal

The `decision` table on both backends, `record_decision`, `get_decision`,
`query_decisions` (with `agent_ids`, `kinds`, `subject_id`, `subject_ids`,
`reviews`, `since`, `until`, `limit`), `reviewed_decision_ids`,
`count_decisions_by_graph`; a row at every writer (§4).

### 10.6 `review`, `apply_review`, `rejudge`

`review()` in `pipelines/review/modes.py`, read-only, `mode` and
`max_results` plus the narrowing arguments (§6.1); `apply_review` and
`rejudge` in `pipelines/review/apply.py`, the only writers of `CONFIRMATION`,
`DISSENT` and `REJUDGMENT`. `confidence` lives on `ValueSignal`, which every
node type carries, so a signal reading `node.confidence` would raise on a
`Topic`; the retrieval-declaration parity suite is what catches that class,
along with an undeclared `retrieved` on a review response, which would grey
out a subject the moment somebody clicked the decision naming it.

### 10.7 Cross-cutting

- Every protocol method lands on `memory.py` and `surrealdb_adapter.py` in
  the same change. No capability flags.
- Settings follow the reflect-threshold pattern: per-graph override,
  explicit default, no singleton (`merge_undo_depth`, `merge_cycle_limit`,
  `require_judge`, approved agent ids).
- Named constants, one declaration each, with a test that reads every
  declaration.
- Reversal is one transaction on both backends, with a test that a mid-way
  failure leaves the graph unchanged. `apply_review` is deliberately not
  (§6.4).

---

## 11. Rejected

- **Hashing the description into the id** (§2.1). Reword and fragment; paste
  and collide.
- **One user-assigned string as id, name and handle.** A name could never be
  corrected, and one character's difference made a second judge with a
  permanently separate history.
- **A `confirm_agent` MCP tool** (§2.3). A tool the agent calls cannot
  establish that the user called it.
- **Session-per-mint.** Makes self-review impossible by construction, and
  fragments one judge across sessions.
- **A shared registry, or agents as graph nodes** (§2.5).
- **An ambient current-agent singleton** (§3.2).
- **A mandatory judge from a fixed release, with blank meaning legacy**
  (§3.3). It bought one meaning by making the field mandatory for ever, a
  large permanent cost to date-stamp a population that needed no date.
  Guarding against a scar is not the same as needing a guarantee.
- **A synthetic legacy judge as backfill** (§3.3).
- **Review state inline on nodes and edges** (§3.4).
- **A traversable `judged_by` edge** (§3.5).
- **One `similarity` edge for every decline** (§1.2).
- **A write on `merge_facts`' refusal, or a standalone `record_similarity`
  tool** (§1.3). The first turns a call that answered *no* into a write; the
  second is `link`, which nobody called.
- **Unrated confidence as a difficulty signal; a `[0.80, 0.85)` band** (§5).
- **`uncertain` and `difficult` as modes** (§6.2); **`between` as a mode;
  `graphs=` on `review()`** (§6.1, §6.6).
- **`reversals` on `apply_review`** (§6.4). It reversed nothing.
- **`rejudge` growing frame and interval fields** (§6.5).
- **A global undo ring** (§7.3). It would throw away its tail across a
  session.
- **Retiring the survivor on reversal; a public `delete_node`** (§7.7).
- **An inference-only reversal guard** (§7.5).
- **A field-listed `MergedEdge`** (§7.9).
- **A warning instead of a refusal for merge oscillation** (§7.8).
- **A per-node note list for advisories** (§9).
