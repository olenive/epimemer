# Reflection — the review loop

Ingestion is mechanical and cheap. Organisation is deliberate and, where it
matters, *agentic*. That is the "write fast, organize slow" rhythm, and `reflect`
is the slow half.

**`reflect` reads and never writes.** It scans the graph, nominates candidates,
and hands them back. Every change goes through `apply_reflection`, and the
judgment in between belongs to the agent — or, for the epistemically consequential
calls, to a human.

Design history: `dev-docs/REVIEW_EPISTEMIC.md`.

---

## 1. The principle: cheap recall, precise judgment

Embeddings are a good **candidate generator** — *these two facts are about the
same thing* — and a poor **judge** — *do they contradict, supersede, or coexist?*

So similarity nominates and an agent decides. Everything in this document follows
from that split, including the parts that look like restraint: `reflect` returns
pairs with their scores rather than verdicts, because a verdict computed from a
cosine number is a guess wearing a decision's clothes.

Two further principles shape it:

- **Nothing is destroyed; ambiguity is made visible.** Outdated and contested
  nodes stay `active` and retrievable, carrying a computed label so anything
  reading them knows. Archival is export, not delete.
- **Two-tier responsibility.** The agent handles the mechanical calls. It
  **escalates the consequential ones to the human** — genuine contradictions,
  anything crossing a frame boundary.

---

## 2. The verdict taxonomy

When a new claim is similar to an existing one, the agent classifies the pair.
Seven verdicts, and the value of the table is that each has exactly one recorded
consequence:

| Verdict | Meaning | Action |
|---|---|---|
| **redundant** | same claim restated | `merge_facts`; refused → record `similarity`, keep both |
| **supersedes** | the new one corrects the old — the old was **wrong** | `superseded_by`; old → `corrected` |
| **succeeds** | both true, over different periods — **the world moved** | `temporally_followed_by`; old → `historical`, restorable |
| **recurs** | a previously retired `historical` claim is true again | `restore` + a new `sourced_from` edge, one transaction |
| **contradicts** | conflicting, same frame, unclear which holds | record `contradiction`; resolve, or escalate |
| **cross-frame** | "conflict" only because the frames differ | not a conflict; both coexist; optionally `variant_of` |
| **compatible** | no conflict | nothing |

**`supersedes` and `succeeds` are the pair worth being careful about.** They are
not degrees of the same thing. `supersedes` asserts the old claim was never true;
`succeeds` asserts it was true and remains true of its period. Filing a change in
the world as an error is how a graph forgets its own history, so `because` is
required and has no default — if you cannot tell which happened, leave the pair
contested rather than guessing. See [VALIDITY.md](VALIDITY.md#5-the-world-changing-is-not-the-same-as-being-wrong).

There are two non-similarity triggers, and they are deliberately different
labels. When a fact is superseded, inferences derived from it become
**evidentially stale** and are flagged automatically. When a fact is *merged*,
its dependents are flagged **`evidence_merged`** instead: the claim under them
did not change, only the wording that states it and the documents behind it, so
what is wanted is a re-read rather than a re-derivation.

`redundant` routes into `merge_facts(source_ids, content)` (built 2026-08-21,
): one node keeping a `sourced_from` edge per
contributing document, so provenance becomes plural rather than being
overwritten. It refuses — with a reason — an **event** rather than a state, a
fact ingested without a `claim_kind`, a retired twin (that is `recurs`, and
`restore`), a pair not standing in exactly the same frames (that is
`record_variant`), a fact that has already been merged and un-merged
`merge_cycle_limit` times (default 2 — an oscillation, and the refusal asks you
to bring in the user rather than try again), and anything below the nomination
bar. Every refusal leaves the older action available and correct: *keep both,
joined by `similarity`*, which is what corroboration reads.

A merge that turns out to be wrong is undone with `reverse_merge(survivor_id)`:
the sources come back active with their own edges and the survivor is deleted,
leaving the graph as it was before. It refuses if anything has been added to the
survivor since — a contradiction, a tag, a verdict — because the delete would
take those with it. `configure_merge` reads and sets the two settings involved,
`merge_undo_depth` and `merge_cycle_limit`.

### Inferences merge too, and are warned rather than refused

`merge_inferences(source_ids, content)` is the sibling tool, and the population
it exists for is one that fact merges create: collapsing four near-identical
facts lands the four inferences drawn on them onto that one survivor, each
carrying an `evidence_merged` flag naming the wording it lost.

The gate is the same minus one rung. There is **no `claim_kind`**, and that is a
decision rather than a gap: `claim_kind` exists because interval union is
mechanically right for a state and fabricating for an event, whereas whether
combining premises is legitimate is answered in the text the agent writes. A
field stored at ingest would freeze what the merge itself decides.

What replaces it is a warning. An inference *is* its derivation, so the survivor
rests on the **union** of its sources' premises — a combination neither original
had. Usually that is two pieces of evidence for one conclusion. Where the two
premises are dated and provably fall clear of each other, it is a claim over
premises no source puts in one period, and `unsound_inferences` will say so on
the next reflect. That is not an argument against the merge: the agent writes
fresh content asserting one claim over both, so if the premises never held
together the result is *genuinely* unsound rather than falsely flagged. The
honest response is usually to narrow the merged wording or its period — which is
something the agent writes — so refusing would block a merge it could have
fixed. The advisory arrives instead, **with the nomination and in the response,
before the content is written**, which is the only moment at which it can change
the answer.

---

## 3. What `reflect` returns

Eleven phases, eleven keys. Each is a worklist, not a verdict:

| Key | Nominates | Applied via |
|---|---|---|
| `similar_pairs` | topics above the similarity threshold | `merges` or `parents` |
| `split_candidates` | topics whose material has high internal variance | `splits` |
| `enrichment_candidates` | thin descriptions with rich underlying material | `enrichments` |
| `contradictions` | same-frame active fact pairs above 0.80 — the one nomination bar, which `merge_facts` also gates on, so a pair listed here is mergeable | `record_contradiction`, then `supersessions`; or `similarities` where neither fits |
| `recurrences` | an active claim beside its own `historical` twin | `restore` |
| `unsound_inferences` | inferences whose premises no source puts in one period | agent judgment |
| `inference_merge_candidates` | near-identical active inferences resting on a shared premise, each with the advisory computed before you decide | `merge_inferences`, or `similarities` where they are two claims |
| `boundary_proposals` | where a succession lets a period close or open | `boundaries` |
| `pending_review` | active nodes already carrying review state | `supersessions`, `record_variant` |
| `archival_candidates` | nodes worth setting aside | `archivals`, `judgments` |
| `similar_relations` | likely-synonymous user relationship labels | `relation_verdicts` |

Two more keys are not worklists: `truncated` names any of the lists that hit
`max_nominations` and were cut, and `relation_pairs_suppressed` counts the
label pairs standing verdicts kept out of `similar_relations` — the suppression
is silent and permanent by design, so without the count an empty list on a
well-judged graph would be indistinguishable from a graph with nothing similar
in it.

**Four of the eleven are built out of pairs** — `similar_pairs`,
`contradictions`, `recurrences`, `similar_relations` — and pairs are quadratic
in the node set where every other list is linear in it. Those four are capped to
their highest-scoring `max_nominations` (200 by default), and a cut list is
named in `truncated` rather than silently shortened, because a caller otherwise
cannot tell an exhausted graph from a trimmed answer. **When a list is named
there, the move is to act on what came back and reflect again**, not to raise the
number: the remainder is the weakest end of the ranking, and a graph that dense
wants a different operation than a longer list.

The cap bounds the **response**, not `reflect`'s peak allocation — the scored
pairs still exist upstream. That is the scope the measurement asked for: real
corpora clear the 0.80 threshold at 0.0105%, which projects to ~3 MB at 10,000
facts, so the response was the thing worth bounding.

`inference_merge_candidates` is built out of pairs too and is deliberately
**not** among them, because it is not quadratic in the graph. It groups
inferences by the premises they rest on and compares only within a group, so the
bound is how many inferences hang off any one fact rather than how many
inferences exist. That shape is also why it exists at all: a global sweep over
all inference pairs was measured at **zero** nominations on both real graphs
(123 inferences, 5,053 pairs, max score 0.66 against a 0.80 bar), and the pairs
it did score highest shared vocabulary while saying different things. The
population worth reviewing is the one a **fact** merge creates — collapsing four
near-identical facts lands their four inferences on one survivor, each flagged
`evidence_merged`.

Two of them exist to keep a distinction that a single list would destroy:

- **`recurrences` is separate from `contradictions`** because a claim standing
  beside its own successor is not in conflict with it. Only *mixed* pairs
  qualify — two active facts are redundancy, two historical ones are both past.
- **Cross-frame pairs are dropped**, not reported. A high-similarity pair across
  disjoint metacontext frames is coexistence, and calling it a contradiction is
  the misreading metacontexts exist to prevent.

`unsound_inferences` and `boundary_proposals` are covered in
[VALIDITY.md](VALIDITY.md#7-what-reflect-does-with-validity). The disjointness
that makes an inference unsound is the same computation that produces an
`inference_merge_candidate`'s advisory — one asked of an inference that exists,
the other of one that would.

### When it runs

Never on its own. `configure_reflection` sets a per-graph threshold on stores;
once it is crossed, `store_decomposition` **flags a suggestion** in its response
and `graph_stats` reports the pressure. Nothing schedules a reflect, and nothing
triggers one — the suggestion is information for the agent, and running it stays
a deliberate act. That is the "organize slow" half of the rhythm holding its
shape: an automatic reflect on a timer would be a consolidation nobody asked for
over a graph nobody was looking at.

---

## 4. Review labels

Three labels are **computed at read time** from edges and never stored, so they
cannot freeze against a graph that has moved on. They ride on `search` and
`query_graph` results:

| Label | Means |
|---|---|
| `superseded_candidate` | something has been nominated as replacing this |
| `evidence_stale` | *(inferences only)* a fact this was derived from has been superseded |
| `evidence_merged` | *(inferences only)* a fact this rests on absorbed another claim; the ids are the phrasings that went away |
| `contested` | this has a `contradiction` edge to a live, same-frame node |

`contested` resolves itself when the partner is retired — the label is derived, so
there is nothing to clean up.

**`evidence_merged` is not a weaker `evidence_stale`, and the difference is
visible in §5**: staleness is an archival class, and a merged premise is not.
Nothing was overturned — the premise gained provenance — so proposing to discard
what rests on it would have every merge nominate its own dependents.

---

## 5. Archival: the hygiene arm

Archival is the same loop with a fourth outcome — *triviality* — rather than a
separate subsystem. `nominate_archival_candidates` proposes, worst first, in four
classes:

| Class | What it catches |
|---|---|
| `retired` | corrected/merged nodes past `max_age_days`, not judged important |
| `evidence_stale` | active inferences whose basis changed, or whose whole evidence set has been archived |
| `never_retrieved` | active facts never returned by a search, not judged important, with nothing depending on them |
| `stale_judgment` | nodes held above the importance ceiling by a judgment nobody has revisited |

**`historical` nodes are never nominated.** They were retired because the world
changed, not because they were wrong, so they remain true of their period and age
alone is not grounds to discard them.

`stale_judgment` is the class that keeps the others honest. Importance protects a
node from every class above it, so without this an assessment that has since
expired would protect a node forever and the cheap tier would never look at it
again. Its resolution is `judgments` — *"keep it, and stop treating it as
important"*, or *"still important, and now recently confirmed"*. Either verdict
moves the clock, so the node leaves the stale set either way.

The `importance_ceiling` is **inclusive** of the default: an un-judged node is not
a node judged worth keeping, and nomination is a proposal rather than a verdict.

Archival itself is an **export**: `apply_reflection(archivals=[…])` returns
`archive_data` — keep it, that copy *is* the archive — and atomically flips the
nodes to `archived`, which removes them from every active-status query. Nothing is
deleted, and `restore` reverses it.

---

## 6. What `apply_reflection` writes

Ten kinds of decision, all optional, applied in one call:

| Argument | Effect |
|---|---|
| `similarities` | record what you decided about a nominated pair |
| `parents` | synthesise a parent topic over children |
| `splits` | split a broad topic into subtopics |
| `enrichments` | replace a thin topic description |
| `merges` | fuse near-duplicate topics into one |
| `supersessions` | resolve a flagged node against an existing one |
| `archivals` | export and retire approved nominees |
| `judgments` | re-judge importance, in either direction, with a reason |
| `relation_verdicts` | record what you decided about a nominated label pair |
| `boundaries` | fill in one open endpoint of one source's period |

**`merges` is the one consolidation that retires nodes from the active graph**, so
the bar is deliberately high: a merge is applied only if *every* pair of sources
clears `merge_similarity_threshold` (0.92), and otherwise it is rejected and
reported. For topics that are merely related rather than duplicates, use
`parents`.

**Three of these carry a frame, and none of them may invent one.** A split's
subtopics inherit what the parent states; a synthesised parent inherits the one
set its children all stand in, and is refused into `parents_refused` when they
differ; a topic merge is refused into `topic_merges_refused` unless every source
stands in exactly the same set. Union is never the answer: one node asserted in
two worlds is the worst outcome available, which is the rule `merge_facts` has
applied since fact dedup shipped.

**A merge re-states the survivor's frame rather than migrating one.** Every
other edge on a survivor is something its sources genuinely brought with them,
but a frame is a claim about which world this is — and the survivor's content is
*synthesised*, so nobody has yet said which world the synthesised wording is
about. Migrating the edge would answer for them and credit whoever framed a
source; the merging agent states it under its own judge instead. Merging is not
coining, one layer up from `describe_relation`'s version of the same rule. A
correction still moves the frame, where the replacement is the same claim.

Where the sources state no frame, nothing is re-stated — there is nothing to
restate, and inventing one would put words in a nobody's mouth. Splits behave
the same way. A node with no `has_metacontext` edge is one nobody spoke for, and
`epimemer frames declare` is how a person ends that state.

Merging rebuilds the node's value signal through one shared function
(`merged_value_signal`) rather than field by field. A field-by-field rebuild
silently resets whatever it forgets to name — and the specific thing it forgot was
`importance_judged_at`, which made every merged node permanently exempt from the
`stale_judgment` class.

Unknown or already-retired ids are **skipped**, not errors. Refused boundaries
come back in `boundaries_refused` with a reason.

### A batch applies, or it never existed

The ten steps share no transaction, and cannot: their order is load-bearing, so
judgments are recorded before the steps that retire the nodes those judgments
name. The guarantee comes from the other end instead — **every entry is checked
before the first step writes**, and a batch containing one that cannot be
applied at all is refused whole, with nothing written and every problem listed
at once.

This matters because the alternative was worse than it looked. An entry missing
a required key used to raise part-way down, leaving everything above it
committed under a response that said the call had failed and could not say what
had landed. Where what landed was a `similarities` or `relation_verdicts` entry
— both **permanently suppressing** — the obvious next move, fixing the entry and
resending the batch, was then refused as a repeat verdict on the pairs that had
gone through.

The check is about *shape*, not judgment: a missing key, an entry that is not an
object, a `pair` that does not name two, a supersession reason outside the
closed set, an unparseable date. Whether an entry *should* apply stays where it
was — an unknown id is skipped, and a judgment the graph can evaluate and reject
comes back in its own `*_refused` list. One already-judged pair never costs a
batch.

### `similarities` — the verdict that used to have no writer

Six of `reflect`'s seven verdicts always had an action. The seventh —
*compatible*, these merely look alike — had none, so a decline was recorded
nowhere and the same pair was nominated again on every pass. Measured
2026-08-21: of eighteen pairs nominated on one real graph, five merged and
**thirteen were declined and came straight back.**

Each entry is `{pair: [a_id, b_id], verdict, because}`, and the verdict picks
what is written:

| Verdict | Use it when | Writes |
|---|---|---|
| `one_claim` | the two really do say the same thing and something blocked the merge — an event, or an unjudged `claim_kind` | `similarity` **and** `assessed` |
| `distinct` | they are different claims that merely look alike | `assessed` only |
| `distinct`, over a pair you earlier called `one_claim` | you got it wrong and want the count back | `retracted_similarity` + `assessed` |

**Both stop the pair being nominated; only `one_claim` corroborates.** That split
is the whole design. A decline is two populations, and one edge cannot serve
both readers: the nomination sweep wants every judged pair suppressed, while
`corroboration.py` wants only restatements of one claim. Record a decline as a
`similarity` and *"these are different claims"* starts counting as a second
source — manufactured support, which is the failure this system treats as its
worst, since a false unification does not lose information, it inverts the
quantity corroboration measures. So reach for `one_claim` only where you would
have merged.

**A `one_claim` can be withdrawn, once.** Recording `distinct` over a pair you
earlier called one claim retracts that verdict: the pair stops corroborating and
the count returns to what it would have been. The `similarity` edge is not
removed — nothing in this system deletes — so the withdrawal is a second edge
that disqualifies the first, which is how a `contradiction` between two facts
has always stopped them counting as support for each other.

**The withdrawal is final**, and the asymmetry is deliberate: nothing re-asserts
`one_claim` afterwards. Withdrawing costs a count the graph will no longer make;
re-asserting invents agreement, and invented agreement does not lose information
— it inverts the quantity corroboration measures. If the pair really is one
claim, `merge_facts` is the call that says so.

Suppression is untouched either way. The pair has now been judged twice and
stays out of every future nomination.

`because` is required. Anything not recorded comes back in
`similarities_refused` with a reason rather than being applied to something
adjacent — a cross-frame pair wants `record_variant`.

Similarities are applied **first** in the call, before any argument that can
retire a node. A judgment is about the wording it was made against, so a
supersession later in the same batch must not turn it into a skip.

**`relation_verdicts` is the same machinery one tier down**, for label pairs
from `similar_relations`: `{pair: [label_a, label_b], kind, verdict:
"distinct" | "synonymous", because}`, with `kind` copied from the nomination
and `because` required. Both verdicts suppress the pair from every future
nomination, permanently — so judge the pair rather than clearing the list.
What was decided is read back on `list_relations`, where each label carries
its standing verdicts, and `reflect` counts what suppression held back in
`relation_pairs_suppressed`. Refusals come back in
`relation_verdicts_refused`, applied at step 1b for the same anchoring reason
as `similarities`.

---

## 7. What reflect deliberately does not do

- **It does not judge succession.** Boundary proposals are drawn from a
  `temporally_followed_by` edge the agent already wrote. Guessing that two similar
  facts are successive is exactly the judgment the taxonomy reserves for the
  agent.
- **It does not write value signals.** It reads `importance`, `confidence` and the
  two clocks to nominate; only `judge_importance` moves importance, and only
  ingest supplies confidence. A decayed judgment would be a number nobody stands
  behind.
- **It does not resolve contradictions.** It surfaces them. Resolution is a
  separate, explicit act, and the genuinely contested ones go to a human.
- **It does not block anything.** The soundness check flags; ingest still stores.

---

## 8. Cost, and the one real limit

`reflect` is the slowest operation in the system and the only one that fails at a
size real use reaches. Against the 30 s default tool timeout it crosses at roughly
**320,000 nodes in-memory and 26,000 on SurrealDB**.

**That is a time limit, and there is a separate memory one.** Candidate pairs are
quadratic and nothing caps how many survive — about 580 bytes per surviving pair —
so on a corpus of genuinely similar documents `reflect` can want gigabytes at
~10,000 facts, *below* the timeout crossing. The benchmark corpus produces almost
no surviving pairs, so it cannot show this. Measurements and options:
`dev-docs/BENCHMARKS.md`.

Practical consequence: run `reflect` deliberately, on a graph you know the size
of, rather than on a schedule against an unbounded one.
