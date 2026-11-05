# hermes-superagent

One agent to talk to, many workers behind it, and a watchdog that notices when one stops making progress. A [Hermes Agent](https://github.com/NousResearch/hermes-agent) plugin based on DeepSeek's [Super-agent design](https://ghost.p2lab.com/exploring-super-agent-design/).

Today a person can follow three to five agent sessions before attention runs out. Here sessions are an implementation detail:

- **Router:** one resident session talks to people on every channel and dispatches. It never executes anything, and that's enforced in code.
- **Workers:** isolated processes, one task each, that report only to the router.
- **Triggers:** environment events pass a deterministic gate, then team-written rules. Message content can't open the gate.
- **Watchdog:** a separate scheduled process checks each worker for real progress and goal drift, then nudges, restarts or escalates.

## Try it

No Hermes, model or network needed. The workers are scripted.

```bash
python3 demo/run_demo.py
```

It runs four scenes: a request going through to a worker and back, a prompt injection refused in code, a stuck worker restarted and fenced out, and an escalation to a person.

## Use it with Hermes

**Early build.** The plugin passes `hermes plugins doctor` and `validate` in CI against Hermes v0.22.7, but a full run with a live model and gateway hasn't been done yet. Expect rough edges in these steps.

Requires Hermes 0.22.7 or newer. Install and enable it in each profile:

```bash
hermes profile create router && hermes profile create worker && hermes profile create watchdog
hermes -p router plugins install AirKyzzZ/hermes-superagent
```

Write `superagent.toml` in a directory the router and watchdog share:

```toml
[sources.discord]
policy = "explicit_only"       # explicit_only, candidate or ignore
allow = ["YOUR_DISCORD_USER_ID"]

[worker]
argv = ["hermes", "-p", "worker", "chat", "-Q", "--query-file", "{brief}", "-t", "terminal,file,superagent"]

[outbound]
argv = ["hermes", "-p", "router", "send", "--to", "{channel}", "{text}"]

[[semantic]]
name = "silent-fix"
text = "Reproduce reported bugs quietly, speak up only with a confirmed fix."
mode = "record"                # logs what it would do until you switch it to "act"
```

Then give each profile its role in `config.yaml`:

```yaml
plugins:
  entries:
    hermes-superagent:
      settings:
        role: router           # or watchdog
        home: /path/to/shared/dir
```

Remove `terminal` and `file` from the router with `hermes -p router tools`, keep gateway credentials out of the worker profile, and schedule the watchdog:

```bash
*/5 * * * * hermes -p watchdog superagent tick
```

A plugin with no role does nothing, so installing it changes nothing until you opt in. Workers get their role from the router.

## Read more

- [DESIGN.md](DESIGN.md): how each module maps to Hermes, what's left out, and the threat model.
- [docs/hackathon.md](docs/hackathon.md): what we know about the plugin hackathon.

## Develop

```bash
uv run --group dev pytest -q
uv run --group dev ruff check .
uv run --group dev mypy
```

## Credits

The design comes from the DeepSeek Harness Team's article and their MIT-licensed [research prototype](https://github.com/deepseek-ai/dsh-superagent-prototype). This is an independent Python implementation, it copies no code from the prototype, and it isn't affiliated with DeepSeek or Nous Research.

## Licence

[MIT](LICENSE)
