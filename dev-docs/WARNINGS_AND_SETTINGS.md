# Advisories, warning settings, and inference merge

An advisory is a typed message a tool attaches to its response when the graph
can compute something the agent cannot: that two premises never held together,
that a contradiction was recorded across metacontexts, and so on. A warning policy,
per process with per-graph overrides, decides which advisories are surfaced and
whether they ask for a person. This document holds the reasoning behind that
design. The mechanics live in code and are not repeated here:
`epimemer/core/advisories.py` for the shape and vocabulary,
`epimemer/pipelines/reflection/inference_dedup.py` for the merge gate and
nomination, `epimemer/storage/protocol.py` for how a per-graph setting resolves.

---

## 1. The pieces

| Piece | Where |
|---|---|
| `Advisory`, `AdvisoryKind`, `AdvisoryAction`, `WarningPolicy` | `core/advisories.py` |
| `AdvisoryStance` and the total `ADVISORY_STANCE` map | `core/advisories.py` |
| Per-graph overrides and `resolve_warning_policy` | `storage/protocol.py`, both backends |
| `configure_warnings` | `mcp/tools.py`, `mcp/server.py` |
| `merge_inferences(source_ids, content)` | `mcp/tools.py` |
| `inference_merge_candidates` | a `reflect` phase, in `REFLECT_PHASES` |
| `DecisionKind.PROCEEDED_DESPITE_ADVISORY` | `core/types.py` |
| `review(mode="advisory")` | `pipelines/review/modes.py` |

The response keys `warning` and `notify_user` predate advisories and keep
their meaning: `warning` is the
first surfaced advisory's message, and `notify_user` is true when any surfaced
advisory resolves to `flag`. Both are documented in the agent guidance and in
`INTEGRATION.md`, so their meaning is fixed even though what produces them is
now a policy.

---

## 2. Why a warning and not a rule

An inference is its derivation, so merging two migrates both sets of
`derived_from` edges onto the survivor: A resting on `{F1}` and B on `{F2}`
becomes one node resting on `{F1, F2}`. Usually that is right, two pieces of
evidence for one conclusion. It is wrong in exactly one checkable case: both
premises are dated and their asserted periods provably fall clear.

It is tempting to read the union as fabrication, a derivation nobody made, and
conclude that inferences must never merge. That reading assumes the merge
silently preserves two separate arguments. It does not: the agent writes fresh
content, asserting one claim over the combined premises. If those premises never
held together, the resulting inference is genuinely unsound and
`find_unsound_inferences` is right to flag it.

So the mechanism is sound and the danger is a specific computable outcome, which
is the shape of thing a warning addresses and a rule does not:

- A rule would refuse. But the honest response to *these premises never held
  together* is often to narrow the merged claim's wording or period, which the
  agent can only do by writing content, which is what it is already doing.
  Refusing blocks a merge the agent could have fixed.
- Warning after the fact arrives detached from the decision that caused it.
- Warning before hands the agent the one thing it cannot compute for itself, at
  the moment it is choosing what to write.

The advisory therefore rides along with the nomination as well as the response.
It is computable from the graph before anything is proposed, so a second round
trip to deliver it would be latency bought for nothing.

---

## 3. The four kinds and their stance

`AdvisoryKind` is a closed vocabulary because the reviewing agent groups and
sorts on it, and re-parsing sentences is how that rots. Every kind has a writer,
on `DecisionKind`'s rule: a kind nothing produces is a filter that returns
nothing and reads as a clean graph.

| Kind | Raised by | Stance |
|---|---|---|
| `disjoint_premises` | inference-merge nomination and `merge_inferences` | objects |
| `cross_metacontext` | `record_contradiction` across metacontexts | objects |
| `same_metacontext_variant` | `record_variant` within one metacontext | objects |
| `same_metacontext_contradiction` | `record_contradiction` within one metacontext | escalates |

`ADVISORY_STANCE` says whether a kind argues with the call (*objects*) or
reports that the call was right and the result wants a person (*escalates*).
`same_metacontext_contradiction` is the only escalating kind: a real conflict in one
world is exactly what is worth putting to a person, and the tool that recorded
it was the right tool.

The stance decides the journal row. `proceeded_despite_advisory` is written only
where an advisory objects, because *despite* means something argued against the
call. Without the split, a correct same-metacontext contradiction would write a row
claiming the agent had overridden advice it never received, doubling the journal
on the commonest path and degrading the review the kind exists for. Two
situations that give opposite advice are two kinds, not one kind with an "or" in
its description.

The map is total rather than a set of objecting kinds: a set makes absence mean
*escalates*, and silence quietly becoming a claim is what the metacontext requirement
exists to prevent. A test asserts both directions.

---

## 4. Policy decisions and their reasons

**`proceed` is the default, and `reject` does not exist.** The advisory reaches
the agent before it decides; an agent that has been told why the merge is
questionable and has written its content accordingly is not a caller who needs
stopping. `reject` is not reserved-but-unimplemented either: a value nothing can
produce is worse than no value at all, because a caller writes a branch for it
and the branch is dead. It lands when something wants it, with its refusal path
and its tests.

**`same_metacontext_contradiction` defaults to `flag`, for compatibility.**
`record_contradiction` has always returned `notify_user` for a same-metacontext pair.
Under an empty `by_kind` and a `proceed` default, the same call would return
`notify_user: false`, the key surviving with its trigger quietly changed. A user
can still set that kind to `proceed`; the point is that turning the notification
off is somebody's decision rather than a side effect of the representation.
Exempting the tool from policy entirely was rejected: `notify_user` would then
mean *policy said flag* on one tool and *metacontexts overlap* on another, and a
caller could not tell which.

**Recording is unconditional; surfacing is the setting.** `surface` gates the
response and never the journal row, because a graph whose warnings were switched
off for a month should still answer *what was decided while nobody was looking*,
which is exactly when the question matters most.

**An explicitly named `flag` outranks the global mute.** `surface` is the
general statement and `by_kind` is the specific one, the same precedence every
`resolve_*` keeps, so muting a graph does not withdraw an escalation somebody
asked for by name. A kind following `default_action` is not named, and is
silenced. Withdrawing a named escalation means setting that kind to `proceed`.
Without this rule, `notify_user: true` could arrive with no text to relay.

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

**One review machine, not two.** Review state lives on `DecisionRecord` only,
and it is derived: a record is reviewed when another record points back at it.
A separate typed note list on the node, with its own `reviewed_at`, was
rejected because an agent proceeding past an advisory would have written to
both, and two *what has nobody looked at* scans is two shapes for one question.

**One journal row per operation, not per advisory.** The agent made one
decision; splitting it invites acting on it several times. The kinds and their
messages go in `certainty_basis`, which review already renders, so the reviewer
sees what the decider was told without a second store to keep in step.
`certainty` stays blank, because nobody rated it.

**No `claim_kind` analogue on the inference gate.** `claim_kind` exists because
interval union is mechanically right for a state and fabricating for an event,
and that union happens on the `sourced_from` edges without anybody's judgment.
Whether combining premises is legitimate is not mechanical: the agent answers it
in the text it writes, and a field stored at ingest would freeze what the merge
itself decides.

**Nomination is scoped to shared evidence, never a global sweep.** Shared
evidence is the case that actually arises once facts have been merged, and it
is cheap. A global sweep over all inference pairs was measured on two real
graphs (123 active inferences, 5,053 pairs) and found nothing above the 0.80
bar: p99 similarity sat between 0.44 and 0.55. The reasoning is in
`inference_dedup.py`'s header with the measurements.

---

## 5. Not built

Both are in `PROPOSED_FEATURES.md` with their open decisions: **advisories on
the dashboard** (the event bus emits at transaction boundaries and an advisory
is not a transaction), and **similar-inference edges** for pairs that share no
premise (which would make agreeing inferences corroborate each other, a live
change to a number callers read).

A settings panel for `configure_warnings` is noted with the dashboard entry: it
is per graph and must say so, and *inherited* is a fourth visual state beside
the two actions, because a kind following the default is not the same as one
explicitly set to the same value.

---

## 6. One question left open

**Should a fact merge against an advisory be possible at all?** `merge_facts`
refuses rather than warns in every case, and nothing about it produces an
advisory. The facility is general and the question is whether any of those
refusals is really a warning wearing the wrong clothes. Nobody has needed it.
