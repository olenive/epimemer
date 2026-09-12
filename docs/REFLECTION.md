# Reflection: the review loop

Ingestion is mechanical and cheap. Organisation is deliberate and, where it
matters, done by an agent. That is the "write fast, organize slow" rhythm,
and `reflect` is the slow half.

**`reflect` reads and never writes.** It scans the graph, nominates
candidates, and hands them back. Every change goes through
`apply_reflection`, and the judgment in between belongs to the agent, or, for
the consequential calls, to a human.

Design notes: `dev-docs/REVIEW_EPISTEMIC.md`.

---

## 1. The principle: cheap recall, precise judgment

Embeddings are a good **candidate generator** (*these two facts are about the
same thing*) and a poor **judge** (*do they contradict, supersede, or
coexist?*).

So similarity nominates and an agent decides. Everything in this document
follows from that split, including the parts that look like restraint:
`reflect` returns pairs with their scores rather than verdicts, because a
verdict computed from a cosine number is a guess dressed as a decision.

Two further principles shape it:

- **Nothing is destroyed; ambiguity is made visible.** Outdated and contested
  nodes stay `active` and retrievable, carrying a computed label so anything
  reading them knows. Archival is export, not delete.
- **Two-tier responsibility.** The agent handles the mechanical calls. It
  **escalates the consequential ones to the human**: genuine contradictions,
  and anything crossing a metacontext boundary.

---

## 2. The verdict taxonomy

When a new claim is similar to an existing one, the agent classifies the
pair. Seven verdicts, and the value of the table is that each has exactly one
recorded consequence:

| Verdict | Meaning | Action |
|---|---|---|
| **redundant** | same claim restated | `merge_facts`; if refused, record `similarity` and keep both |
| **supersedes** | the new one corrects the old: the old was **wrong** | `superseded_by`; old becomes `corrected` |
| **succeeds** | both true, over different periods: **the world moved** | `temporally_followed_by`; old becomes `historical`, restorable |
| **recurs** | a previously retired `historical` claim is true again | `restore` plus a new `sourced_from` edge, one transaction |
| **contradicts** | conflicting, same metacontext, unclear which holds | record `contradiction`; resolve, or escalate |
| **cross-metacontext** | a "conflict" only because the metacontexts differ | not a conflict; both coexist; optionally `variant_of` |
| **compatible** | no conflict | nothing |

**`supersedes` and `succeeds` are the pair to be careful about.** They are
not degrees of the same thing. `supersedes` asserts the old claim was never
true; `succeeds` asserts it was true and remains true of its period. Filing a
change in the world as an error is how a graph forgets its own history, so
`because` is required and has no default. If you cannot tell which happened,
leave the pair contested rather than guessing. See
[VALIDITY.md](VALIDITY.md#5-the-world-changing-is-not-the-same-as-being-wrong).

There are two non-similarity triggers, and they are deliberately different
labels. When a fact is superseded, inferences derived from it become
**evidentially stale** and are flagged automatically. When a fact is
*merged*, its dependents are flagged **`evidence_merged`** instead: the claim
under them did not change, only the wording that states it and the documents
behind it, so what is wanted is a re-read rather than a re-derivation. Either
re-read is recorded with `apply_reflection(retained=…)` (§6).

`redundant` routes into `merge_facts(source_ids, content)`: one node keeping
a `sourced_from` edge per contributing document, so provenance becomes plural
rather than being overwritten. It refuses, with a reason: an **event** rather
than a state; a fact ingested without a `claim_kind`; a retired twin (that is
`recurs`, and `restore`); a pair not standing in exactly the same metacontexts
(that is `record_variant`); a fact that has already been merged and un-merged
`merge_cycle_limit` times (default 2: an oscillation, and the refusal asks
you to bring in the user rather than try again); and anything below the
nomination bar. Every refusal leaves the older action available and correct:
*keep both, joined by `similarity`*, which is what corroboration reads.

A merge that turns out to be wrong is undone with
`reverse_merge(survivor_id)`: the sources come back active with their own
edges and the survivor is deleted, leaving the graph as it was before. It
refuses if anything has been added to the survivor since (a contradiction, a
tag, a verdict), because the delete would take those with it.
`configure_merge` reads and sets the two settings involved,
`merge_undo_depth` and `merge_cycle_limit`.

### Inferences merge too, and are warned rather than refused

`merge_inferences(source_ids, content)` is the sibling tool, and the
population it exists for is one that fact merges create: collapsing four
near-identical facts lands the four inferences drawn on them onto that one
survivor, each carrying an `evidence_merged` flag naming the wording it lost.

The gate is the same minus one rung. There is **no `claim_kind`**, and that
is a decision rather than a gap: `claim_kind` exists because interval union
is mechanically right for a state and fabricated for an event, whereas
whether combining premises is legitimate is answered in the text the agent
writes. A field stored at ingest would freeze what the merge itself decides.

What replaces it is a warning. An inference *is* its derivation, so the
survivor rests on the **union** of its sources' premises, a combination
neither original had. Usually that is two pieces of evidence for one
conclusion. Where the two premises are dated and provably fall clear of each
other, it is a claim over premises no source puts in one period, and
`unsound_inferences` will say so on the next reflect. That is not an argument
against the merge: the agent writes fresh content asserting one claim over
both, so if the premises never held together the result is *genuinely*
unsound rather than falsely flagged. The honest response is usually to narrow
the merged wording or its period, which the agent writes, so refusing would
block a merge it could have fixed. The advisory arrives instead, **with the
nomination and in the response, before the content is written**, which is
the only moment at which it can change the answer.

Going ahead past it is recorded: a `proceeded_despite_advisory` row naming
the survivor and its sources, written whether or not the graph is set to
*show* advisories, and read back by `review(mode="advisory")`. That is what
makes proceeding cost something. It applies to advisories that **object**;
one that merely escalates a correct call (a same-metacontext contradiction is the
only kind that does) sets `notify_user` and journals nothing, because there
was nothing to proceed against.

---

## 3. What `reflect` returns

One key per phase, and `REFLECT_PHASES` names them in execution order. Each
is a worklist, not a verdict:

| Key | Nominates | Applied via |
|---|---|---|
| `similar_pairs` | topics above the similarity threshold | `merges` or `parents` |
| `split_candidates` | topics whose material bisects into two clusters, skipping any somebody stood behind since its material last moved | `splits` or `splits_declined` |
| `enrichment_candidates` | topics whose material moved since somebody last stood behind the description, with the change itself; and topics nobody has described yet, with a sample | `enrichments` or `descriptions_confirmed` |
| `contradictions` | same-metacontext active fact pairs above 0.80, the one nomination bar, which `merge_facts` also gates on, so a pair listed here is mergeable | `record_contradiction`, then `supersessions`; or `similarities` where neither fits |
| `recurrences` | an active claim beside its own `historical` twin | `restore` |
| `unsound_inferences` | inferences whose premises no source puts in one period | agent judgment |
| `inference_merge_candidates` | near-identical active inferences resting on a shared premise, each with the advisory computed before you decide | `merge_inferences`, or `similarities` where they are two claims |
| `boundary_proposals` | where a succession lets a period close or open | `boundaries` |
| `pending_review` | active nodes already carrying review state | `supersessions`, `record_variant`, `retained` |
| `archival_candidates` | nodes worth setting aside | `archivals`, `judgments`, `retained` |
| `similar_relations` | likely-synonymous user relationship labels | `relation_verdicts` |

Two more keys are not worklists. `truncated` names any of the lists that hit
`max_nominations` and were cut. `relation_pairs_suppressed` counts the label
pairs that standing verdicts kept out of `similar_relations`; the suppression
is silent and permanent by design, so without the count an empty list on a
well-judged graph would look the same as a graph with nothing similar in it.

**The lists built out of pairs are capped**, and `CAPPED_KEYS` says which:
pairs grow faster than the node set where every other list is linear in it.
Each is cut to its highest-scoring `max_nominations` (200 by default), and a
cut list is named in `truncated` rather than silently shortened, because a
caller otherwise cannot tell an exhausted graph from a trimmed answer. **When
a list is named there, act on what came back and reflect again** rather than
raising the number: the remainder is the weakest end of the ranking, and a
graph that dense wants a different operation than a longer list.

The cap bounds the **response**, not `reflect`'s peak allocation: the scored
pairs still exist upstream (§8).

`inference_merge_candidates` groups inferences by the premises they rest on
and compares only within a group, so what bounds it is how many inferences
hang off any one fact, a much lower ceiling than the node set. It is capped
anyway: *every pair-built list is capped* is a simpler rule to hold than
*capped except where a grouping argument says otherwise*, and the ceiling
rises in exactly the graphs this list is for, since a heavily merged graph
concentrates inferences onto surviving premises. The grouping is also why the
list is useful: a sweep over all inference pairs mostly finds pairs that
share vocabulary while saying different things, whereas the inferences that
land on one fact-merge survivor are the ones worth comparing.

Two of the lists exist to keep a distinction that a single one would
destroy:

- **`recurrences` is separate from `contradictions`** because a claim
  standing beside its own successor is not in conflict with it. Only *mixed*
  pairs qualify: two active facts are redundancy, two historical ones are
  both past.
- **Cross-metacontext pairs are dropped**, not reported. A high-similarity pair
  across disjoint metacontexts is coexistence, and calling it a
  contradiction is the misreading metacontexts exist to prevent.

`unsound_inferences` and `boundary_proposals` are covered in
[VALIDITY.md](VALIDITY.md#7-what-reflect-does-with-validity). The
disjointness that makes an inference unsound is the same computation that
produces an `inference_merge_candidate`'s advisory, one asked of an inference
that exists, the other of one that would.

### When it runs

Never on its own. `configure_reflection` sets a per-graph threshold on
stores; once it is crossed, `store_decomposition` **flags a suggestion** in
its response and `graph_stats` reports the pressure. Nothing schedules a
reflect, and nothing triggers one. The suggestion is information for the
agent, and running it stays a deliberate act: an automatic reflect on a timer
would be a consolidation nobody asked for over a graph nobody was looking at.

---

## 4. Review labels

Four labels are **computed at read time** from edges and never stored, so
they cannot freeze against a graph that has moved on. They ride on `search`
and `query_graph` results:

| Label | Means |
|---|---|
| `superseded_candidate` | something has been nominated as replacing this |
| `evidence_stale` | *(inferences only)* a fact this was derived from has been superseded |
| `evidence_merged` | *(inferences only)* a fact this rests on absorbed another claim; the ids are the phrasings that went away |
| `contested` | this has a `contradiction` edge to a live, same-metacontext node |

`contested` resolves itself when the partner is retired. The label is
derived, so there is nothing to clean up.

**`evidence_merged` is not a weaker `evidence_stale`, and the difference
shows in §5**: staleness is an archival class, and a merged premise is not.
Nothing was overturned, since the premise gained provenance, so proposing to
discard what rests on it would have every merge nominate its own dependents.

---

## 5. Archival: the hygiene arm

Archival is the same loop with a fourth outcome, *triviality*, rather than a
separate subsystem. `nominate_archival_candidates` proposes, worst first, in
four classes:

| Class | What it catches |
|---|---|
| `retired` | corrected or merged nodes past `max_age_days`, not judged important |
| `evidence_stale` | active inferences whose basis changed, or whose whole evidence set has been archived |
| `never_retrieved` | active facts never returned by a search, not judged important, with nothing depending on them |
| `stale_judgment` | nodes held above the importance ceiling by a judgment nobody has revisited |

**`historical` nodes are never nominated.** They were retired because the
world changed, not because they were wrong, so they remain true of their
period and age alone is not grounds to discard them.

`stale_judgment` is the class that keeps the others honest. Importance
protects a node from every class above it, so without this an assessment
that has since expired would protect a node for ever and the cheap tier
would never look at it again. Its resolution is `judgments`: *"keep it, and
stop treating it as important"*, or *"still important, and now recently
confirmed"*. Either verdict moves the clock, so the node leaves the stale set
either way.

The `importance_ceiling` is **inclusive** of the default: an un-judged node is
not a node judged worth keeping, and nomination is a proposal rather than a
verdict.

Archival itself is an **export**: `apply_reflection(archivals=[…])` returns
`archive_data` (keep it; that copy *is* the archive) and atomically flips the
nodes to `archived`, which removes them from every active-status query.
Nothing is deleted, and `restore` reverses it.

---

## 6. What `apply_reflection` writes

Every kind of decision is optional and they are applied in one call:

| Argument | Effect |
|---|---|
| `similarities` | record what you decided about a nominated pair |
| `parents` | synthesise a parent topic over children |
| `splits` | split a broad topic into subtopics |
| `splits_declined` | record that you read the material and it is one topic |
| `enrichments` | describe a topic, beside its unchanged name |
| `descriptions_confirmed` | record that you read a description against the change and it stands |
| `merges` | fuse near-duplicate topics into one |
| `supersessions` | resolve a flagged node against an existing one |
| `retained` | record that you re-read a nominated node and it stands |
| `archivals` | export and retire approved nominees |
| `judgments` | re-judge importance, in either direction, with a reason |
| `relation_verdicts` | record what you decided about a nominated label pair |
| `boundaries` | fill in one open endpoint of one source's period |

**`merges` is the one consolidation that retires nodes from the active
graph**, so the bar is deliberately high: a merge is applied only if *every*
pair of sources clears 0.92, and otherwise it is rejected and reported. **The
bar is not a parameter of the call**: a caller does not choose the bar its
own merge is checked against, here or in `merge_facts`. For topics that are
merely related rather than duplicates, use `parents`.

**`enrichments` writes a description beside a name that does not change.** Each
entry is `{topic_id, description}`, and the write is in place: the topic keeps
its id, its content byte for byte and every edge it holds. That matters because
`content` is the name a tag resolves to at ingest and the name
`find_nodes(tagged_with_topic=...)` resolves back, and an enrichment that moved
it split the tag in two. It is most worth doing for a topic node created from a
tag, whose bare name says nothing about what it covers, and such a node is
embedded on its name and description together. A description that replaces an
earlier one keeps the earlier wording on the node, so improving one loses
nothing.

**A description is reviewed when the material under it moves.** Each topic
carries `description_reviewed_at`, the moment somebody last wrote the
description or read it and let it stand. Reflect nominates a topic whose
material was created, archived or superseded after that moment, and the
nomination carries the change: `since`, a `changed_material` list of at most
twenty entries newest first, each saying what happened and when, plus
`changed_count` and `material_count` for what the cap hid and how much the
description stands over. Archival and supersession count because a description
standing over material that has since been retired is as stale as one written
before half of it arrived.

A topic nobody has described has no moment to measure from, so it falls back to
the older test: its material is at least three times the length of its name and
description together. That question, *is this topic thin*, answers identically
on every run, which is why a good description over forty facts used to be
nominated for ever. Once a topic has been reviewed, the ratio is never
consulted for it again.

**Both answers clear the nomination, and one of them is required.**
`enrichments` writes a new sentence; `descriptions_confirmed`, a list of topic
ids, records that the description was read against the change and still fits.
Both stamp `description_reviewed_at`. A confirmation journals
`DecisionKind.DESCRIPTION_REVIEW`, one row for the batch, the way `RETENTION`
records that an archival candidate was re-read and stands: an `ENRICHMENT` row
with the same text before and after would misreport it, and a reviewer
selecting `ENRICHMENT` should get the rows where a description moved. A
nomination nobody answers comes back on the next reflect unchanged, which is
the rule an unjudged pair already follows.

**A split nomination is answered the same way.** `should_split` bisects a
topic's material and nominates where the two centroids sit apart relative to
the spread inside them; that number is inflated exactly when there is little
material, so most nominations are read and kept whole. `splits_declined`
records that, on the same `description_reviewed_at` a confirmation stamps:
*last stood behind what this topic says and covers* is one moment, and the
split scan skips a topic whose material has not moved since it. It journals
`DecisionKind.SPLIT_DECLINED`, one row for the batch. Before it existed the
same topics came back on every reflect, 97 of them on one real graph.

**The material walk follows `tagged_with_topic`** as well as
`extracted_under_topic` and `abstracts`. A tag holds neither of the other two,
so until it did, the enrichment scan could not reach a topic node created from
a tag at all, and the only path to a described tag was an agent calling
`apply_reflection` by hand. New tags arrive described, because
`store_decomposition` refuses to mint one without a line in `tag_descriptions`;
the undescribed tags already on a graph are what this scan now finds.

**Three of these carry a metacontext, and none of them may invent one.** A split's
subtopics inherit what the parent states; a synthesised parent inherits the
one set its children all stand in, and is refused into `parents_refused`
when they differ; a topic merge is refused into `topic_merges_refused` unless
every source stands in exactly the same set. An all-tag merge and an all-tag
parent are exempt from those two gates: a tag names something rather than
asserting it, so it stands in every metacontext it is used from, and a node
gathering names is used from every world they were. Union is never the answer
for anything else: one node asserted in two worlds is the worst outcome
available.

**A merge re-states the survivor's metacontext rather than migrating one.** Every
other edge on a survivor is something its sources brought with them, but a
metacontext is a claim about which world this is, and the survivor's content is
*synthesised*, so nobody has yet said which world the synthesised wording is
about. Migrating the edge would answer for them and credit whoever placed a
source; the merging agent states it under its own judge instead. A correction
still moves the metacontext, where the replacement is the same claim.

The survivor re-states the union of what the sources stated, which for every
gated path is the one set they all stand in and for an all-tag merge is every
metacontext the name was used from. Where the sources state no metacontext,
nothing is re-stated: inventing one would put words in nobody's mouth. Splits
behave the same way. A node with no `has_metacontext` edge is one nobody spoke
for, and `epimemer metacontexts declare` is how a person assigns one.

Merging rebuilds the node's value signal through one shared function
(`merged_value_signal`) rather than field by field, so no clock or score is
silently reset.

Unknown or already-retired ids are **skipped**, not errors. Refused
boundaries come back in `boundaries_refused` with a reason.

### A batch applies, or it never existed

The steps share no transaction, and cannot: their order matters, so
judgments are recorded before the steps that retire the nodes those
judgments name. The guarantee comes from the other end instead: **every
entry is checked before the first step writes**, and a batch containing one
that cannot be applied at all is refused whole, with nothing written and
every problem listed at once. That matters most for `similarities` and
`relation_verdicts`, which suppress permanently: a half-applied batch would
leave the fixed-and-resent version refused as a repeat verdict on the pairs
that had gone through.

The check is about *shape*, not judgment: a missing key, an entry that is
not an object, a `pair` that does not name two, a supersession reason outside
the closed set, an unparseable date. Whether an entry *should* apply is
decided in its own step: an unknown id is skipped, and a judgment the graph
can evaluate and reject comes back in its own `*_refused` list. One
already-judged pair never costs a batch.

### `similarities`: recording a verdict on a nominated pair

Declining a pair needs a record as much as acting on one does: without it,
nothing shows the pair was ever looked at, and the same pair is nominated
again on every pass.

Each entry is `{pair: [a_id, b_id], verdict, because}`, and the verdict picks
what is written:

| Verdict | Use it when | Writes |
|---|---|---|
| `one_claim` | the two really do say the same thing and something blocked the merge: an event, or an unjudged `claim_kind` | `similarity` **and** `assessed` |
| `distinct` | they are different claims that merely look alike | `assessed` only |
| `distinct`, over a pair you earlier called `one_claim` | you got it wrong and want the count back | `retracted_similarity` plus `assessed` |

**Both stop the pair being nominated; only `one_claim` corroborates.** That
split is the whole design. A decline is two populations, and one edge cannot
serve both readers: the nomination sweep wants every judged pair suppressed,
while corroboration wants only restatements of one claim. Record a decline as
a `similarity` and *"these are different claims"* starts counting as a second
source: manufactured support, which is the failure this system treats as its
worst, since a false unification does not lose information, it inverts the
quantity corroboration measures. So reach for `one_claim` only where you
would have merged.

**A `one_claim` can be withdrawn, once.** Recording `distinct` over a pair
you earlier called one claim retracts that verdict: the pair stops
corroborating and the count returns to what it would have been. The
`similarity` edge is not removed, since nothing in this system deletes, so
the withdrawal is a second edge that disqualifies the first, the same way a
`contradiction` between two facts stops them counting as support for each
other.

**The withdrawal is final**, and the asymmetry is deliberate: nothing
re-asserts `one_claim` afterwards. Withdrawing costs a count the graph will
no longer make; re-asserting invents agreement. If the pair really is one
claim, `merge_facts` is the call that says so.

Suppression is untouched either way. The pair has now been judged twice and
stays out of every future nomination.

`because` is required. Anything not recorded comes back in
`similarities_refused` with a reason rather than being applied to something
adjacent; a cross-metacontext pair wants `record_variant`.

Similarities are applied **first** in the call, before any argument that can
retire a node. A judgment is about the wording it was made against, so a
supersession later in the same batch must not turn it into a skip.

**`relation_verdicts` is the same machinery one tier down**, for label pairs
from `similar_relations`: `{pair: [label_a, label_b], kind, verdict:
"distinct" | "synonymous", because}`, with `kind` copied from the nomination
and `because` required. Both verdicts suppress the pair from every future
nomination, permanently, so judge the pair rather than clearing the list.
What was decided is read back on `list_relations`, where each label carries
its standing verdicts, and `reflect` counts what suppression held back in
`relation_pairs_suppressed`. Refusals come back in
`relation_verdicts_refused`. They are applied right after `similarities`,
for the same anchoring reason.

### `retained`: recording that a nominee stands

`retained` is the verdict opposite to `archivals`: you looked at a nominee
and decided to keep it. Each entry is `{node_id, because, covers}`.

`covers` must name exactly the reasons still open on the node: the ids
`reflect` lists beside it in `pending_review` under `evidence_stale` or
`evidence_merged`. They are stored on the verdict, so a later change to the
evidence is a new reason no keep covers, and the node is nominated again.
Missing ids are refused (the keep would not cover the nomination) and
surplus ids are refused too (an anchor on a reason nobody named would
pre-cover a change nobody has seen). A reason a standing keep already anchors
to is dropped from the worklist, and naming it again is refused as already
covered. Omit `covers` where the nomination names no reason, such as
`never_retrieved`; the node is then kept for its own sake.

Use `judgments` where the importance was wrong; use `retained` where the
importance is right and the node has been re-read. Raising importance to stop
a nomination would make one field mean both *how consequential this is* and
*do not nominate this*. Entries not recorded come back in `retained_skipped`
with the reason; a node named in both `archivals` and `retained` is archived.

---

## 7. What reflect deliberately does not do

- **It does not judge succession.** Boundary proposals are drawn from a
  `temporally_followed_by` edge the agent already wrote. Guessing that two
  similar facts are successive is exactly the judgment the taxonomy reserves
  for the agent.
- **It does not write value signals.** It reads `importance`, `confidence`
  and the two clocks to nominate; only `judge_importance` moves importance,
  and only ingest supplies confidence. A decayed judgment would be a number
  nobody stands behind.
- **It does not resolve contradictions.** It surfaces them. Resolution is a
  separate, explicit act, and the genuinely contested ones go to a human.
- **It does not block anything.** The soundness check flags; ingest still
  stores.

---

## 8. Cost, and the one real limit

`reflect` is the slowest operation in the system and the only one that fails
at a size real use reaches. Against the 30 s default tool timeout it crosses
at roughly **320,000 nodes in-memory and 26,000 on SurrealDB**.

**That is a time limit, and there is a separate memory one.** Candidate pairs
are quadratic and nothing caps how many survive (about 580 bytes per
surviving pair), so on a corpus of genuinely similar documents `reflect` can
want gigabytes at about 10,000 facts, *below* the timeout crossing. The
benchmark corpus produces almost no surviving pairs, so it cannot show this.
Measurements and options: `dev-docs/BENCHMARKS.md`.

Practical consequence: run `reflect` deliberately, on a graph you know the
size of, rather than on a schedule against an unbounded one.
