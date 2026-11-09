# Examples

Three things hermes-superagent does, each one command, with no Hermes, model, network or credentials. You need Python 3.11+ on macOS or Linux. If your `python3` is older, `uv run --no-project --python 3.12 examples/<name>/run.py` fetches a newer one.

| Example | The problem | Run it |
| --- | --- | --- |
| [Nothing slips](nothing-slips) | Your tools lose things without telling you | `python3 examples/nothing-slips/run.py` |
| [Night shift](night-shift) | You run more agents than you can watch | `python3 examples/night-shift/run.py` |
| [Watchdog](watchdog) | An agent can say it's working and be stuck | `python3 examples/watchdog/run.py` |

They run the real code: `tick`, the ledger, the gate, the router, the watchdog, real worker processes and SQLite. What's scripted is what would need your accounts: the agents, the source data and, where it says so, the clock. Each README lists exactly which is which.

`python3 examples/record.py` rebuilds the recordings, and needs `asciinema` and `agg`.
