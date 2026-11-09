from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from showcase import GREEN, RED, finished, line, scene, scheduled_tick, superagent_home, title, wait_for  # noqa: E402

from superagent.config import WORKDIRS_DIRNAME  # noqa: E402
from superagent.domain import Assignment, TicketStatus, Trigger  # noqa: E402
from superagent.runtime import Runtime, open_runtime  # noqa: E402

CHANNEL = "chat:you"
TICK_SECONDS = 0.25
SCENE_TIMEOUT_SECONDS = 30


@dataclass(frozen=True)
class Failure:
    story: str
    short: str
    goal: str


@dataclass
class Watched:
    repairs: list[str] = field(default_factory=list)
    caught_by: str = ""


FAILURES = (
    Failure("It says it's almost done, forever", "says almost done, forever", "reconcile the October invoices"),
    Failure("It goes quiet, but the process is alive", "quiet but alive", "clean up the old feature flags"),
    Failure("It crashes without a word", "crashes", "resize the product images"),
    Failure("It runs the same failing command over and over", "same command, on a loop", "fix the failing e2e suite"),
    Failure(
        "It's still stuck after a restart, so the budget runs out", "stuck again", "untangle the billing migration"
    ),
)


def start(runtime: Runtime, number: int, goal: str) -> tuple[str, Assignment]:
    trigger = Trigger(f"m{number}", "chat", f"chat:you:{number}", "you", goal, CHANNEL, True)
    ticket_id = runtime.router.ingest(trigger).ticket_ids[0]
    line("you", goal)
    return ticket_id, next(a for a in runtime.store.running_assignments() if a.ticket_id == ticket_id)


def tick_and_show(home: Path, ticket_id: str, watched: Watched) -> None:
    result = scheduled_tick(home)
    for check in result["checks"]:
        if check["ticket"] == ticket_id and check["action"] != "none":
            line("watchdog", f"{check['verdict']} -> {check['action']} ({check['reason']})", RED)
            watched.repairs.append(check["action"])
            watched.caught_by = watched.caught_by or check["reason"]
    for message in result["delivered"]:
        line("relay", message["text"], GREEN)


def watch(runtime: Runtime, home: Path, ticket_id: str) -> Watched:
    watched = Watched()
    deadline = time.monotonic() + SCENE_TIMEOUT_SECONDS
    while runtime.store.get_ticket(ticket_id).status is TicketStatus.OPEN:
        if time.monotonic() > deadline:
            raise TimeoutError(f"example timed out watching {ticket_id}")
        tick_and_show(home, ticket_id, watched)
        time.sleep(TICK_SECONDS)
    tick_and_show(home, ticket_id, watched)
    return watched


def show_fence(home: Path, assignment: Assignment) -> None:
    log = home / WORKDIRS_DIRNAME / assignment.ticket_id / assignment.id / "worker.log"
    wait_for(lambda: log.is_file() and "late report refused" in log.read_text(), "the old worker's late report")
    line("old agent", 'on its way out it claimed "all done, trust me"')
    line("store", log.read_text().strip().splitlines()[-1], GREEN)


def show_outcomes(outcomes: list[tuple[Failure, Watched]]) -> None:
    scene("What the watchdog did, with no model involved")
    print(f"  {'failure':<28}{'caught by':<48}{'repairs':<26}result")
    for failure, watched in outcomes:
        result = "a person was told" if watched.repairs[-1] == "escalate" else "done on attempt 2"
        print(f"  {failure.short:<28}{watched.caught_by:<48}{', '.join(watched.repairs):<26}{result}")


def main() -> None:
    title("watchdog", "no credentials, no model, no network: real watchdog and workers, scripted agents")
    outcomes: list[tuple[Failure, Watched]] = []
    with superagent_home(HERE / "superagent.toml.template") as home:
        runtime = open_runtime(home)
        for number, failure in enumerate(FAILURES, 1):
            scene(f"{number}. {failure.story}")
            ticket_id, first_attempt = start(runtime, number, failure.goal)
            outcomes.append((failure, watch(runtime, home, ticket_id)))
            if number == 1:
                show_fence(home, first_attempt)
        show_outcomes(outcomes)
    finished("Every stuck worker was caught from the outside.")


if __name__ == "__main__":
    main()
