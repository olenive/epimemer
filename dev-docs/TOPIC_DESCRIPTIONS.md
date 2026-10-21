# Topic descriptions: a name that does not move

A design, built: the code is `Topic.description`, `embedding_text` in `epimemer/pipelines/embedding_text.py`, `described` and `changed_since` in `epimemer/pipelines/reflection/topic_enrichment.py`, `resolve_name` in `epimemer/pipelines/name_resolution.py`, and `tag_descriptions` on `store_decomposition`.

A topic carries two texts with two jobs: `content` is the name the graph joins
on, and `description` is the prose that says what the topic is. Whoever
creates a tag writes its description, and reflect asks for a description to be
reviewed exactly when the material under it has moved since somebody last
stood behind it. `docs/REFLECTION.md` §6 has enrichment and description review
as a caller sees them.

---

## 1. The problem

### 1.1 A topic's content is its identity

Topics are joined by name on `content`: `store_decomposition` resolves the
topic node a tag names by looking for a topic whose content is the tag name,
and `find_nodes(tagged_with_topic=...)` resolves a name the same way. Anything
that moves `content` splits the tag: the write path mints a second topic node,
and the read path finds nothing and returns an empty list, with no error. So
nothing that only describes a topic may touch its `content`.

### 1.2 What the names measure

A topic node created from a tag, with no description, is embedded on its name
alone, and a name-only embedding scores names cut from one template above
genuine duplicates spelled two ways, because both kinds of pair have nearly
identical strings (`TAG_IDENTITY.md` §1.1 has the pairs). Such a node is
invisible to a search on meaning, and it looks like its neighbours for reasons
that have nothing to do with what it means. A description separates them;
§6.1 measures by how much.

---

## 2. The change

### 2.2 `content` is the name, and name resolution follows it

*Nothing rewrites a topic's `content`* is too weak a rule, because topic merge
has the same effect and has to be free to name its survivor. A merge retires
its sources as `MERGED` and creates a survivor whose content the agent chooses.
Merge `design decisions` and `design-decisions` into `design-decisions`, then
ingest a document tagged `design decisions`: a lookup that stops at ACTIVE
finds no node with that name and mints a fresh topic node, the split of §1.1
arriving through a different door.

**The root cause is on the read side: a lookup that stops at ACTIVE.** A name
whose node has been retired resolves to nothing, whatever retired it.

**The fix.** `resolve_name`, which both call sites use, follows a `MERGED` or
`CORRECTED` hit forward, through `merged_into` or `superseded_by`, to the live
successor. A `HISTORICAL` node is not followed: its claim is still right of
its period, so its name has not moved. An `ARCHIVED` one is not either, since
`restore` reverses an archival rather than replacing the node. One place, and
it closes both enrichment and merge.

`rename_topic` is the deliberate exception: it renames in place, keeping the
old name on the node's `previous_names` trail, and the old name stops finding
the topic.

**This is a different edge set from the lineage fold, deliberately.** Retrieval's
fold leaves out `merged_into`, because a merged node's content now lives on
the survivor and never reaches search to be folded. Name resolution asks the
opposite question, *where did this name go*, and a merged node is exactly the
case that has an answer.

### 2.3 What gets embedded

**One function decides: `embedding_text`.** Every site that embeds a node goes
through it, and a test asserts that no other module joins the two fields,
because each site spelling the join for itself would be the two-definitions
failure this project has hit more than once. Text that is not a node (a search
query, relation label strings, raw passage text, the material the split sweep
scores) has no second field and is embedded directly.

For a topic node created from a tag with a description, `embedding_text`
returns `f"{content}. {description}"`. For every other node it returns
`content`.

**Why the join is that narrow.** Joining a description changes a topic's
vector, and the merge and nomination bars were tuned against content-only
vectors. The hazard is asymmetric: a topic arriving at ingest has no
description and the stored topic it should match has one, so the two texts
diverge for a reason that has nothing to do with what they mean, and topic
similarity weakens exactly where it is supposed to catch a duplicate. §6.1
measures both directions.

Embedding the name alone everywhere was the alternative. It is cheaper and
changes no existing score, and it gives up the retrieval half of the problem: a
topic node created from a tag stays invisible to a search on meaning, and the
pairs in §1.2 keep their scores.

---

## 3. Enrichment writes the description

Enrichment sets `description` on the topic in place, re-embeds it, and
journals the act. No new node, no `CORRECTED` status, no edge migration.

**In place, rather than a new version.** `Topic.judged_by` is fixed at creation
and never edited, because a new wording is a new node. That rule is about the
claim. A description is guidance about a name that has not changed, and
`describe_relation` set the precedent for prose: it replaces the text and
journals a second row; the first row is not edited, and the record's
`judged_by` never moves.

**The precedent comes with a condition.** What makes `describe_relation`'s
overwrite safe is that the second row holds the new text, so the prior wording
is recoverable from the journal. The enrichment row carries ids only, so an
in-place overwrite would make `description` the one field on a topic that can
be edited and the one field with no history. So the write keeps the wording it
replaced, as an append-only `description_history` in the topic's `metadata`:
the journal row's payload shape is shared by every caller, and a node's own
history already has a place on the node, beside `metacontext_reassignments`
and the `rejudge` trail.

**This removes the need for a guard.** The obvious fix to §1.1 is to refuse
enrichment on a topic node created from a tag. Enrichment cannot reach
`content` at all, so nothing can split a tag, and the guard would have nothing
to do. It is also one fewer "except tags" clause, of which reflection already
has several.

---

## 4. Who writes a description, and when it is reviewed

### 4.1 A new tag needs a description, refused otherwise

`store_decomposition` takes `tag_descriptions`, a line of prose per tag name,
and a call that would create a tag without one is refused having written
nothing, naming the tags that need describing. Refused rather than warned,
because the caller is the only party that knows what the tag means at the
moment it is minted, and a warning is read by the next agent, not this one.

A description supplied for a tag that already has one is not written, since
changing it is enrichment, which goes through `apply_reflection`. The response
carries a `description_not_written` warning that escalates rather than
objects, so nothing is journalled against the ingest: the ingest stands and
one write inside it was declined, and journalling it would put a
`proceeded_despite_warning` row on every ingest by an agent that re-sends its
whole tag dictionary. An existing tag whose description is empty accepts the
supplied one, so the graph backfills as it is used.

A statement topic may carry a description and need not: its `content` already
says what it covers, and §6.1 measured that describing it moves its embedding
for little gain.

### 4.2 Reflect asks for review when the material under a description moved

The question reflect should ask is *has anything happened under this topic
since somebody last looked at what it says about itself*. A length ratio
answers *is this topic thin* instead, identically on every run, so a good
one-line description over forty facts was nominated on every reflect while a
stale description over ten facts, eight of them since archived, passed. So a
topic is nominated when any node in its material was created, archived or
superseded after its `description_reviewed_at`. Archival and supersession
count, because a description written over material that has since been retired
is stale. A topic never reviewed has no moment to measure from, and only there
does the ratio still decide.

The nomination carries the change since that moment rather than a random
sample, because the material before it was covered when the description was
last written or confirmed. The change is capped at twenty entries, chosen to
fit a reflect response with several nominations; the counts beside it say
what the cap hid, and if real graphs routinely exceed it the fix is a per-call
argument.

`description_reviewed_at` is set by creation with a description, by an
enrichment, by a confirmation and by a split decline: a split verdict is a
judgment about the same material a description covers, so the two share one
moment for *last stood behind*. A confirmation journals its own
`description_review` kind rather than an enrichment with unchanged text,
because the journal should show that somebody looked and chose not to change
it.

### 4.3 Backfill on an existing graph

A graph with no reviewed topic nominates every topic on its first reflect, and
answering those nominations is the backfill: describe the tags, and confirm
statement topics on their names. Measured on one real graph on 2026-09-12: 306
topics nominated, 98 of them topic nodes created from tags, all answered in
one sitting, and the reflect straight after returned no similar pairs where
the one before had returned thirteen, every one a pair of bare tag names.

---

## 5. The broken tags

A graph enriched before the name and the description were split may hold tags
whose name was rewritten into a sentence. `epimemer tags repair` finds them and
asks about each one, since only a person who knows what the tag was for can say
which wording is the name; the repair puts the name back and keeps the sentence
as the description.

---

## 6. Measurement

Measured 2026-09-10, with nothing written to the graph. The question was
whether a described topic still matches its own undescribed restatement, and
the answer splits by what `content` is: a name does not, a statement mostly
does.

### 6.1 What the descriptions measure

Fourteen tag names from one real graph, each given a one-sentence description
of the kind enrichment would write, embedded on `all-MiniLM-L6-v2` through
`embedding_text`.

**Symmetric pairs, both described.** The sort §1.2 asked for happens:

| Pair | Bare | Described |
|---|---|---|
| `dev-session-2026-09-05` · `dev-session-2026-09-06` | 0.994 | 0.443 |
| `dev-session-2026-08-12` · `dev-session-2026-07-22` | 0.991 | 0.474 |
| `dev-session-2026-09-05` · `dev-session-2026-07-22` | 0.990 | 0.216 |
| `dev-session-2026-09-04` · `dev-session-2026-09-05` | 0.972 | 0.422 |
| `dev-session-2026-09-04` · `dev-session-2026-09-07` | 0.968 | 0.399 |
| `claim-kind` · `claim_kind` | 0.920 | 0.978 |
| `reflect` · `reflection` | 0.814 | 0.987 |
| `provenance` · `retrieval-provenance` | 0.804 | 0.572 |

Every date pair falls from above the 0.92 merge bar to below the 0.85 topic
nomination bar (`reflect`'s `similarity_threshold`), and both genuine
duplicates rise above 0.97. Of 91 described pairs, exactly two clear 0.80, and
they are the two that should. The `provenance` pair falls because its
descriptions say two different things, one about `sourced_from` edges and one
about the retrieval log, which is the description deciding a question the names
could not.

**Asymmetric pairs, a bare name against its own described node**, which is the
ingest case of §2.3. It fails:

| Name | Self, bare vs described | Nearest described node |
|---|---|---|
| `dev-session-2026-09-05` | 0.439 | `dev-session-2026-08-12`, 0.595 |
| `dev-session-2026-09-07` | 0.471 | `dev-session-2026-08-12`, 0.598 |
| `reflect` | 0.476 | itself |
| `metacontext` | 0.498 | itself |
| `claim-kind` | 0.690 | itself |
| `retrieval-provenance` | 0.728 | itself |

Across all fourteen the self score runs 0.44 to 0.73, below the nomination bar
in every case, and six of the fourteen bare names sit nearer some other
described node than their own. By vector, a name cannot find its described
self.

**That does not reach ingest, because a tag never resolves by vector.** Both
call sites resolve by name, exactly and then up to spelling, so the described
node is found by the string and the vector is never asked. Where it does reach
is `search` on the bare name: a query of `reflect` against the described
`reflect` scores 0.476 where it scored 1.0 before, while a query on what
reflect *does* now finds it. That is the trade the field makes, and
`find_nodes(tagged_with_topic=...)` keeps the exact-name path.

**Statement topics**, measured on four real ones with a sentence of
description each: a topic against its own described self scores 0.946, 0.875,
0.861 and 0.961. All four stay above the 0.85 nomination bar and two fall
below the 0.92 merge bar. For a statement the description is a small addition
to a long text, so the shift is small, and still large enough that topic merge
would refuse to merge a described statement topic with its own undescribed
restatement half the time.

**Decision: join the description into the embedding only where `content` is a
name.** For a topic node created from a tag the description is the whole of its
meaning, the symmetric gain is decisive, and the asymmetric loss lands on a
path that resolves by name anyway. For a statement topic the content already
carries the meaning, the gain is unmeasured and the loss is a merge refusal at
the bar. That is one more `created_from_tag` branch, and a principled one: it
is the name-versus-statement distinction that `embedding_text` exists to draw,
not an exemption from a rule.

---

## 7. Rejected

**A `TagLabel` registry beside the graph**, copying `RelationLabel`. The
graph's only association mechanism is a node-to-node edge, and a tag exists to
be a node that gathers otherwise unrelated facts and inferences. A tag has
nothing at the far end of the edge unless it is itself a node. A relation
label can live off-graph because both of its endpoints are already real nodes.
A registry would also have to be kept in step with the nodes it describes, and
this design has one object where that has two.

**Tags stop being nodes.** The same argument, harder: it would mean a second
kind of association alongside the only one the graph has, plus thousands of
edges and every tag-named topic node migrated, plus every read path that treats
one as an ordinary topic.

**A description on the `tagged_with_topic` edge**, saying why this node
carries this tag. It is per (node, tag) pair, so a document with 30 nodes and
4 tags costs 120 sentences at ingest; nothing reads it, and an unread field
drifts; and edges have no history, so a description on one could not be
revised, only deleted and rewritten.

**A stored `needs_review` flag on the topic**, written by every ingest,
archival and supersession that touches its material. That is a write to the
topic row on every one of those paths, from more than one call site, across
every backend, racing on a busy topic. The comparison against
`description_reviewed_at` is computed at reflect from material reflect already
fetches, and costs no write anywhere.

**Deriving the review time from the journal.** The last enrichment or
description-review row naming the topic would be a review time that is derived
rather than stored, which is how the journal treats review state. It would
cost a journal scan per topic per reflect or a new protocol method to batch
it, and it would put one piece of the node's description state in a different
place from `description` and `description_history`. The node field is the
denormalised copy and the journal row is the record of who, the same split
`judged_by` has.

---

## 8. What this deliberately does not do

**Facts and inferences get no description.** A claim's wording is the claim,
and a second field beside it would be a place to put a softer version of the
same assertion, unattributed and unversioned.

---

## 9. Open

- Whether following a retirement forward should also cover the resolution of
  a source or publisher entity's name, which still looks the name up among
  active nodes alone, or stay in the two name-resolution sites where the
  question is *where did this name go*.
