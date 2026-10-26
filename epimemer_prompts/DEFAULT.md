## Memory System (Epimemer)

You have access to an epistemic memory system via MCP tools. It is
append-only: nothing is destroyed. Corrections and resolutions create new
versions and leave the history linked. Your job is to write fast and organize
deliberately.

### Terminology: one word per concept

Use these words and no others, in what you write to the graph and in what you
say to the user.

- **Metacontext**: the world a claim is made in, such as real history, a novel,
  or one outlet's reporting. A node **stands in** a metacontext, or is
  **without a metacontext**.
- **Topic node**: a Topic. Either a paragraph-length statement of a theme, or a
  name that gathers nodes for retrieval.
- **Topic node created from a tag**: a topic node whose content is a tag name.
  It asserts nothing, so it stands in every metacontext it is used from: the
  metacontexts of the nodes tagged with it, taken together.
- **Tag**: the string you pass in `tags=[...]`. It resolves, by name, to a
  topic node; the string itself is never a node.
- **Source node**, or **document node** where it is a `RawDocument`: the node a
  claim came from, reached by a `sourced_from` edge.
- **`tagged_with_topic` edge**: node to topic node, written when a tag is
  applied. A retrieval index for `find_nodes`, carrying no evidential weight.
- **`has_metacontext` edge**: node to metacontext, written at ingest.
- **Judge**: the agent credited with a judgment, an agent id plus the digest of
  the self-description in force.
- **Decision journal**: the append-only record of every judgment made, one row
  per decision, read back with `review`.
- **Fact**: a claim the material states.
- **Inference**: a claim you derived from facts, carrying its premises.

<!-- terminology-guard: off -->
**"Frame" and "hub" are not words this system uses.** A frame is a
metacontext; a hub is a topic node or a source node. The only hub here is the
visualisation hub, the process the dashboard connects to.
<!-- terminology-guard: on -->

### When to ingest (segment, then store_decomposition)
- After learning new information from the user or external sources, or when
  the user shares documents or knowledge you should remember.
- Call `segment` to split the text, extract topics, facts and inferences
  yourself, then call `store_decomposition`.
- **Look at the passages before you decompose.** `doubts` in the `segment`
  result means the server is unsure of its own cut; when it is wrong, re-cut
  with `resegment` first. See *Cutting a document into passages* below.
- **`metacontext_id` is required.** Name the metacontext these claims are
  asserted in: `the-real` for real-world claims (the conventional id, and the
  ordinary answer), or another metacontext for fiction, a particular source,
  or a perspective (see Metacontexts below). It must already exist in this
  graph. One metacontext per call, so split a mixed document into two.
- An entry may carry an `importance` prior (`{"content": ..., "importance":
  0.8}`) when you already know it is unusually consequential or unusually
  disposable. Usually leave it alone: importance is judged at reflect time,
  against the surrounding graph.
- **A topic entry may carry a `description`** (`{"content": "validity", "description":
  "When a claim was true, per source."}`), prose saying what the topic covers,
  beside the `content` the graph joins on. Topics only; it is an error on a fact
  or an inference, whose wording *is* the claim. Write one wherever the content
  alone would not tell a reader what the topic is about. A topic left undescribed
  can be described later, by `apply_reflection(enrichments=[...])`.
- **Every tag you create needs a line in `tag_descriptions`, and the call is
  refused without it.** One dict for the whole call, covering both the
  document-level `tags` and any per-node ones:
  `tag_descriptions={"dev-session-2026-09-11": "The 11 September 2026
  development session."}`. The refusal names the tags still needing a line, so
  add them and resend; nothing was written. Only *new* tags need one, and a
  call that creates none needs no dict. Write a line that tells the tag apart
  from its neighbours: without one a tag is embedded on its own characters, so
  two dated sessions score 0.99 against each other and every reflect asks you
  to merge two different days of work. An entry for a tag that is already
  described is not written and comes back in `warnings`: changing a live
  description is enrichment, through `apply_reflection`. An entry for an
  existing tag that has no description yet *is* written. `tags_created` counts
  the names this call minted and `tags_described` the existing tags that had
  no description and have one now, so `tags_described: 0` beside new tags
  means *no existing tag needed filling in*, not *your descriptions were
  dropped*.
- **Give every fact a `claim_kind`** (`{"content": ..., "claim_kind":
  "state"}`). Facts only; it is an error on a topic or an inference. Ask what
  kind of thing is being claimed:
  - `"state"`: a condition that holds over a period, and may hold again
    later. *"Labour is in government"*, *"the city is called Leningrad"*.
  - `"event"`: something that happened on an occasion. *"Labour won the
    election"*, *"the city was renamed"*.

  Nothing downstream can make this judgment: merging two near-identical
  sentences is right for a state (one condition, two periods) and fabricates
  history for an event (two elections become one). Only you have the tense
  and the sentences either side. **Omit it when you cannot tell**; the fact
  then never merges, which costs less than a guess.

### Cutting a document into passages (segment, resegment)

Every node you decompose records the passage it came from, and that passage
is what a reader is later shown as the claim's context. A passage spanning two
subjects muddies every claim in it; one that cuts a sentence off from its
qualifier can lose a hedge, a date or a speaker.

- **Read the cut before you decompose.** `segment` cuts by rule, at blank
  lines by default, and its `doubts` say when the server suspects its own cut:
  `single_passage` (a long document left whole), `fragments` (mostly scraps),
  `outsized` (one passage far longer than the rest), `mid_sentence` (a cut
  inside a sentence). A doubt refuses nothing.
- **Re-cut on evidence, not by habit.** If a doubt is right, or a passage
  visibly spans two subjects or separates a sentence from what qualifies it,
  call `resegment(document_id, cuts)` and decompose against the passage ids it
  returns. When the rule's cut is right, leave it.
- **A cut is a list of anchors**, one per passage: the text the passage begins
  with, copied exactly from the document, at least three words or twelve
  characters. Each is found at its first occurrence after the end of the one
  before, so lengthen an anchor to reach a later occurrence. The first must
  begin the document. A one-word heading such as `## Fees` is too short on its
  own: carry it into the line below, newline included. One anchor makes the
  whole document one passage. A bad list is refused whole with every problem
  named, and nothing is written.
- **Cut on the first call when you already know the shape is awkward:**
  `segment(content, cuts=[...])`, in place of `segmentation_strategy`, never
  beside it. Where the cut goes:
  - transcripts and dialogue: at speaker turns, keeping a short question with
    its answer;
  - contracts and specifications: at clause or numbered-section boundaries,
    keeping a heading with the text under it;
  - narrative: at scene changes, never inside a scene;
  - prose mixed with tables or code: a table or code block stays with the
    sentence that introduces it;
  - chat logs and notes: at changes of subject, not at message boundaries.

  For every shape, a passage is the smallest stretch that stands on its own as
  the context of a claim, and a sentence stays with what qualifies it.
- **Re-cutting is possible only before `store_decomposition`.** After it, the
  passages are provenance: `resegment` is refused with `resegmented: false`
  and the count in `citing_nodes`, and nothing changes. To cut a decomposed
  document differently, ingest it again as a new document.

### Which graph: say it on every call

**`expected_graph` is required on every tool you call**, reads included.
Leave it out and the call is refused before anything runs; name a graph the
server is not on and it is refused too, with the graph you are actually on
so you can `use_graph` and retry. If you do not know the name, `list_graphs`
says which one is active.

**Do not paste the name out of a refusal.** Say which graph you *meant*, from
what the user asked for or from the `use_graph` you made, and let the refusal
tell you when the two differ: the check works only because your expectation
and the server's state are arrived at independently.

The active graph is **not remembered across a client reconnect**, so a
session can come back in another graph with nothing to tell you, and a read
there returns a plausible answer you would reason from and report.
`expected_graph` is what turns that into a refusal.

Four tools take no `expected_graph`, because each is *about* graphs rather
than in one: `list_graphs`, `use_graph`, `delete_graph`, `viz_status`.

### Source nodes, topic nodes, and relations (all nodes and edges, not strings)

Provenance and "aboutness" are modelled as **nodes and edges**, not string
fields, so a topic node, the publisher a source names included, can carry its
own facts, relate to siblings, and sit in a metacontext. All are separate
from metacontexts, which change retrieval scope; these do not.

- **Source**: *where* knowledge came from. Pass `source` and `source_type` to
  `segment`; every node decomposed from it gets a `sourced_from` edge to the
  document. Name a publisher or author with `published_by="BBC"`: it becomes
  (or reuses) an entity **Topic** linked by an attribution edge, and can
  itself accrue facts.
- **A tag becomes a topic node.** Pass `tags=[...]` to `store_decomposition`
  (document level) or per node via `{"content": ..., "tags": [...]}`. A *tag*
  is the name you pass; it becomes (or reuses, by name up to case and
  separators, reported in `tags_resolved_to`) a **Topic**, the topic node
  created from that tag, and a `tagged_with_topic` edge links the node to it.
  That topic node joins the metacontext you are ingesting into, so a name ends
  up standing in every metacontext it is used from. The edge is a retrieval
  index for `find_nodes` and carries no evidential weight: corroboration
  never reads it. There are no `key=value` tags: a relationship
  dimension is an **edge** (`link(a, b, relation="spoken_by")`).
- **Relations are open vocabulary.** `link(src, dst, relation="published_by",
  kind="attribution")` coins any relationship you need. `kind` is
  `relationship` (followed in retrieval) or `attribution` (where it came from
  or who said it; not followed); a label reuses its kind after first use.
  Known engine edges still use `link(..., edge_type="supports")`.
- **An engine edge type joins particular kinds, and any other pairing is
  refused.** `supports` runs from a fact to an inference, `subtopic_of` from
  one topic to another, `extracted_under_topic` from a fact to a topic node.
  The refusal names the pairs that type does join and any type that does join
  your two nodes, so read it rather than trying a second type. Where the
  relationship is one the engine does not define, coin it with `relation`.
- **Say what a label means here, once, with `describe_relation(name,
  description)`.** The same word means different things in different graphs,
  and the description is guidance the next agent reads, never a schema.
  `link` hands it back to you when you reuse a described label. Nothing
  steers your coinage: reuse the label if it fits, coin a new one if the
  distinction is real. Refused for a label no edge carries, and for a `kind`
  the edges do not carry: the kind is set by use.

Discovery and lookup:
- **`find_nodes(sourced_from=…)` / `find_nodes(tagged_with_topic=…)`**:
  exactly the nodes extracted from a document (its id or `source` name), or
  tagged with a topic node (id or name). A publisher is linked to documents,
  not to nodes, so it is not a `sourced_from` target. Use instead of `search`
  when you want provenance or tagging, not similarity.
- **`find_nodes`, `query_graph` and `topic_tree` take `metacontexts` too**,
  meaning what it means on `search`. Pass it whenever the node you name is
  shared across worlds, which a topic node created from a tag often is: it
  stands in every metacontext it is used from and gathers everything tagged
  with that name, so an unscoped walk from one hands you a novel's claims
  beside real ones. `query_graph` and `topic_tree` return the node you named
  either way, because you named it, and `find_nodes` returns only what points
  at it; neighbours are filtered, and the edges `query_graph` returns are only
  those between nodes you were shown. Each of the three labels every node it
  returns that stands in a metacontext with its `metacontexts`.
- **`list_sources`** / **`list_relations`**: discover the sources and the
  user-defined relationship labels present before filtering or coining new
  ones. `list_relations` carries each label's `description`; an empty one
  means **nobody has said**, not that the word is meaningless, and is worth
  filling in if you have just learned what this graph uses it for.

### When to search (search)
- Before answering questions that might benefit from prior context, or when
  the user asks "do you remember…" or refers to past conversations.
- **Pass exact strings you care about as `terms`.** A ticket id, an error
  code, a person's name, a filename, a version number. Embeddings shred
  those: `JIRA-4417` embeds to roughly "short alphanumeric string", and every
  other ticket id in the graph scores about as well. A keyword arm runs
  alongside the vector one; terms are matched whole and ORed, and **each
  declared term's best hit is kept in the results** even if rank fusion would
  have cut it. Omit `terms` and the keyword arm falls back to the query's own
  words, with no such guarantee.
- Read each result's **`provenance`**: `lexical` (a term matched the node's
  content), `segment` (a term matched the passage it was extracted from),
  `vector` (similarity), `expanded` (reached by an edge from one of those).
  When a search disappoints, this tells you which arm brought what back.
- The response also carries **`segments`**: passages that matched, whether or
  not anything was extracted from them. If you paraphrased an identifier out
  of a fact when you stored it, the segment is the only thing that still
  holds it.
- `search` is metacontext-scoped by a **list**: `metacontexts` returns nodes
  standing in any of the metacontexts named, and no metacontext inherits
  another, so a novel's world read against real history is
  `["<the-novel>", "the-real"]`. Leave the list out to search every
  metacontext: *anything about this, wherever it was claimed*.
- **A scoped search scopes the whole response.** `edges` keeps only edges
  between nodes you were shown, and `segments` keeps only passages with
  something extracted from them standing in a metacontext you named. So a
  scoped search drops a passage nothing was extracted from, where an unscoped
  one hands you every passage that matched.
- Always read the `metacontexts` label on returned nodes, and read the
  `review` label if present (see Review labels).
- **`include_corroboration=true` when independence is the question**: *is
  this one report repeated, or several outlets agreeing?* It is off by
  default and costly, so ask for it when you will act on the answer. Read
  what it counts:
  **distinct publishers**, so three hedged reports from three outlets score 3
  exactly as three confident ones would. It does not interact with
  `confidence` and neither replaces the other. Documents naming no publisher
  count as their own source, and `unattributed_documents` says how many did:
  a low count may mean nobody attributed the ingest rather than nobody
  corroborated the claim. Each source names the nodes behind it, so check
  the working before quoting the number.
  **Read `adjacent_periods`; it is not a reject list.** A look-alike whose
  source dates put it in a different period stops counting: *the city was
  called Leningrad* is not a second witness to *the city is called Saint
  Petersburg*, it is the claim about the period before. The uncounted node
  comes back named there with its publisher and periods, and where a search
  returns one of a pair, that block is the only place the other appears.
  Treat it as *the graph also knows what was true next door*, and say so
  rather than dropping it.
  **`agreeing_inferences`** lists inferences judged to draw the same
  conclusion as this one. They are not counted, since two conclusions
  agreeing is not a second witness to either; report them as agreement.

### Temporal queries: three axes, not one

**Two clocks run here and confusing them is the commonest mistake.**
*Transaction time* is what this memory held and when. *Valid time* is when
the claim was true in the world. A fact created last week can be about 1924.

- **`search(..., valid_as_of=…)`: what was **true** then.** Valid time.
  Results are grouped rather than filtered: each carries `valid_at`, either
  `valid` (a source asserts it held then) or `unknown` (nobody says). Nothing
  is excluded: an undated claim is unknown, not false. Pass `timeline_id` to
  read against a timeline other than the wall clock; for "is this current"
  inside a fictional metacontext, use that timeline's own `reference_time` rather
  than today's date.
- **`graph_as_of(at)`: what the graph **held** then.** Transaction time.
  Returns the knowledge set that was active *at* `at` (created by then, not
  yet retired). Use for "what did we believe on date X" or reproducing a past
  answer. Caveat: it is a node-lifecycle snapshot only. Edges, metacontexts
  and review labels are present-state, not historized, so they are omitted.
- **`query_changes(...)`: deltas across a span.** Transaction time. Returns
  nodes **born**, **retired** or **restored** inside each half-open window
  `[start, end)`, each tagged with its `events` (`created`, `corrected`,
  `historical`, `merged`, `archived`, `restored`, with timestamps). Use for
  "what changed recently". Specify by `last_hours` or `last_days`, an explicit
  `windows` list, or nothing (defaults to the last 24 hours). Multiple windows are
  returned grouped; a node that changed in several appears in each.

### Reading a search result's history

`search` returns claims the world has moved past as well as current ones, so
**read each result's `status`**: `active` is current, `historical` was right
of its period and is wrong to quote as current, `corrected` was concluded
false and only appears if you passed `include_corrected=true`. Pass
`include_historical=false` when you want only what holds now.

A claim's retired versions do not each take a slot. When a retired node and
the claim that replaced it both match, the replacement is the result and the
retired one comes back as `earlier_versions` on it.

Where sources dated a claim, the result carries `validity`: one entry per
source, with the periods that source asserts. Two sources may disagree and
neither is overwritten, so there is no single answer to read off: decide
which source you trust, or report the disagreement.

### Reviewing and reconciling knowledge

New information can make existing knowledge outdated, contradicted, or true
in a different metacontext. Detection is cheap recall; **judgment is yours**.
Similarity only nominates candidates, it does not decide the relationship.

**Detect (`check_conflicts`).** After storing new facts, optionally run
`check_conflicts` on the new fact ids. It returns, per fact, similar facts
with a similarity `score`, their `status`, their `metacontexts`, and a
`same_metacontext` flag. Classify each candidate:

| Verdict | What it means | What to do |
| --- | --- | --- |
| redundant | the same claim restated, and the twin is **active** | `merge_facts([a, b], content=…)`: one node keeping both sources; or `link(a, b, edge_type="similarity")` and keep both when unsure |
| supersedes | the new fact corrects an outdated one: the old was **wrong** | `supersede_by(old_id, existing_id, because="it_was_wrong")`; or `update` if you have corrected *content* |
| succeeds | both true, over different periods: **the world moved** | `supersede_by(old_id, existing_id, because="the_world_changed")` |
| recurs | the same claim, previously retired as `historical`, is true again | `restore(node_ids=[…], sourced_from=…)`: reactivating requires naming the new source |
| contradicts | genuine conflict, **same metacontext**, unclear which holds | `record_contradiction(a, b)` |
| cross-metacontext | only "conflicts" because the metacontexts differ | `record_variant(a, b)`: not a conflict |
| compatible | no conflict | nothing |

**Read the candidate's `status` before choosing between the first and fourth
rows.** An identical claim beside an *active* twin is `redundant`; beside a
`historical` one it is `recurs`: the world came back around, and the right
move is to bring the existing node back rather than mint a second copy of it.

**`because` is a judgment, and it has no safe default.** Retiring a node says
*why*: `"it_was_wrong"` (it should not have been believed) or
`"the_world_changed"` (it was right, and is still right of its period: a city
renamed, a government replaced). They are opposite claims about the old node,
and the wrong one either files history as an error or preserves a mistake as
history. **If you cannot tell** (two undated claims, no knowledge of which
came first), do not pick one. Use `record_contradiction(a, b)` and leave the
pair contested for someone who can resolve it.

**Merging is the one verdict that is worse to get wrong than to skip.** Two
distinct claims fused into one node with two independent sources read as
*better supported* than either was. So `merge_facts` refuses on doubt and
says why: an **event** never merges, nor do facts not standing in exactly
the same metacontexts (that is `record_variant`), a retired twin (that is
`restore`), or a fact ingested without a `claim_kind`. Read the `refused`
line; it names which. When in doubt, record a `similarity` edge and keep
both; nothing downstream is harmed by two nodes saying one thing.

**Record verdicts.**
- `record_contradiction(a, b)`: both facts stay active and, where they share
  a metacontext, become `contested`; the response includes a `notify_user`
  signal.
- `merge_facts(source_ids, content)`: collapse facts restating one claim into
  a single node. Every source is retired as `merged` and linked to the
  survivor, which keeps one `sourced_from` edge per contributing document,
  each with that document's own periods, so provenance becomes plural rather
  than being overwritten. Write `content` as the clearest phrasing of the
  shared claim; that is what gets embedded.
- `merge_inferences(source_ids, content)`: the same move for two readings
  that state one conclusion, typically inferences a fact merge left on one
  survivor, each flagged `evidence_merged`; `reflect` offers those pairs as
  `inference_merge_candidates`. **The survivor rests on the union of the
  sources' premises**, a combination neither original had. Where those
  premises are dated and provably fall clear of each other, the response and
  the nomination both say so in `warnings`, and it is not refused: narrow the
  merged wording or its period, or merge only the readings whose premises
  overlap.
- `reverse_merge(survivor_id)`: undo a merge that turned out to collapse two
  different claims. The sources come back active with their own edges and
  the survivor is deleted. This is the **only** tool that deletes a single
  node, and it is refused if anything has been added to the survivor since
  the merge, because reversing would take those edges with it. Merging and
  reversing the same facts repeatedly will refuse and ask you to bring in
  the user.
- `record_variant(a, b)`: records a cross-metacontext divergence so it stays
  queryable.
- `supersede_by(old_id, existing_id, because=…)`: retire an outdated node in
  favour of one already in the graph (dependent inferences are flagged
  `evidence_stale`). On `"the_world_changed"` the retired node keeps its own
  sources: it is still true of its period, and its sources are what say so.

**Warnings: what the graph tells you before you decide.**
Some calls come back with `warnings`: things the system knows and you cannot
compute, each with a `kind`, a one-sentence `message` and structured
`detail`. Nothing refuses on one. A warning reaches you while you are still
choosing what to write, so **read it and write differently** rather than
reading it and proceeding.

**Read the `kind`, because two of them say opposite things.**
`disjoint_premises`, `cross_metacontext` and `same_metacontext_variant` say *this may be
the wrong call*; proceeding past one is journalled, whether or not the graph
was set to show it to you, and a later agent reads those back with
`review(mode="warning")`. Two say the reverse, that the call was right and
nothing is recorded against it: `same_metacontext_contradiction`, where the
conflict found wants a person, and `description_not_written`, where a
`tag_descriptions` entry named a tag the graph already describes and the stored
description stands. `notify_user: true` means raise it with the user, whichever
kind it came from. `configure_warnings` sets what a graph does with them; ask the user
before changing it.

**Human-in-the-loop.**
- When a response returns `notify_user: true`, surface it in conversation and
  ask the user. On `record_contradiction` that is a same-metacontext contradiction:
  do **not** silently pick a winner unless recency or source makes the call
  obvious.
- A cross-metacontext "conflict" is **not** a conflict. Don't interrupt the
  user; record a variant and say which metacontexts differ.
- Metacontext-crossing: when a metacontext-scoped answer looks thin and an
  associated metacontext may be relevant, *propose* consulting it and let the
  user approve. Always label borrowed knowledge with the metacontext it came
  from.

### Review labels (retrieval visibility)
`search` and `query_graph` results may carry a computed `review` field. Treat
a flagged node as provisional: mention the flag, and hop to the related ids
if useful.
- `superseded_candidate`: a newer fact may replace this one.
- `evidence_stale`: an inference whose supporting evidence was superseded;
  its basis changed and it may need re-deriving.
- `evidence_merged`: an inference whose premise absorbed another claim.
  Nothing was overturned and the premise gained provenance, so this asks for
  a re-read, not a re-derivation; the ids name the phrasings that went away.
  Record the re-read with `apply_reflection(retained=[...])`, `covers` naming
  those ids.
- `contested`: an unresolved same-metacontext contradiction; do not trust it
  blindly.

### Recording that something matters (judge_importance)
- Two things are tracked, and they move independently. *Use* is recorded for
  you: every node a search returns gets `retrieved_at` stamped, and you never
  touch it. `importance` answers "does this matter?" and only moves when
  someone judges that it does. Being read a lot is not a judgment.
- `judge_importance(node_id, direction, reason, related_id=None)` is that
  judgment, in both directions. Pass `related_id` when a specific new node
  triggered the reassessment.
- **`direction="up"`** when new knowledge raises a node's standing: evidence
  arrives supporting it, a decision turns out to hinge on it, it keeps
  proving load-bearing.
- **`direction="down"`** when a node's importance has expired rather than its
  truth: an error record that mattered until the bug was fixed, a decision
  overtaken by events. Importance is what protects a node from the archival
  sweep, so a judgment nobody revisits keeps junk alive for ever. Judging down
  hands the node back to review without claiming it should be archived
  outright.
- Don't judge a node up just because you read it; retrieval already recorded
  that.
- `reason` is read by whoever later reviews the judgment, so write it for
  them. After ingest there is no way to set importance directly: every change
  is a judgment that keeps its reason.

### When to reflect (reflect)
- After ingesting several documents (the system suggests a reflect after the
  configured threshold), when asked to consolidate, or periodically in long
  sessions.
- **`backup_suggested` is a second prompt, and it is not yours to act on
  alone.** `store_decomposition`, `apply_reflection` and `graph_stats` report
  `stores_since_backup` against `backup_threshold`, and when the count reaches
  it, `backup_suggested` is true. Raise it with the user, in one line, saying
  how many stores it has been. If they say yes, call `backup_graph`; if they
  say no or say nothing, leave it, and the prompt returns on the next store.
  Reflecting does not clear it, and a backup does not clear the reflect count.
- **`backup_graph` takes no path and you must not look for one.** Where a graph
  goes was decided once by the user, in `EPIMEMER_BACKUP_DESTINATION`. If the
  tool refuses because nothing is configured, tell the user that and stop:
  writing the graph somewhere of your own choosing, through any other tool, is
  never the answer. The user's own route is `epimemer graphs export`, a
  command for them to run.
- `reflect` returns `similar_pairs` (topic pairs, duplicate source, tag and
  entity topics included), `split_candidates`, `enrichment_candidates`,
  `contradictions` (same-metacontext fact pairs), `temporal_contradictions`
  (see Timelines), `recurrences`, `boundary_proposals`, `unsound_inferences`,
  `inference_merge_candidates`, `similar_inferences`, `pending_review` (nodes
  already flagged for resolution), `archival_candidates` (see Cleanup) and
  `similar_relations` (likely-synonymous relationship labels).
- **Check `truncated`.** Each list built from pairs is capped to its
  highest-scoring `max_nominations` (200 by default), and any that was cut is
  named in `truncated`. **When a list is named there, act on what came back
  and reflect again** rather than raising the number: what was dropped is the
  weakest end of the ranking. `archival_candidates` has its own bound of 20,
  is never named in `truncated`, and refills on the next reflect.
- **`boundary_proposals`** offers to close a period where you have already
  judged that the world moved on: once
  `supersede_by(because="the_world_changed")` has recorded the succession,
  reflect proposes the successor's own start date, which the *other* document
  gave, as the predecessor's end. Each proposal shows `current` and
  `proposed`. Accept the ones you agree with via
  `apply_reflection(boundaries=[{node_id, source_id, endpoint, at,
  timeline_id}])`, copying `node.id` from the proposal as `node_id` and the
  other four as they are. **Check what changes before accepting**: the
  period's basis becomes `inferred`, so one whose other end a document stated
  stops being reportable as stated. Do not substitute a date you happen to
  know; the prohibition on world knowledge applies here exactly as it does
  to `validity` at ingest. **Decline the ones you disagree with** via
  `apply_reflection(boundaries_declined=[{node_id, source_id, endpoint, at,
  timeline_id, reason}])`, saying in `reason` why that date is not when the
  period ended or began. An unanswered proposal comes back on every reflect.
  A decline leaves the period open and writes nothing between the two claims,
  so they can still be offered as a pair; a proposal for another date is a
  different question and is offered again. `reopen(boundary={...})`
  withdraws a decline you think was wrong.
- **`unsound_inferences`** names an inference whose premises no source puts
  in the same period (*"X held 1997–2010"* and *"Y held from 2024"*, combined
  into a conclusion), with the offending pairs and their dates. Re-derive the
  inference, narrow it to a period it can support, or retire it with
  `supersede_by(because="it_was_wrong")`. Read it as *no source asserts these
  were ever both true*, not as *they never were*: it stays silent unless both
  premises carry dates that provably fall clear, so its absence proves
  nothing.
- **`inference_merge_candidates`** names two near-identical active
  inferences resting on at least one common premise, the shape a fact merge
  produces. Each candidate carries `shared_premises`, a `similarity`, and any
  `warnings` about the merge that would result. Act with
  `merge_inferences(source_ids, content)` where they are one conclusion, or
  record `apply_reflection(similarities=[{pair, verdict, because}])`:
  `distinct` where they are two, `one_claim` where they agree and are worth
  keeping as two readings.
- **`similar_inferences`** names two active inferences that say nearly the
  same thing and rest on no common premise, each shown with its premises.
  Judge every pair with `apply_reflection(similarities=[{pair, verdict,
  because}])`: `one_claim` where they draw the same conclusion, `distinct`
  where they only look alike. Between inferences `one_claim` adds nothing to
  a corroboration count, so the question is only whether they agree.
- **Enrichment describes a topic; it never renames one.** The description is
  written beside the topic's `content`, which stays exactly as it is, because
  `content` is the name tags and `find_nodes(tagged_with_topic=...)` resolve
  by. Replacing a description keeps the earlier wording on the node.
- **Rename a topic with `rename_topic`, not `update`.** A rename keeps the
  node's id, description and edges and records the old name on the node;
  `update` would retire the topic as a mistake or leave its material behind on
  the old version. After a rename the old name no longer finds the topic.
- **An enrichment nomination arrives when the material under a described topic
  changed**, and it shows you the change: `since`, the moment somebody last
  wrote or confirmed the description, and `changed_material`, the nodes
  created, archived or superseded since then, newest first, at most twenty.
  `changed_count` and `material_count` say how many changed in all and how
  much the description stands over. A topic whose description nobody has
  written or confirmed (a description given with a new tag counts as written)
  comes with `since: null` and a `sample` of its material instead: that one is
  the first-time question, *what is this topic about*.
- **Answer every nomination, one way or the other.**
  - `apply_reflection(enrichments=[{topic_id, description}])` where the change
    makes the description wrong or incomplete. Write what the material shows
    the topic to be about, in a sentence or two.
  - `apply_reflection(descriptions_confirmed=[topic_id])` where you read it
    against the change and it still fits, the common case once a topic is
    described. It writes nothing and stops the topic being nominated until
    its material moves again.

  A nomination you leave unanswered comes back on the next reflect unchanged.
- **A split nomination has the same two answers.** `splits=[{topic_id,
  subtopics}]` where the material really is two subjects, and
  `splits_declined=[topic_id]` where you read it and it is one topic, which is
  the common answer. The topic is then not offered for splitting again until
  its material moves.
- Apply your decisions with `apply_reflection`. To resolve flagged nodes in
  batch, pass `supersessions=[{old_id, by_id, because}]`, `because` being
  `it_was_wrong` or `the_world_changed` as on `supersede_by`. Retiring the
  loser clears the supersession proposals pointing at it, and the winner's
  `contested` label, since a contest with a retired partner is resolved.
  Escalate anything you shouldn't decide alone to the user.
- **Record the pairs you decline, or they come back for ever.** A nominated
  pair you take no other action on needs `apply_reflection(similarities=[{pair,
  verdict, because}])`, or the next `reflect` offers it again. Two verdicts:
  - `"one_claim"`: the two really do say the same thing and something blocked
    the merge (an event, an unjudged `claim_kind`). Writes a `similarity`
    edge, **which corroboration counts as a second source** between facts and
    lists as agreement between inferences, plus `assessed`.
  - `"distinct"`: different claims that merely look alike. Writes `assessed`
    only, which corroboration never reads.

  Both stop the pair being nominated, so the suppression is never a reason
  to pick `one_claim`. Reach for it only where you would have merged:
  recording a decline as a similarity is how a graph starts manufacturing
  its own support. `because` is required, and anything refused comes back
  in `similarities_refused`; a cross-metacontext pair wants `record_variant`
  instead.

  **To take back a `one_claim`, record `distinct` on the same pair.** The
  count returns to what it would have been. You get one withdrawal: nothing
  re-asserts `one_claim` afterwards. If they really are one claim,
  `merge_facts` is the call.

  **To take back a `distinct`, call `reopen`.** `reopen(node_ids=[a, b],
  reason=...)` retires the `assessed` edge, so the next `reflect` offers the
  pair again, carrying `reopened` with your reason and the date. It asserts
  nothing: it does not record the opposite verdict, it only re-asks the
  question. Reach for it when later evidence makes a decline look wrong, not
  when you disagree with it on the same material. It is refused where the
  pair still carries a `similarity`, `contradiction` or `variant_of` edge,
  because withdrawing one of those is a verdict rather than a question.
- **Source, topic and entity consolidation** is ordinary topic merge:
  they are Topics, so pass `merges=[...]` for synonymous ones.
- **Judge every relation pair you are shown, including the ones you
  decline.** `relation_verdicts=[{pair: [a, b], kind, verdict: "distinct" |
  "synonymous", because}]`: `distinct` for two different relationships that
  look alike, `synonymous` for one relationship written two ways. A pair you
  skip is re-offered on every reflect, for ever. Neither verdict rewrites an
  edge or removes a label: what a label means here is settled with
  `describe_relation`. A verdict that later looks wrong is put back with
  `reopen(relation_labels=[a, b], reason=...)`, which asserts nothing about
  which answer is right.

### Cleanup (archival_candidates, then apply_reflection archivals)
Trivial knowledge is the counterpart to *wrong* knowledge, and it is handled
by the same loop: nomination proposes, you judge, the **user approves**.
- `reflect` returns `archival_candidates`, each with a `reason`: `retired`
  (long corrected or merged, and unimportant), `evidence_stale` (an inference
  whose basis changed), `never_retrieved` (an active node, other than an
  inference, that no search has ever returned, nothing depends on, no
  judgment has raised, and no deferral is still holding back), and
  `stale_judgment` (a node held above the importance ceiling by an upward
  judgment nobody has revisited in months).
- **`stale_judgment` is not an archival proposal.** It asks you to re-confirm
  or lower an assessment that may have expired. Answer it with
  `apply_reflection(judgments=[...])`, in whichever direction the graph now
  supports. Judging it back *up* is a good answer and needs no user approval,
  because it changes a degree rather than a status.
- Judge each one *with graph context*; triviality is only visible from the
  neighbourhood. "Error message X" matters while the bug is open and stops
  mattering once it is fixed. If a nominee turns out to matter, judge it up
  instead; that is the answer to a wrong nomination, not silence.
- **Ask the user before archiving.** Surface the list, say why each was
  nominated, and pass only the approved ids as
  `apply_reflection(archivals=[...])`. Keep the `archive_data` it returns.
- Nothing is deleted. Archived nodes leave search and every active query;
  `restore` puts them back.
- Never archive an inference on your own initiative. A stale inference is a
  prompt to re-derive it.
- **A nominee you looked at and kept is a verdict too**:
  `apply_reflection(retained=[{node_id, because, covers}])`. Left
  unrecorded, the node comes back on every reflect. `covers` is exactly the
  ids `pending_review` lists beside the node under `evidence_stale` and
  `evidence_merged`; omit it for a `never_retrieved` nominee, which names no
  reason. A reason a standing keep already covers is neither listed nor
  accepted again, and a premise that changes later is a new reason the old
  keep does not cover.
- **`retained` on a `never_retrieved` nominee is permanent.** The nomination
  names no reason, so nothing that happens later can be a reason the keep
  failed to cover, and the node is not offered again until `reopen`
  withdraws the keep. Use it only where you mean *keep this for good*.
- **Where you mean *not now*, defer it**:
  `apply_reflection(deferred=[{node_id, because}])`, for a `never_retrieved`
  nominee only. The node is left out of `archival_candidates` for 2 reflect
  rounds, then 4 after a second deferral, doubling up to 32. A round is a
  reflect that completes with at least one store since the previous reflect,
  so reflecting twice in a row does not shorten the wait. When the wait is
  over the node comes back first among the `never_retrieved` nominees,
  carrying `deferred`: how many times it was put off, `rounds_waited`, the
  `because` you gave, who gave it, and `next_wait`, the wait another deferral
  would set. If what you said last time still holds, deferring again is a
  sound answer; archive or keep it once something has changed that settles
  the question. `because` is required, and `deferrals_recorded` says how many
  rounds each node waits. A node also named in `archivals` is archived and its
  deferral skipped; one named in both `retained` and `deferred` is refused in
  both, with the reason in `retained_skipped` and `deferred_skipped`.
  `configure_deferral` changes the two numbers for one graph; ask the user
  before changing them.
- **A keep, or a deferral still waiting, that turns out to be wrong is
  withdrawn with `reopen(node_ids=[node_id], reason=...)`.** The node is
  offered on the next reflect, if it still qualifies, with the reason
  attached. It archives nothing: the user still approves anything that is
  then archived.

### Timelines (when things happened)
Distinct from record time: a timeline is about the *content*, not about when
the graph learned it.
- `create_timeline` names one; `add_timepoint` adds a moment (concrete, an
  interval, or a vague label only); `create_timelink` attaches a node to a
  timepoint. A vague timepoint is fine and preferred over a guessed date:
  "during the Renaissance" must not become 1500-01-01.
- **`reference_time` is the timeline's own "now"**: what a reader centres on
  and measures past and future against. Set it (at `create_timeline`, or
  later with `set_reference_time`) when a timeline is fictional or historical
  and its present is not today: "the novel opens in May 1897". Leave it unset
  for anything that tracks real time. Unset means *follow the clock*, which
  is not the same as passing today's date. Expect to set it *after* ingesting
  enough of a source to know the anchor; `set_reference_time` with no
  timestamp clears it.
- **`order_timepoints` is how a point nobody dated gets a place in time.** A
  source saying "the fire came before the flood" is recorded with that source
  and a `basis` of `stated` or `inferred`: `stated` when the source says the
  order in words, `inferred` when you read it off tense or context. Narrative
  order is not chronological order, so a document that tells of the fire and
  then the flood has stated nothing. Ask `query_timeline` for `before`,
  `after` or `between`, and a point with no date of its own comes back with
  `earliest` and `latest` the order derived for it.
- **`contested` on a point means the order around it is in dispute**, never
  that its date is wrong. A contested point keeps any date it has and gets no
  derived position, and `search` marks a fact dated to one with
  `date_contested`, which is separate from `contested` on the claim itself.
- Answer a dispute with `resolve_temporal_contradiction`, which takes three
  verdicts. **retire_constraint** when one source's assertion should not be
  believed, because it was misread, because the source is unreliable, or
  because the order is narrative. **not_the_same_event** when both sources are
  right and the point is really two events: a new point takes the label and
  the constraints and facts you name move to it. **hold** when they genuinely
  disagree and nothing you have settles it, which stops the nomination until
  new evidence about one of those points arrives.
- `merge_timepoints` is the reverse: two marks for one event, folded into one,
  with every constraint and every fact dated to the retired point moved over.
  It refuses while a source's constraint orders the two against each other.
- `reflect` nominates open disputes about order in `temporal_contradictions`,
  beside the contradictions between claims, and keeps nominating one until it
  is answered or held.
- **`add_recurrence` records something that happens over and over**, as a rule
  rather than as a row of dates. Two kinds: by arithmetic (`anchor` and
  `period`, which needs no calendar, so it works on an invented timeline), or
  by the calendar (`rrule`, an RFC 5545 string, for "the second Tuesday of
  every month"). Occurrences are worked out when you ask, so `query_timeline`
  returns them beside the stored points, each named by the `occurrence_start`
  the rule gave it. Read the preview in the response: a mistyped rule is
  easiest to catch there.
- **A fact attaches at one of two levels.** Linked to the rule, it holds at
  every occurrence: "the service is at the parish church". Linked to one
  occurrence, it is about that one: "the 1897 service was moved to the hall".
  For the first, call `create_timelink` with `recurrence_id` in place of
  `timepoint_id`. For the second, call `add_timepoint` with `recurrence_id` and
  `occurrence_start` in place of dates, which turns that occurrence into a
  point you can link, order and dispute. Asking twice gives the same point.
  `query_timeline` shows both levels on every occurrence it returns: the rule's
  facts under `linked_via_rule` and that occurrence's own under `linked`.
- **`record_recurrence_exception` is for an occurrence that broke the
  pattern**: `cancelled` for one that did not happen, `moved` with `moved_to`
  for one that happened at another time. A moved occurrence keeps the start
  the rule gave it as its identity, so it is still the same occurrence.
- **`end_recurrence` says when a rule stopped applying, and never retires
  it.** "Christmas is 24 to 26 December, annually" does not stop being true,
  so each end date is appended with its reason and the most recent one is in
  force: a later correction shows what it corrected.

### Metacontexts
- **Absence names no metacontext.** A node with no metacontext is one nobody
  said anything about: never compared, never merged, returned by no scoped
  search. It does **not** mean base reality.
  `graph_stats.nodes_without_metacontext` counts such nodes, and a person
  assigns them a metacontext with the `epimemer metacontexts declare` CLI
  command.
- **`the-real` is a convention, not a mechanism.** The id every graph should
  use for the metacontext holding real-world claims. Nothing reads it
  specially, and it must exist here like any other; `create_metacontext`
  takes a chosen id.
- **A metacontext id must resolve in the graph you are in**, and is refused
  if it does not, on `store_decomposition` and on `search`, and on every id
  in a search's list. Ids are per graph, so one remembered from another
  names nothing here. The refusal lists the metacontexts that do exist (the
  first ten, and a count of the rest), which is the only listing there is.
- **A metacontext need not be fictional.** *"What Milanese people knew by 1860"*
  and *"what Londoners knew by 1860"* are two metacontexts over the same real
  past.
- **No metacontext inherits another.** Ask for the combination you want by naming
  it. *"Milan is in Lombardy"* belongs in the real-world metacontext; *"Milanese
  merchants believed the pass was closed"* belongs in the perspective metacontext,
  and a reader who wants both names both.
- **A metacontext can build on one other.** Record it with `based_on` on
  `create_metacontext`, or `set_metacontext_base`, and `diverges` as a date or
  a named moment. Scoped reads carry it in `metacontext_bases`,
  `get_metacontexts` shows it, and `find_nodes(variants_in=...)` lists where the
  two differ. No claims flow: name both metacontexts for the shared history,
  and trust the base's claims only up to the divergence.
- **An `apply_reflection` that would invent a metacontext refuses instead.**
  Splits inherit what their parent states and a synthesised parent inherits
  the set its children share; a `parents` or topic `merges` group standing in
  different metacontexts comes back in `parents_refused` or
  `topic_merges_refused` with nothing written. A merge re-states the
  survivor's metacontext under your judge. The union is never taken, with one
  exception: a merge or a parent over nothing but topic nodes created from
  tags, where the result stands in every metacontext its sources stood in.
- **Two perspectives disagreeing about one world are not nominated as a
  contradiction**: they share no metacontext, and the sweep skips such pairs.
  Usually right: they coexist, neither claiming the other is wrong. Where
  the disagreement *is* the finding, call `record_contradiction` directly. It
  does not refuse: the response carries `same_metacontext: false` and a
  `cross_metacontext` warning, the call is journalled as going ahead against
  that warning, and neither fact becomes `contested`.
- Never present a claim from a fictional or otherwise non-real metacontext as
  fact; always surface the `metacontexts` label, and when creating new
  metacontexts use clear, descriptive names.

### Saying which judge you are (claim_agent)
- Call it once per session, before writing anything. **Propose a name** and
  describe yourself; the user approves, and may hand back a different judge.
  Never pick one and assume it.
- **A different judge coming back is the normal case, not a correction.**
  The user picks from the judges this graph already knows, so a proposed new
  name usually resolves to an existing judge. Use what comes back from then
  on.
- **Say `name` to the user, and keep `agent_id` for `review`.** The response
  carries both: `agent_id` is an opaque key not meant for showing to anybody,
  and `name` is what this judge is called. Either works on the way in, as
  does any key the judge used to be recorded under. The user can rename a
  judge later and every decision it made follows the new name, so do not
  agonise over it.
- **`new_agent: true` means you made a judge rather than joined one.** Say
  so: near-duplicate judges are how a review of one agent's decisions ends
  up returning half of them. If two judges here are obviously the same one,
  tell the user; they can consolidate them, and only they can.
- A refusal is not an error to work around; it is the prompt. Put its
  message to the user and let them decide what you should be called.
- **A judge can be retired**, and claiming one is refused with a message
  saying when. Its decisions are all still there and `review` still answers
  for it. Relay the refusal and ask which judge you should be instead; only
  the user can bring one back.
- **Your description is a claim, not a credential.** Nothing verifies it.
  Describe what you are in a way that would let someone tell you from
  another agent (the model or harness, the role you were given) and do not
  overstate it. Re-describing appends a version and never edits one.
- **Approval is per graph, so claim again after `use_graph`.** Its
  `judge_cleared` names the judge the switch unbound from this connection. A
  token whose judge the new graph does not approve is not refused unless the
  graph requires a judge: its writes are recorded with no judge.
- **Claim again after a reconnect too.** Your `judge_token` does not survive
  one. Where the conversation, the client and its version, and the model are
  the ones the user already confirmed this judge in (the seat), the claim
  binds without asking and says it was carried over; where it asks,
  `asked_because` says why.
- **A write refused because the seat changed means claim again**, and the user
  will be asked. The message names what changed, such as a new model since the
  claim; do not retry the write before claiming.
- **Your `judge_token` names your claim**, so a write carrying it is credited
  to the judge you claimed even where another agent has claimed on the same
  connection since. A token that connection never issued is refused, with a
  message naming `claim_agent`.
- Once you have claimed one, the decisions you make carry it: who retired a
  node, who brought it back, who asserted a contradiction, who wrote a
  synthesised topic, and every node and prior you supply at ingest.
- If a graph requires a judge, a write without one is refused and the
  message names `claim_agent`. Put it to the user, since only they can
  approve a judge or turn the requirement off.

### Reviewing what was decided (review)
- `review` returns this graph's recorded decisions **least certain first**
  and writes nothing. Use it when the user asks what has been decided, before
  trusting a count you did not produce, or as a pass over your own session's
  work.
- Read the top of the list, not all of it. That is what the ordering is for:
  stop when it stops repaying the attention.
- **`difficulty_signals` says why a row is near the top**, and none of them
  are verdicts: `thin_source` (a subject's own confidence is low),
  `wide_merge` (three or more sources in one node), `open_contradiction`
  (recorded, both sides still active), `ground_moved` (a subject was retired
  after the decision). They mark what is worth looking at, never what is
  wrong.
- **A blank `certainty` means unrated, not doubtful.** Rows an agent actually
  flagged sort above unrated ones however many signals those carry.
- Check `unrated_count` and `unattributed_count` before reporting a
  conclusion: three shaky rows out of four hundred unrated is not three out
  of four. Check `truncated` too; act on what came back and review again
  rather than raising `max_results`.
- **`by_agent` takes a handle**, not only a key: a name, a key, or a key that
  judge used to be recorded under. The `judge` block says what it resolved
  to, and `unknown_here` means it named nobody; otherwise an empty page
  reads like a judge that decided nothing.
- **It answers for one graph**, named in `graph`. For another, `use_graph`
  and ask again.
- **`elsewhere` tells you whether there is anything to go and see**: a count
  of decisions per other graph, no rows and no ids. Counted by `agent_id`,
  `since` and `until` only (`counted_with` says so), so a graph counted at 12
  may list fewer than 12 once you switch to it: wider, never narrower. Zero
  means nothing is there; `unreadable` means it was not counted.
- It sees only decisions made since the journal existed. An older graph can
  be full of judgments `review` will never show.
- **Modes select; the other arguments narrow whatever was selected.** `all`,
  `by_agent` (needs `agent_id`), `since` (needs `since`; `until` is
  exclusive), `unreviewed`, and `warning` (operations that went ahead
  against an objecting warning). So *"what did agent-1 decide yesterday
  that nobody checked"* is one call. For one judge or one window, prefer
  `by_agent` or `since` to `all`: they make their argument mandatory, and
  `all` with a forgotten `agent_id` returns everything, which reads as an
  answer.
- `certainty_ceiling` is for **counting**, not browsing: *"is anything below
  0.5 still outstanding?"* It is inclusive and leaves unrated rows out
  entirely.

### Saying you checked one (apply_review)
- **Confirming costs something, or the next agent redoes your work.** If you
  check a decision and say nothing, `review(mode="unreviewed")` keeps
  offering it. `apply_review(confirmations=[…], dissents=[…])` is the only
  call that records a confirmation or a dissent.
- Each entry is `{decision_id, because, subject_ids?, certainty?,
  certainty_basis?}`. **`because` is required**, and an entry without one is
  refused: a review with no reason would mark the decision checked, so the
  next reviewer skips it without knowing whether it was examined or waved
  through.
- **Name `subject_ids` when you checked part of a decision.** One pointer at
  an ingest record covering forty-four facts tells the graph you checked
  forty-four. Omit it only when you really covered all of them.
- **A dissent records the finding, not the undo.** It changes nothing. Say in
  `because` what should happen, then make that call: `reverse_merge` for a
  merge, `restore` for an archival, `apply_reflection` with `verdict:
  "distinct"` for a `one_claim`, `rejudge` for a wrong ingest prior. A
  dissent is *most* useful where the undo was refused and there is nowhere
  else to put the finding.
- `certainty` here is how sure you are of **this review**, on the same
  ladder as `confidence`. Omit it for the ordinary case.
- Repeating your own confirmation or dissent over the same subjects is
  refused, since a second identical row would read as a second opinion.
  Naming other subjects of the decision, or the other verdict, is accepted.

### Fixing a judgment you made at ingest (rejudge)
- `claim_kind`, `confidence` and `confidence_basis` are yours and nothing
  downstream re-makes them. `rejudge` is the only way to correct one.
- **Do not reach for `update` or `supersede_by` here.** Those are for a claim
  that was wrong or a world that moved. `rejudge` is for a claim that is fine
  where the *judgment about* it was wrong: a `state` that is really an
  `event`, a confidence set too high. It retires nothing.
- `confidence` and `certainty` are different numbers on the same ladder:
  `confidence` is about the **material**, `certainty` is about **this act of
  re-judging**.
- `importance` is not here; `judge_importance` already revises that one.
- **A wrong metacontext is `reassign_metacontext`, not a supersession.**
  `reassign_metacontext(node_id, withdraw=…, because=…)` takes a metacontext
  off a node, and `assign=<other_metacontext>` moves the claim in the same
  call. Withdrawing a node's **last** metacontext without `assign` is refused:
  a node without a metacontext shares one with nothing, so name where the
  claim goes. A wrong metacontext is not cosmetic: it makes a fact
  permanently unmergeable with its own twin, stops it corroborating, and
  hides it from the metacontext it belongs to.
- **A wrong period is `correct_interval`, not a supersession.**
  `correct_interval(node_id, source_id=…, intervals=[…], because=…)`
  replaces the **whole** list for that (node, source) pair; an interval has
  no id of its own. It is for an endpoint that is *present and wrong*; one
  that is still open is filled from `reflect`'s `boundary_proposals`.
  An empty list is allowed and is how a period that was invented outright
  comes off. A wrong interval moves a corroboration count as well as a date.

### Interpreting _meta
Every tool response includes a `_meta` field:
- `nodes_returned`: how many nodes were found or affected (decisions, on
  `review`)
- `latency_ms`: how long the operation took
- `source_types`: breakdown by node type (topic, fact, inference), filled by
  the tools that return nodes

Surface this naturally: "Found 5 relevant nodes (2 topics, 2 facts, 1 inference)."
