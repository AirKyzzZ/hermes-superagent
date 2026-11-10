# hermes-superagent 0.1.0

A Hermes Agent plugin that brings DeepSeek's Super-agent design to Hermes: one router in front of a worker per task, a watchdog that restarts the stuck ones, and a ledger that never drops an issue, task or Claude Code session.

## Try it

The demo is one command and about ten seconds. It needs [uv](https://docs.astral.sh/uv/), but no Hermes, model or credentials:

```bash
uvx --from git+https://github.com/AirKyzzZ/hermes-superagent superagent-demo
```

Run the ledger on your own sources, still without Hermes:

```bash
uv tool install git+https://github.com/AirKyzzZ/hermes-superagent
superagent --home ~/superagent setup
superagent --home ~/superagent doctor
```

Or install it into Hermes 0.22.7 or newer, on macOS or Linux:

```bash
hermes plugins install AirKyzzZ/hermes-superagent --enable
hermes superagent --home ~/superagent setup
```

With no role set, outside its own worker directories, the plugin's hooks do nothing and it offers no tools.

## What's in it

- **A router that can't execute.** A tool allowlist in code, and a sender gate that never reads message text.
- **One worker per task.** Isolated sessions that report only to the router, with follow-ups passed through and blocked tasks parked until someone answers.
- **A watchdog outside the workers.** It checks real progress and goal drift, then nudges, restarts or escalates within a budget. Old workers are fenced before they're stopped.
- **A ledger that keeps everything.** GitHub, GitLab, Todoist, Gmail, Google Calendar, WhatsApp, coding-agent quota, Anki, any tool that prints JSON, and every Claude Code session on your machines. A poll that can't prove it's complete marks nothing gone.
- **A daily brief** that lists every open item again once `stale_after` has passed, three days by default, unless it's snoozed or its worker is running.
- **Worker briefs** on demand with `hermes superagent briefs`, written from the ledger's context, challenged by a second model call and linted in code against untrusted text.

The full list is in the [changelog](https://github.com/AirKyzzZ/hermes-superagent/blob/v0.1.0/CHANGELOG.md).

## What it does on your machine

It runs the worker, delivery and connector commands you configure, spends tokens on your profile's model during ticks and worker briefs, and writes its database and worker directories under the home you choose. It only types into a terminal if you set `relaunch = true`. It opens no network connections in its own code, sends no telemetry and stores none of your accounts' credentials: connectors use your tools' own logins, and `anki` posts to AnkiConnect through `curl`. Details are in the [README](https://github.com/AirKyzzZ/hermes-superagent/blob/v0.1.0/README.md#what-it-does-on-your-machine).

## Not proven yet

CI runs the router and worker path on real Hermes with a fake model, from a webhook through the gateway to a stubbed relay. [One recorded run](https://github.com/AirKyzzZ/hermes-superagent/blob/v0.1.0/docs/live-run.md) on a real model covers the brief writer, a worker, the watchdog and a relay to Discord. The router's own model turn, an incoming chat message and the drift judge haven't run live, and the watchdog's nudge, restart and escalate haven't run on real Hermes at all, only in unit tests and the demo. Gmail, Google Calendar and WhatsApp were built on synthetic data.

## Thanks

The design comes from the DeepSeek Harness Team's article and research prototype. This is an independent implementation that copies no code from it, and it isn't affiliated with DeepSeek or Nous Research. Hermes Agent is by Nous Research.
