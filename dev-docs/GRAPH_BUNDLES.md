# Graph bundles: export, import, backup

One graph written out as plain data files, and an import that rebuilds it on
either backend. The code is `epimemer/pipelines/transfer.py`, the CLI is the
`graphs` group in `epimemer/cli.py`, and the agent-facing half is
`backup_graph`. What follows is what a maintainer needs to change any of it
safely.

---

## 1. The layout

A bundle is a flat set of files: `manifest.json` plus one `.jsonl` per section.
The compressed form is a `tar.gz` holding those files at the archive root, named
`<graph>-<YYYY-MM-DD>.epimemer.tar.gz`; `--plain` writes the same files into a
directory. `read_bundle` takes either, deciding on whether the path is a
directory.

| File | Holds |
|---|---|
| `manifest.json` | One object: `format_version`, `epimemer_version`, `exported_at`, `graph`, `embedding_provider`, `embedding_model_id`, and `counts` per section |
| `metacontexts.jsonl` | Every metacontext, at every status |
| `agents.jsonl` | The judge records |
| `approved_agents.jsonl` | One `{"agent_id": ...}` per id the user admitted |
| `nodes.jsonl` | Every node at every status, each line `{"node_type": ..., "node": {...}}` |
| `edges.jsonl` | Every edge |
| `decisions.jsonl` | The decision journal |
| `relation_labels.jsonl` | The relation vocabulary |
| `relation_verdicts.jsonl` | The verdicts on label pairs |
| `timelines.jsonl` | Timelines, with their timepoints embedded |
| `documents.jsonl` | Raw documents |
| `segments.jsonl` | Segments |
| `settings.jsonl` | One record: the per-graph settings, including both counters and both threshold overrides |

Every section writes a file even when it is empty, so a missing file always
means a damaged bundle rather than an empty graph. `settings` counts 1 for the
same reason: a zero there would read as a graph with no settings.

**`node_type` is recorded, not inferred.** A `Fact` and an `Inference` carry the
same fields, so a line holding only the fields cannot say which class wrote it.

**Embeddings are not in the bundle.** They are the one derived part of a graph,
the part that does not compress, and cheap to recompute. Import re-embeds every
node through `embedding_text`, which is the single definition of what a node
embeds on: computing it a second way here is how a restored graph would stop
matching itself. The manifest records what the graph *was* embedded with, so a
restore onto a different model reports it rather than doing it quietly. That is
also the model-migration path: export, change `EPIMEMER_EMBEDDING_MODEL_ID`,
import.

---

## 2. Byte-identical, and the rules that make it so

Two exports of one graph produce the same bytes. That is a property to preserve,
not a nicety: it is what makes the round-trip test below able to say *lossless*
rather than *nothing obviously missing*.

- Records within a section are **sorted by id**. A backend may return rows in
  any order it likes.
- Keys within a record are **sorted**, separators are tight, and each record is
  one line.
- Datetimes are rendered by `_iso`: UTC, always to the microsecond, always with
  an explicit `+00:00`. Pydantic's JSON mode drops the fractional part when it
  is zero and renders UTC as `Z`, so without this the same instant comes back
  spelled two ways depending on the clock it was written by.
- Floats go through Python's `repr`, which is what `json.dumps` does. A float
  written that way and read back is exactly equal.
- Models are dumped with `mode="python"` and rendered by `_json_default`, so the
  datetime rule has one home. Dumping in JSON mode would have Pydantic render
  the timestamps first and leave nothing here to normalize.

`exported_at` is the one field that cannot be a function of the graph, which is
why `export_graph` takes it as an argument and the round-trip comparison leaves
the manifest out.

---

## 3. The verbatim-write rule

**Import must not go through the ordinary writers.** Three of them derive
something:

- `store_relation_label` merges the row against whatever is recorded under
  `(name, kind)` and hands back that record's id.
- `record_decision` renders `decided_at` through the journal's padding, and
  `journal()` above it stamps the instant.
- The counter methods move a number rather than setting one, and `claim_agent`
  writes `last_seen_at`.

So the protocol carries `write_verbatim_tx`, which takes every section and
writes each record exactly as given. It is **insert-only**, on `write_batch_tx`'s
terms: the target graph did not exist a moment ago, so every id is new. It is
atomic, and it is implemented on both backends with parity tests in
`tests/storage/test_storage_parity.py`.

Two renderings survive inside it and are not stamps: an edge's `type` is written
as the enum's value, and a journal row's `decided_at` goes through
`_decision_row`. The second is required — `decided_at` is indexed and compared
as text, so the padded rendering is what makes a later range query correct.

`set_reflect_counter` and `set_backup_counter` exist for the same reason and
have the same single caller. Everything else in the settings section already had
a whole-record setter.

**Do not reach for `write_verbatim_tx` to skip a writer's reasoning.** Every
other path stamps for a reason. Import is the caller; a second one needs an
argument.

---

## 4. Import creates a new graph

`import_graph` refuses when the target name is already in `list_databases()`,
and there is no override flag. Replacing a graph is `delete_graph` and then
import: two acts, each of which the user can be asked about separately.

On any failure it switches back to the graph the caller was on, drops the
partial graph, and re-raises with the reason, so a retry is never blocked by
wreckage. It restores the caller's active graph on success too: a function that
silently moved the graph out from under its caller would be the wrong-graph
incident with a new cause.

After writing, it re-exports the graph and compares `section_counts` against the
manifest. Re-exporting rather than a second set of counting reads is deliberate:
the counts have to be in the manifest's terms, and a second definition of *what
a section is* is the thing that would drift.

---

## 5. Format versioning

`BUNDLE_FORMAT_VERSION` starts at 1.

- **Newer than the code** is refused outright, by `read_bundle`. Nothing is
  coerced and nothing is guessed at.
- **Older** imports, with model defaults filling in whatever it does not carry —
  the same thing an old storage row does when a field is added — and
  `ImportReport.older_format` says so, which the CLI prints.

Bump the version when a reader of the current code could not make sense of what
a writer produces: a section removed, a section's records reshaped, a field
whose meaning changed under the same name. **Adding** a field or a section is
not a bump.

---

## 6. The round-trip test is the definition of lossless

`tests/pipelines/test_transfer.py` builds a rich graph on one backend, exports
it, imports it into a fresh graph on the **other** backend, exports that, and
compares the files byte for byte. It runs both directions, because the two find
different rendering differences. A second test compares every whole-graph
protocol read on the two graphs, ids and timestamps included; a third asserts
the re-embedded vectors equal the originals under the same provider.

The graph is built by hand rather than driven through the tools, and that is the
point: the fields an export can silently drop are the ones the tools do not
routinely write — a closed lifecycle episode, a second retirement, a value
signal with every clock set, a relation label with a description, a timeline
with a reference time, a journal row with no judge. `test_every_section_actually_carries_something`
is the control, since every other assertion passes over an empty graph.

**Before each release, run the same test against a real long-lived graph.**
Point the CLI at the served store, export, and verify the bundle against the
test database:

```bash
export EPIMEMER_STORAGE_BACKEND=surrealdb
export EPIMEMER_SURREALDB_URL=ws://localhost:8000/rpc
export EPIMEMER_EMBEDDING_PROVIDER=mock   # the vectors are not being compared here

epimemer graphs export <graph> --to /tmp/
epimemer graphs verify /tmp/<graph>-<date>.epimemer.tar.gz
```

`verify` imports the bundle into a scratch graph named `verify-<random>`,
exports that, compares every file, and drops the scratch graph whether the
comparison passed or not. A mismatch names the sections that came back
different. Run it against the largest graph available: the classes of bug it
catches — a field nobody thought to read, a timestamp one backend renders
differently — do not appear on a graph built by a test.

---

## 7. Destinations

`--to` and `EPIMEMER_BACKUP_DESTINATION` both go through `fsspec.open`, so a
local path, a `gs://` URL and an `s3://` URL are one code path. `fsspec` is a
core dependency: it is pure Python with no dependencies of its own, and a local
path has to work with nothing installed. The cloud drivers are the optional
extras `epimemer[gcs]` and `epimemer[s3]`.

`unreachable_destination` is called before the graph is read, so a missing
driver is a refusal naming the extra rather than an `ImportError` from inside
the write. **Credentials come from each provider's own standard chain and
Epimemer reads none**, so a URL this accepts can still be refused by the
service, and that refusal is the service's to explain.

`EPIMEMER_BACKUP_KEEP` is retention: with a count set, `backup_graph` removes
the older bundles of the graph it just wrote, keeping the newest that many, and
reports `kept` and `removed`. Unset it keeps everything, which is the default
because deleting a backup has to be asked for. Three rules make it safe.
`stale_bundles` matches only `<graph>-<YYYY-MM-DD>.epimemer.tar.gz`, anchored on
the date, so a graph named `notes` never claims the bundles of `notes-archive`
and a `--plain` directory matches nothing. It orders by the date in the
filename, never by modification time: an object store stamps mtime at upload and
a copied folder loses it, so mtime order is the order bundles arrived rather
than the order the graph was written in. And the prune runs only once
`write_bundle` has returned, so a failed backup removes nothing; a delete that
fails after that is reported in `removal_failed` rather than raised, because by
then the graph is already written out.

Retention is a property of the destination, which one server fills for every
graph it opens, so there is no per-graph override and no MCP tool to set it. An
agent that could raise the count could also lower it to 1.

Git hosting is not a destination. Bundles are full snapshots and private memory
does not belong on a forge; a user who wants it points a local path at a
repository they commit themselves.

---

## 8. Prompting, not scheduling

Nothing polls and nothing runs in the background. `stores_since_backup` moves
store for store with the reflect counter and is zeroed by a successful backup,
so `store_decomposition`, `apply_reflection` and `graph_stats` can report
`backup_suggested` the way they report `reflect_suggested`. The threshold is the
`reflect_threshold` pattern exactly: `EPIMEMER_BACKUP_THRESHOLD` as the process
default, a per-graph override on the backend, and `resolve_backup_threshold`.

Two counters rather than one, because they are zeroed by different acts. One
counter would have to be reset by whichever came first, and the other question
would go unanswered.

The counter is bumped where the reflect counter is bumped, which today is
`store_decomposition` alone. `apply_reflection` **reports** the numbers without
moving them: it is the moment an agent is already thinking about the state of
the graph, which makes it a good place to see that a backup is due, but the
count is of stores and applying a reflection is not one. If reflect application
ever starts bumping the reflect counter, bump this one in the same place.

`backup_graph` takes **no path**. An agent acting on the prompt should be able
to do the thing without also choosing where a graph goes, and a tool that took a
path would put that choice in the agent's hands. It needs no judge, because a
backup asserts nothing about the graph. It zeroes the counter only after the
write returns: a counter cleared by an attempt would say a graph was safe on the
strength of a write that failed.
