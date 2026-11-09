> Draft for the v0.1.0 GitHub release. Nothing is tagged or published yet. Delete this line when publishing, and recheck "Not proven yet" against the state of `main`.

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

With no role set, the plugin's hooks do nothing and it offers no tools.

## What's in it

- **A router that can't execute.** A tool allowlist in code, and a sender gate that never reads message text.
- **One worker per task.** Isolated sessions that report only to the router, with follow-ups passed through and blocked tasks parked until someone answers.
- **A watchdog outside the workers.** It checks real progress and goal drift, then nudges, restarts or escalates within a budget. Old workers are fenced before they're stopped.
- **A ledger that keeps everything.** GitHub, GitLab, Todoist, Gmail, Google Calendar, WhatsApp, coding-agent quota, Anki, any tool that prints JSON, and every Claude Code session on your machines. A poll that can't prove it's complete marks nothing gone.
- **A daily brief** that lists every open item at least every few days.
- **Worker briefs** written from the ledger's context, challenged by a second model call and linted in code against untrusted text.

The full list is in the [changelog](../CHANGELOG.md).

## What it does on your machine

It runs the worker, delivery and connector commands you configure, spends tokens on your profile's model during ticks and worker briefs, and writes its database and worker directories under the home you choose. It only types into a terminal if you set `relaunch = true`. It makes no network calls of its own, sends no telemetry and holds no credentials. Details are in the [README](../README.md#what-it-does-on-your-machine).

## Not proven yet

No live model or real chat platform has driven it end to end. CI runs the whole router and worker path on real Hermes with a fake model. Gmail and WhatsApp were built on synthetic data.

## Thanks

The design comes from the DeepSeek Harness Team's article and research prototype. This is an independent implementation that copies no code from it, and it isn't affiliated with DeepSeek or Nous Research. Hermes Agent is by Nous Research.
