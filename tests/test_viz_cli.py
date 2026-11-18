"""`epimemer viz fetch`: downloading the browser visualiser.

The handler takes the home directory and the opener as values, so every test
here runs it against `tmp_path` and a fake opener serving a tarball built in
memory. The tests that go through `main` replace the real opener first, so
none of them can reach the network.
"""

import hashlib
import io
import tarfile
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from epimemer import cli
from epimemer.cli import _fetch_visualiser, main
from epimemer.visualization import bundle
from epimemer.visualization.bundle import (
    PINNED_RELEASE,
    BundleRelease,
    InstalledBundle,
    bundle_dir,
    installed_bundle,
    release_url,
)

PAGE = {"index.html": b"<!doctype html><title>visualiser</title>", "app.js": b"run()"}


def _tarball(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


DATA = _tarball(PAGE)
DIGEST = hashlib.sha256(DATA).hexdigest()


def _opener(asked: list[str], data: bytes = DATA):
    """Serves `data` for any URL and records each one asked for."""

    @contextmanager
    def opener(url: str):
        asked.append(url)
        yield SimpleNamespace(status=200, headers={}, read=io.BytesIO(data).read)

    return opener


def _refusing_opener(url: str):
    raise AssertionError(f"nothing should have been downloaded, but {url} was asked for")


@pytest.fixture
def offline(monkeypatch, tmp_path):
    """`main` as a user runs it, with `tmp_path` as home and no network to reach."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(bundle, "open_https", _refusing_opener)
    return tmp_path


def test_with_no_options_the_pinned_release_is_fetched(monkeypatch, capsys, tmp_path):
    fetched = []

    def fake_fetch(release, home, *, opener):
        fetched.append((release, home, opener))
        return InstalledBundle(
            version=release.version, sha256=release.sha256, fetched_at=datetime.now(UTC)
        )

    monkeypatch.setattr(bundle, "fetch_bundle", fake_fetch)
    opener = _opener([])

    code = _fetch_visualiser(None, None, False, home=tmp_path, environ={}, opener=opener)

    out, err = capsys.readouterr()
    assert code == 0
    assert fetched == [(PINNED_RELEASE, tmp_path, opener)]
    assert release_url(PINNED_RELEASE) in out
    assert f"Fetched visualiser {PINNED_RELEASE.version} into {bundle_dir(tmp_path)}" in out
    assert err == ""


def test_a_named_release_is_fetched_and_says_where_it_went(capsys, tmp_path):
    asked: list[str] = []

    code = _fetch_visualiser(
        "v1.2.3", DIGEST, False, home=tmp_path, environ={}, opener=_opener(asked)
    )

    out, _ = capsys.readouterr()
    release = BundleRelease(version="1.2.3", sha256=DIGEST)
    assert code == 0
    assert asked == [release_url(release)]
    assert out.index(release_url(release)) < out.index("Fetched visualiser 1.2.3")
    assert str(bundle_dir(tmp_path)) in out
    assert (bundle_dir(tmp_path) / "index.html").read_bytes() == PAGE["index.html"]
    assert installed_bundle(tmp_path).version == "1.2.3"


@pytest.mark.parametrize(
    "options",
    [["--release", "1.2.3"], ["--sha256", "a" * 64]],
    ids=["release alone", "sha256 alone"],
)
def test_release_and_sha256_go_together(offline, capsys, options):
    """A release other than the pinned one has no checksum in this package, so
    the user supplies it, and one without the other is a usage error."""
    code = main(["viz", "fetch", *options])

    _, err = capsys.readouterr()
    assert code == 2
    assert "--release and --sha256 go together" in err
    assert ".sha256 file" in err
    assert not bundle_dir(offline).exists()


@pytest.mark.parametrize(
    ("release", "digest", "said"),
    [("latest", "a" * 64, "not a release version"), ("1.2.3", "abc", "not a sha256")],
)
def test_a_malformed_release_is_a_usage_error(offline, capsys, release, digest, said):
    code = main(["viz", "fetch", "--release", release, "--sha256", digest])

    _, err = capsys.readouterr()
    assert code == 2
    assert said in err


def test_a_release_already_fetched_is_not_downloaded_again(capsys, tmp_path):
    asked: list[str] = []
    assert (
        _fetch_visualiser("1.2.3", DIGEST, False, home=tmp_path, environ={}, opener=_opener(asked))
        == 0
    )
    capsys.readouterr()

    code = _fetch_visualiser(
        "1.2.3", DIGEST, False, home=tmp_path, environ={}, opener=_opener(asked)
    )

    out, _ = capsys.readouterr()
    assert code == 0
    assert len(asked) == 1
    assert "already fetched" in out
    assert "--force" in out


def test_force_downloads_a_fetched_release_again(capsys, tmp_path):
    asked: list[str] = []
    assert (
        _fetch_visualiser("1.2.3", DIGEST, False, home=tmp_path, environ={}, opener=_opener(asked))
        == 0
    )

    code = _fetch_visualiser(
        "1.2.3", DIGEST, True, home=tmp_path, environ={}, opener=_opener(asked)
    )

    assert code == 0
    assert len(asked) == 2


def test_a_failed_fetch_exits_1_with_the_reason_on_stderr(capsys, tmp_path):
    """The pinned release's checksum is real, so a fake tarball cannot match it:
    the refusal names both digests and nothing is installed."""
    asked: list[str] = []

    code = _fetch_visualiser(None, None, False, home=tmp_path, environ={}, opener=_opener(asked))

    out, err = capsys.readouterr()
    assert code == 1
    assert asked == [release_url(PINNED_RELEASE)]
    assert release_url(PINNED_RELEASE) in out
    assert PINNED_RELEASE.sha256 in err
    assert DIGEST in err
    assert not bundle_dir(tmp_path).exists()


def test_fetching_reads_no_server_configuration(offline, monkeypatch, capsys):
    """A download into the home directory has nothing to do with the store, so
    a configuration the server would refuse must not stop it."""

    def refused():
        raise AssertionError("viz fetch read the server configuration")

    monkeypatch.setattr(cli, "load_config", refused)
    monkeypatch.setattr(bundle, "open_https", _opener([]))

    code = main(["viz", "fetch", "--release", "1.2.3", "--sha256", DIGEST])

    assert code == 0
    assert (bundle_dir(offline) / "index.html").read_bytes() == PAGE["index.html"]


def test_a_fetch_says_when_another_directory_will_be_shown_instead(capsys, tmp_path):
    """With the override set the fetched bundle is not what gets shown, and a
    fetch that said nothing about it would look as if it had not worked."""
    build = tmp_path / "my-build"
    environ = {bundle.BUNDLE_DIR_ENV: str(build)}

    code = _fetch_visualiser(
        "1.2.3", DIGEST, False, home=tmp_path, environ=environ, opener=_opener([])
    )

    out, _ = capsys.readouterr()
    assert code == 0
    assert bundle.BUNDLE_DIR_ENV in out
    assert str(build) in out


def test_a_fetch_with_no_override_does_not_mention_one(capsys, tmp_path):
    code = _fetch_visualiser("1.2.3", DIGEST, False, home=tmp_path, environ={}, opener=_opener([]))

    out, _ = capsys.readouterr()
    assert code == 0
    assert bundle.BUNDLE_DIR_ENV not in out
