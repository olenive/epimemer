"""The browser visualiser the hub serves, and how it is fetched.

The visualiser is a web bundle built in the `epimemer-bevy` repository and
published as a release asset: one gzipped tarball whose top level is the files
a browser loads. `epimemer viz fetch` downloads a release, checks it against a
sha256 known before the download starts, unpacks it, and swaps it in as
`~/.epimemer/viz/current`, where the hub looks for its page on every request.

Every function here works from its arguments: the home directory, the
environment and the opener are passed in, so a test runs a whole fetch against
a tarball in a temporary directory and never reaches the network.
"""

import hashlib
import http.client
import os
import re
import secrets
import shutil
import ssl
import stat
import tarfile
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import certifi
from pydantic import BaseModel, BeforeValidator, ConfigDict

from epimemer.mcp.staleness import UNKNOWN_VERSION, read_installed_version

BUNDLE_REPOSITORY = "olenive/epimemer-bevy"

# Points the hub at a bundle of the user's own, such as a local build. When set
# it is the only place looked, so a broken build is reported rather than
# replaced by another page.
BUNDLE_DIR_ENV = "EPIMEMER_VIZ_BUNDLE_DIR"

# Ceilings on what a fetch accepts, several times the size of the 0.2.0
# release, so a hostile or broken server cannot fill the disk.
MAX_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_UNPACKED_BYTES = 256 * 1024 * 1024
MAX_MEMBERS = 2000

# GitHub answers a release download with one redirect to its asset host.
MAX_REDIRECTS = 5
DOWNLOAD_TIMEOUT_SECONDS = 60.0

INDEX_FILE = "index.html"

# What a fetch names its temporary files and directories, and how old one must
# be before a later fetch deletes it. A fetch that is killed cannot clean up
# after itself; the age keeps the sweep away from a fetch still running.
TEMPORARY_PREFIXES = (".download-", ".unpack-", ".previous-", ".manifest-")
LEFTOVER_AFTER_SECONDS = 24 * 60 * 60

_CHUNK_BYTES = 1024 * 1024
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})

# ASCII digits only: `\d` also matches digits from other scripts, and the
# version goes into a URL.
_RELEASE_VERSION = re.compile(r"[0-9]+\.[0-9]+\.[0-9]+")
_SHA256_HEX = re.compile(r"[0-9a-f]{64}")


class BundleError(Exception):
    """Anything that stops a fetch, worded for the person who ran it."""


def _release_version(value: object) -> str:
    """`0.2.0`, from `0.2.0` or `v0.2.0`: a release tag carries one leading `v`."""
    if not isinstance(value, str):
        raise ValueError("a release version is text, such as 0.2.0")
    version = value.removeprefix("v")
    if _RELEASE_VERSION.fullmatch(version) is None:
        raise ValueError(f"{value!r} is not a release version: expected one such as 0.2.0")
    return version


def _sha256_digest(value: object) -> str:
    """A sha256 as 64 lowercase hex characters, whichever case it was given in."""
    digest = value.lower() if isinstance(value, str) else ""
    if _SHA256_HEX.fullmatch(digest) is None:
        raise ValueError(f"{value!r} is not a sha256: expected 64 hexadecimal characters")
    return digest


ReleaseVersion = Annotated[str, BeforeValidator(_release_version)]
Sha256 = Annotated[str, BeforeValidator(_sha256_digest)]


class BundleRelease(BaseModel):
    """One published visualiser, and the digest its tarball must have."""

    model_config = ConfigDict(frozen=True)

    version: ReleaseVersion
    sha256: Sha256


class InstalledBundle(BaseModel):
    """What `current` holds: the manifest written beside it by a fetch."""

    model_config = ConfigDict(frozen=True)

    version: ReleaseVersion
    sha256: Sha256
    fetched_at: datetime


# The release `epimemer viz fetch` downloads when none is named. To move it,
# take the version and the digest from the new release's `.sha256` asset, and
# try that visualiser against this hub first: the two share a contract.
PINNED_RELEASE = BundleRelease(
    version="0.3.1",
    sha256="a8a967232f1b158848b186361aa0de6bf10ce7e8a4f22d3200ae31598ac2ba05",
)


def requested_release(version: str, sha256: str) -> BundleRelease:
    """The release a user named on the command line, or why it is not one."""
    try:
        return BundleRelease(version=_release_version(version), sha256=_sha256_digest(sha256))
    except ValueError as refused:
        raise BundleError(str(refused)) from None


# ----------------------------------------------------------------------------
# Where things live
# ----------------------------------------------------------------------------


def visualiser_dir(home: Path) -> Path:
    """Everything a fetch writes, the temporary files included."""
    return home / ".epimemer" / "viz"


def bundle_dir(home: Path) -> Path:
    """The fetched bundle the hub serves."""
    return visualiser_dir(home) / "current"


def manifest_path(home: Path) -> Path:
    """Beside the bundle rather than inside it, so the hub never serves it."""
    return visualiser_dir(home) / "current.json"


def release_url(release: BundleRelease) -> str:
    tag = f"v{release.version}"
    return (
        f"https://github.com/{BUNDLE_REPOSITORY}/releases/download/"
        f"{tag}/epimemer-bevy-web-{tag}.tar.gz"
    )


def bundle_dir_override(environ: Mapping[str, str], home: Path) -> Path | None:
    """The directory `EPIMEMER_VIZ_BUNDLE_DIR` names, or None when it is unset or empty.

    A leading `~` is read against `home`, the same home every other path here
    is derived from.
    """
    value = environ.get(BUNDLE_DIR_ENV, "")
    if not value.strip():
        return None
    if value == "~" or value.startswith("~/"):
        return home / value[2:]
    return Path(value).expanduser()


def bundle_candidates(environ: Mapping[str, str], home: Path) -> list[Path]:
    """The directories the page may come from, in the order they are tried.

    The override alone when it is set: someone pointing the hub at their own
    build wants to be told it is broken rather than be shown something else.
    Otherwise the fetched bundle.
    """
    override = bundle_dir_override(environ, home)
    if override is not None:
        return [override]
    return [bundle_dir(home)]


def _is_regular_file(path: Path) -> bool:
    try:
        return stat.S_ISREG(os.lstat(path).st_mode)
    except OSError, ValueError:
        return False


def served_bundle(candidates: Sequence[Path]) -> Path | None:
    """The first candidate that holds a regular file `index.html`, or None."""
    return next((c for c in candidates if _is_regular_file(c / INDEX_FILE)), None)


def installed_bundle(home: Path) -> InstalledBundle | None:
    """What the manifest says is fetched; None when it is missing or unreadable."""
    try:
        return InstalledBundle.model_validate_json(manifest_path(home).read_bytes())
    except OSError, ValueError:
        return None


def already_installed(release: BundleRelease, home: Path) -> bool:
    """Whether `release` is fetched: the manifest names it and the page is there."""
    installed = installed_bundle(home)
    return (
        installed is not None
        and (installed.version, installed.sha256) == (release.version, release.sha256)
        and _is_regular_file(bundle_dir(home) / INDEX_FILE)
    )


# ----------------------------------------------------------------------------
# Downloading
# ----------------------------------------------------------------------------

# One GET of a URL, redirects not followed. What it returns is used as a
# context manager whose value has `status`, `headers` (with `.get`) and
# `read(size)`; `http.client.HTTPResponse` and `urllib.error.HTTPError` both do.
Opener = Callable[[str], AbstractContextManager[Any]]


def user_agent() -> str:
    return f"epimemer/{read_installed_version() or UNKNOWN_VERSION}"


def _is_https(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname)


def open_https(url: str) -> Any:
    """One GET of `url` over https, verified against certifi's roots.

    A redirect comes back as the answer rather than being followed, so
    `download` checks every address before anything is asked of it. The
    opener has a handler for https and none for any other scheme, so an
    address that got past that check still could not be opened. An error
    status comes back as the answer too, for `download` to word.
    """
    if not _is_https(url):
        raise BundleError(f"Refusing {url}: the visualiser is downloaded over https only.")
    context = ssl.create_default_context(cafile=certifi.where())
    director = urllib.request.OpenerDirector()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPDefaultErrorHandler(),
        urllib.request.HTTPErrorProcessor(),
    ):
        director.add_handler(handler)
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent(), "Accept": "application/octet-stream"}
    )
    try:
        return director.open(request, timeout=DOWNLOAD_TIMEOUT_SECONDS)
    except urllib.error.HTTPError as answered:
        return answered


def _mib(size: int) -> str:
    return f"{size / (1024 * 1024):g} MiB"


def _declared_size(headers: Any) -> int | None:
    """The `Content-Length` a server sent, or None for none or nonsense.

    Nonsense is not refused here: the stream is counted whatever the header
    says, so the cap holds either way.
    """
    try:
        return int(headers.get("Content-Length"))
    except TypeError, ValueError:
        return None


def _stream(reply: Any, destination: Path, *, url: str, max_bytes: int) -> str:
    digest = hashlib.sha256()
    received = 0
    with destination.open("wb") as out:
        while chunk := reply.read(_CHUNK_BYTES):
            received += len(chunk)
            if received > max_bytes:
                raise BundleError(
                    f"{url} sent more than {_mib(max_bytes)}, the most a visualiser "
                    f"download may be; refusing it."
                )
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()


def download(
    url: str, destination: Path, *, opener: Opener, max_bytes: int = MAX_DOWNLOAD_BYTES
) -> str:
    """Stream `url` into `destination` and return the sha256 of what arrived.

    Redirects are followed here, each to an https address only, because
    GitHub sends a release download to another host. A body over `max_bytes`
    is refused, by its `Content-Length` before reading and by counting while
    reading, whatever the header said.
    """
    if not _is_https(url):
        raise BundleError(f"Refusing {url}: the visualiser is downloaded over https only.")
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        try:
            with opener(current) as reply:
                if reply.status in _REDIRECT_STATUSES:
                    location = reply.headers.get("Location")
                    if not location:
                        raise BundleError(f"{current} redirected without saying where to.")
                    target = urllib.parse.urljoin(current, location)
                    if not _is_https(target):
                        raise BundleError(
                            f"{current} redirected to {target}, which is not https; "
                            f"refusing to follow it."
                        )
                    current = target
                    continue
                if reply.status != 200:
                    raise BundleError(
                        f"{current} answered HTTP {reply.status}. Check that the "
                        f"release exists at https://github.com/{BUNDLE_REPOSITORY}/releases."
                    )
                declared = _declared_size(reply.headers)
                if declared is not None and declared > max_bytes:
                    raise BundleError(
                        f"{url} is {_mib(declared)}, over the {_mib(max_bytes)} a "
                        f"visualiser download may be; refusing it."
                    )
                return _stream(reply, destination, url=url, max_bytes=max_bytes)
        except (OSError, http.client.HTTPException) as failed:
            raise BundleError(f"Could not download {url}: {failed}") from failed
    raise BundleError(f"{url} redirected more than {MAX_REDIRECTS} times; refusing to follow.")


def verify_checksum(actual: str, release: BundleRelease, url: str) -> None:
    """Refuse a download whose digest is not the one it was fetched against."""
    if actual != release.sha256:
        raise BundleError(
            f"The download from {url} does not match its checksum, so nothing was installed.\n"
            f"  expected sha256 {release.sha256}\n"
            f"  actual sha256   {actual}"
        )


# ----------------------------------------------------------------------------
# Unpacking and installing
# ----------------------------------------------------------------------------


def _refused_kind(member: tarfile.TarInfo) -> str | None:
    if member.issym():
        return "a symbolic link"
    if member.islnk():
        return "a hard link"
    if member.ischr() or member.isblk():
        return "a device"
    if member.isfifo():
        return "a FIFO"
    if not (member.isreg() or member.isdir()):
        return "an entry of an unsupported type"
    return None


def _check_member(member: tarfile.TarInfo) -> None:
    """Refuse anything but a file or directory that stays inside the bundle."""
    kind = _refused_kind(member)
    if kind is not None:
        raise BundleError(
            f"The visualiser archive holds {kind}, {member.name!r}; only files and "
            f"directories are accepted, so nothing was installed."
        )
    if member.name.startswith(("/", "\\")):
        raise BundleError(
            f"The visualiser archive holds an absolute name, {member.name!r}; "
            f"nothing was installed."
        )
    if ".." in member.name.replace("\\", "/").split("/"):
        raise BundleError(
            f"The visualiser archive holds a name that climbs out of it, "
            f"{member.name!r}; nothing was installed."
        )


def _checked_members(
    archive: tarfile.TarFile, *, max_members: int, max_bytes: int
) -> list[tarfile.TarInfo]:
    """Every member, each checked as it is read so a hostile archive stops early.

    Sizes are summed from the headers before the data behind them is read, so
    the caps also bound how much is decompressed to get this far.
    """
    members: list[tarfile.TarInfo] = []
    total = 0
    for member in archive:
        if len(members) >= max_members:
            raise BundleError(
                f"The visualiser archive holds more than {max_members} entries; "
                f"nothing was installed."
            )
        _check_member(member)
        total += member.size if member.isreg() else 0
        if total > max_bytes:
            raise BundleError(
                f"The visualiser archive unpacks to more than {_mib(max_bytes)}; "
                f"nothing was installed."
            )
        members.append(member)
    return members


def unpack(
    archive: Path,
    destination: Path,
    *,
    max_members: int = MAX_MEMBERS,
    max_bytes: int = MAX_UNPACKED_BYTES,
) -> None:
    """Unpack a gzipped tarball into `destination`, which must be empty.

    Only files and directories are accepted, by name and type before anything
    is written, and the `data` filter applies the same rules again at
    extraction. The result must have a regular file `index.html` at its top.
    """
    try:
        with tarfile.open(archive, "r:gz") as tar:
            members = _checked_members(tar, max_members=max_members, max_bytes=max_bytes)
            tar.extractall(destination, members=members, filter="data")
    except (tarfile.TarError, EOFError, OSError) as broken:
        raise BundleError(f"The visualiser archive could not be unpacked: {broken}") from broken
    if not _is_regular_file(destination / INDEX_FILE):
        raise BundleError(
            f"The visualiser archive has no {INDEX_FILE} at its top level, so it is "
            f"not a web bundle; nothing was installed."
        )


def _remove(path: Path) -> None:
    """Delete a file, a link or a directory tree, as far as it can be deleted."""
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def remove_leftovers(home: Path, now: datetime) -> None:
    """Delete what an interrupted fetch left in the visualiser directory.

    Only entries a fetch names for itself, and only once they have gone a day
    untouched, so the bundle, the manifest and a fetch running beside this one
    are all left alone.
    """
    cutoff = now.timestamp() - LEFTOVER_AFTER_SECONDS
    try:
        entries = list(visualiser_dir(home).iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.startswith(TEMPORARY_PREFIXES):
            continue
        try:
            untouched = os.lstat(entry).st_mtime < cutoff
        except OSError:
            continue
        if untouched:
            _remove(entry)


def _touch(path: Path) -> None:
    """Mark `path` as used now, so `remove_leftovers` counts its age from here."""
    try:
        if os.utime in os.supports_follow_symlinks:
            os.utime(path, follow_symlinks=False)
        else:
            os.utime(path)
    except OSError:
        pass


def _staged_manifest(directory: Path, installed: InstalledBundle) -> Path:
    """The manifest written to a temporary file, ready to be renamed into place."""
    fd, name = tempfile.mkstemp(dir=directory, prefix=".manifest-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(installed.model_dump_json(indent=2))
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def install(unpacked: Path, installed: InstalledBundle, home: Path) -> None:
    """Swap `unpacked` in as `current` and record it in the manifest.

    The old `current` is moved aside, the new one renamed into its place, and
    the old one deleted, so nothing of it survives in the new bundle. The
    manifest is written to a temporary file first and renamed over the old one
    last. A failure at any step puts the old bundle and the old manifest back,
    and leaves `unpacked` where it was for the caller to remove.
    """
    viz = visualiser_dir(home)
    current = bundle_dir(home)
    staged = _staged_manifest(viz, installed)
    aside: Path | None = None
    swapped = False
    try:
        if os.path.lexists(current):
            moved = viz / f".previous-{secrets.token_hex(8)}"
            os.rename(current, moved)
            aside = moved
            # A rename keeps the directory's old modification time, which
            # would make the bundle just set aside look like a leftover.
            _touch(moved)
        os.rename(unpacked, current)
        swapped = True
        os.replace(staged, manifest_path(home))
    except BaseException:
        if swapped:
            os.rename(current, unpacked)
        if aside is not None:
            os.rename(aside, current)
        raise
    finally:
        staged.unlink(missing_ok=True)
    if aside is not None:
        _remove(aside)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def fetch_bundle(
    release: BundleRelease,
    home: Path,
    *,
    opener: Opener = open_https,
    now: Callable[[], datetime] = _utc_now,
) -> InstalledBundle:
    """Download, verify, unpack and install `release`, and return its manifest.

    The checksum is compared before anything is unpacked. Every temporary file
    and directory is made inside the visualiser directory, so the final rename
    stays on one filesystem, and is removed whether the fetch succeeds or
    fails. On a failure the bundle and manifest already installed are left as
    they were.
    """
    viz = visualiser_dir(home)
    viz.mkdir(mode=0o700, parents=True, exist_ok=True)
    remove_leftovers(home, now())
    url = release_url(release)
    fd, name = tempfile.mkstemp(dir=viz, prefix=".download-", suffix=".tar.gz")
    os.close(fd)
    archive = Path(name)
    unpacked: Path | None = None
    try:
        actual = download(url, archive, opener=opener)
        verify_checksum(actual, release, url)
        unpacked = Path(tempfile.mkdtemp(dir=viz, prefix=".unpack-"))
        unpack(archive, unpacked)
        installed = InstalledBundle(
            version=release.version, sha256=release.sha256, fetched_at=now()
        )
        install(unpacked, installed, home)
        return installed
    finally:
        archive.unlink(missing_ok=True)
        if unpacked is not None and os.path.lexists(unpacked):
            _remove(unpacked)
