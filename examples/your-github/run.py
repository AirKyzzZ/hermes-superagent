from __future__ import annotations

import shutil
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from showcase import DIM, RESET, line, scene, superagent_home, title  # noqa: E402

from superagent.ledger import Item  # noqa: E402
from superagent.runtime import open_runtime, tick  # noqa: E402

SHOWN = 10
NEXT_STEP = "To get this every morning, with what changed since yesterday: Install in the README."


def days_waiting(item: Item, now: datetime) -> int:
    return (now - datetime.fromisoformat(item.updated.replace("Z", "+00:00"))).days


def show_counts(items: list[Item]) -> None:
    scene("Open right now")
    matched = Counter(name for item in items for name in item.meta["matched"].split(","))
    for name in ("assigned", "review", "mentioned"):
        line(name, str(matched[name]))
    line("total", f"{len(items)} across {len({item.meta['repo'] for item in items})} repositories")


def show_oldest(items: list[Item]) -> None:
    now = datetime.now(UTC)
    oldest = sorted(items, key=lambda item: item.updated)[:SHOWN]
    scene(f"The {len(oldest)} that have gone longest without an update")
    for item in oldest:
        print(f"  {days_waiting(item, now):>5}d  {DIM}{item.meta['kind']:<6}{RESET}{item.title[:110]}")


def main() -> None:
    if shutil.which("gh") is None:
        sys.exit("This one reads your GitHub through the gh CLI. Install it from https://cli.github.com and log in.")
    title("your github", "read-only through your gh login, nothing is stored or sent")
    with superagent_home(HERE / "superagent.toml.template") as home:
        runtime = open_runtime(home)
        tick(runtime)
        problem = runtime.ledger.source("github").last_error
        if problem:
            sys.exit(f"Couldn't read GitHub through gh: {problem}\nCheck your login with: gh auth status")
        items = runtime.ledger.items(open_only=True, connector="github")
    if not items:
        print("\nNothing open is assigned to you, waiting for your review, or mentioning you.")
        return
    show_counts(items)
    show_oldest(items)
    print(f"\n{NEXT_STEP}")


if __name__ == "__main__":
    main()
