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
| **Router:** resident, talks to people, never executes | A `router` profile running the gateway. `post_gateway_admission` takes every admitted message, `pre_llm_call` hands the model admitted trigger ids, the model calls `superagent_dispatch`. `pre_tool_call` blocks every tool outside a short allowlist. Remove `terminal` and `file` from the profile too with `hermes -p router tools`: a plugin can't set that, and the hook leaves their schemas visible | yes |
| **Workers:** isolated, one task each, report only to the router | One OS process per ticket: `hermes -p worker chat -Q --query-file <brief> -t <toolsets>`. The profile gives it its own credentials and tools. `superagent_report` is its only way out | yes, scripted workers in the demo |
| **Threads and pass-through** | A subscription table. An allowed person's follow-up goes straight to the subscribed worker in its next report result, with no router model call (`{"action": "handled"}`) | yes |
| **Trigger fast path** | `gate()`: pure code over source, sender and bot lists from `superagent.toml`. It never reads the message text | yes |
| **Trigger slow path** | `ctx.llm.complete_structured` with no tools, one batch per tick, team rules written in prose. A rule starts in `record` mode and only logs what it would have done | yes, tested with a fake LLM |
| **Sources** | Chat through the gateway, anything else through `hermes superagent ingest` | chat and CLI, webhooks next |
| **Multi-channel output** | The router relays through a configured command such as `hermes send --to {channel} {text}`, capped at 100 words | yes |
| **Watchdog:** external, scheduled | A separate process: `hermes superagent tick` from any scheduler, or the `watchdog` automation blueprint in a `watchdog` profile. Code checks for a dead process, silence, identical reports and repeated tool calls. `ctx.llm` judges goal drift. Repairs go nudge, restart, escalate, within a budget | yes |
| **Bring your own machine** | `python -m superagent report` lets an agent outside Hermes report like any worker | report command yes, tmux backend next |

### Primitives we chose not to build on yet

- **`delegate_task`:** from a plugin it needs a CLI parent agent, so it fails under the gateway, and in-process subagents are lost on restart. A worker may still use it internally.
- **`ctx.subagent_lifecycle`:** needs an active turn and lives in-process, so it can't outlive the router.
- **Kanban:** the best fit for durable worker state, with claims, heartbeats and crash detection. Plain processes came first because they make fencing and least privilege explicit and need no dispatcher. A `KanbanBackend` behind the same `WorkerBackend` protocol is the next step, and the watchdog would then observe `on_kanban_*` hooks.

## What the MVP includes

Router dispatch, one worker per ticket, status back to the router, a gate with record-only rules, thread pass-through, the external watchdog, the guard hooks and the demo.

## What it leaves out

- Kanban, tmux and cmux worker backends, and reading a worker's screen as progress evidence.
- GitHub, email and calendar sources, and the event ledger with its digest and morning sweep.
- Speculative execution, and waking the router model when a worker finishes (`ctx.inject_message`). Relay is code for now.
- Memory updates from worker conclusions, and a desktop panel.
- A watchdog that edits prompts or rules at runtime. DeepSeek's can. Ours can't, on purpose: see below.

The reference deployment this generalises is a small server running the router on a chat platform, coding agents in terminal multiplexers on a laptop as workers, an event ledger feeding triggers, and a morning sweep.

## Threat model

Event content is attacker-controlled, and framing is only a mitigation. So every control that matters is enforced in code and none relies on the model behaving.

| Threat | Control | Residual risk |
| --- | --- | --- |
| Prompt injection through a trigger | The gate looks only at source and sender, never content. `Router.dispatch` refuses any trigger the gate did not admit, so a fooled model cannot start work. Event text is framed with a random fence | An allowed person who pastes hostile text still gets it in front of the model, and the model writes the goal |
| Router tricked into executing | `pre_tool_call` allowlist, which Hermes fails closed on error or timeout, plus operator-removed toolsets | Another plugin calling `ctx.dispatch_tool` skips the hook |
| Stranger steering a worker through a subscribed thread | Strangers and bots are dropped before workers by default. `pass_strangers` opts a source in, framed and marked as having no authority | With the opt-in, a worker still reads hostile text |
| Worker compromised by what it reads | One profile per role, scoped toolsets, an env allowlist so no secrets or YOLO settings are inherited, no outbound tools. Run the worker profile without gateway credentials | A worker with `terminal` can do what its profile's credentials allow. Use a sandboxed terminal backend and scoped tokens |
| Forged or late reports | Per-assignment token, hashed at rest, read from the env by the tool so it is not in the prompt. A restart ends the old assignment and its reports are refused | A worker can read its own env, but the token only covers its own assignment |
| Worker report steering the router | Relay is code and the router model is not in that path. The drift judge sees reports framed as data | The text reaches people verbatim, capped at 100 words |
| Watchdog as a high-value target | The judge has no tools and can only return `on_track` or `drifting`, so a fooled judge costs one bounded repair. It cannot widen a mandate or edit rules. A broken judge never triggers a repair | A hostile report can cause a spurious nudge or restart, up to the budget |
| Credentials | The plugin holds none. Model calls use `ctx.llm`, outbound uses the configured command | Depends on the operator's profile setup |
| Attention abuse | Quiet by default, record-only proactive rules, progress never reaches people | Tuning rules stays the team's job |

## Known limits

Single host: the store is SQLite and `stop` works by local process group, with a start-time check against PID reuse. A failed send retries on the next tick, so a crash between send and mark can deliver a message twice. A restarted worker starts from its brief and doesn't resume its session.

## How this differs from the catalog

Existing plugins attach coordination to kanban cards, cron or a DAG, stop at allowlists for triggers, and check liveness, loops or proof exits. The closest are `crew`, `nerve`, `agora`, `diaktoros` and `hermes-monitor`. None of them keeps a resident router that never executes, gates triggers both deterministically and semantically, or judges workers against their original goal from outside. DeepSeek's public prototype has no watchdog either, so that part is new work.
