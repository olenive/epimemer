# Topic nodes created from tags

A design, built: the code is `tag_key` and `tag_by_key` in `epimemer/pipelines/name_resolution.py`, and `created_from_tag`, `combined_metacontext_set` and `stamp_tag_metacontexts` in `epimemer/pipelines/metacontexts.py`, called from `epimemer/mcp/tools.py`.

A tag is the string passed in `tags=[...]`, and it resolves, by name, to a
topic node created from a tag. Two decisions shape that node: which node a
name resolves to (§1), and which metacontexts it stands in (§2).
`TOPIC_DESCRIPTIONS.md` §2.2 answers a third question, where a name whose node
was retired points: resolution follows `merged_into` or `superseded_by`
forward. Resolution runs the exact name first, following any retirement
forward, then the normalised name.

---

## 1. Identity: separators are spelling, not meaning

**A tag is resolved by its name with case and the separators `[\s_-]`
normalised away**, and the duplicate is never created. Prevention beats
repair: there is no nomination to review, no merge to perform, and no verdict
to record.

Resolution looks the name up exactly first, which is the common case and
keeps the named content index, and on a miss compares the normalised key
against the active topic nodes created from tags before creating anything.
Two limits keep it narrow:

- **Tags only.** A tag is a name and a statement topic is prose, so letting an
  extracted topic answer to a normalised tag name would hand a tag's topic
  node to something nobody wrote as a tag.
- **The Topic branch only**, where a reference could be a node id, a Topic
  name or a document's source name. A normalised match against a filename
  would resolve `ISSUES.md` to `issuesmd`, which nothing asked for.

Where the spelling asked for differs from the one resolved to, the
`store_decomposition` response lists it under `tags_resolved_to`, so a caller
learns this graph's spelling rather than silently getting a different tag from
the one it named. `rename_topic` refuses a new name that another active topic
already answers to up to spelling, since the rename would leave two nodes
answering to one name.

### 1.1 What the similarity bar sorts

Topic merge requires every pair of sources to score at least 0.92 cosine.
Scoring all 46,665 pairs of the 306 active topics on one real graph, eleven
pairs cleared that bar. Ten were pairs of dated session tags, from
`dev-session-2026-09-05` · `dev-session-2026-09-06` at 0.9935 down to 0.9593,
which name different days of work and must never merge. The eleventh was
`design-decisions` · `design decisions` at 0.9741, which should. The other
genuine duplicate, `claim-kind` · `claim_kind`, sat below the bar at 0.9196.
On the twelve pairs it has an opinion about, the bar is right twice.

Normalising the 88 active tags on that graph collapses them to 86 keys, and
the two collisions are exactly those two duplicates. Widened to all 306
topics, the same two collide and no others. `tag_key` gets all twelve pairs
right. The bar's failure is one of sorting rather than calibration (§1.3).

### 1.2 An equal key clears the similarity bar

`claim-kind` against `claim_kind` scores 0.9196, so without an exception the
merge that cleans the pair up is refused. **An equal `tag_key` passes the bar
where every source is a tag**: an identical normalised name is a stronger
proof that two tags are one tag than any cosine between their spellings, and
§1.1 shows cosine cannot make the call. It stays narrow: a pair with different
keys is judged by the bar, and a merge with any statement topic among its
sources is untouched. An existing equal-key pair is therefore cleaned up
through `apply_reflection(merges=[...])`, with no script of its own.

A duplicate that does exist can also be judged: two tags used from one world
are a same-metacontext pair (§2), so `one_claim` is available for them.

### 1.3 Rejected, on measurement

- **Moving the similarity threshold.** Lowering it to anything down to 0.85
  admits exactly one new pair on that graph, the one that should merge, and
  it still fails. The ten pairs that must not merge score 0.959 to 0.994,
  above both pairs that should, so no single value separates them. And *one
  new pair on this graph* is a fact about 306 topics on one day, while the bar
  is a default in force on every graph, there to stop a false merge, which is
  the most damaging thing this system can do.
- **Comparing tags by the material they tag.** The cosine between the
  centroids of each tag's tagged nodes sorts worse: `claim-kind` ·
  `claim_kind` scores 0.4305, the lowest of the twelve pairs and below every
  date pair (0.70 to 0.77). Two tags marking three facts between them have
  centroids drawn from almost nothing, and a day's session tag averages a
  whole day of mixed work into something bland enough to resemble any other
  day's. The signal is weakest exactly where a tag is newest, which is where a
  duplicate is most likely to have just been coined.
- **Edit distance.** `claim-kind` differs from `claim_kind` by one character,
  and `dev-session-2026-09-05` differs from `dev-session-2026-09-06` by one
  character too.

The description field remains the fix for retrieval, which none of this
touches: a tag embedded on its name alone is invisible to a search on
meaning, and the material it marks is not a usable substitute.

### 1.4 Settled by review

1. **`[\s_-]+` is the right set.** It collapses `claim_kind` and `claim-kind`
   and leaves `reflect` and `reflection` alone. Unicode folding, punctuation
   beyond these three, and any form of stemming stay out.
2. **First written wins**, since resolution returns the existing node, and
   `tags_resolved_to` reports the difference.
3. **No stored key yet.** A `tag_key` field with its own index would make the
   miss path indexed rather than a scan, and a scan over 88 rows on a path
   that was about to write a node is not worth a field. Add it when a graph
   makes the cost measurable.
4. **Reflect keeps nominating tag pairs on name similarity.** With `tag_key`
   in force the equal-key class cannot recur, and skipping every tag pair
   would also hide real synonyms that share no key, `reflect` and
   `reflection` among them. Revisit if the noise returns.

---

## 2. Metacontexts: every one a tag is used from

**A topic node created from a tag stands in the union of the metacontexts of
the nodes tagged with it.** The set is derived from use, never from a
judgment, and it only grows: each `store_decomposition` writes a
`has_metacontext` edge from the tag's topic node to the call's metacontext,
unless the node already holds that edge.

For a fact the union would be the worst outcome available:
`shared_metacontext_set` says why a claim derived from a real source and a
fiction one must not assert both. A tag is different because it asserts
nothing. `dev-session-2026-09-08` standing in `the-real` and in a novel's
metacontext says exactly what is true: that name was used from both worlds.
The union is wrong for a claim and right for a name, and `created_from_tag`
draws that line.

The same answer holds wherever tags combine. An all-tag merge re-states the
union of the sources' metacontexts on the survivor, under the merging judge,
so the exact-equality metacontext gate stays exempt for tags, for that stated
reason. Parent synthesis over an all-tag child set takes the same union,
through `combined_metacontext_set`. A graph written before this rule is
stamped on open, adding edges and never removing them. That derives edges from
edges already in the graph, which anyone can re-derive, so it carries no judge
and no journal row.

### 2.1 Why not no metacontext at all

A name asserts nothing, so a tag could be written with no metacontext. The
rest of the system reads absence as *nobody spoke for this node*: nothing
compares it, nothing merges it, and no scoped read returns it. A scoped search
would then drop every tag and every `tagged_with_topic` edge reaching one,
`graph_stats.nodes_without_metacontext` could never stay at zero after a
declaration, and every reader would need an exemption. A rule that needs an
exemption at every site that reads it is the wrong rule.

The worry that rule answered is cross-bleed: a tag shared between a novel's
metacontext and `the-real` is a path from one world to the other. Scoping
handles it: `find_nodes`, `query_graph`, `search` and `topic_tree` take
`metacontexts` and drop what stands outside them, so bleed through a tag is
stopped at the nodes the tag reaches, not at the tag (`docs/RETRIEVAL.md` §8).

### 2.2 Rejected

- **Keep tags without a metacontext and special-case every reader.** Every
  future reader of `has_metacontext` would have to remember the same
  exemption, and a marker every site must subtract fails open at whichever
  site forgets.
- **One topic node per tag per metacontext.** Exact set equality then holds
  for tags too, but it doubles every tag that crosses a world, breaks one name
  being one node (§1), and makes `find_nodes(tagged_with_topic=...)`
  ambiguous. A name is one thing.
- **Derive at read time instead of writing the edge.** Correct by
  construction and never stale, but a second way of answering the question
  every reader already answers by scanning `has_metacontext`, with a join over
  every `tagged_with_topic` edge on the read path.

### 2.3 Accepted limit

A tag whose every tagged node is later reassigned out of a metacontext keeps
standing in it: nothing removes a derived edge. A stale extra metacontext on a
name changes what a scoped read returns by one topic node, and
`reassign_metacontext` withdraws it by hand. The withdrawal lasts until the tag
is next used from that world, which is right: the fact that caused the use is
the thing to reassign.
