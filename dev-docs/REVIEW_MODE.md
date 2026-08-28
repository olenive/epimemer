# Review mode: who judged this, and can someone else check it

**Status: §7, §1, §2, §3, §4 and §9 built, the rest designed (2026-08-23).** Built so far:
§10.2.1's precondition; **steps 0a, 0b and 0c** — the whole of
merge reversal, from capture through the futile-cycle refusal to `reverse_merge`
itself; **step 1**, `apply_reflection(similarities=[…])` and the `ASSESSED`
edge, which closes the `assessed` edge's presenting symptom; and **step 2**, the agent registry
— `claim_agent`, the `agent` table on both backends, approval over elicitation
and config, and the `epimemer` CLI; and **step 3**, the judge threaded through every
reflect-side writer and recorded on the episode, edge, value signal or node the
decision landed on; and **step 4**, ingest attributed and the per-graph
require-a-judge setting with its CLI; and **step 5**, the decision journal —
the `decision` table on both backends, a row at every writer, and
`WARNINGS_AND_SETTINGS.md` §9's node notes folded into it. Steps 6 and 7 below
are design — `review()` and `apply_review`.
Written
before any code, at the user's direction; where an unbuilt section says "does",
read "would". The anchoring rule went first because it is a defect in shipped code that
step 1 would make reachable, and fixing it after would mean shipping it
knowingly. Steps 0a and 0b went next because both read something that exists at
merge time and nowhere else: the edge partition, destroyed as the merge migrates
it, and the lifecycle episodes an oscillation would leave behind. Every merge
taken before 0a landed is permanently irreversible, which is a running cost no
other step has. 0c followed immediately because 0a and 0b are both *dormant*
without it — nothing read the payload and no fact could reach a non-zero cycle
count until reversal existed to write `restored_at`.

**Written to be implemented from.** §10 breaks the build into eight steps with
the types, protocol methods, call sites and tests each one needs; §7.9 does the
same for merge reversal, which is built — read §7 as a record of what was
built and why, and §10 for the seven steps still to come.
An implementer should be able to start from this document without reconstructing
the reasoning — but §11 and §12 record what was rejected and why, and are worth
reading before changing any of it.

**Revised 2026-08-23**: §3.3's absence rule is reversed by the user — blank
means *unknown*, and whether a graph accepts one is a setting rather than a
dated cutover. The reasoning is in §12.2 and the sites it touched are §3.2,
§3.3.1, §4, §6.4, §10.3 and §10.4.

**Revised 2026-08-22 after review**, which found seven defects in the first
draft. Five were mechanical; two changed the design. What moved is recorded in
§11 rather than quietly rewritten, because two of the corrections are the same
carry-forward this repo has now banked three times.

The motivating case, in the user's words: *"using a different agent to review
the decisions previously made by the first agent"*.

That is not renderable from anything the system currently stores, for a reason
worth stating up front: **no decision in this system records who made it.** Not
nodes, not edges, not `LifecycleEpisode`, not `NodeChangeEvent`. A second agent
can see what was decided and when; it cannot see that a different agent did it,
and on its own second pass it cannot tell its own decisions from the first
agent's.

This document covers five parts, together because none of them works alone:

1. **The missing actions** — `similarity`, the verdict with no writer, and
   the suppression marker it turned out to be entangled with.
2. **The registry** — what an agent is, how its identity survives being
   re-described, and why the user assigns it.
3. **Attribution** — where the judge is recorded, and what its absence means.
4. **Review modes** — the filters over decisions, from *the uncertain ones* to
   *all of them*.
5. **Reversal** — what review can actually undo, and the one thing that has to
   be captured before it can ever be built (§7).

Design history it depends on: The `assessed` edge (the defect), fact dedup (fact merge),
The single nomination bar, the nomination cap (bounding a response), the confidence prior (unrated is not
0.5), `REVIEW_EPISTEMIC.md` §3 (the verdict taxonomy), `EVENT_LOG.md` (the
durable change path this extends), and **`WARNINGS_AND_SETTINGS.md` §5.3 and §9**
(node notes, folded into this document's journal — see §9).

---

## 1. The missing actions

### 1.1 The verdict with no writer

`reflect` nominates pairs and the agent classifies each one. Six of the seven
verdicts have an action. The seventh does not.

| Verdict | Action | Exists |
|---|---|---|
| redundant | `merge_facts` | ✅ (fact dedup, 2026-08-21) |
| supersedes | `supersede_by` | ✅ |
| succeeds | `temporally_followed_by` | ✅ |
| recurs | `restore` | ✅ |
| contradicts | `record_contradiction` | ✅ |
| cross-frame | `record_variant` | ✅ |
| **compatible** | `apply_reflection(similarities=[…])` | ✅ (the `assessed` edge step 1, 2026-08-22) |

`grep EdgeType.SIMILARITY` returns three sites and all three read. Measured
2026-08-21: **0 similarity edges of 4,386 on `memory`, 0 of 1,028 on
`petritype-server`.**

Of the 18 pairs `reflect` nominated on 2026-08-21, five merged and **thirteen
were declined and vanished**. They are still being re-nominated, because
`already_linked` in `contradiction_detection` is built from
`SIMILARITY ∪ CONTRADICTION` and is therefore always empty.

### 1.2 A decline is two populations, not one

**This is the correction that most changed the design.** The first draft wrote
one `similarity` edge for every decline, and that edge has two readers which
want opposite breadth:

- `already_linked` wants it **broad** — suppress every pair anybody assessed.
- `corroboration.py` wants it **narrow** — its neighbourhood is *restatements of
  one claim*, and `docs/RETRIEVAL.md` §8 says outright that a wrong `similarity`
  edge overstates the count.

Write one edge for both and *"these two are different claims"* starts
corroborating. That is **manufactured support** — the failure `fact_dedup.py`'s
own header calls the worst the system can produce, since a false merge does not
lose information, it inverts the quantity corroboration measures.

The two populations are already in the taxonomy; the first draft invented a
category ("related, kept apart") that collapsed them:

| Why the agent declined | It is | Records |
|---|---|---|
| `redundant`, but `merge_facts` refused — an **event**, or an **unjudged** `claim_kind` | genuinely one claim | `similarity` **and** `assessed` |
| `compatible` — different claims that merely look alike | not one claim | `assessed` only |

The other two refusals need no new action: a **cross-frame** pair is
`record_variant`, and a **retired twin** is `recurs` → `restore`. A below-bar
refusal cannot reach a nominated pair, since the single nomination bar unified the bar.

So suppression gets a record of its own — an **`ASSESSED` edge** — and
`already_linked` reads `SIMILARITY ∪ CONTRADICTION ∪ VARIANT_OF ∪ ASSESSED`.

`ASSESSED` is a denormalised suppression index and it passes the rule in §3.4:
it is immutable and append-only, so it cannot drift from the journal that also
records the decision. The journal is the audit record; the edge is the index the
sweep reads without a journal query.

### 1.3 The action

**`apply_reflection(similarities=[…])`**, a tenth kind of decision beside the
nine that exist, carrying the verdict rather than a bare pair:

```python
similarities=[
    {"pair": [a_id, b_id], "verdict": "one_claim", "because": "same claim; "
     "merge refused, both are events"},          # → similarity + assessed
    {"pair": [c_id, d_id], "verdict": "distinct", "because": "both about "
     "validity intervals, different assertions"},  # → assessed only
]
```

The agent is already in that call having made every other decision; declining
becomes an outcome applied in the same batch rather than a separate errand. The
two alternatives were rejected:

- **A write on `merge_facts`' refusal** — wrong for the cross-frame refusal,
  where `record_variant` is the correct relation; and it turns a call that
  answered *no* into one that wrote to the graph anyway. §1.2 makes it worse
  still: the refusal reason does not determine the verdict, since an agent may
  decline a mergeable pair as `distinct`.
- **A `record_similarity` tool** — this already effectively exists as `link`,
  has existed throughout, and the count is zero. A call outside the loop where
  the judgment happens is a call nobody makes.

**This is not licence to write these edges automatically.** They record a
*judgment*. A sweep that wrote them for every pair over the bar would fill the
graph with assertions nobody made and suppress its own future nominations while
doing it. Similarity nominates; the agent judges.

---

## 2. The registry: what an agent is

### 2.1 Identity is minted, not derived

The first proposal was to hash the self-description and use that as the id. It
is the right instinct in the wrong slot, and it fails in both directions:

- **Reword the description, become a different judge.** An agent that describes
  itself slightly differently next session gets a new id, and its decision
  history fragments across ids that are in fact one judge.
- **Paste the same description, become the same judge.** Two genuinely
  different agents with identical prose are indistinguishable.

So: **a stable `agent_id` assigned by the user, with an append-only list of
dated descriptions under it, and the hash identifies the description
*version*.**

```python
class AgentDescription(BaseModel):
    """One thing an agent said about itself, and when it said it.

    Append-only. A re-description appends; nothing is ever edited, because a
    decision made last week was made by whatever this agent claimed to be last
    week, and that claim has to stay readable after the agent changes its mind.
    """
    # sha256 of `text`, truncated. Identifies the *version*, so a decision that
    # records it pins what the judge claimed to be at the moment it decided —
    # no as-of query needed. Re-recording identical text is not a new version.
    digest: str
    text: str
    recorded_at: datetime
    # Set only through a channel that terminates at the user — `ctx.elicit`,
    # or the `epimemer agents confirm` CLI where the client cannot elicit
    # (§2.3). Never by the agent alone. `None` is *self-described, unconfirmed*
    # — a different epistemic object, never collapsed into the same field.
    confirmed_at: datetime | None = None


class Agent(BaseModel):
    """A judge: something that made decisions in this graph.

    Not a user account and not a credential. See §2.4.
    """
    id: str                      # assigned by the user, not minted here
    descriptions: list[AgentDescription] = Field(default_factory=list)
    authorised_at: datetime      # when the user admitted this id
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
```

The append-only-list-with-dates shape is deliberately the one `LifecycleEpisode`
already uses for node history. Same problem, same answer: a scalar plus a
timestamp cannot express *changed, and here is what it was before*.

> **Amended 2026-08-26: `id` was doing three jobs, and is now
> three fields.** The reasoning above is intact — an identity must not be
> derived from the description — but the conclusion, *one stable string assigned
> by the user*, collapsed the join key with the human handle. Everything the
> user typed on first contact was frozen into every `judged_by`, so a name could
> never be corrected, and one character's difference made a second judge with a
> permanently separate history. Measured on this repository's own `memory`
> graph: one row under `Opus 5 Judge`, one under `Opus 5`.
>
> `Agent` now carries `id` (opaque, never displayed, minted by
> `new_agent_id()`), `name` (freely renamable, resolved at read time, unique per
> graph), and `former_ids` (the keys this judge's rows may already record).
> `descriptions` is unchanged and still pinned per decision by digest.
>
> **The name and the description resolve by opposite rules on purpose.** *Which
> judge is this* wants the name the user knows it by now, so a rename carries
> old rows with it; *what did this judge claim to be when it decided this* wants
> the claim as it stood, which is the whole reason the digest exists. The earlier identity proposal
> rejected an opaque id partly by running these two together.
>
> **`former_ids` is aliasing, migration and repair in one list.** Consolidating
> two records that were always one judge rewrites nothing and deletes nothing:
> the survivor takes the other's keys and both description histories, and its
> old journal rows keep the key they were written with. An absorbed record is
> kept and stops being *live* — derived by `live_agents`, never stored as a
> flag. `judge_aliases` is the one place *which judge did the caller mean* is
> answered, and `query_decisions` takes `agent_ids` rather than `agent_id`
> because after a consolidation a judge **is** a set of keys.
>
> A record written before this has no name and reads as its own id, which is
> what that id was. No migration writes anything; the next claim fills the name
> in.

### 2.2 The user assigns the id, and that is what makes review provable

An agent that mints or claims its own id cannot establish that it is a
*different* agent from the one that decided yesterday — the motivating case
collapses, because `reviewed_by == judged_by` and self-review is
indistinguishable from independent review.

**Identity is proposed, not claimed** (user's design, 2026-08-22). The agent is
not the author of who it is, and it is not left guessing either — it offers, the
user edits, and the approved pair is what gets recorded:

| Step | Who | How |
|---|---|---|
| Detect a new session or a different client | the server | `ctx.session_id`, `ctx.client_id` |
| Propose an id and a description | the agent | `claim_agent(agent_id, description)` |
| Edit and approve, or name a different id | **the user** | `ctx.elicit` — the server asks, the user answers (§2.3) |
| Record the approved pair | the server | `Agent` + an `AgentDescription` version |
| Re-describe later | the agent, approved the same way | appends a version, never edits one |

**`claim_agent` refuses an id the user has not approved.** An unapproved or
absent identity comes back refused, with a message the agent puts to the user —
so **the refusal is the prompt**, and no separate startup handshake is needed.
Admitting unapproved ids would hand identity straight back to the agent, and
*"a different agent reviewed this"* would be self-asserted again, which is the
whole thing this section exists to prevent.

> **Amended 2026-08-25: the gate guards *assuming* an id, not only
> minting one.** As built, `claim_agent` asked only where the proposed id was
> not already approved — so once an id was admitted, every later session bound
> to it with no user involvement at all, and the refusal above names the
> approved ids, which made a wrong guess a directory lookup. The question now
> goes up on **every bind**, and is a **pick from the judges this graph already
> knows** rather than a name to type: `list_agents` had the answer all along
> and no consumer outside the CLI. What makes asking every time affordable is
> that the answer is usually the first line offered.
>
> Two states that were one value until now, because the conflation only became
> load-bearing here: **declined** (the question reached a person and they said
> no) refuses even a pre-approved id, while **unavailable** (no elicitation
> channel exists) falls back to the approved list, since that approval is §2.3's
> user involvement happening earlier rather than none.
>
> Asked **once per session, per graph, per identity** — keyed on the identity,
> because a memo meaning *this session confirmed something* would let an agent
> be approved as one judge and bind silently as another. A changed description
> is still put to the user: the memo records an identity, not a wording.

> **Extended 2026-08-26: renaming lives on this channel too.** The
> name layer is the only mutable one (§2.1's amendment), and it is reachable
> from exactly the two places approval is — the elicitation prompt and the CLI
> — for the same reason: a handle an agent could rename is a handle an agent
> could point at another judge's history. It is in the picker and not only in
> the CLI because the CLI cannot reach an embedded or in-memory store at all,
> and the picker is where a user *sees* the wrong name.
>
> **A name collision is a question, not a refusal.** Two records that should be
> one is the commonest reason to be renaming, so the collision asks whether they
> are the same judge; yes consolidates, and the consolidation is the migration
> step §2.1's amendment describes. The CLI answers it with `--same-judge`,
> because a command has nowhere to ask.
>
> What the user answers the *identity* question with is a handle as well, so
> choosing an existing judge and typing the name of one land in the same place.
> That matters on the free-text path, reached by asking for a **new** judge:
> typing the name of an existing one now joins it rather than minting a second
> record with the same name, which is exactly how this graph's own split began.

**The user owns the semantics, and the system imposes none.** Whether ids track
a model (*"my llama agent"*), a role (*"my critic"*), or a task (*"my editor
reviewer"*) is the user's scheme. Two harnesses running the same model are one
judge or two exactly as the user decides.

**What the server can and cannot detect, stated because it bounds the above.**
`client_id` and `session_id` identify the **client application**, not the model
behind it. A different harness is detectable; **swapping the model inside one
harness is not** — the model is never on the wire. So detection reliably answers
*"is this a new session?"* and can never answer *"is this a different LLM?"*.
That second question is the user's to answer, which is what the flow above
already assumes.

**Session-per-mint was rejected** — it makes self-review impossible by
construction, but fragments one judge across sessions, which is the failure
§2.1 ruled out hashing for.

### 2.3 Confirmation reaches the user, not the agent

The first draft said confirmation *"is not an MCP tool, and cannot be"*, on the
grounds that a tool called by the agent cannot establish that the **user** called
it. The premise is right and the conclusion was wrong, because it assumed every
MCP call originates with the agent.

**`ctx.elicit` inverts the direction**: the server asks, and the answer comes
back from the user through the client's own UI. Present in FastMCP today, and
gated on a capability the client declares. So confirmation can be in-band after
all, and `confirmed_at` can mean what it says.

| Channel | When | What `confirmed_at` then means |
|---|---|---|
| `ctx.elicit` | the client supports elicitation | the user answered, through their own UI |
| `epimemer agents confirm <id>` (CLI) | it does not | the user ran a command the agent cannot run |

**What has not changed is the rule.** No path exists by which the agent alone
sets `confirmed_at` — the two channels above are the only ones, and both
terminate at the user. A `confirm_agent` tool the agent could call is still
refused, for the reason the first draft gave.

### 2.4 Self-description is a claim, not a credential

**This must be stated in the tool guidance and not only here.** An agent
describing itself is making an assertion, exactly like a fact it ingests.
Nothing verifies it; the field is self-reported prose.

That is fine for what this is — an audit trail. It is not fine as a trust
signal, and the risk grows with adoption: on five decisions a human eyeballs the
field; on six hundred thousand somebody builds a filter like *"only count facts
judged by agent X"* and forgets what the field is made of.

Two rules follow, and they are load-bearing:

- **`confirmed_at` is the only part with human weight**, and §2.3 is why it can
  carry any.
- **The judge never gates anything automatically.** No ranking, no corroboration
  weighting, no default filter. Review mode *selects* on it; nothing *decides*
  on it. §8 refuses the obvious next request.

### 2.5 Where it lives

**A per-graph `agent` table**, like every other table, with the authorised-id
list in per-graph settings. Graphs are isolated by design.

The first draft claimed *"the same `agent_id` appearing in two graphs is how a
human correlates them"* while also minting ids per graph, which never produces
the same id twice. Under §2.2 it works, and for the right reason: **the id is
the user's to assign, so the user can assign the same one in both graphs.**
Correlation is a human act, which is what it always was.

Rejected: a shared registry (breaks the isolation every other table follows),
and agents as ordinary graph nodes (they would surface in search and get swept
by `reflect` — two agents with similar descriptions are not a topic to merge).

---

## 3. Attribution: everywhere, including ingest

### 3.1 Scope

**Every write path carries a judge**, ingest included. The marginal cost over
reflect-only is signature churn rather than architecture, and ingest is where
the unreviewable judgments live: `claim_kind`, `confidence`, `importance` are
supplied by the agent that read the material, frozen at that moment, and
re-examined by nothing.

| Decision | Recorded on | Example of what review recovers |
|---|---|---|
| `claim_kind` at ingest | `Fact` | *"agent-1 called 44 facts `state`; two look like events"* |
| `confidence` + basis | `ValueSignal` / `metadata` | *"every 0.9 in this graph came from one agent"* |
| similarity / assessed / contradiction / variant | edge `metadata` | *"which pairs did it decline, and as what"* |
| merge, supersede, archive | `LifecycleEpisode` | *"who retired this, and what replaced it"* |
| `judge_importance` | `ValueSignal` | already has `importance_judged_at`; gains a judge |
| topic parents / splits / enrichments | `Topic` / `metadata` | structural calls nobody currently owns |

### 3.2 Threading it, without a singleton, and required where the graph says so

The obvious implementation is an ambient "current agent" resolved once and read
from everywhere. **That is a singleton and this project does not have those.**

It is not needed. `storage` is already passed explicitly into every tool in
`mcp/tools.py`; `claim_agent` binds the session; the resolved judge rides down
as one more explicit parameter beside a parameter that is already there.

```python
async def store_decomposition(
    ...,
    storage: StorageBackend,
    judge: JudgeRef | None = None,   # (agent_id, digest); absent = unknown
) -> tuple[dict, ResponseMeta]:
```

> **Revised 2026-08-23 (user's decision).** This block previously read
> `judge: JudgeRef` — *"required, not `| None`"*, with a write lacking one
> refused outright from step 4 onward. **Whether a blank is accepted is the
> graph's policy, not the signature's**, and §3.3 below carries the argument.
> The type stays optional everywhere and one shared check at the tool boundary
> asks *does this graph require a judge, and is there one* — which is also the
> only place that can consult a per-graph setting.
>
> Nothing else in this subsection changes: the judge is still resolved once at
> the boundary and passed explicitly, and it is still never a module global.
> **That** is what has no exceptions.

### 3.3 What absence means — decided on day one, reversed on day five

> **Revised 2026-08-23, and the first bullet is reversed (user's decision).**
> The original is kept below the replacement, because the argument that produced
> it is still the right argument and only its conclusion overreached.
>
> **Blank means *unknown*. That is the whole of it.** It does not mean *written
> before attribution existed*, it carries no date, and it asserts nothing about
> why nobody is named. The old reading bought one meaning by making the field
> mandatory for ever after a fixed release — which is a large permanent cost
> paid to describe a population that can be described honestly for nothing:
> *we do not know who judged this*.
>
> **Whether blank is allowed is a per-graph setting, default permissive.** For
> many graphs it genuinely does not matter who judged; for others the user wants
> every write tied to an agent or a person, and the id is where that goes. So
> the graph says which it is, and a graph that requires a judge refuses a write
> without one. Turning it on later is not retroactive and does not need to be —
> nothing about the earlier rows was ever claiming to be dated.
>
> **What the scars below actually argued for**, and it survives intact: a blank
> must never be given a meaning nobody asserted. The old bullet did exactly that
> — it read a date into an absence. *Unknown* is the reading that adds nothing.
>
> **No backfill still holds, and now has a positive half worth stating.** A
> review of an unattributed decision writes a **new** record naming the
> reviewer, pointing back at the old one (§4). The reviewed record is untouched,
> because records are never edited. So a graph that ran unattributed for months
> can still take a fully attributed review pass, and the result reads honestly:
> *judged by unknown, reviewed by this agent on this date.* That is what makes
> the permissive default safe rather than merely convenient.
>
> **The review-mode default moves with it** — see §6.4, which no longer hides
> blank-judge rows across the board.

The day the field exists, every node and edge already in the graph reads as
*judged by nobody*, and nothing distinguishes that from *written before
attribution existed*.

**This project has the scar twice already.** Every row written before
2026-08-19 carries a literal `0.5` confidence, so those rows read as *rated
ordinary* when nobody rated them — which is why the confidence prior stores unrated as absent.
And fact dedup's 305 of 356 active facts carry no `claim_kind` and never will, an
island that does not shrink by waiting.

So, decided now rather than discovered later:

- ~~**`judged_by is None` means "written before attribution existed"** — one
  meaning, guaranteed by §3.2's refusal, with the cutover date recorded here and
  in `docs/`.~~ **Reversed 2026-08-23; see above.**
- **No backfill, ever.** Stamping a synthetic `legacy-agent` asserts that an
  agent existed and made a judgment. That is the same species of lie as the
  literal `0.5`, reached by the same well-meant route.
- ~~**Review modes exclude null by default**~~ — **superseded**; they still
  **report** how many rows had no judge, so a caller can tell *nothing to
  review* from *nothing attributable*, but hiding them by default is now wrong
  on a graph that never required one (§6.4).

### 3.3.1 The setting

One per-graph setting, stored beside the reflect counter and the merge
overrides, which is where every other per-graph setting already lives:

| | |
|---|---|
| **Off (default)** | a write may carry a judge or not; blank is recorded as unknown |
| **On** | a write without a judge is refused, with prose naming `claim_agent` |

**It is not an MCP tool.** `configure_reflection` and `configure_merge` are
agent-callable because they tune how eagerly the system nominates things. This
one is a gate on the agent itself, and a gate the agent can open is decoration —
so it takes the same channel as the approved-id list (§2.3): an environment
variable read at connect, and a CLI subcommand the agent cannot run.

**Existing graphs start off**, and turning it on affects only writes after that
moment. There is no migration, because nothing is being reinterpreted: the rows
that had no judge still have no judge, and still mean *unknown*.

### 3.4 Immutable facts may be denormalised; mutable state may not

The first draft put `reviewed_by` / `reviewed_at` both on the journal row and
inline on every node and edge — two homes for one *mutable* fact, kept in sync
across two backends. That is the shape this repo has hit three times and it was reintroduced in a document that cites all three.

The rule that prevents it, stated once and applied throughout:

| On the row, inline | In the journal only |
|---|---|
| `judged_by`, `judge_desc` — **never change** | review state — **changes** |

So the inline fields are the original judge and nothing else. Nothing on a node
or an edge ever records that it was reviewed; §4 does, and derives it.

### 3.5 Linkage is inline, not an edge

Each decision record carries the ids as fields. One rule everywhere, no extra
edges, no fan-out on the retrieval path.

A traversable `judged_by` edge was rejected on a structural point rather than a
cost one: **edges cannot originate from edges**, so similarity, assessed,
contradiction and variant decisions — the ones review mode most wants — would
need the inline form regardless. That is two rules for one relation, to make one
query prettier.

The consequence is accepted: *"everything agent X decided"* is a scan rather
than a traversal, wanting an index on `judged_by`. §4 makes it a single-table
one.

---

## 4. The decision journal

Attribution on the rows answers *who judged this node*. Review mode asks the
inverse — *what did this agent judge* — and that question, over rows scattered
across facts, edges, lifecycle episodes and value signals, is five scans and a
reassembly.

**So decisions are also appended to a journal**, and §3.4's inline fields are
the immutable denormalised copy.

```python
class DecisionRecord(BaseModel):
    """One judgment, as an append-only row.

    **Never edited, with no exceptions** — including for review state, which is
    why there is no `reviewed_at` here. A review is another record pointing back
    (`reviews`), so *reviewed* is derived from existence rather than stored as a
    mutable flag on a row that claims to be append-only. The first draft had
    both and the contradiction was load-bearing: a mutable field on this row
    also has to stay in sync with a copy on the node, across two backends.
    """
    id: str = Field(default_factory=_new_id)
    kind: DecisionKind
    subject_ids: list[str]
    # Absent = **unknown**, and nothing more (§3.3). A graph that does not
    # require a judge still journals: the row carries how certain the agent was
    # and whether anyone has since checked it, and both are worth having from an
    # agent that did not name itself. A graph that *does* require one never
    # writes a blank here, because the write was refused before reaching this.
    judged_by: str | None = None
    judge_desc: str | None = None
    decided_at: datetime
    # §5. Same ladder as `confidence`, stated once and referenced — absent
    # means unrated, which is deliberately not a rated 0.5.
    certainty: float | None = None
    certainty_basis: str | None = None
    # The record this one is about. A confirmation reviews without superseding,
    # so the two are separate fields — collapsing them is what breaks a
    # derived-only scheme, since a confirmation supersedes nothing.
    reviews: str | None = None
    supersedes: str | None = None
```

`reviewed` is `EXISTS(record WHERE reviews = this.id)`. `unreviewed` mode is its
complement. Nothing is ever written twice and nothing is ever edited.

**A reversal appends, and never deletes.** Agent 2 overturning agent 1's merge
writes a record with both `reviews` and `supersedes` set. The graph's own rule —
*nothing is destroyed, ambiguity is made visible* — applies to the record of
judgments exactly as it applies to the claims.

### 4.1 Granularity at ingest

**One record per `store_decomposition` call, not per fact.** Forty-four facts
from one document is one reading of one document, and the agent made one pass;
recording 44 decisions would make ingest the journal's dominant writer by orders
of magnitude and would still be describing a single act.

The per-node judgments ride inside the record. The trade, stated: reviewing
*"was this fact really a `state`?"* nominates a record covering the whole
document, and the reviewer opens the facts from `subject_ids`. That is the
honest granularity — one document read, one judgment pass.

**The same coarseness applies to confirmation, and that half is worth naming
separately.** One `reviews` pointer against an ingest record marks **all 44
facts reviewed**, so a reviewer who checked six of them and confirmed has told
the graph it checked forty-four. Nomination degrading gracefully at this
granularity is fine; confirmation does not, because it is what stops the next
reviewer looking. Either a confirmation on an ingest record names the subjects
it actually covers, or the record stays unreviewed until all of them are — and
**naming the subjects is the cheaper of the two**, since `subject_ids` is
already a list.

### 4.2 Which record is primary

A merge now appears in two places, and `EVENT_LOG.md`'s rule is that one of them
is the primary. They answer different questions and neither is derived from the
other:

| Question | Primary |
|---|---|
| *what happened to this node* | `LifecycleEpisode` → `query_changes` |
| *who decided it, how sure were they, has anyone checked* | `DecisionRecord` |

A status change with no judgment behind it is possible (`restore` after an
archival sweep); a judgment that changes no status is common (a declined pair).
Neither can be reconstructed from the other, so both stay, with the boundary
written down here.

---

## 5. Uncertainty: declared and derived, kept apart

*"Reviewing difficult or uncertain decisions"* needs the system to know which
those are, and there are two sources that must not be blended into one number.

**Declared.** The deciding agent supplies `certainty`, on **the same ladder as
`confidence`** rather than a second near-identical one — 0.3 / 0.5 / 0.7 /
0.9 with the same anchors, absent meaning unrated, and a basis asked for
whenever the value is not 0.5. Stated once in the shared guidance and referenced
from both, so ingest guidance does not double in size.

**Derived.** Properties of the decision, computable after the fact:

| Signal | Why it suggests difficulty |
|---|---|
| source `confidence` **< 0.5** | the material was thin |
| a merge over three or more sources | more ways to be wrong |
| a contradiction with no resolution | left open, by decision or by neglect |
| a decision whose subject has since been superseded | the ground moved under it |

**Unrated confidence is not a difficulty signal**, and the first draft's table
said it was. The confidence prior ladder defines absent as *the ordinary case* — "stated
plainly, no specific reason to doubt → omit the field" — and it is the majority
state (125 unrated on `memory`). Treating absence as thinness floods the mode
with ordinary decisions and re-commits the exact sin the confidence prior fixed: assigning a
meaning to absence that nobody asserted.

One population this signal is blind to, recorded rather than papered over: the
pre-2026-08-19 rows carrying a literal `0.5` are *genuinely* unrated and pass a
`< 0.5` filter as rated-ordinary. Nothing can separate them now, which is what
that scar costs.

**No similarity band, until it is measured.** The first draft proposed
`[0.80, 0.85)` as *just cleared the bar*. The 0.85 was invented, and the single nomination bar is
explicit that every bar reads one named, documented constant with a test across
its declarations.

It is also not yet derivable. `BENCHMARKS.md` measures 38,226 real fact pairs
with median 0.164 and p99.9 = 0.683 — but that describes the *whole*
population and says nothing about the shape *above* 0.80, which is where the
band would live. The only two above-bar scores on record, 0.87 and 0.89 from
2026-08-21, sit **above** a `[0.80, 0.85)` band, so it would have selected
neither. Two points is not a distribution.

So the band is cut, and **measuring the above-bar distribution is a precondition
for adding it back**. The four signals above need no threshold and carry tier 2
of §6.2's ordering meanwhile — which is the whole corpus today, and the reason
derived difficulty earns its place at all.

---

## 6. The modes

`review(...)` — read-only, like `reflect`, and for the same reason: it
nominates, and every change goes through the existing decision tools.

**Three things are separate, and the first draft ran them together.** *Which*
decisions you are looking at, *what order* they arrive in, and *whether* the
list is narrowed further are different questions, and `uncertain` and
`difficult` were sitting in the mode column while being answers to the second.

### 6.1 Modes — which decisions

| Mode | Selects | Answers |
|---|---|---|
| `by_agent` | `judged_by == …` | *"check everything this judge did"* |
| `since` / `between` | `decided_at` in range | *"review yesterday's session"* |
| `unreviewed` | no record `reviews` this one | *"what has nobody looked at"* |
| `advisory` | `kind` is an advisory override (§9) | W&S §5.3's `contested_decisions` |
| `all` | every record | the full audit |

Modes compose — `by_agent` **and** `since` is the ordinary case for *"review
what agent-1 did yesterday"*.

> **Revised 2026-08-23, on building it.** Composition and a single `mode` string
> are reconciled as: **the mode names the selection; every argument narrows
> whatever it selected.** So `mode="unreviewed", agent_id=…, since=…` is one
> call, and `by_agent` and `since` become sugar over a *required* argument —
> which is worth having, because `all` with an `agent_id` the caller forgot to
> pass returns the whole journal and reads as an answer.
>
> **`advisory` is refused by name and `between` is not a mode.** `advisory`
> selects on a `DecisionKind` nothing writes, so it would return an empty list
> that reads as *nothing is contested*; `between` is `since` with an `until`,
> and two names for one selection is §5.2's *two shapes for one question*.

### 6.2 Ordering — shakiest first, always

**Every mode returns its results least-confident first**, the way
`nominate_archival_candidates` already returns *worst first*. This replaces the
`uncertain` and `difficult` modes of the first draft, and the reason is that
nobody actually wants only the doubtful ones: a reviewer checking yesterday's
work wants **all** of yesterday, ordered so the doubtful calls are at the top
and they can stop reading when it stops repaying the attention.

It is **two tiers, never one blended number** — §5's rule that declared and
derived must not be mixed, applied to the sort rather than abandoned:

| Tier | Contains | Ordered by |
|---|---|---|
| 1 | decisions with a declared `certainty` | that value, ascending |
| 2 | decisions with none | the derived signals of §5, most signals first |

**Tier 1 before tier 2, and that ordering is itself a rule.** Absence is not a
claim of doubt, so an unrated decision never sorts above one an agent
actually flagged.

**The graceful degradation is the point.** The whole existing corpus is tier 2 —
nothing carries a certainty and nothing will until agents start supplying it —
so on today's graph the order is entirely derived, and it still works. As
certainties accumulate, tier 1 fills from the top and the order improves without
anything changing.

**It also makes the cap benign.** Results are capped and report `truncated`
(below); ordered worst-first, a cut list loses the end nobody was going to read.

### 6.3 Filters — optional narrowing

**`certainty_ceiling`**, off by default. When supplied, keeps only decisions
whose declared certainty is **at or below** it; unrated decisions are excluded,
since blank cannot be distinguished from ordinary.

Its use is not browsing — ordering already covers that — but **counting**:
*"is anything below 0.5 still outstanding before I stop?"* is a gate, and a gate
wants a number rather than a list.

`0.5` is the value to reach for, **inclusive**, on two grounds. `importance_
ceiling` is inclusive of its own default because *"nomination is a proposal
rather than a verdict"*, and the same holds here. And the guidance says to
**omit** at 0.5, so leaving it blank is the easy path — an agent that typed 0.5
anyway was making a point of it, and including it respects that.

**Why 0.5 is a legitimate constant where §5's `0.85` was not**, since the two
look alike and the difference matters: 0.5 is a labelled anchor on the `the confidence prior`
ladder that tool guidance actively teaches, so it means something before anybody
measures anything. The `0.85` was a number with no referent anywhere in the
system. Neither is derived from data — but only one of them needs to be.

**The response names the ceiling this call used**, never asserting what the
graph would have done, because a caller can pass their own. That is the single nomination bar's
carry-forward verbatim: `merge_facts`' refusal message stated a threshold as
though it were the system's, and was false for exactly the caller who overrode
it.

**Always reported: how many decisions were unrated.** Three results out of four
hundred blanks is not the same answer as three out of four, and only one of them
means *"the graph is in good shape"*.

### 6.4 Everything else about the response

**Every mode is capped and reports `truncated`**, the nomination cap's treatment applied
verbatim. `all` over an append-only journal fed by every ingest is precisely the
unbounded response the nomination cap capped four lists for, and designing it uncapped the day
after would be perverse. As there: when a list is named in `truncated`, act on
what came back and review again rather than raising the number.

**Every mode reports how many rows had no judge**, and only the modes that are
*about* who judged exclude them by default — `by_agent` cannot answer without
one, `all` and the difficulty ordering never needed one.

> **Revised 2026-08-23.** This read *"every mode excludes pre-attribution rows
> by default"*, which followed from the reading §3.3 has now reversed. With
> blank meaning **unknown** rather than **legacy**, a blanket exclusion would
> hide most of the corpus on exactly the graphs that chose not to require a
> judge — leaving review nearly blind on the population it is most useful for.
> The count stays: three results out of four hundred unattributed rows is not
> the same answer as three out of four.

**Confirming costs something, or the treadmill moves up a level.** If agent 2
reviews a decision and agrees, and nothing records that, agent 3 does the same
work again — the defect of §1, one layer higher. So confirmation writes a
`DecisionRecord` with `reviews` set, through a named writer:
**`apply_review(confirmations=[…], reversals=[…])`** — `review()` stays
read-only, and none of the existing decision tools can write this.

This follows `judge_importance`, whose `importance_judged_at` moves on
re-confirmation, and whose `stale_judgment` archival class exists to stop an
unrevisited judgment protecting a node for ever (`docs/REFLECTION.md` §5).

> **Revised 2026-08-23, on building it: `reversals` is `dissents`, and it
> reverses nothing.** Every undo already has a tool with its own refusals and
> its own row that legitimately sets `supersedes`; a dispatcher over four of
> them is the misdirected-write scope's fan-out. So a dissent sets only `reviews` — a row claiming to
> supersede a decision whose effect still stands would put the journal in
> disagreement with the graph (§4.2) — and its real use is where the undo was
> **refused**, which is the case this section had not considered. §10.6's second
> amendment has the rest.

### 6.5 Every verdict needs a writer, including the ones review invents

**This document's own flagship example had no action behind it**, which is the `assessed` edge's
defect reborn one layer up. §3.1 offers *"agent-1 called 44 facts `state`; two
look like events"* as what review recovers — and **nothing can act on it**.
`update` takes `new_content` only (`tools.py:1474`); there is no path that
changes a `claim_kind`. A review that nominates a defect nobody can fix is a
verdict with no action, which is the exact shape §1.1 tabulates.

**Supersession is not the missing path and must not be pressed into it.**
`update` requires `because` being *it was wrong* or *the world changed*, and a
mislabelled `claim_kind` is neither: the claim was right and the world did not
move — **the judgment about the claim was wrong**. Filing it as a correction
retires a true node and re-points its edges, which is the forgetting the validity model exists
to prevent, for a metadata mistake.

So the missing writer is narrow, and its narrowness is the design:

```python
async def rejudge(
    node_id: str, storage: StorageBackend, *, judge: JudgeRef, because: str,
    claim_kind: ClaimKind | None = None,
    confidence: float | None = None,
    confidence_basis: str | None = None,
) -> tuple[dict, ResponseMeta]:
    """Revise an agent-supplied judgment about a node, without touching the claim.

    Never a supersession: the node content is unchanged, so nothing was
    corrected and nothing moved on. Writes a `DecisionRecord` whose `reviews`
    points at the record that made the original judgment, and moves no status,
    no edges and no lineage.
    """
```

`judge_importance` already is this tool for one field, and its shape — a
judgment, a reason, a clock — is the one to copy. The re-judgment is checkable
against the material because the segments are still stored, which is what makes
this a review that can conclude rather than only complain.

**Ordering note for the build:** this belongs with step 7, and step 7 is where
review modes become useful. Shipping the modes without it delivers a reviewer
that can find every ingest-time mistake and fix none of them.

#### 6.5.1 Scope, surveyed rather than assumed

Everything an agent supplies at ingest, and where revising it belongs:

| Judgment | Revised by | Why |
|---|---|---|
| `claim_kind` | **`rejudge`** | no path exists today; the motivating case |
| `confidence`, `confidence_basis` | **`rejudge`** | same shape — a prior with a stated reason |
| `importance` | `judge_importance` (**exists**) | already a judgment-with-a-clock; duplicating it would be two writers for one field |
| metacontext assignment | **nothing — a real gap, deliberately not folded in** | see below |
| per-source validity intervals | **nothing — a real gap, deliberately not folded in** | see below |

**Two gaps surfaced by the survey, and both are left out on purpose.**

- **A frame cannot be withdrawn.** `link` adds a `HAS_METACONTEXT` edge and
  nothing removes one, so a fact wrongly framed as fiction stays framed. That
  is not a small mistake — frames gate `merge_refusal`'s cross-frame refusal and
  corroboration's `variant_of` exclusion — but it is a question about **frames**,
  not about ingest priors, and folding it into `rejudge` would put a
  load-bearing epistemic move behind a tool named for tidying metadata.
- **A validity interval cannot be corrected.** Intervals are supplied per source
  at ingest; `boundary_proposals` fills an **open** endpoint, and
  nothing revises a wrong one. Same reasoning: this is a question about
  **validity**, and the answer probably belongs beside the boundary machinery.

Both should be filed. Neither belongs in this document, and `rejudge` stays the
three fields above.

> **Filed as revisable ingest judgments and built 2026-08-27, as `reframe` and
> `correct_interval`.** The conclusion above — keep them out of `rejudge` — held.
> **The reason given was not the strongest one available**, and the better one is
> worth recording here because this is where the survey was made: the split is
> about **addressing**, not about a tool's name. `rejudge` takes a `node_id` and
> promises no status, edge or lineage moves. A frame revision *moves an edge* and
> changes what merges, what corroborates and what a frame-scoped search returns,
> so that promise would become false the day it grew a frame field. An interval
> belongs to a **(node, source) pair**, so folding it in would grow a `source_id`
> read for exactly one field — this repo's own tell that two tools are wearing
> one name.
>
> Two things review added before either was built. **`reframe` takes an optional
> `assign`**, so moving a claim from frame A to frame B never passes through
> *untagged* — where the claim is asserted in **every** frame, and where a failed
> second call would strand it. And **withdrawing a node's last frame is a
> promotion**, not something to forbid: the motivating case *is* a last-frame
> withdrawal, so a flat refusal would have left the tool unable to fix what it
> was built for. It takes `to_base_reality=True` as an acknowledgment instead,
> refused where it does not apply.
>
> Carry-forward: **a refusal that blocks the motivating example is a design
> error, not a safety feature.**
>
> The table above is also one column out of date: `rejudge` covers **five**
> fields, not three — `certainty` and `certainty_basis` arrived with step 7.

### 6.6 Review is per graph, and says so

> **Amended 2026-08-23 — the locator this section filed as `review`'s `elsewhere` is built, and it
> cost a turn rather than a query.**
>
> `review()` carries `elsewhere`: one count per other graph, zeros included, no
> rows and no ids. The rule below survives intact — the graph is the tag on a
> count read from it, never a field on a row.
>
> **What the design did not see is that the locator changes what kind of call
> `review` is.** Reading another graph on SurrealDB means borrowing the
> connection; borrowing takes the guard's mover turn; and `moving()` inside
> `using()` raises rather than deadlocking, because you cannot exclude
> the calls using the graph while being one of them. So `review` is now in
> `MOVES_THE_GRAPH` — a **read** that declares itself a mover. It excludes
> other tool calls and viz snapshots for its duration, and reads a single
> instant in exchange, which is what a journal read wanted anyway.
>
> **The scope rule, which is the part worth carrying past this document: a
> locator may overcount and must never undercount.** Only the filters
> `query_decisions` already implements are mirrored into the sweep —
> `agent_id`, `since`, `until`. `certainty_ceiling` and `mode="unreviewed"` are
> not, and `elsewhere.counted_with` says which ran. Every filter reimplemented
> for a second read is a place two implementations can disagree, and a locator
> that disagrees with the reader it points at is worse than one plainly wider:
> a count too high costs a wasted look, a count too low costs the look.

The journal is a per-graph table like every other, so `review()` answers about
one graph. The question that exposes it is the one review exists for: *"check
everything this judge did"* means *"…in this graph"*, and an agent that worked
in three graphs in one session cannot be reviewed in one question. That is
`WARNINGS_AND_SETTINGS.md` §5.2's *"the reviewing agent ends up unable to ask
one question"*, arriving through a door this design did not consider.

**Per graph is right, and the reason is the ids.** `subject_ids` holds node
ids, and a node id resolves only in the graph that holds it. A row filed
anywhere else carries ids that dereference nowhere, so a central journal would
have to store the graph name on every row and every reader would have to switch
graphs before it could act on one. The row belongs beside its subjects.

That also disposes of the forensic half of the misdirected-write scope. Where a write lands in the
wrong graph its journal row lands there too — *with* the material it describes,
which is where somebody who found the material is already looking. Nothing is
orphaned; what was lost was knowing which graph to open, and that is the hole
`expected_graph` refuses through (`INTEGRATION.md`).

**So `review()` takes no `graphs=` list, and the reason is not scope.** A
fan-out has to borrow the active database and give it back — the `viz_list_*`
pattern, which its own docstring calls unsafe under concurrent tool calls and
which the active-graph guard has documented since July. Doing it by hand is *safer*: `list_graphs`,
then `use_graph`, then `review()`, once per graph, where each switch is the
active state rather than one borrowed mid-call. A convenience less safe than the
sequence it replaces is not a convenience.

**What review owes instead costs nothing: every response names the graph it
answered from.** Silent scoping becomes stated scoping, which is the whole of
§5.2's complaint — a reviewer who can see the answer is one graph wide can
widen it, and one who cannot, cannot.

**What would genuinely close it is a locator, not a reader.** *"agent-1 also has
12 decisions in `field-notes`"* — a count per graph, no rows, no ids to dereference —
turns *there is more elsewhere* from something the reviewer has to think of into
something it is told, and leaves the reading where it is safe. It needs a
cross-graph read that does not move the active database, which does not exist
and cannot be added the obvious way, because a second connection to an embedded
URL is a second store. Filed as `review`'s `elsewhere`, gated on the guard.

**One rule for whoever builds it: the graph is not a field on the row.** A
merged listing tags each row with the graph it was *read from*. Stored, it would
be free to disagree with where the row actually lives — a restored archive, a
copied database — and per-edge-type migration, the drifted lookup tables and the drifted lookup tables are three instances of what that costs.

---

## 7. Reversing a merge

Undo was out of scope in the first draft. It is in scope now because of a
property that only shows up when you look at what a merge leaves behind:
**The information reversal needs is destroyed at merge time and is not
reconstructible afterwards.** So the decision is not *build undo or don't* —
it is *capture or lose*, and it has to be made before the next merge, not
before the undo.

### 7.1 What a merge destroys

`merge_nodes` migrates every knowledge edge onto the survivor, collapsing
duplicates by `(src, dst, type)`. Nothing records which source each migrated
edge came from. Measured on the five merges of 2026-08-21: **the ten retired
sources hold zero knowledge edges between them** — all 24 sit on the survivors,
with no record of the partition. Where two sources cited the same document, the
two `sourced_from` edges genuinely collapsed into one.

`metadata.merged_from` names the nodes that merged. It does not name their
edges, and no later pass can recover them. **This is `claim_kind`'s shape
again**: information that exists at one moment only, with an island that grows
with every merge taken before it is captured.

### 7.2 Where merge information lives today

Distributed across three places, none of them complete:

| Where | What | Grows with |
|---|---|---|
| survivor node, `metadata.merged_from` | the ids that merged into **this** survivor | nothing — set once |
| each retired source, `lifecycle` | `{because: merged, counterpart: <survivor>, retired_at}` | every merge that node takes part in |
| graph edges | `merged_into` (source→survivor), `evidence_merged` (to dependents) | every merge |

> **Corrected 2026-08-22.** This table previously said `merged_from` was
> "appended per merge, unbounded". It is not: `merge_facts` constructs a **new**
> `Fact` on every call with `merged_from` set once at construction, so a single
> survivor's list never grows.
>
> **What grows is the chain**, and that is what §7.4 bounds. `A+B→S1`, then
> `S1+C→S2`: `S1` is retired `MERGED` and linked to `S2` by `merged_into`, so
> unwinding `S2` back to `A, B, C` needs `S2`'s partition *and* `S1`'s. Depth is
> a property of the lineage, not of any one node's list — which is what the
> user's original framing said and this table did not.

### 7.3 The split: audit in the journal, payload on the node

The two halves of "what happened in this merge" have different lifetimes and
different readers, so they live in different places:

| | Lives in | Size | Lifetime |
|---|---|---|---|
| **Audit** — who merged, when, why, how certain | `DecisionRecord` (§4) | small | permanent |
| **Payload** — the pre-merge edge partition | the survivor node | ~190 B/merge, measured | bounded, §7.4 |

Putting the payload on the node rather than in the journal is the user's design
and it is better than the global ring first proposed here, for a reason the
alternative got backwards. A working session of ~55 merges touches ~55
*distinct* survivors, one entry each — so under a per-node bound nothing evicts
and the whole session stays reversible, where a global ring of the same nominal
size would have thrown away its tail.

### 7.4 The bound

**`merge_undo_depth`, default 10, a per-graph setting** — the per-graph override
pattern, no singleton. Each survivor carries the partition of the one merge that
created it; the setting bounds **how far back along the `merged_into` chain
those partitions are retained.** On each merge, walk the chain back from the new
survivor and clear `merge_undo` on any ancestor deeper than the limit. Ten
levels of a lineage stay reversible; the eleventh and beyond become permanent.

> **Shipped 2026-08-22 as an injectable default, not yet a stored per-graph
> override.** `merge_nodes(..., undo_depth=DEFAULT_MERGE_UNDO_DEPTH)` — explicit
> at the call site, overridable by any caller, no singleton — with the
> `get/set_merge_undo_depth_override` storage pair and its `configure_*` tool
> deferred to step 0c.
>
> **The reason is that the pair would have shipped with no writer**, which is
> The `assessed` edge's exact shape and was closed three commits earlier: a stored setting whose
> only user-facing surface is a tool that configures reversal, in a build where
> reversal does not exist yet. The cost of waiting is bounded and self-limiting
> — a graph would need an **eleven-deep** merge chain built between 0a and 0c to
> lose anything a higher setting would have kept, against measured chains of
> depth 1, and nothing could have been reversed in that window anyway. So no
> capability that existed is lost, which is the test this whole section applies.

**Ten, and not more, because of what the bound actually targets.** It is not
storage: merge does not shrink this graph. Sources are retired, not deleted —
all ten from 2026-08-21 are still present as `MERGED` husks keeping their
content and their 384-dimension vectors, so what a merge already retains and
never reclaims exceeds the undo payload it adds. Net node count rose across that
session, 558 → 638.

> **Measured on building it, 2026-08-22, and the earlier figure was wrong.**
> A representative merge — two sources, five migrating edges, matching the 4.8
> knowledge edges per merge measured on the real graph — captures **1,650 B**,
> about 330 B per edge, against **~4.1 KB** of husk and vector the same merge
> already retains for ever. So the ratio is roughly **2.5×, not the ~18× this
> paragraph first claimed from a ~190 B estimate**. That estimate had counted a
> hand-listed field subset; the payload stores the whole edge, which is the
> decision §7.9 makes and defends, and uuid ids, ISO timestamps and the
> `MergedEdge` wrapper account for the rest.
>
> **The conclusion is unchanged, and it is worth saying why rather than just
> asserting it.** The payload is still smaller than what a merge already keeps,
> and the bound was never aimed at bytes — it is aimed at the single claim that
> keeps absorbing restatements, whose chain grows without limit. But a reader
> raising `merge_undo_depth` a long way should now do it against 1.6 KB per
> level, not 190 B.
>
> **Deliberately not shrunk with `exclude_defaults`**, which would cut most of
> it: an omitted field is re-supplied from *today's* default when the payload is
> replayed, so a default changed next year would silently alter a replay of an
> old merge. That is the "partial copy" hazard §7.9 rejects, wearing a
> different hat.

What the bound targets is the case where a single claim keeps absorbing
restatements — merge document 3's phrasing into the survivor, then document 4's,
then document 5's. That is the **expected** pattern for anything frequently
restated rather than a pathology, and today that node's history grows without
limit. Ten levels of reversibility on one claim is generous; chains are rare
enough that all five merges of 2026-08-21 were depth 1.

**What eviction discards, stated plainly because this is the first structure in
the system that deliberately forgets.** `lifecycle` and W&S's `notes` are both
unbounded append-only lists, so a bounded one beside them will otherwise read as
an oversight. What is dropped is **reversal capability, never a claim**: every
merged source node, its content, its provenance, its lifecycle episode and its
`merged_into` lineage edge all remain exactly as before. The graph forgets how
to replay an edge migration automatically. It forgets nothing it knows.

**It also settles the archival interaction**, which the global-ring design could
not. Archive the survivor and its payload goes with it — no dangling promise, no
undo buffer pinning nodes against the graph's own cleanup, no staleness check to
write. That self-maintenance is what node-attachment buys, and it is the second
reason to prefer it.

### 7.5 The guard that is not a setting

Depth bounds *how far back*, and says nothing about *whether it is safe*.
Reversal is refused — with a reason, as every refusal here is — when something
has come to depend on the survivor:

- the survivor has itself been merged again, or superseded;
- **it carries any edge that is neither in the undo payload nor written by the
  merge itself.**

**The second is a set difference, not a list, and that is deliberate.** An
earlier draft named only *inferences drawn on the survivor's own wording*, which
missed everything else that can accrue to a live node: a contradiction recorded
against it, a `distinct` verdict assessed against it, a tag, a user relation.
Reversal ends in `delete_node`, so an edge the guard does not notice is an edge
the reversal destroys — and **a contested claim losing its contest record** is
precisely the loss *nothing is destroyed* exists to prevent. Stated as a
difference, an edge type invented next year is refused by default rather than
deleted by omission.

**No configured value raises past this.** `merge_undo_depth` is policy; this is
correctness, and a reversal that ignored it would leave dependents resting on
text that no longer exists.

### 7.6 What a reversal restores

**The principle (user's, 2026-08-22): reversing returns the graph to the status
it had before the merge, and reversing back and forth N times is
indistinguishable from doing it once.** Every flag the merge set is returned.

| The merge did | Reversal does | Exact |
|---|---|---|
| moved A and B's knowledge edges onto S | replays the captured partition, **splitting** an edge that collapsed when both cited one document | ✅ — what §7.1's capture is for |
| A, B status → `MERGED` | → `ACTIVE` | ✅ |
| wrote `merged_into` A→S, B→S | deletes them | ✅ |
| wrote `evidence_merged` on dependents | deletes them | ✅ |
| appended a lifecycle episode to A and B | closes it with `restored_at` | status ✅, history appended |
| created survivor S | **deletes it** (§7.7) | ✅ |
| — | appends a reversal `DecisionRecord` | new, by design |

**No new flag is raised on the dependents, and that is the principle working
rather than an omission.** The merge re-pointed each dependent's `derived_from`
onto S and flagged it `evidence_merged` — *your premise was reworded, go re-read
it*. Reversal re-points it back to the premise it was actually drawn from, so
the inference is returned to the state it was in before and there is nothing for
a reader to re-read. A flag here would assert a change the reversal has just
undone. `evidence_merged` keeps its name and its single meaning.

**One boundary, because it is the only place exactness does not hold: status is
restored, history is appended.** `lifecycle` is append-only by design — a node
leaving the active set twice is the Saint Petersburg case the validity model legalised — so a
merge/reverse cycle leaves a closed episode behind, and the journal keeps both
decisions. That is the record that it happened, which is not a flag and is not
returned.

---

### 7.7 The survivor is deleted, and `delete_node` is never exposed

**Reversal deletes S rather than retiring it** (user's decision, 2026-08-22).
Two reasons, and the second is the stronger one:

1. **Exactness.** Retiring leaves one husk per merge/reverse cycle, each keeping
   its own 384-dimension vector, visible to `include_corrected` searches and to
   archival nomination. N cycles would stop being equal to one, and §7.6's
   principle would hold only approximately.
2. **A later re-merge must synthesise afresh.** If the same sources are merged
   again, the new survivor should be written from what is known *then* — more
   sources, a different judge, a later reading. Resurrecting the old S would
   silently import a previous agent's wording into a decision nobody made with
   it. This also rules out the tempting optimisation of *reusing* a retired
   survivor on re-merge, and rules it out on principle rather than for being
   fiddly.

**Nothing knowable is lost.** S's content is `extraction_method: "agent:merge"`
— a synthesis sourced from no document. Every claim it carried is back on the
sources with their own provenance, and the merge itself stays in the journal.

**This needs `delete_node` on the storage protocol, which nothing has ever
had.** `delete_edge` exists; no node has ever been hard-deleted, because
retirement is how this system removes things and archival is an export.

> **Built 2026-08-22, and *without* a `delete_node` method — deliberately.**
> The deletion lives inside `reverse_merge_tx`, with the never-expose note on
> that protocol method and on both backends' implementations. Two reasons, and
> the second is the stronger:
>
> 1. **Atomicity.** A standalone `delete_node` called after the reversal
>    transaction would put the one irreversible step outside the rollback. The
>    delete has to be *in* the transaction, so the transaction is where it goes.
> 2. **The safest way to never expose a hard delete is not to have one.** A
>    public `delete_node(node_id)` guarded by a comment is a capability plus a
>    request not to use it; no such method is the same protection without
>    relying on the next reader agreeing. This is a stricter reading of the
>    instruction below rather than a departure from it — the note is in all
>    three places the instruction names, attached to the code that actually
>    deletes.
>
> `InMemoryStorage` keeps a module-level `_destroy_node` helper carrying the
> same note; SurrealDB emits the DELETE statements inline. Both **refuse** a
> survivor that still has edges rather than dropping them, so a guard bug fails
> loudly inside a transaction that rolls back instead of silently destroying the
> contradiction the guard was meant to protect.

> **`delete_node` must never be reachable from an MCP tool, and the note
> belongs in three places** (user's direction): on the protocol method itself,
> on **both** backend implementations, and here. Reversal is its only caller.
> Without that, a system whose central rule is *nothing is destroyed* has
> quietly acquired *delete anything*, and the next person wanting a hard delete
> will find it already built.

What makes it safe is §7.5's guard: by the time reversal is permitted, the only
edges pointing at S are ones the merge itself created.

### 7.8 Futile cycles

A merge reversed, re-made, reversed again is an agent burning tokens on an
oscillation nobody wants. It is not expected — but it is **hard to catch after
the fact and cheap to catch now**, which is the whole case for building it
before it happens.

**The signal already exists and needs no new storage.** Every merge appends a
`LifecycleEpisode` to each source with `because: MERGED`; every reversal closes
that episode with `restored_at`. So one completed cycle leaves one **closed
`merged` episode** on each source, in an append-only list that is never trimmed:

```python
def completed_merge_cycles(node: EpistemicNode) -> int:
    """How many times this node has been merged and then brought back."""
    return sum(
        1 for episode in node.lifecycle
        if episode.because is NodeStatus.MERGED and episode.restored_at is not None
    )
```

**It is also free at the point of use.** `merge_facts` already loads every
source node before calling `merge_refusal`, so `lifecycle` is in hand — the
check costs no round trip and no extra field.

**Counted per node, not per pair.** Pair matching would miss `A+B`, then `A+C`,
then `A+D` — the same node oscillating against different partners. Per-node
catches that, on data that is there either way.

**`merge_cycle_limit`, default 2, a per-graph setting.** One merge-then-reverse
is an ordinary correction. Two can be two judges disagreeing. The third attempt
is oscillation, and that is where the merge refuses:

> **Built 2026-08-22 (step 0b), with the same setting deferral as §7.4.**
> `merge_refusal(..., cycle_limit=DEFAULT_MERGE_CYCLE_LIMIT)`, threaded through
> `merge_facts(..., merge_cycle_limit=...)`; the stored per-graph override and
> its tool land with 0c. **The gate cannot fire until then**, because nothing
> writes `restored_at` — reversal is the only writer — so the count is zero on
> every fact in both real graphs. It is built now because the episodes it reads
> are being written now, and a limit added after an oscillation has run has
> nothing to look at.
>
> **That dormancy is what makes the deferral safe, and it is the load-bearing
> check.** The refusal message tells the agent the limit is configurable, so the
> escape hatch has to be real or a legitimate third merge is blocked with no
> recourse — worse than the oscillation. By the time any fact can reach a
> non-zero count, 0c has shipped the setting. **0c must therefore ship the
> override and the tool**, not merely reversal; §10's row says so.

```
this fact has already been merged and un-merged 2 times, which is the
`merge_cycle_limit` for this graph. Merging it again is likely to be
reversed again. Ask the user before proceeding — and if the merge is
right, the limit is a per-graph setting.
```

**Refusal rather than a warning, deliberately.** `merge_refusal` already returns
prose the caller must act on, and `docs/REFLECTION.md` §1 is explicit that the
agent handles mechanical calls and **escalates consequential ones to the
human**. A warning is something an agent reads and proceeds past, which is the
failure this is for.

**Accepted gap:** an agent could evade the check by merging a different source
set. Recorded rather than closed — the simple version is worth having, and a
system that tried to detect deliberate evasion here would be solving a problem
nobody has.

### 7.9 Implementation notes

**✅ Built 2026-08-22** (step 0c). What follows is the design; the amendments
record where building it changed something. Two things are **not** built and are
waiting on later steps, both noted below: the reversal `DecisionRecord` (needs
§4's journal, step 5) and the `judge` argument (needs §2's registry, step 2).

Enough to build from, in the order the pieces depend on each other.

**The payload type.** Store the edge *values*, not references: migration
collapses duplicates by `(src, dst, type)`, so the original rows may no longer
exist to point at.

```python
class MergedEdge(BaseModel):
    """One edge exactly as it stood before the merge moved it.

    **The whole edge, field by field, and never a hand-listed subset.** Build it
    with `edge.model_dump(exclude={"id"})` — the shape `_migrate_edges_inplace`'s
    copy branch already uses — so a field added to `NodeEdge` later is carried
    without anyone remembering to come back here.
    """
    owner_id: str                 # which merging source it belonged to
    edge: dict                    # NodeEdge.model_dump(exclude={"id"})
    intra_set: bool = False       # §7.9, "the edge the merge deleted outright"


class MergeUndo(BaseModel):
    """Everything needed to replay one merge backwards."""
    source_ids: list[str]
    edges: list[MergedEdge]
    merged_at: datetime
    decision_id: str | None = None      # the DecisionRecord (§4)
    # The survivor's wording, kept because `delete_node` removes the node that
    # held it. Without this a reversal cannot say what it withdrew, and the
    # contested text is unquotable the moment the reversal lands.
    survivor_content: str = ""
```

> **A hand-listed field subset was the first draft of this and it was wrong.**
> It named `src_id`, `dst_id`, `type`, `label`, `kind`, `weight` and `validity`
> — omitting `metadata` (`types.py:761`) and `created_at` (`:762`). Since §3.1
> puts `judged_by` for similarity, assessed, contradiction and variant decisions
> **in edge metadata**, a merge→reverse cycle would have replayed every edge
> with its attribution stripped and its date reset. **Reversal would have
> deleted the judge** — in the document whose entire purpose is recording one.
> The lesson generalises past this field list: *a partial copy of a model is a
> bug with a delay on it.*

**Intra-set edges need capturing too, and are easy to miss.** A **migrating**
edge **between two merging sources** becomes a self-loop, and
`_migrate_edges_inplace` (`memory.py:~509`) drops it outright rather than
collapsing it — so it is neither on the survivor nor on the sources afterwards.
It must be captured with `intra_set=True` and recreated on reversal. This is the
one edge class that is gone *immediately*, not merely re-pointed.

> **Amended 2026-08-22, on building the capture.** This paragraph's example was
> *"a `similarity` or `contradiction` edge between two merging sources"*, and
> The anchoring rule has since made that the one case it is **not**. Judgment edges answer
> `keep`, so the migration loop skips them before it reaches the self-loop
> branch and they survive intact on the retired sources. The mechanism is still
> needed — a user `related` edge, a `supports` edge between two merging facts —
> and a test asserts the edge is gone from the sources *and* the survivor, so it
> is checking the destruction it claims rather than assuming it.

**Where it lives.** `metadata["merge_undo"]`, parsed through `MergeUndo` on read
and write. `Topic`, `Fact` and `Inference` share no base class — each redeclares
`lifecycle`, `value` and `metadata` — so a typed field would have to be added
three times for a payload that only ever exists on merge survivors. `metadata`
already carries `merged_from`, and `merge_nodes` is generic over
`EpistemicNode`, so topics merged through `apply_reflection` get the same
treatment for free.

**Capture point.** `merge_nodes` (`pipelines/graph_construction/versioning.py`),
before migration mutates anything: build `MergeUndo`, assign it onto the merged
node's `metadata`, and let the existing `merge_nodes_tx` call persist it — so
the payload lands in the same transaction as the merge, with no extra write.
Then walk the lineage back from the new survivor and clear `merge_undo` on
ancestors past `merge_undo_depth`.

> **One correction, made on building it.** This said `merge_nodes` "already
> reads every source's edges to migrate them". It does not — *`merge_nodes_tx`*
> does, inside each backend. So the capture adds two reads per source
> (`get_edges_from` + `get_edges_to`), pre-transaction, on the same
> single-connection assumption `_plan_copied_edges` and `merge_nodes_tx` already
> make. Cheap, and a merge is rare; capturing inside the two backends instead
> would have meant implementing the policy twice.
>
> Eviction runs **after** the transaction, so a merge that fails evicts nothing;
> it is idempotent, so a failure there costs at most a payload kept one merge
> past the bound. It walks `MergeUndo.source_ids` rather than `merged_into`
> edges — same lineage, already in hand, one `get_nodes` per level. In practice
> the walk terminates immediately: every merge on both real graphs is depth 1.

**The protocol.** As built, one method rather than two — see §7.7 for why the
deletion is not a `delete_node` of its own:

```python
async def reverse_merge_tx(
    self,
    survivor: EpistemicNode,
    source_nodes: Sequence[EpistemicNode],
    restored_edges: Sequence[NodeEdge],
    *,
    restored_at: datetime,
    delete_edge_ids: Sequence[str],
) -> None:
    """Atomically undo one merge: put the sources back and destroy `survivor`.

    **This is the only hard delete in the system.** Never expose a general node
    delete through an MCP tool: a system whose central rule is *nothing is
    destroyed* must not quietly acquire *delete anything* (§7.7).
    """
```

The same paragraph goes on both `InMemoryStorage` and `SurrealDBStorage`
implementations — the standing rule is the full protocol on every backend, and
a guard stated only on the protocol is a guard the next implementer does not
read.

The backend applies a plan; it does not build one. `reverse_merge` in
`pipelines/graph_construction/versioning.py` runs the guard, replays the payload
into `restored_edges` and collects `delete_edge_ids`, so the two backends cannot
develop different opinions about what a reversal means.

**The reversal.**

```python
async def reverse_merge(
    survivor_id: str, storage: StorageBackend,
) -> ReverseRefused | dict:
```

> **As built, without `judge`.** The registry is step 2 and `JudgeRef` does not
> exist yet; adding the parameter now would mean either a type with no contents
> or a string nothing validates. It arrives with steps 3–4, which thread the
> judge through every write path at once — one change rather than a parameter
> that means nothing until then.

1. Load the survivor. Refuse if it is not `ACTIVE`, or carries no
   `merge_undo` — the latter meaning either it was never a merge survivor or
   its payload aged past `merge_undo_depth`, and **the refusal must say
   which**, since one is permanent and the other is a mistake.
2. **§7.5's guard, and it is broader than "inferences".** Refuse if the
   survivor has an outgoing `merged_into` (it was merged again), is superseded,
   or **carries any edge that is neither in the payload nor written by the merge
   itself**. The narrow inference-only version of this check was wrong: a
   contradiction recorded against `S` after the merge, a `distinct` verdict
   assessed against it, a tag, a user `RELATED` edge — none is in the payload,
   none is merge-created, and `delete_node(S)` would erase every one of them
   silently. **A contested claim losing its contest record** is exactly the loss
   *nothing is destroyed* exists to prevent. The check is therefore a set
   difference, not a list of edge types, so an edge type added later is refused
   by default rather than deleted by omission.
3. Load the sources. Each must be `MERGED` with an open lifecycle episode whose
   `counterpart` is this survivor.
4. **One transaction**, on both backends:
   - recreate each `MergedEdge` on its `owner_id`, splitting any edge the merge
     collapsed;
   - delete the edges the merge left on the survivor;
   - sources → `ACTIVE`, closing the open episode with `restored_at`;
   - recreate any `intra_set` edges the merge deleted outright;
   - delete the `merged_into` and `evidence_merged` edges the merge wrote;
   - `delete_node(survivor_id)` **and its `EmbeddingRecord`** — the vector is
     stored per item, so deleting the node alone strands an entry the index
     still returns. `delete_node` owns the cascade; it is not the caller's to
     remember;
   - append a reversal `DecisionRecord` with `reviews` and `supersedes` set,
     carrying `survivor_content` so the withdrawn wording stays quotable after
     the node holding it is gone. **(Deferred to step 5** — the journal does not
     exist yet. `reverse_merge` returns `survivor_content` in its result today,
     so the wording is not lost; what is missing is the durable record, which
     arrives with the type that holds it.)
5. Return what changed, in the shape `merge_facts` returns.

**The cycle check** goes in `merge_refusal`
(`pipelines/reflection/fact_dedup.py`), ordered with the other refusals —
**After** the permanent ones (cross-frame, event) and before the similarity
bar, since it is fixable by a human decision rather than by the graph changing.

*(An earlier draft listed **unjudged** among the permanent refusals. It is not:
`fact_dedup.py`'s own header names it the fixable one — "an unjudged pair merges
as soon as somebody judges it" — which is why the refusals are ordered
permanent-first in the first place. Two descriptions of one rule, caught before
either was built.)*

**Tests.** `tests/pipelines/test_merge_reversal.py`:

- merge → reverse restores every source to `ACTIVE` with its original edges,
  including the case where two sources cited **one** document and the
  `sourced_from` edges collapsed;
- merge → reverse → merge → reverse leaves the active graph identical to one
  cycle, and `lifecycle` two episodes longer (§7.6's boundary);
- the survivor is gone from `get_node` and from search on both backends;
- each guard in step 2 refuses, with a distinguishable reason;
- a payload cleared by `merge_undo_depth` refuses differently from a node that
  never had one;
- the third merge of an oscillating pair refuses on `merge_cycle_limit`, and
  raising the setting lets it through;
- **an intra-set edge survives the cycle**: `A` and `B` joined by `similarity`,
  merged, reversed — the edge is back. It is the only class the merge deletes
  rather than re-points, so nothing else in this list would catch its loss;
- **edge `metadata` and `created_at` survive the cycle**, `judged_by`
  specifically — the defect the first draft of `MergedEdge` would have shipped;
- reversal **refuses** when `S` carries a post-merge contradiction, assessed
  verdict, tag or user edge, rather than deleting it;
- the survivor's `EmbeddingRecord` is gone after reversal and no search returns
  it;
- a partial failure mid-transaction leaves the graph as it was, on both
  backends.

---

## 8. What this deliberately does not do

- **It does not verify anybody.** §2.4. Descriptions are self-reported and the
  system says so wherever it shows one. Only `confirmed_at` carries human
  weight, and §2.3 is what lets it.
- **It does not weight anything by judge.** *"Score facts higher when a trusted
  agent judged them"* is the natural next request and is refused: the input is
  self-reported prose, so a ranking built on it is one any agent can move by
  describing itself differently. Corroboration counts publishers because a
  publisher is a property of the **document**; a judge is a property of the
  **claimant**.
- **It does not re-open applied changes as a matter of course.** Review
  *nominates* a decision for another look; reversing one is a separate, explicit
  act (§7), bounded by depth and refused outright where something has come to
  depend on the result. A merge older than `merge_undo_depth`, or one whose
  survivor has since been built on, is **not** reversible, and `all` mode says so
  rather than implying an undo it cannot offer.
- **It does not backfill.** §3.3.
- **It does not run on its own.** Same rule as `reflect`: a review nobody asked
  for, over a graph nobody was looking at, is consolidation by timer.

---

## 9. Node notes are decision records (folding in W&S §9)

`WARNINGS_AND_SETTINGS.md` §9 (decided 2026-08-21) gives every node an
append-only `notes` list, each `NodeNote` carrying `reviewed_at` and a verdict;
§5.3 makes `contested_decisions` a reflect list scanning for notes without one.

That is a second review-state machine with a second *"what has nobody looked
at"* scan, and an agent proceeding past an advisory would write into both.
**Decided 2026-08-22: one machine.**

| W&S §9 | Becomes |
|---|---|
| `NodeNote` | `DecisionRecord(kind="proceeded_despite_advisory")` — the kind is added when advisories are, not before (§10.5) |
| `node.notes` | a derived view over records whose `subject_ids` contains the node |
| `NodeNote.reviewed_at` | gone — derived from `reviews`, per §3.4 |
| §5.3 `contested_decisions` | `review(mode="advisory", unreviewed=True)` |

"I was warned and proceeded anyway" is a judgment with a judge, a date and a
subject. It was a separate type only because it was designed a day before the
journal existed.

**W&S §5.2's own argument is why**: two shapes for one question is how *"the
reviewing agent ends up unable to ask one question"*. `WARNINGS_AND_SETTINGS.md`
needs a dated amendment pointing here; it is neither built nor started, so this
costs a paragraph rather than a migration.

---

## 10. Build order, and what each step involves

Each step is useful alone, and each is a precondition for the next.

| # | Step | Why here |
|---|---|---|
| **0a** ✅ | **`merge_nodes` captures the pre-merge edge partition** as `MergeUndo` on the survivor, with chain eviction past `merge_undo_depth` (§7.4, §7.9) | **Capture or lose.** The partition exists only at merge time (§7.1), so every merge taken before this lands is permanently irreversible. The only step with a deadline. **Built 2026-08-22**; the five merges of 2026-08-21 predate it and stay irreversible. |
| **0b** ✅ | **`merge_cycle_limit` in `merge_refusal`** (§7.8) | Same file, same sitting, no new storage — the lifecycle episodes it counts already exist. Cheap now, and near-impossible to reconstruct once an oscillation has run. **Built 2026-08-22**; live since 0c, which is what writes the `restored_at` it counts. |
| 0c ✅ | `reverse_merge_tx` on the protocol and both backends (the hard delete lives *inside* it, §7.7), plus `reverse_merge`, the `reverse_merge` and `configure_merge` tools, and the two stored per-graph settings | Needs 0a to have run for anything to be reversible. Carries the never-expose guard in all three places. **The settings are not optional here**: 0b's refusal tells the agent the limit is configurable, and this is the step where that stops being dormant, so shipping reversal without them leaves a promise the code does not keep. **Built 2026-08-22**, minus the reversal `DecisionRecord` (step 5) and the `judge` argument (steps 2–4). |
| 1 ✅ | `apply_reflection(similarities=[…])` + `ASSESSED` edge | the `assessed` edge's fix. Stops the re-nomination treadmill, and gives corroboration its first real input — **only from `one_claim` verdicts** (§1.2). Independent of everything below. **Built 2026-08-22**, with three refusals the design did not name and one ordering rule it did not state; see §10.2's amendment. |
| 2 ✅ | `agent` table, approved-id settings, `claim_agent`, approval over `ctx.elicit` with `epimemer agents confirm` as fallback | Registry with nothing yet pointing at it. Full protocol on both backends, per the standing rule. **Built 2026-08-22**, with one gate split in two and one seeding rule widened; see §10.3's amendment. |
| 3 ✅ | `judge` threaded through the reflect-side write paths | Smallest surface producing attributed decisions, so step 5 has something to read. **Built 2026-08-23**, with two writers the design's list had missed and one field shape changed; see §10.4's amendment. |
| 4 ✅ | `judge` threaded through ingest (§3.2), plus the require-a-judge setting (§3.3.1) | The bigger churn, and where the unreviewable priors are. **No cutover** — the setting decides, per graph, and ships default-off, so an upgraded server keeps writing exactly as before. **Built 2026-08-23**; the sessionless escape hatch took a different shape from the one this section proposed, see the amendment below. |
| 5 ✅ | `DecisionRecord` + journal writes + W&S §9 folded in (§9) | Makes *"what did this agent judge"* one query. **Built 2026-08-23**, with `kind` carrying `because`, one writer deferred and `certainty` left without a tool that supplies it; see §10.5's amendment. |
| 6 ✅ | `review(mode="all")`, capped, with tier-2 ordering (§6.2) | Works on the existing corpus and orders it usefully, since derived signals need no attribution. **Built 2026-08-23**, with tier-1 ordering pulled forward and one thing the design did not foresee: the journal is younger than the graph, so there is almost nothing to review yet. See §10.6's amendment. |
| 7 ✅ | `by_agent`, `since`, `unreviewed`, tier-1 ordering, `certainty_ceiling`; `apply_review`, `rejudge` | Need attributed decisions to exist; useful from the first session after step 4. **Built 2026-08-23**, with `advisory` refused rather than shipped, `reversals` renamed to `dissents` for a reason that changed what it does, and the batch left un-transactional against §10.7; see §10.6's second amendment. |

**Steps 0a and 0b go first, and not because anything below needs them** —
nothing does. They are first because they are the only steps whose cost rises
while they wait: both record something that exists at merge time and nowhere
else. 0c can follow whenever. Steps 1 and 2 are independent of all of them and
of each other.

### 10.1 Step 0 — capture, cycle limit, reversal

§7.9 has it in full: the `MergedEdge` / `MergeUndo` types, where the capture
goes inside `merge_nodes`, the `delete_node` protocol addition and its
never-expose guard, the five-step reversal algorithm, where the cycle check
sits in the refusal ordering, and the eight tests.

### 10.2 Step 1 — `similarities` and the `ASSESSED` edge

> **✅ BUILT 2026-08-22.** `EdgeType.ASSESSED` in `REVIEW_EDGE_TYPES`,
> `pipelines/reflection/similarity_decisions.py`,
> `apply_reflection(similarities=[…])`, `ALREADY_JUDGED_EDGE_TYPES` in
> `contradiction_detection`, and 57 tests over both backends. Corroboration is
> untouched, and a test asserts an `assessed`-only pair does not corroborate.
> **Five things the design below did not say, four of them found by building it:**
>
> 1. **`NOMINATED_STATUSES`, not "active".** The design said an unknown or
>    retired id is skipped. That is too broad by exactly half the problem: the
>    recurrence sweep nominates **active/historical** pairs (`NOMINATED_STATUSES`
>    is `{ACTIVE, HISTORICAL}`), so refusing a historical side would leave the
>    treadmill running on the population where the graph is offering a claim
>    beside its own predecessor. Both sides must be in `NOMINATED_STATUSES` —
>    which is the rule stated properly rather than a widening: an `assessed`
>    edge earns its place by suppressing a nomination, so it belongs exactly
>    where a nomination could have happened. `CORRECTED`, `ARCHIVED` and
>    `MERGED` are refused, because a judgment there suppresses nothing and a
>    `similarity` edge would still be counted as support.
> 2. **`one_claim` is refused across frames.** §1.2 already routes a cross-frame
>    pair to `record_variant`, and `merge_refusal` already refuses one — but
>    nothing said what happens if the agent sends `one_claim` anyway. A
>    `similarity` edge across frames is a fiction corroborating a fact, and
>    corroboration only disqualifies a partner that carries a `variant_of`,
>    which this pair would not have. `distinct` across frames is accepted:
>    `assessed` corroborates nothing, so there is no reason to make the agent
>    choose between recording its judgment and being accurate.
> 3. **`distinct` after a standing `one_claim` is refused, not layered.**
>    Nothing in this system deletes, this call included, so the `similarity`
>    edge would go on corroborating a pair the agent had just disowned — while
>    the response reported success. Refusing costs no suppression (the standing
>    edge already suppresses) and surfaces a retraction nothing can yet perform.
>    **Filed as the `one_claim` retraction.** The opposite direction is additive and is allowed:
>    `one_claim` after `distinct` adds the `similarity` beside the `assessed`
>    that is already there.
>
>    > **Superseded 2026-08-23 by the `one_claim` retraction's fix.** `distinct` over a standing
>    > `one_claim` is now a **retraction**, not a refusal: it writes a
>    > `retracted_similarity` edge that disqualifies the standing one, which is
>    > the mechanism `corroboration.py` already runs for `contradiction`. The
>    > `similarity` edge is still not deleted, which is why this reads as a
>    > second edge rather than an undo. **The refusal moved to the other
>    > direction** — nothing re-asserts `one_claim` over a withdrawal — because
>    > the two are not symmetric: withholding support costs a count, inventing
>    > it inverts the quantity. Point 3's *"the opposite direction is additive"*
>    > still holds for a pair that was never `one_claim`.
> 4. **Similarities are applied *first* in `apply_reflection`**, before any
>    argument that can retire a node. This is the anchoring rule applied to
>    the order of one call: a judgment is about the wording it was made against,
>    and a supersession later in the same batch would otherwise turn it into a
>    skip — or, worse, leave the agent thinking it had been recorded.
> 5. **The frontend needed telling.** `EdgeType` growing a member that
>    `EDGE_MEANINGS` has never heard of is the drifted lookup tables exactly, one layer over: the edge
>    draws in unknown-kind grey. `assessed` gets the similarity hue drained of
>    saturation — same subject, no assertion of support — with a test that it is
>    neither the similarity colour nor the neutral.
>
> Two things the design got right and are worth keeping visible: the `because`
> rides on both edges' metadata until the journal lands (§3.4 permits it,
> because it is immutable), and the four-type `already_linked` read cost nothing
> measurable — the phase was already batched over the whole fact set.

**The edge type.** Add `ASSESSED = "assessed"` to `EdgeType`
(`core/types.py`) and put it in **`REVIEW_EDGE_TYPES`**, widening that set's
docstring: the operative property is *anchored to a node version, not migrated
on supersession or merge*, and `ASSESSED` is the first member with no retrieval
label. It must not migrate — *"A and B are different claims"* is a judgment
about those two nodes, and carrying it onto a later survivor `S = A + C` would
assert something nobody decided.

**`SIMILARITY` stays a knowledge edge and is traversed, but it does not
migrate either.** An earlier draft of this section said it did, on the grounds
that `S` contains `A`'s claim, so if `A` and `B` were one claim then `S` and `B`
still are. §10.2.1 reversed that, and §10.2.1 is right: a merge *synthesises*
the survivor's content, so `S` is not the wording anybody judged `A` against.
The two records still differ, but in traversal rather than migration —
`SIMILARITY` is knowledge to follow, `ASSESSED` is a suppression index.

> **Corrected 2026-08-22, on building the anchoring rule.** The two subsections gave opposite
> answers for `SIMILARITY` on a merge, four pages apart — the same failure this
> document names as a pattern, in a new place. The costs are asymmetric, which
> is what settles it: anchoring costs one re-nomination of `S` against `B`,
> which is correct, since nobody has judged that pair; migrating can manufacture
> corroboration in silence.

**The argument.** `apply_reflection` (`mcp/tools.py:2347`) gains
`similarities: list[dict] | None = None`, each
`{"pair": [str, str], "verdict": "one_claim" | "distinct", "because": str}`.
`because` is required — the same rule `supersessions` already applies, for the
same reason. An unknown verdict is rejected and reported rather than defaulted;
an unknown or retired id is **skipped**, matching how the other nine arguments
already treat them.

Writes, per §1.2: `one_claim` → a `SIMILARITY` **and** an `ASSESSED` edge;
`distinct` → `ASSESSED` only.

**The reader.** `contradiction_detection.py`'s `already_linked` loop
(lines ~93–102) iterates `(EdgeType.SIMILARITY, EdgeType.CONTRADICTION)`.
Extend to `(SIMILARITY, CONTRADICTION, VARIANT_OF, ASSESSED)`. This is four
typed queries becoming eight, on a phase already batched over the whole fact
set — measure before assuming it matters, and if it does, the lever is one
untyped `get_edges_for` per direction.

**Corroboration is untouched.** `corroboration.py` reads `SIMILARITY` only, and
must keep doing so — that is the entire point of the split, and a test should
assert that an `ASSESSED`-only pair does not corroborate.

#### 10.2.1 A precondition: judgment edges must stop migrating on a correction

> **✅ BUILT 2026-08-22**, ahead of step 1 as required.
> `JUDGMENT_EDGE_TYPES` is in `core/types.py` and `migration_disposition`
> consults it before the status branch, so both backends inherit it — they
> derive their answers from that one function and hold no policy of their own.
> Seventeen tests, over both backends. Two refinements to what is written below:
> the set covers **every** retirement including a merge (see §10.2 above), and
> `ASSESSED` is **not** added to it — `REVIEW_EDGE_TYPES` gives it the same
> anchoring plus the exclusion from traversal it also needs, and
> `NON_KNOWLEDGE_EDGE_TYPES` is consulted first, so listing it twice would be
> redundant.

**This has to land before step 1, not after, and it is a change to existing
code rather than to this design.**

`migration_disposition(edge_type, status)` (`core/types.py:351`) returns
`"move"` for every knowledge edge on a `CORRECTED` retirement, and `SIMILARITY`,
`CONTRADICTION` and `VARIANT_OF` are deliberately **not** in
`NON_KNOWLEDGE_EDGE_TYPES` — the set's own comment says so, because they are
real edges to follow. So:

> `A` carries a `one_claim` similarity to `B`. `A` is corrected to `A′`. The
> edge re-points, asserting **`A′` and `B` are one claim** — a judgment about
> wording nobody assessed — and `corroboration.py` counts `B`'s publisher as
> backing `A′`.

That is **manufactured corroboration arriving through migration**, with §1.2's
split entirely correct. It is the failure `fact_dedup.py`'s header calls the
worst available, reached by a route neither the split nor the merge gate can
see.

**The document already states the principle and the code applies it too
narrowly.** `migration_disposition`'s own docstring, for the world-change case:
*"a contradiction or a variant is a judgment made **about the old claim**, and
re-pointing one asserts it of a claim nobody assessed."* Exactly right — and
scoped to `HISTORICAL` only. A correction is not a different situation in this
respect: the claim is the same, the **wording** changed, and the judgment was
about the wording.

`CONTRADICTION` carries the same latent fault, with an extra sting: **a
correction may be precisely what resolved the contradiction**, so re-pointing it
asserts a conflict the correction just settled.

**The fix, and why not the obvious one.** Adding these types to
`NON_KNOWLEDGE_EDGE_TYPES` would also drop them from default graph traversal,
which is a second behaviour change nobody asked for. Instead a separate set,
consulted first:

```python
# A judgment is about the wording it was made against. Re-pointing one onto a
# replacement asserts it of a claim nobody assessed — the argument
# `migration_disposition` already makes for a world-change, which is just as
# true of a correction. Anchored, never migrated, on any retirement.
JUDGMENT_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {EdgeType.SIMILARITY, EdgeType.CONTRADICTION,
     EdgeType.VARIANT_OF, EdgeType.ASSESSED}
)
```

`migration_disposition` returns `"keep"` for these regardless of status.
Traversal is unaffected. `A′` starts with no judgments and gets re-nominated,
which is correct — `A′` against `B` is a pair nobody has judged.

> **On building it, the reasoning above needed one correction.** This section
> argues that a correction changes the wording and not the claim. But
> `migration_disposition`'s own docstring holds that a correction preserves the
> claim — which is exactly why the sources follow it — so a *one-claim* judgment
> would survive that reading intact, and the argument does not land on its own
> terms. What carries it is the **substantive** correction: "the population is
> 500,000" → "5,000,000" leaves a counterpart judged one claim against a number
> that is no longer there, and `corroboration.py` would count that counterpart's
> publisher as backing the new figure. Same verdict, load-bearing for a
> different reason — and the reason matters, because it is what shows a *merge*
> belongs in the same rule.

**Why it is latent today and stops being so at step 1**: both real graphs carry
zero `similarity`, `contradiction` and `variant_of` edges, so nothing has
ever migrated. Step 1 is the change that starts writing them. **File it as its
own issue** — it is a defect in shipped code, not a design note.

**Tests** (`tests/pipelines/test_similarity_decisions.py`): a `one_claim`
verdict raises corroboration for both nodes from 1 to 2 where the publishers
differ; a `distinct` verdict does not; both suppress re-nomination on the next
`detect_contradictions`; neither `ASSESSED` nor `SIMILARITY`
migrates through a merge (the anchoring rule, built — the earlier draft had `SIMILARITY`
migrating); an unknown verdict is reported, not applied.

### 10.3 Step 2 — the registry

> **✅ BUILT 2026-08-22.** `Agent` / `AgentDescription` / `JudgeRef` in
> `core/types.py`, five protocol methods on both backends, the `agent` table and
> the `approved_agent_ids` graph-state field, `claim_agent` over elicitation,
> `EPIMEMER_APPROVED_AGENTS`, the `epimemer` CLI (`agents confirm`,
> `agents list`), `use_graph` re-validation, and 69 tests. `docs/ATTRIBUTION.md`
> is the current-behaviour page. **Six things the design below did not say:**
>
> 1. **The two questions are not one gate.** §2.2's table has re-description
>    "approved the same way" as the initial claim, and says nothing about the
>    user being unreachable for it. Built as two strengths: an **id** the user
>    has not approved is *refused*, because admitting it hands identity back to
>    the agent; a **new description** under a known id is *recorded either way*
>    and carries `confirmed_at` only where a human saw it. Refusing the second
>    would lose a true record of what the agent said about itself in order to
>    protect a field §2.4 already marks as unverified — and *self-described,
>    unconfirmed* is the object that section exists to keep distinct.
> 2. **A tool that waits on a person cannot share the tool timeout.** Nothing
>    said what happens when a 30s `EPIMEMER_TOOL_TIMEOUT_SECONDS` wraps an
>    elicitation. It would turn *the user was still reading* into *the client
>    cannot elicit* — which is the one direction this must not fail in, since
>    the second refuses the claim. `_run_with_timeout` grows `waits_for_user`,
>    and only a call that puts a question to a person may set it.
> 3. **Config seeding runs on every graph the server lands on**, not only at
>    connect as §10.3 says. Approval is per graph, so connect-time seeding
>    alone leaves every *other* graph unapprovable on an embedded backend —
>    which is the failure this section was written to prevent, one `use_graph`
>    later. Seeding is applied **before** the judge is re-checked, or
>    configuration would clear a judge it was about to admit.
> 4. **No session means no binding, and it is reported rather than raised.**
>    FastMCP's session state raises outside a request context. A graph switch
>    must not fail over an identity feature the caller never used, so absence
>    reads as *no judge*; a successful claim reports `session_bound` so a
>    recorded-but-unbound claim is visible instead of silent.
> 5. **`epimemer agents confirm` stamps the description too**, not only the id:
>    the user vouches for the wording in front of them (§2.3), so the current
>    version gets `confirmed_at` and earlier ones do not. `agents list` was
>    added beside it — approving an id blind is the same gap in miniature, since
>    the user needs to see what the agent claimed before agreeing to it.
> 6. **`list_agents` is protocol-only, not an MCP tool.** The roster is for the
>    user and for review mode; handing the agent a list of judges is one step
>    from the filtering §8 refuses, and it has no other use for it.
>
> Smaller: `is_embedded_url` is now public in the SurrealDB adapter, because the
> CLI needs the same predicate the reconnect path uses and two copies of *what
> counts as embedded* is exactly how the CLI ends up writing approvals into a
> store nobody reads.

**Storage protocol** (`storage/protocol.py`), implemented in full on both
`memory.py` and `surrealdb_adapter.py` — the standing rule, no flags and no
`hasattr`:

```python
async def get_agent(self, agent_id: str) -> Agent | None: ...
async def upsert_agent(self, agent: Agent) -> None: ...
async def list_agents(self) -> list[Agent]: ...
async def get_approved_agent_ids(self) -> list[str]: ...
async def set_approved_agent_ids(self, ids: list[str]) -> None: ...
```

The approved-id list is per-graph settings, stored **exactly** the way
`get_reflect_threshold_override` / `set_reflect_threshold_override` already are
(`protocol.py:747-761`) — beside the reflect counter, scoped per graph,
surviving restarts. SurrealDB gets an `agent` table beside `fact` / `topic` /
`inference`; in-memory gets a dict on the store.

**`claim_agent(agent_id, description)`** (MCP). Refuses an id not in the
approved list, with prose the agent puts to the user (§2.2). On success:
upserts the `Agent`, appends an `AgentDescription` if `text` differs from the
current one, updates `last_seen_at`, and binds `(agent_id, digest)` to the
session via `ctx.set_state`.

**Approval** goes over `ctx.elicit` where the client supports it (§2.3).

**The CLI fallback does not work for every backend, and the gap matters as soon
as anyone requires a judge.** Approved ids live in per-graph settings *inside the
storage backend*.

> **Revised 2026-08-23.** This subsection previously said the gap was
> load-bearing *at step 4*, because step 4 was a dated cutover that would turn
> an approval-less server into one refusing every write. §3.3 has replaced the
> cutover with a per-graph setting that ships off, so nothing is bricked by
> upgrading. The gap is real for anyone who turns the setting **on** with a
> client that cannot elicit and an embedded store — which is the same failure,
> now reached by choice rather than by release date, and still worth closing
> here. `epimemer agents confirm <id>` is a separate process — and the active-graph guard records that **a second `mem://` connection is a separate store**. So
against an embedded backend the CLI writes approvals into a store the running
server will never read. Combine that with an elicitation-less client and there
is **no approval path at all** — which step 4's cutover then turns into a server
that refuses every write.

So the fallback has to be a transport the server itself reads at startup:

| Backend | Elicitation | Fallback that works |
|---|---|---|
| SurrealDB | `ctx.elicit` | `epimemer agents confirm` — same store, different process |
| embedded `mem://` | `ctx.elicit` | **config file or env, read at connect time** |

Read the configured ids when the backend connects and seed the per-graph list
from them. `epimemer agents confirm` stays the convenience for server backends;
it must **refuse loudly against an embedded store** rather than appear to
succeed. Nothing here may be reachable by the agent — that is still the rule
(§2.3) — but *unreachable by the agent* and *unreachable by the user* are
different failures, and the first draft shipped the second one.

**Session detection** uses `ctx.session_id` and `ctx.client_id`, both already
on FastMCP's `Context`, which `server.py` already threads into every tool.
Neither identifies the *model* (§2.2) — do not try to infer one.

**Re-validate the judge on `use_graph`.** The session binds one
`(agent_id, digest)`, but approval is **per graph**. Switching graphs mid-session
would otherwise carry a judge approved for graph A into every write on graph B.
Check at `use_graph`, and again at write time — the second is cheap and is what
makes the first not a single point of failure.

### 10.4 Steps 3–4 — threading the judge

> **✅ STEP 3 BUILT 2026-08-23.** `JudgeRef` reaches ten tools, five storage
> transactions on both backends, and four carriers: `LifecycleEpisode`,
> `NodeEdge`, `ValueSignal` and the node types. 60 tests over both backends.
> Step 4 (ingest, and the require-a-judge setting) is still design.
>
> **Where it landed, and six things the design below did not say:**
>
> 1. **One nested pair, not two columns.** §4 has `judged_by` and `judge_desc`
>    as separate fields. Built as a nested `JudgeRef`, because the pair is never
>    meaningful apart — an agent id without the description version says *who*
>    but not *what they claimed to be at the time*, which is the half that makes
>    an old decision readable — and because four carriers would otherwise each
>    have to remember two field names.
> 2. **On the edge, not in its `metadata`.** §3.1's table routes judgment edges
>    to `metadata`. `metadata` is a free-form bag, and a reader asking *who
>    decided this* would have to know a string. `NodeEdge.judged_by` is a field.
> 3. **Retiring and returning carry two judges.** `retired_by` **and**
>    `restored_by` on the episode, because they are two decisions often months
>    and sometimes two agents apart — and `restored_by` is written by the same
>    single edit that writes `restored_at`, which is the only edit an episode
>    ever receives.
> 4. **`update` and `link` are in, and the design's list omitted both.**
>    `update` is `supersede_by`'s twin — the same retirement with the same
>    `because` — so attributing one and not the other puts a hole in *who
>    retired this* that depends on which tool the agent happened to reach for.
>    `link` asserts a relation, which is the same category as
>    `record_contradiction`.
> 5. **Nodes gained the field now rather than at step 4.** `apply_reflection`
>    synthesises topics (parents, splits, enrichments) and `merge_facts` writes
>    a survivor: those are step-3 writers producing *content*, so the node-level
>    field had to exist for them. Step 4 sets the same field at ingest. A
>    correction does **not** inherit it — the replacement is this agent's
>    wording, and carrying the previous author over would credit them with a
>    sentence they never wrote.
> 6. **`tools.archive` needed nothing, and that is not a hole.** The design
>    lists it among the writers, but it only *exports* candidates; the status
>    flip is `apply_reflection(archivals=…)`, which is attributed.
>
> **Two rules decided while building, both about not overwriting a name:**
> re-recording a pair that already has its edge returns `created: False` and
> leaves the judge alone — a second agent calling the same tool has *confirmed*,
> which is a review with a record of its own (§6.4), not an overwrite of
> somebody else's field. And `ValueSignal.importance_judged_by` is the **latest**
> judgment while each `reinforcements` entry names its own judge, because three
> judgments by three agents compose into one number and the trail is the only
> place they stay separable.
>
> **Approval is re-checked on every write**, as §10.3 asks. A revoked id records
> as *unknown* rather than raising: recording the name would assert an approval
> that no longer exists, and refusing is the graph-level policy talking, which
> §3.3 puts elsewhere.
>
> **Two deliberate gaps, named rather than left to be discovered.**
> `apply_reflection`'s `boundaries` and `relation_merges` both edit an existing
> record in place. Stamping a boundary onto the provenance edge would overwrite
> whoever ingested it, and relabelling has no slot at all. Both want a
> **journal** row (§4, step 5) rather than an inline field, which is the
> distinction §3.4 already draws: inline is the original judgment and never
> changes; anything that revisits one belongs in the journal. *(Boundaries got
> their row. `relation_merges` was removed on 2026-08-28 —
> `RELATION_LABELS.md` §5 — so the second gap closed by the operation ceasing
> to exist rather than by being attributed.)*
>
> Smaller: an unknown judge is **dropped** from retrieval responses rather than
> sent as null. That is the opposite of what `confidence` does, and the
> difference is what the absence says — a missing confidence is a caveat about
> the claim, a missing judge says only that this graph does not record one,
> which is true of every node in it.

```python
class JudgeRef(BaseModel):
    agent_id: str
    digest: str          # the AgentDescription version current at this call
```

Resolved once at the MCP boundary from session state, then passed explicitly —
never read from a module global (§3.2). Step 3 covers the reflect-side writers
(`apply_reflection`, `merge_facts`, `supersede_by`, `record_contradiction`,
`record_variant`, `judge_importance`, `archive`, `restore`); step 4 covers
`store_decomposition` and `segment`.

> **✅ STEP 4 BUILT 2026-08-23.** Ingest attributed (`segment`,
> `store_decomposition`), `require_judge` as a per-graph setting on both
> backends with `EPIMEMER_REQUIRE_JUDGE` behind it and `epimemer agents require`
> in front, one gate at the boundary over twelve write tools, and 55 tests.
>
> **The escape hatch is not the shape proposed below.** This section said a
> write should be able to name its judge with an explicit `agent_id`. Built
> instead as a **fallback binding held on the lifespan**, used only where
> session state does not exist at all. Three reasons, in order of weight: the
> explicit parameter is ten tool schemas wider and something the agent has to
> remember on *every* call, where the binding is claimed once; the security
> argument is identical either way, because approving the id is the gate and the
> binding was only ever ergonomics; and a transport with no session concept is
> single-client, so *the process* and *the client* are the same thing and there
> is nothing for two callers to confuse. It is not a module global — it is
> per-server state reached through `ctx.lifespan_context`, and a successful
> session binding clears it.
>
> **Five smaller things the design did not say:**
>
> 1. **The document and its segments carry no judge.** They are the material,
>    not a claim about it: *who pasted this text* is a different question from
>    *who judged what it says*, and only the second is a judgment review would
>    ever want to select on.
> 2. **Reusing an entity or tag topic does not restamp it**, the same rule step
>    3 settled for a re-recorded edge. Mentioning a name again is not
>    introducing it, and crediting the second agent would take the node from
>    whoever created it.
> 3. **Edges are stamped once, after the batch is assembled**, rather than at
>    each of the five places ingest builds them — three of which are inside a
>    Petritype net that would have had to grow an argument to carry it. One
>    stamp also cannot miss one.
> 4. **The gate covers twelve tools**: the ten from step 3 plus the two ingest
>    steps. Timelines and metacontexts are scaffolding rather than claims, so
>    they are outside it — named here rather than left as an apparent oversight.
> 5. **`epimemer agents require on` warns when no id is approved yet.** It is
>    the one setting that can make a working graph refuse everything, and being
>    told at the moment of switching it on beats discovering it from the next
>    write.
>
> The CLI's refusal against an embedded store is now **action-specific**: two
> settings live behind that wall and they have different environment variables,
> so one generic message would send half its readers to the wrong one.

**Step 4 carries the setting, and there is no cutover** (§3.3, revised
2026-08-23). `judge` is optional in every signature; one shared check at the
tool boundary refuses a blank **only where the graph requires one**, and the
setting ships off, so an upgraded server keeps writing exactly as it did. The
release note says what turning it on requires — approved ids first — rather than
warning about an upgrade that changes nothing by itself.

**What step 4 must also carry, if the setting is to be usable**: a way for a
write to name its judge where the session cannot hold one. `claim_agent` binds
the identity to the MCP session, and a caller without one gets
`session_bound: false` (§10.3, built) — which is harmless while blanks are
allowed and total once they are not. An explicit `agent_id` on the write is no
weaker than the binding, because approving the id is the actual gate and the
binding was only ever ergonomics.

### 10.5 Step 5 — the journal

A `decision` table on both backends, with `DecisionRecord` as §4 defines it.
Reads needed: by `judged_by`, by `decided_at` range, by `subject_ids` contains,
and *"is there a record whose `reviews` is this id"* — the last is what makes
`unreviewed` derived rather than stored (§3.4), so it wants an index on
`reviews` and on `judged_by`.

**Append-only in the strict sense**: no update path exists on this table. A
reversal, a confirmation and an overturn are all new rows.

**Fold W&S §9 here** (§9): `NodeNote` never ships; `node.notes` becomes a
derived read over records whose `subject_ids` contains the node, and W&S §5.3's
`contested_decisions` becomes `review(mode="advisory")`.

> **Built 2026-08-23.** The table, the five reads, and a row at every writer:
> ingest, both supersession readings, contradiction, variant, similarity, merge,
> reversal, synthesis, split, enrichment, archival, reactivation, boundary,
> relation and importance. Four things took a different shape from this section,
> and one writer is missing.
>
> **`kind` carries `because`, rather than a field repeating it.** A correction
> and a world-change are opposite claims about what happened and a
> reviewer asking for one does not want the other, so they are two kinds —
> `supersession_kind(status)` in `types.py` is their single declaration, read by
> `update`, `supersede_by` and reflect's supersessions.
>
> **Granularity is per act, and an act is not always a call.** §4.1 settles
> ingest at one row per `store_decomposition`; the same reasoning makes an
> archival sweep one row (one pass over one nomination list) and a reactivation
> one row, while `apply_reflection`'s other lists get a row each — those are
> independent verdicts about unrelated nodes that happen to be batched. The rule
> underneath both: **one judgment, one row**, and the batching is the request's
> shape rather than the judgment's.
>
> **Re-recording a pair verdict writes a confirmation**, which is §3.4's rule
> finally having somewhere to go: `record_contradiction` and `record_variant` on
> a pair that already carries the edge write a row whose `reviews` names the
> *oldest* record for that pair — the decision, not an intervening confirmation
> of it. Where the verdict predates the journal the pointer is blank, because
> the journal cannot cite a row that does not exist.
>
> **`certainty` has no tool that supplies one.** The field is on the row and
> writable through the protocol, but no MCP tool takes one yet; the first are
> step 7's `apply_review` and `rejudge`, whose whole purpose is a declared
> judgment and where §5's ladder can be stated once instead of on twelve tool
> schemas. §6.2 already designs for exactly this state — the whole corpus is
> tier 2 and the ordering is entirely derived — so nothing promises the agent
> something the code does not do.
>
> **Relation merges still have no row, and that is the one gap.** Every other
> subject in this journal is a node id; a relation merge's subjects are
> *labels*, and putting them in `subject_ids` would give one field two
> namespaces — the tell §11 records twice. The alternative, the id of every edge
> relabelled, needs `relabel_edges` to return them and writes subject lists in
> the hundreds. Filed as an open question rather than guessed at. Boundaries, the
> other gap `docs/ATTRIBUTION.md` named, are closed: both of their subjects are
> nodes.
>
> *(Both halves are now settled and neither the way this expected. The label record
> gave a label an **id**, so the namespace objection dissolved and
> `relation_verdict` rows name two label records like any other subject; then
> The label record §5 removed merging outright on 2026-08-28, so the operation this paragraph
> is about has no row because it has no existence. The subject of a decision
> about vocabulary is the vocabulary entry — it just needed the entry.)*
>
> **`DecisionKind` carries no member without a writer**, corrected on review the
> same day: `relation_merge` and `proceeded_despite_advisory` both shipped
> unwritten and both were removed. `WARNINGS_AND_SETTINGS.md` §8.1 had already
> settled the rule for `AdvisoryAction` — *"a value nothing can produce is worse
> than no value at all"* — and it binds harder here, because review **selects**
> on the kind: an unwritten one is a filter that silently returns nothing and
> reads as a clean graph. Both absences are named in `DecisionKind`'s docstring,
> which is where a not-yet belongs. The drift-guard test now has no exception
> list, and is stronger for it — a list of forgiven cases written by whoever
> added the member is not a check.
>
> **The journal write never raises.** It lands after the decision and outside
> its transaction, which is the safe direction; raising would fail the tool call
> *after* the graph write succeeded, and the retry is worse than the missing row
> every time — a retried `merge_facts` refuses because its sources are already
> retired, a retried `record_contradiction` writes a row that reads as an
> original decision, and a retried `store_decomposition` ingests the document
> twice. The failure is logged with the kind and the subjects, for the operator
> rather than the agent: no tool re-journals a decision, so telling the agent
> would hand it information it has no move for.
>
> **One defect fixed on the way**, in code this step only read: timestamps are
> stored as strings and compared as strings, which is chronologically correct
> only while every rendering has the same shape — and Pydantic omits the
> fractional part on a whole second, so a row at `…41Z` sorts *after* a bound at
> `…41.5Z` because `"Z" > "."`. The journal writes microseconds on both sides.
> `query_changes`' lifecycle window had the same latent case — **fixed the same
> day** as the timestamp-text trap, by `instant()`, which compares instants rather than
> spellings. The journal keeps its padded strings rather than converting: it is
> the one timestamp with an index a range actually uses, and wrapping the field
> costs 45× at 50,000 rows. Reader converts where there is no index; writer pads
> where there is.

### 10.6 Steps 6–7 — `review()`

```python
async def review(
    storage: StorageBackend,
    *,
    mode: ReviewMode = "all",
    agent_id: str | None = None,
    since: datetime | None = None,
    between: tuple[datetime, datetime] | None = None,
    certainty_ceiling: float | None = None,
    include_pre_attribution: bool = False,
    max_results: int = 200,
) -> tuple[dict, ResponseMeta]:
```

Read-only, like `reflect`. Ordering per §6.2 — tier 1 (declared `certainty`
ascending) before tier 2 (unrated, ordered by count of §5's derived signals
descending). Capped at `max_results` with `truncated` named in the response,
The nomination cap's treatment. The response always reports `unrated_count` and
`pre_attribution_excluded`, and — when `certainty_ceiling` was supplied — the
value **this call** used (§6.3, the single nomination bar).

**`apply_review(confirmations=[…], reversals=[…])`** is the only writer;
`review()` never writes.

**No `graphs=` parameter, and every response names the graph it answered from**
(§6.6). Cross-graph review is `list_graphs` → `use_graph` → `review()` per
graph — safer than a fan-out rather than merely equivalent, since each switch is
the active state instead of one borrowed mid-call. The locator that would tell a
reviewer *where else to look* is `review`'s `elsewhere`, gated on the guard. **Built 2026-08-23 as
`elsewhere`; see §6.6's amendment, including what it cost — `review` is a mover
now.**

> **Amended 2026-08-23, on building it.** Five things.
>
> 1. **Only the parameters step 6 owns shipped**: `mode` and `max_results`. The
>    signature above is step 7's, and `agent_id`, `since`, `between`,
>    `certainty_ceiling` and `include_pre_attribution` would each have been an
>    argument that did nothing — which is `DecisionKind`'s rule about members
>    with no writer, arriving on a parameter list. `mode` is validated against
>    the modes that exist and **refuses by name**, saying which are designed but
>    unbuilt: a mode the list admits and nothing implements is a filter that
>    silently returns everything, which reads as a clean graph.
> 2. **Tier-1 ordering came with it**, though the build table assigns it to step
>    7. `certainty` is a field that already exists on `DecisionRecord`; a sort
>    that ignored it would have been silently wrong the moment `apply_review`
>    wrote one, and the rule it encodes — an unrated decision never outranks a
>    flagged one — is the half worth pinning early. It costs five lines and a
>    test over hand-built rows.
> 3. **`confidence` is not on the node.** §5's table says *"source
>    `confidence`"*, and it lives on `ValueSignal`, which every node type
>    carries — so `node.confidence` raises on a `Topic`. Caught by the
>    retrieval-declaration parity suite rather than by anything written for this
>    step, which is what an oracle over *every* tool buys.
> 4. **`retrieved` is not the use signal**, and this nearly shipped without a
>    declaration on the reasoning that reviewer traffic would feed the staleness
>    clock archival nominates on. It would not: only `search` stamps
>    `retrieved_at`, while `meta.retrieved` drives focus in the viewer. An
>    undeclared subject greys out the moment somebody clicks the decision naming
>    it. Same parity suite caught it.
> 5. **The merge subject convention is a trap.** `merge_facts` journals
>    `[survivor, *sources]`, so *"three or more sources"* read off
>    `len(subject_ids)` calls every two-source merge wide. It has a named
>    function and a test of its own for that one off-by-one.
>
> **And the honest scope, measured rather than assumed.** Step 6's row says it
> *"works on the existing corpus"*. That is true of the **signals**, which read
> nodes and so see everything — and false of the **journal**, which begins where
> step 5 does. On the three real graphs on 2026-08-23: `memory` holds **one**
> row (the archival sweep from the wrong-graph incident, correctly first and
> unreviewed), and `field-notes` and `petritype-server` hold **none** — their ingests
> ran through a server that predated the journal. Every decision this project
> has made before 2026-08-23 is invisible to review, permanently, and no later
> pass can reconstruct it: the same island fact dedup left behind, in the other
> direction. Review is right and nearly empty, and it fills from here.
>
> **The read is linear in journal size**, which §6.2 anticipated — *"a caller
> that re-sorts has to fetch enough rows to sort"*. `decisions_scanned` is in
> every response so the cost is visible rather than inferred; the bound arrives
> with step 7's `since`.

**Step 6 ships before any decision has a judge**, and works: the whole existing
corpus is tier 2, so the ordering is entirely derived and still useful (§6.2).

> **Amended 2026-08-23, on building step 7.** Nine things, and the first changed
> what one of the two writers *does*.
>
> 1. **`reversals=[…]` is `dissents=[…]`, and the rename is the design.** The
>    parameter reversed nothing. Every undo in this system already has a tool —
>    `reverse_merge` for a merge, `restore` for an archival, `apply_reflection`
>    with `distinct` for a `one_claim`, and now `rejudge` for an ingest prior —
>    each with its own refusals and its own row that legitimately sets
>    `supersedes`, because it really did supersede something. A dispatcher over
>    four such tools is the misdirected-write scope's fan-out: *a convenience less safe than the sequence
>    it replaces is not a convenience.* So a dissent records the **finding** and
>    sets only `reviews`. A row claiming to supersede a decision whose effect
>    still stands would put the journal in disagreement with the graph, which is
>    the one thing §4.2 exists to prevent.
>
>    **And the reviewer who most needs it is the one whose undo was refused.** A
>    merge whose survivor has since been contradicted cannot be reversed at all
>    (§7). Before this there was nowhere to put that finding, which is the
>    verdict-with-no-writer shape one more time — and it is the argument for the
>    dissent existing at all, which the design had not made.
> 2. **It is not one transaction, against §10.7.** That requirement was written
>    while this tool was imagined as performing the reversals; it performs
>    nothing, so there is no multi-step change to make atomic. Each entry is an
>    independent judgment about an unrelated decision that happens to be batched,
>    which is `apply_reflection`'s shape exactly — per-entry refusals, and one
>    bad `decision_id` does not lose the good ones.
> 3. **`advisory` is refused by name rather than shipped**, and `between` is not
>    a mode. `advisory` selects on a `DecisionKind` that deliberately does not
>    exist, so admitting it would return an empty list that reads as *nothing is
>    contested* — the rule `DecisionKind` states, arriving on a mode. `between`
>    is `since` with an `until`, and two names for one selection is the *two
>    shapes for one question* defect §6.6 cites from `WARNINGS_AND_SETTINGS.md`
>    §5.2. Both are held as data with the reason in the refusal, so a caller that
>    read §6.1 is told where the selection went.
> 4. **§6.1's *"modes compose"* and §10.6's single `mode` string were never
>    reconciled**, and the resolution is: **a mode names the selection; every
>    argument narrows whatever it selected.** `agent_id`, `since`, `until` and
>    `certainty_ceiling` work under every mode. That leaves `by_agent` and
>    `since` as sugar over a *required* argument — and the refusal is their whole
>    value, because `all` with an `agent_id` the caller forgot to pass returns
>    the entire journal, which reads as an answer rather than as a missing
>    filter.
> 5. **A retry must not read as a second opinion**, which the design did not
>    consider. Two confirmations over one decision is exactly the evidence a
>    later reviewer weighs, so an identical judgment by the same judge is refused
>    naming the row that already says it; a *different* judge is the second
>    independent check this design exists for. The refusal is subject-scoped per
>    §4.1, so confirming other subjects of the same ingest record is new work.
>    **The gap it leaves is named rather than hidden:** two blank judges cannot
>    be told apart, so a retry on a graph that does not require one writes a
>    second row. One more thing `require_judge` buys.
> 6. **`rejudge` has to keep what it replaces**, which §6.5's signature and
>    docstring do not say. Without a trail it would be the one call in the system
>    that *destroys* a judgment rather than superseding it, and *"nothing is
>    destroyed"* would stop being true in exactly the tool review reaches for
>    most. Each revision appends `{because, was, now, judged_by}` to the node's
>    `rejudgments`, on `judge_importance`'s reasoning: one chronological trail,
>    because a reviewer wants a judgment and its later reversal in sequence.
> 7. **Restating a judgment is a confirmation, not a rejudgment.** `rejudge`
>    refuses a call where every value supplied is what the node already carries,
>    and points at `apply_review` — otherwise it would write a `REJUDGMENT` row
>    that revised nothing, which review would then rank as a decision.
> 8. **`DecisionRecord.certainty` was unbounded**, and nothing had noticed
>    because nothing supplied one. The ordering *sorts* on it, so an
>    out-of-range value would not have been rejected — it would have silently
>    ranked first or last. Bounded 0.0–1.0 at the model, and refused with a
>    message at both writers.
> 9. **The drift guard was scanning one file.**
>    `test_every_kind_has_a_writer` read `mcp/tools.py`, which was true only
>    because every writer happened to live there — and stopped being true the
>    moment `CONFIRMATION` and `DISSENT` were written from
>    `pipelines/review/apply.py`. It caught them, correctly, and the fix is that
>    it now reads the whole package: **a guard whose reach is an accident of
>    where the code sat is one that fails open.**
>
> **One implementation trap worth the line:** `review()`'s reviewed-set has to
> cover the whole selection rather than the page, or every row past
> `max_results` reads as unreviewed. `unreviewed` filters before ordering, and
> `unreviewed_count` is over what was selected.
>
> **And the scope is unchanged from step 6's amendment.** `memory` still holds
> one journal row; the two writers added here have nothing to act on yet. That is
> not a defect in either — it is the island fact dedup left, and it fills from here.

### 10.7 Cross-cutting

- **Both backends, always.** Every protocol method above lands on `memory.py`
  and `surrealdb_adapter.py` in the same change. No capability flags.
- **Settings** follow the reflect-threshold pattern: per-graph override,
  explicit default, no singleton. Three new ones — `merge_undo_depth` (10),
  `merge_cycle_limit` (2), approved agent ids.
- **Named constants, one declaration each**, with a test that reads every
  declaration — the single nomination bar's carry-forward, which is what caught two numbers pretending
  to be one.
- **Transactions.** Reversal (§7.9 step 4) and `apply_review` are each one
  transaction on both backends, with a test that a mid-way failure leaves the
  graph unchanged.

---

## 11. What the review changed

Recorded rather than silently rewritten, because three of these are
carry-forwards this repo has banked before.

| # | Defect in the first draft | Where |
|---|---|---|
| 1 | `DecisionRecord` claimed append-only and carried mutable review state, duplicated inline — two homes for one mutable fact, the per-edge-type migration/the drifted lookup tables/the drifted lookup tables shape, in a document citing all three | §3.4, §4 |
| 2 | One `similarity` edge for two populations whose readers want opposite breadth; would have manufactured corroboration. **The confidence prior carry-forward verbatim**: when a field is documented with an "and", check whether the two halves want the same storage | §1.2 |
| 3 | Registry had no tool surface at all; identity "resolved at the boundary" from nothing, and self-review was indistinguishable from independent review | §2.2, §2.3 |
| 4 | Did not reconcile with `WARNINGS_AND_SETTINGS.md`, designed one day earlier, and did not even cite it | §9 |
| 5 | Unrated `confidence` used as a difficulty signal, re-committing the sin the confidence prior fixed | §5 |
| 6 | `all` mode unbounded (the day after the nomination cap); `[0.80, 0.85)` minted an unnamed constant (the week of the single nomination bar) | §5, §6 |
| 7 | The absence rule held only if the judge were mandatory, which the signature did not say | §3.2 |

Smaller: ingest journal granularity (§4.1), one ladder not two (§5), which record
is primary (§4.2), and a named writer for confirmations (§6.4).

**One further defect surfaced by discussion rather than by the review**, and it
is the same species as #2 above: §6's mode table ran *which decisions*, *in what
order* and *narrowed how* together in one column, so `uncertain` and `difficult`
sat among the modes while being answers to a different question. Separated in
§6.1–6.3. The tell was the same both times — **a column, or a field, that needs
the word "or" to describe what it holds.**

### 11.1 The second review (2026-08-22)

A second round over the revision found four more, two of them the same species
the document spends its length warning about — *a judgment re-pointed onto
wording nobody judged*.

| # | Defect | Where |
|---|---|---|
| 1 | `MergedEdge` listed seven fields and omitted `metadata` and `created_at`, so reversal would have **stripped `judged_by` from every edge it replayed** | §7.9 |
| 2 | Judgment edges migrate on a **correction**, manufacturing corroboration with §1.2's split entirely correct — a defect in shipped code, now **the anchoring rule**, blocking step 1 | §10.2.1 |
| 3 | The reversal guard checked inferences only, so `delete_node` would have destroyed post-merge contradictions, assessed verdicts and tags | §7.5 |
| 4 | The CLI approval fallback cannot reach an embedded `mem://` store, so an elicitation-less client had **no approval path** and step 4 would have bricked it | §10.3 |

Smaller, all fixed: `use_graph` must re-validate the judge (§10.3); the flagship
review example had no writer, now `rejudge` (§6.5); the reversal record now
keeps the survivor's wording; `delete_node` owns its embedding cascade;
intra-set edges are captured; confirmation granularity is stated (§4.1); and
§12's "none are open" is withdrawn.

**Two patterns worth keeping.** *A partial copy of a model is a bug with a delay
on it* — defect 1 would have surfaced only once somebody reversed a merge on an
attributed graph. And *a rule stated for one branch of a conditional is not a
rule the code applies* — defect 2 sat four lines from the argument that would
have prevented it.

**What the review confirmed and has not moved**: minted id plus append-only
dated descriptions; pinning `(judged_by, judge_desc)` per decision; no backfill;
no judge-weighted ranking, for the reason in §8; derived difficulty as the only
mode that works on the legacy corpus; and the build order's shape — attribution
before journal before modes.

---

## 12. Questions raised, and how they were settled

**Withdrawn 2026-08-22: this section previously opened "None are open."** A
second review found four defects in the material this document had just added,
so the claim was false when written — and it was the overconfidence pattern the
document polices elsewhere, stated about itself. *A design is not finished
because its author has run out of questions.*

### 12.1 What remains, in three kinds

**No design question is open.** That sentence is the one this section withdrew
above, so it is stated narrowly and with its workings: the three items listed
here on 2026-08-22 have each been resolved, filed as work, or scoped out with a
reason — and the categories below are the distinction whose absence made the
earlier claim glib.

**Every step in §10's build order is now built.** Steps 0a through 7 shipped
between 2026-08-22 and 2026-08-23; each carries a dated amendment recording what
took a different shape from the design. What remains below is not the build —
it is what the build left open.

**Decided, and now filed as work:**

- **Blank means unknown, and requiring a judge is a per-graph setting** →
  §3.3, revised 2026-08-23 by the user, reversing the day-one absence rule.
  Filed as part of **step 4**, which also gains the setting itself and a way for
  a write to name its judge where the session cannot hold one. Nothing built
  contradicted it — every occurrence was in this document, which is why the
  correction costs edits rather than a migration.

- **Judgment edges migrate on a correction** → **the anchoring rule**, **✅ built
  2026-08-22**, before step 1 as required. `JUDGMENT_EDGE_TYPES` anchors them on
  every retirement (§10.2.1). Building it surfaced two things the design had
  wrong: §10.2 and §10.2.1 disagreed about `SIMILARITY` on a merge, and the anchoring rule's
  stated reasoning did not carry on its own terms. Both are corrected in place;
  the verdict did not change.
- **Nothing retracts a `one_claim` verdict** → **the `one_claim` retraction**, filed on
  building step 1. The `similarity` edge it writes is what corroboration counts,
  and no call removes one; `apply_reflection(similarities=[…])` refuses a later
  `distinct` on that pair rather than writing `assessed` beside an edge that
  keeps corroborating. The refusal is the honest shape, but it leaves an agent
  that has changed its mind with nowhere to go — which is `reverse_merge`'s
  problem one tier down, and it wants the same answer: a writer, not a delete.
  **Built 2026-08-23**, and it took the writer: `distinct` over a standing
  `one_claim` writes `retracted_similarity`, which `DISQUALIFYING_EDGE_TYPES`
  reads. What the entry did not anticipate is that the read side already
  existed — `contradiction` had been disqualifying a standing `similarity` on
  the same reasoning since before this document.

**Resolved here:**

- **Confirmation granularity at ingest** (§4.1) → *a confirmation names the
  subjects it covers.* The alternative — a record staying unreviewed until every
  subject is confirmed — turns out to **require the same machinery plus
  bookkeeping**, since knowing when *all* subjects are done means tracking
  *which* are done. It is the recommendation plus a rule, so the rule is the
  only thing being chosen, and it is not worth its cost.
- **`rejudge`'s scope** (§6.5.1) → surveyed. `claim_kind`, `confidence` and
  `confidence_basis`; `importance` stays with `judge_importance` rather than
  gaining a second writer. **Built 2026-08-23** at exactly that scope, plus one
  thing the survey did not name: the value it replaces is kept on the node, or
  this would be the one call in the system that destroys a judgment rather than
  superseding it.
- **What `apply_review`'s second list does** (§6.4) → *records a finding, and
  performs no undo.* Settled on building it, and it renamed the parameter:
  `reversals` reversed nothing, because every undo already has a tool that
  journals its own row. A dissent sets `reviews` and never `supersedes`. §10.6's
  second amendment has the argument.

**Out of scope, named rather than hidden** — both surfaced by that survey, both
worth filing on their own:

- **A metacontext assignment cannot be withdrawn.** `link` adds a frame edge;
  nothing removes one. Load-bearing, since frames gate cross-frame merge refusal
  and corroboration's variant exclusion.
- **A validity interval cannot be corrected.** Supplied per source at ingest,
  and `boundary_proposals` only fills an *open* endpoint.

Neither is a review-mode question, and answering them inside a tool named for
ingest priors would bury an epistemic move in a metadata utility.

> **Both filed as revisable ingest judgments and built 2026-08-27** — `reframe` and
> `correct_interval`. Kept out of `rejudge` as this section said, though on
> better grounds than the ones given: **addressing**, not naming. §6.5.1's
> amendment has the argument.

### 12.2 Settled

Newest first.

> **Blank means unknown, and requiring a judge is a per-graph setting** —
> decided 2026-08-23 by the user, §3.3. This reverses *"`judged_by is None`
> means written before attribution existed"*, which was the one decision in this
> document made **on day one to avoid a scar rather than to describe anything**
> — and it overshot in a way worth recording, because the instinct behind it is
> sound and will recur.
>
> The scars it was avoiding are real: a literal `0.5` confidence on every
> pre-2026-08-19 row, and 305 unjudged `claim_kind`s. Both are cases where a
> blank was given a meaning nobody asserted. The fix for that is to **stop
> asserting**, and *unknown* asserts nothing. The old rule instead bought a
> second meaning — *legacy* — by making the field mandatory for every write for
> ever, which is a permanent cost on every graph to date-stamp a population that
> needed no date. **Guarding against a scar is not the same as needing a
> guarantee**, and a rule that costs more than the ambiguity it removes is the
> shape to watch for.
>
> What replaces it costs one setting: off by default, on where the user wants
> every write tied to an agent or a person, not agent-settable for the reason
> approvals are not. And the positive half of *no backfill* is now stated: the
> reviewer's id lands on the **review** record and never on the reviewed one, so
> an unattributed graph can still be reviewed under full attribution.

> **The whole edge is captured, not a field list** — decided 2026-08-22 after
> the second review, §7.9. `MergedEdge` named seven fields and omitted
> `metadata` and `created_at`, so a merge/reverse cycle would have stripped
> `judged_by` from every edge it replayed. The rule that replaces it: *a partial
> copy of a model is a bug with a delay on it.*
>
> **The reversal guard is a set difference, not a list of edge types** — decided
> 2026-08-22, §7.5. The inference-only version would have let `delete_node`
> silently destroy a contradiction, an assessed verdict or a tag written against
> the survivor after the merge.
>
> **Approval needs a transport the server reads** — decided 2026-08-22, §10.3.
> The CLI fallback cannot reach an embedded `mem://` store, so
> config-at-connect is the fallback there. *Unreachable by the agent* and
> *unreachable by the user* are different failures.
>
> **Review gets a writer** — decided 2026-08-22, §6.5. `rejudge` revises an
> agent-supplied judgment without superseding the claim, because a mislabelled
> `claim_kind` is neither *it was wrong* nor *the world changed*.
>
> **"Uncertain" is an ordering, not a mode** — decided 2026-08-22, §6.2. The
> question was *what floor selects the uncertain ones*, and it dissolved once
> somebody asked **when an agent actually needs them**: at no point does anyone
> want only the doubtful decisions. A reviewer wants all of yesterday's work,
> shakiest first, and stops reading when it stops repaying attention. So the
> floor became `certainty_ceiling`, an optional filter for the one case that
> wants a count rather than a list, and ordering carries the rest — in two
> tiers, so declared and derived stay unblended (§5) and the whole
> pre-certainty corpus still sorts usefully.
>
> **The survivor is deleted on reversal, not retired** — decided 2026-08-22,
> and §7.7 has the reasoning. The decisive argument is not exactness but that a
> later re-merge must synthesise from what is known then; resurrecting the old
> survivor would import a previous agent's wording into a decision nobody made
> with it. `delete_node` joins the protocol and **is never exposed through an
> MCP tool**, with that guard written on the method, on both backends, and in
> §7.7.
>
> **Futile merge/reverse cycles refuse rather than warn** — decided 2026-08-22,
> §7.8. `merge_cycle_limit`, default 2, counted per node from closed `merged`
> lifecycle episodes, which already exist and cost no round trip.
>
> **A reversal raises no new flag on its dependents** — decided 2026-08-22 by
> §7.6's principle, which was a question here. The merge's `evidence_merged`
> edges are deleted rather than mirrored by a second label.
>
> **`claim_agent` refuses an unapproved id** — decided 2026-08-22. Admitting
> invented ids hands identity back to the agent and dissolves the guarantee §2.2
> provides. The cost is one approval on a fresh install, and §2.2's refusal-as-
> prompt is what makes that a conversation rather than an error.
>
> **Undo was question 2 and is now §7** (decided 2026-08-22). The question
> changed shape on inspection: the partition reversal needs is destroyed at
> merge time, so the live decision was *capture or lose* rather than *build or
> not*. Settled: journal for the audit, a bounded list on the survivor for the
> payload, `merge_undo_depth` defaulting to 10, and a safety guard no setting can
> raise past.
