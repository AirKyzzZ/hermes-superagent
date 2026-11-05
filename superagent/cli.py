from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections.abc import Callable
from pathlib import Path

from .digest import build_digest, send_digest
from .domain import ReportState, Trigger
from .reporting import identity_from_workdir, submit_report
from .router import Assessor
from .runtime import open_runtime, tick
from .watchdog import DriftJudge

_WORKER_STATES = [state.value for state in ReportState]


def setup_parser(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--home", type=Path, default=None, help="directory holding superagent.toml and the database")
    commands = parser.add_subparsers(dest="superagent_command", required=True)
    commands.add_parser("tick", help="assess candidates, check every worker, relay messages to people")
    commands.add_parser("status", help="list open tickets")
    ingest = commands.add_parser("ingest", help="feed one environment event through the gate")
    ingest.add_argument("--source", required=True)
    ingest.add_argument("--thread", required=True)
    ingest.add_argument("--actor", required=True)
    ingest.add_argument("--text", required=True)
    ingest.add_argument("--id", default=None)
    ingest.add_argument("--channel", default="")
    ingest.add_argument("--addressed", action="store_true")
    ingest.add_argument("--bot", action="store_true")
    digest = commands.add_parser(
        "digest", help="summarise recent work, waiting questions and what record-mode rules would do"
    )
    digest.add_argument("--hours", type=float, default=24.0)
    digest.add_argument("--send", action="store_true", help="queue it for the [digest] channel, sent on the next tick")
    cancel = commands.add_parser("cancel", help="stop a ticket and its worker, and tell the people on it")
    cancel.add_argument("ticket")
    cancel.add_argument("--by", default="an operator")
    report = commands.add_parser("report", help="report status from a worker directory, for agents outside Hermes")
    report.add_argument("--state", required=True, choices=_WORKER_STATES)
    report.add_argument("--summary", required=True)
    report.add_argument("--question", default="")


def run(
    args: argparse.Namespace,
    *,
    default_home: Callable[[], Path] | None = None,
    judge: DriftJudge | None = None,
    assessor: Assessor | None = None,
) -> int:
    if args.superagent_command == "report":
        print(
            json.dumps(
                submit_report(identity_from_workdir(Path.cwd()), ReportState(args.state), args.summary, args.question)
            )
        )
        return 0
    home = args.home or (default_home() if default_home else None)
    if home is None:
        print("--home is required outside Hermes", file=sys.stderr)
        return 2
    runtime = open_runtime(home)
    if args.superagent_command == "digest":
        text = build_digest(runtime.store, time.time() - args.hours * 3600, args.hours)
        if args.send:
            send_digest(runtime.store, runtime.config.digest_channel, text)
        print(text)
        return 0
    if args.superagent_command == "cancel":
        ticket = runtime.router.cancel(args.ticket, args.by)
        print(json.dumps({"ticket": ticket.id, "status": ticket.status.value}))
        return 0
    if args.superagent_command == "status":
        print(
            json.dumps({"tickets": runtime.router.status(), "undelivered": runtime.store.undelivered_count()}, indent=1)
        )
        return 0
    if args.superagent_command == "ingest":
        trigger = Trigger(
            id=args.id or f"cli-{uuid.uuid4().hex[:12]}",
            source=args.source,
            thread=args.thread,
            actor=args.actor,
            text=args.text,
            channel=args.channel or args.thread,
            addressed=args.addressed,
            is_bot=args.bot,
        )
        disposition = runtime.router.ingest(trigger)
        print(
            json.dumps(
                {
                    "trigger": trigger.id,
                    "outcome": disposition.outcome.value,
                    "reason": disposition.reason,
                    "tickets": list(disposition.ticket_ids),
                }
            )
        )
        return 0
    result = tick(runtime, judge, assessor)
    print(
        json.dumps(
            {
                "new_tickets": [ticket.id for ticket in result.new_tickets],
                "checks": [
                    {
                        "ticket": check.ticket_id,
                        "assignment": check.assignment_id,
                        "verdict": check.verdict.value,
                        "action": check.action.value,
                        "reason": check.reason,
                    }
                    for check in result.checks
                ],
                "delivered": [{"channel": channel, "text": text} for _, channel, text in result.delivered],
                "errors": list(result.errors),
                "skipped": result.skipped,
            }
        )
    )
    return 1 if result.errors else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="superagent")
    setup_parser(parser)
    return run(parser.parse_args(argv))
