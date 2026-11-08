from __future__ import annotations

import os
import signal
import sys
import time
from collections.abc import Callable
from pathlib import Path
from types import FrameType

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from superagent.domain import ReportState  # noqa: E402
from superagent.reporting import (  # noqa: E402
    WorkerIdentity,
    identity_from_workdir,
    record_tool_activity,
    submit_report,
)
from superagent.store import StaleAssignmentError  # noqa: E402

Behaviour = Callable[[WorkerIdentity], None]


def finishes(result: str) -> Behaviour:
    def run(identity: WorkerIdentity) -> None:
        for step in ("reading the code", "change written", "tests pass"):
            submit_report(identity, ReportState.PROGRESS, step)
            time.sleep(0.1)
        submit_report(identity, ReportState.READY, result)

    return run


def repeats(summary: str) -> Behaviour:
    def run(identity: WorkerIdentity) -> None:
        stopping: list[int] = []

        def on_term(signum: int, frame: FrameType | None) -> None:
            stopping.append(signum)

        signal.signal(signal.SIGTERM, on_term)
        while not stopping:
            try:
                submit_report(identity, ReportState.PROGRESS, summary)
            except StaleAssignmentError:
                pass
            time.sleep(0.12)
        try:
            submit_report(identity, ReportState.READY, "all done, trust me")
        except StaleAssignmentError as error:
            print(f"late report refused: {error}", flush=True)

    return run


def goes_silent(identity: WorkerIdentity) -> None:
    submit_report(identity, ReportState.PROGRESS, "starting on the flags")
    time.sleep(600)


def crashes(identity: WorkerIdentity) -> None:
    os._exit(1)


def runs_the_same_command(identity: WorkerIdentity) -> None:
    submit_report(identity, ReportState.PROGRESS, "running the end-to-end suite")
    while True:
        try:
            record_tool_activity(identity, "terminal", {"command": "npm run e2e"}, "1 failing")
        except StaleAssignmentError:
            return
        time.sleep(0.1)


def asks(summary: str, question: str) -> Behaviour:
    def run(identity: WorkerIdentity) -> None:
        submit_report(identity, ReportState.BLOCKED, summary, question)

    return run


SCRIPT: dict[str, tuple[Behaviour, ...]] = {
    "fix the flaky cart test": (finishes("rounding bug in the cart total, pull request opened, tests pass"),),
    "add retries to the webhook sender": (finishes("backoff with a cap, pull request opened, tests pass"),),
    "bump the payments SDK to v4": (
        asks("the upgrade is ready except for one call", "v4 drops the EUR rounding helper. Inline it, or stay on v3?"),
        finishes("SDK on v4 with the helper inlined, pull request opened"),
    ),
    "port the reports cron to the queue": (
        repeats("still reading the cron config"),
        finishes("cron moved to the queue on a clean second attempt, pull request opened"),
    ),
    "speed up the search index": (crashes, finishes("index rebuilt 40% faster, pull request opened")),
    "rewrite the auth layer": (repeats("almost done with the session store"),) * 2,
    "reconcile the October invoices": (repeats("almost done"), finishes("invoices reconciled, 2 duplicates removed")),
    "clean up the old feature flags": (goes_silent, finishes("removed 14 dead flags")),
    "resize the product images": (crashes, finishes("all images resized")),
    "fix the failing e2e suite": (runs_the_same_command, finishes("selector fixed, e2e suite passes")),
    "untangle the billing migration": (repeats("nearly there"),) * 2,
}


def main() -> None:
    brief = Path(sys.argv[1]).read_text(encoding="utf-8")
    goal = brief.split("## Task\n", 1)[1].split("\n\n## Rules", 1)[0]
    identity = identity_from_workdir(Path.cwd())
    SCRIPT[goal][identity.attempt - 1](identity)


if __name__ == "__main__":
    main()
