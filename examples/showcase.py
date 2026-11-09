from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

if sys.version_info < (3, 11):  # noqa: UP036
    sys.exit(
        f"The examples need Python 3.11 or newer, this is {sys.version.split()[0]}.\n"
        "With uv installed: uv run --no-project --python 3.12 examples/<name>/run.py"
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

BOLD, DIM, BLUE, GREEN, RED, RESET = "\033[1m", "\033[2m", "\033[38;5;63m", "\033[32m", "\033[31m", "\033[0m"


def title(name: str, note: str) -> None:
    print(f"{BOLD}{name}{RESET} {DIM}({note}){RESET}")


def scene(text: str) -> None:
    print(f"\n{BOLD}{BLUE}== {text}{RESET}")


def line(who: str, text: str, color: str = "") -> None:
    print(f"  {DIM}{who:<10}{RESET}{color}{text}{RESET}")


def block(text: str, color: str = "") -> None:
    for row in text.splitlines():
        print(f"    {color}{row}{RESET}" if row else "")


def finished(text: str) -> None:
    print(f"\n{BOLD}{GREEN}{text}{RESET}")


def wait_for(condition: Callable[[], bool], what: str, timeout: float = 20) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if condition():
            return
        time.sleep(0.05)
    raise TimeoutError(f"example timed out waiting for {what}")


def superagent_cli(home: Path, *args: str) -> str:
    done = subprocess.run(
        [sys.executable, "-m", "superagent", "--home", str(home), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT)},
    )
    if done.returncode != 0:
        raise RuntimeError(f"superagent {args[0]} failed: {done.stdout} {done.stderr}")
    return done.stdout


def scheduled_tick(home: Path) -> dict[str, Any]:
    result: dict[str, Any] = json.loads(superagent_cli(home, "tick"))
    return result


@contextmanager
def superagent_home(template: Path) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="superagent-example-") as temp:
        home = Path(temp)
        config = template.read_text(encoding="utf-8")
        for name, value in (("PYTHON", sys.executable), ("HERE", str(template.parent)), ("HOME", str(home))):
            config = config.replace(f"@{name}@", value)
        (home / "superagent.toml").write_text(config, encoding="utf-8")
        yield home
