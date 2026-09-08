"""The advisory check's reading of an OSV record, without asking OSV.

The network call is not exercised here: what can be wrong in this script is the
reading, and a suite member that needed the internet would be the first thing
skipped on a bad connection. `scripts/audit_dependencies.py` says why the script
exists at all, which is that Dependabot cannot see `uv.lock`.
"""

import importlib.util
import sys
import tomllib
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
        def _set(severity: str | None):
            found = (
                {}
                if severity is None
                else {"widget": {"version": "1.0", "ids": ["X"], "severity": severity, "fixed": []}}
            )
            monkeypatch.setattr(audit, "installed_packages", lambda: [("widget", "1.0")])
            monkeypatch.setattr(audit, "query_osv", lambda packages, **kw: found)

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
