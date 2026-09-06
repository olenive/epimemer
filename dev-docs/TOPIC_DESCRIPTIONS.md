# Topic descriptions: a name that does not move

**Status: proposal, written 2026-09-05 before any code, at the user's
direction. Nothing is built.** §6 breaks it into four stages with the types,
call sites and tests each one needs. §9 records what an independent review
settled and what is still open.

**Revised 2026-09-05 after that review**, in four places, each kept in the
section it changed rather than quietly corrected: §2.2 gained the merge path,
which has the same defect and which the original rule did not cover (§9.1);
§2.3's measurement was withdrawn as insufficient and Stage 1.5 now has to
supply the numbers (§9.2); §3 must keep the previous description, since an
in-place edit with no history is the one field on a topic that cannot be
audited (§9.3); and the tag pairs at 0.99 cosine are a hazard **today**,
independent of anything here, so they are an `ISSUES.md` entry rather than a
line in this document.

`Topic` has one text field. It carries two jobs: the name the graph joins on,
and the prose that explains what the topic is. This proposes splitting them,
`content` for the name and a new `description` for the prose, and it comes out
of a live defect where the two jobs collided.

---

## 1. What is missing, and how it was found

### 1.1 A topic's content is its identity, and enrichment moves it

Topics are joined by exact string match on `content`, in two places:

- **Write.** `_tag_topic` (`mcp/tools.py:926`) resolves or creates a tag by
  looking up an active Topic whose content is exactly the tag name.
- **Read.** `_resolve_hub_id` (`mcp/tools.py:1853`) resolves a name the same
  way for `find_nodes(tagged_with_topic=...)`.

Both go through `get_node_by_content`, which filters to `ACTIVE`.

`apply_reflection(enrichments=[...])` (`mcp/tools.py:4034`) replaces `content`
and supersedes the old node with status `CORRECTED`. So an enrichment moves the
join key, and the two paths then fail in opposite directions:

- The write path finds no active topic with that name and creates a second hub.
  Nodes tagged before the enrichment point at the old node, nodes tagged after
  point at the new one, and neither knows about the other.
- The read path misses, falls back to the raw string, and returns an empty list.
  There is no error. `find_nodes(tagged_with_topic="issue-46")` answered `{"nodes":
  []}` for two days.

The retired node is left `CORRECTED`, the one status `restore` refuses by
design, so no designed path walks any of this back.

**This happened.** On 2026-09-03 an enrichment pass rewrote six tags into
sentences: `issue-16`, `issue-46`, `issue-52`, `issue-53`, `issue-61`,
`issue-62`. `issue-46` was repaired by hand on 2026-09-05, recreating the tag
and moving its 31 `tagged_with_topic` edges onto it. Five are still broken.

The agent that did it was following the tool's own documentation.
`apply_reflection`'s docstring (`mcp/server.py:1712`) read *"enrichments:
Improve a topic's **description** using its associated material"*, and the
parameter it then named was `new_content`. The tool promised a description and
gave the agent the name. That docstring was corrected on 2026-09-05, ahead of
anything in this document.

### 1.2 What the names measure

`memory` holds 86 active tags. Scoring every pair against their stored
embeddings, ten pairs reach 0.75 cosine, and all ten are at or above the 0.80
nomination bar (`pipelines/reflection/review.py:40`):

| Cosine | Pair |
|---|---|
| 0.991 | `dev-session-2026-07-22` · `dev-session-2026-08-12` |
| 0.990 | `dev-session-2026-07-22` · `dev-session-2026-09-05` |
| 0.988 | `dev-session-2026-08-12` · `dev-session-2026-09-05` |
| 0.974 | `design decisions` · `design-decisions` |
| 0.972 | `dev-session-2026-07-22` · `dev-session-2026-09-04` |
| 0.972 | `dev-session-2026-09-04` · `dev-session-2026-09-05` |
| 0.969 | `dev-session-2026-08-12` · `dev-session-2026-09-04` |
| 0.920 | `claim-kind` · `claim_kind` |
| 0.814 | `reflect` · `reflection` |
| 0.804 | `provenance` · `retrieval-provenance` |

Two kinds are mixed together here. Four are one tag spelled two ways and should
merge. Six are different work sessions and must never merge: collapsing
`dev-session-2026-07-22` into `dev-session-2026-08-12` would fuse two days of
unrelated work into one hub.

**A name-only embedding cannot tell them apart**, and scores the pairs that must
not merge higher than the pairs that should. Both score high for the same
reason, which is that the strings are nearly identical.

Writing a description would separate them. Measured on the same model
(`all-MiniLM-L6-v2`), on text composed by hand for the measurement:

| | cosine |
|---|---|
| `issue-46` · `issue-51` | 0.789 |
| the same two with their descriptions | 0.253 |

Bare issue numbers sit one hundredth below the nomination bar purely because
they are lexically alike, and they are about entirely different things.

**Nothing in the graph has a description, and this proposal does not write
one.** Until somebody describes those 86 tags the six `dev-session` pairs stay
at 0.97 to 0.99, and a `reflect` run today can nominate two different days of
work for merging. That hazard exists now and is independent of everything here,
so it is an `ISSUES.md` entry in its own right rather than a benefit claimed by
this document.

What §1.2 does establish is the shape of the problem: a name-only embedding
does two things badly at once. A tag is invisible to a search on meaning, and
it looks like its neighbours for reasons that have nothing to do with what it
means. §2.3 and §6 say what still has to be measured before acting on that.

---

## 2. The change

### 2.1 The field

```python
class Topic(BaseModel):
    content: str  # the name; the join key
    description: str = ""  # advisory prose; empty means undescribed
```

Two precedents in the same file: `Metacontext.content` and
`Metacontext.description` (`core/types.py:1183`, commented "longer
explanation"), and `Timeline.name` with `Timeline.description`
(`core/types.py:1151`). `RelationLabel.description` (`core/types.py:1226`)
states the reason for the default: empty means **undescribed**, which is a true
and useful state, and is why the field exists before anything writes it.

**No migration and no schema change.** Every node table is `SCHEMALESS`
(`storage/surrealdb_adapter.py:940`) and nodes serialise through `model_dump`
(`_serialize`, line 521), so existing rows read back with `description=""` and
new rows carry the field the first time one is written. The in-memory backend
stores the model directly and needs nothing.

### 2.2 `content` is the name, and name resolution follows it

The rule this proposal was first written to establish was *nothing rewrites a
topic's `content`*. **That rule is too weak, and the review found where.**

Topic merge retires its sources as `MERGED` and creates a survivor whose
content the agent chooses (`mcp/tools.py:4115`). Merge `design decisions` and
`design-decisions` into `design-decisions`, then ingest a document tagged
`design decisions`: `_tag_topic` finds no ACTIVE node with that name and mints
a fresh hub. That is the split of §1.1 arriving through a different door, and
the tag-exemption shipped on 2026-09-05 makes such a merge easier to perform,
not harder.

Enrichment writing `description` closes one door. It does not close this one,
and a rule about what may write `content` never will, because a merge has to be
free to name its survivor.

**The root cause is on the read side: name resolution stops at ACTIVE.**
`get_node_by_content` filters to active nodes, so a name whose node has been
retired resolves to nothing, whatever retired it.

**The fix.** `_tag_topic` and `_resolve_hub_id` follow a `MERGED` or
`CORRECTED` hit forward, through `merged_into` or `superseded_by`, to the live
successor. One place, and it closes enrichment, merge, and any rename anyone
writes later. It would have made the 2026-09-03 incident a non-event: every one
of those six tags still had a successor to follow.

**This is a different edge set from `fold_lineage`, deliberately.**
`LINEAGE_FOLD_EDGE_TYPES` (`pipelines/query/lineage.py:28`) excludes
`merged_into` on the grounds that a merged node's content now lives on the
survivor, so it never reaches search to be folded. Name resolution asks the
opposite question, *where did this name go*, and a merged node is exactly the
case that has an answer.

The description field remains the right fix for the retrieval half of §1.2. It
is not a substitute for this.

### 2.3 What gets embedded

**Recommendation: `content` and `description` together, through one function.**

Nine call sites embed today (`vector_search.py:32`, `versioning.py:82` and
`:348`, `relation_consolidation.py:129`, `tools.py:613`, `:942`, `:1013`,
`:3971`, `:4015`). Nine independently written concatenations would be the
two-definitions failure this project has now hit three times, so this needs one
function and one definition:

```python
def embedding_text(node: EpistemicNode) -> str:
    """The text a node is embedded on: its name, plus its description."""
```

For a Topic it returns `content` when the description is empty, and
`f"{content}. {description}"` otherwise. Facts and inferences return `content`.

**The risk, and why §1.2 does not measure it.** This changes the vector of
every topic that gains a description, and the merge and nomination bars were
tuned against content-only vectors.

The pair measured in §1.2 is **symmetric**: both texts carry a description. The
hazard is **asymmetric**. A topic arriving at ingest has no description and the
stored topic it should match has one, so the two texts diverge for a reason
that has nothing to do with what they mean, and topic similarity weakens
exactly where it is supposed to catch a duplicate. A pair of symmetric
measurements says nothing about that case.

So the direction of the effect is unmeasured, and §1.2's number is withdrawn as
evidence for this decision. Stage 1.5 (§6) exists to supply the real numbers
before Stage 2 flips the embedding.

Embedding the name alone is the alternative. It is cheaper and changes no
existing score, and it gives up the retrieval half of the problem: tags stay
invisible to a search on meaning, and the ten pairs in §1.2 keep their scores.

---

## 3. Enrichment writes the description

The branch at `mcp/tools.py:4034` changes shape. `{topic_id, new_content}`
becomes `{topic_id, description}`, and the write becomes:

1. Set `description` on the topic in place.
2. Re-embed from `embedding_text`.
3. Journal `DecisionKind.ENRICHMENT` against the topic id.

No new node, no `CORRECTED` status, no edge migration, which is less work than
it does today.

**In place, rather than a new version.** `Topic.judged_by` is fixed at creation
and never edited, because a new wording is a new node (`core/types.py:829`).
That rule is about the claim. A description is advisory prose about a name that
has not changed, and `describe_relation` already set the precedent for prose:
it *"replaces the text and journals a second row — the first row is not
edited, and the record's `judged_by` never moves"* (`mcp/tools.py:2072`).

**The precedent comes with a condition, and the review found it missing here.**
What makes `describe_relation`'s overwrite safe is that the second row holds
the new text, so the prior wording is recoverable from the journal. `journal(
storage, DecisionKind.ENRICHMENT, [topic_id], judge)` as used today carries
**ids only**. An in-place overwrite on top of that would make `description` the
one field on a topic that can be edited and the one field with no history.

So the write must keep the wording it replaced. Two places it could go, and
§9.3 asks which:

- **The journal payload**, extending the `ENRICHMENT` row to carry the previous
  and new text. It puts the history where the decision already is, and it
  changes `journal`'s shape for every caller.
- **The topic's `metadata`**, appending the replaced text. It touches nothing
  else, and it puts a growing list on the node rather than in the record of
  decisions.

**This removes the need for a guard.** The obvious fix to §1.1 is to refuse
enrichment on a tag, using `is_tag_topic`. If enrichment cannot reach `content`
at all, nothing can split a tag, and the guard has nothing to do. It is also
one fewer "except tags" clause, of which reflection already has several.

---

## 4. Read paths

`search`, `find_nodes` and `topic_tree` return full model dumps, so a
description appears in their responses as soon as the field exists. Three
places need a decision rather than nothing:

- **`topic_tree`** returns "ids and short content previews". A described topic
  should preview its description, since a preview exists to say what the thing
  is (§9.5).
- **`store_decomposition`** could accept a description per topic entry at
  ingest, so a topic arrives described instead of waiting for a reflect (§9.4).
- **The visualisation** tooltip shows topic content, and should show the
  description where there is one.

---

## 5. The five broken tags

`issue-16`, `issue-52`, `issue-53`, `issue-61` and `issue-62` are still
sentences holding their tags' edges. The repair is the one already run for
`issue-46`: create the bare tag, move its `tagged_with_topic` edges onto it keeping
their original `created_at` and `judged_by`, and prune the moved edges from any
merge-undo record that would otherwise restore a duplicate.

The sentences the enrichment wrote are good prose, and they are exactly what
the new field is for. Seeding each repaired tag's `description` from the
sentence that displaced its name turns the damage into the field's first data
(§9.6).

---

## 6. Stages

**Stage 0, name resolution follows a retired node forward** (§2.2). `_tag_topic`
and `_resolve_hub_id` resolve a `MERGED` or `CORRECTED` hit through
`merged_into` or `superseded_by` to the live successor. Independent of the
description field and worth shipping first, because it closes the merge door
that is open right now. Tests: a tag whose node was merged away resolves to the
survivor and mints no second hub; the same for a tag whose node was superseded;
a name that never existed still resolves to nothing; a chain of two hops
resolves to the end of it.

**Stage 1, additive, changes no behaviour.** `Topic.description`, the
`embedding_text` function, and every one of the nine embed sites routed through
it. Every description is empty, so every vector is byte-identical to what it is
today. Tests: a topic round-trips its description through both backends; a
topic with no description embeds exactly as it does now; `embedding_text` is
the only place the two fields are joined.

**Stage 1.5, measurement, and Stage 2 does not begin until it reports.** Seed a
dozen of the 86 tags with real descriptions and re-run the pairwise sweep,
recording in this document:

- **Symmetric pairs**, both described. Does the `dev-session` cluster fall below
  0.80, and do `claim-kind` and `claim_kind` stay above it?
- **Asymmetric pairs**, one described and one not, which is the ingest case of
  §2.3. How far does a described topic move from the same topic undescribed?

The asymmetric number decides whether the embedding changes at all. If a
described topic no longer matches its own undescribed restatement, embedding
the name alone is the answer and the description stays prose for readers.

**Stage 2, the half that pays.** Enrichment writes `description` in place, keeps
the wording it replaced (§3), and re-embeds. `store_decomposition` accepts an
optional description per topic (§9.4). Read paths surface it. Tests: enrichment
leaves `content` byte-identical and the node id unchanged; the replaced wording
is recoverable; a described topic re-embeds; the six-tag failure from §1.1 is a
regression test, tagging a document, enriching the tag, and tagging again,
asserting one hub.

**Stage 3, repair.** The five tags, with descriptions seeded per §9.6.
Verification is `find_nodes(tagged_with_topic=...)` returning the expected count for
each.

---

## 7. Rejected

**A `TagLabel` registry beside the graph**, copying `RelationLabel`. Rejected
on the user's argument: the graph's only association mechanism is a node-to-node
edge, and a tag exists to be a hub that gathers otherwise unrelated facts and
inferences. A tag has nothing at the far end of the edge unless it is itself a
node. A relation label can live off-graph because both of its endpoints are
already real nodes. A registry would also have to be kept in step with the
nodes it describes, and this proposal has one object where that has two.

**Tags stop being nodes.** The same argument, harder: it would mean a second
kind of association alongside the only one the graph has, plus 4,107 edges and
94 topics migrated, plus every read path that treats a tag as a topic.

**A description on the `tagged_with_topic` edge**, saying why this node carries this
tag. Rejected on three counts: it is per (node, tag) pair, so a document with
30 nodes and 4 tags costs 120 sentences at ingest; nothing reads it, and an
unread field drifts; and most entries would restate the tag, which the
project's documentation rule already rejects for comments that only cite. Edges
also have no history, no `status` and no `updated_at`, so a description on one
could not be revised, only deleted and rewritten.

---

## 8. What this deliberately does not do

**Facts and inferences get no description.** A claim's wording is the claim,
and a second field beside it would be a place to put a softer version of the
same assertion, unattributed and unversioned.

---

## 9. Settled by review, and what is open

An independent review on 2026-09-05 verified the diagnosis against the code:
`_tag_topic` and `_resolve_hub_id` both go through `get_node_by_content`, which
filters to ACTIVE; enrichment builds a new Topic and supersedes as `CORRECTED`;
the node tables are `SCHEMALESS`; the nomination bar is 0.80. It accepted the
description field and returned three corrections, taken up in §2.2, §2.3 and
§3.

**Settled:**

1. **The merge path has the same defect** and *nothing rewrites `content`* does
   not cover it. Fixed on the read side instead, by following a retired node
   forward. §2.2, and Stage 0 in §6.
2. **Embed `content + description`, agreed in principle, on numbers this
   document does not yet have.** The measurement in §1.2 is symmetric and the
   hazard is asymmetric. §2.3, and Stage 1.5 in §6.
3. **An in-place description must keep the wording it replaced**, or it becomes
   the one editable field on a topic with no history. §3. **Open:** journal
   payload or node metadata.
4. **`store_decomposition` takes an optional description per topic.** §4.
5. **`topic_tree` previews the description where there is one.** §4.
6. **The five repaired tags take their descriptions from the sentences the
   enrichment wrote, after a person has read the five once.** §5.

**Shipped separately, ahead of all of this**: the `apply_reflection` docstring
at `mcp/server.py:1712`, which told the agent it was improving a description
and handed it the name. It is the proximate cause of the incident in §1.1 and
Stage 2 is weeks away.

**Open:**

- §9.3 above, where the replaced wording lives.
- Whether Stage 0 should also cover `get_node_by_content`'s other callers, or
  stay in the two name-resolution sites where the question is *where did this
  name go*.
