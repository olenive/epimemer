# Review mode: who judged this, and can someone else check it

**Status: designed, not built (2026-08-22).** Written before any code, at the
user's direction. Nothing here is implemented; where it says "does", read
"would".

The motivating case, in the user's words: *"using a different agent to review
the decisions previously made by the first agent"*.

That is not renderable from anything the system currently stores, for a reason
worth stating up front: **no decision in this system records who made it.** Not
nodes, not edges, not `LifecycleEpisode`, not `NodeChangeEvent`. A second agent
can see what was decided and when; it cannot see that a different agent did it,
and on its own second pass it cannot tell its own decisions from the first
agent's.

This document covers four parts, together because none of them works alone:

1. **The missing action** — `similarity`, the verdict with no writer (#64).
2. **The registry** — what an agent is, and how its identity survives being
   re-described.
3. **Attribution** — where the judge is recorded, and what its absence means.
4. **Review modes** — the filters over decisions, from *the uncertain ones* to
   *all of them*.

Design history it depends on: `ISSUES.md` #64 (the defect), #52 (fact merge),
#63 (the one nomination bar), `REVIEW_EPISTEMIC.md` §3 (the verdict taxonomy),
`EVENT_LOG.md` (the durable change path this extends).

---

## 1. The missing action, and why review needs it first

`reflect` nominates pairs and the agent classifies each one. Six of the seven
verdicts have an action. The seventh does not.

| Verdict | Action | Exists |
|---|---|---|
| redundant | `merge_facts` | ✅ (#52, 2026-08-21) |
| supersedes | `supersede_by` | ✅ |
| succeeds | `temporally_followed_by` | ✅ |
| recurs | `restore` | ✅ |
| contradicts | `record_contradiction` | ✅ |
| cross-frame | `record_variant` | ✅ |
| **related, kept apart** | **record `similarity`** | ❌ **no writer anywhere** |

`grep EdgeType.SIMILARITY` returns three sites and all three read. The action
the design recommends in `fact_dedup`'s refusal prose, in `REVIEW_EPISTEMIC.md`
§3, and in the `redundant` row of `docs/REFLECTION.md` §2 is reachable only
through the generic `link(edge_type="similarity")`, and is therefore never
taken. Measured 2026-08-21: **0 similarity edges of 4,386 on `memory`, 0 of
1,028 on `petritype-server`.**

**Decided: `apply_reflection(similarities=[…])`**, a tenth kind of decision
beside the nine that exist. The agent is already in that call having made every
other decision; declining becomes an outcome applied in the same batch rather
than a separate errand. The two alternatives were rejected:

- **A write on `merge_facts`' refusal** — wrong for the cross-frame refusal,
  where `record_variant` is the correct relation and a `similarity` edge would
  assert the wrong thing; and it turns a call that answered *no* into one that
  wrote to the graph anyway.
- **A `record_similarity` tool** — this already effectively exists as `link`,
  has existed throughout, and the count is zero. A call outside the loop where
  the judgment happens is a call nobody makes.

**Why review needs this first.** Two of review mode's populations do not exist
until something writes them down. A declined pair leaves no trace today, so
*"show me what the last agent chose not to merge"* has nothing to read. Of the
18 pairs `reflect` nominated on 2026-08-21, five merged and **thirteen were
declined and vanished** — they are still being re-nominated, because
`already_linked` in `contradiction_detection` is built from
`SIMILARITY ∪ CONTRADICTION` and is therefore always empty.

**This is not licence to write similarity edges automatically.** The edge
records a *judgment* — these two are one claim's neighbourhood and were kept
apart on purpose. A sweep that wrote them for every pair over the bar would fill
the graph with assertions nobody made and suppress its own future nominations
while doing it. Similarity nominates; the agent judges (#63).

---

## 2. The registry: what an agent is

### 2.1 Identity is minted, not derived

The first proposal was to hash the self-description and use that as the id. It
is the right instinct in the wrong slot, and it fails in both directions:

- **Reword the description, become a different judge.** An agent that describes
  itself slightly differently next session gets a new id, and its decision
  history fragments across ids that are in fact one judge.
- **Paste the same description, become the same judge.** Two genuinely
  different agents with identical prose are indistinguishable.

So: **a stable minted `agent_id`, with an append-only list of dated
descriptions under it, and the hash identifies the description *version*.**

```python
class AgentDescription(BaseModel):
    """One thing an agent said about itself, and when it said it.

    Append-only. A re-description appends; nothing is ever edited, because a
    decision made last week was made by whatever this agent claimed to be last
    week, and that claim has to stay readable after the agent changes its mind.
    """
    # sha256 of `text`, truncated. Identifies the *version*, so a decision that
    # records it pins what the judge claimed to be at the moment it decided —
    # no as-of query needed. Re-recording identical text is not a new version.
    digest: str
    text: str
    recorded_at: datetime
    # Set once, when a human confirms this description is accurate. `None` is
    # *self-described, unconfirmed*, which is a different epistemic object and
    # is never collapsed into the same field. To withdraw a confirmation,
    # record a new description; confirmation is about text that stands.
    confirmed_at: datetime | None = None


class Agent(BaseModel):
    """A judge: something that made decisions in this graph.

    Not a user account and not a credential. See §2.3.
    """
    id: str = Field(default_factory=_new_id)
    descriptions: list[AgentDescription] = Field(default_factory=list)
    first_seen_at: datetime
    last_seen_at: datetime
```

The append-only-list-with-dates shape is deliberately the one `LifecycleEpisode`
already uses for node history. Same problem, same answer: a scalar plus a
timestamp cannot express *changed, and here is what it was before*.

### 2.2 What a decision pins

A decision records **both** ids: `judged_by` (the agent) and `judge_desc` (the
description digest current at that moment). The pair answers the question an
auditor actually has — *what did this judge claim to be on the day it made this
call* — without an as-of lookup, and without the answer drifting when the agent
re-describes itself tomorrow.

One accepted imprecision, recorded rather than fixed: a description confirmed by
a human *after* a decision was made will read as confirmed when that decision is
reviewed. Confirmation attaches to the text, the text is pinned by the digest,
and building a second timeline to say *unconfirmed at decision time, confirmed
now* buys precision nobody asked for.

### 2.3 Self-description is a claim, not a credential

**This must be stated in the tool guidance and not only here.** An agent
describing itself is making an assertion, exactly like a fact it ingests.
Anything can claim to be anything; nothing verifies it; and the field is
self-reported prose that is trivially spoofed.

That is fine for what this is — an audit trail, a way for a human or a second
agent to ask *who decided this and what did they say they were*. It is not fine
as a trust signal, and the risk grows with adoption rather than shrinking: on
five decisions a human eyeballs the field, and on six hundred thousand somebody
builds a filter like *"only count facts judged by agent X"* and forgets what the
field is made of.

Two rules follow, and they are load-bearing:

- **`confirmed_at` is the only part with human weight**, and it is recorded
  separately so it can be required separately.
- **The judge never gates anything automatically.** No ranking, no corroboration
  weighting, no filter applied by default. Review mode *selects* on it; nothing
  *decides* on it.

The natural next request — *"score facts higher when a trusted agent judged
them"* — is refused by construction, and §7 says why.

### 2.4 Where it lives

**A per-graph `agent` table**, like every other table. Graphs are isolated by
design and nothing else reads across them; the same `agent_id` appearing in two
graphs is how a human correlates them, and that is enough.

Rejected: a shared registry (breaks the isolation rule every other table
follows, and needs a new cross-graph access path on both backends), and agents
as ordinary graph nodes (in-grain, but they would then surface in search results
and get swept by `reflect`, which is a `topic_consolidation` bug waiting to
happen — two agents with similar descriptions are not a topic to merge).

---

## 3. Attribution: everywhere, including ingest

### 3.1 Scope

**Every write path carries a judge**, ingest included. The marginal cost over
reflect-only is signature churn rather than architecture, and ingest is where
the unreviewable judgments live: `claim_kind`, `confidence`, `importance` are
supplied by the agent that read the material, frozen at that moment, and
re-examined by nothing.

| Decision | Recorded on | Example of what review recovers |
|---|---|---|
| `claim_kind` at ingest | `Fact` | *"agent-1 called 44 facts `state`; two look like events"* |
| `confidence` + basis | `ValueSignal` / `metadata` | *"every 0.9 in this graph came from one agent"* |
| similarity / contradiction / variant | edge `metadata` | *"which pairs did it decline, and why"* |
| merge, supersede, archive | `LifecycleEpisode` | *"who retired this, and what replaced it"* |
| `judge_importance` | `ValueSignal` | already has `importance_judged_at`; gains a judge |
| topic parents / splits / enrichments | `Topic` / `metadata` | structural calls nobody currently owns |

### 3.2 Threading it, without a singleton

The obvious implementation is an ambient "current agent" resolved once and read
from everywhere. **That is a singleton and this project does not have those.**

It is not needed. `storage` is already passed explicitly into every tool in
`mcp/tools.py`; the MCP session is a natural one-agent boundary; so the agent
identity is resolved once at that boundary and rides down as one more explicit
parameter beside a parameter that is already there. It follows the existing
shape rather than fighting it.

```python
async def store_decomposition(
    ...,
    storage: StorageBackend,
    judge: JudgeRef,          # (agent_id, digest) — explicit, never ambient
) -> tuple[dict, ResponseMeta]:
```

### 3.3 What absence means — decide this on day one

The day the field exists, every node and edge already in the graph reads as
*judged by nobody*, and nothing distinguishes that from *written before
attribution existed*.

**This project has the scar twice already.** Every row written before
2026-08-19 carries a literal `0.5` confidence, so those rows read as *rated
ordinary* when nobody rated them — which is why #46 stores unrated as absent.
And #52's 305 of 356 active facts carry no `claim_kind` and never will, an
island that does not shrink by waiting.

So, decided now rather than discovered later:

- **`judged_by is None` means "written before attribution existed."** One
  meaning, recorded once, with the cutover date in this document and in
  `docs/`.
- **No backfill, ever.** Stamping a synthetic `legacy-agent` on old rows asserts
  that an agent existed and made a judgment. That is the same species of lie as
  the literal `0.5`, reached by the same well-meant route.
- **Review modes exclude null by default** and say so in their response, rather
  than silently returning a pre-attribution corpus as *unreviewed*.

Cost of deciding this on day one: a sentence. Cost of deciding it in six
months: archaeology.

### 3.4 Original and reviewer are different fields

Once every record names a judge, the first review raises: whose is it now?

- **Overwrite `judged_by`** and the audit trail you built this for is destroyed
  by the first use of it.
- **Append to a list** and every row on the hottest tables grows without bound.

So they are separate, and only one of them is history:

| Field | Written by | Changes |
|---|---|---|
| `judged_by`, `judge_desc` | the original decision | **never** |
| `reviewed_by`, `reviewed_at` | the most recent review | latest wins |

Anything richer than *somebody looked at this on this date* — what they thought,
what they changed — belongs in the decision journal (§4), not on the row.

### 3.5 Linkage is inline, not an edge

Each decision record carries the ids as fields. One rule everywhere, no extra
edges, no fan-out on the retrieval path.

A traversable `judged_by` edge was rejected on a structural point rather than a
cost one: **edges cannot originate from edges**, so similarity, contradiction
and variant decisions — the ones review mode most wants — would need the inline
form regardless. That is two rules for one relation, to make one query prettier.

The consequence is accepted: *"everything agent X decided"* is a scan rather
than a traversal, wanting an index on `judged_by` (a `DEFINE INDEX` on
SurrealDB, a scan in-memory). §4 is what makes that a single-table query.

---

## 4. The decision journal

Attribution on the rows answers *who judged this node*. Review mode asks the
inverse — *what did this agent judge* — and that question, over rows scattered
across facts, edges, lifecycle episodes and value signals, is five scans and a
reassembly.

**So decisions are also appended to a journal**, and the inline fields of §3
become the denormalised copy that lets a reader see the judge without leaving
the row.

```python
class DecisionRecord(BaseModel):
    """One judgment, as an append-only row. Never edited; a reversal appends."""
    id: str = Field(default_factory=_new_id)
    kind: DecisionKind          # ingest_claim_kind | similarity | merge | …
    subject_ids: list[str]      # the nodes it was about
    judged_by: str
    judge_desc: str
    decided_at: datetime
    # §5. Supplied by the deciding agent; absent means unrated, never 0.5.
    certainty: float | None = None
    certainty_basis: str | None = None
    # Set when a later review touched this record. The review itself is another
    # DecisionRecord, so a chain of reviews is readable in order.
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None
    supersedes: str | None = None   # the record this one revisits
```

This is an extension of a path that already exists rather than a new subsystem:
`query_changes` and `events_in_window` already derive a durable change stream
from lifecycle episodes (`EVENT_LOG.md`). That stream covers **status changes
only** — retirements. The journal covers the decisions that change no status:
a declined pair, a recorded contradiction, a `claim_kind` at ingest.

**A reversal appends, and never deletes.** Agent 2 overturning agent 1's merge
writes a new record with `supersedes` pointing at the old one. The graph's own
rule — *nothing is destroyed, ambiguity is made visible* — applies to the
record of judgments exactly as it applies to the claims.

---

## 5. Uncertainty: declared and derived, kept apart

*"Reviewing difficult or uncertain decisions"* needs the system to know which
those are, and there are two sources that must not be blended into one number.

**Declared.** The deciding agent supplies `certainty` on the decision, mirroring
how `confidence` works at ingest — including the parts that make `confidence`
honest: **absent means unrated**, which is deliberately different from a rated
0.5, and a `certainty_basis` is asked for whenever the value is not 0.5. Only
the agent that had the candidates in front of it can judge this.

**Derived.** Properties of the pair, computable after the fact:

| Signal | Why it suggests difficulty |
|---|---|
| similarity in `[0.80, 0.85)` | just cleared the one nomination bar (#63) |
| source `confidence` < 0.5, or unrated | the material was thin |
| a merge over three or more sources | more ways to be wrong |
| a contradiction with no resolution | left open, by decision or by neglect |
| a decision whose subject has since been superseded | the ground moved under it |

**They are separate modes with separate names, never one blended score.** The
declared one is a record of a judgment; the derived one is a property of the
pair, and calling them the same thing repeats the mistake the whole reflect
design exists to avoid — a number computed from cosines wearing a decision's
clothes (`docs/REFLECTION.md` §1).

The derived mode earns its place for a specific reason: **it is the only one
that works on the decisions already made.** The entire existing corpus predates
attribution, so declared certainty is empty for all of it and will be until new
decisions accumulate. Derived difficulty is what makes review mode useful on day
one instead of in six months.

---

## 6. The modes

`review(...)` — read-only, like `reflect`, and for the same reason: it
nominates, and every change goes through the existing decision tools.

| Mode | Selects | Answers |
|---|---|---|
| `uncertain` | declared `certainty` below a floor | *"what did the last agent say it was unsure about"* |
| `difficult` | the derived signals of §5 | *"what looks hard, on a corpus with no declared certainty"* |
| `by_agent` | `judged_by == …` | *"check everything this judge did"* |
| `since` / `between` | `decided_at` in range | *"review yesterday's session"* |
| `unreviewed` | `reviewed_at is None`, optionally older than N days | *"what has nobody looked at"* |
| `all` | every record | the full audit |

Modes compose — `by_agent` **and** `since` is the ordinary case for *"review
what agent-1 did yesterday"*.

**Every mode excludes pre-attribution rows by default** (§3.3) and reports how
many it excluded, so a caller can tell *nothing to review* from *nothing
attributable*.

**Confirming costs something, or the treadmill just moves up a level.** If agent
2 reviews a decision and agrees, and nothing records that, agent 3 does the same
work again — which is exactly the defect §1 describes, one layer higher. So a
confirmation writes `reviewed_by` / `reviewed_at`, and `unreviewed` is a mode
precisely so the clock can be read. This follows `judge_importance`, whose
`importance_judged_at` moves on re-confirmation, and whose `stale_judgment`
archival class exists to stop an unrevisited judgment protecting a node for
ever (`docs/REFLECTION.md` §5).

---

## 7. What this deliberately does not do

- **It does not verify anybody.** §2.3. Descriptions are self-reported and the
  system says so wherever it shows one.
- **It does not weight anything by judge.** *"Score facts higher when a trusted
  agent judged them"* is the natural next request and is refused: the input is
  self-reported prose, so a ranking built on it is a ranking any agent can move
  by describing itself differently. Corroboration counts publishers because a
  publisher is a property of the document; a judge is a property of the claimant.
- **It does not re-open applied changes as a matter of course.** Review
  *nominates* a decision for another look. Undoing a merge is a real operation
  — `merge_facts` migrated the sources' edges onto the survivor, so reversing it
  is not `restore` — and it is out of scope here. `all` mode will surface merges
  it cannot yet offer an undo for, and should say so rather than implying one.
- **It does not backfill.** §3.3.
- **It does not run on its own.** Same rule as `reflect`: a review nobody asked
  for, over a graph nobody was looking at, is consolidation by timer.

---

## 8. Build order

Each step is useful alone, and each is a precondition for the next.

| # | Step | Why here |
|---|---|---|
| 1 | `apply_reflection(similarities=[…])` | #64's fix. Stops the re-nomination treadmill and gives corroboration its first real input. Independent of everything below. |
| 2 | `agent` table + `Agent` / `AgentDescription`, both backends | Registry with nothing yet pointing at it. Protocol parity per the standing rule — the full protocol on every backend, not flags. |
| 3 | `judge` threaded through the reflect-side write paths | Smallest surface that produces attributed decisions, so step 5 has something to read. |
| 4 | `judge` threaded through ingest | The bigger churn, and where the unreviewable priors are. |
| 5 | `DecisionRecord` + journal writes | Makes *"what did this agent judge"* one query. |
| 6 | `review()` with `difficult` and `all` | Works on the existing corpus, since derived signals need no attribution. |
| 7 | `uncertain`, `by_agent`, `since`, `unreviewed` | Need attributed decisions to exist; useful from the first session after step 4. |

Steps 1 and 2 are independent and can go in either order.

---

## 9. Open questions

These are not decided, and each changes something real.

1. **Does the human confirmation step block, or annotate?** §2.2 assumes an
   agent can act while `confirmed_at` is `None`. The alternative — refuse to
   record decisions from an unconfirmed agent — is defensible and would make the
   registry a gate rather than a log, which is a different feature.
2. **What is the certainty floor for `uncertain` mode?** `confidence`'s ladder
   has documented anchors at 0.3 / 0.5 / 0.7 / 0.9. Certainty wants either the
   same ladder or an explicit statement that it is a different scale.
3. **Undo.** Out of scope above, but `all` mode surfaces merges it cannot
   reverse. Either that gap is stated in the response, or reversing a merge gets
   designed — which is its own document.
4. **Does `reviewed_by` need the description digest too?** §3.4 records only the
   agent. Symmetry says yes; row width says no.
