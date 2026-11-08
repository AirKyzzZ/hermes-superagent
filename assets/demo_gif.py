import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
THEME = "14142a,e6e6f5,0b0b1a,ff6b81,6ee7a8,f5d76e,6f6cff,c39bff,7fd4ff,e6e6f5"
PROMPT = "\u001b[38;5;63m$\u001b[0m "
COLUMNS = 150


def record(cast: Path) -> list[str]:
    command = f"{sys.executable} demo/run_demo.py"
    subprocess.run(
        ["asciinema", "rec", "--headless", "--overwrite", "--window-size", f"{COLUMNS}x60", "-c", command, str(cast)],
        cwd=ROOT,
        check=True,
        capture_output=True,
    )
    rows = cast.read_text(encoding="utf-8").splitlines()[1:]
    output = "".join(event[2] for event in map(json.loads, rows) if event[1] == "o")
    return output.removesuffix("\r\n").split("\r\n")


def paced(lines: list[str]) -> list[list[object]]:
    events: list[list[object]] = [[0.6, "o", PROMPT]]
    events += [[0.045, "o", char] for char in "python3 demo/run_demo.py"]
    events.append([0.5, "o", "\r\n"])
    for line in lines:
        delay = 1.1 if "== " in line or "Demo finished" in line else 0.24 if line.strip() else 0.05
        events.append([delay, "o", line + "\r\n"])
    events.append([0.6, "o", PROMPT])
    return events


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        raw, cast = Path(temp) / "raw.cast", Path(temp) / "demo.cast"
        lines = record(raw)
        header = {"version": 3, "term": {"cols": COLUMNS, "rows": len(lines) + 3}}
        cast.write_text("\n".join(json.dumps(row) for row in [header, *paced(lines)]) + "\n", encoding="utf-8")
        subprocess.run(
            ["agg", "--theme", THEME, "--font-size", "16", "--line-height", "1.35", "--last-frame-duration", "8"]
            + [str(cast), str(ROOT / "assets" / "demo.gif")],
            check=True,
            capture_output=True,
        )


if __name__ == "__main__":
    main()
