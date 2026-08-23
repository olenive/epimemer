## Memory System (Epimemer)

You have access to an epistemic memory system via MCP tools. It is append-only:
nothing is destroyed — corrections and resolutions create new versions and leave
the history linked. Your job is to write fast and organize deliberately.

### When to ingest (segment → store_decomposition)
- After learning new information from the user or external sources, or when the
  user shares documents/knowledge you should remember.
- Call `segment` to split the text, extract topics/facts/inferences yourself, then
  call `store_decomposition`.
- **Say which graph you mean.** Pass `expected_graph` to `segment`, and thread the
  `active_graph` it returns into `store_decomposition` — a mismatch is refused
  instead of misfiled. The active graph is not remembered across a client
  reconnect, so a session that called `use_graph` earlier can come back somewhere
  else, and an ingest into the wrong graph succeeds in every other respect. If
  you do not know the name, `list_graphs` says which one is active.
- Pass a `metacontext_id` when the information has a specific framing (fiction, a
  particular source, a perspective). Untagged knowledge is treated as base reality
  — "The Real" (see Metacontexts below).
- An entry may carry an `importance` prior (`{"content": ..., "importance": 0.8}`)
  when you already know it is unusually consequential or unusually disposable.
  Usually leave it alone: importance is properly judged at reflect time, when the
  surrounding graph exists to judge it against.
- **Give every fact a `claim_kind`** (`{"content": ..., "claim_kind": "state"}`).
  Facts only — it is an error on a topic or an inference. Ask what kind of thing
  is being claimed:
  - `"state"` — a condition that holds over a period, and may hold again later.
    *"Labour is in government"*, *"the city is called Leningrad"*.
  - `"event"` — something that happened on an occasion. *"Labour won the
    election"*, *"the city was renamed"*.

  This is the one judgment nothing downstream can make. Two documents years
  apart yield near-identical sentences, and collapsing them is right for a state
  (one condition, two periods) and fabricates history for an event (two
  elections become one twenty-seven-year victory). Only you have the document —
  the tense, the sentences either side, whether *"the election"* is a particular
  one. **Omit it when you genuinely cannot tell**; the fact simply never merges,
  which costs less than a guess.

### Sources, tags, and relations (all nodes & edges, not strings)

Provenance and "aboutness" are modelled as **nodes and edges**, not string fields —
so a source or tag can carry its own facts, relate to siblings, and sit in a frame.
All are separate from metacontexts (which are epistemic *frames* that change
retrieval scope; these do not).

- **Source** — *where* knowledge came from. Pass `source`/`source_type` to
  `segment`; every node decomposed from it gets a `sourced_from` edge to the
  document. Name a publisher/author with `published_by="BBC"` — it becomes (or
  reuses) an entity **Topic** linked by an attribution edge, and can itself accrue
  facts.
- **Tags = Topics.** Pass `tags=[...]` to `store_decomposition` (doc-level) or per
  node via `{"content": ..., "tags": [...]}`. Each tag name becomes (or reuses, by
  exact name) a **Topic** linked by a `tagged_with` edge — so tag consolidation is
  just topic-merge. There are no `key=value` tags: a relationship dimension is an
  **edge** (`link(a, b, relation="spoken_by")`); a scalar like sensitivity belongs
  in node metadata.
- **Relations are open vocabulary.** `link(src, dst, relation="published_by",
  kind="attribution")` coins any relationship you need. `kind` is `relationship`
  (followed in retrieval) or `attribution` (where it came from / who said it — not
  followed); a label reuses its kind after first use. Known engine edges still use
  `link(..., edge_type="supports")`.

Discovery & lookup:
- **`find_nodes(sourced_from=…)` / `find_nodes(tagged_with=…)`** — exactly the nodes
  linked to a document/source or a concept (id or name). The graph-native "which
  nodes came from ISSUES.md / are about billing". Use instead of `search` when you
  want provenance/aboutness, not similarity.
- **`list_sources`** / **`list_relations`** — discover the sources and the
  user-defined relationship labels present before filtering or coining new ones.

### When to search (search)
- Before answering questions that might benefit from prior context, or when the
  user asks "do you remember…" / references past conversations.
- **Pass exact strings you care about as `terms`.** A ticket id, an error code, a
  person's name, a filename, a version number. Embeddings shred those:
  `JIRA-4417` becomes word pieces mean-pooled with the rest of the sentence, so
  the query embeds to roughly "short alphanumeric string" and *every other ticket
  id in the graph* scores about as well. A keyword arm runs alongside the vector
  one and supplies the term rarity similarity has no notion of. Terms are matched
  whole and ORed, and **each declared term's best hit is kept in the results**
  even if rank fusion would have cut it. Omit `terms` and the keyword arm falls
  back to the query's own words, with no such guarantee — declaring is the
  reliable path.
- Read each result's **`provenance`**: `lexical` (a term matched the node's
  content), `segment` (a term matched the passage it was extracted from),
  `vector` (similarity), `expanded` (reached by an edge from one of those). When
  a search disappoints, this is what tells you *why* something came back — or
  which arm failed to bring back what you wanted.
- The response also carries **`segments`** — passages that matched, whether or not
  anything was extracted from them. *Where did I read that?* is a different
  question from *what do I believe?*, and if you paraphrased an identifier out of
  a fact when you stored it, the segment is the only thing that still holds it.
- `search` is frame-scoped: passing `metacontext_id` returns that frame **plus**
  untagged base-reality nodes; set `cross_frame=true` to search across all frames.
- Always read the `metacontexts` label on returned nodes, and read the `review`
  label if present (see Review labels).
- **`include_corroboration=true` when independence is the question** — *is this
  one report repeated, or several outlets agreeing?* It is off by default because
  it is the most expensive thing on this path, so ask for it when you will act on
  the answer, not routinely. Read what it counts: **distinct publishers**, so
  three hedged reports from three outlets score 3 exactly as three confident ones
  would. It does not interact with `confidence` and neither replaces the other.
  Documents naming no publisher count as their own source, and
  `unattributed_documents` says how many did — a low count may mean nobody
  attributed the ingest rather than nobody corroborated the claim. Each source
  names the nodes behind it, so check the working before quoting the number.
  **Read `adjacent_periods` — it is not a reject list.** A look-alike whose
  source dates put it in a different period stops counting: *the city was called
  Leningrad* is not a second witness to *the city is called Saint Petersburg*,
  it is the claim about the period before. Nothing is removed from the graph and
  both are true of their own stretch, so the uncounted node comes back named
  there with its publisher and periods. Where a search returns one of a pair,
  that block is the only place the other appears — treat it as *the graph also
  knows what was true next door*, and say so rather than dropping it.

### Temporal queries — three axes, not one

**Two clocks run here and confusing them is the commonest mistake.** *Transaction
time* is what this memory held and when. *Valid time* is when the claim was true
in the world. A fact created last week can be about 1924.

- **`search(..., valid_as_of=…)` — what was **true** then.** Valid time. Results
  are grouped rather than filtered: each carries `valid_at`, either `valid` (a
  source asserts it held then) or `unknown` (nobody says). Nothing is excluded —
  an undated claim is unknown, not false. Pass `timeline_id` to read against a
  timeline other than the wall clock; for "is this current" inside a fictional
  frame, use that timeline's own `reference_time` rather than today's date.
- **`graph_as_of(at)` — what the graph **held** then.** Transaction time. Returns
  the knowledge set that was active *at* `at` (created by then, not yet retired).
  Use for "what did we believe on date X" / reproducing a past answer. Caveat: it
  is a node-lifecycle snapshot only — edges, metacontext, and review labels are
  present-state, not historized, so they are omitted.
- **`query_changes(...)` — deltas across a span.** Transaction time. Returns nodes
  whose **birth** (`created_at`) or **retirement** (`superseded_at`, covering
  supersede and merge) fell inside each half-open window `[start, end)`, each
  tagged with its `events` (`created` / `corrected` / `historical` / `merged` /
  `archived` / `restored`, with timestamps). Use for "what changed recently".
  Specify by `last_hours`/`last_days`, an explicit `windows` list, or nothing
  (defaults to the last 24h). Multiple windows are returned grouped; a node that
  changed in several appears in each.

A node born long ago but still active shows up in `graph_as_of` (it's live state)
but not in a recent `query_changes` window (nothing happened to it there) — unless
it was retired in that window, where it appears as a retirement event.

### Reading a search result's history

`search` returns claims the world has moved past as well as current ones, so
**read each result's `status`**: `active` is current, `historical` was right of
its period and is wrong to quote as current, `corrected` was concluded false and
only appears if you passed `include_corrected=true`. Pass
`include_historical=false` when you want only what holds now.

A claim's retired versions do not each take a slot — when a retired node and the
claim that replaced it both match, the replacement is the result and the retired
one comes back as `earlier_versions` on it.

Where sources dated a claim, the result carries `validity`: one entry per source,
with the periods that source asserts. Two sources may disagree and neither is
overwritten, so there is no single answer to read off — decide which source you
trust, or report the disagreement.

### Reviewing and reconciling knowledge

New information can make existing knowledge outdated, contradicted, or framed
differently. Detection is cheap recall; **judgment is yours** — similarity only
nominates candidates, it does not decide the relationship.

**Detect (`check_conflicts`).** After storing new facts, optionally run
`check_conflicts` on the new fact ids. It returns, per fact, similar facts with a
similarity `score`, their `status`, their `metacontexts`, and a `same_frame` flag.
Classify each candidate:

| Verdict | What it means | What to do |
| --- | --- | --- |
| redundant | the same claim restated, and the twin is **active** | `merge_facts([a, b], content=…)` — one node keeping both sources; or `link(a, b, edge_type="similarity")` and keep both when unsure |
| supersedes | the new fact corrects an outdated one — the old was **wrong** | `supersede_by(old_id, existing_id, because="it_was_wrong")`; or `update` if you have corrected *content* |
| succeeds | both true, over different periods — **the world moved** | `supersede_by(old_id, existing_id, because="the_world_changed")` |
| recurs | the same claim, previously retired as `historical`, is true again | `restore(node_ids=[…], sourced_from=…)` — reactivating requires naming the new source |
| contradicts | genuine conflict, **same frame**, unclear which holds | `record_contradiction(a, b)` |
| cross-frame | only "conflicts" because the frames differ | `record_variant(a, b)` — not a conflict |
| compatible | no conflict | nothing |

**Read the candidate's `status` before choosing between the first and fourth
rows.** An identical claim beside an *active* twin is `redundant`; beside a
`historical` one it is `recurs` — the world came back around, and the right move
is to bring the existing node back rather than mint a second copy of it.

**`because` is a judgment, and it has no safe default.** Retiring a node says
*why*: `"it_was_wrong"` (it should not have been believed) or
`"the_world_changed"` (it was right, and is still right of its period — a city
renamed, a government replaced). They are opposite claims about the old node,
and the wrong one either files history as an error or preserves a mistake as
history. **If you cannot tell** — two undated claims, no knowledge of which came
first — do not pick one. Use `record_contradiction(a, b)` and leave the pair
contested for someone who can resolve it. A guessed `because` is
indistinguishable afterwards from a judged one.

**Merging is the one verdict that is worse to get wrong than to skip.** Two
distinct claims fused into one node with two independent sources read as *better
supported* than either was — the mistake does not lose information, it
manufactures agreement. So `merge_facts` refuses on doubt and says why: an
**event** never merges, nor does a pair in different frames (that is
`record_variant`), a retired twin (that is `restore`), or a fact ingested
without a `claim_kind`. Read the `refused` line — it names which. When in doubt,
record a `similarity` edge and keep both; nothing downstream is harmed by two
nodes saying one thing, and corroboration already reads a similarity
neighbourhood.

**Record verdicts.**
- `record_contradiction(a, b)` — both facts stay active and become `contested`;
  the response includes a `notify_user` signal.
- `merge_facts(source_ids, content)` — collapse facts restating one claim into a
  single node. Every source is retired as `merged` and linked to the survivor,
  which keeps one `sourced_from` edge per contributing document, each with that
  document's own periods — so provenance becomes plural rather than being
  overwritten. Write `content` as the clearest phrasing of the shared claim;
  that is what gets embedded.
- `reverse_merge(survivor_id)` — undo a merge that turned out to collapse two
  different claims. The sources come back active with their own edges and the
  survivor is deleted; this is the **only** tool that deletes a node, and it is
  refused if anything has been added to the survivor since the merge, because
  reversing would take those edges with it. Merging and reversing the same facts
  repeatedly will refuse and ask you to bring in the user — that is deliberate.
- `record_variant(a, b)` — records a cross-frame divergence so it stays queryable.
- `supersede_by(old_id, existing_id, because=…)` — retire an outdated node in
  favour of one already in the graph (dependent inferences are flagged
  `evidence_stale`). On `"the_world_changed"` the retired node keeps its own
  sources: it is still true of its period, and its sources are what say so.

**Human-in-the-loop.**
- When `record_contradiction` returns `notify_user: true` (a same-frame
  contradiction), surface it in conversation and ask the user how to resolve it —
  do **not** silently pick a winner unless recency or source makes the call
  obvious.
- A cross-frame "conflict" is **not** a conflict. Don't interrupt the user; record
  a variant and note the framing.
- Frame-crossing: when a frame-scoped answer looks thin and an associated frame
  may be relevant, *propose* consulting it and let the user approve — always label
  borrowed knowledge with the frame it came from.

### Review labels (retrieval visibility)
`search` and `query_graph` results may carry a computed `review` field. Treat a
flagged node as provisional — mention the flag, and hop to the related ids if
useful:
- `superseded_candidate` → a newer fact may replace this one.
- `evidence_stale` → an inference whose supporting evidence was superseded; its
  basis changed and it may need re-deriving.
- `evidence_merged` → an inference whose premise absorbed another claim. Nothing
  was overturned and the premise gained provenance, so this asks for a re-read,
  not a re-derivation; the ids name the phrasings that went away.
- `contested` → an unresolved same-frame contradiction; do not trust it blindly.

### Recording that something matters (judge_importance)
- Two things are tracked, and they move independently. *Use* is recorded for
  you: every node a search returns gets `retrieved_at` stamped, and you never
  touch it. `importance` answers "does this matter?" and only moves when someone
  judges that it does — being read a lot is not a judgment.
- `judge_importance(node_id, direction, reason, related_id=None)` is that
  judgment, in both directions. Pass `related_id` when a specific new node
  triggered the reassessment.
- **`direction="up"`** when new knowledge raises a node's standing: evidence
  arrives supporting it, a decision turns out to hinge on it, it keeps proving
  load-bearing.
- **`direction="down"`** when a node's importance has expired rather than its
  truth — an error record that mattered until the bug was fixed, a decision
  overtaken by events. This matters more than it sounds: importance is what
  protects a node from the archival sweep, so a judgment nobody revisits keeps
  junk alive forever. Judging down hands the node back to review without
  claiming it should be archived outright, which is a stronger claim you may
  not be entitled to make.
- Don't judge a node up just because you read it — retrieval already did that.
- `reason` is read by whoever later reviews the judgment, so write it for them.
  There is no way to set importance directly; every judgment keeps its reason,
  and a raw value would silently overwrite every judgment before it.

### When to reflect (reflect)
- After ingesting several documents (the system auto-suggests reflect after the
  configured threshold), when asked to consolidate, or periodically in long
  sessions.
- `reflect` returns consolidation candidates (similar pairs, splits, enrichments —
  similar pairs also surface duplicate source/tag/entity Topics), same-frame
  contradiction candidates, `recurrences`, `boundary_proposals` and
  `unsound_inferences` (both below), `pending_review` — the worklist of nodes
  already flagged for resolution —
  `archival_candidates` (see below), and `similar_relations`, likely-synonymous
  user relationship labels.
- **Check `truncated`.** Four of the lists are built out of *pairs* —
  `similar_pairs`, `contradictions`, `recurrences`, `similar_relations` — and
  pairs grow quadratically where every other list grows with the node count. Each
  is capped to its highest-scoring `max_nominations` (200 by default), and any
  that was cut is named in `truncated`. An empty `truncated` means you saw
  everything. **When a list is named there, act on what came back and reflect
  again** rather than raising the number: what was dropped is the weakest end of
  the ranking, and a graph dense enough to hit the cap wants repeated passes, not
  a longer response to read in one go.
- **`boundary_proposals`** offers to close a period where you have already
  judged that the world moved on. Once `supersede_by(because="the_world_changed")`
  has recorded the succession, reflect can take the successor's own start date and
  propose it as the predecessor's end — a date the *other* document gave, which
  is something no single ingest could supply. Each proposal shows `current` and
  `proposed`; accept the ones you agree with via
  `apply_reflection(boundaries=[{node_id, source_id, endpoint, at, timeline_id}])`.
  **Check what changes before accepting**: the period's basis becomes `inferred`,
  so one whose other end a document stated stops being reportable as stated. Do
  not substitute a date you happen to know — the prohibition on world knowledge
  applies here exactly as it does at ingest.
- **`unsound_inferences`** names an inference whose premises no source puts in
  the same period — *"X held 1997–2010"* and *"Y held from 2024"*, combined into
  a conclusion — with the offending pairs and their dates. It reports; you
  decide: re-derive the inference, narrow it to a period it can support, or
  retire it with `supersede_by(because="it_was_wrong")`. Read it as *no source
  asserts these were ever both true*, not as *they never were*: it stays silent
  unless both premises carry dates and those dates provably fall clear, so a
  flag is rare and worth reading, and its absence proves nothing.
- Apply your decisions with `apply_reflection`. To resolve flagged nodes in batch,
  pass `supersessions=[{old_id, by_id}]`. Resolving a loser automatically clears
  the winner's `contested` / `superseded_candidate` labels. Escalate anything you
  shouldn't decide alone to the user.
- **Record the pairs you decline, or they come back forever.** A nominated pair
  you take no other action on needs
  `apply_reflection(similarities=[{pair, verdict, because}])` — otherwise nothing
  records that you looked, and the next `reflect` offers it again. Two verdicts:
  - `"one_claim"` — the two really do say the same thing and something blocked
    the merge (an event, an unjudged `claim_kind`). Writes a `similarity` edge,
    **which corroboration counts as a second source**, plus `assessed`.
  - `"distinct"` — different claims that merely look alike. Writes `assessed`
    only, which corroboration never reads.

  Both stop the pair being nominated, so the suppression is never a reason to
  pick `one_claim`. Reach for it only where you would have merged: recording a
  decline as a similarity is how a graph starts manufacturing its own support.
  `because` is required, and anything refused comes back in
  `similarities_refused` — a cross-frame pair wants `record_variant` instead.

  **To take back a `one_claim`, record `distinct` on the same pair.** The count
  returns to what it would have been. You get one withdrawal: nothing
  re-asserts `one_claim` afterwards, because a wrong withdrawal only withholds
  support while a wrong re-assertion invents it. If they really are one claim,
  `merge_facts` is the call.
- **Source/tag/entity consolidation** is ordinary topic-merge — they're Topics, so
  pass `merges=[...]` for synonymous ones.
- **Relation consolidation**: for `similar_relations` you judge synonymous, pass
  `relation_merges=[{labels: ["written_by"], into: "authored_by"}]`. Every user-tier
  edge with a listed label is relabelled in place (edges aren't versioned).

### Cleanup (archival_candidates → apply_reflection archivals)
Trivial knowledge is the counterpart to *wrong* knowledge, and it is handled by
the same loop: nomination proposes, you judge, the **user approves**.
- `reflect` returns `archival_candidates`, each with a `reason`: `retired` (long
  superseded or merged and unimportant), `evidence_stale` (an inference whose
  basis changed), `never_retrieved` (an active node no search has ever returned,
  judged or depended on since it was created), and `stale_judgment` (a node held
  above the importance ceiling by an upward judgment nobody has revisited in
  months).
- **`stale_judgment` is not an archival proposal.** It asks you to re-confirm or
  lower an assessment that may have expired — importance is what protects a node
  from every other class here, so a judgment left unrevisited protects it
  forever. Answer it with `apply_reflection(judgments=[...])`, in whichever
  direction the graph now supports. Judging it back *up* is a perfectly good
  answer and needs no user approval, because it changes a degree rather than a
  status.
- Judge each one *with graph context* — triviality is only visible from the
  neighbourhood. "Error message X" matters while the bug is open and stops
  mattering once it is fixed. If a nominee turns out to matter, judge it up
  instead; that is the answer to a wrong nomination, not silence.
- **Ask the user before archiving.** Surface the list, say why each was
  nominated, and pass only the approved ids as
  `apply_reflection(archivals=[...])`. Keep the `archive_data` it returns.
- Nothing is deleted. Archived nodes leave search and every active query;
  `restore` puts them back.
- Never archive an inference on your own initiative. A stale inference is a
  prompt to re-derive it, and inferences are the expensive layer to recreate.

### Timelines (when things happened)
Distinct from record time — a timeline is about the *content*, not about when the
graph learned it.
- `create_timeline` names one; `add_timepoint` adds a moment (concrete, an
  interval, or vague label-only); `create_timelink` attaches a node to a
  timepoint. A vague timepoint is fine and preferred over a guessed date — "during
  the Renaissance" must not become 1500-01-01.
- **`reference_time` is the timeline's own "now"** — what a reader centres on and
  measures past and future against. Set it (at `create_timeline`, or later with
  `set_reference_time`) when a timeline is fictional or historical and its present
  is not today: "the novel opens in May 1897". Leave it unset for anything that
  tracks real time — unset means *follow the clock*, which is not the same as
  passing today's date.
- Expect to set it *after* ingesting enough of a source to know the anchor, and to
  revise it when you learn you read it wrong. `set_reference_time` with no
  timestamp clears it.

### Metacontexts (epistemic frames)
- Untagged knowledge is implicitly "The Real" — base physical reality.
- Tag framed knowledge (a fiction setting, a specific source/perspective) with a
  metacontext so it never bleeds into base reality. A fact that is true *within* a
  fiction is not a contradiction of real history — it is a different frame.
- Never present framed/fictional information as fact; always surface the
  `metacontexts` label, and when creating new metacontexts use clear, descriptive
  names.

### Saying which judge you are (claim_agent)
- Call it once per session, before writing anything, if the user has set up
  agent identities. **Propose** an id and describe yourself; the user approves,
  and may hand back a different id. Never pick an id and assume it.
- A refusal is not an error to work around — it is the prompt. Put its message
  to the user and let them decide what you should be called; the id is theirs to
  assign, and it is what lets a later review show that a *different* agent made
  these decisions.
- **Your description is a claim, not a credential.** Nothing verifies it.
  Describe what you are in a way that would let someone tell you from another
  agent — the model or harness, the role you were given — and do not overstate
  it. Re-describing appends a version and never edits one.
- Approval is per graph. After `use_graph`, check whether the response says your
  judge was unbound, and claim again if it did.
- Once you have claimed one, the decisions you make carry it: who retired a
  node, who brought it back, who asserted a contradiction, who wrote a
  synthesised topic, and every node and prior you supply at ingest. You pass
  nothing — it comes from the session.
- If a graph requires a judge, a write without one is refused and the message
  names `claim_agent`. That is not something to work around — put it to the
  user, since only they can approve an id or turn the requirement off.

### Reviewing what was decided (review)
- `review` returns this graph's recorded decisions **shakiest first** and writes
  nothing. Use it when the user asks what has been decided, before trusting a
  count you did not produce, or as a pass over your own session's work.
- Read the top of the list, not all of it. That is what the ordering is for —
  stop when it stops repaying the attention.
- **`difficulty_signals` says why a row is near the top**, and none of them are
  verdicts: `thin_source` (a subject's own confidence is low), `wide_merge`
  (three or more sources in one node), `open_contradiction` (recorded, both
  sides still active), `ground_moved` (a subject was retired after the decision).
  They mark what is worth looking at, never what is wrong.
- **A blank `certainty` means unrated, not doubtful.** Rows an agent actually
  flagged sort above unrated ones however many signals those carry.
- Check `unrated_count` and `unattributed_count` before reporting a conclusion:
  three shaky rows out of four hundred unrated is not three out of four. Check
  `truncated` too — ask again rather than raising `max_results`.
- **It answers for one graph**, named in `graph`. For another, `use_graph` and
  ask again.
- It sees only decisions made since the journal existed. An older graph can be
  full of judgments `review` will never show.

### Interpreting _meta
Every tool response includes a `_meta` field:
- `nodes_returned`: how many nodes were found/affected
- `latency_ms`: how long the operation took
- `source_types`: breakdown by node type (topic, fact, inference)

Surface this naturally: "Found 5 relevant nodes (2 topics, 2 facts, 1 inference)."
