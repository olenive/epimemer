# Advisories on the dashboard

**Status: built**, 2026-09-18. The event, the warning rows in the log and the
read-only settings panel are all in the code; §7 names the documents that
moved with them.

Design for showing warnings to a person watching the dashboard. Decided with
the user on 2026-09-18, one question at a time. Vocabulary: the Python class is
`Advisory`; on the wire and in this document a warning is what the agent
receives under `warnings`, and the two words mean the same thing.

## 1. What exists

A warning is computed inside a tool call, before or after the write, from what
the call was about. Five kinds (`epimemer/core/advisories.py`): two premises
that provably never overlapped in time, a contradiction recorded across
metacontexts, a variant recorded inside one metacontext, a contradiction
recorded inside one metacontext, and a tag description that was supplied but
kept as the graph already had it. The tool returns the warning to the agent,
journals a "proceeded despite advisory" row when the warning objects to the
call, and that is where it stops. The dashboard shows the act (the write) and
nothing of the warning.

A per-graph policy (`configure_warnings`) can mute surfacing, so the agent's
response carries no warnings, and can set each kind to `proceed` or `flag`.
The journal row is written regardless of the mute. A kind explicitly set to
`flag` outranks the mute.

`reflect` writes nothing and attaches a warning to each merge candidate whose
premises never overlapped. It resolves the same policy as the tools that write:
a muted kind is stripped from the candidate before the response goes out, and
the candidate itself still arrives.

## 2. The four decisions

### 2.1 A new event, not a field on the act

The dashboard hears about a warning through a new event, `advisory_raised`,
published by the server layer at the point where the tool has computed its
warnings, in the same way `retrieval_recorded` is published from the server
rather than from storage.

Why not put the warning on the act: acts are published by the storage wrapper,
which sees storage calls only. The warning is computed one layer up, in the
tool, and getting it onto the act would mean passing it down through the
storage protocol. A call can also produce several acts, so there is no single
act to put it on. And `reflect` produces warnings with no act at all.

### 2.2 Everything computed is published, with whether the agent saw it

The event carries every warning the tool computed, muted or not, with two
booleans: `surfaced` (was it in the agent's response) and `notify_user` (was
the agent told to raise it with the user). The dashboard is where a person
looks at what the agent was not told, so hiding muted warnings there would
defeat the point. This matches the journal, which already records regardless
of the mute.

`reflect`'s candidate warnings publish on the same terms: `surfaced: false` for
one the mute stripped from the candidate, `true` for one the agent was shown,
and `notify_user` true only where a surfaced warning resolves to `flag`.

### 2.3 A row in the live log, not a separate panel

A warning is a line in the story of a session, beside the act it accompanied.
The live log gains warning rows, a `warned` chip in the verb filter so warnings
can be shown alone or hidden, and click-to-highlight of the subjects as act
rows already have. Warnings join the per-session replay ring so a browser
opened later still sees them.

No unread badge. The dashboard has no notion of "read", and adding one for
warnings alone is a separate feature if the log turns out not to be enough.

### 2.4 Settings are shown, not changed

A small per-graph panel, with the graph name in its title, shows the mute and,
for each kind, the action in force and whether it is inherited from the process
default or set on this graph. Nothing on the dashboard writes a setting.

Why: a write from the browser would be the first write into a graph with no
author. Every change today is journalled against a session and a judge, and a
browser has neither. That is the identity question parked under "Sharing a
graph between users" in `PROPOSED_FEATURES.md`, and this feature must not
settle it by accident.

## 3. The event

```
advisory_raised
  graph         the graph the call ran against
  tool          the tool name, e.g. "record_contradiction"
  kind          the warning kind, e.g. "cross_metacontext"
  message       the warning's one-line message, word for word
  subjects      node ids the warning is about
  detail        the warning's detail dict, as the agent receives it
  action        the resolved action for this kind on this graph: proceed | flag
  surfaced      true when the warning was in the agent's response
  notify_user   true when the agent was told to raise it with the user
  judged_by     the judge on the call, as acts carry it, or null
```

One event per warning, not per call: a call that raises two warnings publishes
two events. Category `GRAPH`. Events are published in the order the warnings
were computed, after the acts of the same call, so the log's arrival order puts
a warning after what it was about. There is no explicit link from a warning to
an act: acts are numbered by the storage wrapper and the tool never sees the
number, and arrival order is enough for a log.

Publishing happens in one place, `carry_advisories`, which every tool that
raises warnings already calls, and which is the one function that knows the
policy and so can compute `surfaced` and `notify_user`. It receives the event
bus as a value, the way the server passes it to `segment`, and does nothing
when there is none. `reflect` publishes its candidate warnings itself, from the
`reflect` tool, under the policy it resolved for the same response.

The hub relays the event as it relays acts, replays it from the ring, and
applies the per-browser graph filter to it. The ring keeps warnings and acts in
one sequence, because replay has to reproduce arrival order.

## 4. The log row

A warning row reads: the kind's message, word for word, prefixed by the tool
it came from, for example

    record_contradiction warned: a contradiction recorded across metacontexts,
    which is the wrong tool; the agent proceeded

with "the agent proceeded" or "flagged to the user" from `action`. The row's
marker uses the `pending` hue from `SemanticPalette` in `theme.ts`; no new
colour. A warning the agent was not shown (`surfaced: false`) draws dimmer,
with "not shown to the agent" in its tooltip. Click highlights `subjects`.

In `log-store.ts`, a `LogEntry` gains a `kind` of `act` or `warning`; the verb
of a warning entry is `warned`, which the existing verb filter and `verbLabel`
then handle without special cases. The substring, node id and time filters
apply to warning rows as they do to acts.

## 5. The settings panel

Read-only. Title: "Warnings on <graph>". One line for the mute ("warnings
shown to the agent" or "warnings muted for the agent"). Then one row per kind:
the kind, the action in force, and "inherited" or "set on this graph". The
distinction is the one `configure_warnings`'s response already makes: a kind
absent from `overridden.by_kind` is inherited. So the panel asks for exactly
that response, over the same request path the graphs list uses (a hub RPC to
the session), and renders it. It re-fetches on `graph_switched`, as the reflect
badge does.

## 6. Tests first

- `tests/visualization/`: the event's shape; `carry_advisories` publishes one
  event per warning with `surfaced` false on a muted graph and true otherwise,
  and publishes nothing when there is no bus; `reflect` publishes its
  candidates' warnings; the hub replays warnings in sequence with acts.
- `frontend/src/log-store.test.ts`: a warning entry from an event, `warned`
  in `verbsIn`, filters apply.
- `frontend/src/log-panel` tests: the row text, the dim state, highlight on
  click.
- Settings panel: renders inherited and set-here from the tool's response
  shape; refetches on graph switch.
- `tests/mcp/test_boundary_exposes_the_implementation.py` or its neighbour:
  the agent's response is unchanged by this work.

## 7. Docs brought current with it

`EVENT_LOG.md` (§12 for the event, and §9's boundary count, which the code
has at nine), `WARNINGS_AND_SETTINGS.md` §3 (the five kinds),
`VISUALISATION.md` (the log row and the panel), the dashboard section of
`README.md`, and `CHANGELOG.md`. `reflect`'s warnings bypassed the mute when
this was written, which has since been fixed: it resolves the policy like every
other tool.
