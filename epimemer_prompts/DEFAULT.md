## Memory System (Epimemer)

You have access to an epistemic memory system via MCP tools. It is
append-only: nothing is destroyed. Corrections and resolutions create new
versions and leave the history linked. Your job is to write fast and organize
deliberately.

Each tool's description says what its parameters take, what it returns and
what it refuses. This guide holds what no single tool can say: which tool a
situation calls for, the judgment a call leaves to you, and what the terms
mean.

Every tool response includes a `_meta` field: `nodes_returned` (how many
nodes were found or affected; decisions, on `review`), `latency_ms`, and
`source_types` (a breakdown by node type, filled by the tools that return
nodes). Surface it naturally: "Found 5 relevant nodes (2 topics, 2 facts, 1
inference)."

### Terminology: one word per concept {#terminology}

Use these words and no others, in what you write to the graph and in what you
say to the user.

- **Metacontext**: the world a claim is made in, such as real history, a novel,
  or one outlet's reporting. A node **stands in** a metacontext, or is
  **without a metacontext**.
- **Topic node**: a Topic. Either a paragraph-length statement of a theme, or a
  name that gathers nodes for retrieval.
- **Topic node created from a tag**: a topic node whose content is a tag name.
  It asserts nothing, so it stands in every metacontext it is used from.
- **Tag**: the string you pass in `tags=[...]`. It resolves, by name, to a
  topic node; the string itself is never a node.
- **Source node**, or **document node** where it is a `RawDocument`: the node a
  claim came from, reached by a `sourced_from` edge.
- **`tagged_with_topic` edge**: node to topic node, written when a tag is
  applied. `find_nodes` reads it and graph expansion follows it; it carries
  no evidential weight.
- **`has_metacontext` edge**: node to metacontext, written at ingest.
- **Judge**: the agent credited with a judgment, an agent id plus the digest of
  the self-description in force.
- **Decision journal**: the append-only record of every judgment, one row per
  decision, read back with `review`.
- **Fact**: a claim the material states.
- **Inference**: a claim you derived from facts, carrying its premises.

<!-- terminology-guard: off -->
**"Frame" and "hub" are not words this system uses.** A frame is a
metacontext; a hub is a topic node or a source node. The only hub here is the
visualisation hub, the process the dashboard connects to.
<!-- terminology-guard: on -->

### When to ingest (segment, then store_decomposition) {#ingest}

- After learning new information from the user or external sources, or when
  the user shares documents or knowledge you should remember.
- Call `segment`, read the passages it cut (section `passages`), extract
  topics, facts and inferences yourself, then call `store_decomposition`,
  whose parameters describe every field an entry can carry.
- **Leave `importance` out** unless you already know an entry is unusually
  consequential or unusually disposable: it is judged at reflect time, against
  the surrounding graph.
- A topic `description` left out here can be written later by
  `apply_reflection(enrichments=[...])`.
- **Write each `tag_descriptions` line to tell the tag apart from its
  neighbours**, two dated sessions for instance. In the response,
  `tags_created` counts the names this call minted and `tags_described` the
  existing topic nodes it filled in, so `tags_described: 0` beside new tags
  is the ordinary result.
- **Give every fact a `claim_kind` where the text lets you tell**, and omit it
  where it does not, which costs less than a guess.

### Cutting a document into passages (segment, resegment) {#passages}

Every node you decompose records the passage it came from, and that passage
is what a reader is later shown as the claim's context. A passage spanning two
subjects muddies every claim in it; one that cuts a sentence off from its
qualifier can lose a hedge, a date or a speaker.

- **Read the cut before you decompose.** `segment` cuts by rule, at blank
  lines by default, and its `doubts` say when the server suspects its own
  cut: `single_passage` (a long document left whole), `fragments` (mostly
  scraps), `outsized` (one passage far longer than the rest), `mid_sentence`
  (a cut inside a sentence).
- **`segment` and `resegment` say when to re-cut and how a list of anchors
  works.** One thing they leave out: a one-word heading such as `## Fees` is
  too short to be an anchor on its own, so carry it into the line below,
  newline included.
- **Where to cut.** Transcripts and dialogue at speaker turns, keeping a short
  question with its answer; contracts and specifications at clause or
  numbered-section boundaries, keeping a heading with the text under it;
  narrative at scene changes; chat logs and notes at changes of subject, not
  at message boundaries. A table or code block stays with the sentence that
  introduces it. A passage is the smallest stretch that stands on its own as
  the context of a claim.

### Which graph: say it on every call {#which-graph}

**`expected_graph` is required on every tool you call**, reads included. A
call without it, or naming a graph the server is not on, is refused before
anything runs, and the refusal names the graph that is active; so does
`list_graphs`.

**Do not paste the name out of a refusal.** Say which graph you *meant*, from
what the user asked for or from the `use_graph` you made: the check works only
because your expectation and the server's state are arrived at independently.
The active graph is not remembered across a client reconnect, so without the
check a session can come back in another graph and report a plausible answer
read from the wrong one.

Five tools take no `expected_graph`, because none of them works in a graph:
`list_graphs`, `use_graph`, `delete_graph`, `viz_status`, `guide`.

### Source nodes, topic nodes, and relations (all nodes and edges, not strings) {#nodes-and-relations}

Provenance and "aboutness" are **nodes and edges**, not string fields, so a
topic node, the publisher a source names included, can carry its own facts and
relate to siblings. None of them changes retrieval scope; metacontexts do.

- **Source**: pass `source`, `source_type` and `published_by` to `segment`. A
  publisher becomes, or reuses, an entity topic node, linked to the document
  by an attribution edge.
- **A tag becomes a topic node**, at document level or per node, and
  `tags_resolved_to` reports which one. The topic node joins the metacontext
  you are ingesting into. There are no `key=value` tags: a relationship
  dimension is an **edge** (`link(a, b, relation="spoken_by")`).
- **Relations are open vocabulary**, and `link` describes coining one and
  its `kind`. An engine edge type joins particular kinds of node, and `link`
  names two: `supports` and `subtopic_of`. A third, `extracted_under_topic`,
  runs from a fact to a topic node. When a pairing is refused, read the
  refusal, which names the types that do join your two nodes, rather than
  trying a second type.
- **Say what a label means here, once, with `describe_relation`.** The same
  word means different things in different graphs, and `link` hands the
  description back when you reuse the label. Reuse a label if it fits and coin
  a new one if the distinction is real.

Discovery and lookup:
- **`find_nodes`** answers provenance and tagging, where `search` answers
  similarity.
- **Pass `metacontexts` to `find_nodes`, `query_graph` and `topic_tree`**
  whenever the node you name is shared across worlds, which a topic node
  created from a tag often is: an unscoped walk from one hands you a novel's
  claims beside real ones.
- **`list_sources`** and **`list_relations`** are worth reading before
  filtering or coining a label. `list_relations` carries each label's
  `description`; an empty one means **nobody has said**, and is worth filling
  in once you learn what this graph uses the label for.

### When to search (search) {#search}

- Before answering questions that might benefit from prior context, or when
  the user asks "do you remember…" or refers to past conversations.
- **Pass exact strings you care about as `terms`**: a ticket id, an error
  code, a person's name, a filename, a version number. The `search`
  description says why, and what each field of a result means.
- When a search disappoints, each result's `provenance` says which arm
  brought it back.
- If you paraphrased an identifier out of a fact when you stored it, the
  passage in `segments` is the only thing that still holds it.
- Always read the `metacontexts` label on returned nodes, and the `review`
  label if present.
- **With `include_corroboration=true`, check the nodes behind each source
  before quoting the count.** Report an `adjacent_periods` entry as what was
  true next door: *the city was called Leningrad* is not a second witness to
  *the city is called Saint Petersburg*, it is the claim about the period
  before.
- Where two sources date a claim differently in `validity`, decide which
  source you trust or report the disagreement.

**Review labels.** `search` and `query_graph` results may carry a computed
`review` field. Treat a flagged node as provisional: mention the flag, and
hop to the related ids if useful.
- `superseded_candidate`: a newer fact may replace this one.
- `evidence_stale`: an inference whose supporting evidence was superseded;
  it may need re-deriving.
- `evidence_merged`: an inference whose premise absorbed another claim.
  Nothing was overturned, so this asks for a re-read, not a re-derivation;
  the ids name the phrasings that went away. Record the re-read with
  `apply_reflection(retained=[...])`, `covers` naming those ids.
- `contested`: an unresolved same-metacontext contradiction; do not trust it
  blindly.

### Temporal queries: three axes, not one {#temporal-queries}

**Two clocks run here, and confusing them is the commonest mistake.**
*Transaction time* is what this memory held and when. *Valid time* is when
the claim was true in the world. A fact created last week can be about 1924.

- **What was true then**: `search(..., valid_as_of=…)`. Valid time.
- **What the graph held then**: `graph_as_of(at)`. Transaction time, for
  "what did we believe on date X" or reproducing a past answer.
- **What changed across a span**: `query_changes(...)`. Transaction time. A
  node restored inside a window is returned like one born or retired there,
  each with its `events` and their timestamps, and a node that changed in
  several windows appears in each.

### Reviewing and reconciling knowledge {#reconciling}

New information can make existing knowledge outdated, contradicted, or true
in a different metacontext. Detection is cheap recall; **judgment is yours**:
similarity nominates candidates and never decides the relationship.

**Detect with `check_conflicts`** on the ids of facts you have just stored.
Its description lists the verdicts and the tool each one calls for:
`merge_facts`, `supersede_by`, `restore`, `record_contradiction`,
`record_variant`. Each of those describes what it writes and what it refuses.
Use `update` in place of `supersede_by` where you hold corrected content and
no node carries it yet.

**In doubt over a merge, keep both and record nothing**, since two nodes
saying one thing harm nothing. A `similarity` edge between two facts keeps
both and still counts the second source, so it belongs only where you are
sure they are one claim.

`record_contradiction` leaves both facts active and, where they share a
metacontext, `contested`.

**Warnings: what the graph tells you before you decide.** Some calls come
back with `warnings`: things the system knows and you cannot compute, each
with a `kind`, a one-sentence `message` and structured `detail`. Nothing
refuses on one, and it reaches you while you are still choosing what to
write, so **read it and write differently**. The kinds fall in two groups
that say opposite things:

- *This may be the wrong call*: `disjoint_premises`, `cross_metacontext` and
  `same_metacontext_variant`. Proceeding past one is journalled, whether or
  not the graph was set to show it to you, and read back with
  `review(mode="warning")`.
- *The call was right, and nothing is recorded against it*:
  `same_metacontext_contradiction`, where the conflict found wants a person;
  `description_not_written`, where a `tag_descriptions` entry named a tag the
  graph already describes and the stored description stands; and
  `tag_joined_topic`, where a tag filed under a topic node of the same name
  that was not created from a tag, so check its description is what the tag
  means.

`configure_warnings` sets what a graph does with them; ask the user before
changing it.

One kind is about the server and not the graph: `stale_server` comes on
every response, errors included, while the server runs an older Epimemer
than the one installed. Its message says what to do, and no setting turns it
off.

**Human-in-the-loop.**
- When a response returns `notify_user: true`, whichever warning it came
  from, surface it and ask the user. On `record_contradiction` that is a
  same-metacontext contradiction: do **not** silently pick a winner unless
  recency or source makes the call obvious.
- A cross-metacontext "conflict" is **not** a conflict. Don't interrupt the
  user; record a variant and say which metacontexts differ.
- When a metacontext-scoped answer looks thin and another metacontext may be
  relevant, *propose* consulting it and let the user approve. Always label
  borrowed knowledge with the metacontext it came from.

### When to reflect (reflect) {#reflect}

- After ingesting several documents, when asked to consolidate, or
  periodically in long sessions.
- **Every nomination wants an answer, and an unanswered one comes back on
  every reflect.** `reflect` describes the lists it returns, and the
  parameters of `apply_reflection` describe the answer each one takes.
  Where you take no other action, record the verdict that says you looked,
  and escalate anything you should not decide alone to the user.
- **`archival_candidates`** (section `cleanup`) has its own bound of 20, is
  never named in `truncated`, and refills on the next reflect.
- **`backup_suggested` is not yours to act on alone.** `store_decomposition`,
  `apply_reflection` and `graph_stats` report `stores_since_backup` against
  `backup_threshold`. Raise it with the user in one line, saying how many
  stores it has been, and call `backup_graph` only on a yes; otherwise the
  prompt returns on the next store. Reflecting does not clear it, and a
  backup does not clear the count of stores since the last reflect. Where
  `backup_graph` refuses because nothing is configured, the user's own route
  is the `epimemer graphs export` command.

**Pairs that look alike.**
- **To take back a `one_claim`, record `distinct` on the same pair**, and the
  corroboration count returns to what it would have been. You get one
  withdrawal: nothing re-asserts `one_claim` afterwards, and if they really
  are one claim, `merge_facts` is the call.
- **To take back a `distinct`, call `reopen`.** It retires the `assessed`
  edge, so the next `reflect` offers the pair again, carrying `reopened` with
  your reason and the date. Use it when later evidence makes a decline look
  wrong, not when you disagree on the same material.
- **`supersessions`** resolves flagged nodes in batch. Retiring the loser
  clears the supersession proposals pointing at it, and the winner's
  `contested` label.

**Topics.**
- **Rename a topic with `rename_topic`, not `update`**, which would retire
  the topic as a mistake or leave its material behind.
- **In an enrichment nomination, `changed_count` counts every change**, not
  only the twenty listed. A description given with a new tag counts as
  written, so that topic does not arrive as one nobody has described.

**Periods and premises.**
- **`boundary_proposals`** arise once
  `supersede_by(because="the_world_changed")` has recorded a succession, and
  run in both directions: the successor's start date as the predecessor's
  end, or the predecessor's end as the successor's start. To accept one, copy
  `node.id` from the proposal as `node_id` and the other four fields as they
  are.
- **`unsound_inferences`**: re-derive the inference, narrow it to a period it
  can support, or retire it with `supersede_by(because="it_was_wrong")`.

### Cleanup (archival_candidates, then apply_reflection archivals) {#cleanup}

Trivial knowledge is the counterpart to *wrong* knowledge, and the same loop
handles it: nomination proposes, you judge, the **user approves**. The
`archivals`, `retained`, `deferred` and `judgments` parameters of
`apply_reflection` describe each answer.

- Each nominee carries a `reason`: `evidence_stale` (an inference whose basis
  changed), `never_retrieved` (an active node, other than an inference, that
  no search has ever returned, nothing links to, whose importance is not
  above the ceiling, and no deferral is holding back), and `stale_judgment`
  (a node held above the importance ceiling whose last judgment nobody has
  revisited in months).
- Judge each one *with graph context*: "Error message X" matters while the
  bug is open and stops mattering once it is fixed. If a nominee turns out to
  matter more than its importance says, judge it up.
- **Ask the user before archiving.** Surface the list, say why each was
  nominated, and pass only the approved ids. Keep the `archive_data` the call
  returns. Archived nodes leave search and every active query.
- **A deferred nominee that comes back** can be deferred again if what you
  said still holds; archive or keep it once something settles the question.

### Timelines (when things happened) {#timelines}

A timeline is about *when the content happened*, not when the graph learned
it. Each tool here describes itself; this is which one to reach for.

- **Build one**: `create_timeline`, then `add_timepoint` and
  `create_timelink`. Prefer a vague timepoint to a guessed date: "during the
  Renaissance" must not become 1500-01-01.
- **Give a fictional or historical timeline its own "now"**:
  `reference_time`, at `create_timeline` or with `set_reference_time`.
- **Place a point nobody dated**: `order_timepoints`, read back with
  `query_timeline`.
- **Settle a dispute about order**: `reflect` nominates it in
  `temporal_contradictions`, and `resolve_temporal_contradiction` answers.
  `merge_timepoints` is the reverse of its `not_the_same_event`: two marks
  for one event folded into one. `search` marks a fact dated to a contested
  point with `date_contested`, separate from `contested` on the claim itself.
- **Record something that repeats**: `add_recurrence`, with
  `record_recurrence_exception` for an occurrence that broke the pattern and
  `end_recurrence` for a rule that stopped applying.

### Metacontexts {#metacontexts}

- **Absence names no metacontext.** A node without a metacontext is one
  nobody said anything about: never compared, never merged, returned by no
  scoped search. It does **not** mean base reality.
  `graph_stats.nodes_without_metacontext` counts them, and a person assigns
  them one with the `epimemer metacontexts declare` CLI command.
- **A metacontext id that does not resolve in this graph is refused**, at
  ingest and in the `metacontexts` list of a read. The refusal lists the
  metacontexts that do exist (the first ten, and a count of the rest), which
  is the only listing there is.
- **A metacontext need not be fictional**, and **none inherits another**:
  *"what Milanese people knew by 1860"* and *"what Londoners knew by 1860"*
  are two metacontexts over the same real past. *"Milan is in Lombardy"*
  belongs in the real-world metacontext, *"Milanese merchants believed the
  pass was closed"* in the perspective one, and a reader who wants both names
  both.
- **A metacontext can build on one other**: `based_on` on
  `create_metacontext`, or `set_metacontext_base`.
- A topic merge re-states the survivor's metacontext under your judge.
- **Two perspectives disagreeing about one world are not nominated as a
  contradiction**, since they share no metacontext, and `record_variant` is
  the usual record. Where the disagreement *is* the finding, call
  `record_contradiction` directly: the response carries `same_metacontext:
  false` and a `cross_metacontext` warning, the call is journalled as going
  ahead against it, and neither fact becomes `contested`.
- Never present a claim from a fictional or otherwise non-real metacontext as
  fact; always surface the `metacontexts` label, and give new metacontexts
  clear, descriptive names.

### Saying which judge you are (claim_agent) {#judges}

`claim_agent` describes the claim itself: proposing a name, what comes back,
and how to describe yourself. Around it:

- **A refusal is the prompt, not an error to work around.** Put its message
  to the user and let them decide. A retired judge is refused with a message
  saying when; its decisions remain and `review` still answers for it, and
  only the user can bring it back.
- If two judges here are obviously the same one, tell the user; only they can
  consolidate them.
- **Approval is per graph, so claim again after `use_graph`.** Where the new
  graph does not approve your judge, the switch unbinds it and names it in
  `judge_cleared`. A token whose judge the new graph does not approve is not
  refused unless the graph requires a judge: its writes are recorded with no
  judge.
- **Claim again after a reconnect too**; your `judge_token` does not survive
  one. Where the conversation, the client and its version, and the model are
  the ones the user already confirmed this judge in (the seat), the claim
  binds without asking and says it was carried over; where it asks,
  `asked_because` says why.
- **A write refused because the seat changed means claim again**, and the
  user will be asked. The message names what changed; do not retry the write
  before claiming.
- A `judge_token` the connection never issued is refused.
- **If a graph requires a judge**, a write that takes a `judge_token` is
  refused while no judge is claimed, and the message names `claim_agent`.
  Put it to the user: only they can approve a judge or turn the requirement
  off.

### Reviewing what was decided (review, apply_review) {#review}

- Use `review` when the user asks what has been decided, before trusting a
  count you did not produce, or as a pass over your own session's work. Read
  the top of the list and stop when it stops repaying the attention.
- **`difficulty_signals` says why a row is near the top**, and none is a
  verdict.
- Check `unrated_count` and `unattributed_count` before reporting a
  conclusion: three shaky rows out of four hundred unrated is not three out
  of four.
- For one judge or one window prefer `by_agent` or `since` to `all`: `all`
  with a forgotten `agent_id` returns everything, which reads as an answer.
- **`by_agent` takes a handle**: a name, a key, or a key that judge used to
  be recorded under. The `judge` block says what it resolved to, and
  `unknown_here` means it named nobody, which an empty page alone would not
  tell you.
- In `elsewhere`, `counted_with` names the filters the counts used, zero
  means nothing is there, and `unreadable` means that graph was not counted.
- It sees only decisions made since the journal existed, so an older graph
  can hold judgments `review` never shows.
- **If you check a decision, say so with `apply_review`**, the only call that
  records a confirmation or a dissent.

### Fixing a judgment without retiring the claim {#corrections}

Each of these corrects something said *about* a claim that is itself fine, so
do not reach for `update` or `supersede_by`.

- **A wrong `claim_kind`, `confidence` or `confidence_basis`**: `rejudge`.
- **A wrong metacontext**: `reassign_metacontext`.
- **A wrong period**: `correct_interval`.
- **A wrong importance**: `judge_importance` on one node, or
  `apply_reflection(judgments=[...])` during a reflect. After ingest,
  importance moves only when someone judges it. *Use* is recorded separately:
  every node a search returns gets `retrieved_at` stamped.
