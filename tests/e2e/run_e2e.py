from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from superagent.domain import Trigger  # noqa: E402
from superagent.runtime import Runtime, open_runtime  # noqa: E402

PORT = 18999
WORKER_ARGV = ["hermes", "-p", "worker", "chat", "-Q", "--query-file", "{brief}", "-t", "terminal,file,superagent"]
TURN_TIMEOUT = 240.0
failures: list[str] = []


def check(name: str, ok: bool, detail: object = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail and not ok else ""), flush=True)
    if not ok:
        failures.append(name)


def wait_until(condition: Callable[[], bool], timeout: float = TURN_TIMEOUT) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.5)
    return condition()


def hermes(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["hermes", *args], stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False)


def write_home(home: Path, *, allow_same_user: bool) -> None:
    home.mkdir(parents=True, exist_ok=True)
    argv = json.dumps(WORKER_ARGV)
    (home / "superagent.toml").write_text(
        '[sources.chat]\npolicy = "explicit_only"\nallow = ["alice"]\n\n'
        f"[worker]\nargv = {argv}\nallow_same_user = {str(allow_same_user).lower()}\n",
        encoding="utf-8",
    )


def set_up_worker_profile(hermes_home: Path, home: Path) -> None:
    created = hermes("profile", "create", "worker")
    print("profile create:", created.returncode, created.stdout[-300:], created.stderr[-300:], flush=True)
    profile = hermes_home / "profiles" / "worker"
    target = profile / "plugins" / "hermes-superagent"
    shutil.copytree(REPO, target, ignore=shutil.ignore_patterns(".git", "__pycache__", "assets", ".venv"))
    enabled = hermes("-p", "worker", "plugins", "enable", "hermes-superagent")
    print("plugins enable:", enabled.returncode, enabled.stdout[-300:], enabled.stderr[-300:], flush=True)
    configure_worker(profile, home)


def configure_worker(profile: Path, home: Path) -> None:
    config_path = profile / "config.yaml"
    config: dict[str, Any] = yaml.safe_load(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    config = config or {}
    config["model"] = {
        "provider": "custom",
        "base_url": f"http://127.0.0.1:{PORT}/v1",
        "default": "fake",
        "api_key": "not-a-real-key",
    }
    plugins = config.setdefault("plugins", {})
    plugins.setdefault("entries", {})["hermes-superagent"] = {"settings": {"role": "worker", "home": str(home)}}
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")


def start_task(runtime: Runtime, goal: str) -> str:
    trigger = Trigger(
        id=f"e2e-{uuid.uuid4().hex[:8]}",
        source="chat",
        thread="",
        actor="alice",
        text=goal,
        channel="chat:e2e",
        addressed=True,
    )
    return runtime.router.ingest(trigger).ticket_ids[0]


def model_log(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def turns(path: Path, goal_id: str) -> list[dict[str, Any]]:
    return [entry for entry in model_log(path) if entry.get("id") == goal_id]


def report_states(runtime: Runtime, ticket: str) -> list[str]:
    return [report.state.value for report in runtime.store.ticket_reports(ticket)]


def scenario_overlap(runtime: Runtime, log: Path) -> None:
    tickets = {goal_id: start_task(runtime, f"scenario=report id={goal_id}") for goal_id in ("alpha", "beta")}
    for goal_id, ticket in tickets.items():
        wait_until(lambda t=ticket: "ready" in report_states(runtime, t))
        check(f"{goal_id}: sees superagent_report", any("superagent_report" in e["tools"] for e in turns(log, goal_id)))
        check(f"{goal_id}: finishes with a stored ready report", "ready" in report_states(runtime, ticket))
        summaries = [report.summary for report in runtime.store.ticket_reports(ticket)]
        check(f"{goal_id}: reports land on its own ticket", all(goal_id in s for s in summaries), summaries)
    check(
        "two workers run at the same time",
        all(any(e.get("overlapped") for e in turns(log, goal_id)) for goal_id in tickets),
    )


def scenario_guard(runtime: Runtime, log: Path, work: Path) -> None:
    ticket = start_task(runtime, "scenario=guard id=guarded")
    wait_until(lambda: "ready" in report_states(runtime, ticket))
    results = [result for entry in turns(log, "guarded") for result in entry["tool_results"]]
    check("guard: terminal is refused on a local backend", any("same OS user" in r for r in results), results[:2])
    check("guard: the blocked command never ran", not (work / "guard-guarded").exists())
    check("guard: the worker can still report", "ready" in report_states(runtime, ticket))


def scenario_fence(runtime: Runtime, log: Path, work: Path) -> None:
    ticket = start_task(runtime, "scenario=fence id=fenced")
    wait_until(lambda: "progress" in report_states(runtime, ticket))
    assignment = next(a for a in runtime.store.running_assignments() if a.ticket_id == ticket)
    before = len(report_states(runtime, ticket))
    runtime.store.end_assignment(assignment.id)
    (work / "release-fenced").touch()
    wait_until(lambda: len(turns(log, "fenced")) >= 4, timeout=120.0)
    check("fence: a late report is not stored", len(report_states(runtime, ticket)) == before)
    check("fence: a late tool call never runs", not (work / "late-fenced").exists())


def scenario_stop(runtime: Runtime, log: Path) -> None:
    ticket = start_task(runtime, "scenario=stop id=stopped")
    running = wait_until(lambda: subprocess.run(["pgrep", "-f", "sleep 300"], check=False).returncode == 0)
    check("stop: the worker's command started", running, turns(log, "stopped")[-3:])
    assignment = next((a for a in runtime.store.running_assignments() if a.ticket_id == ticket), None)
    if assignment is None:
        check("stop: the assignment is still running", False)
        return
    runtime.store.end_assignment(assignment.id)
    runtime.router.backend.stop(assignment)
    gone = wait_until(lambda: subprocess.run(["pgrep", "-f", "sleep 300"], check=False).returncode != 0, timeout=60.0)
    check("stop: the worker's command is gone", gone)
    settled = len(turns(log, "stopped"))
    time.sleep(10)
    check("stop: no more model calls", len(turns(log, "stopped")) == settled)
    check("stop: the client process is gone", not runtime.router.backend.is_alive(assignment))


def scenario_host_env() -> None:
    found = subprocess.run(["pgrep", "-f", "gateway run"], capture_output=True, text=True, check=False)
    pids = found.stdout.split()
    check("a host gateway is running", bool(pids))
    for pid in pids:
        environ = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
        check(f"host gateway {pid} holds no SUPERAGENT_ variable", "SUPERAGENT_" not in environ)


def show_logs(hermes_home: Path, work: Path) -> None:
    for path in sorted(work.glob("**/worker.log")) + sorted(hermes_home.glob("**/logs/*.log")):
        text = path.read_text(encoding="utf-8", errors="replace")
        print(f"\n--- {path}\n{text[-3000:]}", flush=True)
    print("\n--- model log", flush=True)
    log = work / "model.jsonl"
    if log.exists():
        for line in log.read_text(encoding="utf-8").splitlines()[-40:]:
            print(line[:700], flush=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    work: Path = args.work
    work.mkdir(parents=True, exist_ok=True)
    hermes_home = Path(os.environ["HERMES_HOME"])
    guarded_home = work / "guarded"
    open_home = work / "open"
    write_home(guarded_home, allow_same_user=False)
    write_home(open_home, allow_same_user=True)
    set_up_worker_profile(hermes_home, guarded_home)
    log = work / "model.jsonl"
    model = subprocess.Popen(
        [
            sys.executable,
            str(Path(__file__).with_name("fake_model.py")),
            "--port",
            str(PORT),
            "--log",
            str(log),
            "--work",
            str(work),
        ]
    )
    try:
        guarded = open_runtime(guarded_home)
        scenario_overlap(guarded, log)
        scenario_guard(guarded, log, work)
        scenario_fence(guarded, log, work)
        configure_worker(hermes_home / "profiles" / "worker", open_home)
        scenario_stop(open_runtime(open_home), log)
        scenario_host_env()
    finally:
        if failures:
            show_logs(hermes_home, work)
        model.terminate()
    print(f"\n{len(failures)} failure(s)" + (": " + "; ".join(failures) if failures else ""), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
