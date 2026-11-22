# Changelog

Notable changes to hermes-superagent. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The panel can act: answer a parked worker, cancel a task, accept a held-back snapshot or snooze an overdue item until tomorrow, with the same token as the page. An answer reaches the worker like a message in its thread and resumes it. `superagent panel --read-only` hides the buttons and refuses every action.
- A `markdown` connector reads open `- [ ]` tasks from your notes, such as an Obsidian vault or a `TODO.md`, with no account or CLI. A task closes once it's ticked in its file, and moving lines around changes nothing.
- The panel's token is saved across restarts (`--new-token` replaces it), and `--allow-host` lets it answer behind `tailscale serve` on your tailnet.
- The panel groups work by project, with a card per project, a drawer with its blockers, workers, sessions and pull requests, and each worker's full conversation one click further. `superagent_dispatch` takes an optional `project`.
- `superagent panel` serves a live side panel on localhost: what needs you, ranked, then today's flow from sources to relay, what runs now, workers, Claude Code sessions, the ledger, sources and a feed. It reads the database read-only and needs the token from the URL it prints. `--json` prints the same snapshot.
- `[worker] max_running` caps how many workers run at once. Tasks past the cap wait in a queue, say so once, and start oldest first as workers finish, so a burst of requests doesn't start a model session each at the same moment.

### Changed

- The router's prompt is delegation-first: its context is kept for talking and deciding, anything with more than one step goes to a worker, and each goal is written as a complete brief in English. The worker brief now says who relays for whom, then lists goal, context, what done means, constraints and how to report. `[worker] on_behalf_of` sets the name both use.

### Fixed

- A config value of the wrong type, such as `argv = 1` or `pass_strangers = "false"`, is refused like any other bad edit, so the plugin keeps running on the last good config instead of crashing or reading `"false"` as true.
- Unknown keys in `[briefs]` are refused, like everywhere else.
- `briefs --compare` finds a GitLab task's checkout under its `gitlab:group/project` key, as a real brief does.
- `hermes superagent setup` sends through Hermes when Hermes was installed with its own installer, whose launcher hides its name. Before, `--alerts` was refused there and nothing was set to deliver messages.
- `doctor` says "1 minute ago", and setup's hint for the Claude Code hook speaks to you rather than to an agent.
- Stopping a worker also stops its descendants that left its process group, such as a `setsid` dev server, while their parent chain leads back to the worker, and it never reads other processes' environments. Before, they survived a stop or a restart. A process that left the group and lost its parent first, such as a double-forked daemon, still isn't found.
- A stop that can't list processes after SIGTERM, because `ps` timed out or failed, still sends SIGKILL, and still reports the worker as possibly running unless a later listing shows it gone.

## [0.1.0] - 2026-11-10

The first version, built for the Hermes Plugin Hackathon. It needs Hermes 0.22.7 or newer on macOS or Linux and has no Python dependencies of its own.

### Added

#### Router, workers and watchdog

- A resident router that dispatches and can't execute, enforced by a `pre_tool_call` allowlist that fails closed.
- One isolated worker per task, with its own session, directory and environment allowlist, reporting only through `superagent_report`.
- A sender gate that never reads message text, and team-written trigger rules that stay in `record` mode until you set `act`.
- Work started from GitHub and other webhook events, with a redelivered webhook treated as the same trigger.
- An external watchdog that checks real progress and goal drift, then nudges, restarts or escalates within a budget.
- Fenced repairs: each worker has its own token and is fenced before it's stopped, so a late report from an old worker is refused.
- Parked tasks that wait for an answer and resume with a new worker, follow-ups passed through to a running worker, and cancelling a task on request.
- An outbox that retries undelivered messages in order, alerts on lasting tick errors and stuck ticks, a heartbeat, and a digest of recent work and of what `record` rules would have done.

#### Ledger and connectors

- An item ledger that never deletes an item. Closing or ignoring one takes a reason, and every change is kept with the content it saw.
- Connectors for GitHub (`gh`), GitLab (`glab`), Todoist (`td`), Gmail and Google Calendar (`gog`), WhatsApp (`wacli`), coding-agent quota (`codexbar`), Anki (AnkiConnect) and any tool that prints JSON.
- Polls that can't prove they're complete mark nothing gone, and vanished items are checked one by one.
- An agents board of every Claude Code session on your machines, from a small hook and a read-only probe over ssh.
- Triage rules that file new items into areas.
- A daily brief that lists every open item again once `stale_after` has passed, three days by default, unless it's snoozed or its worker is running.
- Worker briefs on demand with `hermes superagent briefs`, written from the ledger's context: drafted, challenged and revised by the model, then linted in code and marked NEEDS APPROVAL when they draw on untrusted text, with a blind comparison against the briefs that ran. Workers don't get them automatically yet.
- An optional nudge for Claude Code sessions stopped by an API error or a rate limit, off unless you set `relaunch = true`.

#### Install and tooling

- `hermes superagent` commands, including `setup`, `doctor`, `tick`, `status`, `ledger`, `agents`, `brief` and `briefs`.
- A standalone `superagent` command, installable from git with `uv tool install` or `pip install`, for running the ledger without Hermes.
- A demo that runs with one command, `superagent-demo`, or `demo/run_demo.py` in a clone, and runnable examples under `examples/`. The demo and three of the examples need no Hermes, model, network or credentials.
- CI running tests, lint and types on Linux and macOS, plugin validation, a real install and an end-to-end run with a fake model on real Hermes 0.22.7 and 0.23.0, and clean-venv install checks.

### Security

- Dispatch and forward accept only a trigger the gate admitted, offered within the hour, still allowed and unused.
- Send directives in untrusted text are neutralised, including behind extra brackets.
- Report tokens are per assignment, hashed in the database and handed to each worker in a private file.
- Workers start from an environment allowlist, so they inherit no YOLO or approval-bypass setting.
- Stopping a worker checks its start time first and handles macOS zombies, other users and leaderless process groups.

### Known limits

- CI runs the router and worker path on real Hermes with a fake model, from a webhook through the gateway to a stubbed relay. [One recorded run](docs/live-run.md) on a real model covers the brief writer, a worker, the watchdog and a relay to Discord. The router's own model turn, an incoming chat message and the drift judge haven't run live, and the watchdog's nudge, restart and escalate haven't run on real Hermes at all, only in unit tests and the demo.
- Gmail, Google Calendar and WhatsApp were built on synthetic data until real samples arrive.
- Workers that are Claude Code sessions in tmux or cmux over ssh aren't implemented.

[Unreleased]: https://github.com/AirKyzzZ/hermes-superagent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/AirKyzzZ/hermes-superagent/releases/tag/v0.1.0
