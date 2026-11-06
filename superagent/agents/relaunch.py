from __future__ import annotations

import json
import os
import re
import subprocess
import time
from pathlib import Path

SESSIONS = Path(os.environ.get("SUPERAGENT_SESSIONS_DIR") or Path.home() / ".superagent" / "sessions")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{7,79}")
TAIL_BYTES = 65536
SCREEN_LINES = 25
BLOCKERS = (
    "interrupted",
    "do you want",
    "esc to cancel",
    "enter to confirm",
    "(y/n)",
    "esc to interrupt",
    "-- normal --",
)
PROMPT_START = re.compile(r"\s*[│|]?\s*[❯>!#]")
PROMPT_EMPTY = re.compile(r"\s*[│|]?\s*[❯>]\s*[│|]?\s*")
RULE = re.compile(r"\s*[─━]{8,}\s*")
WORKING = re.compile(r"…\s*\(\d")
STARTED = time.time()
PANE_ID = re.compile(r"%\d+")
START_SLACK_SECONDS = 5.0
MTIME_SLACK_SECONDS = 2.0
GUARD = "#{&&:#{==:#{pane_in_mode},0},#{&&:#{==:#{pane_synchronized},0},#{==:#{pane_input_off},0}}}"


def tail(path: Path) -> list[dict[str, object]]:
    with open(path, "rb") as file:
        file.seek(max(0, path.stat().st_size - TAIL_BYTES))
        lines = file.read().splitlines()
    found = []
    for line in lines:
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            found.append(event)
    return found


def run(argv: list[str], timeout: float = 10) -> str:
    env = {**os.environ, "LC_ALL": "C"} if argv[0] == "ps" else None
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False, env=env)
    if done.returncode != 0:
        raise RuntimeError(f"{' '.join(argv[:2])} exited {done.returncode}: {done.stderr.strip()[-200:]}")
    return done.stdout


def ready(screen: str) -> str:
    lines = [line.rstrip() for line in screen.splitlines() if line.strip()][-SCREEN_LINES:]
    lowered = "\n".join(lines).lower()
    for marker in BLOCKERS:
        if marker in lowered:
            return f"its screen shows {marker!r}"
    if any(WORKING.search(line) for line in lines):
        return "its screen shows it still working"
    boxed = [
        index
        for index in range(1, len(lines) - 1)
        if RULE.fullmatch(lines[index - 1]) and RULE.fullmatch(lines[index + 1])
    ]
    starts = [index for index, line in enumerate(lines) if PROMPT_START.match(line)]
    if not boxed or not starts:
        return "its screen shows no input box"
    if starts[-1] != boxed[-1]:
        return "its input box isn't a single empty line"
    if not PROMPT_EMPTY.fullmatch(lines[boxed[-1]]):
        return "its input box isn't empty"
    return ""


def latest(events: list[dict[str, object]], key: str) -> object:
    return next((event[key] for event in reversed(events) if event.get(key)), None)


def pane_of(events: list[dict[str, object]]) -> dict[str, str]:
    pane = latest(events, "pane")
    return {str(key): str(value) for key, value in pane.items()} if isinstance(pane, dict) else {}


def last_time(events: list[dict[str, object]]) -> float:
    return max(float(str(event.get("t") or 0)) for event in events)


def first_time(events: list[dict[str, object]], pid: object) -> float:
    times = [float(str(event.get("t") or 0)) for event in events if event.get("pid") == pid]
    return min(times) if times else last_time(events)


def start_ticks(pid: int) -> str:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return ""
    return stat.rsplit(")", 1)[-1].split()[19]


def started_at(pid: int) -> float | None:
    text = run(["ps", "-o", "lstart=", "-p", str(pid)], timeout=5).strip()
    try:
        return time.mktime(time.strptime(" ".join(text.split()), "%a %b %d %H:%M:%S %Y"))
    except ValueError:
        return None


def same_process(pid: object, start: object, first: float) -> bool:
    if not isinstance(pid, int) or pid <= 1:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    if start and Path("/proc").is_dir():
        return start_ticks(pid) == str(start)
    began = started_at(pid)
    return began is not None and began <= first + START_SLACK_SECONDS


def pgid_of(pid: int) -> int:
    found = run(["ps", "-o", "pgid=", "-p", str(pid)], timeout=5).strip()
    return int(found) if found.isdigit() else -1


def in_cmux_surface(pid: int, pane: dict[str, str], cmux: str) -> bool:
    workspace = pane["CMUX_WORKSPACE_ID"]
    top = run([cmux, "--json", "--id-format", "both", "top", "--all", "--processes", "--workspace", workspace])
    try:
        data = json.loads(top)
    except ValueError:
        return False
    surfaces = [
        surface
        for window in data.get("windows", [])
        for workspace in window.get("workspaces", [])
        for each in workspace.get("panes", [])
        for surface in each.get("surfaces", [])
        if isinstance(surface, dict) and surface.get("id") == pane["CMUX_SURFACE_ID"]
    ]
    return (
        len(surfaces) == 1
        and pid in surfaces[0].get("tty_process_pids", [])
        and (pgid_of(pid) in surfaces[0].get("foreground_pgids", []))
    )


def foreground_tty(pid: int) -> str:
    fields = run(["ps", "-o", "tty=,tpgid=,pgid=", "-p", str(pid)], timeout=5).split()
    if len(fields) != 3 or fields[1] != fields[2]:
        return ""
    return fields[0]


def ancestors(pid: int) -> list[int]:
    chain: list[int] = []
    while pid > 1 and len(chain) < 64:
        chain.append(pid)
        parent = run(["ps", "-o", "ppid=", "-p", str(pid)], timeout=5).strip()
        if not parent.isdigit():
            break
        pid = int(parent)
    return chain


def shared_with_live_session(session: str, pane: dict[str, str]) -> bool:
    mine = {key: pane[key] for key in ("TMUX_PANE", "CMUX_SURFACE_ID") if key in pane}
    for path in SESSIONS.glob("*.jsonl"):
        if path.stem == session:
            continue
        events = tail(path)
        other = pane_of(events)
        if any(other.get(key) == value for key, value in mine.items()):
            pid = latest(events, "pid")
            ended = any(event.get("event") == "SessionEnd" for event in events[-3:])
            if not ended and same_process(pid, latest(events, "pid_start"), first_time(events, pid)):
                return True
    return False


def process_moved_on(session: str, pid: int, since: float) -> bool:
    for path in SESSIONS.glob("*.jsonl"):
        if path.stem == session or path.stat().st_mtime < since - MTIME_SLACK_SECONDS:
            continue
        events = tail(path)
        if latest(events, "pid") == pid and last_time(events) > since:
            return True
    return False


def out_of_time(args: dict[str, object]) -> bool:
    return time.time() - STARTED > float(str(args.get("budget", 0)))


def nudge(session: str, events: list[dict[str, object]], args: dict[str, object]) -> dict[str, object]:
    pane, text = pane_of(events), str(args["text"])
    pid = latest(events, "pid")
    if not isinstance(pid, int) or not same_process(pid, latest(events, "pid_start"), first_time(events, pid)):
        return refuse("its claude process isn't running")
    if process_moved_on(session, pid, last_time(events)):
        return refuse("its claude process has moved on to another session")
    if shared_with_live_session(session, pane):
        return refuse("another live session shares its pane")
    tty = foreground_tty(pid)
    if not tty:
        return refuse("its claude process isn't in the foreground of its terminal")
    if pane.get("TMUX_PANE"):
        socket, target = pane.get("TMUX", "").split(",")[0], pane["TMUX_PANE"]
        if not PANE_ID.fullmatch(target):
            return refuse("its tmux pane id looks wrong")
        base = [str(args["tmux"]), "-S", socket] if socket else [str(args["tmux"])]
        query = "#{pane_pid}\t#{pane_tty}\t#{pane_dead}\t" + GUARD
        fields = run([*base, "display-message", "-p", "-t", target, query]).rstrip("\n").split("\t")
        if len(fields) != 4 or not fields[0].isdigit():
            return refuse(f"tmux couldn't describe pane {target}")
        pane_pid, pane_tty, dead, clear = fields
        if dead == "1" or clear != "1":
            return refuse(f"tmux pane {target} is dead, scrolled back, synchronized or not taking input")
        if int(pane_pid) not in ancestors(pid) or not pane_tty.endswith(tty):
            return refuse(f"its claude process doesn't run in tmux pane {target}")
        problem = ready(run([*base, "capture-pane", "-p", "-t", target, "-S", "-40"]))
        if problem:
            return refuse(problem)
        if out_of_time(args):
            return refuse("it ran out of time before typing")
        send = f'send-keys -t {target} -l "{text}" ; send-keys -t {target} Enter ; display-message -p sent'
        try:
            answer = run([*base, "if-shell", "-F", "-t", target, GUARD, send, "display-message -p held"]).strip()
        except Exception as error:
            return unknown(error)
        if answer != "sent":
            return refuse(f"tmux pane {target} changed just before typing")
        return {"done": True, "detail": f"typed {text!r} in tmux pane {target}"}
    if pane.get("CMUX_SURFACE_ID") and pane.get("CMUX_WORKSPACE_ID"):
        cmux = str(args["cmux"])
        if not in_cmux_surface(pid, pane, cmux):
            return refuse("its claude process isn't the one in the foreground of its cmux surface")
        where = ["--workspace", pane["CMUX_WORKSPACE_ID"], "--surface", pane["CMUX_SURFACE_ID"]]
        problem = ready(run([cmux, "read-screen", *where, "--lines", "40"]))
        if problem:
            return refuse(problem)
        if out_of_time(args):
            return refuse("it ran out of time before typing")
        try:
            run([cmux, "send", *where, "--", text + "\\n"])
        except Exception as error:
            return unknown(error)
        return {"done": True, "detail": f"typed {text!r} in its cmux surface"}
    return refuse("it runs outside tmux and cmux")


def unknown(error: Exception) -> dict[str, object]:
    log = f"{type(error).__name__}: {error}"[:300]
    return {"done": False, "unknown": True, "detail": "it failed while typing, so it may have typed", "log": log}


def refuse(detail: str) -> dict[str, object]:
    return {"done": False, "detail": detail}


def act(args: dict[str, object]) -> dict[str, object]:
    session = str(args.get("session", ""))
    if not SESSION_ID.fullmatch(session):
        return refuse("not a session id")
    path = SESSIONS / f"{session}.jsonl"
    if not path.is_file():
        return refuse("its hook log is gone")
    events = tail(path)
    if not events or abs(last_time(events) - float(str(args.get("since")))) > 0.001:
        return refuse("it moved on since it was checked")
    if args.get("action") == "nudge":
        return nudge(session, events, args)
    return refuse("unknown action")


def main(raw: str) -> None:
    try:
        result = act(json.loads(raw))
    except Exception as error:
        result = {
            "done": False,
            "detail": "the relaunch script failed",
            "log": f"{type(error).__name__}: {error}"[:300],
        }
    print(json.dumps(result))
