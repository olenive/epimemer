# Topic nodes created from tags: stand in every metacontext they are used from

**Status: built.** All three stages in §4 are in the code.

A topic node created from a tag is the one kind of node `store_decomposition`
writes with no `has_metacontext` edge. That was a deliberate answer to a real
question, and §1 says what the answer costs. This proposes a different answer:
a tag stands in every metacontext of the nodes that carry it, and the set grows
as the tag is used.

---

## 1. Where the current rule leaves a graph

The rule today, from `created_from_tag` in `pipelines/metacontexts.py`: a tag
is a name, a name asserts nothing, so there is no world for it to be about, and
the topic node is written with no metacontext. One tag serves every
metacontext at once.

The first half is right and the conclusion does not follow from it. The rest of
the system reads absence as *nobody spoke for this node*, and it treats such a
node accordingly: nothing compares it, nothing merges it, and no scoped read
returns it. A topic node created from a tag is then a node that everybody uses
and nobody can find.

### 1.1 Scoped reads drop the tag and everything that touches it

`_in_metacontext_nodes` keeps a node only where its metacontext set overlaps
the caller's list, and a tag's set is empty. So `search(metacontexts=["the-real"])`
never returns the topic node `design-decisions`, and `_edges_among` then drops
every `tagged_with_topic` edge that reached it, since one endpoint is gone.
`topic_tree` documents the consequence rather than fixing it: a topic node
created from a tag "carries no such key". The tags are the most-used topics in
any real graph, 4,662 `tagged_with_topic` edges among 11,100 in one, and the
scoped read hides them.

### 1.2 `nodes_without_metacontext` can never stay at zero

`epimemer metacontexts declare` exists to end the state where a graph holds
nodes nobody spoke for, and `graph_stats.nodes_without_metacontext` is its
completeness check. Both count tags. The sweep stamps every tag it finds, so the
count does reach zero; the next ingest then mints a tag with no metacontext, and
it is nonzero again. Measured on 2026-09-10 on one real graph: 27 nodes without
a metacontext, all 27 of them topic nodes created from tags, and every one of
them written after the last declaration.

The readout therefore has no steady state that means *done*, and the graph is
in a third state nobody designed: older tags stamped `the-real` by the sweep,
newer tags in nothing, and the same name doing both jobs depending on when it
was first typed.

### 1.3 The exemptions exist only to work around the rule

Three places carry a special case for `created_from_tag`, and each one is there
because the tag's empty metacontext set would otherwise refuse something
correct:

- The topic-merge gate in `apply_reflection` demands exact set equality of
  metacontexts and exempts an all-tag merge, because two tags would otherwise
  compare `{}` against `{"the-real"}` after a declaration and be refused.
- `reflect` re-states a survivor's metacontext under the merging agent's judge
  and re-states nothing where the sources hold none, which for tags is always.
- `declare_metacontext` stamps tags along with everything else, which is the
  one place the exemption is missing, and §1.2 is the result.

A rule that needs an exemption at every site that reads it is the wrong rule.

### 1.4 What the rule was protecting against

The worry was cross-bleed: a tag shared between a novel's metacontext and
`the-real` is a path from one world to the other. That worry is real and it is
already handled, by the scoping work in 0.2.0: `find_nodes`, `query_graph`,
`search` and `topic_tree` all take `metacontexts` and drop what stands outside
them. Bleed through a tag is stopped at the nodes the tag reaches, not at the
tag. Leaving the tag without a metacontext protects nothing the scope does not
already protect, and costs §1.1.

---

## 2. The change

**A topic node created from a tag stands in the union of the metacontexts of
the nodes tagged with it.** The set is derived from use, never from a
judgment, and it only grows.

For a fact this would be the worst outcome available: `shared_metacontext_set`
says why a claim derived from a real source and a fiction one must not assert
both. A tag is different because it asserts nothing. `dev-session-2026-09-08`
standing in `the-real` and in a novel's metacontext says exactly what is true:
that name was used from both worlds. The union is wrong for a claim and right
for a name, and `created_from_tag` already draws that line.

### 2.1 At ingest

`store_decomposition` resolves each tag through `_tag_topic` and then writes a
`tagged_with_topic` edge per node. It also writes, once per tag per call, a
`has_metacontext` edge from the topic node to the call's `metacontext_id`,
unless the topic node already holds one. A new topic node gets the edge in the
same atomic batch; an existing one is checked in one `get_edges_for` read over
the resolved tags, so a repeated tag costs one query per call rather than one
per node.

### 2.2 At merge

An all-tag merge in `apply_reflection` re-states the union of the sources'
metacontexts on the survivor, under the merging judge as today. Names take the
union, so the exact-equality gate stays exempt for tags, and the exemption now
has a stated reason instead of a workaround. Parent synthesis over an all-tag
child set takes the same union, through `combined_metacontext_set` in
`pipelines/metacontexts.py`, which states the two answers once: union for
names, the one shared set for anything else.

### 2.3 Migration

Schema v5 in `_migrate_schema` stamps every active topic node created from a
tag with each metacontext in the union of its tagged nodes' metacontexts that
it does not already hold. It adds and never removes, so a tag already declared
`the-real` by a sweep keeps that and gains whatever else its uses say, and a
tag that tags nothing stays as it is until it is next used. The stamping is
one pure function over `(tag, tagged nodes' metacontexts)` in
`pipelines/metacontexts.py`, so the in-memory backend runs the same code in
tests and the SurrealDB step only supplies the rows.

No judge and no journal row: this derives an edge from edges already in the
graph, which anyone can re-derive, and a declaration is a person's act that
this is not.

### 2.4 What changes for the reader

- Scoped `search`, `find_nodes`, `query_graph` and `topic_tree` return the
  tags used from the scope, with their `tagged_with_topic` edges.
- `topic_tree` entries for tags carry `metacontexts` like every other entry.
- `graph_stats.nodes_without_metacontext` reaches zero after a declaration and
  stays there, which makes it the completeness check it was meant to be.
- `epimemer metacontexts declare` needs no special case, and after this change
  it will normally find no tags to stamp at all.
- Reflect's topic similarity nominations can now put a tag and a statement
  topic in a same-metacontext pair, where before every such pair was
  cross-metacontext and `one_claim` was refused. That is a gain: a tag and its
  spelt-out topic are the pair worth judging.

### 2.5 What does not change

- `_tag_topic` and `_resolve_node_reference` still resolve a tag by name across
  the whole graph. One name is one topic node, whatever it stands in.
- `find_nodes(tagged_with_topic=...)` unscoped still returns everything tagged
  with it, and the tool text saying to scope such a listing stays true.
- `reassign_metacontext` works on a tag as on any node. Withdrawing a
  metacontext from a tag lasts until the tag is next used from that world,
  which is right: the fact that caused the use is the thing to reassign.

---

## 3. Rejected

### 3.1 Keep tags without a metacontext and special-case every reader

Teach `_in_metacontext_nodes`, `_edges_among`, `topic_tree`, the declaration
sweep and the count to recognise `created_from_tag` and let it through. This
fixes §1.1 and §1.2 by adding two more exemptions to §1.3, and every future
reader of `has_metacontext` has to remember the same one. `pipelines/metacontexts.py`
already records the failure shape: a marker every site must subtract fails
open at whichever site forgets.

### 3.2 One topic node per tag per metacontext

`design-decisions` in `the-real` and a separate `design-decisions` in the
novel's metacontext. Exact set equality then holds for tags too and no
exemption is needed anywhere. It doubles every tag that crosses a world,
breaks the promise `TAG_IDENTITY.md` makes that one name is one node, and
makes `find_nodes(tagged_with_topic="design-decisions")` ambiguous. A name is
one thing.

### 3.3 Derive at read time instead of writing the edge

Compute a tag's metacontexts from its tagged nodes on every scoped read.
Correct by construction and never stale, but a second way of answering the
question every reader already answers by scanning `has_metacontext`, and it
puts a join over 4,662 edges on the read path. Writing the edge keeps one
representation, and §2.3 keeps it consistent.

---

## 4. Stages

**Stage 1, ingest and merge.** `store_decomposition` writes the edge (§2.1);
the all-tag merge re-states the union (§2.2). Tests: a new tag stands in the
ingest's metacontext; a tag reused from a second metacontext stands in both,
with one edge each; a repeated tag in one call gets one edge; a scoped `search`
returns a tag used from that scope and its `tagged_with_topic` edges, and not a
tag used only from another; `topic_tree` labels a tag; an all-tag merge across
`{a}` and `{a, b}` survives in `{a, b}`; `nodes_without_metacontext` is zero
after an ingest into a declared graph.

**Stage 2, migration.** Schema v5 (§2.3) and the pure stamping function.
Tests on the in-memory backend: a tag with no metacontext gains the union; a
tag declared `the-real` whose uses include a novel gains the novel and keeps
`the-real`; a tag tagging nothing is untouched; a rerun changes nothing.
Checked on the real graph the way v4 was: `nodes_without_metacontext` goes
from 27 to 0 on open.

**Stage 3, prose.** The sentence "stands in no metacontext" is stated for tags
in `docs/README.md`, `epimemer_prompts/DEFAULT.md`, the `find_nodes`,
`query_graph` and `topic_tree` descriptions in `mcp/server.py` and the matching
docstrings in `mcp/tools.py`, `docs/REFLECTION.md`, `created_from_tag` itself,
and the merge-gate comment in `apply_reflection`. Each becomes "stands in every
metacontext it is used from". The terminology guard in `tests/test_docs.py`
does not cover this and should not; the `find_nodes` test for the label is the
check.

One release, since Stage 1 without Stage 2 leaves §1.2's third state in place
and Stage 2 without Stage 1 is undone by the next ingest.

---

## 5. Open questions

1. **Should the migration journal?** §2.3 says no, on the grounds that it is
   derived. The declaration sweep journals because a person is asserting
   something. If a reviewer wants the migration visible in `query_changes`, the
   cost is one row per graph.
2. **A tag whose every tagged node is later reassigned out of a metacontext**
   keeps standing in it. Nothing removes a derived edge, and this proposal
   does not add a sweep that would. Acceptable for now: a stale extra
   metacontext on a name changes what a scoped read returns by one topic node,
   and `reassign_metacontext` withdraws it by hand.
