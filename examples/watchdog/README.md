# Watchdog

Five ways an agent gets stuck, and what catches each one. Asking an agent how it's going gets you "almost done", so none of this asks.

![Five stuck workers: repeating itself, going quiet, crashing, looping on a command, and running out of budget, each caught and repaired or escalated](recording.gif)

```bash
python3 examples/watchdog/run.py
```

| The agent | Caught by | What happens |
| --- | --- | --- |
| Says it's almost done, forever | The last 3 reports are identical | Nudge, restart |
| Goes quiet, process still alive | No report for `stall_after_seconds` | Nudge, restart |
| Crashes without a word | The process is gone with no final report | Restart |
| Runs the same failing command | The same tool call and result 5 times in a row | Nudge, restart |
| Still stuck after the restart | The repair budget is spent | Stopped, and you get a "Needs you" |

After the first restart the old agent tries to file "all done, trust me" on its way out. It gets refused, because a worker is fenced before it's stopped.

The watchdog, the fencing, the store and the worker processes are the production code, and each check runs as the same `python -m superagent tick` you'd put on a timer. The agents are [scripted](../scripted_agent.py), and [`superagent.toml.template`](superagent.toml.template) sets a short stall limit so the quiet one only takes seconds.

There's no model anywhere in this run. The one thing a model decides in a real install is whether a worker has drifted off its goal, and that isn't shown here. That judge has no tools and can only answer `on_track` or `drifting`.
