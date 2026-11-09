import json
import subprocess
import sys
import tempfile
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent
ROOT = EXAMPLES_DIR.parent
THEME = "14142a,e6e6f5,0b0b1a,ff6b81,6ee7a8,f5d76e,6f6cff,c39bff,7fd4ff,e6e6f5"
PROMPT = "\u001b[38;5;63m$\u001b[0m "
COLUMNS = 140
MAX_ROWS = 42
EXAMPLES = ("nothing-slips", "night-shift", "watchdog")


def record(name: str, raw: Path) -> list[str]:
    command = f"{sys.executable} examples/{name}/run.py"
    subprocess.run(
        ["asciinema", "rec", "--headless", "--overwrite", "--window-size", f"{COLUMNS}x60", "-c", command, str(raw)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    rows = raw.read_text(encoding="utf-8").splitlines()[1:]
    output = "".join(event[2] for event in map(json.loads, rows) if event[1] == "o")
    return output.removesuffix("\r\n").split("\r\n")


def paced(name: str, lines: list[str]) -> list[list[object]]:
    events: list[list[object]] = [[0.6, "o", PROMPT]]
    events += [[0.045, "o", char] for char in f"python3 examples/{name}/run.py"]
    events.append([0.5, "o", "\r\n"])
    reading = 0.0
    for chunk in chunks(lines):
        delay = (1.4 if "== " in chunk[0] else 0.14) + reading
        events.append([delay, "o", "".join(line + "\r\n" for line in chunk)])
        reading = 0.09 * len(chunk) if len(chunk) > 1 else 0.0
    events.append([0.6 + reading, "o", PROMPT])
    return events


def chunks(lines: list[str]) -> list[list[str]]:
    grouped: list[list[str]] = []
    for line in lines:
        if grouped and (line.startswith("    ") or not line.strip()):
            grouped[-1].append(line)
        else:
            grouped.append([line])
    return grouped


def render(name: str) -> None:
    target = EXAMPLES_DIR / name
    with tempfile.TemporaryDirectory() as temp:
        lines = record(name, Path(temp) / "raw.cast")
    cast = target / "recording.cast"
    header = {"version": 3, "term": {"cols": COLUMNS, "rows": min(len(lines) + 3, MAX_ROWS)}}
    cast.write_text("\n".join(json.dumps(row) for row in [header, *paced(name, lines)]) + "\n", encoding="utf-8")
    subprocess.run(
        ["agg", "--theme", THEME, "--font-size", "16", "--line-height", "1.35", "--last-frame-duration", "8"]
        + [str(cast), str(target / "recording.gif")],
        check=True,
        capture_output=True,
    )


def main() -> None:
    for name in sys.argv[1:] or EXAMPLES:
        render(name)


if __name__ == "__main__":
    main()
