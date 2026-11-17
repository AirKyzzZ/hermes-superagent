from __future__ import annotations

import hmac
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Iterator, Sequence
from contextlib import closing, contextmanager
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path, PurePosixPath
from urllib.parse import parse_qs, quote, urlsplit
from zoneinfo import ZoneInfo

from .agents import ATTENTION_STATES, redact
from .config import DB_FILENAME, WORKDIRS_DIRNAME, Config, load_config
from .connectors import ConnectorSpec
from .ledger import Item, ItemState, item_from_row, source_state_from_row
from .panel_page import PAGE, SCRIPT
from .polling import FAILURES_BEFORE_WARNING, source_health
from .router import PROJECT_NAME_LIMIT, project_name
from .runtime import last_tick

TEXT_LIMIT = 280
BRIEF_SHOWN_BYTES = 6000
LOG_SHOWN_BYTES = 6000
_TICKET_ID = re.compile(r"tk-[0-9a-f]{8}")
_ASSIGNMENT_ID = re.compile(r"as-[0-9a-f]{8}")
_TERMINAL_ESCAPES = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
FEED_LIMIT = 20
ESCALATIONS_SHOWN_SECONDS = 86400.0
DEFAULT_PORT = 8765
TOKEN_HEADER = "X-Superagent-Token"
_CLOSED = (ItemState.DONE.value, ItemState.IGNORED.value)
_ATTENTION_STATES = {state.value for state in ATTENTION_STATES}
_ATTENTION_ORDER = ("escalation", "blocked", "session", "source", "overdue")
_ADMISSION_WORDS = {"act": "Let in", "candidate": "Held as a candidate", "ignore": "Dropped"}
_SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self'; style-src 'unsafe-inline'; connect-src 'self'; "
        "img-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Cache-Control": "no-store",
}


def snapshot(home: Path, config: Config, now: float | None = None) -> dict[str, object]:
    moment = time.time() if now is None else now
    zone = ZoneInfo(config.brief_timezone)
    local = datetime.fromtimestamp(moment, zone)
    day_start = local.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    base: dict[str, object] = {"generated_at": moment, "tick": last_tick(home), "problem": config.problem}
    path = home / DB_FILENAME
    if not path.is_file():
        return {**base, "missing": f"no {DB_FILENAME} in {home} yet, run a tick first"}
    with _read(path) as connection:
        if not _schema_ready(connection):
            return {**base, "missing": f"{DB_FILENAME} is from an older version, run a tick to update it"}
        items = [
            item_from_row(row) for row in connection.execute("SELECT * FROM items WHERE state NOT IN (?, ?)", _CLOSED)
        ]
        sources = _sources(connection, config.connectors, items, moment)
        workers = _workers(connection, config, moment)
        sessions = _sessions(config.connectors, items)
        _infer_projects(workers, sessions, items, config)
        return {
            **base,
            "flow": _flow(connection, day_start, sources),
            "attention": _attention(connection, workers, sessions, sources, items, config, local, moment),
            "now": [worker for worker in workers if worker["state"] == "running"]
            + [session for session in sessions if session["state"] in ("working", "background")],
            "workers": workers,
            "sessions": sessions,
            "ledger": _ledger(items, config, local, moment),
            "sources": sources,
            "feed": _feed(connection),
            "projects": _projects(workers, sessions, _tracked(items, config)),
        }


@contextmanager
def _read(path: Path) -> Iterator[sqlite3.Connection]:
    with closing(sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN")
        try:
            yield connection
        finally:
            connection.execute("ROLLBACK")


def _schema_ready(connection: sqlite3.Connection) -> bool:
    columns = {
        (table, row[1])
        for table in ("tickets", "items", "sources")
        for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
    }
    needed = {("tickets", "queued_at"), ("tickets", "project"), ("items", "last_briefed"), ("sources", "confirm_error")}
    return needed <= columns


def _text(value: object, limit: int = TEXT_LIMIT) -> str:
    flat = " ".join(redact(str(value or "")).split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def _count(connection: sqlite3.Connection, query: str, *args: object) -> int:
    return int(connection.execute(query, args).fetchone()[0])


def _flow(connection: sqlite3.Connection, day_start: float, sources: Sequence[dict[str, object]]) -> dict[str, object]:
    admissions = dict(
        connection.execute(
            "SELECT admission, COUNT(*) FROM triggers WHERE received_at >= ? GROUP BY admission", (day_start,)
        ).fetchall()
    )
    actions = dict(
        connection.execute(
            "SELECT action, COUNT(*) FROM watchdog_events WHERE at >= ? GROUP BY action", (day_start,)
        ).fetchall()
    )
    return {
        "sources": {"polling": len(sources), "failing": sum(1 for source in sources if source["failing"])},
        "gate": {
            "admitted": admissions.get("act", 0),
            "candidates": admissions.get("candidate", 0),
            "dropped": admissions.get("ignore", 0),
        },
        "router": {
            "dispatched": _count(connection, "SELECT COUNT(*) FROM tickets WHERE created_at >= ?", day_start),
            "forwarded": _count(
                connection,
                "SELECT COUNT(*) FROM triggers WHERE assessed_at >= ? AND assessment LIKE 'forwarded to %'",
                day_start,
            ),
        },
        "workers": {
            "running": _count(connection, "SELECT COUNT(*) FROM assignments WHERE status = 'running'"),
            "queued": _count(
                connection, "SELECT COUNT(*) FROM tickets WHERE status = 'open' AND queued_at IS NOT NULL"
            ),
            "parked": _count(connection, "SELECT COUNT(*) FROM tickets WHERE status = 'waiting'"),
        },
        "watchdog": {
            "nudges": actions.get("nudge", 0),
            "restarts": actions.get("restart", 0),
            "escalations": actions.get("escalate", 0),
        },
        "relay": {
            "sent": _count(connection, "SELECT COUNT(*) FROM outbox WHERE sent_at >= ?", day_start),
            "waiting": _count(connection, "SELECT COUNT(*) FROM outbox WHERE sent_at IS NULL AND failed_at IS NULL"),
        },
    }


def _workers(connection: sqlite3.Connection, config: Config, moment: float) -> list[dict[str, object]]:
    workers = []
    for ticket in connection.execute(
        "SELECT * FROM tickets WHERE status IN ('open', 'waiting') ORDER BY created_at"
    ).fetchall():
        assignment = connection.execute(
            "SELECT * FROM assignments WHERE ticket_id = ? ORDER BY started_at DESC LIMIT 1", (ticket["id"],)
        ).fetchone()
        report = connection.execute(
            "SELECT state, summary, question, at FROM reports WHERE ticket_id = ? ORDER BY seq DESC LIMIT 1",
            (ticket["id"],),
        ).fetchone()
        running = assignment is not None and assignment["status"] == "running"
        if ticket["status"] == "waiting":
            state = "parked"
        elif ticket["queued_at"] is not None:
            state = "queued"
        else:
            state = "running" if running else "starting"
        quiet_since = max(
            report["at"] if report else 0.0, assignment["started_at"] if assignment else ticket["created_at"]
        )
        workers.append(
            {
                "kind": "worker",
                "ticket": ticket["id"],
                "goal": _text(ticket["goal"], 160),
                "project": _text(ticket["project"], PROJECT_NAME_LIMIT),
                "source": _text(json.loads(ticket["mandate"]).get("source", ""), 80),
                "channel": _text(ticket["channel"], 80),
                "state": state,
                "attempt": assignment["attempt"] if assignment else 0,
                "since": assignment["started_at"] if running else ticket["created_at"],
                "last": _text(report["summary"]) if report else "",
                "last_at": report["at"] if report else None,
                "question": _text(report["question"]) if report and state == "parked" else "",
                "quiet_for": moment - quiet_since if running else 0.0,
                "stall_after": config.watchdog.stall_after_seconds,
            }
        )
    return workers


def item_project(item: Item) -> str:
    named = item.meta.get("repo") or item.meta.get("project") or ""
    return _text(project_name(str(named)), PROJECT_NAME_LIMIT)


def _infer_projects(
    workers: Sequence[dict[str, object]], sessions: Sequence[dict[str, object]], items: Sequence[Item], config: Config
) -> None:
    known = {str(entry["project"]) for entry in [*workers, *sessions] if entry.get("project")}
    known |= {item_project(item) for item in items} | {project_name(repo) for repo in config.brief_repos}
    names = sorted((name for name in known if len(name) >= 3), key=len, reverse=True)
    for worker in workers:
        if worker["project"]:
            continue
        goal = str(worker["goal"]).lower()
        worker["project"] = next((name for name in names if re.search(rf"\b{re.escape(name.lower())}\b", goal)), "")


def _projects(
    workers: Sequence[dict[str, object]], sessions: Sequence[dict[str, object]], items: Sequence[Item]
) -> list[dict[str, object]]:
    groups: dict[str, dict[str, object]] = {}

    def group(name: str) -> dict[str, object]:
        key = name.lower() or "~"
        if key not in groups:
            groups[key] = {"name": name or "No project", "workers": [], "sessions": [], "items": [], "last": 0.0}
        return groups[key]

    def touch(entry: dict[str, object], at: object) -> None:
        entry["last"] = max(_sortable(entry["last"]), _sortable(at))

    for worker in workers:
        entry = group(str(worker["project"]))
        _append(entry, "workers", worker["ticket"])
        touch(entry, worker["last_at"] or worker["since"])
    for session in sessions:
        entry = group(str(session["project"]))
        _append(entry, "sessions", session["handle"])
        touch(entry, session["since"])
    for item in sorted(items, key=lambda item: item.last_changed, reverse=True):
        name = item_project(item)
        if not name:
            continue
        entry = group(name)
        _append(
            entry,
            "items",
            {
                "handle": item.handle,
                "title": _text(item.title, 160),
                "url": _text(item.url, 500),
                "state": item.state.value,
                "due": item.due[:10],
                "kind": _item_kind(item),
                "since": item.last_changed,
            },
        )
        touch(entry, item.last_changed)
    waiting = {str(w["ticket"]) for w in workers if w["state"] == "parked"}
    waiting |= {str(x["handle"]) for x in sessions if x["state"] in _ATTENTION_STATES}
    for entry in groups.values():
        entry["waiting"] = sum(1 for key in ("workers", "sessions") for ref in _listed(entry[key]) if ref in waiting)
    return sorted(
        groups.values(),
        key=lambda entry: (entry["name"] == "No project", -_sortable(entry["waiting"]), -_sortable(entry["last"])),
    )


def _listed(values: object) -> list[str]:
    return [str(value) for value in values] if isinstance(values, list) else []


def _append(entry: dict[str, object], key: str, value: object) -> None:
    values = entry[key]
    if isinstance(values, list):
        values.append(value)


def _item_kind(item: Item) -> str:
    if "/pull/" in item.url or "/merge_requests/" in item.url:
        return "pr"
    return "issue" if "/issues/" in item.url else "item"


def _sessions(specs: Sequence[ConnectorSpec], items: Sequence[Item]) -> list[dict[str, object]]:
    boards = {spec.name: spec for spec in specs if spec.type == "agents"}
    sessions = []
    for item in items:
        if item.connector not in boards:
            continue
        cwd = item.meta.get("cwd", "")
        sessions.append(
            {
                "kind": "session",
                "host": item.connector,
                "handle": item.handle,
                "project": _text(PurePosixPath(cwd).name if cwd else item.title, 60),
                "branch": _text(item.meta.get("branch", ""), 80),
                "state": item.meta.get("state", ""),
                "since": item.last_changed,
                "detail": _text(item.meta.get("error", "")) if boards[item.connector].notify_detail else "",
            }
        )
    return sessions


def _sources(
    connection: sqlite3.Connection, specs: Sequence[ConnectorSpec], items: Sequence[Item], moment: float
) -> list[dict[str, object]]:
    rows = {row["connector"]: row for row in connection.execute("SELECT * FROM sources").fetchall()}
    sources = []
    for spec in specs:
        state = source_state_from_row(spec.name, rows.get(spec.name))
        warnings = [_text(warning.text) for warning in source_health(spec, state, moment)]
        sources.append(
            {
                "name": spec.name,
                "type": spec.type,
                "open": sum(1 for item in items if item.connector == spec.name),
                "last_ok": state.last_ok,
                "failures": state.failures,
                "failing": state.failures >= FAILURES_BEFORE_WARNING,
                "held": state.held_count is not None,
                "warnings": warnings,
            }
        )
    return sources


def _tracked(items: Sequence[Item], config: Config) -> list[Item]:
    hidden = {spec.name for spec in config.connectors if not spec.track or spec.type == "agents"}
    return [item for item in items if item.connector not in hidden]


def _ledger(items: Sequence[Item], config: Config, local: datetime, moment: float) -> dict[str, int]:
    tracked = _tracked(items, config)
    today = local.date().isoformat()
    return {
        "new": sum(1 for item in tracked if item.state is ItemState.NEW),
        "waiting": sum(1 for item in tracked if item.state is ItemState.WAITING),
        "due_today": sum(1 for item in tracked if item.due[:10] == today),
        "gone": sum(1 for item in tracked if item.missing_since is not None),
        "stale": sum(
            1
            for item in tracked
            if moment - item.last_changed > config.stale_after_seconds
            and (item.snoozed_until is None or item.snoozed_until < moment)
        ),
    }


def _attention(
    connection: sqlite3.Connection,
    workers: Sequence[dict[str, object]],
    sessions: Sequence[dict[str, object]],
    sources: Sequence[dict[str, object]],
    items: Sequence[Item],
    config: Config,
    local: datetime,
    moment: float,
) -> list[dict[str, object]]:
    cards: list[dict[str, object]] = [
        {
            "kind": "escalation",
            "tone": "bad",
            "title": _text(row["goal"], 160),
            "detail": "the watchdog gave up on it",
            "quote": _text(row["reason"]),
            "ticket": row["ticket_id"],
            "since": row["at"],
        }
        for row in connection.execute(
            "SELECT e.ticket_id, e.reason, e.at, t.goal FROM watchdog_events e JOIN tickets t ON t.id = e.ticket_id "
            "WHERE e.action = 'escalate' AND e.at >= ? AND NOT EXISTS (SELECT 1 FROM subscriptions s "
            "JOIN subscriptions later ON later.thread = s.thread JOIN tickets newer ON newer.id = later.ticket_id "
            "WHERE s.ticket_id = e.ticket_id AND newer.created_at > e.at) ORDER BY e.at",
            (moment - ESCALATIONS_SHOWN_SECONDS,),
        ).fetchall()
    ]
    cards += [
        {
            "kind": "blocked",
            "tone": "you",
            "title": worker["goal"],
            "detail": f"asked on {worker['channel'] or worker['source']}",
            "quote": worker["question"],
            "ticket": worker["ticket"],
            "since": worker["last_at"] or worker["since"],
        }
        for worker in workers
        if worker["state"] == "parked"
    ]
    cards += [
        {
            "kind": "session",
            "tone": "you" if session["state"] == "waiting" else "bad",
            "title": " · ".join(str(part) for part in (session["project"], session["branch"]) if part),
            "detail": f"{session['host']} · {session['state']}",
            "quote": session["detail"],
            "handle": session["handle"],
            "since": session["since"],
        }
        for session in sessions
        if session["state"] in _ATTENTION_STATES
    ]
    cards += [
        {
            "kind": "source",
            "tone": "bad" if source["failing"] else "you",
            "title": source["name"],
            "detail": f"{source['failures']} failed polls" if source["failing"] else "needs a look",
            "quote": _joined(source["warnings"]),
            "since": source["last_ok"],
        }
        for source in sources
        if source["warnings"]
    ]
    today = local.date().isoformat()
    cards += [
        {
            "kind": "overdue",
            "tone": "you",
            "title": _text(item.title, 160),
            "detail": f"{item.connector} · due {item.due[:10]}",
            "quote": "",
            "handle": item.handle,
            "url": _text(item.url, 500),
            "since": item.last_changed,
        }
        for item in _tracked(items, config)
        if item.due and item.due[:10] < today and item.state is not ItemState.NEW
    ]
    return sorted(cards, key=lambda card: (_ATTENTION_ORDER.index(str(card["kind"])), _sortable(card["since"])))


def _joined(texts: object) -> str:
    return " ".join(str(text) for text in texts)[:TEXT_LIMIT] if isinstance(texts, list) else ""


def _sortable(value: object) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _feed(connection: sqlite3.Connection) -> list[dict[str, object]]:
    limit = FEED_LIMIT
    events = [
        {
            "at": row["received_at"],
            "who": "gate",
            "text": f"{_ADMISSION_WORDS.get(row['admission'], 'Saw')} a {_text(row['source'], 80)} event",
        }
        for row in connection.execute(
            "SELECT received_at, admission, source FROM triggers ORDER BY received_at DESC LIMIT ?", (limit,)
        )
    ]
    events += [
        {"at": row["created_at"], "who": "router", "text": f"Opened {row['id']}: {_text(row['goal'], 120)}"}
        for row in connection.execute(
            "SELECT id, goal, created_at FROM tickets ORDER BY created_at DESC LIMIT ?", (limit,)
        )
    ]
    events += [
        {"at": row["at"], "who": row["ticket_id"], "text": f"{row['state'].capitalize()}: {_text(row['summary'], 160)}"}
        for row in connection.execute(
            "SELECT ticket_id, state, summary, at FROM reports WHERE state != 'progress' ORDER BY seq DESC LIMIT ?",
            (limit,),
        )
    ]
    events += [
        {
            "at": row["at"],
            "who": "watchdog",
            "text": f"{row['action'].capitalize()} {row['ticket_id']}: {_text(row['reason'], 160)}",
        }
        for row in connection.execute(
            "SELECT ticket_id, action, reason, at FROM watchdog_events WHERE action != 'none' "
            "ORDER BY seq DESC LIMIT ?",
            (limit,),
        )
    ]
    return sorted(events, key=lambda event: _sortable(event["at"]), reverse=True)[:limit]


def ticket_detail(home: Path, ticket_id: str) -> dict[str, object] | None:
    path = home / DB_FILENAME
    if not _TICKET_ID.fullmatch(ticket_id) or not path.is_file():
        return None
    with _read(path) as connection:
        if not _schema_ready(connection):
            return None
        ticket = connection.execute("SELECT * FROM tickets WHERE id = ?", (ticket_id,)).fetchone()
        if ticket is None:
            return None
        attempts = connection.execute(
            "SELECT id, attempt, status, started_at, ended_at FROM assignments WHERE ticket_id = ? ORDER BY started_at",
            (ticket_id,),
        ).fetchall()
        timeline = [{"at": ticket["created_at"], "kind": "opened", "text": _text(ticket["goal"], 2000)}]
        timeline += [
            {"at": row["delivered_at"], "kind": "to worker", "text": f"{_text(row['label'], 80)}: {_text(row['text'])}"}
            for row in connection.execute(
                "SELECT label, text, delivered_at FROM inputs WHERE ticket_id = ? ORDER BY seq", (ticket_id,)
            )
        ]
        timeline += [
            {
                "at": row["at"],
                "kind": row["state"],
                "text": _text(row["summary"], 2000) + (f"\nAsked: {_text(row['question'])}" if row["question"] else ""),
            }
            for row in connection.execute(
                "SELECT state, summary, question, at FROM reports WHERE ticket_id = ? ORDER BY seq", (ticket_id,)
            )
        ]
        timeline += [
            {"at": row["at"], "kind": f"watchdog {row['action']}", "text": _text(row["reason"])}
            for row in connection.execute(
                "SELECT action, reason, at FROM watchdog_events WHERE ticket_id = ? AND action != 'none' ORDER BY seq",
                (ticket_id,),
            )
        ]
        timeline += [
            {"at": row["sent_at"], "kind": "to people", "text": _text(row["text"], 2000)}
            for row in connection.execute(
                "SELECT text, sent_at FROM outbox WHERE ticket_id = ? ORDER BY seq", (ticket_id,)
            )
        ]
    latest = attempts[-1]["id"] if attempts else ""
    workdir = home / WORKDIRS_DIRNAME / ticket_id / latest if _ASSIGNMENT_ID.fullmatch(latest) else None
    return {
        "ticket": ticket_id,
        "goal": _text(ticket["goal"], 2000),
        "project": _text(ticket["project"], PROJECT_NAME_LIMIT),
        "status": ticket["status"],
        "channel": _text(ticket["channel"], 80),
        "attempts": [dict(row) for row in attempts],
        "timeline": sorted(timeline, key=lambda event: (event["at"] is None, _sortable(event["at"]))),
        "brief": _tail(workdir / "brief.md", BRIEF_SHOWN_BYTES) if workdir else "",
        "log": _tail(workdir / "worker.log", LOG_SHOWN_BYTES) if workdir else "",
    }


def _tail(path: Path, limit: int) -> str:
    try:
        with path.open("rb") as file:
            file.seek(max(0, path.stat().st_size - limit))
            text = file.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    return redact(_TERMINAL_ESCAPES.sub("", text)).strip()


def make_token() -> str:
    return secrets.token_urlsafe(24)


def panel_handler(home: Path, token: str) -> type[BaseHTTPRequestHandler]:
    class PanelHandler(BaseHTTPRequestHandler):
        server_version = "superagent-panel"

        def do_GET(self) -> None:
            url = urlsplit(self.path)
            port = getattr(self.server, "server_port", 0)
            if self.headers.get("Host", "") not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
                self._send(421, "text/plain", b"open the panel on 127.0.0.1 or localhost")
            elif url.path == "/":
                self._send(200, "text/html", PAGE.encode())
            elif url.path == "/panel.js":
                self._send(200, "text/javascript", SCRIPT.encode())
            elif url.path not in ("/api/snapshot", "/api/ticket"):
                self._send(404, "text/plain", b"not found")
            elif not hmac.compare_digest(self.headers.get(TOKEN_HEADER, "").encode(), token.encode()):
                self._send(403, "text/plain", b"missing or wrong token, use the URL superagent panel printed")
            elif url.path == "/api/ticket":
                self._ticket(parse_qs(url.query).get("id", [""])[0])
            else:
                self._snapshot()

        def _ticket(self, ticket_id: str) -> None:
            try:
                detail = ticket_detail(home, ticket_id)
            except Exception as error:
                self._send(500, "application/json", json.dumps({"error": f"{type(error).__name__}: {error}"}).encode())
                return
            if detail is None:
                self._send(404, "application/json", json.dumps({"error": f"no ticket {ticket_id!r}"}).encode())
                return
            self._send(200, "application/json", json.dumps(detail).encode())

        def _snapshot(self) -> None:
            try:
                body = json.dumps(snapshot(home, load_config(home)))
            except Exception as error:
                self._send(500, "application/json", json.dumps({"error": f"{type(error).__name__}: {error}"}).encode())
                return
            self._send(200, "application/json", body.encode())

        def _send(self, status: int, content_type: str, body: bytes) -> None:
            self.send_response(status)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            for name, value in _SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return PanelHandler


def serve(home: Path, port: int = DEFAULT_PORT, token: str = "") -> None:
    secret = token or make_token()
    server = ThreadingHTTPServer(("127.0.0.1", port), panel_handler(home, secret))
    print(f"superagent panel on http://127.0.0.1:{server.server_port}/#{secret}", flush=True)
    print("Open it next to your chat. Ctrl-C stops it.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
