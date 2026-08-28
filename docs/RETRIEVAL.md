# Retrieval — how a search is answered

`search` is one tool with several arms behind it. This page is the principle
behind each: what it contributes, when it fires, and what the result carries
because of it.

For the tool signature and parameter list, see
[INTEGRATION.md](../INTEGRATION.md#available-tools). For the design history and
the arguments that were rejected, see `dev-docs/LEXICAL_SEARCH.md` and
`dev-docs/RETRIEVAL_PROVENANCE.md`.

---

## 1. Two arms, because they fail in opposite directions

Embedding similarity has no notion of **term rarity**. The default model
(`all-MiniLM-L6-v2`, 384-dim) splits an identifier like `JIRA-4417` into word
pieces and mean-pools them with every other token in the sentence, so the query
embeds to approximately *"short alphanumeric string"* — which sits close to every
other ticket id in the graph. The failure is not that the right node ranks low.
It is that the wrong ticket ids rank about equally high.

BM25 supplies exactly the missing statistic. So `search` runs two independent
arms over the same query:

| Arm | Finds | Blind to |
|---|---|---|
| **Vector** | paraphrase, related phrasing, conceptual neighbours | rare exact tokens |
| **Lexical (BM25)** | identifiers, error codes, names, filenames | anything reworded |

Neither is a fallback for the other. They run concurrently as two transitions off
the same place in the query net, and neither can change what the other computes.

### Declaring terms

**Pass the exact strings you care about as `terms`.**

```python
search("why did the deploy fail", terms=["JIRA-4417", "certificate rotation"])
```

- A term is matched **whole**: each term matches only documents containing *all*
  of its words, which is what separates `JIRA-4417` from `JIRA-4418`.
- Terms are **ORed** with each other.
- A term absent from the corpus contributes nothing rather than excluding
  everything.

Omit `terms` and the lexical arm falls back to the query's own words. Rare ones
still fire; common ones contribute nothing. **Declaring is the reliable path**,
and it is the only one that carries the survival guarantee below.

---

## 2. Fusion is by rank, never by score

Cosine similarity and BM25 are on incomparable scales with no calibration between
them, so any weighted sum is a magic number that gets re-tuned forever.
**Reciprocal Rank Fusion** uses only ranks:

```
score(id) = Σ over lists  1 / (60 + rank)      # rank is 1-based
```

`k = 60` is the conventional value and nothing here departs from it. Ranks are
also the only thing that may legitimately cross a corpus boundary — BM25's IDF
term is computed per index, so scores from the node table and the segment table
are not comparable, while their ranks are. Fusion therefore consumes one vector
list, one list per node table, and one segment list.

### The one deliberate departure

Pure RRF does not guarantee that an exact match survives. A node present in
*both* lists scores at least `1/70 + 1/71 ≈ 0.0284`, beating a lexical-only
rank-1 at `1/61 ≈ 0.0164` — and list overlap peaks in precisely the scenario the
feature exists for, where every ticket id embeds alike. So the guarantee is made
explicit rather than assumed:

> **Each declared term's best hit survives to the final result**, even where rank
> fusion would have cut it.

Fallback terms — the ones derived from the query when `terms` is omitted — get no
such protection.

---

## 3. Segments are a second corpus, and answer a different question

`store_decomposition` is agent-driven: the calling agent writes the fact content.
If it paraphrases *"ticket JIRA-4417 was closed"* into *"the deployment ticket
was closed"*, the identifier never enters the graph and **no** search of any kind
recovers it from nodes.

`RawDocument` and `Segment` retain the raw text, so segments are indexed as their
own corpus:

| Corpus | Question it answers |
|---|---|
| Nodes | *What do I believe?* |
| Segments | *Where did I read that?* |

A rare identifier is almost always the second question. Segment hits surface
under their own `segments` key in the response — a segment is not a graph node
and must not be pretended into one — and they bridge back: a node's `source_id`
is its segment's id, so a segment hit also yields the nodes extracted from it.
*The id is in passage S; here is what we concluded from S.*

---

## 4. Every result says how it was reached

Flattening the routes into a boolean "retrieved" throws away the most useful
thing hybrid retrieval produces. *This matched at 0.82; that one was dragged in
by an edge from it; this third came back on an exact token match* is the question
you are actually asking when a search disappoints.

Each returned node carries `provenance`:

| Value | Meaning |
|---|---|
| `lexical` | a term matched the node's own content |
| `segment` | a term matched the passage the node was extracted from |
| `vector` | embedding similarity |
| `expanded` | reached by an edge from one of the above |
| `direct` | returned unranked — `find_nodes`, `graph_as_of`, `topic_tree`, … |

A node reached by more than one route gets the **most specific** label, in that
order: an exact token match is a rarer and more diagnostic fact about a result
than similarity, which is the default expectation. So a node both arms found is
reported as `lexical`. `direct` never appears on a `search` result — the query
pipeline always knows which arm reached a node — and it carries no score, because
showing a blank is honest where showing `1.0` would be a lie.

---

## 5. Graph expansion

Fused seeds are expanded outward along typed edges, `graph_hops` deep (default
1). Expansion is what makes the graph structure earn its keep: a fact that no
query phrasing would reach directly still arrives if it hangs off one that does.

Not every edge is knowledge worth following, so expansion skips two families by
default:

- **History and review edges** — `superseded_by`, `temporally_followed_by`,
  `merged_into`, and the review bookkeeping. These are graph plumbing, and
  fanning out from a version hub returns a claim's ancestry instead of its
  subject matter. (Lineage is still reported, by the fold in §6, which is a
  different mechanism answering a different question.)
- **Provenance and attribution** — `sourced_from`, and user relations whose
  `kind` is `attribution`. Expanding into these would return the publisher of
  everything that matched.

`tagged_with` and relationship-kind user edges *are* followed, alongside `about`
and `supports`: they say what a node is about, which is the thing expansion is
for.

---

## 6. History comes back by default, folded

**Knowledge that is not current is still knowledge.** A claim retired because the
world moved on (`historical`) is returned by default and says so in its `status`.
A claim retired for being *wrong* (`corrected`) is off by default — it is kept for
the audit trail rather than for reading — but reachable via
`include_corrected=True`, because *"what did we believe about X that turned out
wrong?"* is a fair question for an epistemic memory, and walling it off would make
it answerable only by someone who already knows the node id.

Default-on requires **lineage collapse**, or ranking fills with versions of one
claim: a historical node is near-identical text to the claim that replaced it, so
a claim with four predecessors would occupy half a top-10 on its own. When both
match, the replacement takes the slot and the earlier one attaches to it under
`earlier_versions`.

Three things about the fold are worth knowing, because each was a bug before it
was a rule:

- **It reads the node's status, not the edge.** Two `active` nodes joined by a
  lineage edge are two current claims — the shape `restore` leaves behind — and
  folding one would hide a live answer.
- **The walk is cycle-safe by requirement.** Recurrence makes cycles legal on
  `temporally_followed_by`, and a cycle has no last version, so its best-ranked
  member hosts the rest.
- **The top-k cut happens after the fold**, not before. Folding a result that has
  already been cut rearranges what the cut was supposed to save. Retrieval
  over-fetches to make room.

---

## 7. Valid time answers in groups, never as a filter

`valid_as_of` asks *what was true then*. It never excludes anything. Every result
carries `valid_at`, and the response carries the same information as buckets:

| Bucket | Meaning |
|---|---|
| `valid` | some source asserts the claim held at that moment |
| `unknown` | nobody says |

There is no third bucket. An interval asserts what a source says and **nothing
about the world outside itself**, so a moment nobody dated is *unknown*, not
false — which makes a valid-time filter unimplementable rather than merely
misleading. A filter would convert missing metadata into a silent false negative,
which is the one failure mode this system is built to refuse.

Nodes whose sources dated them also carry `validity`: one entry per source, with
the periods that source asserts, **uncollapsed**. Union takes one careful source
and one sloppy one and yields a period neither claims; intersection turns two
separate episodes into "never". See [VALIDITY.md](VALIDITY.md).

`valid_as_of` is named apart from `graph_as_of` on purpose. They are different
clocks — when a claim was true, versus what the graph held at an instant — and
the unmarked name inherits the default reading.

---

## 8. What else rides on a result

- **Metacontext labels**, always. Fiction and fact must never come back mixed
  without the distinction surfacing.
- **Computed review labels** — `superseded_candidate`, `evidence_stale`,
  `evidence_merged`, `contested` — derived at read time from edges, never
  stored. See
  [REFLECTION.md](REFLECTION.md#4-review-labels).
- **Hierarchy neighbours** on topics that sit in a split hierarchy (`parents` /
  `subtopics`, as id + preview), so a caller can drill via `topic_tree` rather
  than be handed the whole subtree.
- **`retrieved_at` is stamped** on every returned node. Being retrieved is what
  tells a used node from a merely old one, and it is what the `never_retrieved`
  archival class reads. Ranking is unaffected. `record_retrieval=False` disables
  it.
- **Corroboration**, but only if asked — see below.

### Corroboration: how many independent sources back this

`search(include_corroboration=True)` adds, per node, a count of the **distinct
publishers** behind it, with the sources and contributing nodes that produced it.

It answers a question `confidence` cannot. `confidence` is a prior supplied at
ingest, about the material; corroboration is a fact about the *graph*, so it
changes as the graph does — which is why it is derived at read time and never
stored. A stored count would be an answer frozen at the moment it was taken.

Five things to know before reading the number:

- **It counts independence, not strength.** Three hedged reports from three
  outlets score 3, exactly as three confident ones would. The two signals do not
  interact and neither substitutes for the other.
- **It counts publishers, not documents.** Two BBC articles are one source.
  Publisher identity is exact-name, so "BBC" and "BBC News" are two — the count
  inherits that over-split and shows its working so you can see it.
- **Documents naming no publisher stand as their own source.** Most do today, so
  a graph ingested without attribution scores lower for that reason alone.
  `unattributed_documents` says how many did.
- **It is computed over a similarity neighbourhood**, because facts are only
  deduplicated where an agent judged them the same claim *and* the merge cleared
  its gate — which is nothing written before 2026-08-21, since dedup reads a
  judgment recorded at ingest. That is deliberate: a wrong `similarity`
  edge overstates a number you can inspect, where a wrong merge would destroy a
  node. Merging moves a pair from the neighbourhood reading to the identity one;
  the `sources` list on every result is what makes a count taken before and
  after comparable.
- **A claim about another period is not a second witness.** "The city is called
  Leningrad" (BBC, 1924–1991) and "the city is called Saint Petersburg"
  (Reuters, 1991–) are near-identical sentences, so `reflect` pairs them — and
  Reuters is not backing the Leningrad claim. Where the periods two sources
  state provably fall clear of each other, the look-alike stops counting and
  comes back under **`adjacent_periods`** instead, with its publisher, its
  documents and its own periods.

  Nothing is removed from the graph and nothing is rejected: both claims are
  true of their own stretches of time, both stay, and the succession between
  them is what [VALIDITY.md](VALIDITY.md) records. Only the tally narrows.

  It fires **only where the dates provably clear each other** — an undated side,
  a half-dated pair, or intervals kept on different timelines all go on
  corroborating, per the open-world rule. Most nodes carry no intervals, so on
  most graphs this changes nothing; where it fires, the graph knew.

Retired neighbours are read the way [VALIDITY.md](VALIDITY.md) reads them
everywhere else: a `corrected` claim does not corroborate, because it was
concluded false; a `historical` one does, because it was right and is still
right of its period — it is the *dates*, not the status, that decide whether a
retired claim witnessed this one.

**Off by default on a measurement**, not on taste. It is several times the cost
of every other annotation here, and its price rises with the density of
`similarity` edges — so it would grow fastest on the graphs where it says most.
`dev-docs/BENCHMARKS.md` has the table, measured against edges assigned at a
fixed degree.

**One thing to know before reading a count on an older graph.** Until
2026-08-22 nothing in the system wrote a `similarity` edge: the neighbourhood
bullet above described a walk whose input no tool produced, and both real graphs
carried zero of them. Any count taken before then is
the identity reading — the distinct publishers behind *that node's own*
documents, with no restatement folded in. Honest, and cheaper than the table
suggests, but not the cross-restatement count this section describes.

`apply_reflection(similarities=[…])` is what writes them now, and only on an
agent's explicit `one_claim` verdict about a pair `reflect` nominated. The
companion `assessed` edge, written for **both** verdicts, is deliberately not
read here: it records that somebody judged a pair, which is not a claim that
they agreed — see [REFLECTION.md](REFLECTION.md) §6. So the number rises as
judgments accumulate, and only ever on pairs somebody said were one claim.

**And it can come back down.** An agent that withdraws a `one_claim` verdict
leaves the `similarity` edge in place — nothing here deletes — and a
`retracted_similarity` edge beside it stops the pair counting, exactly as a
`contradiction` or a `variant_of` between two facts already does. A withdrawal
is final: nothing re-asserts the verdict afterwards, because getting *that*
wrong manufactures support rather than merely withholding it.

### Frame scoping

`metacontexts` is a list of frame ids, and results are the nodes standing in
**Any** of them — a set union the caller states per query. **No frame inherits
another**, and there is no base-reality background a frame is read against: a
question about a novel's world read against real history names both
(`["world-of-anarres", "the-real"]`), and one about only what the novel says
names one. An inheritance rule fixed in the code could express exactly one of
those and hide that it was choosing.

Omitting the list searches every frame. **It is optional here and required on
ingest, and the asymmetry is deliberate**: an omitted filter on the read side is
a coherent question — *anything about this, wherever it was claimed* — while an
omitted frame on the write side said nothing about which world the claim was
in.

**Every id must resolve in the active graph**, not just the first — metacontext
ids are per graph, and one that names nothing here is refused rather than
silently narrowing the search to the frames that do exist and answering as
though that were the question. A node stating no frame at all matches nothing
scoped and appears only in an unscoped search; that state is reachable only on a
graph written before frames were required, and `epimemer frames declare` ends
it. Frame-scoped retrieval **over-fetches**, so an in-frame node ranked below
the raw vector top-k is still found rather than lost to a filter applied after
the cut.

---

## 9. The retrieval record

Every tool whose response carries node ids also writes a **retrieval record** — a
bounded ring in the session process — holding what was returned, with what
provenance and score, plus the exact response text. The dashboard reads these to
dim everything the last retrieval did *not* return.

The rule that keeps coverage from drifting is semantic rather than an enumeration
of tools: `retrieved` is the set of node ids **present in the response**. Not what
the tool looked at — `reflect` scans the whole active graph and the agent sees
only the nominees, so a reflect record correctly dims everything except them.

The panel is labelled **"Response"**, not "Context". What lands in the model's
context is the MCP client's rendering of what we returned, possibly truncated by
the client, inside a tool-result block we never see. A panel captioned *"what the
agent saw"* would be making a claim the system cannot verify.

---

## 10. Cost

`search` is the hottest path in the system and the furthest from its ceiling —
roughly 1.5M nodes in-memory and 2.9M on SurrealDB against the 30 s default tool
timeout. Two optimisations hold it there, and the naive form of each is what a
reader would otherwise expect:

- in-memory edge lookups go through endpoint indexes rather than scanning the
  edge set;
- SurrealDB's `vector_search` **ranks before filtering by status** rather than
  filtering inside the ranking query — SurrealDB re-runs such a subquery per row,
  which cost `search` two orders of magnitude.

Measurements: [dev-docs/BENCHMARKS.md](../dev-docs/BENCHMARKS.md).
