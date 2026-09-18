# Changelog

All notable changes to this project are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/).

## [0.2.5] — 2026-09-18

**A wrong decline is no longer permanent: `reopen` puts the question back.**
Recording a verdict stops a pair or a node being nominated again, which is what
keeps `reflect` worth running twice, and until now none of the three
suppressions could be taken back. A pair judged *distinct* in error never came
back, however much later evidence said it should. `reopen` withdraws one
suppression and does nothing else: it never records the opposite of the earlier
verdict and never says the earlier judge was wrong, it only makes `reflect`
offer the question again. One tool covers all three layers, and which one it is
follows from the target: two node ids for a pair judged *distinct*, two relation
label names for a label pair, one node id for a node a `retained` verdict kept.

- Nothing is deleted. The `assessed` edge is retired the way a timeline link is,
  stamped with when it stopped counting and by whom; the verdict table takes a
  new row saying `reopened` rather than losing one; the journal only grows. A
  pair reopened and judged *distinct* a second time carries both rounds, and the
  second verdict suppresses it again.
- The nomination says it was reopened, with the reason and the date, so the next
  judge sees the history instead of re-deriving it. It reaches the similar-pair,
  contradiction, recurrence, inference-merge, archival and relation-label
  nominations.
- Refused when nothing is suppressed for the target, naming what it looked for,
  because *there was nothing to undo* must not read like *done*. Refused too
  when the pair carries a standing `similarity`, `contradiction` or `variant_of`
  edge: those assert something about the pair rather than declining it, so
  withdrawing one is a verdict. `apply_reflection(similarities=[...])` with
  *distinct* is still what withdraws a standing *one_claim*.
- Every reopen is journalled as a `reopened` decision carrying the reason, and
  reaches the live log as one act naming the judge. The row lands in the same
  transaction as the change, because here the row is the act: a reopen whose row
  was lost would put a question back on the worklist with nothing saying it had
  ever been declined.
- The refusal an agent gets for repeating a verdict it has already recorded now
  points at `reopen` rather than at the open question it used to name.

**A warning the agent was given now reaches the dashboard, including one the
agent was not given.** Until now a warning was computed inside a tool call,
handed to the agent, journalled where it objected to the call, and that was the
end of it: the live log showed the act and nothing of what the agent had been
told about it, and a graph with warnings muted showed nothing anywhere. Each
warning is now published as its own event and becomes a `warned` row in the log
beside the act it accompanied, saying which tool raised it, what it said word
for word, and whether the agent was asked to raise it with the user. Every
warning a call computed is published, muted or not: a muted one draws dimmer
and says *not shown to the agent* in its tooltip, because the dashboard is where
a person looks at what the agent was not told. `warned` joins the verb filter,
so warnings can be read alone or left out, and clicking one highlights the nodes
it was about. The tool responses are untouched, and a test asserts it: the event
reports what the agent saw and never decides it.

- A read-only **Warnings on <graph>** panel sits beside the reflect badge,
  showing the mute in words and, per kind, the action in force and whether it is
  inherited from the process default or set on this graph. It is a view rather
  than a control on purpose: a write from the browser would be the first write
  into a graph with no author, and every change today is recorded against a
  session and a judge. `configure_warnings` is still how a setting changes.
- `configure_warnings` and the panel are built from one function, so the agent
  and a watching person cannot be shown two different answers about what a graph
  is set to.
- A graph that muted its warnings is no longer shown them by `reflect`. The
  merge candidates it nominates carried their warnings whatever the graph was
  set to, so one run could answer *no warnings* from `record_contradiction` and
  *here is a warning* from `reflect`. `reflect` now reads the same setting as
  every other tool: a muted kind is dropped from the candidate, the candidate
  itself still arrives with everything there is to act on, and a kind set to
  `flag` by name survives the mute here as it does everywhere. The dashboard
  still hears about every one, with *not shown to the agent* on the ones that
  were dropped.

**The timeline panel draws what the order, the disputes and the rules say.**
Until now the dashboard drew dates and nothing else: a point the graph could
place was still a chip in a tray, a disputed order looked exactly like an
agreed one, and a recurrence rule was invisible. The snapshot now carries what
`query_timeline` answers, computed by the same functions, so the panel and the
tool cannot disagree about a point. A point with derived bounds is drawn as a
hatched band spanning them, its label written on it word for word, fading out
on the side where the bound is unknown. A contested point keeps its place and
gains a red zigzag that says the order is disputed rather than that the date is
wrong, since the date a source gave is kept either way. A rule's occurrences are
beads on a dotted spine, the dots meaning nothing is asserted between one and
the next, with a materialised occurrence drawn as the ordinary point it became
and a moved one drawn where it moved to. Occurrences are computed rather than
stored, so a snapshot picks its own window: the span of the timeline's dated
points widened by one period of the rule on each side, under the same per-rule
cap. What is left in the tray is what the panel has nowhere honest to put:
points nothing constrains, contested points with no date, and points on another
timeline's clock.

**A source can now say what order things came in, and a point nobody dated gets
a place in time.** Until now a timeline could hold "during the Renaissance" but
had nowhere to put it: the point sat in a tray with no position on the axis, and
nothing could say it came after one thing and before another. `order_timepoints`
records that a source asserts an order between two points, with the source named
and a `basis` saying whether the source stated it in words or a judge read it off
tense and context. That distinction matters because narrative order is not
chronological order: a document that tells of the fire and then the flood has
stated nothing about which came first. Nothing lets the graph assert an order no
source stated, and extraction still proposes dates only.

- `query_timeline` gained three ordering modes, `before`, `after` and `between`,
  which walk the stated order rather than the dates, and `between` answers with
  the points that both come after the first and before the second. A point with
  no date of its own now comes back with the `earliest` and `latest` the order
  derived for it, computed on read and never written onto the record, so
  retiring a constraint later takes its bound away with it. `basis="stated"`
  builds the answer from what sources said in words alone, and
  `include_contested=False` leaves out the points whose order is in dispute.
- **Two orderings that cannot both hold are kept, both of them.** A loop in the
  order, or a point squeezed until everything before it ends after everything
  after it begins, opens a temporal contradiction naming the steps, the
  constraints and the sources behind them. The write still succeeds: the graph
  holds the disagreement rather than deciding it. Dates take part in the check,
  so a source claiming the flood came before a fire dated earlier is caught, and
  adding a dated point re-runs the check because a new date can close a loop that
  the constraints alone did not.
- `resolve_temporal_contradiction` answers one with three verdicts. Retire the
  constraint that should not be believed, and every other constraint in the
  dispute returns to live unless a second contradiction still holds it. Decide
  the two sources are each right about a different occurrence, which splits the
  point in two, moves the constraints named and moves the facts named with it.
  Or hold it, when nothing on hand settles the disagreement, which stops the
  nomination until a new constraint or a new dated point touching it arrives.
  Nothing is deleted and no stored constraint is edited: a constraint that moves
  is retired and a fresh one written, linked to it.
- `merge_timepoints` is the reverse of the split, for the two marks extraction
  makes when two documents call one event by different names. Every constraint
  and every fact dated to the retired point moves to the survivor, since a merge
  asserts the two are one. It refuses while a source's constraint orders the two
  against each other, because a source that ordered them said they are not one
  moment.
- **An edge can now be retired.** A `TIMELINK` a split or a merge moves is kept
  with when it stopped counting, who decided, and the link written in its place,
  and retrieval, graph queries, `find_nodes` and the dashboard all stop following
  it. There is still no general tool for detaching a fact from its date: a fact's
  date is part of what it claims, so moving one takes a decision.
- `reflect` nominates open disputes about order in `temporal_contradictions`,
  beside the contradictions between claims, and `search` marks a result dated to
  a contested point with `date_contested`. That is deliberately a separate flag
  from `contested`: doubt about when the coronation happened must never be read
  as doubt about the treasury being empty at it.
- Graph bundles carry the new lists without a format change, since adding fields
  to an existing section is not one.

**A timeline can now hold what recurs, as a rule rather than as a row of dates.**
`add_recurrence` records that something happens over and over, either by
arithmetic (an anchor and a period, which needs no calendar and so works on an
invented timeline as well as a real one) or by the calendar (an RFC 5545 `rrule`,
for "the second Tuesday of every month"). The occurrences are worked out when
somebody asks and are never stored, because a stored expansion is a second thing
that can disagree with the rule that made it. Until now the only way to record a
weekly service was to write every service by hand, or to leave the pattern out of
the graph entirely.

- An occurrence is named by the start the rule gives it, and nothing counts
  occurrences. A number would need a walk from the rule's beginning, cheap near
  it and not cheap a century later, so a number that had to be capped would be
  missing exactly where most queries land.
- `query_timeline` computes occurrences into its answer, marked with the rule
  they came from, the `occurrence_start` that names them, and the point that
  materialised them where one has. A range query enumerates each rule inside the
  window, a nearest query gives the nearest occurrence of each, and a query with
  no window gives the next one after the timeline's own present: a timeline
  anchored in May 1897 answers in 1897, and `next_after` measures from a moment
  you name instead. This is the first thing `reference_time` decides rather than
  merely reports.
- **There is a cap, per rule per call.** One plausible query over a daily rule
  would otherwise enumerate without end; when the cap fires the answer says
  `truncated` for that rule and names the window it did cover. Finding the
  nearest occurrence enumerates nothing at all: one division for a periodic rule,
  one step each way for a calendar one.
- `add_timepoint` gained `recurrence_id` with `occurrence_start` in place of
  dates, which turns one occurrence into an ordinary timepoint that can be
  linked, ordered and disputed like any other, and takes part in the ordering
  checks the same way. It is idempotent on the pair, so asking twice gives the
  same point. This is what lets a fact attach at either of two levels: to the
  rule, where it holds at every occurrence, or to one occurrence, where it is
  about that one.
- `create_timelink` gained `recurrence_id`, the other of those two levels, for a
  fact that holds at every occurrence: "market day is held in the square" is
  about the rule rather than about any one market. Exactly one of
  `timepoint_id` and `recurrence_id` is given, and the response says which level
  the fact now attaches at. A rule-level link holds no place in the order, so
  nothing can contest its date, a split or a merge of points leaves it where it
  is, and the dashboard carries it without drawing it on a mark.
- `query_timeline` now names the facts attached to every point and every
  occurrence it returns. `linked` is what was dated to that point, and on an
  occurrence `linked_via_rule` is what was linked to the rule, so a fact that
  holds at every occurrence is visible on each of them instead of only on the
  rule. The two lists stay apart, because only one of them says anything about
  that one date.
- `record_recurrence_exception` records an occurrence that did not happen or
  happened at another time. Without it a rule that held except for one year would
  force a choice between recording something false and abandoning the rule, and
  the second is worse because the recurrence is then lost. A moved occurrence
  keeps the start the rule gave it as its identity, so materialising it before
  and after the move gives one point rather than two.
- `end_recurrence` says when a rule stopped applying and never retires it: a
  recurrence that was true does not stop having been true. Each end date is
  appended with its reason, and the most recent is in force, so "we thought it
  stopped in 1990, then learned it was 1993" reads as the correction it is.
- `python-dateutil` is now a dependency rather than something three optional
  extras happened to pull in, so calendar rules work on a plain install instead
  of only on a machine that had one of those extras.

**The dashboard's log now shows the six timeline decisions**, which used to pass
it in silence: ordering points, answering a dispute about order, merging two
points, and the three recurrence decisions. Each reads as one line naming the
timeline, the judge, and what it wrote. A split that moves a fact from one date
to another now also lands as a single transaction, timeline and links together,
where before a failure part-way through could leave the record saying the fact
had moved while its link still named the old date. Creating a timeline, setting
its present, adding a point and linking a fact to one stay out of the log, since
they record where something sits rather than deciding anything about it.

`store_decomposition` now reports `tags_created`, how many tags this call
minted, beside `tags_described`, which counts only existing tags that had no
description and got one. A call creating three tags used to report
`tags_described: 0` and nothing else, which read as the descriptions having been
dropped; it now reports `tags_created: 3` beside it.

## [0.2.4] — 2026-09-16

**A timepoint now says what kind of thing it is, and a range query answers in
order.** Every point a timeline hands back carries a `kind`: an `instant` is a
date, an `interval` is a date and an end, and a `vague` point is a label such as
"during the Terror" that nothing has placed on the axis yet. The kind is read
off the dates whenever it is asked for rather than stored beside them, so it
cannot go stale, a caller passing one is refused, and records written before
this existed read back as the kind their fields imply with no migration. Two
shapes that used to be stored silently are now refused in plain words: an `end`
with no `start`, since the start is where the mark goes, and a point with
neither a date nor a label, which is a mark with nothing written on it. A range
query used to return intervals that began before the window after the points
inside it, so the war that was already running arrived after the treaty that
ended it; results now come back in order of start, and the search back for
still-running intervals stops at the longest interval on the timeline instead of
testing every point in the earlier history.

**A backup destination can now be told how many bundles to hold.** Set
`EPIMEMER_BACKUP_KEEP` to a count and `backup_graph` removes the older bundles
of the graph it just wrote, keeping the newest that many, and reports which went
and how many are left. Until now every backup added a dated file and nothing
ever removed one, so a graph backed up on prompt filled a folder or a bucket
with near-identical snapshots that the user had to prune by hand. Leaving the
variable unset keeps everything, which stays the default: deleting a backup has
to be asked for. Bundles are ordered by the date in the filename rather than by
modification time, which an object store stamps at upload and a copied folder
loses; only files of the exact shape `<graph>-<date>.epimemer.tar.gz` are
matched, so a graph named `notes` never touches the bundles of `notes-archive`,
and a `--plain` directory is left alone. The prune runs after the write returns,
so a failed backup removes nothing.

**The server now carries its own guidance.** The per-call rules
(`epimemer_prompts/RULES.md`, about 2 KB) are its MCP `instructions` string,
so every client has them from connect; the full guide (`DEFAULT.md`) is the
MCP prompt `guide`, `/mcp__epimemer__guide` in Claude Code, pulled before
nontrivial memory work. Until now the guide reached an agent only if someone
pasted it into that agent's instructions, and the server said five sentences
about itself.

## [0.2.3] — 2026-09-13

**A judge can be taken out of use, brought back, or removed if it never judged
anything.** Until now a judge, once confirmed, could only be renamed or
consolidated, so one nobody had claimed for weeks sat in the `claim_agent`
picker beside the live ones, one keystroke from being selected by mistake.

- `epimemer agents retire <handle>` takes a judge out of use. Its record, name,
  description history, approval and every decision stay exactly as they are,
  and `review(mode="by_agent")` still answers for it; what changes is that
  `claim_agent` refuses it, whether reached by name, by key, by a former key
  or through the approved list, and the picker stops offering it. Sessions already
  bound continue until they reconnect.
- `epimemer agents reinstate <handle>` brings one back, and so does the judge
  picker's new *A retired judge…* entry, shown only where the graph has one. It
  opens a second picker over the retired judges, reinstates the chosen one, and
  returns to the main picker with it on the list: choosing it is a separate
  gesture, so a deliberate removal is never undone by one keystroke.
- `epimemer agents delete <handle>` removes a judge that has judged nothing.
  The command scans this graph's journal rows, nodes, edges and relation
  records for the judge's keys, prints the result, and asks before acting
  (`--yes` skips the prompt). Any count above zero is a refusal that states the
  counts and points at `retire` instead, because `judged_by` holds a key and
  the key has to keep resolving to a name for as long as anything carries it.
- `epimemer agents list` shows retired judges under a heading of their own,
  with the date each was put away.
- `Agent.retirements` records the trail as an append-only list of
  `RetirementEpisode`, the same shape the descriptions use: a scalar plus a
  timestamp cannot say *was out of use, came back, and is out of use again*. A
  record written before this reads as an empty trail, which is a serving judge;
  no schema step.
- `judge_usage(agent_ids)` and `delete_agent(agent_id)` on the storage
  protocol, implemented on both backends. `judge_usage` counts by aggregate
  rather than materialising the graph, and `delete_agent` is the one hard
  delete on the protocol, gated by the scan above rather than by the backend.
- None of the three is reachable from an MCP tool, for the reason renaming is
  not: a handle an agent could retire is a handle an agent could use to take a
  rival judge off the roster.

**A graph can be written out as a bundle and rebuilt from one.** Nothing
exported a graph before this: the in-memory backend persists nothing and a
SurrealDB graph lives on a volume inside the user's container runtime, so one
deleted machine was the whole graph. There was also no way to move a graph
between backends, and no way to re-embed one after changing the embedding model.

- `epimemer graphs export <graph> --to <path>` writes the graph as
  `<graph>-<YYYY-MM-DD>.epimemer.tar.gz`, one JSON Lines file per section plus a
  manifest. `--to` takes a local path, a `gs://` URL or an `s3://` URL, the last
  two through the optional `epimemer[gcs]` and `epimemer[s3]` extras;
  credentials come from each provider's own standard chain and Epimemer reads
  none. `--plain` writes an uncompressed directory for reading or diffing, and
  import accepts either form.
- `epimemer graphs import <bundle> --graph <name>` rebuilds it as a **new**
  graph. It refuses a name that exists, with no override: replacing a graph is
  `delete_graph` and then import, two acts the user already has. A failure drops
  the partial graph and says why, so a retry is never blocked by a half-written
  one.
- `epimemer graphs verify <bundle>` imports into a scratch graph, exports it
  again, compares the files, and drops the scratch graph. That comparison is the
  definition of lossless here, run on the user's own data.
- **Embeddings are not in the bundle**, and import re-embeds every node with the
  provider the importing server is configured with. Vectors are the one derived
  part of a graph, the part that does not compress, and cheap to recompute. It
  also makes *export, change `EPIMEMER_EMBEDDING_MODEL_ID`, import* the model
  migration path, which did not otherwise exist. The manifest records what the
  graph was embedded with, so a restore onto a different model says so.
- `backup_graph` is a new MCP tool. It writes a bundle to
  `EPIMEMER_BACKUP_DESTINATION` and takes **no path**, so an agent can act on a
  backup prompt without choosing where a graph goes; with nothing configured it
  refuses and names the variable. It needs no judge, because a backup asserts
  nothing about the graph.
- `store_decomposition` and `apply_reflection` now return `stores_since_backup`,
  `backup_threshold` and `backup_suggested` beside the reflect keys, and
  `graph_stats` reports them too. The threshold follows the reflect threshold's
  pattern: `EPIMEMER_BACKUP_THRESHOLD` (default 50) as the process default, a
  per-graph override set by the new `configure_backup` tool, and one pure
  `resolve_backup_threshold`. The two counters are separate because they are
  zeroed by different acts: reflecting clears one, a successful backup the other.
- On the storage protocol, implemented on both backends: `query_documents`,
  `query_segments` and `query_edges`, which are the whole-section reads export
  needs; `write_verbatim_tx`, the one write that stores records exactly as given
  rather than deriving ids and timestamps the way `store_relation_label`,
  `record_decision` and the counters do; `set_reflect_counter`; and the six
  backup-bookkeeping methods.
- `fsspec` joins the runtime dependencies. It is pure Python with no
  dependencies of its own, and it is what makes a local path and a cloud URL one
  code path.

## [0.2.2] — 2026-09-12

**A new tag needs a description, and the call is refused without one.** This
changes what every caller of `store_decomposition` that creates tags has to
send: a `tag_descriptions` dict with one line per new tag. Existing tags need
nothing. The rest of this release is the other half of the same idea, that
every reflect nomination has a recordable answer, so nothing is asked twice
until something changes.

- `tag_descriptions` is required on `store_decomposition` for every tag the
  call would create, and the call is refused without it, naming the tags that
  need a line. A tag with no description is embedded on its own characters, so
  two dated session tags score 0.99 against each other and every reflect asks
  for them to be merged. An entry for a tag the graph already describes is not
  written and comes back in `warnings`; an entry for an existing tag with no
  description is written, so a graph backfills as it is used.
- Reflect nominates a topic's description for review when the material under it
  changed, rather than whenever the material is long. The nomination carries
  the delta: `since`, the nodes created, archived or superseded since then
  (newest first, at most twenty, each saying which and when), `changed_count`
  and `material_count`. A topic nobody has described keeps the old length
  ratio, and arrives with `since: null` and a sample instead.
- `apply_reflection(descriptions_confirmed=[topic_id])` records that a
  description was read against the change and still fits. The answer opposite
  to an enrichment, and the one that had no writer, so an unanswered nomination
  came back for ever.
- `DecisionKind.DESCRIPTION_REVIEW` journals a confirmation, one row per batch,
  the way `RETENTION` stands beside `ARCHIVAL`. An `ENRICHMENT` row with the
  same text before and after would misreport it.
- `description_reviewed_at` on `Topic` records when its description was last
  written or confirmed. Absent on existing rows, where it reads `None` and
  means never reviewed; no schema step.
- A synthesised parent whose children are all topic nodes created from tags
  stands in the union of their metacontexts, the way an all-tag merge survivor
  does, instead of being refused when the children are used from different
  worlds. `combined_metacontext_set` states both answers once.
- `apply_reflection(splits_declined=[topic_id])` records that a split
  nomination was read and the topic is one topic, journaled as
  `DecisionKind.SPLIT_DECLINED`. It stamps `description_reviewed_at`, and the
  split scan skips a topic whose material has not moved since somebody last
  stood behind it, whether by a description written or confirmed or a split
  declined. Before this the same topics came back on every reflect.
- The material walk under a topic follows `tagged_with_topic` as well as
  `extracted_under_topic` and `abstracts`, so reflect can reach a topic node
  created from a tag at all. It never could before, which is why the tags on a
  real graph had stayed undescribed.

## [0.2.1] — 2026-09-10

**`link` refuses an edge whose ends are the wrong kinds of node.** Until now it
checked that the edge type was a real one and that both nodes existed, and
nothing else, so `link(fact, topic, edge_type="supports")` was written even
though `supports` means a fact backing an inference. So were a `subtopic_of`
between two facts and an `extracted_under_topic` pointing at an inference. The
graph then held edges whose type said one thing and whose ends said another,
and every tool reading them had to re-check what it had just been handed.

Each engine edge type now declares which kinds of node it joins, in a table
beside the list of edge types so the two cannot come apart. A pairing outside
it is refused outright rather than recorded with an override, because it is a
category error against what the type means rather than a judgment anyone could
defend. The refusal names the pairs that type does join, and any type that does
join the two nodes you named: asking for `supports` from a fact to a topic node
now comes back suggesting `extracted_under_topic` and `tagged_with_topic`. A
relationship the engine does not define is what `relation` was always for, and
that stays open: the agent coins the word and the word says what it joins.

The same question is now asked by every other call that takes both ends from
the caller : `supersede_by`, `record_contradiction`, `record_variant`, and the
similarity verdicts and synthesised parents inside `apply_reflection`, each
refusing in the way it already refuses everything else. Existing edges are left
alone; nothing rewrites what is already stored.

**A topic node created from a tag now stands in every metacontext it is used
from.** It used to stand in none, on the grounds that a name asserts nothing and
so has no world to be about. The first half is right and the conclusion did not
follow: absence means *nobody spoke for this node* everywhere else here, so
nothing compared a tag, nothing merged it, and no scoped read returned it. A
search scoped to `the-real` dropped `design-decisions` along with every
`tagged_with_topic` edge that reached it, which on one real graph meant hiding
the most-used topic nodes in the whole thing. `graph_stats.nodes_without_metacontext`
had no steady state meaning *done* either: `epimemer metacontexts declare`
stamped every tag it found, and the next ingest minted another one standing
nowhere.

A tag now takes the union of the metacontexts of the nodes tagged with it, which
is the worst answer available for a claim and the right one for a name:
`dev-session-2026-09-08` standing in both a novel's world and `the-real` says
only that the name was used from both. `store_decomposition` adds its
metacontext to each tag it resolves, once per tag per call and only where the
topic node is not already there, and an all-tag merge leaves the survivor
standing where all its sources stood. Scoped reads return the tags used from the
scope, `topic_tree` labels them like any other entry, and the count of nodes
without a metacontext reaches zero and stays there.

Schema version 5 stamps the tags already in a graph when it is opened, from the
metacontexts their tagged nodes stand in. It adds edges and removes none, so a
tag a declaration sweep stamped keeps what it was given and gains whatever else
its uses say; a tag that tags nothing is left alone. That migration is one-way:
a graph opened by this version holds the new edges, and nothing puts them back.

**Enrichment describes a topic instead of renaming it.** A topic's content is
the name the graph joins on: `store_decomposition` resolves a tag to a topic
node by it, and `find_nodes(tagged_with_topic=...)` resolves the same name back.
Enrichment used to replace that content and retire the original, so an enriched
tag split in two: documents tagged before it stayed on the first node,
documents tagged after minted a second, and neither knew about the other. It now
writes prose into the topic's `description` and leaves the name alone. The node
keeps its id, its status and every edge it holds, so there is nothing left for a
guard to refuse and no reason to treat a tag as a special case.

**This is a breaking change for callers of `apply_reflection`.** An enrichment
entry is `{topic_id, description}`; `new_content` is refused, and the refusal
says what to send instead. Improving a description keeps the wording it replaced
in the topic's `description_history`, so nothing is lost by writing a better one.

### Added

- `store_decomposition` takes an optional `description` on a topic entry, so a
  topic arrives described rather than waiting for a reflect to notice it. Facts
  and inferences refuse one: a claim's wording is the claim.
- `epimemer tags repair` puts back the names the old enrichment overwrote. It is
  for graphs written before this release, prints each sentence beside the name
  it would restore, and asks before touching anything. The repair is in place:
  the node keeps its id and its edges, the sentence becomes its description, and
  the retired original is left as it is. Idempotent, so a second run finds
  nothing. Like the other write commands here it refuses an embedded store, and
  an embedded graph has nothing to repair in any case.

### Changed

- A topic node created from a tag is embedded on its name **and** its
  description where it has one. A bare tag name says nothing about what the
  topic covers: on one real graph every active tag pair above 0.75 cosine was
  also above the 0.80 nomination bar, with `dev-session-<date>` pairs at 0.97 to
  0.99 outscoring `claim-kind` against `claim_kind` at 0.92, and two issue-number
  tags scored 0.789 bare against 0.253 with descriptions. Every other node still
  embeds on its content alone, statement topics included: their content is
  already the prose a description would restate, so joining one there would only
  move a stored topic away from the same topic arriving undescribed at ingest.
- `topic_tree` previews a topic's description where it has one.
- The visualiser's node detail shows a topic's description beneath its content.

## [0.2.0] — 2026-09-08

**"Frame" and "hub" are gone as words.** Both read as general graph
vocabulary: a frame sounds like any context you care to name, and a hub like
any well-connected node. So users, agents and the tool descriptions themselves
drifted into using them loosely, and two precise concepts blurred into two
vague ones. A metacontext is the world a claim is made in, and a topic node or
a source node is a node other nodes point at. Warning about the looser reading
would have left both words in play; removing them is what actually ends it.

The graph rewrites its own stored strings when 0.2.0 opens it, so no manual
step is needed. That migration is one-way: a graph opened by 0.2.0 holds the
new spellings and should not be reopened by 0.1.x, which has no step that puts
them back.

**One metacontext could bleed into another through a shared topic node.** A
topic node created from a tag asserts nothing, so it stands in no metacontext,
and every node tagged with that name points at the same one whatever world it
was claimed in. A fact from a novel and a fact about real history therefore sit
one hop apart, through a node neither of them is a claim about, and the read
tools walked straight across it. A search scoped to `the-real` filtered its
node list correctly and then returned edges naming the fiction nodes it had
just removed, plus the fiction document's own raw passages. `query_graph`
returned every neighbour whatever world it belonged to, and did not even say
which world that was.

### Changed

- The MCP tool `reframe` is now `reassign_metacontext`, with the same
  arguments and the same behaviour.
- Response keys: `same_frame` → `same_metacontext`, `nodes_without_frame` →
  `nodes_without_metacontext` (in `graph_stats`), `already_framed` →
  `already_in_metacontext`, `frames_now` → `metacontexts_now`.
- Advisory kinds, as they appear in a response's `warnings` and as keys of the
  policy `configure_warnings` stores: `cross_frame` → `cross_metacontext`,
  `same_frame_variant` → `same_metacontext_variant`,
  `same_frame_contradiction` → `same_metacontext_contradiction`.
- Decision journal kinds, as `review` and `query_decisions` return them:
  `frame_declaration` → `metacontext_declaration`, `reframe` →
  `metacontext_reassignment`. A journal row's `frame` field is now
  `metacontext`.
- The CLI command `epimemer frames declare --frame` is now
  `epimemer metacontexts declare --metacontext`.
- The storage protocol method `count_nodes_without_frame` is now
  `count_nodes_without_metacontext`, on both backends and the instrumented
  wrapper. `epimemer/pipelines/frames.py` is now
  `epimemer/pipelines/metacontexts.py`, and the names in it move the same way:
  `declare_frames` → `declare_metacontext`, `shared_frame_set` →
  `shared_metacontext_set`, `frame_edges` → `metacontext_edges`,
  `is_tag_topic` → `created_from_tag`, and so on through the package.
- A tag is the string passed in `tags=[...]`; the node it resolves to is a
  topic node created from that tag. "Tag topic" is not used.
- **Schema version 4, applied when a graph is opened.** It rewrites the two
  journal kinds, moves each journal row's `frame` field to `metacontext`,
  rewrites the renamed advisory kinds inside the prose of every
  `proceeded_despite_advisory` row and in the graph's own warning policy, and
  moves the metacontext-reassignment trail on each node it finds one on.
- **A Terminology section** in `docs/README.md` and in
  `epimemer_prompts/DEFAULT.md`, naming one word per concept, plus a guard in
  `tests/test_docs.py` that fails when a retired word reappears in the
  documentation or the code.

- `find_nodes`, `query_graph` and `topic_tree` take an optional `metacontexts`
  list, meaning on each what it means on `search`: results are the nodes
  standing in any of the metacontexts named, no metacontext inherits another,
  and an id that resolves nowhere is refused with the graph's own list rather
  than silently narrowing the answer. The node the caller named by id is
  returned whether or not it stands in one of them, which is what keeps a
  topic node created from a tag usable as a starting point; its neighbours are
  filtered. On `find_nodes` the filter runs before the `limit` cut. On
  `topic_tree` an ancestor outside the scope is left out and the rest are
  still ancestors, and a subtopic outside it takes its branch with it.
- `find_nodes`, `query_graph` and `topic_tree` label every node they return
  with the `metacontexts` it stands in, as `search` already did. Reading a
  traversal used to mean asking again, node by node, which world each answer
  was a claim about.

### Security

- Every dependency is updated to a version with no published advisory. The
  installed set went from 19 packages carrying advisories to none. The ones
  reachable from this code were `starlette`, whose `StaticFiles` served the
  visualization hub, and `aiohttp`, which `surrealdb` uses for every graph
  operation. The rest sat behind FastMCP's OAuth and OpenAPI features, which
  this server does not use, or behind the `notebooks` extra.
- The `fastmcp` and `starlette` floors rise to the releases that fixed those
  advisories, so an install resolving to the oldest permitted version no longer
  gets a vulnerable one.

### Fixed

- A metacontext-scoped `search` now scopes the whole response. `edges` keeps
  only edges whose two endpoints are both returned nodes, so no returned edge
  names a node the scope removed. `segments` keeps only passages with at least
  one node extracted from them standing in a named metacontext, matching each
  passage to what was concluded from it. A passage nothing was extracted from
  is dropped from a scoped search and still returned by an unscoped one, which
  goes on answering *where did I read that?* for every passage that matched.

- **A tag resolves to one node however its name is spelled.** `claim_kind` and
  `claim-kind` were two topic nodes, and so were `design-decisions` and `design
  decisions`, because a tag was matched on its exact content. Names are now
  compared with case and separators collapsed, at ingest and at
  `find_nodes(tagged_with_topic=...)`. Dates are untouched, which is the point:
  the similarity bar cannot separate `claim-kind` from `claim_kind` at 0.9196
  without also fusing two different days of work at 0.9935. Where the spelling
  asked for is not the one stored, `store_decomposition` reports it under
  `tags_resolved_to` rather than resolving it silently.
- **A tag name whose node was retired now resolves to the node carrying its
  content.** Enrichment rewrites a topic's content and retires the old node, and
  a merge retires its sources; either left the old name resolving to nothing, so
  the next document carrying it created a second topic node while
  `find_nodes(tagged_with_topic=...)` returned an empty list rather than an
  error. Resolution follows `merged_into` and `superseded_by` forward. A
  historical node is deliberately not followed: its claim is still right of its
  period and its name has not moved.
- Two tags whose names are equal once spelling is collapsed may be merged
  without clearing the topic similarity threshold. An identical normalised name
  is a stronger proof that two tags are one tag than a cosine between their
  spellings, which is what let `claim-kind` and `claim_kind` sit below the bar
  at 0.9196 while pairs that must never merge sat above it. The exemption is
  narrow: every source must be a tag, and every key must match.

## [0.1.3] — 2026-09-06

A patch release on the same reasoning as 0.1.2: no feature changes, and
existing graphs migrate themselves when opened.

### Changed

- A keep verdict now lives only in the decision journal. It used to be written
  twice, as a `retention` row whose anchors were prose at the end of
  `certainty_basis` and as one edge per anchor; the anchors are now a `covers`
  field on the row, and an empty `covers` means the node was kept for its own
  sake. Nothing changes in the `apply_reflection(retained=...)` call: the same
  `covers` goes in, and a later change to the evidence is still a reason the old
  keep does not cover.
- `query_decisions` takes `subject_ids`, matching rows that name any of them.
  One query serves a whole population of nodes, which is what reading keep
  verdicts back needs.
- **Graphs migrate themselves to schema version 3 on open**, wherever a graph is
  opened: embedded and remote alike, on connect and on every graph switch. The
  step parses each retention row's anchors out of its prose into `covers`,
  writes a row for any keep that existed only as edges, and deletes the edges.
  There is nothing to run by hand. **It is one-way**: 0.1.2 reads the anchors
  from the edges, which are gone. To go back, re-derive one
  `review_confirmed` edge from each retention row's `covers` (`src_id` the
  anchor, `dst_id` the subject, and the subject's own id where `covers` is
  empty), then `DELETE schema_version;` so the next upgrade migrates again.

### Removed

- The `review_confirmed` edge type. It was a hand-built index over verdicts the
  journal could not be queried for, and the shape with no anchor had to be faked
  as an edge from a node to itself, which drew as a self-loop in the visualiser
  and was indistinguishable from a real relation.

## [0.1.2] — 2026-09-06

A patch release rather than a minor one, on purpose: no feature changes, and
an existing graph opens under this version and updates itself. Two things to
know before upgrading are in the **Changed** entries below: one tool argument
is renamed, and a graph this version has opened cannot be read by 0.1.1
without a manual step.

### Changed

- `tagged_with` is now `tagged_with_topic`. The old name read as a string stuck
  on the node; the new one says what is at the far end. The vocabulary that goes
  with it: a *tag* is the name passed in `tags=`, a *tag topic* is the Topic it
  becomes. **`find_nodes(tagged_with=...)` is now
  `find_nodes(tagged_with_topic=...)`**, and a call using the old argument name
  is refused. An agent picks the new name up from the tool schema and from the
  guidance shipped in the wheel; only a script or a custom prompt that spells
  out the old argument needs editing.
- The fact → topic reading of `supports` is now its own edge type,
  `extracted_under_topic`. `supports` means fact → inference and nothing else,
  which is what corroboration and the soundness check already took it for; the
  new type is what `reflect` reads when it gathers a topic's material. Nothing
  in the API changes: a caller writing `link(..., edge_type="supports")` between
  a fact and a topic is still accepted, and should now write
  `extracted_under_topic`, which is what the readers of that pair look for.
- **Graphs migrate themselves.** Opening a graph written before this release
  renames both edge types in place, once, and stamps a `schema_version` record
  so later opens skip the work. It runs wherever a graph is opened: embedded
  (`mem://`, `rocksdb:`) and remote (`ws://`) alike, on connect and on every
  graph switch. There is nothing to run by hand, and the migration can be
  deleted from the code once no graph older than this release is expected.
  **It is one-way.** 0.1.1 does not know the new names, so a graph this
  version has opened will not load its renamed edges under the old version.
  To go back, run these against the graph's database first:
  `UPDATE node_edge SET type = 'tagged_with' WHERE type = 'tagged_with_topic';`
  and `UPDATE node_edge SET type = 'supports' WHERE type = 'extracted_under_topic';`,
  then `DELETE schema_version;` so the next upgrade migrates again.

### Removed

- The `associated_timeline` edge type. Nothing wrote it and nothing read it; a
  node reaches its timeline through the `timelink` edge, which points at the
  timeline and names the timepoint in its metadata.

### Fixed

- An inference flagged `evidence_merged` can now be kept. The flag asked for a
  re-read but nothing recorded one, so every such inference came back on every
  `reflect`. `apply_reflection(retained=...)` now anchors a keep to the absorbed
  ids, and `reflect` stops listing the node once they are covered. The archival
  nominator still never reads the flag.
- `retained` measures `covers` against what is still open on a node, not
  against every reason it has ever carried. A node kept against one premise and
  later flagged on another no longer demands the first premise be named again,
  and naming it is refused as already covered rather than written twice.
- `similarity_edges_written` in the `apply_reflection` response counts the
  `similarity` edges a batch wrote, where it previously counted every edge the
  decisions wrote. A `distinct` verdict now reports zero rather than reporting
  the `assessed` edge that records the decline. **A caller comparing this
  number across versions will see it change**: one `one_claim` reports 1 where
  it reported 2.
- Tag Topics merge with one another whatever frame stamps they carry. A tag
  names something rather than asserting it, so it stands in no frame, and the
  frame-equality gate on topic merge left a tag written before
  `epimemer frames declare` permanently unmergeable with one written after.
  The gate still applies wherever any source is a statement, and the survivor
  of an all-tag merge stays a tag.

## [0.1.1] — 2026-08-31

### Fixed

- The wheel now carries `epimemer_prompts/DEFAULT.md`. `INTEGRATION.md` tells a
  reader to open the agent guidance and add it to their agent's instructions,
  and 0.1.0 shipped without the file, so anyone installing from PyPI was
  pointed at something they did not have.


