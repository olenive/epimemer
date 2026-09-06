# Retrieval provenance: seeing what the agent was actually given

A focus mode over the graph and timeline that dims everything the last
retrieval did *not* return, plus a record of the response text the agent
received, reachable from a list of recent retrievals.

The question it answers is not "what is in the graph" but **"what did the agent
see, and what did it miss?"** The second half is why the non-returned nodes
stay on screen rather than being filtered away.

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

Every tool passes through `_run_with_timeout` in `mcp/server.py`, which holds
the tool name, the complete result dict, the meta and the latency, and is the
last thing to touch a response before `_build_response` serialises it. That is
where the record is written: one insertion, every tool, and any future tool
covered by construction.

Only the *record* is by construction. The *ids* are by declaration (§2.1), and
a tool that never declares would produce a silently empty record. Two things
close that gap:

- `retrieved: list[RetrievedNode] | None = None` on `ResponseMeta`. `None`
  means "this tool has not declared", and the wrapper flags the record as
  undeclared; `[]` means "declared: nothing returned". A forgotten declaration
  is visible rather than silent.
- The coverage test is an **oracle**, not a list:
  `test_any_node_id_in_a_response_is_declared` is parametrised over all
  registered tools, seeds a known graph, calls each, and asserts that any known
  node id appearing in the serialised response also appears in `retrieved`.
  Walking a result for id-shaped keys is wrong in the wrapper (§2.1) and safe
  in a test, where a miss merely under-checks. This test is what finds the next
  tool; it has done so every time a new tool has shipped.

`reflect` declares by walking its own result. Its nominee lists have as many
shapes as there are nominators, and a hand-written list of key paths is how the
next one would go undeclared. §2.1's objection to walking is about the choke
point guessing across tools it knows nothing about; a tool reading the
structure it built three lines earlier is the opposite case.
`test_a_reflect_record_names_its_nominees_not_its_scan` pins the semantics:
nominees, never the scan.

### 2.1 Tools declare their ids; the wrapper does not guess

Walking an arbitrary result dict looking for id-shaped keys would guess
differently per tool and break silently when a shape changes. Each tool states
its own instead, on the `ResponseMeta` it already returns:

```python
class ResponseMeta(BaseModel):
    ...
    # In-process only. `exclude=True` keeps these out of the `_meta` the agent
    # sees: ids are for the dashboard, and putting them on the wire would cost
    # the agent tokens to read a list it has no use for.
    retrieved: list[RetrievedNode] | None = Field(default=None, exclude=True)
```

`_build_response` serialises with `model_dump_json(by_alias=True)`, which
honours `exclude`, so this cannot leak into the response by accident.

---

## 3. The record

```python
class RetrievalRecord(BaseModel):
    record_id: str  # monotonic, assigned here, not the hub's `seq`
    at: datetime
    tool: str  # "epimemer.search", …
    query: str  # the query text, or a rendering of the arguments
    graph: str
    retrieved: list[RetrievedNode]
    response_text: str  # exactly what _build_response returned
    truncated: bool  # response_text hit the size cap


class RetrievedNode(BaseModel):
    node_id: str
    provenance: SeedProvenance
    score: float | None  # similarity or BM25, where the tool has one
```

`SeedProvenance` is the enum from `LEXICAL_SEARCH.md` §6 (`vector`,
`lexical`, `segment`, `expanded`) plus `direct` for tools that return nodes
without ranking them (`find_nodes`, `graph_as_of`, `query_changes`,
`topic_tree`, `reflect`, `list_sources`, and the write tools). `check_conflicts`
candidates are `vector` with the cosine similarity as `score`: they genuinely
are vector-similarity results.

**Why an enum and not a boolean.** A flat "retrieved" set throws away the most
useful thing the feature produces. *This matched at 0.82; that one was dragged
in by an edge from it; this third one came back on an exact token match* is
the question you are actually asking when a search disappoints.

**`score` is blank for `search` today.** `QueryResult` carries `provenance` and
no fused score, and a fused RRF number is not a similarity, so exposing one
would need its own explanation before it was worth showing. `check_conflicts`
is the one tool that declares a score. The field is there for whoever wants
the "matched at 0.82" line on a `search` record.

**Failed calls write no record.** There is no response to record, and the
selector lists what the agent was handed. The opposite is defensible (a failed
retrieval is interesting), so this is stated rather than left to be inferred.

**One call, one record.** The record is written at `_run_with_timeout`, which
sees the tool call and not the frame-widening loop inside `search`, so the
loop cannot appear as several records.

### 3.1 It is *our* response, not *the agent's context*

`response_text` is what epimemer returned. What lands in the model's context is
the MCP client's rendering of that, possibly truncated by the client, inside a
tool-result block we never see.

The panel is therefore labelled **"Response"**, not "Context". A panel
captioned "what the agent saw" would be making a claim the system cannot
verify.

### 3.2 Where records live, and retention

A bounded ring in the session process (`visualization/ring.py`, capacity 20,
with a per-record size cap on `response_text` that `truncated` reports and a
cap on `retrieved`, since a `reflect` record can carry hundreds of ids),
served by a `retrievals` RPC alongside `snapshot` and `list_graphs`. Uncapped,
this would be a copy of the graph held in memory and served to any browser
that connects.

Records also mirror into a hub-side ring hanging off the session, so they
survive session death: the normal case is opening the dashboard *after* a
retrieval looked wrong, and a session-only store is empty exactly then. The
guard is the bind, not the process:

- **Loopback bind (the default): full records mirror**, query and
  `response_text` included, under the same caps. Selector, focus mode and the
  Response tab all survive session death.
- **Non-loopback bind (`EPIMEMER_VIZ_HOST` set): structural metadata only.**
  `record_id`, `tool`, `at`, `graph`, counts and ids; no query text, no
  payload. The Response tab for a dead session then says so plainly. While the
  session lives, the `retrievals` RPC serves the payload as before.

The strip happens at the producer: the session reads `config.viz_host`, the
same setting the hub binds, so a payload never leaves the process by that
route rather than travelling and being discarded on arrival.

Records travel as events carried opaquely. `RetrievalRecorded.record` is a
plain dict rather than a typed field, because `visualization/` sits below
`mcp/` and importing the record type upward would invert the layering, the
same reason `PublishEvent.payload` is opaque to the hub.

The hub stamps `session_id` at ingest and ring placement is per session, so
records from different sessions cannot mix; records also carry `graph` (§6).

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

`statusOpacity` maps `active` to full and every retired status to faded. If
focus mode also dimmed by opacity, *retired + retrieved* and *active +
not-retrieved* would land at the same alpha: one channel carrying two meanings,
decided in two files. So focus mode owns **saturation**; status keeps
**opacity**.

Cytoscape has no saturation property. Nodes draw as `background-color:
data(color)`, so desaturation is a computed colour written into `data("color")`,
not a separate channel the renderer blends. That matters because `applyTheme`
recomputes `color` from node type and theme; left alone, toggling the theme
while focus mode is on would restore every node to full saturation and
silently exit the mode. So the colour has one origin, and focus is an argument
to it rather than a later mutation:

```ts
/** The colour a node draws in. Every caller goes through here, including
 *  `applyTheme`. */
export const nodeFill = (nodeType: string, theme: Theme, inFocus: boolean): string =>
  inFocus ? nodeColor(nodeType, theme) : desaturate(nodeColor(nodeType, theme));
```

`desaturate` lives in `theme.ts`, not in a panel, because both panels dim and
a second implementation is how they would come to disagree. It mixes each
channel toward the grey of the same luminance, so lightness is untouched: a
desaturation that also darkened would be an opacity change wearing another
name.

`setFocus(null)` leaves the mode; `setFocus([])` dims everything, which is the
honest picture of a search that returned nothing. The distinction is the same
as `retrieved` being `None` versus `[]`, one layer up. Focus survives a
snapshot reload: nodes are re-added through `nodeFill(…, inFocus(id))`, ids no
longer in the graph are simply absent, and the selector keeps its records.

### 4.2 Both panels, or the dashboard lies

Timeline marks are the same nodes (`timeline-model.ts` builds them from
nodes). Dim only the graph and the two panels disagree about what came back.
Focus state therefore lives above both panels in `main.ts`, like the theme
does, and both are told on change.

### 4.3 Dimmed nodes stay live

Making them inert would remove the answer to the question the mode exists to
ask. Clicking a dimmed node opens its detail with a **"not in this
retrieval"** marker, so the absence is stated rather than implied by the
colour. `notRetrievedMarker(inFocus, focusOn)` is empty unless focus mode is on
*and* the node was absent: outside focus mode there is no retrieval to be
absent from. This is also cheaper: no hit-testing changes, no second
interaction model.

### 4.4 Highlight's two silent failures

Shared with `EVENT_LOG.md` §7, and fixed once rather than twice:
`highlightNodes` must not no-op on an unknown id, and the type filter's
`display: none` must not let a highlight land on something invisible.

---

## 5. Where the response text appears, and when

### 5.1 Two tabs in the drawer, not one pane

Node detail follows your selection; a response follows the selected record.
In focus mode you want both at once: *here is what the agent got, and here is
the node I just clicked from inside it.* A single pane forces them to clobber
each other, so the drawer has two tabs, **Node** and **Response**, each
holding its own content.

### 5.2 Never pushed

The rule the codebase already follows, from the pipeline strip: **ambient
signal, deliberate detail.** Tiles glow and counters tick on their own, but the
detail overlay opens only on a click.

| Event | What happens |
|---|---|
| A retrieval occurs | The header's record selector gains an entry, with an unread count. Nothing else moves. |
| You select a record | Focus mode engages, the Response tab fills, the drawer opens if hidden and shows that tab |
| You click a node | The Node tab fills and becomes active. The Response tab keeps its content |
| A retrieval occurs *while you are reading* | Selector updates. Drawer, active tab and focus mode are untouched |

A retrieval must never steal the drawer. The agent fires on the order of ten
per task, and a drawer that flipped content underneath you would be
unreadable, and would clobber a node detail you deliberately opened.

The drawer stays fixed-height. The Response tab scrolls internally and never
resizes it: a drawer that grew with its text would re-lay out the panel under
the cursor.

---

## 6. Scoping

- Records belong to a session **and** a graph. A record from graph A must not
  highlight into graph B.
- A session switch clears the selector, following the pipeline strip's rule.
- Leaving focus mode restores both panels; switching graphs leaves it.

---

## 7. Tests

`tests/mcp/test_retrieval_declaration.py`, `test_retrieval_rpc.py`,
`test_retrieval_records.py`, `test_retrieval_recording.py`,
`tests/visualization/test_hub.py`, and the frontend suites `focus.test.ts`
and `drawer.test.ts`. Three are load-bearing:

- The §2 oracle, `test_any_node_id_in_a_response_is_declared`, parametrised
  over all registered tools.
- `test_rpc_hands_the_frontend_exactly_what_the_agent_saw` runs a live hub, a
  live session client and real tool calls, then fetches over `/api/retrievals`
  as a browser does.
- `test_records_survive_session_death_in_the_hub_ring` kills the session and
  subscribes a browser. Neither of the last two alone would catch a record
  stored on the wrong side.

Cytoscape cannot be instantiated under jsdom (no canvas), so the graph panel's
rules are tested as pure functions (`nodeFill`, `refreshedFill`) and the
drawer's through real DOM.

---

## 8. Rejected

**Splitting `lexical` into exact-match and token-match provenance.** The
lexical arm has that distinction internally, and §3's own argument is the case
for showing it. It stays one value: provenance is the vocabulary the tool
response already speaks (`search` puts `provenance: "lexical"` on every node
dict), so a new member would change what the agent reads to serve a dashboard
label. That belongs to whoever revisits `LEXICAL_SEARCH.md` §6.

**A typed `retrieved` on the wire.** Ids are for the dashboard; putting them
in `_meta` costs the agent tokens for a list it has no use for (§2.1).
