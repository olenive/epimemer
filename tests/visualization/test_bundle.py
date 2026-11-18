"""Fetching the browser visualiser: where it lives, how it is checked, how it is swapped in.

Every test runs against a tarball built in `tmp_path` and handed out by a fake
opener, with `tmp_path` as the home directory, so nothing here reaches the
network or the real `~/.epimemer`.
"""

import hashlib
import io
import os
import stat
import tarfile
import urllib.error
from collections.abc import Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from epimemer.visualization.bundle import (
    BUNDLE_DIR_ENV,
    MAX_REDIRECTS,
    PINNED_RELEASE,
    BundleError,
    BundleRelease,
    InstalledBundle,
    already_installed,
    bundle_candidates,
    bundle_dir,
    bundle_dir_override,
    download,
    fetch_bundle,
    installed_bundle,
    manifest_path,
    open_https,
    release_url,
    remove_leftovers,
    requested_release,
    served_bundle,
    unpack,
    user_agent,
    visualiser_dir,
)

FETCHED_AT = datetime(2026, 11, 17, 12, 0, tzinfo=UTC)

PAGE = {
    "index.html": b"<!doctype html><title>visualiser</title>",
    "epimemer-bevy-abc123.js": b"export default function init() {}",
    "epimemer-bevy-abc123_bg.wasm": b"\x00asm\x01\x00\x00\x00",
    "assets/fonts/FiraSans-Bold.ttf": b"font bytes",
}


def _tarball(files: Mapping[str, bytes], *extra: tarfile.TarInfo) -> bytes:
    """A gzipped tarball of `files` (name to content), then any special members."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        for info in extra:
            tar.addfile(info)
    return buffer.getvalue()


def _member(name: str, kind: bytes, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    return info


def _opener(answers: Mapping[str, tuple[int, Mapping[str, str], bytes]], asked: list[str]):
    """An opener that answers each URL from `answers` and records what was asked.

    A URL it has no answer for raises KeyError, which `download` does not
    catch, so a test that reaches somewhere unexpected fails loudly.
    """

    @contextmanager
    def opener(url: str):
        asked.append(url)
        status, headers, body = answers[url]
        yield SimpleNamespace(status=status, headers=dict(headers), read=io.BytesIO(body).read)

    return opener


def _release_of(data: bytes, version: str = "9.9.9") -> BundleRelease:
    return BundleRelease(version=version, sha256=hashlib.sha256(data).hexdigest())


def _serving(data: bytes, release: BundleRelease, asked: list[str]):
    return _opener({release_url(release): (200, {}, data)}, asked)


def _fetch(home: Path, files: Mapping[str, bytes], version: str = "9.9.9") -> InstalledBundle:
    data = _tarball(files)
    release = _release_of(data, version)
    return fetch_bundle(release, home, opener=_serving(data, release, []), now=lambda: FETCHED_AT)


def _leftovers(home: Path) -> list[str]:
    """Anything in the visualiser directory other than the bundle and its manifest."""
    return sorted(
        entry.name
        for entry in visualiser_dir(home).iterdir()
        if entry.name not in ("current", "current.json")
    )


def _tree(directory: Path) -> dict[str, bytes]:
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in sorted(directory.rglob("*"))
        if path.is_file()
    }


# --- where the page comes from ---


def test_with_no_override_the_fetched_bundle_is_the_only_candidate(tmp_path):
    """The package carries no page of its own, so without an override the page
    comes from what `epimemer viz fetch` installed or from nowhere."""
    assert bundle_candidates({}, tmp_path) == [tmp_path / ".epimemer" / "viz" / "current"]


def test_the_override_is_the_only_candidate(tmp_path):
    """Someone pointing at their own build is told when it is broken, rather
    than shown the fetched bundle instead."""
    own = tmp_path / "my-build"

    assert bundle_candidates({BUNDLE_DIR_ENV: str(own)}, tmp_path) == [own]


def test_an_empty_override_is_no_override(tmp_path):
    assert bundle_candidates({BUNDLE_DIR_ENV: ""}, tmp_path) == [bundle_dir(tmp_path)]
    assert bundle_dir_override({BUNDLE_DIR_ENV: "  "}, tmp_path) is None


def test_a_tilde_in_the_override_is_read_against_the_home_given(tmp_path):
    assert bundle_dir_override({BUNDLE_DIR_ENV: "~/builds/web"}, tmp_path) == (
        tmp_path / "builds" / "web"
    )
    assert bundle_dir_override({BUNDLE_DIR_ENV: "~"}, tmp_path) == tmp_path


def test_the_first_candidate_holding_an_index_is_served(tmp_path):
    empty, directory_index, second, third = (tmp_path / name for name in "abcd")
    empty.mkdir()
    (directory_index / "index.html").mkdir(parents=True)
    for page in (second, third):
        page.mkdir()
        (page / "index.html").write_text("page")

    assert served_bundle([tmp_path / "missing", empty, directory_index, second, third]) == second
    assert served_bundle([empty, directory_index]) is None
    assert served_bundle([]) is None


def test_the_manifest_sits_beside_the_bundle_never_inside_it(tmp_path):
    """Inside it, the hub would serve it as one of the page's files."""
    assert manifest_path(tmp_path).parent == bundle_dir(tmp_path).parent
    assert bundle_dir(tmp_path) not in manifest_path(tmp_path).parents


# --- releases ---


def test_the_pinned_release_is_well_formed():
    assert PINNED_RELEASE.version == "0.3.1"
    assert PINNED_RELEASE.sha256 == (
        "a8a967232f1b158848b186361aa0de6bf10ce7e8a4f22d3200ae31598ac2ba05"
    )
    assert BundleRelease.model_validate(PINNED_RELEASE.model_dump()) == PINNED_RELEASE
    assert release_url(PINNED_RELEASE) == (
        "https://github.com/olenive/epimemer-bevy/releases/download/"
        "v0.3.1/epimemer-bevy-web-v0.3.1.tar.gz"
    )


def test_a_version_may_carry_one_leading_v_and_a_digest_any_case():
    release = BundleRelease(version="v1.2.3", sha256="AB" * 32)

    assert release.version == "1.2.3"
    assert release.sha256 == "ab" * 32


@pytest.mark.parametrize(
    "version",
    ["vv1.2.3", "1.2", "1.2.3.4", "1.2.3-rc1", "../1.2.3", "1.2.3/x", "١.٢.٣"],
)
def test_a_version_that_is_not_three_ascii_numbers_is_refused(version):
    """The version goes into a URL, so digits from other scripts are refused too."""
    with pytest.raises(ValidationError):
        BundleRelease(version=version, sha256="a" * 64)


@pytest.mark.parametrize("digest", ["a" * 63, "a" * 65, "g" * 64, ""])
def test_a_digest_that_is_not_64_hex_characters_is_refused(digest):
    with pytest.raises(ValidationError):
        BundleRelease(version="1.2.3", sha256=digest)


def test_a_release_named_by_a_user_is_refused_in_words():
    with pytest.raises(BundleError, match="not a release version"):
        requested_release("latest", "a" * 64)
    with pytest.raises(BundleError, match="not a sha256"):
        requested_release("1.2.3", "abc")


# --- downloading ---


def test_a_download_returns_the_digest_of_what_arrived(tmp_path):
    asked: list[str] = []
    url = "https://example.test/bundle.tar.gz"
    opener = _opener({url: (200, {"Content-Length": "5"}, b"bytes")}, asked)

    digest = download(url, tmp_path / "out", opener=opener)

    assert digest == hashlib.sha256(b"bytes").hexdigest()
    assert (tmp_path / "out").read_bytes() == b"bytes"


def test_a_url_that_is_not_https_is_refused_before_anything_is_asked(tmp_path):
    asked: list[str] = []

    with pytest.raises(BundleError, match="https only"):
        download("http://example.test/b.tar.gz", tmp_path / "out", opener=_opener({}, asked))

    assert asked == []


def test_a_redirect_to_https_is_followed(tmp_path):
    """GitHub sends a release download to another host, so redirects are followed."""
    asked: list[str] = []
    start = "https://github.test/download/b.tar.gz"
    opener = _opener(
        {
            start: (302, {"Location": "https://assets.test/signed?x=1"}, b""),
            "https://assets.test/signed?x=1": (200, {}, b"payload"),
        },
        asked,
    )

    digest = download(start, tmp_path / "out", opener=opener)

    assert digest == hashlib.sha256(b"payload").hexdigest()
    assert asked == [start, "https://assets.test/signed?x=1"]


def test_a_redirect_to_plain_http_is_refused_without_following_it(tmp_path):
    asked: list[str] = []
    start = "https://github.test/download/b.tar.gz"
    opener = _opener({start: (301, {"Location": "http://assets.test/b.tar.gz"}, b"")}, asked)

    with pytest.raises(BundleError, match="http://assets.test/b.tar.gz, which is not https"):
        download(start, tmp_path / "out", opener=opener)

    assert asked == [start]


def test_a_redirect_loop_is_given_up_on(tmp_path):
    url = "https://github.test/loop"
    asked: list[str] = []
    opener = _opener({url: (302, {"Location": url}, b"")}, asked)

    with pytest.raises(BundleError, match="redirected more than"):
        download(url, tmp_path / "out", opener=opener)

    assert len(asked) == MAX_REDIRECTS + 1


def test_an_oversize_content_length_is_refused_before_reading(tmp_path):
    url = "https://example.test/big.tar.gz"

    def unread(size=-1):
        raise AssertionError("the body was read although its declared size is over the cap")

    @contextmanager
    def opener(asked_url):
        yield SimpleNamespace(status=200, headers={"Content-Length": "101"}, read=unread)

    with pytest.raises(BundleError, match="over the"):
        download(url, tmp_path / "out", opener=opener, max_bytes=100)


@pytest.mark.parametrize("declared", [{"Content-Length": "10"}, {}, {"Content-Length": "lots"}])
def test_a_stream_over_the_cap_is_refused_whatever_the_header_said(tmp_path, declared):
    url = "https://example.test/big.tar.gz"
    opener = _opener({url: (200, declared, b"x" * 101)}, [])

    with pytest.raises(BundleError, match="sent more than"):
        download(url, tmp_path / "out", opener=opener, max_bytes=100)


def test_an_error_status_is_refused_with_the_status(tmp_path):
    url = "https://example.test/missing.tar.gz"

    with pytest.raises(BundleError, match="HTTP 404"):
        download(url, tmp_path / "out", opener=_opener({url: (404, {}, b"nope")}, []))


@pytest.mark.parametrize("url", ["http://example.test/b.tar.gz", "file:///etc/passwd"])
def test_the_real_opener_refuses_anything_but_https_before_connecting(url):
    with pytest.raises(BundleError, match="https only"):
        open_https(url)


def test_the_user_agent_names_epimemer_and_its_version():
    name, _, version = user_agent().partition("/")

    assert name == "epimemer"
    assert version


def test_a_network_failure_is_reported_in_words(tmp_path):
    def unreachable(url):
        raise urllib.error.URLError("no route to host")

    with pytest.raises(BundleError, match="Could not download .*no route to host"):
        download("https://example.test/b.tar.gz", tmp_path / "out", opener=unreachable)


# --- unpacking ---


@pytest.mark.parametrize(
    "member",
    [
        _member("evil-link", tarfile.SYMTYPE, "/etc/passwd"),
        _member("evil-hardlink", tarfile.LNKTYPE, "index.html"),
        _member("dev-char", tarfile.CHRTYPE),
        _member("dev-block", tarfile.BLKTYPE),
        _member("a-fifo", tarfile.FIFOTYPE),
    ],
    ids=["symlink", "hard link", "character device", "block device", "fifo"],
)
def test_anything_but_files_and_directories_is_refused(tmp_path, member):
    archive = tmp_path / "bundle.tar.gz"
    archive.write_bytes(_tarball(PAGE, member))
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(BundleError, match="only files and directories"):
        unpack(archive, destination)

    assert list(destination.iterdir()) == []


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("/etc/cron.d/evil", "absolute name"),
        ("../outside.txt", "climbs out"),
        ("assets/../../outside.txt", "climbs out"),
        ("assets\\..\\..\\outside.txt", "climbs out"),
    ],
)
def test_a_name_that_leaves_the_bundle_is_refused(tmp_path, name, reason):
    archive = tmp_path / "bundle.tar.gz"
    archive.write_bytes(_tarball({**PAGE, name: b"payload"}))
    destination = tmp_path / "unpack" / "here"
    destination.mkdir(parents=True)

    with pytest.raises(BundleError, match=reason):
        unpack(archive, destination)

    assert list(destination.iterdir()) == []
    assert not (tmp_path / "unpack" / "outside.txt").exists()
    assert not (tmp_path / "outside.txt").exists()


def test_too_many_members_are_refused(tmp_path):
    archive = tmp_path / "bundle.tar.gz"
    archive.write_bytes(_tarball({f"file-{n}": b"x" for n in range(4)}))

    with pytest.raises(BundleError, match="more than 3 entries"):
        unpack(archive, tmp_path, max_members=3)


def test_an_archive_that_unpacks_too_large_is_refused(tmp_path):
    archive = tmp_path / "bundle.tar.gz"
    archive.write_bytes(_tarball({"index.html": b"x" * 60, "big.wasm": b"y" * 60}))
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(BundleError, match="unpacks to more than"):
        unpack(archive, destination, max_bytes=100)

    assert list(destination.iterdir()) == []


@pytest.mark.parametrize(
    "files",
    [{"main.js": b"x"}, {"web/index.html": b"<html>"}],
    ids=["no index", "index only in a subdirectory"],
)
def test_an_archive_without_a_top_level_index_is_refused(tmp_path, files):
    archive = tmp_path / "bundle.tar.gz"
    archive.write_bytes(_tarball(files))
    destination = tmp_path / "out"
    destination.mkdir()

    with pytest.raises(BundleError, match="no index.html at its top level"):
        unpack(archive, destination)


def test_something_that_is_not_a_gzipped_tarball_is_refused(tmp_path):
    archive = tmp_path / "bundle.tar.gz"
    archive.write_bytes(b"<html>GitHub is down</html>")

    with pytest.raises(BundleError, match="could not be unpacked"):
        unpack(archive, tmp_path / "out")


def test_names_written_with_a_leading_dot_slash_unpack_at_the_top(tmp_path):
    """`tar -C dir .` writes every name under `./`, and that is still the top level."""
    archive = tmp_path / "bundle.tar.gz"
    archive.write_bytes(_tarball({f"./{name}": content for name, content in PAGE.items()}))
    destination = tmp_path / "out"
    destination.mkdir()

    unpack(archive, destination)

    assert _tree(destination) == PAGE


# --- fetching end to end ---


def test_a_release_is_fetched_end_to_end(tmp_path):
    data = _tarball(PAGE)
    release = _release_of(data)
    asked: list[str] = []
    signed = "https://release-assets.test/signed?token=abc"
    opener = _opener(
        {
            release_url(release): (302, {"Location": signed}, b""),
            signed: (200, {"Content-Length": str(len(data))}, data),
        },
        asked,
    )

    installed = fetch_bundle(release, tmp_path, opener=opener, now=lambda: FETCHED_AT)

    assert installed == InstalledBundle(
        version="9.9.9", sha256=release.sha256, fetched_at=FETCHED_AT
    )
    assert asked == [release_url(release), signed]
    assert _tree(bundle_dir(tmp_path)) == PAGE
    assert installed_bundle(tmp_path) == installed
    assert already_installed(release, tmp_path)
    assert _leftovers(tmp_path) == []


def test_the_first_fetch_makes_the_visualiser_directory_private(tmp_path):
    _fetch(tmp_path, PAGE)

    assert stat.S_IMODE(visualiser_dir(tmp_path).stat().st_mode) == 0o700


def test_a_checksum_mismatch_installs_nothing_and_keeps_what_was_there(tmp_path):
    _fetch(tmp_path, {**PAGE, "index.html": b"old page"}, version="1.0.0")
    before = _tree(bundle_dir(tmp_path))
    manifest_before = manifest_path(tmp_path).read_bytes()

    data = _tarball(PAGE)
    expected = "0" * 64
    release = BundleRelease(version="2.0.0", sha256=expected)
    with pytest.raises(BundleError) as refused:
        fetch_bundle(release, tmp_path, opener=_serving(data, release, []))

    assert expected in str(refused.value)
    assert hashlib.sha256(data).hexdigest() in str(refused.value)
    assert _tree(bundle_dir(tmp_path)) == before
    assert manifest_path(tmp_path).read_bytes() == manifest_before
    assert _leftovers(tmp_path) == []


def test_a_refused_archive_leaves_no_temporary_files(tmp_path):
    data = _tarball(PAGE, _member("evil-link", tarfile.SYMTYPE, "/etc/passwd"))
    release = _release_of(data)

    with pytest.raises(BundleError):
        fetch_bundle(release, tmp_path, opener=_serving(data, release, []))

    assert not bundle_dir(tmp_path).exists()
    assert installed_bundle(tmp_path) is None
    assert _leftovers(tmp_path) == []


def test_a_new_bundle_replaces_the_old_one_entirely(tmp_path):
    """A file only the old release had must not survive into the new one,
    where the new page could load it."""
    _fetch(tmp_path, {**PAGE, "old-only.js": b"stale"}, version="1.0.0")

    installed = _fetch(tmp_path, PAGE, version="2.0.0")

    assert _tree(bundle_dir(tmp_path)) == PAGE
    assert installed_bundle(tmp_path) == installed
    assert installed.version == "2.0.0"
    assert _leftovers(tmp_path) == []


def _aged(path: Path, days: float) -> Path:
    """`path` last touched `days` before the moment the tests fetch at."""
    at = (FETCHED_AT - timedelta(days=days)).timestamp()
    os.utime(path, (at, at))
    return path


def test_a_later_fetch_removes_what_an_interrupted_one_left(tmp_path):
    """A fetch that is killed cannot clean up after itself, so the next one
    does: its download and its half-unpacked bundle would otherwise stay for
    good."""
    _fetch(tmp_path, PAGE, version="1.0.0")
    viz = visualiser_dir(tmp_path)
    download_left = viz / ".download-killed.tar.gz"
    download_left.write_bytes(b"partial")
    unpack_left = viz / ".unpack-killed"
    unpack_left.mkdir()
    (unpack_left / "index.html").write_bytes(b"half a page")
    _aged(download_left, 2)
    _aged(unpack_left, 2)

    _fetch(tmp_path, PAGE, version="2.0.0")

    assert _leftovers(tmp_path) == []
    assert _tree(bundle_dir(tmp_path)) == PAGE


def test_the_sweep_leaves_everything_else_alone(tmp_path):
    """Only a fetch's own temporary names, and only once a day old: a fetch
    running beside this one, the bundle, the manifest and a file somebody put
    there all stay, however old the last three are."""
    installed = _fetch(tmp_path, PAGE, version="1.0.0")
    viz = visualiser_dir(tmp_path)
    running = viz / ".download-running.tar.gz"
    running.write_bytes(b"in flight")
    _aged(running, 0.5)
    notes = viz / "notes.txt"
    notes.write_bytes(b"mine")
    _aged(notes, 30)
    _aged(manifest_path(tmp_path), 30)
    _aged(bundle_dir(tmp_path), 30)

    remove_leftovers(tmp_path, FETCHED_AT)

    assert running.exists()
    assert notes.exists()
    assert _tree(bundle_dir(tmp_path)) == PAGE
    assert installed_bundle(tmp_path) == installed


def test_a_sweep_of_a_home_with_no_visualiser_directory_does_nothing(tmp_path):
    remove_leftovers(tmp_path, FETCHED_AT)

    assert not visualiser_dir(tmp_path).exists()


def test_a_failed_swap_puts_the_old_bundle_back(tmp_path, monkeypatch):
    _fetch(tmp_path, {**PAGE, "index.html": b"old page"}, version="1.0.0")
    before = _tree(bundle_dir(tmp_path))
    manifest_before = manifest_path(tmp_path).read_bytes()
    real_rename = os.rename

    def failing_rename(src, dst):
        if Path(dst) == bundle_dir(tmp_path) and Path(src).name.startswith(".unpack-"):
            raise OSError("rename refused for the test")
        real_rename(src, dst)

    monkeypatch.setattr(os, "rename", failing_rename)
    data = _tarball(PAGE)
    release = _release_of(data, "2.0.0")

    with pytest.raises(OSError, match="rename refused"):
        fetch_bundle(release, tmp_path, opener=_serving(data, release, []))

    assert _tree(bundle_dir(tmp_path)) == before
    assert manifest_path(tmp_path).read_bytes() == manifest_before
    assert _leftovers(tmp_path) == []


def test_a_failed_manifest_write_puts_the_old_bundle_back(tmp_path, monkeypatch):
    """The manifest and the bundle change together or not at all, so the
    manifest never names a release the bundle is not."""
    _fetch(tmp_path, {**PAGE, "index.html": b"old page"}, version="1.0.0")
    before = _tree(bundle_dir(tmp_path))
    manifest_before = manifest_path(tmp_path).read_bytes()
    real_replace = os.replace

    def failing_replace(src, dst):
        if Path(dst) == manifest_path(tmp_path):
            raise OSError("disk full for the test")
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", failing_replace)
    data = _tarball(PAGE)
    release = _release_of(data, "2.0.0")

    with pytest.raises(OSError, match="disk full"):
        fetch_bundle(release, tmp_path, opener=_serving(data, release, []))

    assert _tree(bundle_dir(tmp_path)) == before
    assert manifest_path(tmp_path).read_bytes() == manifest_before
    assert _leftovers(tmp_path) == []


# --- the manifest ---


def test_no_manifest_and_an_unreadable_one_both_read_as_nothing_installed(tmp_path):
    assert installed_bundle(tmp_path) is None

    manifest_path(tmp_path).parent.mkdir(parents=True)
    manifest_path(tmp_path).write_text("{not json")
    assert installed_bundle(tmp_path) is None

    manifest_path(tmp_path).write_text('{"version": "1.0.0", "sha256": "short"}')
    assert installed_bundle(tmp_path) is None


def test_a_manifest_without_its_page_is_not_installed(tmp_path):
    """The manifest alone proves nothing if someone has since emptied `current`."""
    _fetch(tmp_path, PAGE)
    installed = installed_bundle(tmp_path)
    assert installed is not None
    release = BundleRelease(version=installed.version, sha256=installed.sha256)
    assert already_installed(release, tmp_path)

    (bundle_dir(tmp_path) / "index.html").unlink()

    assert not already_installed(release, tmp_path)
