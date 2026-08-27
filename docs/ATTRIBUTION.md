# Attribution — who judged this

**Built: the registry, the judge on every write, the setting that can require
one, the journal, and the review loop over it.** An agent can be given an
identity, the user assigns it, a session is bound to it, every decision that
session makes — during review and at ingest — carries it, every decision is also
appended to a journal, so *what did this agent judge* is one query. `review`
reads that journal back shakiest-first, `apply_review` records that somebody
checked a decision, and `rejudge` revises a judgment made at ingest without
touching the claim. The design is `dev-docs/REVIEW_MODE.md`.

## The problem it exists for

No decision in this system used to record who made it. Not nodes, not edges, not
`LifecycleEpisode`, not `NodeChangeEvent`. A second agent could see what was
decided and when; it could not see that a *different* agent did it, and on its
own second pass it could not tell its own decisions from the first agent's.

That is the whole motivation: **using a different agent to review the decisions
previously made by the first agent.** Without identity, `reviewed_by ==
judged_by` is unfalsifiable and self-review is indistinguishable from
independent review.

## Identity is assigned, not minted

An agent **proposes a name** and describes itself; the **user** picks which
judge it is, from the judges this graph already knows, or names a new one. What
the user picked is what gets recorded.

```
claim_agent(agent_id="olegs-critic", description="Claude Opus, running as the reviewer pass")
```

Three things follow from the identity being the user's:

- **An unapproved judge is refused.** The refusal is the prompt — there is no
  separate startup handshake, so the message the agent gets is what it puts to
  the user, and it names every channel they can approve through.
- **The user owns the semantics.** Whether judges track a model (*"my llama
  agent"*), a role (*"my critic"*), or a task (*"my editor reviewer"*) is their
  scheme. Two harnesses running the same model are one judge or two exactly as
  they decide.
- **The same name can appear in two graphs**, because the user can assign it in
  both. Correlating them is a human act.

Hashing the description to get an identity was rejected: reword it and you
become a different judge; paste someone else's and you become the same one. The
hash survives one level down, as the **digest** of a description *version*.

## Three layers: the key, the name, and the claim

One field used to be all three, and the collapse was the problem: the name the
user typed on first contact was frozen into every decision, so it could never be
changed, and one character's difference made a second judge with a permanently
separate history.

| Layer | Mutable | What it is for |
|---|---|---|
| key (`agent_id`) | never | The join key. Frozen into every decision, and shown to nobody. |
| name | freely, by the user | The handle: the picker, `review(mode="by_agent")`, a frontend label. Resolved **at read time**. |
| descriptions | append-only | What the judge claimed to be **then**. Pinned per decision by digest. |

**The name and the description resolve by opposite rules, and that is not an
inconsistency.** *Which judge is this* wants the name the user knows it by now,
so a rename carries every old decision with it. *What did this judge claim to be
when it decided this* wants the claim as it stood, or an old decision stops
being readable — which is what the digest is for.

**A handle is anything that names a judge**: its name, its key, or a key it used
to be recorded under. `claim_agent` and `review` both take one, so an agent may
propose whatever the user calls this judge, and a returning one may pass back
the key it was handed. `claim_agent` returns both: `name` is what to say to a
person, `agent_id` is the key.

## Renaming, and repairing a split

**A name can be changed, and the decisions follow it.** Nothing is rewritten:
the key each decision recorded does not change, and the name is resolved when
the row is read. So nobody has to name a judge correctly before knowing what it
will be used for.

Two channels, the same two that can approve — a handle an agent could rename is
a handle an agent could point at another judge's history:

- the judge picker's **rename** option, which then asks which judge and what to
  call it;
- `epimemer agents rename <handle> <name>`, on a served SurrealDB.

**A name already taken is a question, not an error.** Two records that should be
one is the commonest reason to be renaming at all, so the collision asks whether
they are the same judge. Answering yes **consolidates** them: the judge holding
the name takes the other's keys as former keys and both description histories
are kept, so its old decisions stay readable through the record that now answers
for them. Nothing is deleted and no journal row is rewritten — an absorbed
record is kept and simply stops being offered as a judge in its own right.

After consolidating, *this judge's decisions* is a query over a **set** of keys.
That is what `former_ids` is for, and a judge that was never consolidated has a
set of one.

## Descriptions append, and are never edited

`Agent.descriptions` is an append-only list of dated `AgentDescription`s — the
same shape as `LifecycleEpisode` on a node, for the same reason: a scalar plus a
timestamp cannot express *changed, and here is what it was before*. A decision
made last week was made by whatever the agent claimed to be last week, and that
claim has to stay readable after it changes its mind.

Re-claiming with **identical** text is not a new version. Only changed wording
appends.

## A description is a claim, not a credential

Nothing verifies it. It is self-reported prose, exactly like a fact the agent
ingests, and it must never be read as a trust signal. Two rules keep it honest:

- **`confirmed_at` is the only part with human weight.** It is set only through
  a channel that terminates at the user, and `None` means *self-described,
  unconfirmed* — a different object, never collapsed into the same field.
- **The judge gates nothing automatically.** No ranking, no corroboration
  weighting, no default filter. Review will *select* on it; nothing *decides* on
  it.

## Approval reaches the user, not the agent

No MCP tool can approve an id: a tool the agent calls cannot establish that the
*user* called it. Three channels can, and all three terminate at a person.

| Channel | When it works | What `confirmed_at` then means |
|---|---|---|
| the client's elicitation prompt | the client supports elicitation | the user answered through their own UI |
| `EPIMEMER_APPROVED_AGENTS` | always; read when the backend connects and when the server lands on a graph | the user configured the server before starting it |
| `epimemer agents confirm <handle>` | **served SurrealDB only** | the user ran a command the agent cannot run |

The CLI's limit is not an oversight. Approvals live in per-graph settings inside
the backend, and an embedded store (`mem://`, `file://`, `surrealkv://`, or the
in-memory backend) lives inside the server process — a second connection to it
is a *separate store*, not a second view of one. Writing there would report
success into a store the running server never reads, so the command refuses and
names the environment variable instead.

`epimemer agents list` shows a graph's approved judges by name, the key each was
recorded under, any keys consolidated into it, and what each has said about
itself, marked confirmed or self-reported.

Both configuration channels take **names**, because a person types names: a
handle is resolved to the judge that holds it, and one matching nothing is
admitted as its own key, which is what seeding a judge that has not claimed yet
has always meant.

## Approval is per graph

Graphs are isolated, and so are their approved lists, their names and their
keys. A session binds **one**
judge, so `use_graph` re-checks it: a judge the new graph has not approved is
unbound, and the response says so. Carrying a judge approved for graph A into
every write on graph B is how attribution starts recording something nobody
approved.

Ids from `EPIMEMER_APPROVED_AGENTS` are applied to whatever graph the server
lands on, and applied *before* the judge is re-checked — otherwise configuration
would clear a judge it was about to admit.

## What a decision records

Once a session has claimed an identity, these writers record it, each on the
thing the decision landed on:

| Decision | Recorded on |
|---|---|
| `update`, `supersede_by`, `merge_facts`, `apply_reflection`'s merges and supersessions and archivals | the retired node's lifecycle episode, as `retired_by` |
| `restore`, `reverse_merge` | the same episode, as `restored_by` — a separate field, because returning is a separate decision |
| `record_contradiction`, `record_variant`, `link`, `apply_reflection`'s similarity verdicts | the edge, as `judged_by` |
| `judge_importance` | the value signal, as the latest judge — and every entry in the node's reinforcement trail names its own |
| content written during reflect: synthesised parents, splits, enrichments, merge survivors, and `update`'s replacement | the new node, as `judged_by` |
| `segment`, `store_decomposition` | every node and edge the ingest creates, as `judged_by` — including the priors `claim_kind`, `confidence` and `importance`, which nothing downstream re-makes |
| `link` coining a relation label for the first time | the label's record, as `judged_by` — **the coiner, never the describer**. `describe_relation`, a verdict, or a backfill creates a record carrying no judge at all, since none of them is claiming to have introduced the word |
| `reframe`, `correct_interval`, `describe_relation` | nothing on the node or edge — each journals its own row instead, because the thing being revised was somebody else's judgment and overwriting their name would hide that |

Three things follow that are easy to get wrong:

- **A correction does not inherit the previous author.** The replacement is the
  correcting agent's wording, and crediting the earlier one would attribute a
  sentence they never wrote.
- **Re-recording a pair does not restamp its edge.** A second agent calling
  `record_contradiction` on a pair that already has one has *confirmed*, not
  decided. That is a review, and a review gets its own record rather than
  overwriting somebody else's name.
- **Approval is re-checked on every write.** An id the graph no longer approves
  records as unknown rather than failing the call: writing the name would assert
  an approval that no longer exists.

A document and its segments carry **no** judge. They are the material rather
than a claim about it — *who pasted this text* is a different question from *who
judged what it says*. And reusing an existing entity or tag topic does not
restamp it: mentioning a name again is not introducing it.

One decision is still **not** attributed: merging relation labels, through
`apply_reflection(relation_merges=…)`. The obstacle that used to make it
impossible is gone — labels have records with ids since `ISSUES.md` #74 stage 1,
so a row naming them would resolve like any other — and what remains is that no
writer has been built. That waits on #74 settling whether relation merging
survives at all; if it does, the row is stage 4's work. Describing a label **is**
attributed, under its own `relation_description` kind, and so is **judging one
label against another** — `relation_verdict`, whose subjects are the two label
records. That row is where the question this section used to call unanswerable
actually got its answer: the subject of a decision about vocabulary is the
vocabulary entry, and it needed the entry to exist.

Accepting a boundary proposal, the other gap this section used to name, is
closed: it edits an existing edge rather than adding one, which is exactly the
case the journal is for, and both of its subjects are nodes.

## The journal — what did this agent judge

Attribution on the rows answers *who judged this node*. Review asks the inverse,
and over fields scattered across facts, edges, lifecycle episodes and value
signals that is five scans and a reassembly. So every decision is **also**
appended to a journal, and the inline fields are the immutable copy.

A row carries what was decided (`kind`), what it was about (`subject_ids`), who
decided it, when, and — where an agent supplies one — how certain they were.

| Rule | Why |
|---|---|
| **Append is the only write.** No update path exists, on either backend or on the protocol | A reversal, a confirmation and an overturn are all new rows. An edit here would make review state mutable in one place and derived in another |
| **`reviewed` is derived**, from a row whose `reviews` names another | A flag on an append-only row would have to stay in sync with a copy on the node, across two backends |
| **One judgment, one row** | Forty-four facts out of one document is one reading of one document; an archival sweep is one pass over one nomination list. Reflect's other decisions get a row each, because those are independent verdicts that happen to be batched |
| **The row is written after the decision lands** | A refused write leaves no row. The other order would have review chasing a decision the graph never made |

Two kinds are worth knowing about by name. A **correction** and a
**world_change** are separate kinds rather than one supersession with a reason
attached, because they are opposite claims about what happened and a reviewer
asking for one does not want the other. And a **reversal** — undoing a merge —
is the one row that both `reviews` and `supersedes` the record it overturns.

**Re-recording a verdict writes a confirmation.** A second agent calling
`record_contradiction` on a pair that already has one leaves the edge alone and
writes a row pointing at the original decision. That is what stops a third agent
doing the same work again. Where the original predates the journal there is
nothing to point at, and the pointer stays blank rather than inventing a target.

**`certainty` is supplied by the review writers and nowhere else.**
`apply_review` and `rejudge` are calls whose whole point is a declared judgment,
so the ladder is stated there rather than added to a dozen other tool schemas.
Every other decision is unrated, which is deliberately different from a rated
0.5.

## Reading it back — `review`

`review` returns this graph's decisions **shakiest first**, so a reviewer can
start at the top and stop when it stops repaying the attention. It writes
nothing; acting on what you find goes through the ordinary decision tools.

The order is **two tiers that never blend into one score**. A decision whose
agent declared a low `certainty` comes first, ascending. Everything unrated
follows, ordered by how many *derived* signals it carries:

| Signal | What it saw |
|---|---|
| `thin_source` | a subject's own `confidence` is below 0.5 |
| `wide_merge` | three or more sources collapsed into one node |
| `open_contradiction` | recorded, and both sides are still active |
| `ground_moved` | a subject was retired *after* the decision was made |

**An unrated decision never outranks a flagged one.** Blank means *unrated*, not
*doubtful* — the same rule `confidence` follows — so no amount of derived
evidence lets absence read as a claim of doubt.

Read `unrated_count` and `unattributed_count` beside the results: three shaky
rows out of four hundred unrated is not the same answer as three out of four.
`truncated` says the list was cut; act on what came back and ask again rather
than raising the cap.

**The answer covers one graph and `graph` names which** — the journal is per
graph because a row's `subject_ids` resolve only where those nodes live. For
another, `use_graph` and ask again.

**`elsewhere` says where else there is something to see.** One count per other
graph — zeros included — plus a `total`, the filters those counts used
(`counted_with`), and any graph that could not be read (`unreadable`). No rows
and no ids: an id from another graph dereferences nowhere here, and every write
path is single-graph anyway. Going there is still `use_graph` then ask again,
which is safer than a fan-out would be because each switch is the active graph
rather than one borrowed mid-call.

The reviewer this is for is a **later, different agent**. The one that made the
decisions switched those graphs itself and never needed telling.

**It counts wider than it reads, deliberately.** Only the judge, `since` and
`until` narrow the counts; `mode` and `certainty_ceiling` do not. So a graph
counted at 12 can list fewer than 12 once you get there, and `counted_with` is
what tells you that is scope rather than a defect. A count too high costs a
wasted look; a count too low costs the look entirely.

### Modes and filters

A **mode** names the selection; every other argument narrows whatever it
selected. So *"what did agent-1 decide yesterday that nobody has checked"* is
one call rather than three vocabularies.

| Mode | Selects | Needs |
|---|---|---|
| `all` | every row | — |
| `by_agent` | one judge's decisions | `agent_id` |
| `since` | a time window; `until` is exclusive | `since` |
| `unreviewed` | rows no other record points back at | — |

`by_agent`'s `agent_id` is a **handle**: a name, a key, or a key the judge used
to be recorded under. The response's `judge` block says what it resolved to, and
says `unknown_here` where it resolved to nothing — an empty page is otherwise
indistinguishable from a judge that has decided nothing, which is exactly what a
typo or a forgotten rename produces.

`by_agent` and `since` are the same filters the other modes accept, made
**mandatory**. That is their whole value: asking for `all` with an `agent_id`
you forgot to pass returns the entire journal, which reads as an answer rather
than as a missing filter.

`certainty_ceiling` is for **counting**, not browsing — the ordering already
covers browsing. *"Is anything below 0.5 still outstanding before I stop?"* is a
gate, and a gate wants a number. It is inclusive, and it leaves unrated rows out
entirely, since blank cannot be told from ordinary.

**It sees only what was decided after the journal existed.** Anything the graph
was told before then left no row, and nothing can reconstruct one.

## Recording that somebody checked — `apply_review`

If an agent checks a decision and agrees, and nothing records that, the next
agent does the same work again. So a review is a row pointing back, and
`apply_review` is the only thing that writes one.

It takes `confirmations` and `dissents`, each entry `{decision_id, because,
subject_ids?, certainty?, certainty_basis?}`. `because` is required in both:
a review with no reason is a rubber stamp, and it costs more than nothing —
it marks the decision checked, so the next reviewer skips it without being able
to tell whether it was examined or waved through.

**Neither list changes the graph, and the dissent least of all.** Undoing a
merge is `reverse_merge`; an archival, `restore`; a `one_claim` verdict, a
`distinct` through `apply_reflection`; a wrong ingest prior, `rejudge`. A
dissent records the *finding* and sets only `reviews` — never `supersedes` —
because a row claiming to have overturned a decision whose effect still stands
would put the journal in disagreement with the graph. It is most useful exactly
where the undo was **refused**: a merge whose survivor has since been
contradicted cannot be reversed at all, and this is where that finding goes.

`subject_ids` narrows a review to what was actually looked at. One pointer at an
ingest record covering forty-four facts otherwise tells the graph a reviewer
checked forty-four when it checked six.

**A retry must not read as a second opinion.** The same judge confirming the
same decision twice is refused, naming the row that already says it. A
*different* judge is not a retry — it is the second independent check this whole
design exists to make possible. On a graph that does not require a judge, two
blanks cannot be told apart, so a retry there writes a second row: one more
thing `require_judge` buys.

## Revising a judgment — `rejudge`

`claim_kind`, `confidence` and `confidence_basis` are supplied by an agent that
read the material, and nothing downstream re-makes them. Until `rejudge` existed,
review could find every ingest-time mistake and fix none of them.

**It is not a correction and not a supersession.** `update` is for a claim that
was wrong or a world that moved. This is for a claim that is fine where the
*judgment about* it was wrong — a fact labelled `state` that is really an
`event`, a confidence set too high. Nothing here retires a node, moves an edge
or writes lineage, and the node keeps its `judged_by`: that field records who
wrote the wording, which is unchanged.

**The value it replaces is kept**, appended to the node's `rejudgments` trail
with what it was, what it became and why — otherwise this would be the one call
in the system that destroys a judgment rather than superseding it.

`importance` is not here. `judge_importance` is already this tool for that one
field, and two writers for one value is how it ends up depending on which ran
last.

**Two more revisions live in their own tools** — `reframe` and
`correct_interval` (`ISSUES.md` #66) — and the split is about **how each is
addressed**, not about tidiness. `rejudge` names a `node_id` and promises that no
status, edge or lineage moves. A frame revision moves an edge and changes what
merges, what corroborates and what a frame-scoped search returns, so that promise
would become false the day it grew a frame field. An interval belongs to a
**(node, source) pair**, so folding it in would grow a `source_id` that applies to
one field out of five — the tell that two tools are wearing one name.

`rejudge`'s docstring and its "nothing to revise" refusal both name all three
siblings, because the one real argument for a single tool was that an agent looks
in the obvious place. So the obvious place points on — otherwise it reaches for
`supersede_by`, which files a true claim as an error.

## Withdrawing a frame — `reframe`

A metacontext assignment used to be one-way: `link` writes a `has_metacontext`
edge and nothing removed one, so a fact wrongly framed as fiction stayed framed.
That is not cosmetic. It becomes permanently unmergeable with its own twin
(`merge_refusal` refuses cross-frame pairs), it stops corroborating the real copy,
and a frame-scoped search misses it where it belongs while returning it where it
does not. All three fail silently.

**`assign` makes the common repair atomic, and that is the point.** A claim
mis-filed under frame A that belongs in frame B could be moved by withdrawing then
linking — but that path passes through *untagged*, where the claim is asserted in
**every** frame, and it strands the node there permanently if the second call
never happens.

**A withdrawal that leaves no frames is a promotion**, and has to be said out
loud. Untagged is not neutral: base-reality knowledge is inherited by every frame,
so a claim made inside one novel becomes a claim made in all of them.
`to_base_reality=True` is required there — required rather than inferred for
`expected_graph`'s reason, that the check is worth something only because the
agent's intent is stated independently of the state. It is refused where it does
not apply, because a flag that lies about what it authorised is worse than none.

**The withdrawal deletes the edge rather than marking it**, and #68's
carry-forward is why: *before designing a mechanism for undo-without-delete, check
whether the read that would honour it is already there.* Here it is not — frames
are derived by scanning `has_metacontext` edges, so a `withdrawn` marker would
need every such site to subtract it, and any site missed would fail **open**, with
the frame still applying. Deleting fails closed. The withdrawn frame survives in
the node's `reframings` trail and in the journal row, which matters more here than
for a rejudgment: every search and corroboration answer given while the frame was
wrong was wrong, and the trail plus the row's timestamp is the only thing that
bounds which answers those were.

## Correcting a period — `correct_interval`

For an endpoint that is **present and wrong**. `reflect`'s `boundary_proposals`
fills one that is *open*, where a succession implies it — and that is the half
that can ever be automated, because nothing can derive that a stated date was
misread. Different evidence, different act, two calls.

A wrong interval moves a count as well as a date: corroboration reads intervals to
decide whether a look-alike witnesses the same period or is the neighbouring
truth.

**The whole list for that (node, source) pair is replaced**, because an interval
is a position in a list on one edge and has no id of its own. An empty list is
allowed, and is the correction for a period that was invented outright — refusing
it would leave a fabricated period unremovable, which is #66's own shape a second
time. `basis` stays the caller's to state per interval rather than being forced to
`inferred` as `apply_boundary` forces it: a correction is often restoring what the
document actually said. The prior list is kept in the edge's
`interval_corrections` trail.

## Requiring a judge

By default a write may name a judge or not, and a blank means *unknown*. A user
who wants every write tied to an agent or a person turns the requirement on:

```
epimemer agents require on            # this graph
EPIMEMER_REQUIRE_JUDGE=true           # every graph this server opens
```

With it on, a write from a session that has not claimed an approved identity is
**refused**, with a message naming `claim_agent` and this graph's approved
judges.
It covers the twelve tools that create or retire epistemic content; timelines and
metacontexts are scaffolding rather than claims and stay outside it.

Three things about it are deliberate:

- **No MCP tool can change it.** `configure_reflection` and `configure_merge` are
  agent-callable because they tune how eagerly the system nominates things. This
  is a gate on the agent itself, and a gate the agent can open is decoration.
- **It is not retroactive.** Turning it on says nothing about earlier writes:
  the rows that had no judge still have none, and still mean unknown.
- **Turning it on before approving a judge refuses everything**, so the command
  says so at the moment you switch it on rather than letting the next write
  explain it.

A client whose connection cannot hold a session binding is not locked out: a
claim that could not bind to a session is held for the process instead, which is
safe precisely because a transport with no sessions has one client.

**One cost of *blank means unknown*, stated rather than discovered.** On a graph
that never turns the requirement on, a row written before attribution existed
and a row written yesterday by an agent that did not name itself are
**permanently indistinguishable** — both are blank, and nothing else separates
them. That is the price of the rule, and it was paid deliberately: the
alternative read blank as *"written before a date"*, which asserts something
about every unattributed row that nobody checked. Turning the requirement on
draws the line from that moment forward; it cannot draw one backwards.

## Where it lives

A per-graph `agent` table beside `fact` / `topic` / `inference`, with the
approved-key list in per-graph settings beside the reflect counter. Agents are
deliberately **not** graph nodes: as nodes they would surface in `search` and be
swept by `reflect`, and two agents with similar descriptions are not a topic to
merge.

The journal is a per-graph `decision` table beside them, indexed on the judge's
key, the date, the kind, the subjects and what a row reviews — the five reads
review mode needs. Its rows are not nodes either, for the same reason and one
more: a judgment about the graph is not a claim the graph holds.

**Per graph, and that follows from the ids.** `subject_ids` holds node ids —
or, for the two kinds that judge the graph's **vocabulary** rather than its
claims, `relation_label` record ids; either way an id resolves only in the graph
that holds it, so a row filed anywhere else would carry ids that dereference
nowhere. `review` says which of the two answered, in each subject's
`subject_kind`, and a null there still means *nothing did*. The row lives with its subjects,
which is also true when a write lands somewhere unintended: the material and the
record of it stay together. *What did this agent decide* is therefore asked once
per graph, with `list_graphs` and `use_graph` between the asks, and deliberately
not by a fan-out — a switch is the active state, while a fan-out has to borrow it
mid-call and give it back.
