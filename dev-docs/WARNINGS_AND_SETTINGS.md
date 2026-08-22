# Warnings, settings, and the decisions made against them

**Status: designed, not built (2026-08-21).** Written before any code, at the
user's direction. **§9 was superseded on 2026-08-22** — node notes fold into
`dev-docs/REVIEW_MODE.md`'s decision journal; see the banner there and §5.3. Nothing here is implemented; where it says "does", read
"would". **One exception, and it is marked in place**: §1's live defect — a fact
merge not flagging its dependents — was built the same day (§7, ISSUES.md #61).
Advisories, settings, notes and inference merge remain designed only.

This document covers one theme in four parts, and they are together because
each is unusable without the others:

1. **Advisories** — the system telling an agent what is wrong with what it is
   about to do, *before* it does it.
2. **Settings** — how a user turns that surfacing on and off, globally and per
   kind, without a singleton.
3. **The record** — what persists when an agent proceeds past an advisory, and
   who reads it.
4. **Inference merge** — the feature that motivated all of the above, and the
   first consumer of it.

---

## 1. The motivating case

Facts merge as of 2026-08-21 (`ISSUES.md` #52). The immediate consequence is
that a fact which used to exist as four near-identical nodes across four
documents becomes one node — and the inferences drawn on those four nodes all
migrate onto the survivor. What was four inferences hanging off four facts is
now **four near-identical inferences hanging off one fact**, which is both the
clearest case for merging inferences and the one the current system cannot see.

Two gaps are exposed by that, and the first is a live defect:

**A fact merge does not flag its dependent inferences.** Every other event that
changes a premise does: `supersede_node` and `supersede_by_existing` both call
`plan_evidence_stale_edges`, and `review_labels_for` derives `evidence_stale`
from any `derived_from` edge into a retired fact. A merge does neither. The
`derived_from` edge migrates to the survivor, the survivor is `ACTIVE`, and
nothing fires — even though the survivor's content is **agent-written text the
inference was never drawn from**. Verified by construction: after merging *"the
deploy failed"* and *"the deployment did not succeed"* into *"deployments have
been failing"*, the dependent inference's review labels are `{}`.

That is the same reasoning correction uses — the wording under the inference
changed — so a merge should flag dependents too. It is a small fix and it is
listed in §7.

> **Built 2026-08-21 (ISSUES.md #61), and it is the one thing in this document
> that is no longer "would".** The flag is its own edge type, `evidence_merged`,
> deriving its own review label — not `evidence_superseded`, because archival
> nominates on `evidence_stale` and one shared label would have every merge
> propose discarding its own dependents. **It also makes §2's motivating case
> visible for the first time**: four near-identical inferences collected onto
> one survivor now each carry a flag naming the wording they lost, which is the
> population inference merge exists to nominate from.

**Nothing can nominate two inferences as duplicates.** No path merges
inferences, no reflect list nominates inference pairs, and archival can only
reach an inference through `evidence_stale`. An active inference whose evidence
is intact is unreachable by every nomination path in the system.

---

## 2. Why merging inferences needs a warning rather than a rule

An inference node **is** its derivation. Merging two of them migrates both sets
of `derived_from` edges onto the survivor, so a merge of A (resting on `{F1}`)
and B (resting on `{F2}`) produces a node resting on `{F1, F2}` — a combination
neither original had.

Usually that is right and good: two pieces of evidence for one conclusion.

It goes wrong in exactly one **checkable** case: `F1` and `F2` are both dated and
their asserted periods provably fall clear of each other. Then the survivor is
an inference resting on premises no source puts in the same period, which
`find_unsound_inferences` will flag on the next reflect.

**This is not an argument against the merge.** The first draft of this analysis
treated the union as a *fabrication* — the merge inventing a derivation nobody
made — and concluded inferences must never merge. That was wrong, and the
correction is worth recording because it changes the whole design: the
fabrication reading assumes the merge silently preserves two separate arguments.
It does not. **The agent writes fresh content**, asserting one claim over the
combined premises. If those premises never held together, the resulting
inference is *genuinely* unsound and the flag is correct, not manufactured.

So the mechanism is sound and the danger is a specific, computable outcome. That
is precisely the shape of thing a **warning** addresses and a **rule** does not:

- A rule would refuse the merge. But the honest response to *"these premises
  never held together"* is often to narrow the merged claim's wording or period
  — which the agent can only do by writing content, which is what it is already
  doing. Refusing blocks a merge the agent could have fixed.
- Warning *after* the fact arrives detached from the decision that caused it.
- Warning **before** hands the agent the one thing it cannot compute for itself,
  at the moment it is choosing what to write.

**The warning is pre-decision, delivered with the nomination.** Not a second
round trip in which the agent proposes, is rejected, and re-proposes: the
disjointness is computable from the graph before anything is proposed, so it
rides along with the candidate pair. An extra agentic step to deliver
information we already had would be latency bought for nothing.

---

## 3. Advisories

### 3.1 The shape

```python
class AdvisoryKind(str, Enum):
    DISJOINT_PREMISES = "disjoint_premises"
    CROSS_FRAME = "cross_frame"
    SAME_FRAME_CONTRADICTION = "same_frame_contradiction"


class Advisory(BaseModel):
    kind: AdvisoryKind
    message: str                     # one sentence, for a human or an agent
    subjects: list[str] = []         # node ids the advisory is about
    detail: dict = {}                # structured evidence, per kind
```

**Named `Advisory` rather than `Warning` for one reason only**: `Warning` is a
Python builtin, and a Pydantic model shadowing it makes every module that
imports both read ambiguously. The *wire format keeps the user's vocabulary* —
tool responses carry `warnings: [...]` — so only the Python class differs, and
this note is the whole of the translation.

`detail` is structured rather than folded into `message` because the reviewing
agent in §5 needs to sort and group these, and re-parsing prose is how that
rots. For `DISJOINT_PREMISES` it carries the premise ids and their periods —
the same shape `UnsoundInference.disjoint_premises` already produces, reused
rather than restated.

### 3.2 What already exists, and what replaces it

`record_contradiction` and `record_variant` each return an ad-hoc
`result["warning"]` string plus, in the first case, `notify_user: bool`. That is
the right idiom and the wrong plumbing: two hand-written strings, no kind, no
subjects, nothing a setting can address and nothing a reviewer can group.

They become `Advisory` instances with `CROSS_FRAME` and
`SAME_FRAME_CONTRADICTION` kinds. **The existing response keys stay** —
`notify_user` and `warning` are documented in the agent guidance and in
`INTEGRATION.md`, and breaking them to tidy an internal representation would
cost more than it buys. `warning` becomes the rendered `message` of the first
advisory; `notify_user` becomes "any advisory whose resolved action is `flag`".

> **Amended 2026-08-21: that mapping regresses today's behaviour unless the
> default policy says otherwise, so the default policy says otherwise.**
> `record_contradiction` returns `notify_user = shares_frame` today
> (`tools.py`) — a same-frame contradiction *always* notifies. Under §4.2's
> `by_kind = {}` and `default_action = PROCEED`, the same call would resolve to
> `proceed` and return `notify_user: False`. The key would survive with its
> trigger quietly changed, which is the outcome the paragraph above exists to
> prevent.
>
> **So `WarningPolicy` ships with `by_kind = {SAME_FRAME_CONTRADICTION: FLAG}`
> as its default**, not an empty map, and a test pins today's response before
> the refactor lands. A user can still set that kind to `proceed` — the point is
> that turning the notification off becomes a decision somebody makes rather
> than a side effect of a representation change.
>
> The alternative — exempting `record_contradiction` from policy entirely — was
> rejected: `notify_user` would then mean *policy said flag* on one tool and
> *frames overlap* on another, and a caller reading the key could not tell
> which.

### 3.3 Three actions

| Action | Meaning |
|---|---|
| `proceed` | The operation applies. The advisory is recorded and surfaced. **Default.** |
| `flag` | The operation applies, and `notify_user` is set — the agent is expected to raise it with the user. |
| `reject` | The operation is refused, and the advisory is the reason. **Not in the first version** — decided 2026-08-21, see §8.1. |

`proceed` is the default on the user's instruction, and the reasoning is worth
keeping: **the advisory reaches the agent in the nomination, before it decides.**
An agent that has already been told why the merge is questionable and has
written its content in light of that is not a caller who needs stopping. The
other two exist because the same facility will carry advisories that are not
pre-decision, and because a user may reasonably want a class of decision
escalated or forbidden on their graph.

---

## 4. Settings

### 4.1 Explicitly, never a singleton

Settings are a **value passed explicitly**, exactly as `ServerConfig` already is
through `deps["config"]`. No module-level mutable global, no `get_settings()`
accessor, no import-time construction.

The reasons are not stylistic:

- **Tests run two backends and many graphs in one process.** A singleton makes
  every test that changes a setting order-dependent with every test that reads
  one, and that failure appears as an unrelated test breaking later.
- **Settings are per graph.** A process-wide instance cannot answer "what is the
  policy here" once `use_graph` has switched, and a singleton that re-reads
  itself on switch is a cache with an invalidation problem.
- **A function that takes its policy is testable by calling it.** One that
  reaches for a global is testable only by mutating the world first.

### 4.2 The shape

```python
class WarningPolicy(BaseModel):
    """What to do about advisories, and whether to say so."""

    # The global switch. False stops advisories being *surfaced*; it never
    # stops them being recorded — see §5.
    surface: bool = True
    # What happens to a kind nobody named.
    default_action: AdvisoryAction = AdvisoryAction.PROCEED
    # Granular per-kind overrides. Absent means `default_action`.
    by_kind: dict[AdvisoryKind, AdvisoryAction] = Field(default_factory=dict)


def resolved_action(policy: WarningPolicy, kind: AdvisoryKind) -> AdvisoryAction:
    """The action for one kind. Pure, and the only place the fallback lives."""
    return policy.by_kind.get(kind, policy.default_action)
```

`surface` and `by_kind` answer the user's two requirements directly: one switch
for all warnings, and per-kind granularity underneath it.

### 4.3 Two layers, mirroring the reflect threshold

There is already a precedent in the codebase for a setting with a process
default and a per-graph override, and it should be copied rather than paralleled:

| | Reflect threshold | Warning policy |
|---|---|---|
| Process default | `ServerConfig.reflect_threshold` (env) | `ServerConfig.warning_policy` |
| Per-graph override | `set_reflect_threshold_override` | `set_warning_policy_override` |
| Resolution | `resolve_reflect_threshold(override, default)` | `resolve_warning_policy(override, default)` |
| Tool | `configure_reflection` | `configure_warnings` |

`None` as the override means *follow the process default at the time*, not
*freeze today's value* — the property `configure_reflection` already documents,
and the reason it matters is the same: a graph configured last year should pick
up a changed default rather than silently keeping an old one.

**Resolution is per kind, not per policy.** A graph that overrides one kind
should not thereby discard the process defaults for the others, so
`resolve_warning_policy` merges `by_kind` maps rather than replacing them. This
is the one place the two settings differ, because a threshold is a scalar and a
policy is a map, and a map override that silently drops unnamed keys is the
same class of bug as a field-by-field merge rebuild forgetting a field (#45).

### 4.4 A settings menu in the UI — notes, not a design

Not designed here; recorded so the eventual design starts from the constraints
rather than rediscovering them.

- **The panel is per graph**, and must say so on its face. The dashboard already
  follows a `use_graph` switch, and a settings panel that looks global while
  writing per-graph state is a trap.
- **Not a checkbox.** `proceed` / `flag` do not collapse to on/off — one
  surfaces and the other escalates, and neither is "no warning". *(Amended
  2026-08-21: this bullet read "three states … a tri-state control that renders
  as a checkbox will silently lose `reject`", written before §8.1 decided
  `reject` does not exist and is not reserved. Left in the shape it will take
  if `reject` ever lands: the argument is about a control that has to show more
  states than it has, not about the third value specifically.)*
- **"Inherited" is a fourth visual state.** A kind with no override is following
  the process default, and that is different from one explicitly set to the same
  value — the first tracks a changed default, the second does not. The panel has
  to show which, or clearing an override becomes impossible through the UI.
- **The global switch governs surfacing only.** It must not read as "turn off
  warnings", because the records keep accruing either way. Label it for what it
  does.
- Reuse `SemanticPalette` in `theme.ts` (`VISUALISATION.md` C.6) rather than
  minting colours; advisories are a semantic category and should look like one.

---

## 5. The record

### 5.1 Recording is unconditional

**Surfacing is a setting. Recording is not.** The user asked for an option to
suppress a warning while still recording the flag, and that separation is the
load-bearing part of this whole design: a graph whose warnings were switched off
for a month should still be able to answer *"what was decided while nobody was
looking?"* — which is exactly when the question matters most.

So `WarningPolicy.surface` gates the response and the log, never the stamp.

### 5.2 What is stamped

> **Superseded by §9 (2026-08-21).** This section proposed a metadata key. It is
> now a typed `notes` list on the node — a feature rather than a flag, because a
> boolean cannot distinguish *never looked at* from *looked at and upheld* from
> *looked at and fixed*. The paragraph below is kept for the argument it makes
> about generality, which still holds.

When an operation proceeds against an advisory, the affected node carries a note
recording it. A list, because a node can be created by a decision carrying more
than one advisory, and because a later operation on the same node adds rather
than overwrites.

**It is general, not merge-specific.** Fact merges, inference merges, and any
later warned action write the same structure — which is the answer to "should
this be inference-only". A second, differently-shaped record per operation is
how the reviewing agent in §5.3 ends up unable to ask one question.

### 5.3 Who reads it

A new reflect nominee list, `contested_decisions`: active nodes carrying an
**unreviewed note** — a `NodeNote` with `reviewed_at is None` — with the
advisory and the node's current state.

> **Amended 2026-08-22 — this becomes a `review()` mode, not a reflect list.**
> Per §9's banner, notes fold into `DecisionRecord`, so the scan is
> `review(mode="advisory", unreviewed=True)` and "unreviewed" is derived rather
> than a null field. The purpose is unchanged; the reader moves.

> **Restated 2026-08-21 to match §9.** This section was written against the
> `proceeded_despite` metadata key that §9 replaced with the typed `notes` list,
> and said so only in §5.2's banner — so the consumer described here still
> scanned for a key nothing would write. The list is unchanged in purpose; what
> it scans is a field, not a metadata key, and *unreviewed* is now expressible,
> which a bare stamp never was.

> **The key was called `decided_against` in the first draft.** Renamed
> 2026-08-21 because it reads as *decided against doing it* — the opposite of
> what it records. The stamp means *proceeded, despite advice*, and a key whose
> plain reading inverts its meaning is one that will be misread by whoever
> writes the consumer.

Three notes on it:

- **It is linear, not quadratic.** A scan of active nodes for a non-empty
  `notes` list, in the same batched read the other linear phases use. It does
  not join the four capped lists (#60) and needs no cap of its own.
- **It is not the same as `unsound_inferences`**, and folding it in was
  considered and rejected. That list answers *"is this inference unsound
  now?"* — recomputed from the graph every time, and correctly silent once
  somebody fixes the wording. This one answers *"was this decided against
  advice?"*, which is a historical fact that stays true. Merging them would make
  a fixed inference disappear from the audit trail, which is the opposite of
  what an audit trail is for.
- **The reviewing agent is a second agent by intent.** The value of the list is
  that someone other than the decider looks at it, which is why it is a reflect
  worklist rather than a return value the deciding agent reads and discards.

---

## 6. Inference merge

### 6.1 Nomination — scoped to shared evidence

Candidates are **near-identical active inferences that share at least one
premise**. Not a global sweep over all inference pairs, for three reasons:

- It is the case that actually arises. A fact merge collects duplicate
  inferences onto one survivor; that is the population worth reviewing.
- It is cheap. One batched `derived_from` read, grouped by premise id, comparing
  only within groups — where a global sweep is quadratic in *all* inferences and
  would be a fifth capped list immediately after #60 capped four.
- A global sweep would nominate nothing today. Measured on both real graphs
  (2026-08-21): **123 active inferences, 5,053 pairs, zero at the nomination
  bar**, p50 0.16–0.24, p99 0.44–0.55, max 0.66. Measured against 0.83 and
  unaffected by the move to 0.80 (`ISSUES.md` #63) — the highest-scoring pair in
  either graph is 0.14 below the lower bar. The top-scoring pairs are not
  duplicates at all: they share vocabulary and say different things.

That last measurement is why this whole section is designed and not built: the
duplication it addresses does not exist yet, and will not until fact merges start
collecting inferences together.

> **Amended 2026-08-21 — the precondition has since been created.** Later the
> same day, five `merge_facts` calls on the `memory` graph (`ISSUES.md` #52) did
> exactly what the first bullet above anticipates: a fact merge collected
> duplicate inferences onto one survivor. The merged fact *"Corroboration is off
> by default…"* now `supports` three inferences, two of which state one claim in
> different words and previously rested on different premises. **So the
> shared-evidence population is no longer empty, and the measurement above no
> longer describes the graph it was taken on.** What it still describes correctly
> is the *global* sweep, which remains the wrong shape for the same three
> reasons. Building this now wants a fresh count of grouped candidates rather
> than a repeat of the pair census — the zero was a fact about a corpus with no
> merges in it, and there is no longer one.

Each candidate carries its advisory, computed before the agent decides:

```json
{
  "inferences": [{"id": "...", "content": "..."}, ...],
  "shared_premises": ["fact-id", ...],
  "similarity": 0.91,
  "warnings": [
    {"kind": "disjoint_premises",
     "message": "F1 (1997–2010) and F2 (from 2024) are not asserted to have held together",
     "detail": {...}}
  ]
}
```

### 6.2 The tool

`merge_inferences(source_ids, content)` — a sibling of `merge_facts`, refusing
on the same structural grounds and one fewer epistemic one:

| Gate | Carries over from `merge_facts`? |
|---|---|
| Two or more distinct nodes | Yes |
| All `ACTIVE` | Yes |
| Identical frame sets | Yes — the union problem is the same |
| Similarity nomination bar | Yes |
| `claim_kind` | **No** |

**There is no `claim_kind` analogue, and that is a decision rather than an
omission.** `claim_kind` exists because interval union is correct for a state
and fabricating for an event, and the union happens *mechanically* on the
`sourced_from` edges. The inference equivalent — whether combining premises is
legitimate — is not mechanical: the agent writes the merged claim, and the
question of tense and generality the user raised ("correct/update tenses and/or
generality") is answered *in that text*, not by a field. A stored judgment would
freeze at ingest what the merge itself decides.

Disjoint premises produce an advisory, not a refusal (§2).

### 6.3 What the survivor carries

As `merge_facts`: `merged_value_signal` for the value, `merged_from` in
metadata, sources retired `MERGED` with `merged_into` lineage, `derived_from`
edges migrated. Plus `proceeded_despite` when an advisory was in play.

---

## 7. Also to be done

- ~~**A fact merge should flag dependent inferences** (§1).~~ **Done
  2026-08-21.** The decision it needed went to a distinguishable flag:
  `plan_evidence_merged_edges` writes `EdgeType.EVIDENCE_MERGED` per source, and
  `merge_nodes_tx` carries them atomically on both backends. The flag names the
  fact that went away rather than the survivor, and there is no live-check half
  behind it — migration moves `derived_from` onto the survivor in the same
  transaction, so the flag is the only record the event leaves.
- **Similar-inference edges.** Reflect nominates near-identical inferences that
  do **not** share evidence and proposes `similarity` edges between them.
  Nothing enforces node types on `SIMILARITY` today — `link` checks only that
  both nodes exist — and retrieval already traverses it, so the expansion
  benefit arrives for free. **One consequence needs deciding first**:
  `corroboration` walks `SIMILARITY` neighbours, so inference-to-inference edges
  would make agreeing inferences corroborate each other. Defensible as
  independent support, but it is a live change to a number callers read, not a
  retrieval nicety.
  *Files*: `pipelines/reflection/` (a nominee list beside the existing four,
  capped as they are — #60), `pipelines/query/corroboration.py` for the
  consequence. **The collision with `ISSUES.md` #62 is cleared** — #62 shipped
  2026-08-21 — but it left the walk a shape worth reusing rather than a file
  merely free to edit: a `SIMILARITY` neighbour can now be *counted*, *excluded*
  (contradiction, variant, corrected) or *reported without counting*
  (`adjacent_periods`). Agreeing inferences most likely want the third, which
  turns the open decision above from a yes/no into a choice of which of three
  existing treatments applies.
- **Front-end surfacing.** Advisories should reach the dashboard log. The event
  bus emits at the five `_tx` boundaries (`EVENT_LOG.md`), and an advisory is not
  a transaction — so this needs either a new event kind or a deliberate decision
  to carry it on the act that triggered it.

---

## 8. Open questions

1. ~~**Does `reject` need to exist in the first version?**~~ **Answered
   2026-08-21: no.** `AdvisoryAction` ships with `proceed` and `flag` only. The
   member is not reserved-but-unimplemented either — a value nothing can produce
   is worse than no value at all, on `ValidityVerdict`'s grounds: a caller writes
   a branch for it and the branch is dead. It lands when something wants it,
   with the refusal path and its tests.
2. ~~**Does `proceeded_despite` survive a later supersession of the node?**~~
   **Answered 2026-08-21: it stays behind, and is not copied.** See §9 — the
   metadata stamp is replaced by a per-node note list, and notes are never
   migrated.
3. **Should a fact merge against an advisory be possible at all today?**
   `merge_facts` currently refuses rather than warns in every case. Nothing
   about it produces an advisory yet, so the general facility has exactly one
   producer until §7's work lands.

---

## 9. Node notes (decided 2026-08-21)

> **Superseded 2026-08-22 — `NodeNote` folds into `DecisionRecord`.** A day
> after this section was decided, `dev-docs/REVIEW_MODE.md` designed a decision
> journal whose rows also carry a judge, a date, a subject and review state.
> Review of that document found the two to be **two review-state machines with
> two "what has nobody looked at" scans**, where an agent proceeding past an
> advisory would write into both.
>
> **The user's decision: one machine.** A `NodeNote` becomes a
> `DecisionRecord(kind="proceeded_despite_advisory")`; `node.notes` becomes a
> derived view over records whose `subject_ids` contains the node; §5.3's
> `contested_decisions` becomes `review(mode="advisory", unreviewed=True)`. The
> mapping is REVIEW_MODE §8.
>
> **`reviewed_at` does not survive the fold**, and that is the substantive
> change rather than a rename. Review state is mutable, so it lives in exactly
> one place and is *derived* — a record is reviewed when another record points
> back at it. Storing it on the note as well would be two homes for one mutable
> fact across two backends, which is #54/#55/#56's shape.
>
> **§5.2's own argument is why this went the way it did**: two shapes for one
> question is how "the reviewing agent ends up unable to ask one question".
> What this section decided stands — three states, not a boolean; append-only;
> a feature rather than a flag. Only the type it lives on changed, and neither
> was built, so the cost is this paragraph rather than a migration.

§5.2 proposed a `proceeded_despite` key in `metadata`. That is replaced by this
section, which is a **feature to build rather than a flag to set** — the user's
direction, and the reason is that a boolean cannot express the three states that
matter: never looked at, looked at and upheld, looked at and fixed.

### 9.1 A second list, beside `lifecycle` and not inside it

Every node gains `notes: list[NodeNote]` — append-only, ordered, nothing ever
cleared or overwritten. The same discipline `lifecycle` already keeps, and
deliberately **not** the same list.

The two answer different questions. `lifecycle` answers *was this node in the
active set at time T*, and `events_in_window` maps each episode's `because`
straight into a `NodeChangeEvent.kind`. Put an entry in there that is not a
status and `query_changes` and `graph_as_of` either raise or answer wrongly —
and a silently wrong history is the worst failure available in the feature whose
whole job is history.

Readers wanting one timeline get it from a merge function over both lists, not
from one list carrying both meanings.

### 9.2 Kept narrow, on purpose

A note records **something that may need looking at later**, and nothing else.
Not retrievals, not importance judgments, not creation — those are either
already on the node (`retrieved_at`, `importance_judged_at`) or in the session
action log (`EVENT_LOG.md`), and duplicating them here would make this an
activity log competing with one that already exists.

```python
class NodeNote(BaseModel):
    raised_at: datetime
    kind: AdvisoryKind          # why it was raised
    message: str
    detail: dict = {}
    # Set when somebody looks. Absent means nobody has.
    reviewed_at: datetime | None = None
    verdict: str | None = None  # what they concluded, in their words
```

`reviewed_at is None` is the whole of "still needs looking at", so the
`contested_decisions` list in §5.3 is a scan for notes without it — not a flag
somebody has to remember to clear.

**"Unreviewed" describes the note, never the node.** The inference is finished;
the agent wrote it and did its best. What is outstanding is the request that
somebody check the decision behind it.

### 9.3 Notes are never migrated

A note stays on the node version it was written about. It is not copied onto a
correction, a world-change, or a merge survivor.

This is `migration_disposition`'s existing rule, not a new one: a judgment made
*about the old claim* must not be re-pointed at a claim nobody assessed. A note
saying "these premises were combined despite never holding together" is about
particular wording, and the replacement has different wording that the noter
never saw.

**Superseding a node is itself a review**, which is what makes leaving the note
behind safe rather than lossy. The agent read the old version, judged it
wanting, and wrote a replacement — the note asked for somebody to look, and
somebody looked. Whether the *new* version needs a note is that agent's
judgment, made with the new text in front of it, and it writes a fresh one if it
thinks so.

> **The third state has to be written down, or it is not one (2026-08-21).**
> §9's whole justification is that a boolean cannot express *never looked at* /
> *looked at and upheld* / *looked at and fixed* — but as described, "fixed by
> supersession" writes no `reviewed_at` and leaves the note on a node the
> active-only scan in §5.3 skips. It drops off the worklist, correctly, and in
> the historical record it is then indistinguishable from a note nobody ever
> read, which is exactly what §5.1 promises the graph can answer.
>
> **Two ways to close it, and this is the decision the first implementer
> makes.** Either the retirement *writes* the review — `supersede_node` stamps
> `reviewed_at` and a `verdict` naming the replacement on every open note it
> leaves behind, which makes the third state real at the cost of a write on a
> path that currently only reads notes — or the reader *derives* it, treating an
> open note on a node retired by supersession as reviewed-by-replacement, which
> costs nothing and depends on nobody forgetting the rule. Prefer the write: a
> derived rule with one reader today is how `evidence_merged` nearly went
> wrong (ISSUES.md #61), and a note is a record rather than a label.

The alternative — copying open notes forward — was rejected for the reason above
and for a second one: a copied note and its original can later disagree, and
then nothing says which is the record.
