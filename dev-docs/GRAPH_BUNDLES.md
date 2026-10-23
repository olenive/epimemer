# Graph bundles: export, import, backup

One graph written out as plain data files, and an import that rebuilds it on
any backend. The code is `epimemer/pipelines/transfer.py`, the `graphs` group
in `epimemer/cli.py` (`list`, `export`, `import`, `convert`, `verify`), and the
`backup_graph` and `configure_backup` tools. README.md's *Backup and restore*
describes the commands for someone using them; what follows is what a
maintainer needs to change any of it safely.

---

## 1. The layout

A bundle is `manifest.json` plus one `.jsonl` per section, compressed into a
`tar.gz` or, with `--plain`, written into a directory; `SECTIONS` in
`transfer.py` lists them.

Every section writes a file even when it is empty, so a missing file always
means a damaged bundle rather than an empty graph. `settings` is one
`GraphSettings` record: the graph's overrides (require judge, reflect and
backup thresholds, merge, deferral and warning overrides), its two store
counters and its reflect round, which deferrals are measured on. It counts 1
for the same reason: a zero there would read as a graph with no settings.

**`node_type` is recorded, not inferred.** A `Fact` and an `Inference` carry the
same fields, so a line holding only the fields cannot say which class wrote it.

**Embeddings are not in the bundle.** They are the one derived part of a graph,
the part that does not compress, and cheap to recompute. Import re-embeds every
node through `embedding_text`, which is the single definition of what a node
embeds on: computing it a second way here is how a restored graph would stop
matching itself. The manifest records what the graph *was* embedded with, so a
restore onto a different model reports it rather than doing it quietly.

---

## 2. Byte-identical, and the rules that make it so

Two exports of one graph produce the same bytes, which is what lets the
round-trip test below say *lossless* rather than *nothing obviously missing*.

- Records within a section are **sorted by id**. A backend may return rows in
  any order it likes.
- Keys within a record are **sorted**, separators are tight, and each record is
  one line.
- Datetimes are UTC, always to the microsecond, always with an explicit
  `+00:00`. Pydantic's JSON mode drops the fractional part when it is zero and
  renders UTC as `Z`, so without this the same instant comes back spelled two
  ways depending on the clock it was written by. Models are dumped in Python
  mode for that reason, so the datetime rule has one home.
- Lengths of time (a recurrence's period and duration) are ISO-8601
  durations, the spelling Pydantic gives a `timedelta`. A length is not an
  instant, so the datetime rule has nothing to say about it.
- Floats go through Python's `repr`, which is what `json.dumps` does. A float
  written that way and read back is exactly equal.

`exported_at` is the one field that cannot be a function of the graph, which is
why `export_graph` takes it as an argument and the round-trip comparison leaves
the manifest out.

---

## 3. The verbatim-write rule

**Import must not go through the ordinary writers.** Four of them derive
something:

- `store_relation_label` merges the row against whatever is recorded under
  `(name, kind)` and hands back that record's id.
- `record_decision` renders `decided_at` through the journal's padding, and
  `journal()` above it stamps the instant.
- The counter methods move a number rather than setting one, and `claim_agent`
  writes `last_seen_at`.
- `store_timeline` stores one more than the `version` it was given
  (`TIMELINES.md` §2.6), so a timeline restored through it would come back a
  version ahead of the one exported.

So the protocol carries `write_verbatim_tx`, which takes every section and
writes each record exactly as given. It is **insert-only**, on `write_batch_tx`'s
terms: the target graph did not exist a moment ago, so every id is new. It is
atomic, and every backend implements it.

Two renderings survive inside it and are not stamps: an edge's `type` is written
as the enum's value, and a journal row's `decided_at` goes through each
backend's own row rendering. The second is required: on SurrealDB and SQLite
`decided_at` is indexed and compared as text, so the padded rendering is what
makes a later range query correct, and on Postgres it is a `timestamptz`.

`set_reflect_counter` and `set_backup_counter` exist for the same reason and
have the same single caller. `set_reflect_round` restores the round deferrals
are measured on, and is import's alone too: a completed reflect moves the round
through `complete_reflect_round`, which zeroes the counter and advances the
round in one atomic step. A round is a reflect that completes with at least one
store since the previous reflect.

A deferral cap above the 1024-round ceiling is imported as it is stored. The
ceiling is enforced where a value is written and applied where one is
resolved, so such a bundle restores, and the value behaves as the ceiling.

**Do not reach for `write_verbatim_tx` to skip a writer's reasoning.** Every
other path stamps for a reason. Import is the caller; a second one needs an
argument.

---

## 4. Import creates a new graph

`import_graph` refuses a target name that already exists, and there is no
override flag. Replacing a graph is `delete_graph` and then import: two acts,
each of which the user can be asked about separately.

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
- **Older** imports, with model defaults filling in whatever it does not carry,
  the same thing an old storage row does when a field is added, and
  `ImportReport.older_format` says so, which the CLI prints.

Bump the version when a reader of the current code could not make sense of what
a writer produces: a section removed, a section's records reshaped, a field
whose meaning changed under the same name. **Adding** a field or a section is
not a bump.

---

## 6. The round-trip test is the definition of lossless

The round-trip test exports a rich graph, imports it into a fresh graph on
another backend, exports that, and compares the files byte for byte, in both
directions, because the two directions find different rendering differences.

The graph is built by hand rather than driven through the tools, and that is the
point: the fields an export can silently drop are the ones the tools do not
routinely write: a closed lifecycle episode, a second retirement, a value
signal with every clock set, a relation label with a description, a timeline
with a reference time, a journal row with no judge. A control asserts that
every section actually carries something, since every other assertion passes
over an empty graph.

**Before each release, run the same check against a real long-lived graph.**
Point the CLI at the served store, export, and verify the bundle, which imports
it into a scratch graph on that same store:

```bash
export EPIMEMER_STORAGE_BACKEND=surrealdb
export EPIMEMER_SURREALDB_URL=ws://localhost:8000/rpc
export EPIMEMER_EMBEDDING_PROVIDER=mock   # the vectors are not being compared here

epimemer graphs export <graph> --to /tmp/
epimemer graphs verify /tmp/<graph>-<date>.epimemer.tar.gz
```

`verify` imports the bundle into a scratch graph named `verify-<random>`,
exports that, compares every file, names any section that came back
different, and drops the scratch graph either way. Run it against the largest
graph available: a field nobody thought to read or a timestamp one backend
renders differently does not appear on a graph built by a test.

---

## 7. Destinations

`--to` and `EPIMEMER_BACKUP_DESTINATION` both go through `fsspec.open`, so a
local path, a `gs://` URL and an `s3://` URL are one code path. `fsspec` is a
core dependency: it is pure Python with no dependencies of its own, and a local
path has to work with nothing installed. The destination is checked before the
graph is read, so a missing driver is a refusal naming the extra rather than an
`ImportError` from inside the write.

`EPIMEMER_BACKUP_KEEP` is retention, and unset it keeps everything, because
deleting a backup has to be asked for. Three rules make it safe.
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

Git hosting is not a destination, because bundles are full snapshots of
private memory; a user who wants one in a repository points a local path at a
checkout and commits it themselves.

---

## 8. Convert: a bundle that never touches disk

`epimemer graphs convert` is export and import in one process. The destination
is a second `ServerConfig` built from the `--to` flags, and `create_storage`
opens each side, so the command knows nothing about either backend. Every rule
above holds unchanged: the destination graph must not exist, records land
verbatim, vectors are recomputed. The in-memory store is refused as a
destination, since it would be gone at exit.

## 9. Prompting, not scheduling

Nothing polls and nothing runs in the background. `stores_since_backup` moves
store for store with the reflect counter and is zeroed by a successful backup,
so `store_decomposition`, `apply_reflection` and `graph_stats` can report
`backup_suggested` the way they report `reflect_suggested`. The threshold
follows the `reflect_threshold` pattern, with `configure_backup` setting the
per-graph override.

Two counters rather than one, because reflecting zeroes one and a backup the
other.

The counter is bumped where the reflect counter is bumped, which is
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
