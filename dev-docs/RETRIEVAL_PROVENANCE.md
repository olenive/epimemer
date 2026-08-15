# Retrieval provenance: seeing what the agent was actually given

Design for a focus mode over the graph and timeline that dims everything the
last retrieval did *not* return, plus a record of the response text the agent
received, reachable from a list of recent retrievals.

Decided 2026-08-17. Nothing here is built yet.

The question it answers is not "what is in the graph" but **"what did the agent
see, and what did it miss?"** — and the second half is why the non-returned
nodes stay on screen rather than being filtered away.

---

## 1. Decisions

| # | Decision | Why |
|---|---|---|
| 1 | Cover **every** node-returning tool, not just `search` | A panel that greys a node the agent read via `topic_tree` a minute ago, labelled "not retrieved", teaches a false belief — in a system whose point is epistemic honesty |
| 2 | Focus mode dims by **desaturation**, never opacity | Opacity already encodes node status (§4.1) |
| 3 | Dimmed nodes stay **clickable** | The interesting click is on a dimmed node: *why didn't this come back?* |
| 4 | Response text lives in the **detail drawer**, as a second tab | §5 |
| 5 | Records live in the **session process**, fetched by RPC | Payloads are the largest and most sensitive thing the system holds |

Decision 5 was **revised on 2026-08-17** — records mirror into a hub-side ring
so they survive session death, with a non-loopback guard keeping payloads
session-side in the one configuration where exposure is real. See §3.2.

---

## 2. Coverage: one choke point, not six wrappers

Six tools put node identity or content into the agent's context:

| Tool | Serializer |
|---|---|
| `search`, `as_of`, `query_changes`, `find_nodes`, `query_graph` | `_node_to_dict` |
| `topic_tree` | `_content_preview` (id + preview — still "the agent saw this node") |

> **Corrected (2026-08-17, review): the census above counted serializer call
> sites, not tools.** The six are exactly the callers of `_node_to_dict` /
> `_content_preview` — but `check_conflicts` serializes candidate
> `{id, content, similarity}` dicts through its own path, `reflect` returns
> `pending_review` and similar-pair ids, and `list_sources` returns source ids
> and names. By decision 1's own rationale, a node the agent just examined via
> `check_conflicts` — the review loop's front door — would get the same false
> grey the decision exists to prevent. **Coverage is every tool whose response
> carries node ids**: the six above plus `check_conflicts`, `reflect`,
> `list_sources`. The rule that keeps this from drifting again is semantic,
> not an enumeration: `retrieved` is the set of node ids present in the
> response — what the agent saw, uniformly, per §3.1's response-not-context
> principle. `reflect` **scans** the whole active graph but the agent sees
> only the nominees, so a reflect record dims everything except them — which
> is accurate, and no special case.

Wrapping each one would be six insertions that drift. **Every tool already
passes through `_run_with_timeout` (`mcp/server.py:203-232`)**, which holds the
tool name, the complete result dict, the meta and the latency, and which is the
last thing to touch a response before `_build_response` serializes it.

That is where the record is written. One insertion, all six tools, and any
future tool covered by construction.

> **Corrected (2026-08-17, review): only the *record* is by construction; the
> *ids* are by declaration (§2.1), and a tool that never declares produces a
> silently-empty record.** Two changes close the gap. First,
> `retrieved: list[RetrievedNode] | None = None` — `None` means "this tool
> has not declared" (the wrapper flags the record undeclared), `[]` means
> "declared: nothing returned". Silent gaps become visible ones. Second, the
> §7 coverage test stops enumerating tools and becomes an **oracle**:
> parametrised over *all registered tools*, it seeds a known graph, calls
> each, and asserts any known node id appearing in the serialized response
> also appears in `retrieved`. Walking results for id shapes was rightly
> rejected in the wrapper — in a test the guess is safe (a miss merely
> under-checks), and the oracle is what actually catches the seventh tool.

### 2.1 Tools declare their ids; the wrapper does not guess

The alternative — walking an arbitrary result dict looking for id-shaped keys —
would guess differently per tool and break silently when a shape changes. Each
tool states its own instead, on the `ResponseMeta` it already returns:

```python
class ResponseMeta(BaseModel):
    ...
    # In-process only. `exclude=True` keeps these out of the `_meta` the agent
    # sees: ids are for the dashboard, and putting them on the wire would cost
    # the agent tokens to read a list it has no use for.
    # None = the tool never declared (flagged undeclared at the choke point);
    # [] = declared, nothing returned. The distinction is what makes a
    # forgotten declaration visible rather than silent (§2, corrected).
    retrieved: list[RetrievedNode] | None = Field(default=None, exclude=True)
```

`_build_response` serializes with `model_dump_json(by_alias=True)`
(`server.py:172`), which honours `exclude`, so this cannot leak into the
response by accident.

---

## 3. The record

```python
class RetrievalRecord(BaseModel):
    record_id: str            # monotonic, assigned here — not the hub's `seq`
    at: datetime
    tool: str                 # "epimemer.search", …
    query: str                # the query text, or a rendering of the arguments
    graph: str
    retrieved: list[RetrievedNode]
    response_text: str        # exactly what _build_response returned
    truncated: bool           # response_text hit the size cap

class RetrievedNode(BaseModel):
    node_id: str
    provenance: SeedProvenance
    score: float | None       # similarity or BM25, where the tool has one
```

`SeedProvenance` is the four-value enum from `LEXICAL_SEARCH.md` §6 —
`vector` / `lexical` / `segment` / `expanded` — plus `direct` for tools that
return nodes without ranking them (`find_nodes`, `as_of`, `query_changes`,
`topic_tree`).

> **Amended (2026-08-17, review), with the §2 census fix:** `check_conflicts`
> candidates are tagged `vector` with the cosine similarity as `score` — they
> genuinely are vector-similarity results, and no new enum value is needed.
> `reflect` and `list_sources` ids are `direct`. Records are made for every
> covered tool and every record appears in the selector — deciding which
> records are "real" retrievals would be a second census, drifting like the
> first. One sizing consequence for §3.2: a `reflect` record can carry
> hundreds of ids, so the ring's cap covers `retrieved` as well as
> `response_text`.

**Why an enum and not a boolean.** A flat "retrieved" set throws away the most
useful thing the feature produces. *This matched at 0.82; that one was dragged
in by an edge from it; this third one came back on an exact token match* is the
question you are actually asking when a search disappoints. Two tiers today,
four once lexical search lands — designing it as a boolean now means rebuilding
it later.

### 3.1 It is *our* response, not *the agent's context*

`response_text` is what epimemer returned. What lands in the model's context is
the MCP client's rendering of that, possibly truncated by the client, inside a
tool-result block we never see.

The panel is therefore labelled **"Response"**, not "Context". This is a small
wording decision and it is load-bearing: a panel captioned "what the agent saw"
would be making a claim the system cannot verify.

### 3.2 Where records live, and retention

A bounded ring in the session process — the same module as `EVENT_LOG.md` §4,
different instance and different placement. Log entries are small and structural
and live in the hub; these carry response payloads and stay in the process that
produced them, fetched by a `retrievals` RPC alongside `snapshot` and
`list_graphs` (`visualization/protocol.py:75`).

Backfill matters here for the same reason it does for the log: the normal case
is opening the dashboard *after* a retrieval looked wrong. A live-only stream is
empty exactly when you want it.

Bounded at ~20 records with a per-record size cap on `response_text`
(`truncated` says when it bit). Uncapped, this is a copy of the graph held in
memory and served to any browser that connects. The hub binds `127.0.0.1` by
default but `EPIMEMER_VIZ_HOST` overrides it (`visualization/hub.py:333`), so
the cap is a deliberate decision rather than an optimisation.

> **Revised (2026-08-17, review): records mirror into a hub-side ring, and
> the guard is the bind, not the process.** Session-side-only placement made
> records unreachable the moment the MCP process exited — the hub keeps
> disconnected sessions but raises on RPC to them (hub.py:139-140,
> `_mark_disconnected` at 222) — which is exactly the "open the dashboard
> after noticing" case this section calls normal. So:
>
> - **Loopback bind (the default): full records mirror to a hub ring hanging
>   off `sessions[sid]`** — query and `response_text` included, each under
>   the per-record caps above, the ring bounded as before, and the cap
>   covering `retrieved` too (§3). Selector, focus mode and the Response tab
>   all survive session death.
> - **Non-loopback bind: structural metadata only** — `record_id`, `tool`,
>   `at`, `graph`, counts and ids; no query text, no payload. The Response
>   tab for a dead session then says so plainly. While the session lives, the
>   `retrievals` RPC serves the payload as originally designed.
> - **Keying:** the hub stamps `session_id` at ingest (hub.py:197-200) and
>   ring placement is per-session by construction, so records from different
>   sessions cannot mix; records also carry `graph` (§6).
>
> **Stated assumptions, not built:** the identity unit is the MCP *process* —
> one process per conversation. A future shared deployment (one Epimemer
> serving multiple agents, e.g. cooperative KG access) breaks that: many
> conversations inside one session means `session_id` no longer identifies
> the caller. That deployment requires a per-caller principal on the record
> and hub authentication, neither of which exists; both are prerequisites to
> revisit *then*, per the design-when-picked-up rule, not fields to add now.

---

## 4. Focus mode

Selecting a record dims every node it did not return, in **both** panels, while
leaving them drawn, hoverable and clickable.

### 4.1 Desaturation, because opacity is taken

`statusOpacity` (`frontend/src/graph-panel.ts:89`) maps `active` → full and
every retired status → faded. The comment above it exists because two retired
states once drew as live (ISSUES.md #55).

If focus mode also dimmed by opacity, *retired + retrieved* and *active +
not-retrieved* would land at the same alpha — one channel carrying two meanings,
decided in two files, and #55 silently re-opened in one mode. That is the
pattern this codebase keeps producing.

So focus mode owns **saturation**; status keeps **opacity**.

**Cytoscape has no saturation property.** Nodes draw as
`background-color: data(color)` and `opacity: data(opacity)`
(`graph-panel.ts:141-142`), so desaturation is a *computed colour* written into
`data("color")`, not a separate channel the renderer blends for us.

That matters more than it sounds, because `applyTheme` recomputes `color` from
node type and theme **alone** (`graph-panel.ts:409-411`). Left as it is,
toggling the theme while focus mode is on restores every node to full
saturation and silently exits the mode — colour decided in two places, which is
the #56 failure exactly.

So the colour has one origin, and focus is an argument to it rather than a
later mutation:

```ts
/** The colour a node draws in. Every caller goes through here — including
 *  `applyTheme`, which is where focus state was previously lost. */
export const nodeFill = (nodeType: string, theme: Theme, inFocus: boolean): string =>
  inFocus ? nodeColor(nodeType, theme) : desaturate(nodeColor(nodeType, theme));
```

`statusOpacity` is untouched. The two channels never meet in a caller.

### 4.2 Both panels, or the dashboard lies

Timeline marks are the same nodes (`timeline-model.ts` builds them from nodes).
Dim only the graph and the two panels disagree about what came back — the class
of bug ISSUES.md #56 fixed for colour.

Focus state therefore lives above both panels in `main.ts`, like the theme does,
and both are told on change.

### 4.3 Dimmed nodes stay live

Making them inert would remove the answer to the question the mode exists to
ask. Clicking a dimmed node opens its detail with a **"not in this retrieval"**
marker, so the absence is stated rather than merely implied by the colour.

This is also cheaper: no hit-testing changes, no second interaction model.

### 4.4 Highlight has two silent failures to close first

Shared with `EVENT_LOG.md` §7, and they must be fixed once rather than twice:
`highlightNodes` no-ops on an unknown id, and the type filter sets
`display: none` (`graph-panel.ts:431-437`) so a highlight can land on something
invisible.

---

## 5. Where the response text appears, and when

### 5.1 Two tabs in the drawer, not one pane

The drawer (`index.html:153-159`) currently serves one thing: the selected
node's detail. Response text is a second thing with a **different driver and a
different lifetime** — node detail follows your selection, a response follows
the selected record.

In focus mode you want both at once: *here is what the agent got, and here is
the node I just clicked from inside it.* A single pane forces them to clobber
each other, so the drawer gets two tabs — **Node** and **Response** — each
holding its own content.

### 5.2 The answer to "when is it shown": never pushed

The rule the codebase already follows, from the pipeline strip: **ambient
signal, deliberate detail.** Tiles glow and counters tick on their own, but the
detail overlay opens only when you click a tile (`pipeline-strip.ts:1-9`).

Applied here:

| Event | What happens |
|---|---|
| A retrieval occurs | The header's record selector gains an entry, with an unread count. **Nothing else moves.** |
| You select a record | Focus mode engages, the Response tab fills, the drawer opens if hidden and shows that tab |
| You click a node | The Node tab fills and becomes active. The Response tab keeps its content |
| A retrieval occurs *while you are reading* | Selector updates. Drawer, active tab and focus mode are untouched |

A retrieval must never steal the drawer. The agent fires on the order of ten per
task, and a drawer that flipped content underneath you would be unreadable — and
would clobber a node detail you deliberately opened.

The drawer stays fixed-height (`h-40`). The Response tab scrolls internally and
never resizes it, for the reason already recorded in the markup: a drawer that
grew with its text would re-lay out the panel under the cursor.

---

## 6. Scoping

- Records belong to a session **and** a graph. A record from graph A must not
  highlight into graph B.
- A session switch clears the selector, following the pipeline strip's existing
  rule (`main.ts:339`).
- Leaving focus mode restores both panels; switching graphs leaves it.

---

## 7. Tests, written first

- `test_any_node_id_in_a_response_is_declared` — the §2 oracle (revised
  2026-08-17; replaces `test_every_node_returning_tool_records_its_ids`, which
  enumerated six tools and so could not catch a seventh): parametrised over
  **all registered tools**, seeds a known graph, calls each, asserts every
  known node id appearing in the serialized response also appears in
  `retrieved`. Catches new tools and census omissions alike.
- `test_an_undeclared_tool_is_flagged_not_silent` — §2.1's `None` vs `[]`:
  a tool that never sets `retrieved` yields a record marked undeclared, not an
  indistinguishable empty one.
- `test_rpc_hands_the_frontend_exactly_what_the_agent_saw` — integration
  (added 2026-08-17): a seeded test graph, real tool calls (`search`,
  `check_conflicts`, `reflect`), then fetch the records over the `retrievals`
  RPC as a browser would; assert each record's id set equals the node ids in
  that tool's serialized response. This is the feature's core invariant —
  *what the agent saw is what the frontend is told* — asserted through the
  surface the frontend actually uses, not through internals. A reflect record
  here also pins the §2 semantics: nominees only, not everything scanned.
- `test_retrieved_ids_are_not_serialized_to_the_agent` — §2.1's `exclude=True`.
  Guards a token-cost regression that no other test would notice.
- `test_focus_mode_leaves_status_opacity_alone` — §4.1. A retired-and-retrieved
  node and an active-not-retrieved node must remain distinguishable. This is
  #55's guard extended, and an opacity-based implementation fails it.
- `test_theme_toggle_preserves_focus_desaturation` — §4.1. The failure mode the
  `nodeFill` signature exists to prevent, and the one an implementation that
  mutates `data("color")` after the fact will hit.
- `test_focus_mode_applies_to_both_panels` — §4.2.
- `test_dimmed_node_stays_clickable_and_says_it_was_not_retrieved` — §4.3.
- `test_a_new_record_does_not_change_the_open_drawer` — §5.2, the whole
  interaction rule in one assertion.
- `test_response_tab_and_node_tab_keep_separate_content` — §5.1.
- `test_records_ring_is_bounded_and_caps_response_text` — §3.2.
- `test_records_survive_session_death_in_the_hub_ring` — §3.2 revised: kill
  the session, subscribe a browser, the selector still lists its records.
- `test_payloads_stay_session_side_on_nonloopback_bind` — §3.2's guard: with
  a non-loopback viz host, the hub ring holds structural metadata only.
- `test_records_never_mix_across_sessions` — two sessions, interleaved
  retrievals; each browser subscription sees only its session's records.
  Pins the per-session keying as a contract rather than an accident of
  placement.

---

## 8. Commit sequence

1. `RetrievalRecord` / `RetrievedNode` / `SeedProvenance`, and the ring (shared
   module with `EVENT_LOG.md` §4).
2. `ResponseMeta.retrieved` + every tool populating it (§2.1).
3. Recording at `_run_with_timeout`, plus the `retrievals` RPC (§2, §3.2).
4. `nodeAppearance` and the two silent-failure fixes in `highlightNodes`
   (§4.1, §4.4) — no UI yet, both panels ready.
5. Record selector in the header; focus mode across both panels.
6. Drawer tabs and the interaction rule (§5).

Steps 1–3 change nothing a user sees. Step 4 is shared with the event log and
should land whichever feature gets there first.

---

## 9. Open

- **Scores for unranked tools.** `find_nodes` and `as_of` have no score. Showing
  a blank is honest; showing 1.0 would be a lie. Leaning blank.
- **What "the last query" means for frame-scoped search**, which runs the
  retrieval net repeatedly in a widening loop (`mcp/tools.py:478-483`). One
  `search` call is one record — the loop is an implementation detail and must
  not appear as several.
- **Whether focus mode survives a snapshot reload.** It probably should, but the
  ids may no longer be present.
