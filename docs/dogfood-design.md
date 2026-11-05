# Dogfood design: one owner's daily setup

hermes-superagent becomes the owner's real daily setup. It goes into their live Hermes profile on a Linux VPS and, once it has proven itself, replaces a home-made mix of Hermes, ssh, cmux scripts and ad-hoc sweeps. That mix misses things: Todoist tasks never picked up, GitHub issues forgotten, emails dropped.

**The one requirement that outranks the others: nothing that enters a source may be silently lost.** In practice, every item any source shows us is either still visible in a daily view or was closed with a recorded reason by someone (the source, a rule, the owner). A source that stops answering must be just as visible as an item.

Everything below is generic. Any user enables only the sources and machines they have, and the plugin works with none.

## What exists and what's missing

| Need | In the plugin today | Missing |
| --- | --- | --- |
| Sources | Push only: chat through the gateway, webhooks, `superagent ingest`. Gate rules per source | Pull connectors that poll a tool and normalise what they find |
| Tracking | Tickets for work that has started, with an audit of reports | A ledger of every item, worked on or not, with a state until it's closed |
| Reconciliation | A daily digest of tickets, escalations, record-mode rules and undelivered messages | Untouched, stale and vanished items. Source health |
| Router | A strict `router` profile that never executes | A mode that's safe to enable in the owner's own profile, where every tool keeps working |
| Workers | Hermes sessions on the same host, `python -m superagent report` for outside agents | Claude Code workers, and backends on other machines: tmux or cmux over ssh |
| Channels | One channel per source (`notify`) and one digest channel | A channel per area, and splitting long messages for Discord |
| Reliability | Outbox with retries, last-good config, watchdog with budgets, silent exits parked for a person | Per-source health, snapshot sanity checks, a heartbeat |
| Setup | A README walkthrough | One install command, a guided `setup`, a `doctor` |

The store, outbox, watchdog, labels, cancel, digest and the real-Hermes CI job carry over unchanged.

## Reliability invariants

Each one gets a test before the feature that relies on it.

1. **Items are never deleted.** Every change of state writes an event row recording who, from, to and why. `superagent ledger show <item>` answers "where did it go".
2. **Vanishing never closes an item.** If a full snapshot no longer contains an open item, the item gets `missing_since` and stays open. The connector may then fetch that one item to confirm what happened, and gets back `open`, `closed` or `unknown`. An error raises instead of answering, so a token that lost access can't close anything. Only `closed` closes the item. Anything else keeps it listed as vanished until someone closes it.
3. **Every open item is shown in full, regularly.** The reconciliation is everything still open, minus three kinds of item:
   - items snoozed to a future date;
   - items whose worker is confirmed alive on this tick;
   - triaged items that changed within `stale_after`, 3 days by default.

   That's a complement, not a list of hand-picked cases. Every listed item gets its own line, never "and N more", so no open item goes unshown for longer than `stale_after`. The test checks the text that was actually delivered, not the set behind it.
4. **Sources fail loudly.** Every poll writes a run row. Each of these raises an alert at once, and appears in every brief until it recovers:
   - a source with no successful run in `max(3 × every, 1h)`;
   - three failures in a row;
   - a delta source that returns nothing new for longer than its `quiet_after`.

   A `command` source fails its run when an item lacks its `id` or a mapped path is missing. That's how a CLI upgrade that changes the output gets caught.
5. **A snapshot is complete only when it really is.** Every page is fetched and every sub-query has to succeed. When the source reports a total, the count must match it. A snapshot that shrinks from 5 or more items to zero, or loses more than half of them, still adds its new items, but its vanish marking is held back and alerted. That's what an expired token returning an empty list looks like. `superagent ledger accept <connector>` applies it after a real bulk cleanup.
6. **Polling is idempotent and resumable.** Items are upserted by `(connector, external id)`. A delta cursor moves only after its batch commits, and every delta poll overlaps the previous window. So a VPS that was down for two days catches up without losing or duplicating anything. Polls run under the existing tick lock.
7. **Owner-facing messages never expire.** Today the outbox drops a message after five failed sends. Briefs and alerts are retried with backoff until they're delivered, and "since the last brief" only moves forward once every part has been sent. The brief goes out once a day, at or after its hour, and is caught up if the box was down.
8. **A stuck tick is a failure.** Each tick has a hard deadline, and model calls get their own timeout and are skipped for that tick when they run over. A tick that finds the lock held for longer than the deadline alerts and exits non-zero, instead of exiting 0 as "skipped". The `heartbeat` command, such as a ping to an external uptime check, runs only after a tick that completed every phase. That's how a dead timer or a hung tick gets noticed from somewhere other than the box. A heartbeat is required to leave shadow mode.
9. **A bad config never stops polling, and never hides.** The last-good fallback already exists and covers connectors too. A config problem is now an alert, not just a line on stderr, and the brief lists connectors that are configured but not running.

## Items and the ledger

An item is one thing a source shows: a Todoist task, an issue or PR, a mail thread, an event, a reading.

| Field | Meaning |
| --- | --- |
| `id` | `<connector>:<external id>`, plus a short handle such as `it-7f3k` to type in Discord |
| `title`, `url`, `due` | As the source gives them |
| `upstream` | `open` or `closed` at the source, and the source's last-updated time |
| `excerpt` | At most 2000 characters, kept as untrusted data and framed whenever it's shown to a model |
| `meta` | Source-specific keys that rules match on: repo, labels, sender, account, project |
| `state`, `reason`, `area`, `owner` | Ledger state, see below |
| `first_seen`, `last_seen`, `missing_since`, `snoozed_until`, `ticket_id` | Bookkeeping |

States:

| State | Means | Who can set it |
| --- | --- | --- |
| `new` | Seen, not triaged yet | the connector |
| `triaged` | Has an area and is the owner's to do | a rule, the owner |
| `assigned` | A worker ticket is running it | the owner, or a rule on a source that allows dispatch |
| `waiting` | Needs the owner: a question, a PR to review | a worker's report |
| `done` | Closed at the source, or by the owner | the connector, the owner |
| `ignored` | Not worth tracking, always with a reason | a rule (its name is the reason), the owner, the model on sources that allow it |

A worker can never close an item. It can move an item to `waiting` at most. A closed item reopens as `new` when the source reports it open again, or when it changes after it was closed, and the reopening is listed. The model can suggest a state in any mode. It only sets `ignored`, on sources with `auto_ignore = true`, and every ignore is listed once with its reason in the next brief, so a wrong one is visible.

Tables: `items`, `item_events`, `connector_runs` and `connector_cursors` in the existing SQLite store. An item that gets assigned creates a ticket through the existing router path, so the watchdog, reports, labels and cancel all apply. When a ticket ends as `failed` or `dropped`, the item goes back to `triaged` with a note instead of disappearing.

## Connectors

A connector polls one tool and returns items. Adding one means adding a file to `superagent/connectors/`, usually under 100 lines, and an entry in a registry dict. The core doesn't change.

```python
class Connector(Protocol):
    def poll(self, cursor: str) -> Poll: ...
    def confirm(self, external_id: str) -> Upstream | None: ...

@dataclass(frozen=True)
class Poll:
    items: tuple[Item, ...]
    complete: bool
    cursor: str = ""
```

`complete=True` means the poll returned a full snapshot of what's open, such as Todoist's active tasks or open issues assigned to you. Vanish detection relies on it. Delta sources return what changed since the cursor and never mark anything vanished. `confirm` decides what closing means for each source: a completed task, a closed issue, an archived mail thread. Commands run without a shell, with a timeout and a minimal environment. Credentials come from environment variables named in the config, never from the YAML itself.

The `command` connector covers most tools with no code at all. It runs an argv, reads JSON, and maps fields with dotted paths and `{path}` templates:

```toml
[connectors.sleep]
type = "command"
every = "6h"
track = false
argv = ["ssh", "mac", "some-health-cli", "last-night", "--json"]
items = "nights"
fields = { id = "date", title = "Sleep {duration}, HRV {hrv}" }
```

`track = false` marks informational sources, like readings, calendars and quotas. Their items show in the brief and need no owner. Built-in types:

| Type | Tool | An item is | Default | PR |
| --- | --- | --- | --- | --- |
| `todoist` | Todoist API, token in an env var | an active task | tracked, snapshot | 6 |
| `github` | `gh search` | an open issue or PR assigned to you, asking for your review, or mentioning you | tracked, snapshot | 6 |
| `gitlab` | `glab api` | an open issue or MR assigned to you or asking for your review | tracked, snapshot | 6 |
| `gmail` | `gog`, one block per account | an inbox thread, closed when it's archived or trashed | tracked, snapshot | 8 |
| `gcal` | `gog` | an event in the next N days | info | 8 |
| `ics` | an ICS URL, for example an iCloud calendar shared as a link | an event | info | 8 |
| `whatsapp` | `wacli`, read only | a chat with listed contacts whose last message isn't yours, closed when you reply | tracked, delta | 9 |
| `zepp`, `anki`, `codexbar` | `command` presets wrapping the commands the old setup already runs | a daily reading | info | 9 |

Mail is polled as a full snapshot of `in:inbox`, paginated, not as a date window. A thread you snoozed or moved back to the inbox keeps its old date, so a window would miss it. Category filters such as promotions or forums are triage rules, which means every mail they drop is listed with a reason. Nothing is excluded at the query, where it would go unrecorded.

Turning on a source that already holds a backlog doesn't flood the first brief. Items older than `backfill` (14 days by default) are recorded as ignored, with the reason "before setup". The brief shows a count of them and the command that lists them.

Reading a mail or a WhatsApp message on the phone never closes the item.

Each built-in is tested against a fixture recorded from the real tool and scrubbed of personal data. The `gog`, `wacli`, Zepp, Anki and CodexBar formats will be captured on the VPS. Until then, those sources can run as plain `command` blocks.

## Triage and areas

Rules first, model second. Rules are deterministic, ordered, and the first match wins:

```toml
[[triage]]
match = { connector = "gmail-personal", from = "*@news.*" }
ignore = "newsletter"

[[triage]]
match = { connector = "github", repo = "your-org/*" }
area = "work"

[[triage]]
match = { connector = "todoist", label = "agent" }
area = "personal"
assign = "vps"
```

An item no rule matches stays `new`. It then shows up in every brief, so a gap in the rules costs attention but never an item. When `hermes superagent tick` runs through Hermes, the existing assessor gives each unmatched new item one suggestion. Suggestions are batched, capped per tick and limited by the model timeout. In shadow mode, that suggestion is the "would track, would assign, would ignore because" line.

## The brief and reconciliation

Once a day in `brief`, and per area when an area has its own channel:

- **Sources:** connectors that are failing, quiet, stale, held back or configured but not running come first. Then one line per source with its counts.
- **New, no owner:** grouped by area.
- **Held:** assigned items waiting for a pool, a host or quota, and why.
- **Waiting on you:** oldest first.
- **Stale:** open with no change for `stale_after` (3 days by default).
- **Vanished:** gone from a full snapshot without a confirmed close.
- **Workers:** tickets that ended without closing their item.
- **Ignored since the last brief:** with reasons.
- **Today:** events, readings and quota, from untracked sources.

Messages split on line boundaries under Discord's 2000-character limit, and each part carries the brief's date and its number, such as 2/3. Alerts go to `alerts` as soon as they're raised, not only in the brief.

## Channels

```toml
[channels]
brief = "discord:BRIEF_CHANNEL_ID"
alerts = "discord:ALERTS_CHANNEL_ID"

[areas.work]
channel = "discord:WORK_CHANNEL_ID"

[areas.personal]
```

The owner creates the channels. The plugin posts only to channels named in the config, through the existing `outbound` command, and refuses any other target. An area without a channel posts to `brief`.

## Workers

Workers are Claude Code sessions on the owner's Max subscription, logged in on each machine. No API key is involved.

- **Report contract, the same on every machine:** the worker appends JSON lines (`state`, `summary`, `question`) to `superagent-reports.jsonl` in its working directory. The backend reads new lines on each tick and stores them like `superagent_report` calls. A worker that exits without a final line is parked for the owner with its last output, as already happens today.
- **Backends** plug in the way connectors do. Each one is five argv templates: start, alive, stop, output and read reports.
  - `local`: a process on the VPS, the existing subprocess backend.
  - `tmux`: over ssh. `tmux new-session -d`, `has-session`, `kill-session`, `capture-pane`. Covers a Windows PC through WSL.
  - `cmux`: over ssh, built from the commands the current relay scripts use, so the owner can watch a worker on the Mac and take it over.
- **Pools:**

  ```toml
  [workers.vps]
  backend = "local"
  max = 2

  [workers.mac]
  backend = "cmux"
  host = "mac"
  max = 3
  repos = { "your-org/app" = "~/code/app" }
  ```

  An area or rule names a pool. When a pool is full, or ssh can't reach its host, the ticket waits under "Held" in every brief. It never fails the ticket and never moves it to a second machine behind the owner's back. Only a worker confirmed alive takes an item out of the reconciliation.
- **Quota gate:** when the `codexbar` source reports the Claude quota under `min_quota`, new dispatches wait and the brief says how many are held.

## Safety

- **Read only:** built-in connectors call only list, search and get commands. A test checks every preset's argv against an allowlist of read verbs. The `command` connector runs what the owner writes and says so in its docs.
- **No sending:** the only outbound path is the configured channels. Nothing replies to mail or WhatsApp, and nothing posts to issues except what a worker does in a repo it was assigned.
- **Protected repos:** for `protected_repos` globs, such as repos where the owner reviews, the brief says to open a PR and never merge or push to the default branch. Claude Code also starts with `--disallowedTools` for merge and force-push commands. That's defence in depth only, because a shell can get around a prefix rule. The real guarantee is the forge's branch protection, and `doctor` warns when a protected repo doesn't have it.
- **Scoped briefs:** one item (title, url, framed excerpt), the repo path, the rules and the report contract. No other ledger content, mail or credentials.
- **Untrusted sources never dispatch on their own:** `dispatch` is false by default for `gmail` and `whatsapp`, so a crafted email can't start a worker. The owner can still assign it.

## Where it runs

- **Shadow needs no role:** with no role set, the plugin's hooks do nothing and its tools stay hidden. Enabling it in the live profile changes nothing there. Polling, the ledger and the brief all run from the CLI tick.
- **Owner role, from live mode on:** set in Hermes's `config.yaml` (`plugins.entries.hermes-superagent.settings.role: owner`) by the install steps, never by the plugin. Its hooks stay inert, with no gate and no tool guard. Its model gets the ledger tools: list, show, close, ignore, snooze, assign. The strict `router` role stays for team setups.
- **Scheduling:** `hermes superagent tick` every 2 minutes from a systemd user timer with a `TimeoutStartSec`, or from cron. One tick polls whichever connectors are due, reconciles, runs the watchdog and relays. Polling needs no model call. Only suggestions for unmatched new items do.
- **Setup:**
  - Install with `hermes plugins install AirKyzzZ/hermes-superagent --enable`, then run `hermes superagent setup`.
  - Setup finds `gh`, `glab`, `gog`, `wacli`, `tmux`, `claude` and ssh hosts, and asks which to enable. It writes `superagent.toml` in shadow mode, and writes the timer unit with an explicit `PATH`, `HOME` and environment file.
  - `--yes --enable github,todoist` does the same without prompts.
  - `hermes superagent doctor` polls every connector once without writing anything, and prints each count or error. It runs under the timer's own unit, because `gh` or `gog` can work from a shell and still fail under the timer's minimal environment.
- **Config:** stays `superagent.toml`, with new `[connectors.<name>]`, `[[triage]]`, `[channels]`, `[areas.<name>]` and `[workers.<name>]` tables. YAML was the ask. But Hermes parses its config with ruamel.yaml, the plugin can't declare a parser without Hermes rebuilding its shared environment on enable, and TOML is in the standard library. The unknown-key refusal and the last-good fallback carry over.

## Rollout

The current setup never breaks. The plugin never edits Hermes config, the old cron jobs or the relay scripts, and disabling it returns the owner to exactly what they had before.

1. **Shadow:**
   - Installed in the live profile with no role and `mode = "shadow"`. It polls, builds the ledger and posts a daily "what I would track and assign" brief to its own channel. Nothing else.
   - The old setup keeps running. Hermes compares the brief and `hermes superagent ledger list --json` with what its sweeps caught. Each item the old setup missed gets noted as a win.
   - Exit: 7 days with the heartbeat green, no unexplained source failure, nothing the old setup tracked missing from the ledger, and noise the owner accepts.
2. **Take over, one source at a time, in this order:** Todoist, GitHub, GitLab, then mail.
   - `mode = "live"` per connector: new items post to their area, the owner acts through the ledger tools, and the old sweep for that source is switched off.
   - Exit per source: a week with nothing missed.
3. **Dispatch:** items go to workers.
   - Order: the VPS first, then cmux on the Mac, then tmux on the PC, replacing the relay scripts one machine at a time.

## PR plan

Each PR is small, ships with tests and keeps the plugin installable and catalog-ready. Shadow mode is complete after PR 10.

1. Inert install: CI proves a profile behaves the same with the plugin enabled and no role set. No tool is blocked, no message dropped and no tool added.
2. Delivery you can trust: owner-facing messages never expire, a tick deadline, a stuck-tick alert, a heartbeat after full ticks, config problems as alerts. Invariants 7 to 9.
3. Ledger: tables, states, events, `ledger list/show/close/ignore/snooze/accept` CLI. Invariants 1 and 2.
4. Connector framework, `command` connector, polling in the tick, source health, snapshot completeness, quiet watermarks. Invariants 4 to 6.
5. Triage rules, areas, backfill, and model suggestions under a timeout.
6. Brief and reconciliation, area channels, message splitting, alerts, shadow mode. Invariant 3, tested on the delivered text.
7. `todoist`, `github`, `gitlab`, each paginated to the end.
8. `gmail` (several accounts, inbox snapshot), `gcal`, `ics`.
9. `whatsapp` and the reading presets, from fixtures captured on the VPS.
10. `setup`, `doctor` and `docs/dogfood-install.md` with the exact VPS steps. Catalog disclosure updated for connectors.
11. `owner` role, ledger tools, per-source live mode.
12. Claude Code workers: the report file contract on the local backend.
13. ssh backends (tmux, cmux), pools, held items, protected repos, quota gate.

## Open questions for the owner

1. From live mode on, use the `owner` role in the default profile, so it keeps all its tools, rather than turning that profile into a strict router? Recommended.
2. Todoist: an API token in an environment variable, or a CLI already on the VPS?
3. Mail: the whole inbox per account, with promotions, social, updates and forums ignored by visible rules, closing a thread when it's archived? That only works if the inbox is kept small. If it isn't, which label or query should mean "track this"?
4. Config stays TOML (see "Where it runs"). Is that fine, or is YAML worth depending on the parser that ships inside Hermes?
5. From the VPS, through Hermes: `--help` and one scrubbed JSON sample for `gog` (mail and calendar), `wacli`, and the Zepp, Anki and CodexBar commands, plus the cmux commands the relay scripts run.

## Not doing

No writes to any source. No auto-reply. No model deciding that an item is done. No second scheduler: everything rides the existing tick. No changes to Hermes core.
