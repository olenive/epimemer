# Topic descriptions: a name that does not move

A topic carries two texts with two jobs: `content` is the name the graph joins
on, and `description` is the prose that says what the topic is. Whoever
creates a tag writes its description, and reflect asks for a description to be
reviewed exactly when the material under it has moved since somebody last
stood behind it. The code is `Topic.description`, `embedding_text` in
`epimemer/pipelines/embedding_text.py`, `described` and `changed_since` in
`epimemer/pipelines/reflection/topic_enrichment.py`, `resolve_name` in
`epimemer/pipelines/name_resolution.py`, and `tag_descriptions` on
`store_decomposition`. `docs/REFLECTION.md` §6 has enrichment and description
review as a caller sees them.

---

## 1. Why a name and a description

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
that have nothing to do with what it means. A description separates them
(§6.1).

---

## 2. Names and embeddings

### 2.2 `content` is the name, and name resolution follows it

Topic merge has to be free to name its survivor, so a lookup that stopped at
ACTIVE would split a tag the way §1.1 describes: merge `design decisions` and
`design-decisions` into `design-decisions`, then ingest a document tagged
`design decisions`, and the lookup would find no live node with that name and
mint a fresh one.

So `resolve_name`, which both call sites use, follows a `MERGED` or
`CORRECTED` hit forward, through `merged_into` or `superseded_by`, to the live
successor. A `HISTORICAL` node is not followed: its claim is still right of
its period, so its name has not moved. An `ARCHIVED` one is not either, since
`restore` reverses an archival rather than replacing the node.

`rename_topic` is the deliberate exception: it renames in place, keeping the
old name on the node's `previous_names` trail, and the old name stops finding
the topic.

**This is a different edge set from the lineage fold, deliberately.**
Retrieval's fold leaves out `merged_into`, because a merged node's content now
lives on the survivor and never reaches search to be folded. Name resolution
asks the opposite question, *where did this name go*, and a merged node is
exactly the case that has an answer.

### 2.3 What gets embedded

**One function decides: `embedding_text`.** Every site that embeds a node goes
through it, and a test asserts that no other module joins the two fields,
because each site spelling the join for itself would be two definitions of
one thing. Text that is not a node (a search query, relation label strings,
raw passage text, the material the split sweep scores) has no second field and
is embedded directly.

For a topic node created from a tag with a description, `embedding_text`
returns `f"{content}. {description}"`. For every other node it returns
`content`.

**Why the join is that narrow.** Joining a description changes a topic's
vector, and the merge and nomination bars were tuned against content-only
vectors. The hazard is asymmetric: a topic arriving at ingest has no
description and the stored topic it should match has one, so topic similarity
weakens exactly where it is supposed to catch a duplicate. For a tag that
loss lands on a path that resolves by name anyway; for a statement topic it
would land on merge (§6.1). Embedding the name alone everywhere would leave a
topic node created from a tag invisible to a search on meaning.

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

**The prior wording is kept.** What makes `describe_relation`'s overwrite safe
is that the second row holds the new text. The enrichment row carries ids
only, so the write keeps the wording it replaced as an append-only
`description_history` in the topic's `metadata`, beside
`metacontext_reassignments` and the `rejudge` trail, rather than changing the
journal row's payload shape that every caller shares.

Enrichment cannot reach `content` at all, so it needs no guard against
splitting a tag.

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
says what it covers, and describing it moves its embedding for little gain
(§6.1).

### 4.2 Reflect asks for review when the material under a description moved

The question is *has anything happened under this topic since somebody last
looked at what it says about itself*. A length ratio would answer *is this
topic thin* instead, identically on every run. So a topic is nominated when
any node in its material was created, archived or superseded after its
`description_reviewed_at`; archival and supersession count, because a
description written over material since retired is stale. A topic never
reviewed has no moment to measure from, and only there does the ratio still
decide.

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
statement topics on their names. Once the tags are described, the similar
pairs that were only bare tag names stop being nominated.

---

## 5. The broken tags

A graph enriched before the name and the description were split may hold tags
whose name was rewritten into a sentence. `epimemer tags repair` finds them and
asks about each one, since only a person who knows what the tag was for can say
which wording is the name; the repair puts the name back and keeps the sentence
as the description.

---

## 6. Measurement

### 6.1 What the descriptions measure

Fourteen tag names from one real graph, each given a one-sentence description
of the kind enrichment would write, embedded on `all-MiniLM-L6-v2` through
`embedding_text`:

- **Both sides described.** Every pair of dated session tags falls from 0.97
  to 0.99 bare to between 0.22 and 0.47, below the 0.85 topic nomination bar
  (`reflect`'s `similarity_threshold`), and both genuine duplicates rise above
  0.97 (`claim-kind` · `claim_kind` 0.920 to 0.978, `reflect` · `reflection`
  0.814 to 0.987). Of 91 described pairs, exactly two clear 0.80, the two that
  should.
- **A bare name against its own described node**, the ingest case of §2.3,
  scores 0.44 to 0.73, and six of the fourteen bare names sit nearer some
  other described node than their own. That does not reach ingest, because a
  tag resolves by name, exactly and then up to spelling. It reaches `search`
  on the bare name, where a query of `reflect` scores 0.476 against the
  described `reflect` while a query on what reflect *does* now finds it;
  `find_nodes(tagged_with_topic=...)` keeps the exact-name path.
- **Statement topics**, four real ones with a sentence of description each,
  score 0.86 to 0.96 against their own described selves: all above the 0.85
  nomination bar, two below the 0.92 merge bar.

**Decision: join the description into the embedding only where `content` is a
name.** For a topic node created from a tag the description is the whole of
its meaning, the symmetric gain is decisive, and the asymmetric loss lands on
a path that resolves by name. For a statement topic the content already
carries the meaning, and the loss is a merge refusal at the bar half the time.

---

## 7. Rejected

- **A `TagLabel` registry beside the graph**, copying `RelationLabel`. A tag
  exists to be a node that gathers otherwise unrelated facts and inferences,
  and the graph's only association mechanism is a node-to-node edge; a
  relation label can live off-graph because both its endpoints are already
  real nodes. A registry would also have to be kept in step with the nodes it
  describes.
- **Tags stop being nodes.** The same argument, harder: a second kind of
  association, every tag-named topic node migrated, and every read path that
  treats one as an ordinary topic changed.
- **A description on the `tagged_with_topic` edge**, saying why this node
  carries this tag. It costs a sentence per (node, tag) pair at ingest,
  nothing reads it, and edges have no history to revise it with.
- **A stored `needs_review` flag on the topic.** A write to the topic row on
  every ingest, archival and supersession that touches its material, racing
  on a busy topic. Comparing against `description_reviewed_at` at reflect
  costs no write anywhere.
- **Deriving the review time from the journal.** A journal scan per topic per
  reflect, or a new protocol method to batch it, and one piece of the node's
  description state kept apart from `description` and `description_history`.
  The node field is the denormalised copy and the journal row is the record of
  who, the same split `judged_by` has.

---

## 8. Facts and inferences get no description

A claim's wording is the claim, and a second field beside it would be a place
to put a softer version of the same assertion, unattributed and unversioned.

---

## 9. Open

- Whether following a retirement forward should also cover the resolution of
  a source or publisher entity's name, which still looks the name up among
  active nodes alone, or stay in the two name-resolution sites where the
  question is *where did this name go*.
