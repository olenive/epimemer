# Descriptions: written by whoever creates the topic, reviewed when its material moves

**Status: built**, all three stages, 2026-09-12. Decisions in §2 were taken
2026-09-11; §5 breaks the work into the stages they were built in. The backfill
in §3 ran on the real graph on 2026-09-12.

`TOPIC_DESCRIPTIONS.md` gave a topic a `description` beside its name and made
enrichment write there. Two gaps remain. Nothing obliges anyone to write a
description when a topic is created, so on the real graph every topic node
created from a tag but five is still embedded on its name alone. And reflect
decides whether to nominate a topic for description by a length ratio, so a
topic described last week is nominated again while its material is long, and a
topic whose material changed is not nominated while the ratio holds. This
proposes two rules: the caller that creates a tag writes its description, and
reflect nominates a description for review exactly when the material under it
has changed since the description was last written or confirmed.

---

## 1. What the gaps cost

### 1.1 Undescribed tags are near-duplicates of each other

A topic node created from a tag embeds `content` alone until it has a
description (`TOPIC_DESCRIPTIONS.md` §2.3). Names cut from one template,
`dev-session-2026-09-10` and `dev-session-2026-09-11`, score 0.97 to 0.99, so
every reflect nominates every pair of them and the judge declines every pair,
for ever, since a declined pair is suppressed but a new dated tag arrives with
each session. §6.1 of `TOPIC_DESCRIPTIONS.md` measured what a description
does to that: the same pairs fall to 0.22 to 0.47.

The mechanism to fix it had been built and nothing drove it. Reflect's
enrichment scan could not reach a tag, because `gather_associated_material_for`
in `pipelines/reflection/topic_enrichment.py` followed `extracted_under_topic`
and `abstracts` and a tag holds neither: its material hangs off
`tagged_with_topic`. So the only path to a described tag was an agent
remembering to call `apply_reflection(enrichments=...)` by hand. The walk
follows `tagged_with_topic` now.

### 1.2 The ratio heuristic measures the wrong thing

`_should_enrich` nominated every topic whose material was at least three times
the length of its name and description together. That answers *is this topic
thin*, and it answers it identically on every run: a topic with a good
one-line description over forty facts was nominated on every reflect, and the
judge declined it on every reflect, and nothing recorded the decline. The
question reflect should ask is *has anything happened under this topic since
somebody last looked at what it says about itself*. A stale description over
ten facts, eight of them since archived, passed the ratio and was wrong. The
ratio now answers only for a topic nobody has described, where there is no
moment to measure change from.

---

## 2. The change

### 2.1 A new tag needs a description, refused otherwise

`store_decomposition` takes a new argument, `tag_descriptions: dict[str, str]`,
name to one line of prose. Every tag name in the call that resolves to no
existing topic node must have an entry; a call that creates a tag without one
is refused, and the refusal lists the names that need describing, so the
caller adds them and resends. Tags occur in two places in a call, the
document-level `tags` list and the `tags` on a topic entry, and one dict at
the call level covers both, which is why the argument is not a change to the
shape of `tags`.

Refused rather than warned, because the caller is the only party that knows
what the tag means at the moment it is minted, and a warning is read by the
next agent, not this one.

A description supplied for a tag that already exists is ignored with a
warning: the description on a live node is judged prose with a history trail,
and changing it is enrichment, which goes through `apply_reflection`. One
exception: an existing tag whose description is **empty** accepts the supplied
one, so the graph backfills as it is used, with no separate pass.

The new tag's node is written with `description` set, embedded on
`content. description` through `embedding_text`, and `description_reviewed_at`
(§2.3) set to its creation time, since the writer has just looked at it.

### 2.2 Reflect nominates a description when the material under it moved

The enrichment scan keeps its shape, one nomination per topic per run, and
changes its test. A topic is nominated when any node in its material was
created, archived or superseded after the topic's `description_reviewed_at`.
The material walk gains `tagged_with_topic`, so tags are topics here like any
other. Archival and supersession count as changes, on the reading in §1.2: a
description written over material that has since been retired is stale.

A topic that has never been reviewed (`description_reviewed_at` is `None`)
keeps the ratio heuristic, because there is no reference time to measure
change from. Once reviewed, the ratio is never consulted for it again.

The nomination carries what a reviewer needs and no more:

- `topic_id`, `current_content`, `current_description`, as now.
- `since`: the `description_reviewed_at` the change is measured from.
- `changed_material`: the nodes created, archived or superseded since then,
  newest first, capped at twenty, each with content and what happened to it.
- `changed_count` and `material_count`, so the reviewer knows what the cap hid
  and how much the description is standing over.

The delta, not a random sample, is the sample. The question is whether the
description still covers what came in, and the material before `since` was
covered when the description was last written or confirmed. For a
never-reviewed topic there is no delta, and the nomination carries a capped
sample of the material, as the ratio path does today.

### 2.3 Two answers, both clear the nomination

`apply_reflection` already takes `enrichments=[{topic_id, description}]`. It
gains `descriptions_confirmed: list[str]`, topic ids whose description was
read against the change and still fits. Both set `description_reviewed_at`
to now. An enrichment journals `ENRICHMENT` as it does; a confirmation
journals a new `DecisionKind.DESCRIPTION_REVIEW`, one row for the batch, the
way `RETENTION` records that an archival candidate was re-read and stands.
Its own kind rather than an enrichment with unchanged text, because the
journal should show that somebody looked and chose not to change it, and a
reviewer selecting `ENRICHMENT` should get rows where a description moved.

A nomination nobody answers comes back on the next reflect, unchanged. That is
the existing rule for an unjudged pair and it is the right one here: the
description is unreviewed until somebody reviews it.

`description_reviewed_at` is a field on `Topic`, `datetime | None`, set by
creation with a description, by `described()`, by confirmation, and (since
2026-09-12) by a split decline, `apply_reflection(splits_declined=...)`: a
split verdict is a judgment about the same material a description covers, so
the two share one moment for *last stood behind*, and the split scan skips a
topic whose material has not moved since it. Read by reflect alone. It lives beside `description` and `description_history`, which
is where the node's own description state already is; a node's description
is mutable and this records when it was last stood behind.

### 2.4 What changes for the caller

An agent ingesting a document with a fresh session tag writes one line for it
in the same call. `epimemer_prompts/DEFAULT.md` says so in the ingest section,
and the `store_decomposition` description in `mcp/server.py` names the
argument and the refusal. At reflect, the enrichment nominations arrive less
often and each says what changed; the agent answers every one, with a new
sentence or a confirmation, as it already must for pairs.

### 2.5 What does not change

Enrichment still writes `description` and never `content`. `described()`
still keeps the replaced wording in `description_history`. The embedding rule
stays as measured: `content. description` for a topic node created from a tag,
`content` alone for a statement topic. No schema step: `description_reviewed_at`
is absent on old rows and reads back `None`, which is the never-reviewed case.

---

## 3. Backfill on an existing graph

**Run on `memory`, 2026-09-12.** Every topic written before the field existed
has `description_reviewed_at` unset, so the first reflect after shipping ran
the ratio path over all of them and nominated 306 topics: 98 topic nodes
created from tags and 208 statement topics. One sitting answered all of them:
94 tags described, the four already described confirmed, and the 208 statement
topics confirmed on their names, since a statement topic's `content` already
says what it covers (§6). The reflect straight after returned zero enrichment
candidates and zero similar pairs, where the one before had returned thirteen,
every one a pair of bare tag names. The measurements that predicted this are
in `TOPIC_DESCRIPTIONS.md` §6.1.

---

## 4. Rejected

### 4.1 Warn on a new tag without a description

The warning lands in the response to the call that already minted the tag,
and the caller that reads it has moved on. The ISSUES entry "Tag names embed
so alike" has been open through several sessions of agents who knew about
descriptions and wrote none; a warning would not have changed that.

### 4.2 Mark the topic at ingest with a stored flag

A `needs_review` flag written by every ingest, archival and supersession that
touches material under a topic means writing to the topic row on every one of
those paths, from more than one call site, across two backends, and racing on
a node like `issue-53` with over a hundred nodes under it. The comparison
against `description_reviewed_at` is computed at reflect from material reflect
already fetches for every topic, and costs no write anywhere.

### 4.3 Derive the review time from the journal

The last `ENRICHMENT` or `DESCRIPTION_REVIEW` row naming the topic would be a
review time that is derived rather than stored, which is how `DecisionRecord`
treats review state. It would cost a journal scan per topic per reflect or a
new protocol method to batch it, and it would put one piece of the node's
description state in a different place from `description` and
`description_history`. The node field is the denormalised copy and the journal
row is the record of who, the same split `judged_by` has.

### 4.4 A random sample of the material

Random sampling answers *what is this topic about*, which is the first-time
question. After the first time the question is *what changed*, and the delta
answers it exactly, with a cap for the rare topic where a hundred nodes landed
in one interval.

### 4.5 Reuse `ENRICHMENT` for a confirmation

A journal row that says a description was enriched, with the same text before
and after, misreports the act. `RETENTION` exists for the same reason beside
`ARCHIVAL`.

---

## 5. Stages

**Stage 1, the field and the review rule. Built.** `Topic.description_reviewed_at`;
`described()` sets it; `gather_associated_material_for` follows
`tagged_with_topic` and returns nodes with their timestamps rather than
content strings; a pure `changed_since(material, since)` in
`topic_enrichment.py`; the enrichment scan nominates on change for reviewed
topics and on ratio for unreviewed ones, with the nomination shape in §2.2;
`apply_reflection(descriptions_confirmed=...)` and
`DecisionKind.DESCRIPTION_REVIEW`. Tests: a described topic with no change
since is not nominated; one with a fact added since is, and the nomination
carries that fact and not the older ones; one with a fact archived since is;
one with a fact superseded since is; a confirmation sets the time and journals
the kind; an enrichment sets the time; an unanswered nomination returns on the
next reflect; a tag with tagged nodes is nominated through `tagged_with_topic`;
an unreviewed topic keeps the ratio path; the cap holds at twenty with the
count reporting the rest; both backends round-trip the field and an old row
reads `None`. The tests are in `tests/mcp/test_description_review.py` and
`tests/pipelines/test_topic_enrichment.py`; the round trip is in
`tests/storage/test_storage_parity.py`, beside the `description` pair.

**Two decisions the section left open.** A nomination on the never-reviewed
path carries `sample` rather than a `changed_material` full of nulls, since the
two lists answer different questions and `since: null` already says which one
this is. And where a node both arrived and was retired since `since`, the
retirement is the change reported: it is the later of the two, and it is what
decides what the topic now stands over.

**Stage 2, the ingest rule. Built.** `tag_descriptions` on `store_decomposition`
through `server.py` and `tools.py`; refusal listing the missing names; ignore
with warning on an existing described tag; accept on an existing undescribed
one; `description_reviewed_at` set on creation. Tests: a new tag without an
entry is refused and nothing is written; with an entry it is written described
and embedded on `content. description`; an existing described tag keeps its
description and the call warns; an existing undescribed tag takes the
description; a tag on a topic entry is covered by the call-level dict; a call
with no new tags needs no dict.

**The warning is an advisory**, through the mechanism every other one uses:
`AdvisoryKind.DESCRIPTION_NOT_WRITTEN`, classified `escalates`, so the response
carries it in `warnings` and nothing is journalled against the ingest. That
classification is the one the stance field actually decides — *does this argue
the call was wrong* — and the answer is no: the ingest stands, and one write
inside it was declined. Journalling it would put a
`proceeded_despite_advisory` row on every ingest by an agent that re-sends its
whole tag dictionary, which is exactly the swamping the kind was kept narrow to
avoid. A blank line is treated as no description, so the requirement cannot be
met by a key with nothing in it.

**Stage 3, prose. Built.** `DEFAULT.md` ingest and reflect sections; `server.py`
descriptions for `store_decomposition`, `reflect` and `apply_reflection`;
`docs/REFLECTION.md` for the review rule; `TOPIC_DESCRIPTIONS.md` status and
§9 pointer; the ISSUES entry "Tag names embed so alike" was deleted once the
backfill in §3 had run on the real graph.

One release. Stage 2 without Stage 1 obliges callers to write descriptions
that nothing then reviews; Stage 1 without Stage 2 reviews descriptions that
nothing obliges anyone to write.

---

## 6. Open

1. **Whether a statement topic created in `store_decomposition` should also
   require a description.** The refusal in §2.1 is for tags, where the name is
   the whole node. A statement topic's `content` already says what it covers,
   and `TOPIC_DESCRIPTIONS.md` §6.1 measured that describing it moves its
   embedding for little gain. Left optional.
2. **The cap of twenty.** Chosen to fit a reflect response with several
   nominations. If a real graph shows topics routinely exceeding it, the fix is
   a per-call argument, and the count already reports what was hidden.
