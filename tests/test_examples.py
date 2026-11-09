from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run_example(name: str) -> str:
    done = subprocess.run(
        [sys.executable, str(ROOT / "examples" / name / "run.py")],
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


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
