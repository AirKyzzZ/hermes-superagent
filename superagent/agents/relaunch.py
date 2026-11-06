from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path

SESSIONS = Path(os.environ.get("SUPERAGENT_SESSIONS_DIR") or Path.home() / ".superagent" / "sessions")
SESSION_ID = re.compile(r"[A-Za-z0-9-]{8,80}")
TAIL_BYTES = 65536
SCREEN_LINES = 25
BLOCKERS = ("interrupted", "do you want", "esc to cancel", "enter to confirm", "(y/n)", "esc to interrupt")
PROMPT = re.compile(r"\s*[│|]?\s*[❯>]\s*[│|]?\s*")
RULE = re.compile(r"\s*[─━]{8,}\s*")
WORKING = re.compile(r"…\s*\(\d")
PANE_IDS = ("TMUX_PANE", "CMUX_SURFACE_ID")


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


def run(argv: list[str], timeout: float = 15) -> str:
    done = subprocess.run(argv, capture_output=True, text=True, timeout=timeout, check=False)
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
    prompts = [index for index, line in enumerate(lines) if PROMPT.fullmatch(line)]
    if not prompts or prompts[-1] == 0 or not RULE.fullmatch(lines[prompts[-1] - 1]):
        return "its screen shows no empty input box"
    return ""


def latest(events: list[dict[str, object]], key: str) -> object:
    return next((event[key] for event in reversed(events) if event.get(key)), None)


def pane_of(events: list[dict[str, object]]) -> dict[str, str]:
    pane = latest(events, "pane")
    return {str(key): str(value) for key, value in pane.items()} if isinstance(pane, dict) else {}


def last_time(events: list[dict[str, object]]) -> float:
    return max(float(str(event.get("t") or 0)) for event in events)


def taken_over(session: str, pane: dict[str, str], since: float) -> bool:
    mine = {key: pane[key] for key in PANE_IDS if key in pane}
    for path in SESSIONS.glob("*.jsonl"):
        if path.stem == session or path.stat().st_mtime <= since:
            continue
        other = pane_of(tail(path))
        if any(other.get(key) == value for key, value in mine.items()):
            return True
    return False


def ancestors(pid: int) -> list[int]:
    chain: list[int] = []
    while pid > 1 and len(chain) < 64:
        chain.append(pid)
        parent = subprocess.run(
            ["ps", "-o", "ppid=", "-p", str(pid)], capture_output=True, text=True, timeout=5, check=False
        ).stdout.strip()
        if not parent.isdigit():
            break
        pid = int(parent)
    return chain


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def boot_time() -> float | None:
    try:
        with open("/proc/stat") as stat:
            for line in stat:
                if line.startswith("btime "):
                    return float(line.split()[1])
    except OSError:
        pass
    try:
        found = re.search(r"sec = (\d+)", run(["sysctl", "-n", "kern.boottime"], timeout=5))
    except (OSError, RuntimeError, subprocess.SubprocessError):
        return None
    return float(found.group(1)) if found else None


def tmux_base(pane: dict[str, str], tmux: str) -> list[str]:
    socket = pane.get("TMUX", "").split(",")[0]
    return [tmux, "-S", socket] if socket else [tmux]


def nudge(session: str, events: list[dict[str, object]], args: dict[str, object]) -> dict[str, object]:
    pane, text = pane_of(events), str(args["text"])
    pid = latest(events, "pid")
    if not isinstance(pid, int) or pid <= 1 or not alive(pid):
        return refuse("its claude process isn't running")
    if taken_over(session, pane, last_time(events)):
        return refuse("another session has taken over its pane since")
    if pane.get("TMUX_PANE"):
        base, target = tmux_base(pane, str(args["tmux"])), pane["TMUX_PANE"]
        pane_pid = run([*base, "display-message", "-p", "-t", target, "#{pane_pid}"]).strip()
        if not pane_pid.isdigit() or int(pane_pid) not in ancestors(pid):
            return refuse(f"its claude process doesn't run in tmux pane {target}")
        problem = ready(run([*base, "capture-pane", "-p", "-t", target, "-S", "-40"]))
        if problem:
            return refuse(problem)
        run([*base, "send-keys", "-t", target, "-l", text])
        run([*base, "send-keys", "-t", target, "Enter"])
        return {"done": True, "detail": f"typed {text!r} in tmux pane {target}"}
    if pane.get("CMUX_SURFACE_ID") and pane.get("CMUX_WORKSPACE_ID"):
        if pane.get("CMUX_CLAUDE_PID") and pane["CMUX_CLAUDE_PID"] != str(pid):
            return refuse("its cmux surface runs another claude process")
        where = ["--workspace", pane["CMUX_WORKSPACE_ID"], "--surface", pane["CMUX_SURFACE_ID"]]
        problem = ready(run([str(args["cmux"]), "read-screen", *where, "--lines", "40"]))
        if problem:
            return refuse(problem)
        run([str(args["cmux"]), "send", *where, "--", text + "\\n"])
        return {"done": True, "detail": f"typed {text!r} in its cmux surface"}
    return refuse("it runs outside tmux and cmux")


def resume(session: str, events: list[dict[str, object]], args: dict[str, object]) -> dict[str, object]:
    pane = pane_of(events)
    pid = latest(events, "pid")
    if isinstance(pid, int) and pid > 1 and alive(pid):
        return refuse("a process with its id is still running")
    booted = boot_time()
    if booted is None or booted <= last_time(events):
        return refuse("the host hasn't restarted since it stopped, so it may have been closed on purpose")
    cwd = str(latest(events, "cwd") or "")
    if not cwd or not os.path.isdir(cwd):
        return refuse(f"its directory {cwd!r} is gone")
    command = shlex.join([str(args["claude"]), "--resume", session])
    name = f"resume-{session[:8]}"
    if pane.get("TMUX_PANE"):
        run([str(args["tmux"]), "new-session", "-d", "-s", name, "-c", cwd, command])
        return {"done": True, "detail": f"resumed in tmux session {name}"}
    if pane.get("CMUX_SURFACE_ID"):
        run([str(args["cmux"]), "new-workspace", "--name", name, "--cwd", cwd, "--command", command])
        return {"done": True, "detail": f"resumed in cmux workspace {name}"}
    return refuse("it ran outside tmux and cmux")


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
    if args.get("action") == "resume":
        return resume(session, events, args)
    return refuse("unknown action")


def main(raw: str) -> None:
    try:
        result = act(json.loads(raw))
    except Exception as error:
        result = refuse(f"{type(error).__name__}: {error}"[:300])
    print(json.dumps(result))
