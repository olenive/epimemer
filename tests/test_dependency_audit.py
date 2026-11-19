"""The advisory check's reading of OSV and GitHub records, without asking either.

The network call is not exercised here: what can be wrong in this script is the
reading, and a suite member that needed the internet would be the first thing
skipped on a bad connection. `scripts/audit_dependencies.py` says why the script
exists at all, which is that Dependabot cannot see `uv.lock` and OSV cannot see
an advisory GitHub has not reviewed.
"""

import email.message
import importlib.util
import sys
import tomllib
import urllib.error
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "audit_dependencies", Path(__file__).parent.parent / "scripts" / "audit_dependencies.py"
)
audit = importlib.util.module_from_spec(_spec)
sys.modules["audit_dependencies"] = audit
_spec.loader.exec_module(audit)


def _vuln(vuln_id: str, severity: str | None = None, *, name: str = "widget", fixed=()):
    record = {
        "id": vuln_id,
        "affected": [
            {
                "package": {"name": name, "ecosystem": "PyPI"},
                "ranges": [{"events": [{"introduced": "0"}, *({"fixed": f} for f in fixed)]}],
            }
        ],
    }
    if severity is not None:
        record["database_specific"] = {"severity": severity}
    return record


# The real one, for the tests that put it back after a fixture replaces it.
_QUERY_GITHUB = audit.query_github


def _entry(name: str | None, vulnerable: str | None, patched: str = "", ecosystem: str = "pip"):
    """One `vulnerabilities[]` entry of a GitHub repository advisory."""
    return {
        "package": {"ecosystem": ecosystem if name else "", "name": name or ""},
        "vulnerable_version_range": vulnerable,
        "patched_versions": patched,
        "vulnerable_functions": [],
    }


def _advisory(ghsa: str, severity: str | None, entries, *, withdrawn: str | None = None):
    return {
        "ghsa_id": ghsa,
        "severity": severity,
        "withdrawn_at": withdrawn,
        "vulnerabilities": list(entries),
    }


def _refuse(code: int, **headers: str):
    """A stand-in for one GitHub request that GitHub answers with this HTTP error."""
    message = email.message.Message()
    for key, value in headers.items():
        message[f"x-ratelimit-{key}"] = value

    def _get(url, token, timeout):
        raise urllib.error.HTTPError(url, code, "refused", message, None)

    return _get


class TestSeverity:
    def test_the_worst_of_several_wins(self):
        vulns = [_vuln("a", "LOW"), _vuln("b", "CRITICAL"), _vuln("c", "MODERATE")]
        assert audit.severity_of(vulns) == "CRITICAL"

    def test_an_advisory_with_no_severity_is_unknown_rather_than_dropped(self):
        """Missing information is not an assurance, so it still counts as a finding."""
        assert audit.severity_of([_vuln("a")]) == "UNKNOWN"

    def test_an_unrecognised_severity_does_not_raise(self):
        assert audit.severity_of([_vuln("a", "SPICY"), _vuln("b", "HIGH")]) == "HIGH"

    def test_nothing_is_unknown(self):
        assert audit.severity_of([]) == "UNKNOWN"


class TestFixedVersions:
    def test_it_collects_every_fix_named(self):
        vulns = [_vuln("a", "HIGH", fixed=("1.2.0",)), _vuln("b", "LOW", fixed=("1.3.1", "1.2.0"))]
        assert audit.fixed_versions("widget", vulns) == ["1.2.0", "1.3.1"]

    def test_another_package_in_the_same_record_is_ignored(self):
        """One advisory can name several packages; only this one's fixes apply."""
        vulns = [_vuln("a", "HIGH", name="something-else", fixed=("9.9.9",))]
        assert audit.fixed_versions("widget", vulns) == []

    def test_an_advisory_with_no_fix_yields_nothing(self):
        assert audit.fixed_versions("widget", [_vuln("a", "HIGH")]) == []


class TestReport:
    def test_a_clean_run_says_so(self):
        assert "none with a published advisory" in audit.report({}, 132, "UNKNOWN")

    def test_findings_are_ordered_worst_first(self):
        findings = {
            "quiet": {"version": "1.0", "ids": ["X-1"], "severity": "LOW", "fixed": ["1.1"]},
            "loud": {"version": "2.0", "ids": ["X-2"], "severity": "CRITICAL", "fixed": ["2.1"]},
        }
        body = audit.report(findings, 10, "UNKNOWN")
        assert body.index("loud") < body.index("quiet")

    def test_a_package_with_no_fix_says_so_rather_than_showing_an_empty_list(self):
        findings = {"stuck": {"version": "1.0", "ids": ["X-1"], "severity": "HIGH", "fixed": []}}
        assert "no fixed version published" in audit.report(findings, 10, "UNKNOWN")

    def test_only_findings_at_or_above_the_threshold_are_marked(self):
        findings = {
            "quiet": {"version": "1.0", "ids": ["X-1"], "severity": "LOW", "fixed": []},
            "loud": {"version": "2.0", "ids": ["X-2"], "severity": "HIGH", "fixed": []},
        }
        lines = audit.report(findings, 10, "HIGH").splitlines()
        marked = [line for line in lines if line.startswith("!")]
        assert len(marked) == 1 and "loud" in marked[0]


class TestExitCode:
    """What makes it a scheduled job that stays quiet until it has something to say."""

    @pytest.fixture
    def findings(self, monkeypatch):
        """OSV's answer at this severity, and GitHub's from these advisories."""

        def _set(severity: str | None, advisories=()):
            found = (
                {}
                if severity is None
                else {"widget": {"version": "1.0", "ids": ["X"], "severity": severity, "fixed": []}}
            )
            monkeypatch.setenv("GITHUB_TOKEN", "a-token")
            monkeypatch.setattr(audit, "installed_packages", lambda: [("widget", "1.0")])
            monkeypatch.setattr(audit, "query_osv", lambda packages, **kw: found)
            monkeypatch.setattr(
                audit, "installed_repositories", lambda: {"widget": {"acme/widget"}}
            )
            monkeypatch.setattr(
                audit, "query_github", lambda repos, token, **kw: ({"acme/widget": advisories}, [])
            )

        return _set

    def test_a_clean_environment_exits_zero(self, findings, capsys):
        findings(None)
        assert audit.main([]) == 0

    def test_any_advisory_fails_by_default(self, findings, capsys):
        findings("UNKNOWN")
        assert audit.main([]) == 1

    def test_a_raised_bar_passes_over_what_sits_below_it(self, findings, capsys):
        findings("MODERATE")
        assert audit.main(["--fail-on", "high"]) == 0

    def test_a_raised_bar_still_fails_at_the_bar(self, findings, capsys):
        findings("HIGH")
        assert audit.main(["--fail-on", "high"]) == 1

    def test_a_finding_on_github_alone_fails(self, findings, capsys):
        findings(None, [_advisory("GHSA-1", "low", [_entry("widget", "<= 1.0")])])
        assert audit.main([]) == 1

    def test_an_advisory_for_a_person_fails_at_its_severity(self, findings, capsys):
        findings(None, [_advisory("GHSA-1", "medium", [_entry("widget", "all")])])
        assert audit.main([]) == 1
        assert audit.main(["--fail-on", "high"]) == 0

    def test_a_github_refusal_is_the_check_failing_not_a_finding(
        self, findings, monkeypatch, capsys
    ):
        findings("HIGH")
        monkeypatch.setattr(audit, "query_github", _QUERY_GITHUB)
        monkeypatch.setattr(audit, "_github_get", _refuse(403, remaining="0"))
        assert audit.main([]) == 2
        assert "rate limit" in capsys.readouterr().err

    def test_an_unreachable_github_exits_two(self, findings, monkeypatch, capsys):
        findings(None)
        monkeypatch.setattr(audit, "query_github", _QUERY_GITHUB)

        def _unreachable(url, token, timeout):
            raise urllib.error.URLError("no route to host")

        monkeypatch.setattr(audit, "_github_get", _unreachable)
        assert audit.main([]) == 2

    def test_no_token_exits_two_and_says_how_to_go_on(self, findings, monkeypatch, capsys):
        """Without a token GitHub's half cannot finish, so the check has not run."""
        findings("HIGH")
        monkeypatch.delenv("GITHUB_TOKEN")
        assert audit.main([]) == 2
        err = capsys.readouterr().err
        assert "GITHUB_TOKEN" in err and "--skip-github" in err

    def test_skipping_github_needs_no_token_and_says_so(self, findings, monkeypatch, capsys):
        findings(None, [_advisory("GHSA-1", "critical", [_entry("widget", "<= 1.0")])])
        monkeypatch.delenv("GITHUB_TOKEN")
        assert audit.main(["--skip-github"]) == 0
        assert "were not checked" in capsys.readouterr().out

    def test_a_defect_in_the_script_exits_two_not_one(self, findings, monkeypatch, capsys):
        """Python's own exit code for an uncaught exception is 1, which is a finding."""
        findings(None)

        def _broken(packages, **kw):
            raise RuntimeError("a bug")

        monkeypatch.setattr(audit, "query_osv", _broken)
        assert audit.main([]) == 2


class TestGithubRepository:
    """Which repository a package's metadata points at, if any."""

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/pydantic/pydantic",
            "https://github.com/pydantic/pydantic/",
            "https://github.com/pydantic/pydantic.git",
            "https://github.com/pydantic/pydantic/issues",
            "https://github.com/pydantic/pydantic/tree/main/pydantic-core",
            "https://github.com/pydantic/pydantic/blob/main/HISTORY.md#v2",
            "https://github.com/pydantic/pydantic/actions?query=branch:main",
            "https://www.github.com/pydantic/pydantic",
            "http://github.com/pydantic/pydantic",
            "git+https://github.com/pydantic/pydantic.git",
            "  https://github.com/Pydantic/Pydantic  ",
        ],
    )
    def test_every_shape_names_the_same_repository(self, url):
        assert audit.github_repository(url) == "pydantic/pydantic"

    @pytest.mark.parametrize(
        "url",
        [
            "https://github.com/sponsors/samuelcolvin",
            "https://github.com/orgs/pydantic/discussions",
            "https://github.com/pydantic",
            "https://github.com/aio-libs/.github/blob/master/CODE_OF_CONDUCT.md",
            "https://grpc.github.io/grpc/python",
            "https://gitlab.com/pydantic/pydantic",
            "https://github.com.example.org/pydantic/pydantic",
            "ftp://github.com/pydantic/pydantic",
            "UNKNOWN",
            "",
        ],
    )
    def test_a_url_that_is_not_a_repository_names_none(self, url):
        assert audit.github_repository(url) is None

    def test_a_project_url_is_read_past_its_label(self):
        urls = audit.metadata_urls(
            ["Source, https://github.com/grpc/grpc", "Homepage, https://grpc.io"], None
        )
        assert urls == ["https://github.com/grpc/grpc", "https://grpc.io"]

    def test_the_home_page_counts_too(self):
        urls = audit.metadata_urls([], "https://github.com/oauthlib/oauthlib")
        assert audit.repositories_of(urls) == {"oauthlib/oauthlib"}

    def test_links_into_one_repository_are_one_repository(self):
        urls = audit.metadata_urls(
            [
                "Issues, https://github.com/authlib/authlib/issues",
                "Source, https://github.com/authlib/authlib",
                "Donate, https://github.com/sponsors/lepture",
            ],
            None,
        )
        assert audit.repositories_of(urls) == {"authlib/authlib"}


def _holds(text: str, version: str) -> bool:
    return audit.github_range(text)(audit.Version(version))


class TestGithubRange:
    """GitHub's range syntax, read against an installed version."""

    def test_a_bounded_range(self):
        assert _holds(">= 2.13.0, < 2.14.2", "2.13.5")
        assert not _holds(">= 2.13.0, < 2.14.2", "2.14.2")
        assert not _holds(">= 2.13.0, < 2.14.2", "2.12.9")

    def test_an_inclusive_upper_bound(self):
        assert _holds("<= 1.9.8", "1.9.8")
        assert not _holds("<= 1.9.8", "1.9.9")

    def test_githubs_single_equals_is_an_exact_version(self):
        assert _holds("= 1.85.0", "1.85.0")
        assert not _holds("= 1.85.0", "1.85.1")

    def test_a_bare_list_is_those_versions_exactly(self):
        """How authlib writes it: `1.6.5, 1.6.6`, patched in 1.6.7."""
        assert _holds("1.6.5, 1.6.6", "1.6.6")
        assert not _holds("1.6.5, 1.6.6", "1.6.4")
        assert not _holds("1.6.5, 1.6.6", "1.9.8")

    def test_a_pre_release_inside_the_range_is_affected(self):
        assert _holds(">= 1.0, < 2.0", "1.5.0rc1")

    @pytest.mark.parametrize("text", ["all", "~> 1.2", ">= 2.0 < 3", "1.2 - 1.4"])
    def test_a_range_that_will_not_parse_is_none(self, text):
        assert audit.github_range(text) is None

    @pytest.mark.parametrize("text", [None, "", " , "])
    def test_an_empty_range_is_none_rather_than_every_version(self, text):
        assert audit.github_range(text) is None


# Two packages from one repository and one from another, as pydantic and torch are.
_INSTALLED = [("pydantic", "2.13.5"), ("pydantic_core", "2.46.5"), ("torch", "2.14.0")]
_REPOS = {
    "pydantic": {"pydantic/pydantic"},
    "pydantic_core": {"pydantic/pydantic"},
    "torch": {"pytorch/pytorch"},
}


def _read(repo: str, *advisories):
    return audit.github_findings(_INSTALLED, _REPOS, {repo: list(advisories)})


class TestGithubFindings:
    """Repository advisories read against what is installed."""

    def test_a_named_package_in_range_is_affected(self):
        advisory = _advisory(
            "GHSA-a", "medium", [_entry("pydantic", ">= 2.13.0, < 2.14.2", "2.14.2")]
        )
        findings, unresolved, notes = _read("pydantic/pydantic", advisory)
        assert findings == {
            "pydantic": {
                "version": "2.13.5",
                "ids": ["GHSA-a"],
                "severity": "MODERATE",
                "fixed": ["2.14.2"],
            }
        }
        assert unresolved == [] and notes == []

    def test_a_named_package_out_of_range_is_not(self):
        advisory = _advisory("GHSA-a", "high", [_entry("pydantic", "< 2.0.0", "2.0.0")])
        assert _read("pydantic/pydantic", advisory) == ({}, [], [])

    def test_names_match_under_pep_503(self):
        advisory = _advisory("GHSA-a", "low", [_entry("Pydantic-Core", "<= 2.46.5")])
        findings, _, _ = _read("pydantic/pydantic", advisory)
        assert list(findings) == ["pydantic_core"]

    def test_a_package_from_another_repository_is_still_matched(self):
        """An advisory is about the package it names, wherever that came from."""
        advisory = _advisory("GHSA-a", "high", [_entry("torch", ">= 2.0")])
        findings, _, _ = _read("pydantic/pydantic", advisory)
        assert list(findings) == ["torch"]

    def test_another_ecosystem_is_ignored(self):
        advisory = _advisory("GHSA-a", "high", [_entry("pydantic", ">= 0", ecosystem="npm")])
        assert _read("pydantic/pydantic", advisory) == ({}, [], [])

    def test_a_package_not_installed_is_ignored(self):
        advisory = _advisory("GHSA-a", "high", [_entry("pydantic-settings", ">= 0")])
        assert _read("pydantic/pydantic", advisory) == ({}, [], [])

    def test_a_withdrawn_advisory_is_ignored(self):
        advisory = _advisory(
            "GHSA-a", "high", [_entry("pydantic", ">= 0")], withdrawn="2026-11-01T00:00:00Z"
        )
        assert _read("pydantic/pydantic", advisory) == ({}, [], [])

    def test_no_package_from_a_repository_with_one_package_is_that_package(self):
        """The torch advisory names no package, only a range."""
        advisory = _advisory("GHSA-t", "high", [_entry(None, ">=2.14.0", "2.15.0")])
        findings, unresolved, _ = _read("pytorch/pytorch", advisory)
        assert findings["torch"]["ids"] == ["GHSA-t"] and findings["torch"]["fixed"] == ["2.15.0"]
        assert unresolved == []

    def test_no_package_from_a_shared_repository_goes_to_a_person(self):
        advisory = _advisory("GHSA-p", "high", [_entry(None, ">= 2.0")])
        findings, unresolved, _ = _read("pydantic/pydantic", advisory)
        assert findings == {}
        assert [u["id"] for u in unresolved] == ["GHSA-p"]
        assert unresolved[0]["severity"] == "HIGH"
        assert "pydantic 2.13.5" in unresolved[0]["reason"]
        assert "pydantic_core 2.46.5" in unresolved[0]["reason"]

    def test_no_package_whose_range_clears_every_candidate_is_dropped(self):
        advisory = _advisory("GHSA-p", "high", [_entry(None, "< 1.0")])
        assert _read("pydantic/pydantic", advisory) == ({}, [], [])

    def test_a_range_that_will_not_parse_goes_to_a_person(self):
        advisory = _advisory("GHSA-x", "critical", [_entry("pydantic", "~> 2.13")])
        findings, unresolved, _ = _read("pydantic/pydantic", advisory)
        assert findings == {}
        assert unresolved[0]["severity"] == "CRITICAL" and "~> 2.13" in unresolved[0]["reason"]

    def test_an_unparseable_range_past_every_patch_is_a_note(self):
        """Pydantic's 2021 advisory: range `all`, patched only on the 1.x line."""
        advisory = _advisory(
            "GHSA-v1", "medium", [_entry("pydantic", "all", "v1.8.2,v1.7.4,v1.6.2")]
        )
        findings, unresolved, notes = _read("pydantic/pydantic", advisory)
        assert findings == {} and unresolved == []
        assert [n["id"] for n in notes] == ["GHSA-v1"]

    def test_an_unparseable_range_not_past_a_patch_goes_to_a_person(self):
        advisory = _advisory("GHSA-v1", "medium", [_entry("pydantic", "all", "2.14.0, 1.10.9")])
        _, unresolved, notes = _read("pydantic/pydantic", advisory)
        assert [u["id"] for u in unresolved] == ["GHSA-v1"] and notes == []

    def test_a_version_that_is_not_pep_440_goes_to_a_person(self):
        findings, unresolved, _ = audit.github_findings(
            [("oddity", "not-a-version")],
            {"oddity": {"acme/oddity"}},
            {"acme/oddity": [_advisory("GHSA-o", "low", [_entry("oddity", "< 2.0")])]},
        )
        assert findings == {} and "not a PEP 440 version" in unresolved[0]["reason"]

    def test_every_patched_version_is_kept(self):
        advisory = _advisory(
            "GHSA-g",
            "high",
            [_entry("pydantic", ">= 2.0, < 2.15", "2.13.9 and 2.14.1")],
        )
        findings, _, _ = _read("pydantic/pydantic", advisory)
        assert findings["pydantic"]["fixed"] == ["2.13.9", "2.14.1"]

    def test_several_advisories_on_one_package_are_one_finding(self):
        findings, _, _ = _read(
            "pydantic/pydantic",
            _advisory("GHSA-a", "low", [_entry("pydantic", "<= 2.13.5", "2.13.6")]),
            _advisory("GHSA-b", "critical", [_entry("pydantic", "= 2.13.5", "2.14.0")]),
        )
        assert findings["pydantic"]["ids"] == ["GHSA-a", "GHSA-b"]
        assert findings["pydantic"]["severity"] == "CRITICAL"
        assert findings["pydantic"]["fixed"] == ["2.13.6", "2.14.0"]

    @pytest.mark.parametrize(
        ("github", "ours"),
        [("critical", "CRITICAL"), ("high", "HIGH"), ("medium", "MODERATE"), ("low", "LOW")],
    )
    def test_githubs_severity_in_osvs_words(self, github, ours):
        assert audit.github_severity({"severity": github}) == ours

    def test_no_severity_is_unknown(self):
        assert audit.github_severity({"severity": None}) == "UNKNOWN"


class TestMergeFindings:
    def test_an_advisory_already_in_osv_is_listed_once(self):
        osv = {"grpcio": {"version": "1.83.1", "ids": ["GHSA-a"], "severity": "LOW", "fixed": []}}
        github = {
            "grpcio": {
                "version": "1.83.1",
                "ids": ["GHSA-a", "GHSA-b"],
                "severity": "CRITICAL",
                "fixed": ["1.83.2"],
            }
        }
        merged = audit.merge_findings(osv, github)
        assert merged["grpcio"]["ids"] == ["GHSA-a", "GHSA-b"]
        assert merged["grpcio"]["severity"] == "CRITICAL"
        assert merged["grpcio"]["fixed"] == ["1.83.2"]

    def test_a_package_one_source_names_passes_through(self):
        only = {"torch": {"version": "2.14.0", "ids": ["GHSA-t"], "severity": "HIGH", "fixed": []}}
        assert audit.merge_findings({}, only) == only
        assert audit.merge_findings(only, {}) == only


class TestRepositoryAdvisories:
    """Reading one repository's security page, page by page."""

    def test_it_follows_every_page(self, monkeypatch):
        second = f"{audit.GITHUB_API}/repositories/1/security-advisories?after=x"
        pages = iter([([{"ghsa_id": "GHSA-1"}], second), ([{"ghsa_id": "GHSA-2"}], None)])
        asked = []

        def _get(url, token, timeout):
            asked.append(url)
            return next(pages)

        monkeypatch.setattr(audit, "_github_get", _get)
        found = audit.repository_advisories("acme/widget", "a-token")
        assert [a["ghsa_id"] for a in found] == ["GHSA-1", "GHSA-2"]
        assert "state=published" in asked[0] and asked[1] == second

    def test_a_next_page_on_another_host_stops_the_check(self, monkeypatch):
        """The token goes to GitHub and nowhere else."""
        monkeypatch.setattr(
            audit, "_github_get", lambda url, token, timeout: ([], "https://example.org/page2")
        )
        with pytest.raises(ValueError, match="example.org"):
            audit.repository_advisories("acme/widget", "a-token")

    def test_a_repository_github_does_not_have_is_reported_missing(self, monkeypatch):
        monkeypatch.setattr(audit, "_github_get", _refuse(404))
        assert audit.query_github(["acme/gone"], "a-token") == ({}, ["acme/gone"])

    def test_the_rate_limit_says_so(self, monkeypatch):
        monkeypatch.setattr(audit, "_github_get", _refuse(403, remaining="0", reset="0"))
        with pytest.raises(OSError, match="rate limit"):
            audit.repository_advisories("acme/widget", "a-token")

    def test_any_other_refusal_is_an_error(self, monkeypatch):
        monkeypatch.setattr(audit, "_github_get", _refuse(500))
        with pytest.raises(OSError, match="HTTP 500"):
            audit.repository_advisories("acme/widget", "a-token")

    def test_a_response_that_is_not_a_list_is_an_error(self, monkeypatch):
        monkeypatch.setattr(
            audit, "_github_get", lambda url, token, timeout: ({"message": "?"}, None)
        )
        with pytest.raises(ValueError, match="not a list"):
            audit.repository_advisories("acme/widget", "a-token")

    @pytest.mark.parametrize("token", ["a-token", None])
    def test_the_token_is_sent_only_where_it_was_asked_for(self, monkeypatch, token):
        """Unredirected: a redirect carries the request on, never the token."""
        sent = []

        def _capture(request, timeout):
            sent.append(request)
            raise urllib.error.URLError("offline")

        monkeypatch.setattr(audit.urllib.request, "urlopen", _capture)
        with pytest.raises(urllib.error.URLError):
            audit._github_get(f"{audit.GITHUB_API}/repos/acme/widget", token, 1.0)
        request = sent[0]
        assert "Authorization" not in request.headers
        expected = {"Authorization": "Bearer a-token"} if token else {}
        assert request.unredirected_hdrs == expected


def _summary(**overrides):
    base = {"repositories": 3, "unchecked": [], "missing": [], "unresolved": [], "notes": []}
    return {**base, **overrides}


class TestGithubReport:
    def test_skipping_github_is_said_plainly(self):
        assert "GitHub repository advisories were not checked" in audit.report({}, 10, "UNKNOWN")

    def test_packages_left_to_osv_alone_are_named(self):
        body = audit.report({}, 10, "UNKNOWN", _summary(unchecked=["decorator", "pluggy"]))
        assert "2 packages name none" in body and "decorator, pluggy" in body

    def test_a_missing_repository_is_named(self):
        body = audit.report({}, 10, "UNKNOWN", _summary(missing=["acme/gone"]))
        assert "acme/gone" in body

    def test_an_advisory_for_a_person_is_counted_and_marked(self):
        item = {"id": "GHSA-p", "severity": "HIGH", "repo": "acme/widget", "reason": "why"}
        lines = audit.report({}, 10, "HIGH", _summary(unresolved=[item])).splitlines()
        assert "1 for a person to read" in lines[0]
        assert any(line.startswith("!") and "GHSA-p" in line for line in lines)

    def test_a_note_is_shown_but_not_marked(self):
        note = {"id": "GHSA-v1", "repo": "pydantic/pydantic", "reason": "range 'all'"}
        body = audit.report({}, 10, "UNKNOWN", _summary(notes=[note]))
        assert "none with a published advisory" in body
        assert any("GHSA-v1" in line and not line.startswith("!") for line in body.splitlines())


def test_it_reads_this_environment():
    """The environment, not the lock file: an advisory is about what is installed.

    The version comes from `pyproject.toml` rather than being written here: a
    release bump would otherwise fail this test for the one reason that is not a
    defect, and the copy that goes stale is the one nobody is looking at.
    """
    manifest = tomllib.loads((Path(__file__).parent.parent / "pyproject.toml").read_text())
    packages = audit.installed_packages()
    assert ("epimemer", manifest["project"]["version"]) in packages
    assert len(packages) > 50
