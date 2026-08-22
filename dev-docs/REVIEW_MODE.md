# Review mode: who judged this, and can someone else check it

**Status: designed, not built (2026-08-22).** Written before any code, at the
user's direction. Nothing here is implemented; where it says "does", read
"would".

**Revised 2026-08-22 after review**, which found seven defects in the first
draft. Five were mechanical; two changed the design. What moved is recorded in
§11 rather than quietly rewritten, because two of the corrections are the same
carry-forward this repo has now banked three times.

The motivating case, in the user's words: *"using a different agent to review
the decisions previously made by the first agent"*.

That is not renderable from anything the system currently stores, for a reason
worth stating up front: **no decision in this system records who made it.** Not
nodes, not edges, not `LifecycleEpisode`, not `NodeChangeEvent`. A second agent
can see what was decided and when; it cannot see that a different agent did it,
and on its own second pass it cannot tell its own decisions from the first
agent's.

This document covers five parts, together because none of them works alone:

1. **The missing actions** — `similarity`, the verdict with no writer (#64), and
   the suppression marker it turned out to be entangled with.
2. **The registry** — what an agent is, how its identity survives being
   re-described, and why the user assigns it.
3. **Attribution** — where the judge is recorded, and what its absence means.
4. **Review modes** — the filters over decisions, from *the uncertain ones* to
   *all of them*.
5. **Reversal** — what review can actually undo, and the one thing that has to
   be captured before it can ever be built (§7).

Design history it depends on: `ISSUES.md` #64 (the defect), #52 (fact merge),
#63 (the one nomination bar), #60 (bounding a response), #46 (unrated is not
0.5), `REVIEW_EPISTEMIC.md` §3 (the verdict taxonomy), `EVENT_LOG.md` (the
durable change path this extends), and **`WARNINGS_AND_SETTINGS.md` §5.3 and §9**
(node notes, folded into this document's journal — see §9).

---

## 1. The missing actions

### 1.1 The verdict with no writer

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
| **compatible** | **nothing — by omission, not by design** | ❌ |

`grep EdgeType.SIMILARITY` returns three sites and all three read. Measured
2026-08-21: **0 similarity edges of 4,386 on `memory`, 0 of 1,028 on
`petritype-server`.**

Of the 18 pairs `reflect` nominated on 2026-08-21, five merged and **thirteen
were declined and vanished**. They are still being re-nominated, because
`already_linked` in `contradiction_detection` is built from
`SIMILARITY ∪ CONTRADICTION` and is therefore always empty.

### 1.2 A decline is two populations, not one

**This is the correction that most changed the design.** The first draft wrote
one `similarity` edge for every decline, and that edge has two readers which
want opposite breadth:

- `already_linked` wants it **broad** — suppress every pair anybody assessed.
- `corroboration.py` wants it **narrow** — its neighbourhood is *restatements of
  one claim*, and `docs/RETRIEVAL.md` §8 says outright that a wrong `similarity`
  edge overstates the count.

Write one edge for both and *"these two are different claims"* starts
corroborating. That is **manufactured support** — the failure `fact_dedup.py`'s
own header calls the worst the system can produce, since a false merge does not
lose information, it inverts the quantity corroboration measures.

The two populations are already in the taxonomy; the first draft invented a
category ("related, kept apart") that collapsed them:

| Why the agent declined | It is | Records |
|---|---|---|
| `redundant`, but `merge_facts` refused — an **event**, or an **unjudged** `claim_kind` | genuinely one claim | `similarity` **and** `assessed` |
| `compatible` — different claims that merely look alike | not one claim | `assessed` only |

The other two refusals need no new action: a **cross-frame** pair is
`record_variant`, and a **retired twin** is `recurs` → `restore`. A below-bar
refusal cannot reach a nominated pair, since #63 unified the bar.

So suppression gets a record of its own — an **`ASSESSED` edge** — and
`already_linked` reads `SIMILARITY ∪ CONTRADICTION ∪ VARIANT_OF ∪ ASSESSED`.

`ASSESSED` is a denormalised suppression index and it passes the rule in §3.4:
it is immutable and append-only, so it cannot drift from the journal that also
records the decision. The journal is the audit record; the edge is the index the
sweep reads without a journal query.

### 1.3 The action

**`apply_reflection(similarities=[…])`**, a tenth kind of decision beside the
nine that exist, carrying the verdict rather than a bare pair:

```python
similarities=[
    {"pair": [a_id, b_id], "verdict": "one_claim", "because": "same claim; "
     "merge refused, both are events"},          # → similarity + assessed
    {"pair": [c_id, d_id], "verdict": "distinct", "because": "both about "
     "validity intervals, different assertions"},  # → assessed only
]
```

The agent is already in that call having made every other decision; declining
becomes an outcome applied in the same batch rather than a separate errand. The
two alternatives were rejected:

- **A write on `merge_facts`' refusal** — wrong for the cross-frame refusal,
  where `record_variant` is the correct relation; and it turns a call that
  answered *no* into one that wrote to the graph anyway. §1.2 makes it worse
  still: the refusal reason does not determine the verdict, since an agent may
  decline a mergeable pair as `distinct`.
- **A `record_similarity` tool** — this already effectively exists as `link`,
  has existed throughout, and the count is zero. A call outside the loop where
  the judgment happens is a call nobody makes.

**This is not licence to write these edges automatically.** They record a
*judgment*. A sweep that wrote them for every pair over the bar would fill the
graph with assertions nobody made and suppress its own future nominations while
doing it. Similarity nominates; the agent judges (#63).

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

So: **a stable `agent_id` assigned by the user, with an append-only list of
dated descriptions under it, and the hash identifies the description
*version*.**

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
    # Set only through a channel that terminates at the user — `ctx.elicit`,
    # or the `epimemer agents confirm` CLI where the client cannot elicit
    # (§2.3). Never by the agent alone. `None` is *self-described, unconfirmed*
    # — a different epistemic object, never collapsed into the same field.
    confirmed_at: datetime | None = None


class Agent(BaseModel):
    """A judge: something that made decisions in this graph.

    Not a user account and not a credential. See §2.4.
    """
    id: str                      # assigned by the user, not minted here
    descriptions: list[AgentDescription] = Field(default_factory=list)
    authorised_at: datetime      # when the user admitted this id
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
```

The append-only-list-with-dates shape is deliberately the one `LifecycleEpisode`
already uses for node history. Same problem, same answer: a scalar plus a
timestamp cannot express *changed, and here is what it was before*.

### 2.2 The user assigns the id, and that is what makes review provable

An agent that mints or claims its own id cannot establish that it is a
*different* agent from the one that decided yesterday — the motivating case
collapses, because `reviewed_by == judged_by` and self-review is
indistinguishable from independent review.

**Identity is proposed, not claimed** (user's design, 2026-08-22). The agent is
not the author of who it is, and it is not left guessing either — it offers, the
user edits, and the approved pair is what gets recorded:

| Step | Who | How |
|---|---|---|
| Detect a new session or a different client | the server | `ctx.session_id`, `ctx.client_id` |
| Propose an id and a description | the agent | `claim_agent(agent_id, description)` |
| Edit and approve, or name a different id | **the user** | `ctx.elicit` — the server asks, the user answers (§2.3) |
| Record the approved pair | the server | `Agent` + an `AgentDescription` version |
| Re-describe later | the agent, approved the same way | appends a version, never edits one |

**`claim_agent` refuses an id the user has not approved.** An unapproved or
absent identity comes back refused, with a message the agent puts to the user —
so **the refusal is the prompt**, and no separate startup handshake is needed.
Admitting unapproved ids would hand identity straight back to the agent, and
*"a different agent reviewed this"* would be self-asserted again, which is the
whole thing this section exists to prevent.

**The user owns the semantics, and the system imposes none.** Whether ids track
a model (*"my llama agent"*), a role (*"my critic"*), or a task (*"my editor
reviewer"*) is the user's scheme. Two harnesses running the same model are one
judge or two exactly as the user decides.

**What the server can and cannot detect, stated because it bounds the above.**
`client_id` and `session_id` identify the **client application**, not the model
behind it. A different harness is detectable; **swapping the model inside one
harness is not** — the model is never on the wire. So detection reliably answers
*"is this a new session?"* and can never answer *"is this a different LLM?"*.
That second question is the user's to answer, which is what the flow above
already assumes.

**Session-per-mint was rejected** — it makes self-review impossible by
construction, but fragments one judge across sessions, which is the failure
§2.1 ruled out hashing for.

### 2.3 Confirmation reaches the user, not the agent

The first draft said confirmation *"is not an MCP tool, and cannot be"*, on the
grounds that a tool called by the agent cannot establish that the **user** called
it. The premise is right and the conclusion was wrong, because it assumed every
MCP call originates with the agent.

**`ctx.elicit` inverts the direction**: the server asks, and the answer comes
back from the user through the client's own UI. Present in FastMCP today, and
gated on a capability the client declares. So confirmation can be in-band after
all, and `confirmed_at` can mean what it says.

| Channel | When | What `confirmed_at` then means |
|---|---|---|
| `ctx.elicit` | the client supports elicitation | the user answered, through their own UI |
| `epimemer agents confirm <id>` (CLI) | it does not | the user ran a command the agent cannot run |

**What has not changed is the rule.** No path exists by which the agent alone
sets `confirmed_at` — the two channels above are the only ones, and both
terminate at the user. A `confirm_agent` tool the agent could call is still
refused, for the reason the first draft gave.

### 2.4 Self-description is a claim, not a credential

**This must be stated in the tool guidance and not only here.** An agent
describing itself is making an assertion, exactly like a fact it ingests.
Nothing verifies it; the field is self-reported prose.

That is fine for what this is — an audit trail. It is not fine as a trust
signal, and the risk grows with adoption: on five decisions a human eyeballs the
field; on six hundred thousand somebody builds a filter like *"only count facts
judged by agent X"* and forgets what the field is made of.

Two rules follow, and they are load-bearing:

- **`confirmed_at` is the only part with human weight**, and §2.3 is why it can
  carry any.
- **The judge never gates anything automatically.** No ranking, no corroboration
  weighting, no default filter. Review mode *selects* on it; nothing *decides*
  on it. §8 refuses the obvious next request.

### 2.5 Where it lives

**A per-graph `agent` table**, like every other table, with the authorised-id
list in per-graph settings. Graphs are isolated by design.

The first draft claimed *"the same `agent_id` appearing in two graphs is how a
human correlates them"* while also minting ids per graph, which never produces
the same id twice. Under §2.2 it works, and for the right reason: **the id is
the user's to assign, so the user can assign the same one in both graphs.**
Correlation is a human act, which is what it always was.

Rejected: a shared registry (breaks the isolation every other table follows),
and agents as ordinary graph nodes (they would surface in search and get swept
by `reflect` — two agents with similar descriptions are not a topic to merge).

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
| similarity / assessed / contradiction / variant | edge `metadata` | *"which pairs did it decline, and as what"* |
| merge, supersede, archive | `LifecycleEpisode` | *"who retired this, and what replaced it"* |
| `judge_importance` | `ValueSignal` | already has `importance_judged_at`; gains a judge |
| topic parents / splits / enrichments | `Topic` / `metadata` | structural calls nobody currently owns |

### 3.2 Threading it, without a singleton, and mandatory after cutover

The obvious implementation is an ambient "current agent" resolved once and read
from everywhere. **That is a singleton and this project does not have those.**

It is not needed. `storage` is already passed explicitly into every tool in
`mcp/tools.py`; `claim_agent` binds the session; the resolved judge rides down
as one more explicit parameter beside a parameter that is already there.

```python
async def store_decomposition(
    ...,
    storage: StorageBackend,
    judge: JudgeRef,          # (agent_id, digest) — required, not `| None`
) -> tuple[dict, ResponseMeta]:
```

**Required, and a write without one is refused** — the `judge_importance` shape,
not a default. This is not fastidiousness: §3.3's whole rule depends on it. An
optional parameter makes `None` ambiguous again on day two, and the island
below stops being datable.

### 3.3 What absence means — decided on day one

The day the field exists, every node and edge already in the graph reads as
*judged by nobody*, and nothing distinguishes that from *written before
attribution existed*.

**This project has the scar twice already.** Every row written before
2026-08-19 carries a literal `0.5` confidence, so those rows read as *rated
ordinary* when nobody rated them — which is why #46 stores unrated as absent.
And #52's 305 of 356 active facts carry no `claim_kind` and never will, an
island that does not shrink by waiting.

So, decided now rather than discovered later:

- **`judged_by is None` means "written before attribution existed"** — one
  meaning, guaranteed by §3.2's refusal, with the cutover date recorded here and
  in `docs/`.
- **No backfill, ever.** Stamping a synthetic `legacy-agent` asserts that an
  agent existed and made a judgment. That is the same species of lie as the
  literal `0.5`, reached by the same well-meant route.
- **Review modes exclude null by default** and report how many they excluded, so
  a caller can tell *nothing to review* from *nothing attributable*.

### 3.4 Immutable facts may be denormalised; mutable state may not

The first draft put `reviewed_by` / `reviewed_at` both on the journal row and
inline on every node and edge — two homes for one *mutable* fact, kept in sync
across two backends. That is the shape this repo has hit three times (#54, #55,
#56) and it was reintroduced in a document that cites all three.

The rule that prevents it, stated once and applied throughout:

| On the row, inline | In the journal only |
|---|---|
| `judged_by`, `judge_desc` — **never change** | review state — **changes** |

So the inline fields are the original judge and nothing else. Nothing on a node
or an edge ever records that it was reviewed; §4 does, and derives it.

### 3.5 Linkage is inline, not an edge

Each decision record carries the ids as fields. One rule everywhere, no extra
edges, no fan-out on the retrieval path.

A traversable `judged_by` edge was rejected on a structural point rather than a
cost one: **edges cannot originate from edges**, so similarity, assessed,
contradiction and variant decisions — the ones review mode most wants — would
need the inline form regardless. That is two rules for one relation, to make one
query prettier.

The consequence is accepted: *"everything agent X decided"* is a scan rather
than a traversal, wanting an index on `judged_by`. §4 makes it a single-table
one.

---

## 4. The decision journal

Attribution on the rows answers *who judged this node*. Review mode asks the
inverse — *what did this agent judge* — and that question, over rows scattered
across facts, edges, lifecycle episodes and value signals, is five scans and a
reassembly.

**So decisions are also appended to a journal**, and §3.4's inline fields are
the immutable denormalised copy.

```python
class DecisionRecord(BaseModel):
    """One judgment, as an append-only row.

    **Never edited, with no exceptions** — including for review state, which is
    why there is no `reviewed_at` here. A review is another record pointing back
    (`reviews`), so *reviewed* is derived from existence rather than stored as a
    mutable flag on a row that claims to be append-only. The first draft had
    both and the contradiction was load-bearing: a mutable field on this row
    also has to stay in sync with a copy on the node, across two backends.
    """
    id: str = Field(default_factory=_new_id)
    kind: DecisionKind
    subject_ids: list[str]
    judged_by: str
    judge_desc: str
    decided_at: datetime
    # §5. Same ladder as `confidence` (#46), stated once and referenced — absent
    # means unrated, which is deliberately not a rated 0.5.
    certainty: float | None = None
    certainty_basis: str | None = None
    # The record this one is about. A confirmation reviews without superseding,
    # so the two are separate fields — collapsing them is what breaks a
    # derived-only scheme, since a confirmation supersedes nothing.
    reviews: str | None = None
    supersedes: str | None = None
```

`reviewed` is `EXISTS(record WHERE reviews = this.id)`. `unreviewed` mode is its
complement. Nothing is ever written twice and nothing is ever edited.

**A reversal appends, and never deletes.** Agent 2 overturning agent 1's merge
writes a record with both `reviews` and `supersedes` set. The graph's own rule —
*nothing is destroyed, ambiguity is made visible* — applies to the record of
judgments exactly as it applies to the claims.

### 4.1 Granularity at ingest

**One record per `store_decomposition` call, not per fact.** Forty-four facts
from one document is one reading of one document, and the agent made one pass;
recording 44 decisions would make ingest the journal's dominant writer by orders
of magnitude and would still be describing a single act.

The per-node judgments ride inside the record. The trade, stated: reviewing
*"was this fact really a `state`?"* nominates a record covering the whole
document, and the reviewer opens the facts from `subject_ids`. That is the
honest granularity — one document read, one judgment pass.

### 4.2 Which record is primary

A merge now appears in two places, and `EVENT_LOG.md`'s rule is that one of them
is the primary. They answer different questions and neither is derived from the
other:

| Question | Primary |
|---|---|
| *what happened to this node* | `LifecycleEpisode` → `query_changes` |
| *who decided it, how sure were they, has anyone checked* | `DecisionRecord` |

A status change with no judgment behind it is possible (`restore` after an
archival sweep); a judgment that changes no status is common (a declined pair).
Neither can be reconstructed from the other, so both stay, with the boundary
written down here.

---

## 5. Uncertainty: declared and derived, kept apart

*"Reviewing difficult or uncertain decisions"* needs the system to know which
those are, and there are two sources that must not be blended into one number.

**Declared.** The deciding agent supplies `certainty`, on **the same ladder as
`confidence`** (#46) rather than a second near-identical one — 0.3 / 0.5 / 0.7 /
0.9 with the same anchors, absent meaning unrated, and a basis asked for
whenever the value is not 0.5. Stated once in the shared guidance and referenced
from both, so ingest guidance does not double in size.

**Derived.** Properties of the decision, computable after the fact:

| Signal | Why it suggests difficulty |
|---|---|
| source `confidence` **< 0.5** | the material was thin |
| a merge over three or more sources | more ways to be wrong |
| a contradiction with no resolution | left open, by decision or by neglect |
| a decision whose subject has since been superseded | the ground moved under it |

**Unrated confidence is not a difficulty signal**, and the first draft's table
said it was. The #46 ladder defines absent as *the ordinary case* — "stated
plainly, no specific reason to doubt → omit the field" — and it is the majority
state (125 unrated on `memory`). Treating absence as thinness floods the mode
with ordinary decisions and re-commits the exact sin #46 fixed: assigning a
meaning to absence that nobody asserted.

One population this signal is blind to, recorded rather than papered over: the
pre-2026-08-19 rows carrying a literal `0.5` are *genuinely* unrated and pass a
`< 0.5` filter as rated-ordinary. Nothing can separate them now, which is what
that scar costs.

**No similarity band, until it is measured.** The first draft proposed
`[0.80, 0.85)` as *just cleared the bar*. The 0.85 was invented, and #63 is
explicit that every bar reads one named, documented constant with a test across
its declarations.

It is also not yet derivable. `BENCHMARKS.md` measures 38,226 real fact pairs
with median 0.164 and p99.9 = 0.683 — but that describes the *whole*
population and says nothing about the shape *above* 0.80, which is where the
band would live. The only two above-bar scores on record, 0.87 and 0.89 from
2026-08-21, sit **above** a `[0.80, 0.85)` band, so it would have selected
neither. Two points is not a distribution.

So the band is cut, and **measuring the above-bar distribution is a precondition
for adding it back**. The four signals above need no threshold and carry the
mode meanwhile.

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
| `unreviewed` | no record `reviews` this one | *"what has nobody looked at"* |
| `advisory` | `kind` is an advisory override (§9) | W&S §5.3's `contested_decisions` |
| `all` | every record | the full audit |

Modes compose — `by_agent` **and** `since` is the ordinary case for *"review
what agent-1 did yesterday"*.

**Every mode is capped and reports `truncated`**, #60's treatment applied
verbatim. `all` over an append-only journal fed by every ingest is precisely the
unbounded response #60 capped four lists for, and designing it uncapped the day
after would be perverse. As there: when a list is named in `truncated`, act on
what came back and review again rather than raising the number.

**Every mode excludes pre-attribution rows by default** (§3.3) and reports how
many it excluded.

**Confirming costs something, or the treadmill moves up a level.** If agent 2
reviews a decision and agrees, and nothing records that, agent 3 does the same
work again — the defect of §1, one layer higher. So confirmation writes a
`DecisionRecord` with `reviews` set, through a named writer:
**`apply_review(confirmations=[…], reversals=[…])`** — `review()` stays
read-only, and none of the existing decision tools can write this.

This follows `judge_importance`, whose `importance_judged_at` moves on
re-confirmation, and whose `stale_judgment` archival class exists to stop an
unrevisited judgment protecting a node for ever (`docs/REFLECTION.md` §5).

---

## 7. Reversing a merge

Undo was out of scope in the first draft. It is in scope now because of a
property that only shows up when you look at what a merge leaves behind:
**the information reversal needs is destroyed at merge time and is not
reconstructible afterwards.** So the decision is not *build undo or don't* —
it is *capture or lose*, and it has to be made before the next merge, not
before the undo.

### 7.1 What a merge destroys

`merge_nodes` migrates every knowledge edge onto the survivor, collapsing
duplicates by `(src, dst, type)`. Nothing records which source each migrated
edge came from. Measured on the five merges of 2026-08-21: **the ten retired
sources hold zero knowledge edges between them** — all 24 sit on the survivors,
with no record of the partition. Where two sources cited the same document, the
two `sourced_from` edges genuinely collapsed into one.

`metadata.merged_from` names the nodes that merged. It does not name their
edges, and no later pass can recover them. **This is `claim_kind`'s shape
again**: information that exists at one moment only, with an island that grows
with every merge taken before it is captured.

### 7.2 Where merge information lives today

Distributed across three places, none of them complete:

| Where | What | Grows with |
|---|---|---|
| survivor node, `metadata.merged_from` | the ids that merged into **this** survivor | nothing — set once |
| each retired source, `lifecycle` | `{because: merged, counterpart: <survivor>, retired_at}` | every merge that node takes part in |
| graph edges | `merged_into` (source→survivor), `evidence_merged` (to dependents) | every merge |

> **Corrected 2026-08-22.** This table previously said `merged_from` was
> "appended per merge, unbounded". It is not: `merge_facts` constructs a **new**
> `Fact` on every call with `merged_from` set once at construction, so a single
> survivor's list never grows.
>
> **What grows is the chain**, and that is what §7.4 bounds. `A+B→S1`, then
> `S1+C→S2`: `S1` is retired `MERGED` and linked to `S2` by `merged_into`, so
> unwinding `S2` back to `A, B, C` needs `S2`'s partition *and* `S1`'s. Depth is
> a property of the lineage, not of any one node's list — which is what the
> user's original framing said and this table did not.

### 7.3 The split: audit in the journal, payload on the node

The two halves of "what happened in this merge" have different lifetimes and
different readers, so they live in different places:

| | Lives in | Size | Lifetime |
|---|---|---|---|
| **Audit** — who merged, when, why, how certain | `DecisionRecord` (§4) | small | permanent |
| **Payload** — the pre-merge edge partition | the survivor node | ~190 B/merge, measured | bounded, §7.4 |

Putting the payload on the node rather than in the journal is the user's design
and it is better than the global ring first proposed here, for a reason the
alternative got backwards. A working session of ~55 merges touches ~55
*distinct* survivors, one entry each — so under a per-node bound nothing evicts
and the whole session stays reversible, where a global ring of the same nominal
size would have thrown away its tail.

### 7.4 The bound

**`merge_undo_depth`, default 10, a per-graph setting** — the per-graph override
pattern, no singleton. Each survivor carries the partition of the one merge that
created it; the setting bounds **how far back along the `merged_into` chain
those partitions are retained.** On each merge, walk the chain back from the new
survivor and clear `merge_undo` on any ancestor deeper than the limit. Ten
levels of a lineage stay reversible; the eleventh and beyond become permanent.

**Ten, and not more, because of what the bound actually targets.** It is not
storage: merge does not shrink this graph. Sources are retired, not deleted —
all ten from 2026-08-21 are still present as `MERGED` husks keeping their
content and their 384-dimension vectors, so what a merge already retains and
never reclaims (~3.4 KB) is roughly eighteen times the undo payload it would
add (~190 B). Net node count rose across that session, 558 → 638.

What the bound targets is the case where a single claim keeps absorbing
restatements — merge document 3's phrasing into the survivor, then document 4's,
then document 5's. That is the **expected** pattern for anything frequently
restated rather than a pathology, and today that node's history grows without
limit. Ten levels of reversibility on one claim is generous; chains are rare
enough that all five merges of 2026-08-21 were depth 1.

**What eviction discards, stated plainly because this is the first structure in
the system that deliberately forgets.** `lifecycle` and W&S's `notes` are both
unbounded append-only lists, so a bounded one beside them will otherwise read as
an oversight. What is dropped is **reversal capability, never a claim**: every
merged source node, its content, its provenance, its lifecycle episode and its
`merged_into` lineage edge all remain exactly as before. The graph forgets how
to replay an edge migration automatically. It forgets nothing it knows.

**It also settles the archival interaction**, which the global-ring design could
not. Archive the survivor and its payload goes with it — no dangling promise, no
undo buffer pinning nodes against the graph's own cleanup, no staleness check to
write. That self-maintenance is what node-attachment buys, and it is the second
reason to prefer it.

### 7.5 The guard that is not a setting

Depth bounds *how far back*, and says nothing about *whether it is safe*.
Reversal is refused — with a reason, as every refusal here is — when something
has come to depend on the survivor:

- the survivor has itself been merged again, or superseded;
- inferences have been drawn on the survivor's own wording, rather than migrated
  onto it by the merge being reversed.

**No configured value raises past this.** `merge_undo_depth` is policy; this is
correctness, and a reversal that ignored it would leave dependents resting on
text that no longer exists.

### 7.6 What a reversal restores

**The principle (user's, 2026-08-22): reversing returns the graph to the status
it had before the merge, and reversing back and forth N times is
indistinguishable from doing it once.** Every flag the merge set is returned.

| The merge did | Reversal does | Exact |
|---|---|---|
| moved A and B's knowledge edges onto S | replays the captured partition, **splitting** an edge that collapsed when both cited one document | ✅ — what §7.1's capture is for |
| A, B status → `MERGED` | → `ACTIVE` | ✅ |
| wrote `merged_into` A→S, B→S | deletes them | ✅ |
| wrote `evidence_merged` on dependents | deletes them | ✅ |
| appended a lifecycle episode to A and B | closes it with `restored_at` | status ✅, history appended |
| created survivor S | **deletes it** (§7.7) | ✅ |
| — | appends a reversal `DecisionRecord` | new, by design |

**No new flag is raised on the dependents, and that is the principle working
rather than an omission.** The merge re-pointed each dependent's `derived_from`
onto S and flagged it `evidence_merged` — *your premise was reworded, go re-read
it*. Reversal re-points it back to the premise it was actually drawn from, so
the inference is returned to the state it was in before and there is nothing for
a reader to re-read. A flag here would assert a change the reversal has just
undone. `evidence_merged` keeps its name and its single meaning.

**One boundary, because it is the only place exactness does not hold: status is
restored, history is appended.** `lifecycle` is append-only by design — a node
leaving the active set twice is the Saint Petersburg case #53 legalised — so a
merge/reverse cycle leaves a closed episode behind, and the journal keeps both
decisions. That is the record that it happened, which is not a flag and is not
returned.

---

### 7.7 The survivor is deleted, and `delete_node` is never exposed

**Reversal deletes S rather than retiring it** (user's decision, 2026-08-22).
Two reasons, and the second is the stronger one:

1. **Exactness.** Retiring leaves one husk per merge/reverse cycle, each keeping
   its own 384-dimension vector, visible to `include_corrected` searches and to
   archival nomination. N cycles would stop being equal to one, and §7.6's
   principle would hold only approximately.
2. **A later re-merge must synthesise afresh.** If the same sources are merged
   again, the new survivor should be written from what is known *then* — more
   sources, a different judge, a later reading. Resurrecting the old S would
   silently import a previous agent's wording into a decision nobody made with
   it. This also rules out the tempting optimisation of *reusing* a retired
   survivor on re-merge, and rules it out on principle rather than for being
   fiddly.

**Nothing knowable is lost.** S's content is `extraction_method: "agent:merge"`
— a synthesis sourced from no document. Every claim it carried is back on the
sources with their own provenance, and the merge itself stays in the journal.

**This needs `delete_node` on the storage protocol, which nothing has ever
had.** `delete_edge` exists; no node has ever been hard-deleted, because
retirement is how this system removes things and archival is an export.

> **`delete_node` must never be reachable from an MCP tool, and the note
> belongs in three places** (user's direction): on the protocol method itself,
> on **both** backend implementations, and here. Reversal is its only caller.
> Without that, a system whose central rule is *nothing is destroyed* has
> quietly acquired *delete anything*, and the next person wanting a hard delete
> will find it already built.

What makes it safe is §7.5's guard: by the time reversal is permitted, the only
edges pointing at S are ones the merge itself created.

### 7.8 Futile cycles

A merge reversed, re-made, reversed again is an agent burning tokens on an
oscillation nobody wants. It is not expected — but it is **hard to catch after
the fact and cheap to catch now**, which is the whole case for building it
before it happens.

**The signal already exists and needs no new storage.** Every merge appends a
`LifecycleEpisode` to each source with `because: MERGED`; every reversal closes
that episode with `restored_at`. So one completed cycle leaves one **closed
`merged` episode** on each source, in an append-only list that is never trimmed:

```python
def completed_merge_cycles(node: EpistemicNode) -> int:
    """How many times this node has been merged and then brought back."""
    return sum(
        1 for episode in node.lifecycle
        if episode.because is NodeStatus.MERGED and episode.restored_at is not None
    )
```

**It is also free at the point of use.** `merge_facts` already loads every
source node before calling `merge_refusal`, so `lifecycle` is in hand — the
check costs no round trip and no extra field.

**Counted per node, not per pair.** Pair matching would miss `A+B`, then `A+C`,
then `A+D` — the same node oscillating against different partners. Per-node
catches that, on data that is there either way.

**`merge_cycle_limit`, default 2, a per-graph setting.** One merge-then-reverse
is an ordinary correction. Two can be two judges disagreeing. The third attempt
is oscillation, and that is where the merge refuses:

```
this fact has already been merged and un-merged 2 times, which is the
`merge_cycle_limit` for this graph. Merging it again is likely to be
reversed again. Ask the user before proceeding — and if the merge is
right, the limit is a per-graph setting.
```

**Refusal rather than a warning, deliberately.** `merge_refusal` already returns
prose the caller must act on, and `docs/REFLECTION.md` §1 is explicit that the
agent handles mechanical calls and **escalates consequential ones to the
human**. A warning is something an agent reads and proceeds past, which is the
failure this is for.

**Accepted gap:** an agent could evade the check by merging a different source
set. Recorded rather than closed — the simple version is worth having, and a
system that tried to detect deliberate evasion here would be solving a problem
nobody has.

### 7.9 Implementation notes

Enough to build from, in the order the pieces depend on each other.

**The payload type.** Store the edge *values*, not references: migration
collapses duplicates by `(src, dst, type)`, so the original rows may no longer
exist to point at.

```python
class MergedEdge(BaseModel):
    """One edge as it stood before the merge moved it."""
    owner_id: str                 # which merging source it belonged to
    src_id: str
    dst_id: str
    type: EdgeType
    label: str | None = None
    kind: Literal["relationship", "attribution"] = "relationship"
    weight: float = 1.0
    validity: list[ValidityInterval] = Field(default_factory=list)


class MergeUndo(BaseModel):
    """Everything needed to replay one merge backwards."""
    source_ids: list[str]
    edges: list[MergedEdge]
    merged_at: datetime
    decision_id: str | None = None      # the DecisionRecord (§4)
```

**Where it lives.** `metadata["merge_undo"]`, parsed through `MergeUndo` on read
and write. `Topic`, `Fact` and `Inference` share no base class — each redeclares
`lifecycle`, `value` and `metadata` — so a typed field would have to be added
three times for a payload that only ever exists on merge survivors. `metadata`
already carries `merged_from`, and `merge_nodes` is generic over
`EpistemicNode`, so topics merged through `apply_reflection` get the same
treatment for free.

**Capture point.** `merge_nodes` (`pipelines/graph_construction/versioning.py`),
which already reads every source's edges to migrate them — build `MergeUndo`
from that same read, before migration mutates anything, and store it on the
merged node in the same transaction. Then walk `merged_into` back from the new
survivor and clear `merge_undo` on ancestors past `merge_undo_depth`.

**The protocol.**

```python
async def delete_node(self, node_id: str) -> None:
    """Remove a node permanently. **Reversal of a merge is the only caller.**

    Never expose this through an MCP tool. Nothing else in this system hard-
    deletes a node: retirement is how things leave the active set and archival
    is an export. The one exception is a merge survivor being reversed, whose
    content was written by an agent, sourced from no document, and whose every
    claim is restored to the sources it came from (REVIEW_MODE.md §7.7).
    """
```

The same paragraph goes on both `InMemoryStorage` and `SurrealDBStorage`
implementations — the standing rule is the full protocol on every backend, and
a guard stated only on the protocol is a guard the next implementer does not
read.

**The reversal.**

```python
async def reverse_merge(
    survivor_id: str, storage: StorageBackend, *, judge: JudgeRef,
) -> ReverseRefused | ReverseResult:
```

1. Load the survivor. Refuse if it is not `ACTIVE`, or carries no
   `merge_undo` — the latter meaning either it was never a merge survivor or
   its payload aged past `merge_undo_depth`, and **the refusal must say
   which**, since one is permanent and the other is a mistake.
2. **§7.5's guard.** Refuse if the survivor has an outgoing `merged_into` (it
   was merged again), is superseded, or carries `derived_from` / `supports`
   edges from inferences that are not in the payload — meaning something was
   drawn on the survivor's own wording rather than migrated onto it.
3. Load the sources. Each must be `MERGED` with an open lifecycle episode whose
   `counterpart` is this survivor.
4. **One transaction**, on both backends:
   - recreate each `MergedEdge` on its `owner_id`, splitting any edge the merge
     collapsed;
   - delete the edges the merge left on the survivor;
   - sources → `ACTIVE`, closing the open episode with `restored_at`;
   - delete the `merged_into` and `evidence_merged` edges the merge wrote;
   - `delete_node(survivor_id)`;
   - append a reversal `DecisionRecord` with `reviews` and `supersedes` set.
5. Return what changed, in the shape `merge_facts` returns.

**The cycle check** goes in `merge_refusal`
(`pipelines/reflection/fact_dedup.py`), ordered with the other refusals —
**after** the permanent ones (cross-frame, event, unjudged) and before the
similarity bar, since it is fixable by a human decision rather than by the
graph changing.

**Tests.** `tests/pipelines/test_merge_reversal.py`:

- merge → reverse restores every source to `ACTIVE` with its original edges,
  including the case where two sources cited **one** document and the
  `sourced_from` edges collapsed;
- merge → reverse → merge → reverse leaves the active graph identical to one
  cycle, and `lifecycle` two episodes longer (§7.6's boundary);
- the survivor is gone from `get_node` and from search on both backends;
- each guard in step 2 refuses, with a distinguishable reason;
- a payload cleared by `merge_undo_depth` refuses differently from a node that
  never had one;
- the third merge of an oscillating pair refuses on `merge_cycle_limit`, and
  raising the setting lets it through;
- a partial failure mid-transaction leaves the graph as it was, on both
  backends.

---

## 8. What this deliberately does not do

- **It does not verify anybody.** §2.4. Descriptions are self-reported and the
  system says so wherever it shows one. Only `confirmed_at` carries human
  weight, and §2.3 is what lets it.
- **It does not weight anything by judge.** *"Score facts higher when a trusted
  agent judged them"* is the natural next request and is refused: the input is
  self-reported prose, so a ranking built on it is one any agent can move by
  describing itself differently. Corroboration counts publishers because a
  publisher is a property of the **document**; a judge is a property of the
  **claimant**.
- **It does not re-open applied changes as a matter of course.** Review
  *nominates* a decision for another look; reversing one is a separate, explicit
  act (§7), bounded by depth and refused outright where something has come to
  depend on the result. A merge older than `merge_undo_depth`, or one whose
  survivor has since been built on, is **not** reversible, and `all` mode says so
  rather than implying an undo it cannot offer.
- **It does not backfill.** §3.3.
- **It does not run on its own.** Same rule as `reflect`: a review nobody asked
  for, over a graph nobody was looking at, is consolidation by timer.

---

## 9. Node notes are decision records (folding in W&S §9)

`WARNINGS_AND_SETTINGS.md` §9 (decided 2026-08-21) gives every node an
append-only `notes` list, each `NodeNote` carrying `reviewed_at` and a verdict;
§5.3 makes `contested_decisions` a reflect list scanning for notes without one.

That is a second review-state machine with a second *"what has nobody looked
at"* scan, and an agent proceeding past an advisory would write into both.
**Decided 2026-08-22: one machine.**

| W&S §9 | Becomes |
|---|---|
| `NodeNote` | `DecisionRecord(kind="proceeded_despite_advisory")` |
| `node.notes` | a derived view over records whose `subject_ids` contains the node |
| `NodeNote.reviewed_at` | gone — derived from `reviews`, per §3.4 |
| §5.3 `contested_decisions` | `review(mode="advisory", unreviewed=True)` |

"I was warned and proceeded anyway" is a judgment with a judge, a date and a
subject. It was a separate type only because it was designed a day before the
journal existed.

**W&S §5.2's own argument is why**: two shapes for one question is how *"the
reviewing agent ends up unable to ask one question"*. `WARNINGS_AND_SETTINGS.md`
needs a dated amendment pointing here; it is neither built nor started, so this
costs a paragraph rather than a migration.

---

## 10. Build order

Each step is useful alone, and each is a precondition for the next.

| # | Step | Why here |
|---|---|---|
| **0a** | **`merge_nodes` captures the pre-merge edge partition** as `MergeUndo` on the survivor, with chain eviction past `merge_undo_depth` (§7.4, §7.9) | **Capture or lose.** The partition exists only at merge time (§7.1), so every merge taken before this lands is permanently irreversible. The only step with a deadline. |
| **0b** | **`merge_cycle_limit` in `merge_refusal`** (§7.8) | Same file, same sitting, no new storage — the lifecycle episodes it counts already exist. Cheap now, and near-impossible to reconstruct once an oscillation has run. |
| 0c | `delete_node` on the protocol and both backends, plus `reverse_merge` (§7.7, §7.9) | Needs 0a to have run for anything to be reversible. Carries the never-expose guard in all three places. |
| 1 | `apply_reflection(similarities=[…])` + `ASSESSED` edge | #64's fix. Stops the re-nomination treadmill, and gives corroboration its first real input — **only from `one_claim` verdicts** (§1.2). Independent of everything below. |
| 2 | `agent` table, approved-id settings, `claim_agent`, approval over `ctx.elicit` with `epimemer agents confirm` as fallback | Registry with nothing yet pointing at it. Full protocol on both backends, per the standing rule. |
| 3 | `judge` threaded through the reflect-side write paths | Smallest surface producing attributed decisions, so step 5 has something to read. |
| 4 | `judge` threaded through ingest, mandatory (§3.2) | The bigger churn, and where the unreviewable priors are. **This is the cutover date** §3.3 pins. |
| 5 | `DecisionRecord` + journal writes + W&S §9 folded in (§9) | Makes *"what did this agent judge"* one query. |
| 6 | `review()` with `difficult` and `all`, capped | Works on the existing corpus, since derived signals need no attribution. |
| 7 | `uncertain`, `by_agent`, `since`, `unreviewed`, `advisory`; `apply_review` | Need attributed decisions to exist; useful from the first session after step 4. |

**Steps 0a and 0b go first, and not because anything below needs them** —
nothing does. They are first because they are the only steps whose cost rises
while they wait: both record something that exists at merge time and nowhere
else. 0c can follow whenever. Steps 1 and 2 are independent of all of them and
of each other.

---

## 11. What the review changed

Recorded rather than silently rewritten, because three of these are
carry-forwards this repo has banked before.

| # | Defect in the first draft | Where |
|---|---|---|
| 1 | `DecisionRecord` claimed append-only and carried mutable review state, duplicated inline — two homes for one mutable fact, the #54/#55/#56 shape, in a document citing all three | §3.4, §4 |
| 2 | One `similarity` edge for two populations whose readers want opposite breadth; would have manufactured corroboration. **The #46 carry-forward verbatim**: when a field is documented with an "and", check whether the two halves want the same storage | §1.2 |
| 3 | Registry had no tool surface at all; identity "resolved at the boundary" from nothing, and self-review was indistinguishable from independent review | §2.2, §2.3 |
| 4 | Did not reconcile with `WARNINGS_AND_SETTINGS.md`, designed one day earlier, and did not even cite it | §9 |
| 5 | Unrated `confidence` used as a difficulty signal, re-committing the sin #46 fixed | §5 |
| 6 | `all` mode unbounded (the day after #60); `[0.80, 0.85)` minted an unnamed constant (the week of #63) | §5, §6 |
| 7 | The absence rule held only if the judge were mandatory, which the signature did not say | §3.2 |

Smaller: ingest journal granularity (§4.1), one ladder not two (§5), which record
is primary (§4.2), and a named writer for confirmations (§6).

**What the review confirmed and has not moved**: minted id plus append-only
dated descriptions; pinning `(judged_by, judge_desc)` per decision; no backfill;
no judge-weighted ranking, for the reason in §8; derived difficulty as the only
mode that works on the legacy corpus; and the build order's shape — attribution
before journal before modes.

---

## 12. Open questions

1. **What is the `uncertain` floor?** §5 settles the ladder but not the
   threshold. Likely below 0.5, but it wants the same treatment as every other
   bar here: one named constant, documented, read everywhere (#63).

> **The survivor is deleted on reversal, not retired** — decided 2026-08-22,
> and §7.7 has the reasoning. The decisive argument is not exactness but that a
> later re-merge must synthesise from what is known then; resurrecting the old
> survivor would import a previous agent's wording into a decision nobody made
> with it. `delete_node` joins the protocol and **is never exposed through an
> MCP tool**, with that guard written on the method, on both backends, and in
> §7.7.
>
> **Futile merge/reverse cycles refuse rather than warn** — decided 2026-08-22,
> §7.8. `merge_cycle_limit`, default 2, counted per node from closed `merged`
> lifecycle episodes, which already exist and cost no round trip.
>
> **A reversal raises no new flag on its dependents** — decided 2026-08-22 by
> §7.6's principle, which was a question here. The merge's `evidence_merged`
> edges are deleted rather than mirrored by a second label.
>
> **`claim_agent` refuses an unapproved id** — decided 2026-08-22. Admitting
> invented ids hands identity back to the agent and dissolves the guarantee §2.2
> provides. The cost is one approval on a fresh install, and §2.2's refusal-as-
> prompt is what makes that a conversation rather than an error.
>
> **Undo was question 2 and is now §7** (decided 2026-08-22). The question
> changed shape on inspection: the partition reversal needs is destroyed at
> merge time, so the live decision was *capture or lose* rather than *build or
> not*. Settled: journal for the audit, a bounded list on the survivor for the
> payload, `merge_undo_depth` defaulting to 10, and a safety guard no setting can
> raise past.
