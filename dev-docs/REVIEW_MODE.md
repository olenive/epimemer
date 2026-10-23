# Review mode: who judged this, and can someone else check it

The code is `epimemer/pipelines/review/`, the judge registry and decision
journal on every storage backend, and merge reversal in
`epimemer/pipelines/graph_construction/versioning.py`. `docs/ATTRIBUTION.md`
is the behaviour a caller sees; this page holds the decisions a change has to
keep. It builds on `REVIEW_EPISTEMIC.md` §3 (the verdict taxonomy),
`EVENT_LOG.md` and `WARNINGS_AND_SETTINGS.md`. Review means a different
agent checking an earlier agent's decisions, which needs every part below.

---

## 1. Recording a decline

Every verdict `reflect` asks for has a writer, because a decline with no
record is re-nominated on every reflect to an agent who cannot know it was
refused. `docs/REFLECTION.md` §6 lists the verdicts and what each writes.

**A decline is two populations.** Nomination wants suppression broad;
corroboration wants `similarity` narrow. One edge for both would make *these
are different claims* count as support, inverting what corroboration
measures.

| Verdict | The pair is | Writes |
|---|---|---|
| `one_claim` (`merge_facts` refused: an event, or an unjudged `claim_kind`) | one claim | `similarity`, `assessed` |
| `distinct` | different claims that look alike | `assessed` |
| `distinct` over a standing `one_claim` | withdrawn | `retracted_similarity`, `assessed` |

Every pair sweep skips a pair carrying `similarity`, `contradiction`,
`variant_of` or `assessed`, through one shared reader. `assessed` is a
denormalised suppression index, allowed because it is immutable and cannot
drift from the journal (§3.4).

- **Both sides must be `ACTIVE` or `HISTORICAL`**, where a nomination could
  have happened; elsewhere a judgment suppresses nothing.
- **`one_claim` is refused across metacontexts**: that `similarity` edge would
  let a fiction corroborate a fact. A cross-metacontext pair is
  `record_variant`.
- **Withdrawal is one-way**: nothing re-asserts `one_claim` over a
  `retracted_similarity`, because withholding support costs a count while
  inventing it inverts the quantity.
- **Judgment edges never migrate on retirement** (`similarity`,
  `contradiction`, `variant_of`, `assessed`), so the replacement is
  re-nominated (`REVIEW_EPISTEMIC.md` §4.2).
- **Only an agent's verdict writes these edges**; a sweep writing them would
  assert judgments nobody made.
- **Only `reopen` takes a suppression back**, asserting nothing about the
  answer.

---

## 2. The registry: what an agent is

### 2.1 Identity is minted, not derived

Three layers: an opaque key the server mints, a name the user can change, and
an append-only list of dated self-descriptions pinned by digest. A hashed
description would split one judge on a reword and merge two on a paste. A
decision records a `JudgeRef`, `(agent_id, digest)`: the name resolves to the
current one, the digest to what the judge claimed when it decided.

`former_ids` consolidates two records that were always one judge: the
survivor takes the other's keys and descriptions, and nothing is rewritten. A
judge is therefore a set of keys, and every lookup by judge takes the set.

### 2.2 The user assigns identity, and that is what makes review provable

- **Proposed, then approved.** The agent offers a name and description through
  `claim_agent` and the user approves, because a self-claimed id makes
  self-review indistinguishable from independent review.
- **The question goes up on every fresh bind**, as a pick from known judges,
  because an admitted id would otherwise bind every later session silently.
  The memo that quiets a re-claim is keyed on the identity, so approval as one
  judge never binds as another.
- **The prompt names the proposed judge** and any judge the connection already
  has, since one connection can carry several agents, with the
  self-description last because a terminal cuts the end. The proposed name is
  a picker line, because Claude Code draws free text as a required field, and
  is left off where it matches a listed judge.
- **Declined and unavailable differ.** Declined refuses even a pre-approved
  id; unavailable (no elicitation channel) falls back to the approved list.
- **Rename, retire and reinstate go through the prompt and the CLI only**,
  because an agent that could rename or retire could point a name at another
  judge's history or remove a rival. A name collision asks whether the two are
  one judge. A retired judge stays readable and cannot be bound; reinstating
  takes two gestures.
- **Deleting is CLI-only and gated on a scan**: only a record nothing names
  and that absorbed nothing, because a `judged_by` key must keep resolving.
  `node_judge_ids` and the `_NODE_NAMES_JUDGE` clauses in the SQLite, Postgres
  and SurrealDB adapters must list the same fields.
- **The user owns the semantics**: whether a judge tracks a model, a role or a
  task is the user's scheme. The server sees the model only through the
  client hook in §2.6.

### 2.3 Confirmation reaches the user, not the agent

`ctx.elicit` asks through the client's UI. Otherwise `epimemer agents confirm
<id>` is a command the agent cannot run, and `EPIMEMER_APPROVED_AGENTS` seeds
approval for embedded stores the CLI cannot reach. There is no agent-callable
confirm tool, because it could not establish that the user called it.
`claim_agent` runs outside `tool_timeout_seconds`, so *still reading* never
reads as *cannot elicit*.

### 2.4 Self-description is a claim, not a credential

Nothing verifies a description; `confirmed_at` is the only part with human
weight. **The judge never gates anything automatically**: no ranking, no
weighting, no default filter. Review selects on it; nothing decides on it.

### 2.5 Where it lives

A per-graph `agent` table, approvals in per-graph settings, because graphs are
isolated. Agents are not graph nodes, which search and `reflect` would sweep.
`list_agents` is on the storage protocol and is not an MCP tool, since the
roster in the agent's hands is one step from filtering on it.

### 2.6 The seat: when a confirmation carries over

The seat is the conversation, the client (name and version) and the model
(`epimemer/core/seat.py`). Change any part and the user is asked again. A
seat is complete with the conversation and the model, because client info is
optional in the MCP handshake and Claude Code sends none.

- **A complete seat's confirmation is persisted per graph** and a matching
  claim binds without asking while the judge is approved and not retired,
  because a `/mcp` reconnect restarts the stdio server and would otherwise
  stall an unattended loop on the picker. An incomplete seat asks, says why,
  and uses the in-process memo.
- **The model comes from the harness.** `epimemer client-state record` runs as
  Claude Code's `SessionStart` and `PostModelSwitch` hooks and writes a file
  per session (`epimemer/client_state.py`); a missing file reads as *model
  unknown*. Whether `/fast` or a subagent fires `PostModelSwitch` is
  undocumented.
- **A write from a changed seat is refused**, unknown to known included,
  because the user vouched for the old seat. Reads are not gated.
- **Accepted hole**: a subagent shares its parent's conversation id and so its
  seat; tokens still keep their writes apart (§3.2).
- **Bounds, not expiry**: the newest `JUDGE_CONFIRMATIONS_KEPT` (500)
  confirmations per graph, ordered by instant; client-state files are pruned
  after 30 days unwritten.

---

## 3. Attribution: everywhere, including ingest

### 3.1 Scope

Every write path carries a judge, ingest included, because nothing
re-examines the `claim_kind`, `confidence` and `importance` set there.
`docs/ATTRIBUTION.md` lists where each decision records it. Retire and restore
name two judges on one episode, and each importance reinforcement its own,
since they are separate decisions.

A correction gets the correcting judge, since the wording is new. Reusing an
entity or a topic node created from a tag does not restamp it, and
re-recording an existing pair edge leaves its judge alone (a second agent
agreeing is a review, §6.4). Documents and segments carry no judge. Approval
is re-checked on every write, and a revoked id records as unknown.

### 3.2 Threading it, without a singleton

The judge is resolved once at the tool boundary and passed down as
`judge: JudgeRef | None`. A write naming its `judge_token` is credited to that
claim, which is what lets a subagent and its parent share a connection; an
unknown token refuses, since falling back to the binding is the
misattribution tokens prevent. The binding, tokens and memo live on the
lifespan, fresh per process, and the server speaks stdio only. FastMCP session
state cannot hold them, because the MCP SDK serves each request on a new
connection object and FastMCP keys its state on it.

### 3.3 What absence means

Blank means unknown and nothing more, as an unrated confidence is stored
absent rather than 0.5. There is no backfill, since a synthetic legacy agent
asserts a judgment nobody made; reviewing an unattributed decision writes a
new record naming the reviewer. An unknown judge is dropped from retrieval
responses.

### 3.3.1 The setting

`require_judge` (per graph, off by default, process default
`EPIMEMER_REQUIRE_JUDGE`, set with `epimemer agents require`) refuses a write
without a judge (`docs/ATTRIBUTION.md`, *Requiring a judge*). It is not an MCP
tool, because a gate the agent can open is decoration. Turning it on
reinterprets nothing already written.

### 3.4 Immutable facts may be denormalised; mutable state may not

`judged_by` on a row is the original judge and never changes. Review state
changes, so it lives only in the journal and is derived (§4), because two
homes for one mutable fact drift apart across backends.

### 3.5 Linkage is inline, not an edge

Decision records carry ids as fields, because edges cannot originate from
edges and the pair judgments are edges. *Everything agent X decided* is a
single-table scan of the journal.

---

## 4. The decision journal

Every decision is appended to a per-graph journal of `DecisionRecord` rows.

- **Reviewed is derived**: a record is reviewed when another's `reviews` names
  it. `reviews` and `supersedes` are separate, because a confirmation reviews
  without superseding; a reversal sets both and deletes nothing.
- **Every `DecisionKind` has a writer**, enforced by a test, because an
  unwritten kind is a review filter that silently returns nothing.
  Correction and world change are two kinds, since a reviewer wants one or
  the other.
- **The journal write never raises.** It lands after the decision, outside its
  transaction, and logs a failure, because a raise invites a retry worse than
  a missing row (a retried `store_decomposition` ingests twice). The
  exceptions are rows that are the act: `retention`, `deferral`,
  `boundary_declined`, `reopened`, `confirmation` and `dissent`.
- **Timestamps are padded** with `iso_micros`, because `decided_at` is
  compared as text through its index (`DEVELOPER_GUIDE.md`, *Comparing
  timestamps*).

### 4.1 Granularity: one judgment, one row

One `store_decomposition` is one row whatever the fact count; an archival
sweep and a reactivation are one row each; each entry in `apply_reflection`'s
lists gets its own row, being an independent verdict. A confirmation names
the subjects it covers, because one pointer at an ingest record would mark
forty-four facts reviewed when six were checked.

### 4.2 Which record is primary

| Question | Primary |
|---|---|
| *what happened to this node* | `LifecycleEpisode`, via `query_changes` |
| *who decided it, how sure, has anyone checked* | `DecisionRecord` |

Both stay, because a status change can lack a judgment and a judgment can
change no status. A row never claims to supersede a decision whose effect
still stands, which is why a dissent sets only `reviews`.

---

## 5. Uncertainty: declared and derived, kept apart

**Declared**: `certainty`, on the `confidence` ladder (absent is unrated, not
0.5). Only `apply_review` and `rejudge` take one, since a declared judgment is
the point of those calls.

**Derived** (`pipelines/review/difficulty.py`):

| Signal | Meaning |
|---|---|
| `thin_source` | a subject's `confidence` is below 0.5 |
| `wide_merge` | a merge over three or more sources |
| `open_contradiction` | a contradiction left unresolved |
| `ground_moved` | a subject retired since the decision |

Unrated confidence is not a signal, because absence is the ordinary case.
Rows from before confidence became optional carry a literal `0.5` and cannot
be separated. There is no *just cleared the bar* similarity band, because the
above-bar distribution is unmeasured.

---

## 6. The modes

`review` is read-only, like `reflect`: it nominates, and every change goes
through a writer. `docs/ATTRIBUTION.md` has the modes and filters as a caller
sees them.

### 6.1 Modes: which decisions

`all`, `by_agent`, `since`, `unreviewed`, `warning`
(`pipelines/review/modes.py`). A mode names the selection and every argument
narrows it. `by_agent` and `since` exist to refuse a missing `agent_id` or
`since`, because `all` with a forgotten filter returns the whole journal and
reads as an answer. `between` is refused with a pointer to `since` plus
`until`.

### 6.2 Ordering: shakiest first, always

| Tier | Contains | Ordered by |
|---|---|---|
| 1 | a declared `certainty` | that value, ascending |
| 2 | none | count of §5's signals, most first |

Uncertainty is an ordering, not a mode, because a reviewer wants the whole
session with the doubtful calls on top. Tier 1 always precedes tier 2, since
absence is not a claim of doubt. Worst-first, the cap loses the end nobody
reads. The read is linear in journal size; `decisions_scanned` reports it.

### 6.3 Filters: optional narrowing

`certainty_ceiling` is for counting (*is anything at or below 0.5
outstanding?*): inclusive, because an agent that typed 0.5 meant it, and
excluding unrated rows, because blank cannot be told from ordinary.

### 6.4 The response, and the writer

- **Capped at `REVIEW_MAX_RESULTS` (200), reporting `truncated`**, because the
  journal is unbounded. `unrated_count`, `unattributed_count` and
  `unreviewed_count` cover the whole selection; only `by_agent` excludes
  unattributed rows.
- **`apply_review` is the only writer of a review**, because an unrecorded
  agreement makes the next reviewer repeat the work.
- **A dissent performs no undo.** Each undo has its own tool and refusals
  (`reverse_merge`, `restore`, `apply_reflection` with `distinct`, `rejudge`,
  `reassign_metacontext`, `correct_interval`), and a dispatcher would be less
  safe than the sequence. A dissent is where the finding goes when the undo
  is refused (§7.5).
- **A retry is not a second opinion**: the same judge (any of its keys)
  confirming the same subjects again is refused; a different judge is
  accepted. Two blank judges cannot be told apart, which `require_judge`
  fixes.
- **Not one transaction**: entries are independent, so refusals are per
  entry. The reviewed set covers the whole selection, not the page.

### 6.5 Every verdict needs a writer, including the ones review invents

`rejudge` revises `claim_kind`, `confidence` or `confidence_basis` without
moving status, edges or lineage; the node keeps its `judged_by` and the old
values are appended to `metadata["rejudgments"]`. A wrong `claim_kind` is
neither *it was wrong* nor *the world changed*, so a supersession would retire
a true node.

`importance` stays with `judge_importance`, one writer per field.
`reassign_metacontext` and `correct_interval` are separate because they move
an edge or address a `(node, source)` pair. `reassign_metacontext` takes an
optional `assign`, so a move never passes through no metacontext, and refuses
a withdrawal that would leave none, since such a node is never compared,
merged or returned by a scoped search. It deletes the edge rather than
marking it, so no reader can fail open by missing a marker.

### 6.6 Review is per graph, and says so

The journal is per graph because `subject_ids` resolve only there. `review`
takes no `graphs=` list, because switching by hand is safer than borrowing the
active database mid-call. Every response names its `graph`.

`elsewhere` is one count per other graph, zeros included, no rows or ids. It
counts with `agent_id`, `since` and `until` only, so it may overcount but never
undercount. `review` is in `MOVES_THE_GRAPH` and takes the graph guard's mover
turn on every backend, because SurrealDB borrows the connection to read
another graph.

---

## 7. Reversing a merge

The information a reversal needs is destroyed at merge time, so every merge
captures it.

### 7.1 What a merge destroys

`merge_nodes` migrates every knowledge edge onto the survivor, collapsing
duplicates by `(src, dst, type)`, so which source each edge came from is lost,
and two `sourced_from` edges to one document become one. A chain (`A+B→S1`,
then `S1+C→S2`) needs every partition along it.

### 7.3 The split: audit in the journal, payload on the node

The audit is the `DecisionRecord`. The payload, the pre-merge edge
partition, lives in the survivor's `metadata`: a per-node bound evicts nothing
in a session that touches distinct survivors, and archiving the survivor
takes the payload with it.

### 7.4 The bound

`merge_undo_depth` (default 10, per graph, `configure_merge`) bounds how far
back along `merged_into` partitions are kept, for the claim that keeps
absorbing restatements. Eviction runs after the merge transaction and is
idempotent. The payload is stored without `exclude_defaults`, so a replay
never picks up a later default. Eviction discards reversal capability, never a
claim; it is the one structure that deliberately forgets.

### 7.5 The guard that is not a setting

Reversal is refused when the survivor has been merged again or superseded, or
carries any edge neither in the payload nor written by the merge. That is a
set difference rather than a list of edge types, because reversal ends in a
hard delete and an edge type added later must be refused by default. No
setting raises past it.

### 7.6 What a reversal restores

The pre-merge state, and N reversals equal one. The reversal's journal row
keeps the survivor's content, so the withdrawn wording stays quotable.
Dependents' `derived_from` edges are re-pointed to the original premise and the
merge's `evidence_merged` flag goes, with no new flag. Status is restored and
history appended: a closed episode and two journal rows.

### 7.7 The survivor is deleted, and a node delete is never exposed

The survivor is deleted rather than retired, so cycles leave no husks with
live vectors and a re-merge synthesises afresh rather than reviving an older
agent's `agent:merge` wording. There is no `delete_node`: the delete lives
inside `reverse_merge_tx` on every backend, inside the rollback. Every backend
refuses a survivor that still has edges, and the `EmbeddingRecord` goes with
the node.

### 7.8 Futile cycles

`merge_cycle_limit` (default 2, per graph, `configure_merge`) refuses a merge
of a fact with that many completed merge-and-reverse cycles, counted per node
from closed `merged` episodes, because pair matching misses `A+B` then `A+C`.
Two cycles can be two judges disagreeing; a third is oscillation. A refusal
rather than a warning, because agents proceed past warnings, and it names the
setting so a legitimate merge has recourse. It runs after the
cross-metacontext, event and unjudged-`claim_kind` refusals and before the
similarity bar. Merging a different source set evades it, and that is
accepted.

### 7.9 The payload

Each moved edge (`MergedEdge`) is stored whole, as values, because a
hand-listed subset drops what it forgets: leaving out `metadata` would strip
`judged_by` from every replayed edge. It sits in `metadata` rather than a typed
field because `Topic`, `Fact` and `Inference` share no base class.

---

## 8. What this deliberately does not do

Review does not verify anybody or weight anything by judge (§2.4), backfill
(§3.3), or reverse what it finds (§7). It never runs on its own, since a review
nobody asked for is consolidation by timer.

---

## 9. Warnings are decision records

Proceeding past a warning is a
`DecisionRecord(kind="proceeded_despite_warning")` and nothing else, read with
`review(mode="warning")`, because a second review-state store would be two
shapes for one question (`WARNINGS_AND_SETTINGS.md`).

---

## 11. Rejected

- **One user-assigned string as id, name and handle**: a typo made a second
  judge for ever.
- **A new id per session**: self-review becomes impossible and one judge
  fragments.
- **A mandatory judge from a fixed release, blank meaning legacy**: a
  permanent cost to date a population that needed no date (§3.3).
