# Tag identity: separators are spelling, not meaning

**Status: built, both stages (§5).** §7 records what review
settled. Where an unbuilt section says "does", read "would".

Two tags in one real graph name the same thing twice: `claim-kind` beside
`claim_kind`, and `design-decisions` beside `design decisions`. Reflect
nominates one of those pairs for merging and refuses the other, and the reason
it can do neither reliably is that it is comparing the names as text.

This proposes resolving a tag by a name normalised for case and separators, so
the duplicate is never created. It is the prevention half of the same
name-resolution problem `TOPIC_DESCRIPTIONS.md` §2.2 attacks from the retired-node
side, and §6 says why the two should land together.

---

## 1. What the merge bar actually sorts

`apply_reflection` merges topics only where every pair scores at least 0.92
cosine (`mcp/tools.py`). Scoring all 46,665 pairs of the 306 active topics
against their stored embeddings, eleven pairs clear that bar:

| Cosine | Pair | Should merge? |
|---|---|---|
| 0.9935 | `dev-session-2026-09-05` · `dev-session-2026-09-06` | no |
| 0.9911 | `dev-session-2026-08-12` · `dev-session-2026-07-22` | no |
| 0.9910 | `dev-session-2026-09-06` · `dev-session-2026-07-22` | no |
| 0.9902 | `dev-session-2026-09-05` · `dev-session-2026-07-22` | no |
| 0.9884 | `dev-session-2026-09-05` · `dev-session-2026-08-12` | no |
| 0.9848 | `dev-session-2026-08-12` · `dev-session-2026-09-06` | no |
| 0.9741 | `design-decisions` · `design decisions` | **yes** |
| 0.9723 | `dev-session-2026-09-04` · `dev-session-2026-07-22` | no |
| 0.9720 | `dev-session-2026-09-04` · `dev-session-2026-09-05` | no |
| 0.9686 | `dev-session-2026-09-04` · `dev-session-2026-08-12` | no |
| 0.9593 | `dev-session-2026-09-04` · `dev-session-2026-09-06` | no |

And one pair sits below it:

| 0.9196 | `claim-kind` · `claim_kind` | **yes** |

**Ten of the eleven admitted pairs must never merge**, since a dated session tag
names one day of work and merging two fuses their topic nodes. The bar rates
every one
of them as a better candidate than either pair that should merge, and excludes
one of those outright. On the twelve pairs it has an opinion about, it is right
twice.

That is a sorting failure rather than a calibration failure, and §4.1 records why
moving the number cannot fix it.

---

## 2. The change

**A tag is resolved by its name with case and separators normalised.**

```python
def tag_key(name: str) -> str:
    """A tag's identity, ignoring case and separators.

    A tag is a name, and `claim_kind` and `claim-kind` are one name written
    twice. Dates survive this untouched, which is the whole point: it collapses
    spelling and nothing else.
    """
    return re.sub(r"[\s_-]+", "", name.strip().lower())
```

Applied in the two places a tag name is resolved, `_tag_topic` and
`_resolve_node_reference`, both in `mcp/tools.py`:

1. Look the name up exactly, as today. This is the common case and it keeps the
   named content index, measured at 0.53 ms against 4.0 ms without it
   (`storage/surrealdb_adapter.py`).
2. On a miss, compare `tag_key` against the active topic nodes created from
   tags before creating
   anything. There are 88 in one real graph, so the scan is affordable on a path
   that was about to write a node anyway.

**The scan matches `created_from_tag` nodes only.** A tag is a name and a statement
topic is prose, so letting an extracted topic answer to a normalised tag name
would hand a tag's topic node to something nobody wrote as a tag.

**In `_resolve_node_reference` it applies to the Topic branch alone.** That function
resolves three things in order, a node id, a Topic name and a document's source
name, and only the middle one is a tag lookup. A normalised match against a
document filename would resolve `ISSUES.md` to `issuesmd`, which nothing asked
for.

**`tag_cache` is keyed by `tag_key` as well as by the raw name.** Within one
`store_decomposition` call the cache is consulted before storage, so a document
tagged both `claim_kind` and `claim-kind` misses the cache twice, misses storage
twice, and mints the pair this proposal exists to prevent. The cache has to
answer the same question the store does.

`store_decomposition` then reuses the existing tag rather than minting a second,
and `find_nodes(tagged_with_topic="claim-kind")` reaches the topic node written as
`claim_kind`. Where the spelling asked for differs from the one resolved to, the
response reports `resolved_to`, so a caller learns this graph's spelling rather
than silently getting a different tag from the one it named.

### 2.1 What it catches, measured

Normalising all 88 active tags collapses them to 86 keys. The two collisions are:

| Key | Names |
|---|---|
| `claimkind` | `claim-kind`, `claim_kind` |
| `designdecisions` | `design-decisions`, `design decisions` |

Both are genuine duplicates, and **no date pair collides**. Widening the same
test to all 306 active topics rather than tags alone produces those same two
collisions and no others, so nothing else in the graph is at risk of being
conflated by this.

Scored against the same twelve pairs §1 measures: the 0.92 bar gets two right,
`tag_key` gets twelve.

### 2.2 It also makes the eventual merge safe

Merging retires the losing source, and name resolution stops at ACTIVE, so today
a merge strands the losing spelling: the next document tagged `claim-kind` mints
a fresh topic node. That is why the `claim-kind` pair is left unmerged.

With `tag_key` in force the stranding cannot happen for this class, because the
losing spelling normalises to the survivor. Cleaning up the two existing
duplicates becomes safe rather than a trade.

---

### 2.3 An equal key clears the similarity bar

Topic merge refuses unless every pair of sources scores at least 0.92, and that
gate runs before the tag exemption. `claim-kind` against `claim_kind` scores
0.9196, so the merge that would clean the pair up is rejected.

**An equal `tag_key` passes the bar where every source is a tag.** That is the
argument of this document applied to the gate: an identical normalised name is a
stronger proof that two tags are one tag than any cosine between their spellings,
and §1 is the measurement showing cosine cannot make the call.

It stays narrow. A pair with different keys is judged by the bar as before, and a
merge with any statement topic among its sources is untouched.

## 3. Prevention, not repair

The duplicate is never created, so there is no nomination to review, no merge to
perform, and no verdict to record. `RELATION_LABELS.md` argues the same way for
descriptions on relation labels: an agent picking from a described vocabulary
never coins the fourth synonym, and the intervention moves from repair to
prevention.

It also removes work already sitting in the queue. Both duplicate pairs were
live nominations, and one of them, `claim-kind` against `claim_kind`, could not
be recorded either way while a tag stood in no metacontext: `one_claim` was
refused as a cross-metacontext pair and `distinct` would have been false. A tag
now stands in every metacontext it is used from, so two tags used from one world
are a same-metacontext pair and `one_claim` is available for them. Preventing
the duplicate is still worth more than being able to judge it.

---

## 4. Rejected, on measurement

### 4.1 Moving the similarity threshold

Lowering the bar from 0.92 to anything down to 0.85 admits exactly one new pair
on this graph, the one that should merge, and nothing else. It still fails, for
two reasons.

**It cannot separate them.** The ten pairs that must not merge score 0.959 to
0.994, above the pair that should merge at 0.974, and above the other one at
0.920. No single value has the wanted pairs on one side and the unwanted pairs
on the other, because the quantity being thresholded does not track the
distinction.

**The measurement does not generalise.** *One new pair on this graph today* is a
fact about 306 topics on one day, not a property of the rule. The bar is a
default in force on every graph, and it exists to stop a false merge, which is
the most damaging thing this system can do.

### 4.2 Comparing tags by the material they tag

If names are the wrong signal, the nodes a tag actually marks look like a better
one. Measured as the cosine between the centroids of each tag's tagged nodes:

| Pair | name | material |
|---|---|---|
| `claim-kind` · `claim_kind` | 0.9196 | **0.4305** |
| `design-decisions` · `design decisions` | 0.9741 | 0.7621 |
| `dev-session-2026-09-05` · `dev-session-2026-09-06` | 0.9935 | 0.7709 |
| `dev-session-2026-09-05` · `dev-session-2026-08-12` | 0.9884 | 0.7003 |
| `dev-session-2026-09-04` · `dev-session-2026-09-05` | 0.9720 | 0.7155 |

**It sorts worse.** The pair that most clearly should merge scores lowest of all
twelve on material, below every date pair. Two tags marking three facts between
them have centroids drawn from almost nothing, and a day's session tag averages
a whole day of mixed work into something bland enough to resemble any other
day's. The signal is weakest exactly where a tag is newest, which is where a
duplicate is most likely to have just been coined.

### 4.3 Edit distance

`claim-kind` differs from `claim_kind` by one character, and
`dev-session-2026-09-05` differs from `dev-session-2026-09-06` by one character
too. Any measure that counts characters without knowing which ones carry meaning
lands in the same place as §1.

---

## 5. Stages

**Stage 1, `tag_key`, the two resolution sites, the cache and the merge gate.**
**Built**, in `pipelines/name_resolution.py` and `mcp/tools.py`, alongside stage
0 of `TOPIC_DESCRIPTIONS.md`, which edits the same two functions. Additive: an exact hit behaves exactly as it does now, and only a miss consults
the normalised form. Tests: `claim_kind` resolves to a tag stored as
`claim-kind`; two dated session tags stay distinct; a name matching nothing
still creates one tag; the exact path is unchanged for a name with no
separators; one document tagging both spellings creates one tag, which is the
cache case; a statement topic whose content normalises to a tag name does not
answer to it; a document source name is not matched normally; an all-tag merge
whose sources share a key passes the similarity bar, and one whose sources do
not is still refused; `tag_key` collapses case, spaces, hyphens and underscores
and nothing else.

**Stage 2, clean up the two existing duplicates.** **Done** on 2026-09-07,
through `apply_reflection(merges=[...])` rather than a script of its own. `merge_nodes`
already migrates every source's edges onto a fresh survivor and retires the
sources, and tags are already exempt from the metacontext gate, so the only thing that
stood in the way was the similarity bar that §2.3 opens. Survivors: `claim_kind`,
which is the field name in the code and so the spelling an agent reading the code
will type, and `design-decisions`, which is both the older name and the hyphen
convention the rest of the tags follow. Safe only after Stage 1, per §2.2.

---

## 6. Relationship to `TOPIC_DESCRIPTIONS.md`

Both change the same two functions, and they answer different halves of one
question:

- **This document**: a name written two ways is one name. Resolution normalises.
- **`TOPIC_DESCRIPTIONS.md` §2.2, Stage 0**: a name whose node was retired still
  points somewhere. Resolution follows `merged_into` or `superseded_by` forward.

Neither subsumes the other, and landing them separately means editing
`_tag_topic` and `_resolve_node_reference` twice. They should go in together.

The description field remains the fix for the retrieval half, which neither of
these touches: a tag embedded on its name alone is invisible to a search on
meaning, and §4.2 shows that the material it marks is not a usable substitute.

---

## 7. Settled by review

1. **`[\s_-]+` is the right set.** It collapses `claim_kind` and `claim-kind`
   and leaves `reflect` and `reflection` alone. Unicode folding, punctuation
   beyond these three, and any form of stemming stay out.
2. **First written wins**, since resolution returns the existing node, and the
   response reports `resolved_to` where the spelling differs so a caller learns
   this graph's spelling rather than quietly getting a tag it did not name.
   Stage 2's survivors are named in §5.
3. **No stored key yet.** A `tag_key` field with its own index would make the
   miss path indexed rather than a scan, and a scan over 88 rows on a path that
   was about to write a node is not worth a field. Add it when a graph makes the
   cost measurable.
4. **Reflect keeps nominating tag pairs on name similarity.** After Stage 1 the
   equal-key class cannot recur, and the date pairs on this graph are already
   suppressed. Skipping every tag pair would also hide real synonyms that share
   no key, `reflect` and `reflection` among them. Revisit if the noise returns.
