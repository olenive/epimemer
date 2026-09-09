# Topic descriptions: a name that does not move

**Status: stages 0 and 1 built; the field exists and nothing writes it yet.**
§6 breaks the rest into stages with the types, call sites and tests each one
needs; §9 lists what is still open. Where an unbuilt section says "does", read
"would".

`Topic` has one text field. It carries two jobs: the name the graph joins on,
and the prose that explains what the topic is. This proposes splitting them,
`content` for the name and a new `description` for the prose. It comes out of
a live defect where the two jobs collided.

---

## 1. The problem

### 1.1 A topic's content is its identity, and enrichment moves it

Topics are joined by exact string match on `content`, in two places:

- **Write.** `_tag_topic` in `mcp/tools.py` resolves or creates the topic node a
  tag names by looking up an active Topic whose content is exactly the tag name.
- **Read.** `_resolve_node_reference` in `mcp/tools.py` resolves a name the same way
  for `find_nodes(tagged_with_topic=...)`.

Both go through `get_node_by_content`, which filters to `ACTIVE`.

`apply_reflection(enrichments=[...])` replaces `content` and supersedes the old
node with status `CORRECTED`. So an enrichment moves the join key, and the two
paths then fail in opposite directions:

- The write path finds no active topic with that name and creates a second
  topic node. Nodes tagged before the enrichment point at the old node, nodes tagged
  after point at the new one, and neither knows about the other.
- The read path misses, falls back to the raw string, and returns an empty
  list. There is no error.

The retired node is left `CORRECTED`, the one status `restore` refuses by
design, so no designed path walks any of this back.

This has happened: an enrichment pass on a real graph rewrote six topic nodes
created from tags
(`issue-16`, `issue-46`, `issue-52`, `issue-53`, `issue-61`, `issue-62`) into
sentences, following a tool docstring that promised to improve a description
and handed the agent the name. The docstring now says what the tool does; §5
covers the repair.

### 1.2 What the names measure

A topic node created from a tag is embedded on its name alone. On a real graph,
scoring every
active tag pair against its stored embedding, every pair above 0.75 cosine was
also at or above the 0.80 nomination bar (`pipelines/reflection/review.py`),
and two kinds were mixed together:

| Cosine | Pair | Should merge? |
|---|---|---|
| 0.97 to 0.99 | `dev-session-<date>` against another date | No: different days of work |
| 0.974 | `design decisions` · `design-decisions` | Yes |
| 0.920 | `claim-kind` · `claim_kind` | Yes |
| 0.814 | `reflect` · `reflection` | Yes |
| 0.804 | `provenance` · `retrieval-provenance` | Yes |

A name-only embedding cannot tell them apart, and scores the pairs that must
not merge higher than the pairs that should. Both score high for the same
reason, which is that the strings are nearly identical.

Writing a description would separate them. On the same model
(`all-MiniLM-L6-v2`), with text composed by hand for the measurement, two
issue-number tags scored 0.789 bare and 0.253 with their descriptions.

Nothing in the graph has a description, and this proposal does not write one.
Until somebody describes the tags, the `dev-session` pairs stay where they are
and a `reflect` run can nominate two different days of work for merging. That
hazard exists now and is independent of everything here, so it has its own
`ISSUES.md` entry rather than being a benefit claimed by this document.

What §1.2 establishes is the shape of the problem: a name-only embedding does
two things badly at once. Such a node is invisible to a search on meaning, and
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

Two precedents in the same file: `Metacontext.content` with
`Metacontext.description`, and `Timeline.name` with `Timeline.description`.
`RelationLabel.description` states the reason for the default: empty means
**undescribed**, which is a true and useful state, and is why the field exists
before anything writes it.

**No migration and no schema change.** Every node table is `SCHEMALESS` and
nodes serialise through `model_dump`, so existing rows read back with
`description=""` and new rows carry the field the first time one is written.
The in-memory backend stores the model directly and needs nothing.

### 2.2 `content` is the name, and name resolution follows it

*Nothing rewrites a topic's `content`* is too weak a rule, because topic merge
has the same defect and has to be free to name its survivor. A merge retires
its sources as `MERGED` and creates a survivor whose content the agent chooses.
Merge `design decisions` and `design-decisions` into `design-decisions`, then
ingest a document tagged `design decisions`: `_tag_topic` finds no ACTIVE node
with that name and mints a fresh topic node. That is the split of §1.1 arriving
through a different door, and exempting tag-named topics from the merge
metacontext gate makes such a merge easier to perform, not harder.

**The root cause is on the read side: name resolution stops at ACTIVE.**
`get_node_by_content` filters to active nodes, so a name whose node has been
retired resolves to nothing, whatever retired it.

**The fix.** `_tag_topic` and `_resolve_node_reference` follow a `MERGED` or
`CORRECTED` hit forward, through `merged_into` or `superseded_by`, to the live
successor. One place, and it closes enrichment, merge, and any rename anyone
writes later. Every tag in the §1.1 incident still had a successor to follow.

**This is a different edge set from `fold_lineage`, deliberately.**
`LINEAGE_FOLD_EDGE_TYPES` in `pipelines/query/lineage.py` excludes
`merged_into` on the grounds that a merged node's content now lives on the
survivor, so it never reaches search to be folded. Name resolution asks the
opposite question, *where did this name go*, and a merged node is exactly the
case that has an answer.

The description field remains the right fix for the retrieval half of §1.2.
It is not a substitute for this.

### 2.3 What gets embedded

**Recommendation: `content` and `description` together, through one function.**

A node's vector is written at ingest, when a tag or an entity name resolves to a
topic node, on supersession, on merge, and on both halves of the topic
hierarchy. Each of those spelling the join for itself would be the
two-definitions failure this project has hit more than once, so this needs one
function and one definition:

```python
def embedding_text(node: EpistemicNode) -> str:
    """The text a node is embedded on: its name, plus its description."""
```

For a Topic it returns `content` when the description is empty, and
`f"{content}. {description}"` otherwise. Facts and inferences return `content`.

**The risk, and why §1.2 does not measure it.** This changes the vector of
every topic that gains a description, and the merge and nomination bars were
tuned against content-only vectors. The pair measured in §1.2 is symmetric:
both texts carry a description. The hazard is asymmetric. A topic arriving at
ingest has no description and the stored topic it should match has one, so the
two texts diverge for a reason that has nothing to do with what they mean, and
topic similarity weakens exactly where it is supposed to catch a duplicate. So
the direction of the effect is unmeasured, and Stage 1.5 (§6) exists to supply
the real numbers before Stage 2 flips the embedding.

Embedding the name alone is the alternative. It is cheaper and changes no
existing score, and it gives up the retrieval half of the problem: a topic node
created from a tag stays invisible to a search on meaning, and the pairs in §1.2
keep their scores.

---

## 3. Enrichment writes the description

The enrichment branch of `apply_reflection` changes shape. `{topic_id,
new_content}` becomes `{topic_id, description}`, and the write becomes:

1. Set `description` on the topic in place.
2. Re-embed from `embedding_text`.
3. Journal `DecisionKind.ENRICHMENT` against the topic id.

No new node, no `CORRECTED` status, no edge migration, which is less work than
it does today.

**In place, rather than a new version.** `Topic.judged_by` is fixed at creation
and never edited, because a new wording is a new node. That rule is about the
claim. A description is advisory prose about a name that has not changed, and
`describe_relation` already set the precedent for prose: it replaces the text
and journals a second row; the first row is not edited, and the record's
`judged_by` never moves.

**The precedent comes with a condition.** What makes `describe_relation`'s
overwrite safe is that the second row holds the new text, so the prior wording
is recoverable from the journal. The `ENRICHMENT` row as written today carries
ids only. An in-place overwrite on top of that would make `description` the
one field on a topic that can be edited and the one field with no history. So
the write must keep the wording it replaced. Two places it could go, and §9
asks which:

- **The journal payload**, extending the `ENRICHMENT` row to carry the
  previous and new text. It puts the history where the decision already is,
  and it changes `journal`'s shape for every caller.
- **The topic's `metadata`**, appending the replaced text. It touches nothing
  else, and it puts a growing list on the node rather than in the record of
  decisions.

**This removes the need for a guard.** The obvious fix to §1.1 is to refuse
enrichment on a topic node created from a tag, using `created_from_tag`. If
enrichment cannot reach
`content` at all, nothing can split a tag, and the guard has nothing to do. It
is also one fewer "except tags" clause, of which reflection already has several.

---

## 4. Read paths

`search`, `find_nodes` and `topic_tree` return full model dumps, so a
description appears in their responses as soon as the field exists. Three
places need a decision rather than nothing:

- **`topic_tree`** returns ids and short content previews. A described topic
  should preview its description, since a preview exists to say what the thing
  is.
- **`store_decomposition`** takes an optional description per topic entry at
  ingest, so a topic arrives described instead of waiting for a reflect.
- **The visualisation** tooltip shows topic content, and should show the
  description where there is one.

---

## 5. The broken tags

Of the six topic nodes created from tags and rewritten in the §1.1 incident,
`issue-46` has been
repaired; `issue-16`, `issue-52`, `issue-53`, `issue-61` and `issue-62` are
still sentences holding their tags' edges. The repair is the one already run:
create the bare topic node, move its `tagged_with_topic` edges onto it keeping
their original `created_at` and `judged_by`, and prune the moved edges from any
merge-undo record that would otherwise restore a duplicate.

The sentences the enrichment wrote are good prose, and they are exactly what
the new field is for. Seeding each repaired node's `description` from the
sentence that displaced its name turns the damage into the field's first data,
after a person has read the five once.

---

## 6. Stages

**Stage 0, name resolution follows a retired node forward** (§2.2). **Built**,
in `pipelines/name_resolution.py`, alongside stage 1 of `TAG_IDENTITY.md`, which
edits the same two functions. `_tag_topic` and `_resolve_node_reference` resolve
a `MERGED` or `CORRECTED` hit through `merged_into` or `superseded_by` to the live
successor. A `HISTORICAL` node is deliberately not followed: its claim is still
right of its period, so its name has not moved. Tests: a tag whose node was
merged away resolves to the survivor and mints no second topic node; the same for a tag whose node was
superseded; a name that never existed still resolves to nothing; a chain of
two hops resolves to the end of it.

**Stage 1, additive, changes no behaviour. Built.** `Topic.description` on the
model, and `embedding_text` in `pipelines/embedding_text.py`, with every site
that embeds a node routed through it:

| Site | What it embeds |
|---|---|
| `_resolve_entity_topic`, `mcp/tools.py` | a source or publisher entity's topic node, on creation |
| `_tag_topic`, `mcp/tools.py` | the topic node a tag mints when its name resolves to nothing |
| `store_decomposition`, `mcp/tools.py` | every topic, fact and inference in a segment |
| parent synthesis, `apply_reflection` in `mcp/tools.py` | a topic node synthesised over its children |
| topic splitting, `apply_reflection` in `mcp/tools.py` | each subtopic a split creates |
| `supersede_node`, `pipelines/graph_construction/versioning.py` | the replacement node |
| `merge_nodes`, `pipelines/graph_construction/versioning.py` | the survivor |

Four further embed calls stay as they are, because what they embed is not a
node and so has no second field to join: the query text in
`pipelines/query/vector_search.py`, the relation label strings in
`pipelines/reflection/relation_consolidation.py`, the raw passage text in
`pipelines/segmentation/semantic_similarity.py`, and the material the split
sweep scores for internal variance in `mcp/tools.py`, which is the contents of
facts and inferences.

**No migration and no schema step.** The `topic` table is `SCHEMALESS` and
nodes serialise through `model_dump`, so an existing row simply has no
`description` key and Pydantic supplies the empty default on read.
`_SCHEMA_VERSION` is untouched: nothing needs rewriting.

Every description is empty, so every vector is byte-identical to what it was.
Tests: a topic round-trips its description through both backends and an
undescribed one reads back empty; a SurrealDB row with the key unset reads back
empty; a topic with no description embeds exactly as it did; `embedding_text`
is the only place the two fields are joined, and every embed call in the
package either reaches it or is named above as embedding something other than a
node.

**Stage 1.5, measurement. Stage 2 does not begin until it reports.** Seed a
dozen topic nodes created from tags with real descriptions and re-run the
pairwise sweep,
recording in this document:

- **Symmetric pairs**, both described. Does the `dev-session` cluster fall
  below 0.80, and do `claim-kind` and `claim_kind` stay above it?
- **Asymmetric pairs**, one described and one not, which is the ingest case of
  §2.3. How far does a described topic move from the same topic undescribed?

The asymmetric number decides whether the embedding changes at all. If a
described topic no longer matches its own undescribed restatement, embedding
the name alone is the answer and the description stays prose for readers.

**Stage 2, the half that pays.** Enrichment writes `description` in place,
keeps the wording it replaced (§3), and re-embeds. `store_decomposition`
accepts an optional description per topic. Read paths surface it. Tests:
enrichment leaves `content` byte-identical and the node id unchanged; the
replaced wording is recoverable; a described topic re-embeds; the §1.1 failure
is a regression test, tagging a document, enriching the tag, and tagging
again, asserting one topic node.

**Stage 3, repair.** The five topic nodes, with descriptions seeded per §5.
Verification is `find_nodes(tagged_with_topic=...)` returning the expected
count for each.

---

## 7. Rejected

**A `TagLabel` registry beside the graph**, copying `RelationLabel`. The
graph's only association mechanism is a node-to-node edge, and a tag exists to
be a node that gathers otherwise unrelated facts and inferences. A tag has
nothing at the far end of the edge unless it is itself a node. A relation
label can live off-graph because both of its endpoints are already real nodes.
A registry would also have to be kept in step with the nodes it describes, and
this proposal has one object where that has two.

**Tags stop being nodes.** The same argument, harder: it would mean a second
kind of association alongside the only one the graph has, plus thousands of
edges and every tag-named topic node migrated, plus every read path that treats
one as an ordinary topic.

**A description on the `tagged_with_topic` edge**, saying why this node
carries this tag. Rejected on three counts: it is per (node, tag) pair, so a
document with 30 nodes and 4 tags costs 120 sentences at ingest; nothing reads
it, and an unread field drifts; and most entries would restate the tag, which
the project's documentation rule already rejects for comments that only cite.
Edges also have no history, no `status` and no `updated_at`, so a description
on one could not be revised, only deleted and rewritten.

---

## 8. What this deliberately does not do

**Facts and inferences get no description.** A claim's wording is the claim,
and a second field beside it would be a place to put a softer version of the
same assertion, unattributed and unversioned.

---

## 9. Open

- Where the replaced wording lives when enrichment overwrites a description:
  the journal payload or the topic's `metadata` (§3).
- Whether Stage 0 should also cover `get_node_by_content`'s other callers, or
  stay in the two name-resolution sites where the question is *where did this
  name go*.
