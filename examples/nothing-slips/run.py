from __future__ import annotations

import itertools
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from showcase import GREEN, RED, block, finished, line, scene, superagent_home, title  # noqa: E402

from superagent.ledger import Ledger  # noqa: E402
from superagent.runtime import open_runtime, tick  # noqa: E402

MONDAY_8AM = datetime(2026, 11, 9, 8, tzinfo=UTC).timestamp()
DAY_SECONDS = 86400


def use_simulated_clock(start: float) -> list[float]:
    moment = [start]
    steps = itertools.count()
    time.time = lambda: moment[0] + next(steps) / 1000
    return moment


def apply_overnight_changes(state: dict[str, Any], morning: dict[str, Any]) -> None:
    state["broken"] = morning.get("broken", [])
    for name, items in morning.get("new", {}).items():
        state["sources"][name] += items
    for name, gone in morning.get("deleted", {}).items():
        state["sources"][name] = [item for item in state["sources"][name] if item["id"] not in gone]
    for status, changes in (("done", morning.get("done", {})), ("open", morning.get("reopened", {}))):
        for name, ids in changes.items():
            for item in state["sources"][name]:
                if item["id"] in ids:
                    item["status"] = status


def show_final_count(state: dict[str, Any], ledger: Ledger) -> None:
    scene("What each list says on Thursday")
    in_source = sum(item["status"] == "open" for item in state["sources"]["todoist"])
    tracked = ledger.items(open_only=True, connector="todoist")
    lost = next(item for item in tracked if item.missing_since)
    line("todoist", f"{in_source} open tasks, and no trace of the one that was deleted")
    line("ledger", f'{len(tracked)} open. "{lost.title}" stays until its source closes it or you do', GREEN)


def main() -> None:
    fixtures = json.loads((HERE / "fixtures.json").read_text(encoding="utf-8"))
    state: dict[str, Any] = {"sources": fixtures["sources"], "broken": []}
    title("nothing slips", "no credentials, no model, no network: real ledger and brief, simulated clock and sources")
    with superagent_home(HERE / "superagent.toml.template") as home:
        clock = use_simulated_clock(MONDAY_8AM)
        for day, morning in enumerate(fixtures["mornings"]):
            apply_overnight_changes(state, morning)
            (home / "tools.json").write_text(json.dumps(state), encoding="utf-8")
            clock[0] = MONDAY_8AM + day * DAY_SECONDS + day * 60
            result = tick(open_runtime(home))
            if result.errors:
                raise RuntimeError(f"tick failed: {result.errors}")
            scene(f"{morning['day']}: {morning['story']}")
            messages = {channel: text for _, channel, text in result.delivered}
            if "chat:alerts" in messages:
                line("alert", messages["chat:alerts"], RED)
            block(messages["chat:brief"])
        show_final_count(state, Ledger(home / "superagent.db"))
    finished("Nothing slipped.")


if __name__ == "__main__":
    main()
