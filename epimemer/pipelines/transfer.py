"""Graph bundles: one graph written out as plain data, and read back.

A bundle is a directory of JSON Lines files plus a `manifest.json`, usually
compressed into a single `tar.gz`. It holds everything a graph is except its
vectors: the metacontexts, the judges and the ids a user approved, every node at
every status with its lifecycle and value signals, the edges, the decision
journal, the relation vocabulary and the verdicts on it, the timelines, the
documents and segments the nodes were extracted from, and the graph's own
settings.

**Embeddings are not in it.** A vector is the one part of a graph that is
derived rather than written, it is the part that does not compress, and it is
cheap to recompute locally. Import re-embeds every node with the provider it is
handed, which is what makes *export, change the model, import* the migration
path for a new embedding model. The manifest records what the graph was embedded
with, so a restore can say when the model changed.

**Byte-identical is the contract.** Two exports of the same graph produce the
same bytes, and exporting a graph, importing it, and exporting that produces the
same bytes again. That is what "lossless" means here, and it is the reason for
every serialization rule below: sorted keys, sorted records, one record per
line, datetimes rendered to the microsecond with an explicit UTC offset, and
floats through Python's own repr. Anything that varies between two runs of the
same export is a field this module is not reading faithfully.

`dev-docs/GRAPH_BUNDLES.md` carries the layout, the verbatim-write rule, and how
to run the round-trip against a real graph before a release.
"""

from __future__ import annotations

import gzip
import io
import json
import re
import tarfile
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, timedelta
from enum import Enum
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from pydantic import BaseModel, Field

from epimemer.core.types import (
    Agent,
    DecisionRecord,
    EmbeddingRecord,
    EpistemicNode,
    Fact,
    Inference,
    Metacontext,
    NodeEdge,
    NodeStatus,
    NodeType,
    RawDocument,
    RelationLabel,
    RelationVerdict,
    Segment,
    Timeline,
    Topic,
)
from epimemer.embeddings.protocol import EmbeddingProvider
from epimemer.pipelines.embedding_text import embedding_text
from epimemer.storage.protocol import (
    MergeOverrides,
    StorageBackend,
    WarningOverrides,
    validate_graph_name,
)

# The bundle layout's version. Bump it when a reader of the current code could
# not make sense of what a writer produces: a section removed, a section's
# records reshaped, a field whose meaning changed under the same name. Adding a
# field or a section is not a bump — an older bundle imports with model defaults
# and says so, which is the rule the storage rows already follow.
BUNDLE_FORMAT_VERSION = 1

# The file extension the CLI gives a compressed bundle, and what `read_bundle`
# recognises. A directory is the uncompressed form.
BUNDLE_SUFFIX = ".epimemer.tar.gz"

MANIFEST_FILENAME = "manifest.json"

_NODE_CLASSES: dict[NodeType, type] = {
    NodeType.TOPIC: Topic,
    NodeType.FACT: Fact,
    NodeType.INFERENCE: Inference,
}

_NODE_TYPES: dict[type, NodeType] = {cls: nt for nt, cls in _NODE_CLASSES.items()}


class BundleFormatError(ValueError):
    """A bundle this code cannot read, with the reason in the message."""


class GraphSettings(BaseModel):
    """A graph's own answers, as the bundle carries them.

    Every field here is per-graph state the protocol exposes a getter for.
    `None` on an override keeps its meaning across the round trip: *follow the
    process default*, and deliberately not the default's current value.
    """

    require_judge: bool | None = None
    reflect_threshold_override: int | None = None
    backup_threshold_override: int | None = None
    stores_since_reflect: int = 0
    stores_since_backup: int = 0
    merge_overrides: MergeOverrides = Field(default_factory=MergeOverrides)
    warning_overrides: WarningOverrides = Field(default_factory=WarningOverrides)


class BundleManifest(BaseModel):
    """What this bundle is, and what a reader should expect to find in it."""

    format_version: int
    epimemer_version: str
    exported_at: datetime
    graph: str
    # What the graph's vectors were computed with. Not used to re-embed — the
    # importing process supplies its own provider — but recorded so a restore
    # onto a different model can say that is what happened.
    embedding_provider: str
    embedding_model_id: str
    # Section name to row count. Import checks what it wrote against these, so a
    # bundle that lost a file fails loudly rather than restoring a smaller graph.
    counts: dict[str, int] = Field(default_factory=dict)


class Bundle(BaseModel):
    """One graph, in memory, in the order the files hold it.

    Every list is sorted by id, and the sort is part of the format rather than a
    convenience: two exports of one graph have to produce the same bytes, and a
    backend is free to return its rows in any order it likes.
    """

    manifest: BundleManifest
    metacontexts: list[Metacontext] = Field(default_factory=list)
    agents: list[Agent] = Field(default_factory=list)
    approved_agents: list[str] = Field(default_factory=list)
    nodes: list[EpistemicNode] = Field(default_factory=list)
    edges: list[NodeEdge] = Field(default_factory=list)
    decisions: list[DecisionRecord] = Field(default_factory=list)
    relation_labels: list[RelationLabel] = Field(default_factory=list)
    relation_verdicts: list[RelationVerdict] = Field(default_factory=list)
    timelines: list[Timeline] = Field(default_factory=list)
    documents: list[RawDocument] = Field(default_factory=list)
    segments: list[Segment] = Field(default_factory=list)
    settings: GraphSettings = Field(default_factory=GraphSettings)


# The sections, in the order a reader would want them, and the order the files
# are written in. Named once so the manifest, the writer, the reader and the
# count check cannot disagree about what a bundle contains.
SECTIONS: tuple[str, ...] = (
    "metacontexts",
    "agents",
    "approved_agents",
    "nodes",
    "edges",
    "decisions",
    "relation_labels",
    "relation_verdicts",
    "timelines",
    "documents",
    "segments",
    "settings",
)


# --- Serialization ---------------------------------------------------------


def _iso(at: datetime) -> str:
    """A datetime as the bundle writes every one: UTC, microseconds, offset.

    Pydantic's JSON mode drops the fractional part when it is exactly zero and
    renders UTC as `Z`, so the same instant can come back written two ways
    depending on the clock and the backend it passed through. Two spellings of
    one instant would break the byte-identical rule for no reason anybody could
    see, so this normalizes both halves.

    A naive datetime is read as UTC. Nothing in the system writes one, and
    guessing the local zone here would make a bundle mean different things on
    two machines.
    """
    aware = at.replace(tzinfo=UTC) if at.tzinfo is None else at
    return aware.astimezone(UTC).isoformat(timespec="microseconds")


def _duration(length: timedelta) -> str:
    from pydantic import TypeAdapter

    return TypeAdapter(timedelta).dump_python(length, mode="json")


def _json_default(value):
    """The types `model_dump(mode="python")` leaves for `json.dumps` to handle.

    Dumping in Python mode rather than JSON mode is what keeps the datetime
    rendering in one place: JSON mode would have already turned every timestamp
    into a string of Pydantic's choosing, and there would be nothing left here
    to normalize.
    """
    if isinstance(value, datetime):
        return _iso(value)
    if isinstance(value, timedelta):
        # ISO-8601, the same spelling Pydantic's JSON mode gives a timedelta, so
        # a duration reads the same in a bundle as it does anywhere else. It is
        # a length rather than an instant, so `_iso` has nothing to say about it.
        return _duration(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, frozenset | set):
        return sorted(value)
    raise TypeError(f"a bundle cannot carry {type(value).__name__}: {value!r}")


def _line(payload: dict) -> str:
    """One record as one line: sorted keys, no incidental whitespace.

    `sort_keys` and the tight separators are what make the bytes a function of
    the content alone. `ensure_ascii=False` keeps text readable in the
    uncompressed form; the file is UTF-8 and says so.
    """
    return json.dumps(
        payload,
        default=_json_default,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _record(model: BaseModel) -> dict:
    return model.model_dump(mode="python")


def _node_line(node: EpistemicNode) -> dict:
    """A node with its class named alongside it.

    A fact and an inference carry the same fields, so a line holding only the
    fields could not say which class wrote it, and a bundle that guessed would
    quietly turn one into the other. The type is recorded rather than inferred.
    """
    return {"node_type": _NODE_TYPES[type(node)].value, "node": _record(node)}


def _node_from_line(row: dict) -> EpistemicNode:
    return _NODE_CLASSES[NodeType(row["node_type"])].model_validate(row["node"])


def _section_lines(bundle: Bundle, section: str) -> list[str]:
    """One section as the lines its file holds."""
    if section == "nodes":
        return [_line(_node_line(node)) for node in bundle.nodes]
    if section == "approved_agents":
        return [_line({"agent_id": agent_id}) for agent_id in bundle.approved_agents]
    if section == "settings":
        return [_line(_record(bundle.settings))]
    return [_line(_record(model)) for model in getattr(bundle, section)]


def _section_bytes(bundle: Bundle, section: str) -> bytes:
    """A section file's contents. Empty sections write an empty file.

    A file per section whatever the count, so a bundle's shape does not depend
    on what the graph happened to contain and a missing file always means a
    damaged bundle.
    """
    lines = _section_lines(bundle, section)
    return "".join(f"{line}\n" for line in lines).encode("utf-8")


def _manifest_bytes(manifest: BundleManifest) -> bytes:
    """The manifest, on the section files' rules plus a trailing newline."""
    return (_line(_record(manifest)) + "\n").encode("utf-8")


def section_counts(bundle: Bundle) -> dict[str, int]:
    """How many records each section holds, for the manifest and the check.

    `settings` counts 1: it is one record in a file of its own rather than a
    section that can be empty, and a zero there would read as a graph with no
    settings rather than a bundle missing them.
    """
    return {section: len(_section_lines(bundle, section)) for section in SECTIONS}


# --- Export ----------------------------------------------------------------


def _epimemer_version() -> str:
    """The installed version, or a plain marker when there is no distribution.

    A checkout run straight from the source tree has no installed metadata, and
    refusing to export from one would be an odd place to draw the line.
    """
    try:
        return version("epimemer")
    except PackageNotFoundError:  # pragma: no cover - only outside an install
        return "unknown"


def _by_id(models: Iterable[BaseModel]) -> list:
    return sorted(models, key=lambda model: model.id)


async def export_graph(
    storage: StorageBackend,
    *,
    embedding_provider: str,
    embedding_model_id: str,
    exported_at: datetime | None = None,
) -> Bundle:
    """Read the active graph whole, through the protocol and nothing else.

    `embedding_provider` and `embedding_model_id` are values rather than a
    config object: what the graph was embedded with is a fact about the server
    that wrote it, and the module that reads a graph has no business reaching
    for process configuration.

    `exported_at` is an argument for the tests' sake — it is the one field of a
    bundle that cannot be a function of the graph, and so the one field the
    round-trip comparison has to leave out.
    """
    metacontexts = [
        mc for status in NodeStatus for mc in await storage.query_metacontexts(status=status)
    ]
    nodes = [node for status in NodeStatus for node in await storage.query_nodes(status=status)]

    bundle = Bundle(
        manifest=BundleManifest(
            format_version=BUNDLE_FORMAT_VERSION,
            epimemer_version=_epimemer_version(),
            exported_at=exported_at or datetime.now(UTC),
            graph=storage.current_database,
            embedding_provider=embedding_provider,
            embedding_model_id=embedding_model_id,
        ),
        metacontexts=_by_id(metacontexts),
        agents=_by_id(await storage.list_agents()),
        # Sorted like everything else. The stored order is the order a user
        # approved ids in, which is not information anything reads back, and
        # leaving it unsorted would make the bytes depend on it.
        approved_agents=sorted(await storage.get_approved_agent_ids()),
        nodes=_by_id(nodes),
        edges=_by_id(await storage.query_edges()),
        decisions=_by_id(await storage.query_decisions()),
        relation_labels=_by_id(await storage.query_relation_labels()),
        relation_verdicts=_by_id(await storage.query_relation_verdicts()),
        timelines=_by_id(await storage.query_timelines()),
        documents=_by_id(await storage.query_documents()),
        segments=_by_id(await storage.query_segments()),
        settings=GraphSettings(
            require_judge=await storage.get_require_judge(),
            reflect_threshold_override=await storage.get_reflect_threshold_override(),
            backup_threshold_override=await storage.get_backup_threshold_override(),
            stores_since_reflect=await storage.get_reflect_counter(),
            stores_since_backup=await storage.get_backup_counter(),
            merge_overrides=await storage.get_merge_overrides(),
            warning_overrides=await storage.get_warning_overrides(),
        ),
    )
    return bundle.model_copy(
        update={"manifest": bundle.manifest.model_copy(update={"counts": section_counts(bundle)})}
    )


# --- Writing and reading ---------------------------------------------------


def bundle_bytes(bundle: Bundle) -> dict[str, bytes]:
    """The whole bundle as `{filename: contents}`, which is what both forms write.

    One function so the compressed and uncompressed forms cannot drift: a
    `--plain` directory and the archive beside it hold the same files with the
    same bytes.
    """
    files = {MANIFEST_FILENAME: _manifest_bytes(bundle.manifest)}
    for section in SECTIONS:
        files[f"{section}.jsonl"] = _section_bytes(bundle, section)
    return files


def _tar_bytes(files: dict[str, bytes]) -> bytes:
    """The files as a deterministic `tar.gz`.

    Every timestamp, owner and mode is fixed, and gzip is given `mtime=0`.
    Nothing depends on the archive itself being reproducible — the round-trip
    test compares the files inside it — but an archive whose bytes change every
    second is one nobody can check by hand.
    """
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as archive:
        for name in sorted(files):
            info = tarfile.TarInfo(name=name)
            info.size = len(files[name])
            info.mtime = 0
            info.mode = 0o644
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            archive.addfile(info, io.BytesIO(files[name]))
    compressed = io.BytesIO()
    with gzip.GzipFile(fileobj=compressed, mode="wb", mtime=0) as handle:
        handle.write(raw.getvalue())
    return compressed.getvalue()


def default_bundle_name(graph: str, at: datetime, *, plain: bool = False) -> str:
    """The filename a bundle takes when the user named a directory and not a file.

    `<graph>-<date>.epimemer.tar.gz`, and the date rather than the instant: a
    user looking at a folder of these wants to know which day's graph it is, and
    two backups on one day are one intent, not two artefacts to keep apart.
    """
    stamp = at.astimezone(UTC).date().isoformat()
    suffix = BUNDLE_SUFFIX.removesuffix(".tar.gz") if plain else BUNDLE_SUFFIX
    return f"{graph}-{stamp}{suffix}"


def _dated_bundle(graph: str) -> re.Pattern[str]:
    """Filenames `default_bundle_name` writes for `graph`, and nothing else.

    Anchored on the date, because a bare prefix match is the bug here: a graph
    named `notes` would otherwise claim every bundle of `notes-archive`, and
    retention would delete another graph's backups. The graph name is escaped
    because nothing stops one containing a regex metacharacter.
    """
    return re.compile(rf"^{re.escape(graph)}-\d{{4}}-\d{{2}}-\d{{2}}{re.escape(BUNDLE_SUFFIX)}$")


def stale_bundles(names: Sequence[str], graph: str, keep: int) -> list[str]:
    """Which of `names` retention should remove, newest `keep` spared.

    Pure, and takes names rather than a filesystem so the rule can be read and
    tested without a destination. Entries may be bare names or full paths; the
    last segment decides, and whatever was passed in comes back, so the caller
    deletes by the same string it listed.

    **Ordered by the date in the name, never by modification time.** Object
    stores stamp mtime at upload and a copied folder loses it altogether, so
    mtime order is the order the bundles arrived at this destination rather
    than the order the graph was written in. The date is in the filename for
    exactly this reason, and it is ISO-8601, so sorting it as text is sorting
    it chronologically.

    A `--plain` bundle is a directory, matches nothing here, and is left alone:
    retention removes what `backup_graph` writes, and that is always a tarball.
    """
    if keep < 1:
        raise ValueError(f"keep must be at least 1, got {keep}")

    pattern = _dated_bundle(graph)
    matched = [name for name in names if pattern.match(name.rstrip("/").rsplit("/", 1)[-1])]
    oldest_first = sorted(matched, key=lambda name: name.rstrip("/").rsplit("/", 1)[-1])
    return oldest_first[: max(0, len(oldest_first) - keep)]


class PruneReport(BaseModel):
    """What retention did at a destination, for the backup result to report."""

    kept: int
    removed: list[str] = Field(default_factory=list)
    # Name to reason, for bundles retention chose but could not delete. They
    # are still there, so they are counted in `kept`.
    failed: dict[str, str] = Field(default_factory=dict)


def prune_bundles(destination: str, graph: str, keep: int) -> PruneReport:
    """Delete every bundle of `graph` at `destination` but the newest `keep`.

    Call it only once a write has returned. A prune that ran first, or ran
    alongside, could delete the last good bundle on the way to a backup that
    then failed.

    **A delete that fails does not fail the backup.** The graph is already
    written out by the time this runs, and a bundle nobody could remove is a
    tidiness problem, not a data problem, so the names come back in `failed`
    for the user to deal with.
    """
    import fsspec

    fs, root = fsspec.core.url_to_fs(destination)
    try:
        present = fs.ls(root, detail=False)
    except FileNotFoundError:
        # Nothing written here yet, which a first backup reaches by racing its
        # own destination into existence.
        return PruneReport(kept=0)

    pattern = _dated_bundle(graph)
    total = sum(1 for name in present if pattern.match(str(name).rstrip("/").rsplit("/", 1)[-1]))
    removed: list[str] = []
    failed: dict[str, str] = {}
    for name in stale_bundles([str(name) for name in present], graph, keep):
        try:
            fs.rm(name)
        except Exception as refused:
            failed[name.rsplit("/", 1)[-1]] = str(refused)
        else:
            removed.append(name.rsplit("/", 1)[-1])

    return PruneReport(kept=total - len(removed), removed=removed, failed=failed)


# Which extra installs the filesystem behind a URL scheme. `fsspec` itself is a
# core dependency and is tiny; the cloud drivers are not, and neither is wanted
# by a user whose backups go to a folder.
DESTINATION_EXTRAS: dict[str, str] = {
    "gs": "gcs",
    "gcs": "gcs",
    "s3": "s3",
    "s3a": "s3",
}


def unreachable_destination(destination: str) -> str | None:
    """Why a bundle cannot be written here, or None if it can.

    Prose rather than a flag, and returned rather than raised, on
    `cli.unreachable_store`'s reasoning: the two ways this fails want different
    advice and both of them are the user's to act on.

    **Credentials are not checked and never come from Epimemer.** Each provider
    reads its own standard chain, so a URL this accepts can still be refused by
    the service, and that refusal is the service's to explain.
    """
    import fsspec

    protocol = fsspec.utils.get_protocol(destination)
    if protocol == "file":
        return None
    try:
        fsspec.get_filesystem_class(protocol)
    except ImportError:
        extra = DESTINATION_EXTRAS.get(protocol)
        if extra is None:
            return (
                f"Writing to {protocol}:// needs an fsspec filesystem that is "
                f"not installed. Install the package that provides it, or give "
                f"a local path instead."
            )
        return (
            f"Writing to {protocol}:// needs an optional extra that is not "
            f"installed. Install it with\n"
            f"    uv pip install 'epimemer[{extra}]'\n"
            f"Credentials come from the provider's own standard chain; Epimemer "
            f"reads none."
        )
    except ValueError:
        return (
            f"{destination!r} names the scheme {protocol}://, which fsspec does "
            f"not know. Give a local path, a gs:// URL or an s3:// URL."
        )
    return None


def write_bundle(bundle: Bundle, path: str | Path, *, plain: bool = False) -> str:
    """Write `bundle` to `path`, compressed by default.

    `plain` writes a directory of files instead, for reading or diffing. The
    path is taken literally either way: naming the default filename is the CLI's
    job, because only a command knows whether the user pointed at a directory.

    Written through `fsspec.open`, so a local path, a `gs://` URL and an `s3://`
    URL are one code path and only the cloud ones need an extra installed. Call
    `unreachable_destination` first if you want a refusal that names the extra
    rather than an `ImportError` from inside the write.

    Returns the path written, as a string, because a destination is a URL as
    often as it is a filesystem path.
    """
    import fsspec

    target = str(path)
    files = bundle_bytes(bundle)
    if plain:
        base = target.rstrip("/")
        for name, contents in sorted(files.items()):
            with fsspec.open(f"{base}/{name}", "wb", auto_mkdir=True) as handle:
                handle.write(contents)
        return base
    with fsspec.open(target, "wb", auto_mkdir=True) as handle:
        handle.write(_tar_bytes(files))
    return target


def _read_files(path: Path) -> dict[str, bytes]:
    """The bundle's files, from a directory or an archive.

    Which one is decided by the path: a directory is the uncompressed form and
    anything else is read as a `tar.gz`. Archive members are taken by their base
    name and anything outside the top level is refused, so a bundle from
    somewhere else cannot write a path of its own choosing.
    """
    if path.is_dir():
        return {entry.name: entry.read_bytes() for entry in path.iterdir() if entry.is_file()}
    if not path.exists():
        raise BundleFormatError(f"No bundle at {path}.")
    files: dict[str, bytes] = {}
    try:
        archive = tarfile.open(path, mode="r:gz")
    except (tarfile.TarError, OSError, EOFError) as unreadable:
        # A truncated download and a file that was never a bundle fail the same
        # way here, and the caller's next move is the same either way: get the
        # file again. Saying which one it is would need a guess.
        raise BundleFormatError(
            f"{path} is not a readable Epimemer bundle: {unreadable}. A bundle is "
            f"a .tar.gz written by `epimemer graphs export`, or the directory "
            f"`--plain` writes."
        ) from unreadable
    with archive:
        for member in archive.getmembers():
            if not member.isfile():
                continue
            if "/" in member.name or member.name in ("", ".", ".."):
                raise BundleFormatError(
                    f"The bundle at {path} holds {member.name!r}, which is not a "
                    f"top-level file. Bundles are flat, and a nested path in one "
                    f"is a sign it was not written by Epimemer."
                )
            handle = archive.extractfile(member)
            if handle is not None:
                files[member.name] = handle.read()
    return files


def _rows(files: dict[str, bytes], section: str) -> list[dict]:
    name = f"{section}.jsonl"
    if name not in files:
        raise BundleFormatError(
            f"The bundle has no {name}. Every section is written even when it is "
            f"empty, so a missing file means the bundle is damaged."
        )
    text = files[name].decode("utf-8")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def read_bundle(path: str | Path) -> Bundle:
    """Read a bundle from a `tar.gz` or an uncompressed directory.

    Refuses a `format_version` this code does not know how to read, and only
    that one: a bundle from an older format is read with model defaults filling
    in whatever it does not carry, exactly as an old storage row is.
    """
    files = _read_files(Path(path))
    if MANIFEST_FILENAME not in files:
        raise BundleFormatError(
            f"The bundle at {path} has no {MANIFEST_FILENAME}, so nothing can say "
            f"what format it is in or what it should contain."
        )
    manifest = BundleManifest.model_validate_json(files[MANIFEST_FILENAME])
    if manifest.format_version > BUNDLE_FORMAT_VERSION:
        raise BundleFormatError(
            f"The bundle is in format version {manifest.format_version} and this "
            f"Epimemer reads up to {BUNDLE_FORMAT_VERSION}. Upgrade Epimemer and "
            f"import it again; nothing here will guess at what the newer format "
            f"means."
        )

    settings_rows = _rows(files, "settings")
    return Bundle(
        manifest=manifest,
        metacontexts=[Metacontext.model_validate(row) for row in _rows(files, "metacontexts")],
        agents=[Agent.model_validate(row) for row in _rows(files, "agents")],
        approved_agents=[row["agent_id"] for row in _rows(files, "approved_agents")],
        nodes=[_node_from_line(row) for row in _rows(files, "nodes")],
        edges=[NodeEdge.model_validate(row) for row in _rows(files, "edges")],
        decisions=[DecisionRecord.model_validate(row) for row in _rows(files, "decisions")],
        relation_labels=[
            RelationLabel.model_validate(row) for row in _rows(files, "relation_labels")
        ],
        relation_verdicts=[
            RelationVerdict.model_validate(row) for row in _rows(files, "relation_verdicts")
        ],
        timelines=[Timeline.model_validate(row) for row in _rows(files, "timelines")],
        documents=[RawDocument.model_validate(row) for row in _rows(files, "documents")],
        segments=[Segment.model_validate(row) for row in _rows(files, "segments")],
        settings=(
            GraphSettings.model_validate(settings_rows[0]) if settings_rows else GraphSettings()
        ),
    )


def older_format(manifest: BundleManifest) -> bool:
    """Whether this bundle predates the format the code writes.

    Import reports it rather than refusing: whatever the older format did not
    carry takes its model default, which is the same thing an old storage row
    does when a field is added.
    """
    return manifest.format_version < BUNDLE_FORMAT_VERSION


# --- Import ----------------------------------------------------------------


class ImportReport(BaseModel):
    """What an import did, for a caller to print or check."""

    graph: str
    counts: dict[str, int]
    nodes_embedded: int
    embedding_model_id: str
    # What the bundle says its graph was embedded with. Compared against the
    # model actually used, so a restore onto a different model says so instead
    # of quietly re-embedding.
    bundle_embedding_model_id: str
    format_version: int
    older_format: bool

    @property
    def reembedded_with_a_different_model(self) -> bool:
        return self.embedding_model_id != self.bundle_embedding_model_id


async def _embeddings_for(
    nodes: Sequence[EpistemicNode], embedding_provider: EmbeddingProvider
) -> list[EmbeddingRecord]:
    """A fresh vector per node, on the text that node embeds on.

    One `embed` call rather than one per node: the providers batch, and a
    restore of a real graph is thousands of nodes. The text comes from
    `embedding_text`, which is the single definition of what a node embeds on —
    computing it here a second way is how a restored graph would stop matching
    itself.
    """
    if not nodes:
        return []
    vectors = await embedding_provider.embed([embedding_text(node) for node in nodes])
    return [
        EmbeddingRecord(
            item_id=node.id,
            model_id=embedding_provider.model_id,
            vector=vector,
        )
        for node, vector in zip(nodes, vectors, strict=True)
    ]


async def _restore_settings(storage: StorageBackend, bundle: Bundle) -> None:
    """Put the graph's own answers back, through the setters that take them whole."""
    settings = bundle.settings
    await storage.set_require_judge(settings.require_judge)
    await storage.set_reflect_threshold_override(settings.reflect_threshold_override)
    await storage.set_backup_threshold_override(settings.backup_threshold_override)
    await storage.set_merge_overrides(settings.merge_overrides)
    await storage.set_warning_overrides(settings.warning_overrides)
    await storage.set_approved_agent_ids(list(bundle.approved_agents))
    await storage.set_reflect_counter(settings.stores_since_reflect)
    await storage.set_backup_counter(settings.stores_since_backup)


async def _written_counts(storage: StorageBackend, manifest: BundleManifest) -> dict[str, int]:
    """What the restored graph actually holds, counted by exporting it again.

    Re-exporting rather than a second set of reads written out here: the counts
    have to be in the manifest's terms, and a second definition of what a
    section is would be the thing that drifts. It also means the check exercises
    every read the export path uses, on a graph the import path just wrote.
    """
    written = await export_graph(
        storage,
        embedding_provider=manifest.embedding_provider,
        embedding_model_id=manifest.embedding_model_id,
        exported_at=manifest.exported_at,
    )
    return section_counts(written)


async def import_graph(
    bundle: Bundle,
    storage: StorageBackend,
    embedding_provider: EmbeddingProvider,
    *,
    graph: str,
) -> ImportReport:
    """Rebuild `bundle` as a new graph named `graph`.

    **A new graph only, and there is no override.** Replacing a graph is
    `delete_graph` and then import, which are two acts the user already has, and
    each of them is one they can be asked about. An import that could overwrite
    would be one keystroke from destroying the graph it was meant to protect.

    Every record goes in through `write_verbatim_tx`, so ids and timestamps are
    the bundle's rather than this moment's. Vectors are the exception and are
    recomputed, because the bundle does not carry them.

    A failure at any point drops the graph and re-raises with the reason, so a
    retry is never blocked by a half-written one. The active graph is put back
    where it was found, whether the import succeeded or not: a function that
    silently moved the caller's graph would be the wrong-graph incident with a
    new cause.
    """
    validate_graph_name(graph)
    if graph in await storage.list_databases():
        raise ValueError(
            f"Graph '{graph}' already exists. Import creates a new graph and "
            f"never writes into one that is there: delete it first if that is "
            f"what you meant, or import under another name."
        )

    original = storage.current_database
    await storage.switch_database(graph)
    try:
        embeddings = await _embeddings_for(bundle.nodes, embedding_provider)
        await storage.write_verbatim_tx(
            documents=bundle.documents,
            segments=bundle.segments,
            nodes=bundle.nodes,
            edges=bundle.edges,
            embeddings=embeddings,
            timelines=bundle.timelines,
            metacontexts=bundle.metacontexts,
            relation_labels=bundle.relation_labels,
            relation_verdicts=bundle.relation_verdicts,
            decisions=bundle.decisions,
            agents=bundle.agents,
        )
        await _restore_settings(storage, bundle)

        written = await _written_counts(storage, bundle.manifest)
        expected = bundle.manifest.counts or section_counts(bundle)
        wrong = {
            section: (expected[section], written[section])
            for section in expected
            if section in written and expected[section] != written[section]
        }
        if wrong:
            detail = ", ".join(
                f"{section}: manifest says {want}, graph holds {got}"
                for section, (want, got) in sorted(wrong.items())
            )
            raise ValueError(f"The restored graph does not match the manifest ({detail}).")
    except Exception:
        await storage.switch_database(original)
        await storage.delete_database(graph)
        raise
    await storage.switch_database(original)

    return ImportReport(
        graph=graph,
        counts=written,
        nodes_embedded=len(embeddings),
        embedding_model_id=embedding_provider.model_id,
        bundle_embedding_model_id=bundle.manifest.embedding_model_id,
        format_version=bundle.manifest.format_version,
        older_format=older_format(bundle.manifest),
    )


__all__ = [
    "BUNDLE_FORMAT_VERSION",
    "BUNDLE_SUFFIX",
    "DESTINATION_EXTRAS",
    "Bundle",
    "BundleFormatError",
    "BundleManifest",
    "GraphSettings",
    "ImportReport",
    "PruneReport",
    "bundle_bytes",
    "default_bundle_name",
    "export_graph",
    "import_graph",
    "prune_bundles",
    "read_bundle",
    "section_counts",
    "stale_bundles",
    "unreachable_destination",
    "write_bundle",
]
