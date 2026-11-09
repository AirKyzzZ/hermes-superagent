from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def start_example(name: str, path: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "examples" / name / "run.py")],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
        env={**os.environ, "PATH": path or os.environ["PATH"]},
    )


def run_example(name: str, path: str = "") -> str:
    done = start_example(name, path)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def github_issue(repo: str, number: int, title: str, updated: str) -> dict[str, object]:
    return {
        "repository_url": f"https://api.github.com/repos/{repo}",
        "number": number,
        "title": title,
        "html_url": f"https://github.com/{repo}/issues/{number}",
        "updated_at": updated,
    }


def fake_gh(directory: Path, search: dict[str, list[dict[str, object]]]) -> str:
    fixture = directory / "gh.json"
    fixture.write_text(json.dumps({"search": search}), encoding="utf-8")
    command = directory / "gh"
    command.write_text(
        f'#!/bin/sh\nFAKE_GH_FIXTURE="{fixture}" exec "{sys.executable}" "{ROOT / "tests" / "fake_gh.py"}" "$@"\n',
        encoding="utf-8",
    )
    command.chmod(0o755)
    return f"{directory}{os.pathsep}{os.environ['PATH']}"


def test_nothing_slips_keeps_what_each_source_loses() -> None:
    output = run_example("nothing-slips")
    assert "Brief for Mon 09 Nov: 13 items" in output
    assert "its snapshot shrank from 6 to 2 items, so nothing was marked gone" in output
    assert "Gone from their source without a close (1)" in output
    assert "Reopened at the source (1)" in output
    assert "Refund webhook retries forever" in output.split("Reopened at the source (1)")[1]
    assert "Also open, each shown at least every 3 days (8)" in output
    assert "4 open tasks, and no trace of the one that was deleted" in output
    assert '5 open. "Fix the backup job" stays' in output


def test_night_shift_ends_with_one_digest_and_the_sessions_board() -> None:
    output = run_example("night-shift")
    assert "stuck -> restart (worker process is gone without a final report)" in output
    assert "stuck -> escalate (last 3 reports are identical)" in output
    assert "Done (4):" in output
    assert "Waiting on you (1):" in output
    assert "v4 drops the EUR rounding helper" in output
    assert "Escalated (1):" in output
    assert "Ignored events: github 2" in output
    assert "passed_through" in output
    assert "SDK on v4 with the helper inlined" in output
    assert "docs-site [laptop] is waiting: permission for Bash: npm publish" in output
    assert "billing [laptop] is limited: usage limit reached" in output
    assert "api-gateway [laptop] is stuck" in output


def test_the_watchdog_catches_each_way_a_worker_gets_stuck() -> None:
    output = run_example("watchdog")
    assert "stuck -> restart (last 3 reports are identical)" in output
    assert "late report refused" in output
    assert "stuck -> restart (no report for" in output
    assert "stuck -> restart (worker process is gone without a final report)" in output
    assert "stuck -> restart (same tool call and result 5 times in a row)" in output
    assert "stuck -> escalate (last 3 reports are identical)" in output
    assert "Needs you [untangle the billing migration" in output
    assert output.count("done on attempt 2") == 4
    assert output.count("a person was told") == 1


def test_your_github_lists_what_has_waited_longest(tmp_path: Path) -> None:
    old = github_issue("acme/shop", 12, "Refund webhook retries forever", "2020-01-01T00:00:00Z")
    new = github_issue("acme/docs", 3, "Fix the install page", "2026-01-01T00:00:00Z")
    search = {"is:open assignee:@me": [new, old], "is:open mentions:@me": [old]}
    output = run_example("your-github", fake_gh(tmp_path, search))
    assert "2 across 2 repositories" in output
    assert output.index("acme/shop#12 Refund webhook") < output.index("acme/docs#3 Fix the install page")


def test_your_github_says_what_it_needs_when_gh_is_missing(tmp_path: Path) -> None:
    done = start_example("your-github", str(tmp_path))
    assert done.returncode != 0
    assert "https://cli.github.com" in done.stderr


def test_your_github_reports_a_failed_read(tmp_path: Path) -> None:
    command = tmp_path / "gh"
    command.write_text("#!/bin/sh\necho 'not logged in' >&2\nexit 1\n", encoding="utf-8")
    command.chmod(0o755)
    done = start_example("your-github", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")
    assert done.returncode != 0
    assert "Couldn't read GitHub through gh" in done.stderr
