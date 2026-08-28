# Advisories, warning settings, and inference merge

**Built 2026-08-28.** This was a design document written before any code, at the
user's direction; it is now the record of the decisions the code cannot state
for itself. Everything it described as *would* is *does*. What remains is the
reasoning that would otherwise be re-litigated, the two decisions that were
reversed while building, and the two pieces deliberately left unbuilt.

The reasoning that lives in code lives there and is not repeated here:
`epimemer/core/advisories.py` for the shape and vocabulary,
`epimemer/pipelines/reflection/inference_dedup.py` for the merge gate and
nomination, `epimemer/storage/protocol.py` for how a per-graph setting resolves.

---

## 1. What shipped

| Piece | Where |
|---|---|
| `Advisory`, `AdvisoryKind`, `AdvisoryAction`, `WarningPolicy` | `core/advisories.py` |
| `AdvisoryStance` and the total `ADVISORY_STANCE` map | `core/advisories.py` (added on review, §4) |
| Per-graph overrides and `resolve_warning_policy` | `storage/protocol.py`, both backends |
| `configure_warnings` | `mcp/tools.py`, `mcp/server.py` |
| `merge_inferences(source_ids, content)` | `mcp/tools.py` |
| `inference_merge_candidates` | `reflect`'s eleventh phase |
| `DecisionKind.PROCEEDED_DESPITE_ADVISORY` | `core/types.py` |
| `review(mode="advisory")` | `pipelines/review/modes.py` |

Three existing behaviours changed rather than being added to:

- `record_contradiction` and `record_variant` returned hand-written `warning`
  strings with no kind, no subjects, and nothing a setting could address. They
  now build `Advisory` instances. **The response keys stay** — `warning` is the
  first advisory's message and `notify_user` is *any advisory resolved to
  `flag`* — because both are documented in the agent guidance and in
  `INTEGRATION.md`.
- `record_variant` gains `notify_user`, which it did not have — but it reads
  `false` by default, so the tool keeps the quiet note it always had. What
  changed is that the quiet is now a policy a graph can override rather than a
  hard-coding.
- `review`'s `advisory` mode was **refused by name** until now, on the grounds
  that it selected a kind nothing wrote. The refusal removed itself, exactly as
  it said it would.

---

## 2. Why a warning and not a rule

An inference **is** its derivation, so merging two migrates both sets of
`derived_from` edges onto the survivor: A resting on `{F1}` and B on `{F2}`
becomes one node resting on `{F1, F2}`. Usually right — two pieces of evidence
for one conclusion. Wrong in exactly one **checkable** case: both premises are
dated and their asserted periods provably fall clear.

**The first draft of this analysis was wrong, and the correction is the whole
design.** It read the union as a *fabrication* — the merge inventing a
derivation nobody made — and concluded that inferences must never merge. That
assumes the merge silently preserves two separate arguments. It does not: **the
agent writes fresh content**, asserting one claim over the combined premises. If
those premises never held together, the resulting inference is *genuinely*
unsound and `find_unsound_inferences` is right to flag it.

So the mechanism is sound and the danger is a specific computable outcome, which
is the shape of thing a warning addresses and a rule does not:

- A rule would refuse. But the honest response to *these premises never held
  together* is often to narrow the merged claim's wording or period — which the
  agent can only do by writing content, which is what it is already doing.
  Refusing blocks a merge the agent could have fixed.
- Warning *after* arrives detached from the decision that caused it.
- Warning **before** hands the agent the one thing it cannot compute for itself,
  at the moment it is choosing what to write.

The advisory therefore rides along with the nomination as well as the response.
It is computable from the graph before anything is proposed, so a second agentic
round trip to deliver it would be latency bought for nothing.

---

## 3. Decisions worth keeping

**`proceed` is the default, and `reject` does not exist.** The advisory reaches
the agent *before* it decides; an agent that has been told why the merge is
questionable and has written its content accordingly is not a caller who needs
stopping. `reject` is not reserved-but-unimplemented either — a value nothing can
produce is worse than no value at all, because a caller writes a branch for it
and the branch is dead. It lands when something wants it, with its refusal path
and its tests.

**`SAME_FRAME_CONTRADICTION` defaults to `flag`, and that is compatibility
rather than preference.** `record_contradiction` has always returned
`notify_user` for a same-frame pair. Under an empty `by_kind` and a `proceed`
default, the same call would return `notify_user: False` — the key surviving
with its trigger quietly changed, which is the outcome the "keys stay" rule
exists to prevent. A user can still set that kind to `proceed`; the point is
that turning the notification off becomes somebody's decision rather than a side
effect of a representation change. Exempting the tool from policy entirely was
rejected: `notify_user` would then mean *policy said flag* on one tool and
*frames overlap* on another, and a caller could not tell which.

**Recording is unconditional; surfacing is the setting.** `surface` gates the
response and never the journal row, because a graph whose warnings were switched
off for a month should still answer *what was decided while nobody was looking*
— which is exactly when the question matters most.

**Per-kind resolution merges maps rather than replacing them.** A graph with an
opinion about one kind has not withdrawn the defaults for the others, and a map
override that silently drops unnamed keys is the same class of bug as a
field-by-field rebuild forgetting a field. This is the one place the setting
differs from the reflect threshold, and only because a threshold is a scalar.

**No environment variable.** Every other `ServerConfig` field has one; this does
not, because `by_kind` is a map and an env var is one string. A hand-rolled
parser would be a second syntax for a setting the tool already expresses
properly. A deployment that wants a different default constructs `ServerConfig`
with one.

**One review machine, not two.** The design's `NodeNote` — a typed, append-only
list on the node with its own `reviewed_at` — was folded into `DecisionRecord`
before either was built. Two review-state machines with two *what has nobody
looked at* scans would both have been written to by an agent proceeding past an
advisory, which is the "two shapes for one question" defect this document argued
against elsewhere in its own text. `reviewed_at` did not survive the fold, and
that is the substantive part rather than a rename: review state is mutable, so
it lives in exactly one place and is *derived* — a record is reviewed when
another record points back at it. What the note section decided still stands —
three states rather than a boolean, append-only, a feature rather than a flag —
only the type it lives on changed.

**One journal row per operation, not per advisory** — and only where an
advisory **objects**. The agent made one decision; splitting it invites acting
on it several times. The kinds and their messages go in `certainty_basis`, which
review already renders, so the reviewer sees what the decider was told without a
second store to keep in step. `certainty` stays blank, because nobody rated it.

**An explicitly named `flag` outranks the global mute.** `surface` is the
general statement and `by_kind` is the specific one, which is the same
precedence every `resolve_*` here keeps — so muting a graph does not withdraw an
escalation somebody asked for by name. A kind following `default_action` is not
named, and is silenced. Withdrawing a named escalation means setting that kind
to `proceed`, which is the only honest way to do it. Without this,
`notify_user: true` could arrive with no text to relay.

**No `claim_kind` analogue on the inference gate.** `claim_kind` exists because
interval union is mechanically right for a state and fabricating for an event,
and that union happens on the `sourced_from` edges without anybody's judgment.
Whether combining premises is legitimate is not mechanical: the agent answers it
in the text it writes, and a field stored at ingest would freeze what the merge
itself decides.

**Nomination is scoped to shared evidence.** Never a global sweep — it is the
case that actually arises, it is cheap, and a global sweep nominated nothing.
The three reasons are in `inference_dedup.py`'s header with the measurements.

---

## 4. The kind that was two kinds

**Found on review, 2026-08-29.** `SAME_FRAME_CONTRADICTION` was raised in two
situations that give opposite advice:

- on `record_contradiction`, it means *the tool was right, and this conflict
  wants a person*;
- on `record_variant`, it means *this is the wrong tool for two facts in one
  frame*.

One field needing "or" to describe it is a tell this codebase has caught several
times, and here it propagated: `proceeded_despite_advisory` inherited the
confusion, because *despite* is meaningful only where something argued against
the call — and a correct same-frame contradiction had nothing to proceed
against. Every one of them wrote a row, doubling the journal on the commonest
path and degrading exactly the review the kind exists for.

**The fix is a classification, not an exception.** `SAME_FRAME_VARIANT` is a
fourth kind, defaulting to `proceed`; `ADVISORY_STANCE` maps every kind to
`objects` or `escalates`; and the journal row follows the stance. Each kind now
has exactly one recorded consequence, which is the property the verdict taxonomy
is built on, and the two questions this had raised — *should the row be written
here?* and *should `record_variant` escalate?* — both fall out of the split with
no special case in either tool.

The map is **total** rather than a set of objecting kinds: a set makes absence
mean *escalates*, and silence quietly becoming a claim is what the frame
requirement exists to prevent. A test asserts both directions.

---

## 5. The two measurements that dated

**The live defect** this document opened with — a fact merge not flagging its
dependent inferences — was built the same day it was written, as its own edge
type `evidence_merged` deriving its own review label. Not `evidence_superseded`,
because archival nominates on `evidence_stale` and one shared label would have
every merge propose discarding its own dependents.

**"Zero pairs at the nomination bar"** was true and is not any more. Measured on
both real graphs: 123 active inferences, 5,053 pairs, zero above 0.80 (p50
0.16–0.24, p99 0.44–0.55, max 0.66). It was a fact about a corpus with **no
merges in it**, and the same day five `merge_facts` calls on the `memory` graph
produced exactly the population this feature exists for. The measurement still
describes the *global* sweep correctly, which is why that sweep was not built.

---

## 6. Still unbuilt

Both are in `PROPOSED_FEATURES.md` with their open decisions: **advisories on
the dashboard** (the event bus emits at transaction boundaries and an advisory
is not a transaction), and **similar-inference edges** for pairs that share no
premise (which would make agreeing inferences corroborate each other — a live
change to a number callers read).

A settings panel for `configure_warnings` is noted with the dashboard entry: it
is per graph and must say so, and *inherited* is a fourth visual state beside the
two actions, because a kind following the default is not the same as one
explicitly set to the same value.

---

## 7. One question left open

**Should a fact merge against an advisory be possible at all?** `merge_facts`
refuses rather than warns in every case, and nothing about it produces an
advisory. The facility is general and the question is whether any of those
refusals is really a warning wearing the wrong clothes. Nobody has needed it.
