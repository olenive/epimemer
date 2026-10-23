# Reflection: the review loop

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

So similarity nominates and an agent decides: `reflect` returns pairs with
their scores rather than verdicts. Two further principles:

- **Nothing is destroyed; ambiguity is made visible.** Outdated and contested
  nodes stay `active` and retrievable, carrying a computed label so anything
  reading them knows. Archival is export, not delete.
- **Two-tier responsibility.** The agent handles the mechanical calls. It
  **escalates the consequential ones to the human**: genuine contradictions,
  and anything crossing a metacontext boundary.

---

## 2. The verdict taxonomy

When a new claim is similar to an existing one, the agent classifies the
pair. Each verdict has exactly one recorded consequence:

| Verdict | Meaning | Action |
|---|---|---|
| **redundant** | same claim restated | `merge_facts`; if refused, record `similarity` and keep both |
| **supersedes** | the new one corrects the old: the old was **wrong** | `superseded_by`; old becomes `corrected` |
| **succeeds** | both true, over different periods: **the world moved** | `temporally_followed_by`; old becomes `historical`, restorable |
| **recurs** | a previously retired `historical` claim is true again | `restore` plus a new `sourced_from` edge, one transaction |
| **contradicts** | conflicting, same metacontext, unclear which holds | record `contradiction`; resolve, or escalate |
| **cross-metacontext** | a "conflict" only because the metacontexts differ | not a conflict; both coexist; optionally `variant_of` |
| **compatible** | no conflict | nothing in the graph; a pair `reflect` nominated still takes `similarities` with `distinct`, so it is not offered again |

**`supersedes` and `succeeds` are the pair to be careful about.** They are
not degrees of the same thing. `supersedes` asserts the old claim was never
true; `succeeds` asserts it was true and remains true of its period. Filing a
change in the world as an error is how a graph forgets its own history, so
`because` is required and has no default. If you cannot tell which happened,
leave the pair contested rather than guessing. See
[VALIDITY.md](VALIDITY.md#5-the-world-changing-is-not-the-same-as-being-wrong).

Two flags arrive without any similarity check. When a fact is superseded, inferences derived from it become
**evidentially stale** and are flagged automatically. When a fact is
*merged*, its dependents are flagged **`evidence_merged`** instead: the claim
under them did not change, only the wording that states it and the documents
behind it, so what is wanted is a re-read rather than a re-derivation. Either
re-read is recorded with `apply_reflection(retained=…)` (§6).

`redundant` routes into `merge_facts(source_ids, content)`: one node keeping a
`sourced_from` edge per contributing document, so provenance becomes plural
rather than being overwritten. It refuses, with a reason: an **event** rather
than a state; a fact ingested without a `claim_kind`; a retired twin (that is
`recurs`, and `restore`); a pair not standing in exactly the same metacontexts
(that is `record_variant`); a fact that has already been merged and un-merged
as many times as the graph's merge cycle limit allows (default 2: an
oscillation, and the refusal asks you to bring in the user rather than try
again); and anything below the nomination bar. Every refusal leaves the older
action available and correct: *keep both, joined by `similarity`*, which is
what corroboration reads.

A merge that turns out to be wrong is undone with
`reverse_merge(survivor_id)`: the sources come back active with their own
edges and the survivor is deleted, leaving the graph as it was before. It
refuses if anything has been added to the survivor since (a contradiction, a
tag, a verdict), because the delete would take those with it, and it refuses a
survivor merged again since or one whose replay record has aged out.
`configure_merge` reads and sets the two settings involved: `undo_depth`, how
far back along a chain of merges the graph keeps what a reversal needs
(default 10), and `cycle_limit` (default 2). Its response reports them as
`merge_undo_depth` and `merge_cycle_limit`; `clear=True` returns both to the
process defaults, which `EPIMEMER_MERGE_UNDO_DEPTH` and
`EPIMEMER_MERGE_CYCLE_LIMIT` set.

### Inferences merge too, and are warned rather than refused

`merge_inferences(source_ids, content)` is the sibling tool, typically for
the inferences a fact merge left resting on one survivor. The gate is the
same without the `claim_kind` rung.

The survivor rests on the **union** of its sources' premises. Where two of
them are dated and provably fall clear of each other, the merge is warned
rather than refused, and the warning arrives **with the nomination and in the
response, before the content is written**; the usual answer is to narrow the
merged wording or its period. Going ahead past it writes a
`proceeded_despite_warning` row, read back by `review(mode="warning")`
([ATTRIBUTION.md](ATTRIBUTION.md)).

---

## 3. What `reflect` returns

Each key is a worklist, not a verdict:

| Key | Nominates | Applied via |
|---|---|---|
| `similar_pairs` | topic pairs at or above `similarity_threshold` (0.85 by default) | `merges` or `parents` |
| `split_candidates` | topics whose material bisects into two clusters, skipping any somebody stood behind since its material last moved | `splits` or `splits_declined` |
| `enrichment_candidates` | topics whose material moved since somebody last stood behind the description, with the change itself; and topics nobody has described yet, with a sample | `enrichments` or `descriptions_confirmed` |
| `contradictions` | same-metacontext active fact pairs at or above 0.80, the one nomination bar, which `merge_facts` also gates on, so a pair listed here is mergeable | `record_contradiction`, then `supersessions`; or `similarities` where neither fits |
| `temporal_contradictions` | open, unheld disagreements about the order of timepoints: a cycle in what sources stated, or a point squeezed until its earliest bound is later than its latest | `resolve_temporal_contradiction`, which is a timeline tool rather than a verdict list on `apply_reflection` |
| `recurrences` | an active claim beside its own `historical` twin | `restore` |
| `unsound_inferences` | inferences whose premises no source puts in one period | agent judgment |
| `inference_merge_candidates` | near-identical active inferences resting on a shared premise, each with the warning computed before you decide | `merge_inferences`, or `similarities` where they are two claims |
| `similar_inferences` | similar active inferences resting on no common premise, each side with its premises | `similarities`: `one_claim` where they draw the same conclusion, `distinct` where they do not |
| `boundary_proposals` | where a succession lets a period close or open, leaving out any somebody declined | `boundaries` or `boundaries_declined` |
| `pending_review` | active nodes already carrying review state | `supersessions`, `record_variant`, `retained` |
| `archival_candidates` | nodes worth setting aside, less any a deferral is still holding back | `archivals`, `judgments`, `retained`, `deferred` |
| `similar_relations` | likely-synonymous user relationship labels, at or above `relation_similarity_threshold` (0.9 by default) | `relation_verdicts` |

`temporal_contradictions` is answered by `resolve_temporal_contradiction`
rather than by `apply_reflection`, because its subjects are ordering
constraints and timepoints rather than nodes. Its verdicts, including the
*hold* that stops a contradiction being nominated, are in
[TIMELINES.md](TIMELINES.md#the-three-verdicts).

Not every key is a worklist. `truncated` names any of the lists that hit
`max_nominations` and were cut. `relation_pairs_suppressed` counts the label
pairs that standing verdicts kept out of `similar_relations`, so an empty list
beside a non-zero count means *already judged*. `stores_since_last_reflect` is
the store count this call reset to zero.

**The lists built out of pairs are capped**: `similar_pairs`,
`contradictions`, `recurrences`, `inference_merge_candidates`,
`similar_inferences` and `similar_relations`. Each is cut to its
highest-scoring `max_nominations` (200 by default), and a cut list is named in
`truncated`. **When a list is named there, act on what came back and reflect
again** rather than raising the number: the remainder is the weakest end of
the ranking.

`inference_merge_candidates` compares inferences only where they rest on a
shared premise, which is where near-duplicates land after a fact merge.
`similar_inferences` is the rest of the similar inference pairs, the ones
resting on no common premise, so the two lists never offer one pair twice. A
`one_claim` on one of its pairs writes a `similarity` edge that corroboration
reports beside the count rather than in it (§6), because two conclusions
agreeing is not a second witness to either.

Two of the lists keep a distinction apart:

- **`recurrences` is separate from `contradictions`** because a claim
  standing beside its own successor is not in conflict with it. Only *mixed*
  pairs qualify: two active facts are redundancy, two historical ones are
  both past.
- **Cross-metacontext pairs are dropped**, not reported. A high-similarity pair
  across disjoint metacontexts is coexistence, and calling it a
  contradiction is the misreading metacontexts exist to prevent.

`unsound_inferences` and `boundary_proposals` are covered in
[VALIDITY.md](VALIDITY.md#7-what-reflect-does-with-validity).

### When it runs

Never on its own. `configure_reflection` sets a per-graph threshold on
stores; once it is crossed, `store_decomposition` **flags a suggestion** in
its response and `graph_stats` reports the pressure. Nothing schedules or
triggers a reflect: running it is the agent's deliberate act.

A completed reflect also advances the graph's **reflect round** by one when
there was at least one store since the previous reflect, in the same atomic
step that zeroes the counter. The round is the clock a deferred nominee waits
on, and it travels in a bundle with the rest of the graph's settings.

### Backing up runs the same way

Backups are prompted on the same pattern. A second counter,
`stores_since_backup`, is zeroed by a successful backup rather than by a
reflect, and `configure_backup` sets its per-graph threshold.
`store_decomposition`, `graph_stats` and `apply_reflection` report
`backup_suggested`.

A backup is the user's call, so the agent raises the suggestion and calls
`backup_graph` only if they agree. It writes to `EPIMEMER_BACKUP_DESTINATION`,
takes **no path**, refuses when no destination is configured, and needs no
judge.

---

## 4. Review labels

Four labels are **computed at read time** from edges and never stored, so
they cannot freeze against a graph that has moved on. They ride, under
`review`, on the nodes `search`, `query_graph` and `query_changes` return:

| Label | Means |
|---|---|
| `superseded_candidate` | something has been nominated as replacing this |
| `evidence_stale` | *(inferences only)* a fact this was derived from has been superseded |
| `evidence_merged` | *(inferences only)* a fact this rests on absorbed another claim; the ids are the phrasings that went away |
| `contested` | this has a `contradiction` edge to a live, same-metacontext node |

`contested` resolves itself when the partner is retired. The label is
derived, so there is nothing to clean up.

**`evidence_merged` is not a weaker `evidence_stale`**: staleness is an
archival class (§5), and a merged premise is not, since nothing was overturned.

---

## 5. Archival: the hygiene arm

Archival is the same loop with one more outcome, *triviality*. Reflect's
`archival_candidates` proposes nodes, worst first, in four classes:

| Class | What it catches |
|---|---|
| `retired` | corrected or merged nodes retired more than `max_age_days` ago (90 by default), not judged important |
| `evidence_stale` | active inferences whose basis changed, or whose whole evidence set has been archived |
| `never_retrieved` | active facts and topics never returned by a search, not judged important, with nothing depending on them, and not deferred within their wait |
| `stale_judgment` | nodes held above the importance ceiling by an upward judgment more than 180 days old that nobody has revisited |

The list holds at most twenty nominees a pass. A deferred node whose wait is
over is placed first within `never_retrieved`, so the limit cannot keep pushing
it behind nodes nobody has looked at yet.

**`historical` nodes are never nominated.** They were retired because the
world changed, not because they were wrong, so they remain true of their
period and age alone is not grounds to discard them.

`stale_judgment` exists because importance protects a node from every class
above it. Its answer is `judgments`, in either direction: *"keep it, and stop
treating it as important"*, or *"still important, and now recently
confirmed"*. Either moves the clock, so the node leaves the stale set.

The `importance_ceiling` is **inclusive** of the default: an un-judged node is
not a node judged worth keeping, and nomination is a proposal rather than a
verdict.

Archival itself is an **export**: `apply_reflection(archivals=[…])` returns
`archive_data` (keep it; that copy *is* the archive) and atomically flips the
nodes to `archived`, which removes them from every active-status query.
Nothing is deleted, and `restore` reverses it. Only an active node is
archived: any other id named comes back in `archivals_skipped` as
`{node_id, why}`, saying whether it is unknown, already archived, or not
active, with its status.

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
| `deferred` | put a `never_retrieved` nominee off for a number of reflect rounds that doubles each time |
| `archivals` | export and retire approved nominees |
| `judgments` | re-judge importance, in either direction, with a reason |
| `relation_verdicts` | record what you decided about a nominated label pair |
| `boundaries` | fill in one open endpoint of one source's period |
| `boundaries_declined` | record that a proposed endpoint is wrong, with the reason, so it is not proposed again |

**`merges` is the one consolidation that retires nodes from the active graph**,
so the bar is deliberately high: a merge is applied only if *every* pair of
sources clears 0.92, and otherwise it is rejected and counted in
`merges_rejected`. Two spellings of one tag name, such as `claim-kind` and
`claim_kind`, are the exception: they normalise to one key and merge whatever
their similarity. **The bar is not a parameter of the call**: a caller does not
choose the bar its own merge is checked against, here or in `merge_facts`. For
topics that are merely related rather than duplicates, use `parents`.

**`enrichments` writes a description beside a name that does not change.** Each
entry is `{topic_id, description}`, and the write is in place: the topic keeps
its id, its content (the name tags and `find_nodes(tagged_with_topic=...)`
resolve by) and every edge it holds. It is most worth doing for a topic node
created from a tag, whose bare name says nothing about what it covers; such a
node is embedded on its name and description together. A description that
replaces an earlier one keeps the earlier wording on the node.

**A description is reviewed when the material under it moves.** Each topic
carries `description_reviewed_at`, the moment somebody last wrote the
description or read it and let it stand. Reflect nominates a topic whose
material was created, archived or superseded after that moment, and the
nomination carries the change: `since`, a `changed_material` list of at most
twenty entries newest first, each saying what happened and when, plus
`changed_count` and `material_count` for what the cap hid and how much the
description stands over.

A topic nobody has described has no moment to measure from, so it is nominated
when its material is at least three times the length of its name and
description together, and arrives with `since: null` and a `sample` of its
material. Once a topic has been reviewed, the length test is never applied to
it again.

**Both answers clear the nomination, and one of them is required.**
`enrichments` writes a new sentence; `descriptions_confirmed`, a list of topic
ids, records that the description was read against the change and still fits.
Both stamp `description_reviewed_at`, and a confirmation journals its own
`description_review` row. A nomination nobody answers comes back on the next
reflect unchanged.

**A split nomination is answered the same way.** Most are read and kept
whole, and `splits_declined` records that on the same
`description_reviewed_at` a confirmation stamps, so the split scan skips a
topic whose material has not moved since.

The enrichment scan reaches a topic node created from a tag through the nodes
tagged with it. New ones arrive described, because `store_decomposition` refuses to create one
without a line in `tag_descriptions`; this scan finds the undescribed ones a
graph already holds.

**Three of these carry a metacontext, and none of them may invent one.** A split's
subtopics inherit what the parent states; a synthesised parent inherits the
one set its children all stand in, and is refused into `parents_refused`
when they differ; a topic merge is refused into `topic_merges_refused` unless
every source stands in exactly the same set. An all-tag merge and an all-tag
parent are exempt from those two gates: a tag names something rather than
asserting it, so it stands in every metacontext it is used from, and a node
gathering names is used from every world they were. Union is never the answer
for anything else.

**A merge re-states the survivor's metacontext under the merging agent's
judge rather than migrating it**, because the survivor's wording is new and
nobody has yet said which world it is about. It re-states what the sources
stated; where they stated none, nothing is re-stated, and `epimemer
metacontexts declare` is how a person assigns one.

Unknown or already-retired ids are **skipped**, not errors. Refused
boundaries come back in `boundaries_refused` with a reason, and refused
declines in `boundaries_declined_refused`: a decline naming a claim or source
that is not there, an endpoint that is not `start` or `end`, or a proposal
already declined.

### A malformed batch writes nothing

**Every entry is checked before the first step writes**, and a batch
containing one that cannot be applied at all is refused whole, with nothing
written and every problem listed at once, so the corrected batch can be resent
as it is.

The check is about *shape*, not judgment: a missing key, a value of the
wrong type, a reason outside a closed set, an unparseable date, a blank
reason where one is required. Whether an entry *should* apply is
decided in its own step: an unknown id is skipped, and a judgment the graph
can evaluate and reject comes back in its own `*_refused` list. One
already-judged pair never costs a batch.

### `similarities`: recording a verdict on a nominated pair

Declining a pair needs a record as much as acting on one does: without it,
the same pair is nominated again on every pass.

Each entry is `{pair: [a_id, b_id], verdict, because}`, and the verdict picks
what is written:

| Verdict | Use it when | Writes |
|---|---|---|
| `one_claim` | the two really do say the same thing and something blocked the merge: an event, or an unjudged `claim_kind` | `similarity` **and** `assessed` |
| `distinct` | they are different claims that merely look alike | `assessed` only |
| `distinct`, over a pair you earlier called `one_claim` | you got it wrong and want the count back | `retracted_similarity` plus `assessed` |

**Both stop the pair being nominated; only `one_claim` corroborates.**
Recording a decline as `one_claim` would count *"these are different claims"*
as a second source, so reach for `one_claim` only where you would have merged.

**Between two inferences, `one_claim` records agreement and adds nothing to
the count.** Corroboration lists the partner under `agreeing_inferences`
instead, whichever list nominated the pair, so the verdict for a
`similar_inferences` pair is simply whether the two draw the same conclusion.

**A `one_claim` can be withdrawn, once.** Recording `distinct` over a pair
you earlier called one claim retracts that verdict: the pair stops
corroborating. The `similarity` edge stays, disqualified by a second edge.
**The withdrawal is final**: nothing re-asserts `one_claim` afterwards, and if
the pair really is one claim, `merge_facts` is the call that says so.

Suppression is untouched either way. The pair has now been judged twice and
stays out of every future nomination, until `reopen` puts it back (below).

`because` is required. Anything not recorded comes back in
`similarities_refused` with a reason rather than being applied to something
adjacent. `one_claim` over a cross-metacontext pair is refused in favour of
`record_variant`, and `distinct` is accepted there.

Similarities are applied **first** in the call, before any argument that can
retire a node, so a supersession later in the same batch cannot turn one into
a skip.

**`relation_verdicts` is the same machinery one tier down**, for label pairs
from `similar_relations`: `{pair: [label_a, label_b], kind, verdict:
"distinct" | "synonymous", because}`, with `kind` copied from the nomination
and `because` required. Both verdicts suppress the pair from every future
nomination until `reopen(relation_labels=...)` puts it back, so judge the pair
rather than clearing the list.
What was decided is read back on `list_relations`, where each label carries
its standing verdicts, and `reflect` counts what suppression held back in
`relation_pairs_suppressed`. Refusals come back in
`relation_verdicts_refused`. They are applied right after `similarities`.

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

**A keep on a `never_retrieved` nominee is permanent.** Its nomination names no
reason, so nothing that happens later can be a reason the keep failed to cover.
The keep stands until `reopen` withdraws it, so the node is not offered as
`never_retrieved` again before then. Where you mean *not now* rather than
*keep it*, use `deferred`.

Use `judgments` where the importance was wrong; use `retained` where the
importance is right and the node has been re-read, rather than raising
importance to stop a nomination. Entries not recorded come back in
`retained_skipped` with the reason; a node named in both `archivals` and
`retained` is archived.

### `deferred`: not now, ask again later

`deferred` is the third answer to a `never_retrieved` nominee. Each entry is
`{node_id, because}`, and `because` is required.

The node is left out of `archival_candidates` for 2 **reflect rounds** after
its first deferral, doubling with each one after it, up to 32. A round is a
reflect that completes with at least one store since the previous reflect; a
reflect that fails or times out counts for nothing. When the wait is over the
node comes back first among the `never_retrieved` nominees, carrying
`deferred`: `count`, `last_round`, `rounds_waited`, the `because` given,
`judged_by`, and `next_wait`, the wait another deferral would set.

Only a node that is a `never_retrieved` nominee at the moment of the call can
be deferred, including one past the twenty-nominee limit. Anything else, a
node still waiting included, comes back in `deferred_skipped` with the reason.
A node also named in `archivals` is archived; one named in both `retained` and
`deferred` is refused in both. `deferrals_recorded` lists each deferral written
as `{node_id, count, due_after_rounds}`.

Each deferral is a `deferral` journal row carrying the round as `at_round`. A
later `retained` verdict for the node clears its deferral state, and so does
`reopen(node_ids=[node_id])` while the node is still waiting; `reopen` refuses
a deferral whose wait is over, since it holds nothing back. The next deferral
after a clear counts from one again.

`configure_deferral` reads and sets one graph's `first_wait` and `cap`;
`clear=["first_wait"]` returns a named setting to the process default, which
`EPIMEMER_DEFERRAL_FIRST_WAIT` and `EPIMEMER_DEFERRAL_CAP` set. A change is
refused unless each value is from 1 to 1024 rounds and `cap` is at least
`first_wait`; a read is never refused. A stored value above 1024, from a
bundle for instance, behaves as 1024 and is reported under `clamped`. Every
wait is computed at the reflect that reads it, so a change applies to
deferrals already written.

### `reopen`: taking a suppression back

A verdict stops a question being asked again. `reopen` withdraws one
suppression when later evidence says the question is worth asking again, and
the target says which layer:

| Call | Withdraws | Effect |
|---|---|---|
| `reopen(node_ids=[a, b], reason=...)` | the pair's `assessed` edge | the pair is nominated again |
| `reopen(relation_labels=[a, b], reason=...)` | the pair's standing verdict | the label pair is nominated again |
| `reopen(node_ids=[node_id], reason=...)` | the node's standing keep, or a deferral still waiting | the node returns to the archival worklist |
| `reopen(boundary={node_id, source_id, endpoint, at, timeline_id}, reason=...)` | the standing decline of that boundary proposal | the proposal is offered again |

**It asserts nothing.** It does not record the opposite of the earlier verdict
and does not say the earlier judge was wrong: it re-asks the question, and
whoever answers it next does so with the same tools as the first judge. Reach
for it when later evidence makes a decline look wrong, rather than when you
disagree with it on the same material.

**Nothing is deleted.** The `assessed` edge is retired and left in the graph,
the verdict table takes a new row, and the journal records a `reopened`
decision carrying your `reason`. So a pair reopened and judged `distinct` a
second time carries both rounds, and the second verdict suppresses it again.

**The nomination says it was reopened**, with the reason, the date and the
judge. It appears as `reopened` on `similar_pairs`, `contradictions`, `recurrences`,
`inference_merge_candidates`, `similar_inferences`, `archival_candidates`,
`boundary_proposals` and `similar_relations`. On `boundary_proposals` the note
is matched by claim and source, so a reopen of one endpoint also shows on a
proposal for the other endpoint of the same source's period; the note names
the one it was about.

**Refused when nothing is suppressed for the target**, naming what it looked
for. Refused too when
the pair carries a standing `similarity`, `contradiction` or `variant_of`
edge, because those assert something about the pair rather than declining it,
and withdrawing one is a verdict. Recording `distinct` through `similarities`
is still what withdraws a standing `one_claim`.

---

## 7. What reflect deliberately does not do

- **It does not judge succession.** Boundary proposals are drawn from a
  `temporally_followed_by` edge the agent already wrote.
- **It does not write value signals.** It reads `importance`, `confidence`
  and the two clocks to nominate. Importance moves only through
  `judge_importance` and `apply_reflection(judgments=...)`, and confidence
  comes from ingest, with `rejudge` the one tool that revises it.
- **It does not resolve contradictions.** It surfaces them. Resolution is a
  separate, explicit act, and the genuinely contested ones go to a human.
- **It does not block anything.** The soundness check flags; ingest still
  stores.

---

## 8. Cost, and the one real limit

`reflect` is the slowest operation in the system and the only one that fails
at a size real use reaches: against the 30 s default tool timeout it crosses
at roughly **320,000 nodes in-memory and 26,000 on SurrealDB**
(`dev-docs/BENCHMARKS.md`). Run it deliberately, on a graph you know the size
of.
