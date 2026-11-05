# hermes-superagent design

A Hermes Agent plugin that brings DeepSeek's Super-agent design to Hermes: one agent to talk to, many workers behind it, and something external that notices when a worker stops making progress.

Sources: DeepSeek's article [Exploring Super-agent Design](https://ghost.p2lab.com/exploring-super-agent-design/) (unreviewed English translation) and their MIT research prototype [dsh-superagent-prototype](https://github.com/deepseek-ai/dsh-superagent-prototype). No code is copied from the prototype. Its principle is the one we keep: take as little human attention as possible, and give as much value as possible.

## The four properties

- **Continuous:** people talk to one agent on the channels they already use. Sessions are an implementation detail.
- **Efficient:** many tasks run in parallel, and a new request never waits for an old one.
- **Proactive:** environment events drive the system, with written rules for when to act and when to stay quiet.
- **Persistent:** it survives failures through an external control loop and durable state.

## Mapping to Hermes

Nothing here touches Hermes core or imports its internals. Everything goes through `ctx`, documented hooks and the CLI.

| DeepSeek module | Hermes primitive | In the slice |
| --- | --- | --- |
| **Router:** resident, talks to people, never executes | A `router` profile running the gateway. `pre_gateway_dispatch` gates every inbound message before Hermes handles it, `pre_llm_call` hands the model admitted trigger ids, the model calls `superagent_dispatch`, `superagent_forward` or, when the person who asked wants it stopped, `superagent_cancel`. `pre_tool_call` blocks every tool outside a short allowlist. Remove `terminal` and `file` from the profile too with `hermes -p router tools`: a plugin can't set that, and the hook leaves their schemas visible | yes |
| **Workers:** isolated, one task each, report only to the router | One session per ticket: `hermes -p worker chat -Q --query-file <brief> -t <toolsets>`, run from the worker's own directory. The turn runs in the Hermes gateway, so the plugin knows which worker it serves from `ctx.session_cwd()`, and the role comes from profile config. The profile gives it its own credentials, tools and sandboxed terminal backend. `send_message` and cron are blocked and the documented toolsets add no other messaging tool, so `superagent_report` is its route to the router and to people, though a terminal can reach whatever its sandbox allows. `blocked` parks the ticket until a person answers, then a new worker resumes with the answer | yes, scripted workers in the demo |
| **Threads and pass-through** | A subscription table, for real threads only. An allowed person's follow-up goes straight to the subscribed worker in its next report result, with no router model call (`{"action": "skip"}`). Outside a thread the message reaches the router, which can pass it on with `superagent_forward` | yes |
| **Trigger fast path** | `gate()`: pure code over source, sender and bot lists from `superagent.toml`. It never reads the message text | yes |
| **Trigger slow path** | `ctx.llm.complete_structured` with no tools, one batch per tick, team rules written in prose. A rule starts in `record` mode and only logs what it would have done | yes, tested with a fake LLM |
| **Sources** | Chat through the gateway, GitHub and other webhooks through Hermes's webhook platform, anything else through `hermes superagent ingest`. A webhook's author comes from the payload and must be the same person who triggered the event, which Hermes checks against the route's secret. Its issue or pull request becomes a thread scoped to that route, and work starts in code unless the source sets `via_router` | yes |
| **Multi-channel output** | The router relays through a configured command such as `hermes send --to {channel} {text}`, capped at 100 words | yes |
| **Watchdog:** external, scheduled | A separate process: `hermes superagent tick` from any scheduler, or the `watchdog` automation blueprint in a `watchdog` profile. Code checks for a dead process, silence, identical reports, repeated tool calls and open tickets with no worker, and stops processes that outlive their assignment. `ctx.llm` judges goal drift. Repairs go nudge, restart, escalate, within a budget | yes |
| **Bring your own machine** | `python -m superagent report` lets an agent outside Hermes report like any worker | report command yes, tmux backend next |

### Primitives we chose not to build on yet

- **`delegate_task`:** from a plugin it needs a CLI parent agent, so it fails under the gateway, and in-process subagents are lost on restart. A worker may still use it internally.
- **`ctx.subagent_lifecycle`:** needs an active turn and lives in-process, so it can't outlive the router.
- **Kanban:** the best fit for durable worker state, with claims, heartbeats and crash detection. Plain processes came first because they make fencing and least privilege explicit and need no dispatcher. A `KanbanBackend` behind the same `WorkerBackend` protocol is the next step, and the watchdog would then observe `on_kanban_*` hooks.

## What the MVP includes

Router dispatch, one worker per ticket, status back to the router, a gate with record-only rules, thread pass-through, the external watchdog, the guard hooks and the demo.

## What it leaves out

- Kanban, tmux and cmux worker backends, and reading a worker's screen as progress evidence.
- Email and calendar sources, and a full event ledger. The digest covers results, waiting questions, escalations and record-mode rule decisions, not every event.
- Speculative execution, and waking the router model when a worker finishes (`ctx.inject_message`). Relay is code for now.
- Memory updates from worker conclusions, and a desktop panel.
- A watchdog that edits prompts or rules at runtime. DeepSeek's can. Ours can't, on purpose: see below.

The reference deployment this generalises is a small server running the router on a chat platform, coding agents in terminal multiplexers on a laptop as workers, an event ledger feeding triggers, and a morning sweep.

## Threat model

Event content is attacker-controlled, and framing is only a mitigation. So who can start or steer work is enforced in code. Framing is a second layer that depends on the model, and an `act` rule lets a model reading event text open a ticket.

| Threat | Control | Residual risk |
| --- | --- | --- |
| Prompt injection through a trigger | The gate looks only at source and sender, never content, and runs in `pre_gateway_dispatch`, before a stranger's text can reach a running turn. `Router.dispatch` and `forward` only accept a trigger the gate admitted, that was offered to the current turn within the hour and that the current rules still allow. Each trigger works once, and `forward` stays within the same conversation or the same person on the same platform, so a fooled model cannot start work or steer someone else's task. Event text is framed with a random fence | An allowed person who pastes hostile text still gets it in front of the model, and the model writes the goal |
| Router tricked into executing | `pre_tool_call` allowlist, which Hermes fails closed on error or timeout, plus operator-removed toolsets | Another plugin calling `ctx.dispatch_tool` skips the hook |
| Forged webhook | Hermes checks each route's secret, and the gate admits an event only when the comment's or issue's author is also the one who triggered it, so nobody can act on someone else's text. Threads are scoped to their source | On a route without a secret, anyone who can reach it can claim any author |
| Stranger steering a worker through a subscribed thread | Bots never reach workers, and strangers don't by default. `pass_strangers` opts a source in, framed and marked as having no authority, and a stranger can never resume a parked ticket | With the opt-in, a worker still reads hostile text |
| Worker compromised by what it reads | **The boundary is the sandbox.** On a local terminal backend a worker runs as the router's OS user, so it could rewrite `superagent.toml` (whose commands the watchdog runs) and the database. The plugin therefore blocks `terminal`, file and code tools in a worker unless the profile uses a sandboxed backend such as docker, or the operator sets `allow_same_user` and accepts that. Workers also get an env allowlist, so no secrets or YOLO settings are inherited, and no outbound tools | This is a denylist over Hermes tools, not a full sandbox. The report command for agents outside Hermes has no such guard, so those agents need their own. A sandboxed worker can still do what its own credentials allow, so scope them |
| Forged or late reports | Per-assignment token, hashed at rest, read by the tool from a private file in the session's worker directory, so it is not in the prompt. A session anywhere else gets no tools. A worker is fenced before it is stopped, then its reports and every tool call are refused | Only holds while the worker can't reach the database, see above. A worker may be able to read its own identity file, but the token only covers its own assignment |
| Worker report steering the router or exfiltrating files | Relay is code and the router model is not in that path. `MEDIA:` tags are neutralised, since `hermes send` would otherwise upload the file a report names. The drift judge sees reports framed as data, most recent first when truncated | Report text reaches people otherwise verbatim, capped at 100 words |
| Watchdog as a high-value target | The judge has no tools and can only return `on_track` or `drifting`, so a fooled judge can only spend the repair budget and escalate. It cannot widen a mandate or edit rules. A broken judge never triggers a repair | A hostile report can cause a spurious nudge or restart, up to the budget |
| Credentials | The plugin holds none. Model calls use `ctx.llm`, outbound uses the configured command | Depends on the operator's profile setup |
| Attention abuse | Quiet by default, record-only proactive rules, progress never reaches people | Tuning rules stays the team's job |

If the gate itself fails, the message is dropped and logged rather than let through, so a person gets silence during that failure.

## Known limits

- By default Hermes runs every profile's turns in one host gateway process. Profiles scope config and credentials, not the OS process, so the plugin hooks and a sandboxed terminal are what separate a worker from the router.
- `stop` and `is_alive` track the `hermes chat` client. SIGTERM cancels the turn, but after a SIGKILL the running command finishes, and the worker's next tool call is what gets refused.
- A `terminal.cwd` in the worker profile can replace the session directory, in which case the worker gets no tools instead of the wrong identity.
- Single host, macOS and Linux only: the store is SQLite, and `stop` works by local process group, with a start-time check against PID reuse. If workers run under another OS user, `stop` can't signal them, so the watchdog escalates to a person instead of starting a second worker.
- Two narrow cases remain in `stop`. A group with no leader and under six hours old is assumed to be the worker's. If a tick dies mid-repair on a worker it can't stop, a later tick can escalate and still start a second worker.
- A failed send retries on later ticks, up to five times per message, so a crash between send and mark can deliver a message twice. Every tick with an undelivered or dropped message exits with an error.
- Candidate events are assessed 20 at a time, oldest first. A batch the model fails on three times is marked failed and skipped. Ignored events are deleted after a week.
- A restarted worker starts from its brief plus the last report and pending messages. It doesn't resume its session.
- A worker that ends its turn without a final report is parked, not restarted: its last output goes to a person as a question, so side effects aren't repeated. A worker that dies with no output at all is restarted.
- Hermes gives hooks no mention flag, so chat messages count as addressed and rely on the adapter's own mention rules. Semantic rules therefore apply to listed bots and `ingest` sources for now.
- Under `plugins.isolation: host`, Hermes skips `hermes superagent`, so use `python -m superagent` there.
- `hermes plugins validate` passes with one scanner caution: the invisible-character regex in `framing.py`, which is the sanitizer itself.
- Not yet run with a live model. CI runs the whole path on real Hermes with a fake model, in `tests/e2e`: a webhook message through the gate, the router, real workers, a tick and the relay.

## How this differs from the catalog

Existing plugins attach coordination to kanban cards, cron or a DAG, stop at allowlists for triggers, and check liveness, loops or proof exits. The closest are `crew`, `nerve`, `agora`, `diaktoros` and `hermes-monitor`. None of them keeps a resident router that never executes, gates triggers both deterministically and semantically, or judges workers against their original goal from outside. DeepSeek's public prototype has no watchdog either, so that part is new work.
