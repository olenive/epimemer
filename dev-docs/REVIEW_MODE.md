# Review mode: who judged this, and can someone else check it

**Status: designed, not built (2026-08-22).** Written before any code, at the
user's direction. Nothing here is implemented; where it says "does", read
"would".

**Revised 2026-08-22 after review**, which found seven defects in the first
draft. Five were mechanical; two changed the design. What moved is recorded in
§10 rather than quietly rewritten, because two of the corrections are the same
carry-forward this repo has now banked three times.

The motivating case, in the user's words: *"using a different agent to review
the decisions previously made by the first agent"*.

That is not renderable from anything the system currently stores, for a reason
worth stating up front: **no decision in this system records who made it.** Not
nodes, not edges, not `LifecycleEpisode`, not `NodeChangeEvent`. A second agent
can see what was decided and when; it cannot see that a different agent did it,
and on its own second pass it cannot tell its own decisions from the first
agent's.

This document covers four parts, together because none of them works alone:

1. **The missing actions** — `similarity`, the verdict with no writer (#64), and
   the suppression marker it turned out to be entangled with.
2. **The registry** — what an agent is, how its identity survives being
   re-described, and why the user assigns it.
3. **Attribution** — where the judge is recorded, and what its absence means.
4. **Review modes** — the filters over decisions, from *the uncertain ones* to
   *all of them*.

Design history it depends on: `ISSUES.md` #64 (the defect), #52 (fact merge),
#63 (the one nomination bar), #60 (bounding a response), #46 (unrated is not
0.5), `REVIEW_EPISTEMIC.md` §3 (the verdict taxonomy), `EVENT_LOG.md` (the
durable change path this extends), and **`WARNINGS_AND_SETTINGS.md` §5.3 and §9**
(node notes, folded into this document's journal — see §8).

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
    # Set only by `epimemer agents confirm`, which is a CLI command and not an
    # MCP tool (§2.3). `None` is *self-described, unconfirmed* — a different
    # epistemic object, never collapsed into the same field.
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

So identity arrives from outside the agent:

| Step | Who | Where |
|---|---|---|
| Authorise an id | the user | graph settings — the per-graph override pattern, no singleton |
| Claim it for this session | the agent | `claim_agent(agent_id, description)` (MCP) |
| Confirm a description | the user | `epimemer agents confirm <id>` (**CLI, not MCP**) |
| Re-describe | the agent | `claim_agent` with new text; appends a version |

`claim_agent` **refuses an id the user has not authorised.** That refusal is the
whole mechanism: an id in the settings is an assertion the user made, and the
agent can only pick from what is there. Two sessions of the same model are
distinct judges exactly when the user gave them distinct ids, which is the right
place for that decision to live.

**Session-per-mint was rejected** — it makes self-review impossible by
construction, but fragments one judge across sessions, which is the failure
§2.1 ruled out hashing for.

### 2.3 Confirmation is not an MCP tool, and cannot be

An MCP tool called by the agent cannot establish that the *user* called it. If
`confirm_agent` were a tool, `confirmed_at` would mean *"the agent asserts the
user confirmed"*, which is worth approximately nothing and is worse than
nothing once anybody builds on it.

So confirmation is a CLI command outside the agent's reach. This is the one
place in the design with real human weight, and it is worth the extra step to
keep it real.

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
  on it. §7 refuses the obvious next request.

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
| `advisory` | `kind` is an advisory override (§8) | W&S §5.3's `contested_decisions` |
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

## 7. What this deliberately does not do

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
  *nominates* a decision for another look. Undoing a merge is a real operation —
  `merge_facts` migrated the sources' edges onto the survivor, so reversing it is
  not `restore` — and it is out of scope. `all` mode surfaces merges it cannot
  offer an undo for and **says so** rather than implying one (§9.2).
- **It does not backfill.** §3.3.
- **It does not run on its own.** Same rule as `reflect`: a review nobody asked
  for, over a graph nobody was looking at, is consolidation by timer.

---

## 8. Node notes are decision records (folding in W&S §9)

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

## 9. Build order

Each step is useful alone, and each is a precondition for the next.

| # | Step | Why here |
|---|---|---|
| 1 | `apply_reflection(similarities=[…])` + `ASSESSED` edge | #64's fix. Stops the re-nomination treadmill, and gives corroboration its first real input — **only from `one_claim` verdicts** (§1.2). Independent of everything below. |
| 2 | `agent` table, authorised-id settings, `claim_agent`, `epimemer agents confirm` | Registry with nothing yet pointing at it. Full protocol on both backends, per the standing rule. |
| 3 | `judge` threaded through the reflect-side write paths | Smallest surface producing attributed decisions, so step 5 has something to read. |
| 4 | `judge` threaded through ingest, mandatory (§3.2) | The bigger churn, and where the unreviewable priors are. **This is the cutover date** §3.3 pins. |
| 5 | `DecisionRecord` + journal writes + W&S §9 folded in (§8) | Makes *"what did this agent judge"* one query. |
| 6 | `review()` with `difficult` and `all`, capped | Works on the existing corpus, since derived signals need no attribution. |
| 7 | `uncertain`, `by_agent`, `since`, `unreviewed`, `advisory`; `apply_review` | Need attributed decisions to exist; useful from the first session after step 4. |

Steps 1 and 2 are independent and can go in either order.

---

## 10. What the review changed

Recorded rather than silently rewritten, because three of these are
carry-forwards this repo has banked before.

| # | Defect in the first draft | Where |
|---|---|---|
| 1 | `DecisionRecord` claimed append-only and carried mutable review state, duplicated inline — two homes for one mutable fact, the #54/#55/#56 shape, in a document citing all three | §3.4, §4 |
| 2 | One `similarity` edge for two populations whose readers want opposite breadth; would have manufactured corroboration. **The #46 carry-forward verbatim**: when a field is documented with an "and", check whether the two halves want the same storage | §1.2 |
| 3 | Registry had no tool surface at all; identity "resolved at the boundary" from nothing, and self-review was indistinguishable from independent review | §2.2, §2.3 |
| 4 | Did not reconcile with `WARNINGS_AND_SETTINGS.md`, designed one day earlier, and did not even cite it | §8 |
| 5 | Unrated `confidence` used as a difficulty signal, re-committing the sin #46 fixed | §5 |
| 6 | `all` mode unbounded (the day after #60); `[0.80, 0.85)` minted an unnamed constant (the week of #63) | §5, §6 |
| 7 | The absence rule held only if the judge were mandatory, which the signature did not say | §3.2 |

Smaller: ingest journal granularity (§4.1), one ladder not two (§5), which record
is primary (§4.2), and a named writer for confirmations (§6).

**What the review confirmed and has not moved**: minted id plus append-only
dated descriptions; pinning `(judged_by, judge_desc)` per decision; no backfill;
no judge-weighted ranking, for the reason in §7; derived difficulty as the only
mode that works on the legacy corpus; and the build order's shape — attribution
before journal before modes.

---

## 11. Open questions

1. **What is the `uncertain` floor?** §5 settles the ladder but not the
   threshold. Likely below 0.5, but it wants the same treatment as every other
   bar here: one named constant, documented, read everywhere (#63).
2. **Undo.** Out of scope in §7, but `all` surfaces merges it cannot reverse.
   Either that gap is stated in the response — the current answer — or reversing
   a merge gets designed, which is its own document.
3. **Does a reversal need to re-run what depended on it?** Overturning a merge
   should plausibly re-flag the `evidence_merged` inferences (#61). Nothing here
   says so.
4. **Does `claim_agent` refuse, or warn, on an unauthorised id?** §2.2 says
   refuse. The softer alternative — admit it as unconfirmed — is friendlier for
   solo use and dissolves the guarantee §2.2 exists to provide.
