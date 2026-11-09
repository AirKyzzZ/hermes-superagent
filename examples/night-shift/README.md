# Night shift

Six coding tasks handed off at 23:00, and the one digest you read in the morning.

![Six workers overnight: two finish, one parks a question, one crash and one loop are repaired, one is escalated, then the morning digest and the sessions board](recording.gif)

```bash
python3 examples/night-shift/run.py
```

- Each task gets its own ticket and its own worker process, side by side. The router runs none of the work.
- A process outside the workers checks them on a schedule. A crash gets a restart, a loop gets a nudge and then a restart, and a task that's still stuck when the budget runs out goes to you.
- A worker that needs a decision parks its question and stops. Your one-line reply starts a new worker with the answer, with no model call in between.
- Strangers and unlisted bots get refused on who sent them, and the text is never read.
- Workers report often and you hear only the results, so the morning is one digest.
- The Claude Code sessions you left open show on the same board: waiting on a permission, stuck, rate-limited or gone. This part needs no Hermes at all on a real install, only the [hook](../../docs/dogfood-install.md).

The gate, the router, the watchdog, the worker processes, the digest, the sessions connector and its probe are the production code, and `tick` runs as the same `python -m superagent tick` you'd put on a timer. The agents are [scripted](../scripted_agent.py), the session logs are written by [`run.py`](run.py), and the ticks run back to back instead of every 2 minutes.

On a real install each worker is a Hermes session. That path runs on real Hermes with a fake model in CI, and [one recorded run](../../docs/live-run.md) took a worker through it with a real model, see the warning in the [main README](../../README.md#why).
