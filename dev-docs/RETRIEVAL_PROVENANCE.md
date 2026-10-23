# Retrieval provenance: seeing what the agent was actually given

A focus mode over the graph and timeline that dims everything the last
retrieval did *not* return, plus a record of the response text the agent
received, reachable from a list of recent retrievals. The records are
`epimemer/mcp/retrieval_records.py`; focus mode lives in the dashboard's graph
and timeline panels.

The question it answers is not "what is in the graph" but **"what did the agent
see, and what did it miss?"** The second half is why the non-returned nodes
stay on screen rather than being filtered away. The rules focus mode shares
with the other panels are `VISUALISATION.md`, *Rules every panel follows*.

---

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| 1 | Cover **every** tool whose response carries node ids, not just `search` | A panel that greys a node the agent read via `topic_tree` a minute ago, labelled "not retrieved", teaches a false belief, in a system whose point is epistemic honesty |
| 2 | Focus mode dims by **desaturation**, never opacity | Opacity already encodes node status (§4.1) |
| 3 | Dimmed nodes stay **clickable** | The interesting click is on a dimmed node: *why didn't this come back?* |
| 4 | Response text lives in the **detail drawer**, as a second tab | §5 |
| 5 | Records live in the **session process** and mirror into the hub, with the mirror stripped to metadata on a non-loopback bind | Payloads are the largest and most sensitive thing the system holds, and the dashboard is usually opened *after* the retrieval looked wrong (§3.2) |

---

## 2. Coverage: one choke point, not a wrapper per tool

**The rule is semantic, not an enumeration: `retrieved` is the set of node ids
present in the response.** What the agent saw, uniformly. Any census of "the
retrieval tools" drifts: write tools such as `update`, `supersede_by`,
`judge_importance`, `create_timelink` and `merge_facts` all put node ids where
the agent can read them, and a refusal declares too (`merge_facts` names its
sources whether or not it merges them), because the rule is about what the
agent can read, not about whether the call changed the graph. `reflect` scans
the whole active graph but the agent sees only the nominees, so a reflect
record dims everything except them, which is accurate and no special case.

Every tool call passes through one wrapper in `mcp/server.py`, which holds the
tool name, the complete result, the meta and the latency, and writes the record
right after the response is serialised: one insertion, every tool, and any
future tool covered by construction.

Only the *record* is by construction. The *ids* are by declaration (§2.1), and
a tool that never declares would produce a silently empty record. Two things
close that gap:

- `retrieved` on `ResponseMeta` is `None` until a tool declares, and the record
  is then flagged as undeclared; `[]` means "declared: nothing returned". A
  forgotten declaration is visible rather than silent.
- The coverage test is an **oracle**, not a list: parametrised over all
  registered tools, it seeds a known graph, calls each, and asserts that any
  known node id appearing in the serialised response also appears in
  `retrieved`. The known ids are every node the graph holds before and after
  the call, each call names the graph, and a call stopped at the graph gate
  fails the test rather than passing it unchecked. Walking a result for
  id-shaped keys is wrong in the wrapper (§2.1) and safe in a test, where a
  miss merely under-checks. This test is what finds the next tool that forgot
  to declare.

`reflect`, and every tool whose response carries `warnings`, declares by
walking its own result: a warning names its nodes in `subjects` and in a
`detail` whose shape depends on its kind, and `reflect`'s nominee lists have as
many shapes as there are nominators. A hand-written list of key paths is how
the next one would go undeclared. §2.1's objection to walking is about the choke
point guessing across tools it knows nothing about; a tool reading the
structure it built three lines earlier is the opposite case. Its record names
the nominees, never the scan.

### 2.1 Tools declare their ids; the wrapper does not guess

Walking an arbitrary result dict looking for id-shaped keys would guess
differently per tool and break silently when a shape changes. Each tool states
its own instead, as `retrieved` on the `ResponseMeta` it already returns.

The field is excluded from serialisation, so the ids stay out of the `_meta`
the agent sees: they are for the dashboard, and putting them on the wire would
cost the agent tokens to read a list it has no use for. The response is
serialised by a call that honours the exclusion, so they cannot leak into it by
accident.

---

## 3. The record

A record holds the tool, the query text or a rendering of the arguments, the
graph, the declared ids each with its provenance and score, the response text
exactly as returned, and whether a cap cut anything (§3.2). Its `record_id` is
assigned by the session, not the hub's `seq`; the fields are
`RetrievalRecord` in `mcp/retrieval_records.py`.

Provenance uses the labels `search` returns, `vector`, `lexical`, `segment`
and `expanded` (`docs/RETRIEVAL.md` §4), for the reason given there: a flat
"retrieved" set throws away the most useful thing the feature produces. A fifth
label, `direct`, covers tools that return nodes without ranking them
(`find_nodes`, `graph_as_of`, `query_changes`, `topic_tree`, `reflect`,
`list_sources`, and the write tools). `check_conflicts` candidates are
`vector` with the cosine similarity as `score`: they genuinely are
vector-similarity results.

**`score` is blank for `search`.** `QueryResult` carries `provenance` and no
fused score, and a fused RRF number is not a similarity, so showing one would
need its own explanation. `check_conflicts` is the one tool that declares a
score.

**Failed calls write no record.** There is no response to record, and the
selector lists what the agent was handed.

**One call, one record.** The record is written by the wrapper, which sees the
tool call and not the metacontext-widening loop inside `search`, so the loop
cannot appear as several records.

### 3.1 It is *our* response, not *the agent's context*

`response_text` is what epimemer returned. What lands in the model's context is
the MCP client's rendering of that, possibly truncated by the client, inside a
tool-result block we never see.

The panel is therefore labelled **"Response"**, not "Context". A panel
captioned "what the agent saw" would be making a claim the system cannot
verify.

### 3.2 Where records live, and retention

A bounded ring of 20 records in the session process, answered by the
`retrievals` read beside the snapshot. Each record is capped too:
`response_text` at 20,000 characters and `retrieved` at 200 ids, since a
`reflect` record can carry hundreds, and `truncated` says when either cap cut
something. The caps are applied when a record is built, so no caller can make
an uncapped one. Uncapped, this would be a copy of the graph held in memory and
served to any browser that connects.

Records also mirror into a hub-side ring hanging off the session, so they
survive session death: the normal case is opening the dashboard *after* a
retrieval looked wrong, and a session-only store is empty exactly then. The
guard is the bind, not the process:

- **Loopback bind (the default): full records mirror**, query and
  `response_text` included, under the same caps. Selector, focus mode and the
  Response tab all survive session death.
- **Non-loopback bind (`EPIMEMER_VIZ_HOST` names anything other than
  `127.0.0.1`, `::1` or `localhost`): structural metadata only.** `record_id`,
  `at`, `tool`, `graph`, `truncated` and the retrieved ids; no query text, no
  payload (`structural_only`). The Response tab for a dead session then says
  so plainly. While the session lives, the `retrievals` read serves the
  payload as before.

The strip happens at the producer: the session reads `config.viz_host`, the
same setting the hub binds, so a payload never leaves the process by that
route rather than travelling and being discarded on arrival.

The event carries the record as a plain dict, because `visualization/` sits
below `mcp/` and importing the record type upward would invert the layering.
The hub stamps `session_id` at ingest and keeps a ring per session, so records
from different sessions cannot mix; records also carry `graph` (§6).

**Stated assumption:** the identity unit is the MCP *process*, one per
conversation. A shared deployment (one Epimemer serving several agents) breaks
that: many conversations inside one session means `session_id` no longer
identifies the caller. That deployment needs a per-caller principal on the
record and hub authentication, neither of which exists, and both are things to
design then rather than fields to add now.

---

## 4. Focus mode

Selecting a record dims every node it did not return, in both panels, while
leaving them drawn, hoverable and clickable.

### 4.1 Desaturation, because opacity is taken

Status already owns opacity: active is full and every retired status is faded.
If focus mode also dimmed by opacity, *retired + retrieved* and *active +
not-retrieved* would land at the same alpha, one channel carrying two meanings.
So focus mode owns **saturation**.

Cytoscape has no saturation property, so desaturation is a computed colour.
That colour has one origin, with focus as an argument to it rather than a later
change: the theme recomputes every node's colour, and a focus applied
afterwards would be lost on a theme toggle, silently leaving the mode. One
`desaturate` in `theme.ts` serves both panels, because a second implementation
is how they would come to disagree. It mixes each channel toward the grey of
the same luminance, so lightness is untouched: a desaturation that also
darkened would be an opacity change wearing another name.

Leaving focus (`null`) and focusing on nothing (`[]`) are different: the second
dims everything, which is the honest picture of a search that returned nothing,
the same distinction as `retrieved` being `None` versus `[]`, one layer up.
Focus survives a snapshot reload: re-added nodes take their focus from the same
function, ids no longer in the graph are simply absent, and the selector keeps
its records.

### 4.2 Both panels, or the dashboard lies

Timeline marks are the same nodes. Dim only the graph and the two panels
disagree about what came back, so focus state lives above both panels, like the
theme does, and both are told on change.

### 4.3 Dimmed nodes stay live

Making them inert would remove the answer to the question the mode exists to
ask. Clicking a dimmed node opens its detail with a **"not in this
retrieval"** marker, so the absence is stated rather than implied by the
colour. The marker shows only when focus mode is on *and* the node was absent:
outside focus mode there is no retrieval to be absent from.

---

## 5. Where the response text appears, and when

### 5.1 Two tabs in the drawer, not one pane

Node detail follows your selection; a response follows the selected record.
In focus mode you want both at once: *here is what the agent got, and here is
the node I just clicked from inside it.* A single pane forces them to clobber
each other, so the drawer has two tabs, **Node** and **Response**, each
holding its own content.

### 5.2 Never pushed

The panels' shared rule, ambient signal and deliberate detail, applied to
retrievals:

| Event | What happens |
|---|---|
| A retrieval occurs | The header's record selector gains an entry, with an unread count. Nothing else moves. |
| You select a record | Focus mode engages, the Response tab fills, the drawer opens if hidden and shows that tab |
| You click a node | The Node tab fills and becomes active. The Response tab keeps its content |
| A retrieval occurs *while you are reading* | Selector updates. Drawer, active tab and focus mode are untouched |

The drawer stays fixed-height. The Response tab scrolls internally and never
resizes it: a drawer that grew with its text would re-lay out the panel under
the cursor.

---

## 6. Scoping

Records belong to a session **and** a graph, under the panels' shared scoping
rule: a session switch clears the selector, and a record from graph A never
highlights into graph B. Leaving focus mode restores both panels; switching
graphs leaves it.

---

## 7. Rejected

**Splitting `lexical` into exact-match and token-match provenance.** The
lexical arm has that distinction internally, and the argument for provenance
labels is the case for showing it. It stays one value: provenance is the
vocabulary the tool response already speaks (`search` puts `provenance:
"lexical"` on every node dict), so a new member would change what the agent
reads to serve a dashboard label. That belongs to whoever revisits the labels
(`docs/RETRIEVAL.md` §4).
