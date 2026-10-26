# Retrieval: how a search is answered

`search` is one tool with several arms behind it. This page explains each
arm: what it contributes, when it fires, and what the result carries because
of it.

For the tool signature and parameter list, see
[INTEGRATION.md](../INTEGRATION.md#available-tools). For the design notes,
see `dev-docs/LEXICAL_SEARCH.md` and `dev-docs/RETRIEVAL_PROVENANCE.md`.

---

## 1. Two arms, because they fail in opposite directions

Embedding similarity has no notion of **term rarity**: an identifier like
`JIRA-4417` embeds close to every other ticket id in the graph, so the wrong
ids rank about as high as the right one. BM25 keyword matching supplies that
statistic. So `search` runs two independent arms over the same query:

| Arm | Finds | Blind to |
|---|---|---|
| **Vector** | paraphrase, related phrasing, conceptual neighbours | rare exact tokens |
| **Lexical (BM25)** | identifiers, error codes, names, filenames | anything reworded |

Neither is a fallback for the other, and neither can change what the other
computes.

### Declaring terms

**Pass the exact strings you care about as `terms`.**

```python
search("why did the deploy fail", terms=["JIRA-4417", "certificate rotation"])
```

- A term is matched **whole**: each term matches only documents containing
  *all* of its words, which is what separates `JIRA-4417` from `JIRA-4418`.
- Terms are **ORed** with each other.
- A term absent from the corpus contributes nothing rather than excluding
  everything.

Omit `terms` and the lexical arm falls back to the query's own words. Rare
ones still fire; common ones contribute nothing. **Declaring is the reliable
path**, and it is the only one that carries the survival guarantee below.

---

## 2. Fusion is by rank, never by score

Cosine similarity and BM25 are on incomparable scales, so the arms are fused
by **Reciprocal Rank Fusion**, which uses only ranks. Topics, facts,
inferences and segments each contribute their own ranked list.
`dev-docs/LEXICAL_SEARCH.md` §3.1 has the formula.

> **Each declared term's best hit survives to the final result**, even where
> rank fusion would have cut it.

Fallback terms, the ones derived from the query when `terms` is omitted, get
no such protection.

---

## 3. Segments are a second corpus, and answer a different question

The calling agent writes the fact content at `store_decomposition`. If it
paraphrases *"ticket JIRA-4417 was closed"* into *"the deployment ticket was
closed"*, the identifier never enters the graph and **no** search of any kind
recovers it from nodes.

`RawDocument` and `Segment` retain the raw text, so segments are indexed as
their own corpus:

| Corpus | Question it answers |
|---|---|
| Nodes | *What do I believe?* |
| Segments | *Where did I read that?* |

Segment hits come back under their own `segments` key, since a segment is not
a graph node. They bridge back: a node's `source_id` is its segment's id, so a
segment hit also yields the nodes extracted from it. *The id is in passage S;
here is what we concluded from S.*

---

## 4. Every result says how it was reached

Each node `search` returns carries `provenance`:

| Value | Meaning |
|---|---|
| `lexical` | a term matched the node's own content |
| `segment` | a term matched the passage the node was extracted from |
| `vector` | embedding similarity |
| `expanded` | reached by an edge from one of the above |

A node reached by more than one route gets the **most specific** label, in
that order, so a node both arms found is reported as `lexical`.

The retrieval record (§9) uses the same labels for every tool, plus a fifth,
`direct`, for a node a tool returned without ranking it: what `find_nodes`,
`graph_as_of` and `topic_tree` return, the node a `query_graph` walk started
from, and so on. `direct` never appears on a `search` result, and it carries
no score.

---

## 5. Graph expansion

Fused seeds are expanded outward along typed edges, `graph_hops` deep
(default 1), so a fact no query phrasing reaches directly still arrives if it
hangs off one that does. Only active neighbours are added.

Expansion skips two families by default:

- **History and review edges**: `superseded_by`, `temporally_followed_by`,
  `merged_into`, and the review bookkeeping. Fanning out from a version node
  would return a claim's ancestry instead of its subject matter. (Lineage is
  reported by the fold in §6 instead.)
- **Provenance and attribution**: `sourced_from`, and user relations whose
  `kind` is `attribution`. Expanding into these would return the publisher of
  everything that matched.

`search` always expands with those defaults. `query_graph` walks the same
expansion and takes `edge_types`, the edge types to follow; a list given there
replaces both default families. A **retired edge** (one with `retired_at`
set) is never followed, whatever the list says: a `timelink` that a timepoint
split or merge moved, or an `assessed` edge that `reopen` withdrew. So a fact
whose date moved to a new point is reached through its new link only, while
the old one stays in the record.

`tagged_with_topic` and relationship-kind user edges *are* followed,
alongside `about` and `extracted_under_topic`: they say what a node is about.
`supports` is followed too, and says which inference a fact is evidence for.

---

## 6. History comes back by default, folded

A claim retired because the world moved on (`historical`) is returned by
default and says so in its `status`; `include_historical=False` leaves it out
when only what holds now is wanted. A claim retired for being *wrong*
(`corrected`) is off by default and reachable with `include_corrected=True`,
for *"what did we believe about X that turned out wrong?"*.

Results are folded by **lineage**, or ranking would fill with versions of one
claim. When a retired node and the claim that replaced it both match, the
replacement takes the slot and the earlier one attaches to it under
`earlier_versions`, with its id, a preview and its status.

Three rules of the fold:

- **It reads the node's status, not the edge.** Two `active` nodes joined by
  a lineage edge are two current claims, the shape `restore` leaves behind,
  and neither is folded.
- **The walk is cycle-safe.** Recurrence makes cycles legal on
  `temporally_followed_by`, and a cycle has no last version, so its
  best-ranked member hosts the rest.
- **The top-k cut happens after the fold.** Retrieval over-fetches to make
  room.

---

## 7. Valid time answers in groups, never as a filter

`valid_as_of` asks *what was true then*, read against the clock `timeline_id`
names (the wall clock when omitted). It never excludes anything. Every result
carries `valid_at`, and the response repeats the split as id lists under a
top-level `valid_at`:

| Bucket | Meaning |
|---|---|
| `valid` | some source asserts the claim held at that moment |
| `unknown` | nobody says |

There is no third bucket. An interval asserts what a source says and
**nothing about the world outside itself**, so a moment nobody dated is
*unknown*, not false, and periods recorded on another clock count as unknown.
A claim provably valid at that moment also keeps its own slot rather than
folding into a later version of itself (§6).

Nodes whose sources dated them also carry `validity`: one entry per source,
with the periods that source asserts, uncollapsed, for the reason
[VALIDITY.md](VALIDITY.md#2-validity-lives-on-the-provenance-edge-per-source)
§2 gives.

`valid_as_of` is when a claim was true; what the graph held at an instant is
`graph_as_of`, a different clock.

---

## 8. What else rides on a result

- **Metacontext labels** (`metacontexts`), always, so fiction and fact never
  come back mixed without the distinction showing.
- **Computed review labels** under `review`: `superseded_candidate`,
  `evidence_stale`, `evidence_merged`, `contested`, derived at read time from
  edges, never stored. See [REFLECTION.md](REFLECTION.md#4-review-labels).
- **`date_contested`**, on a node linked by `timelink` to a timepoint whose
  place in time is disputed, and present only when the list is non-empty. It
  is a list of `{timeline_id, timepoint_id, temporal_contradiction_id}`, one
  entry per live link to a contested point. It differs from `contested`:
  `contested` means the claim itself has a contradiction edge, while
  `date_contested` means the claim stands and only its position in time is in
  dispute.
- **Hierarchy neighbours** on topics that sit in a split hierarchy
  (`parents` and `subtopics`, as id plus preview), so a caller can drill via
  `topic_tree` rather than be handed the whole subtree.
- **`retrieved_at` is stamped** on every returned node. Being retrieved is
  what tells a used node from a merely old one, and it is what the
  `never_retrieved` archival class reads. Ranking is unaffected. The server
  setting `EPIMEMER_RECORD_RETRIEVAL=false` turns the stamp off, which leaves
  `never_retrieved` unable to tell a used node from a stale one.
- **Corroboration**, but only if asked. See below.

### Corroboration: how many independent sources back this

`search(include_corroboration=True)` adds, per node, a count of the
**distinct publishers** behind it, with the sources and contributing nodes
that produced it. It is a fact about the *graph*, derived at read time and
never stored, so it changes as the graph does; `confidence` is the prior
supplied at ingest about the material.

Before reading the number:

- **It counts independence, not strength.** Three hedged reports from three
  outlets score 3, exactly as three confident ones would. The two signals do
  not interact and neither substitutes for the other.
- **It counts publishers, not documents.** Two BBC articles are one source.
  Publisher identity is exact-name, so "BBC" and "BBC News" are two; the
  `sources` list shows the working so you can see it.
- **Documents naming no publisher stand as their own source.** A graph
  ingested without attribution scores lower for that reason alone.
  `unattributed_documents` says how many did.
- **It is computed over a similarity neighbourhood**: the node and the
  `similarity` edges `apply_reflection` writes on an explicit `one_claim`
  verdict. The number rises only as judgments accumulate; on a graph nobody
  has judged yet, it is the distinct publishers behind that node's own
  documents. A merge moves a pair from the neighbourhood into one node, and a
  withdrawn verdict stops the pair counting, for good.
  [REFLECTION.md](REFLECTION.md#6-what-apply_reflection-writes) §6 has both.
- **A claim about another period is not a second witness.** "The city is
  called Leningrad" (BBC, 1924–1991) and "the city is called Saint
  Petersburg" (Reuters, 1991–) are near-identical sentences, so `reflect`
  pairs them, and Reuters is not backing the Leningrad claim. Where the
  periods two sources state provably fall clear of each other, the look-alike
  stops counting and comes back under **`adjacent_periods`** instead, with
  its publisher, its documents and its own periods. Both claims stay in the
  graph; only the tally narrows. It fires **only where the dates provably
  clear each other**: an undated side, a half-dated pair, or intervals kept on
  different timelines all go on corroborating.
- **An agreeing inference is listed, not counted.** Two inferences judged
  `one_claim` draw the same conclusion from their own evidence, and agreement
  between two conclusions is not a second witness to either. The partner
  comes back under **`agreeing_inferences`**, with its id and the start of its
  wording, and the count is what it would be without it. A fact's
  restatements still count.

Retired neighbours are read the way [VALIDITY.md](VALIDITY.md) reads them
everywhere else: a `corrected` claim does not corroborate, because it was
concluded false; a `historical` one does, because it is still right of its
period. The *dates*, not the status, decide whether a retired claim witnessed
this one.

**Off by default on cost.** It is several times the cost of every other
annotation here and rises with the density of `similarity` edges
(`dev-docs/BENCHMARKS.md`).

### Metacontext scoping

`metacontexts` is a list of metacontext ids, and results are the nodes
standing in **any** of them: a set union the caller states per query. **No
metacontext inherits another.** A question about a novel's world read against
real history names both (`["world-of-anarres", "the-real"]`), and one about
only what the novel says names one.

Omitting the list searches every metacontext. It is optional here and
required on ingest.

**Every id must resolve in the active graph**, not just the first: one that
names nothing here is refused rather than silently narrowing the search. A
node stating no metacontext at all matches nothing scoped and appears only in
an unscoped search; `epimemer metacontexts declare` assigns such nodes a
metacontext. Scoped retrieval **over-fetches**, so an in-metacontext node
ranked below the raw vector top-k is still found.

A world that shares another's history up to a point records it as a
**base**, with `based_on` on `create_metacontext` or with
`set_metacontext_base`, and `diverges` saying where the two part, as a date or
a named moment. An alternate history whose lighthouse never fell is based on
`the-real` and diverges at
`{"instant_kind": "named", "label": "the 1962 storm"}`. A metacontext has one
base. A search or `find_nodes` scoped to it carries the base in
`metacontext_bases`, `get_metacontexts` shows it, and
`find_nodes(variants_in=...)` lists its facts with a `variant_of` edge to a
fact in the base. No claims flow along it: name both metacontexts to search
the shared history, and trust the base's claims only up to the divergence.

#### A scoped search scopes the whole response

A topic node created from a tag stands in every metacontext it is used from,
so it bridges worlds: a fact from a novel and a fact about real history sit
one hop apart through it. Filtering the node list alone would leave that
bridge in the response, so scoping covers every key:

| Key | Scoped | Unscoped |
|---|---|---|
| `nodes` | nodes standing in a named metacontext | every node retrieval reached |
| `edges` | edges whose **both** endpoints are returned nodes | every edge between returned nodes |
| `segments` | passages with something extracted from them standing in a named metacontext | every passage that matched |

A segment stands in no metacontext of its own and is judged by what was
concluded from it: a passage is in a metacontext exactly when a node
extracted from it is. A passage nothing was extracted from is therefore
dropped from a scoped search and kept in an unscoped one.

### Scoping the other read tools

`find_nodes`, `query_graph` and `topic_tree` take the same `metacontexts`
list, with the same meaning: a union the caller states, no metacontext
inheriting another, and every id refused unless it resolves in the active
graph. `query_graph` needs it most: a walk from a topic node created from a
tag reaches every world that tag was ever applied in.

- **`find_nodes`** returns the nodes standing in a named metacontext. The
  filter runs before the `limit` cut, so a page is a full page of what was
  asked for.
- **`query_graph`** returns the seed whether or not it stands in a named
  metacontext, because the caller named it by id: that is what makes a topic
  node created from a tag usable as a starting point. Its neighbours are
  filtered, and the edges returned are only those between nodes that survived.
- **`topic_tree`** returns the topic that was named the same way. An ancestor
  outside the scope is left out and the rest are still ancestors; a subtopic
  outside it takes its branch with it.

All three label every node they return with the `metacontexts` it stands in,
as `search` does.

---

## 9. The retrieval record

Every tool call that succeeds writes a **retrieval record** into a bounded
ring in the server process: the node ids the response carried, with their
provenance and score, and the response text. The id list and the text are
both capped, and the record says when a cap cut them. The dashboard reads
these: pick one and it dims everything that retrieval did *not* return. A
record from a tool that declared no ids is listed as *not declared*, and
picking one dims every node, the same as a retrieval that returned nothing.

`retrieved` is the set of node ids **present in the response**, for every
tool, rather than what the tool looked at: `reflect` scans the whole active
graph and the agent sees only the nominees, so a reflect record dims
everything except them. `dev-docs/RETRIEVAL_PROVENANCE.md` has the design.
