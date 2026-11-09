# Nothing slips

Four mornings across GitHub, Todoist, Gmail and WhatsApp, in a few seconds.

![Four daily briefs: a broken export is held back, a deleted task stays on the list, a reverted fix comes back, and untouched items resurface](recording.gif)

```bash
python3 examples/nothing-slips/run.py
```

- **Monday:** the first sweep puts 13 items in the ledger and sends the first brief.
- **Tuesday:** the Todoist export breaks and returns 2 of 6 tasks. A plain sweep would read the other 4 as done. The snapshot is held back, an alert goes out, and nothing is marked gone.
- **Wednesday:** one task is finished, and its source confirms it. Another is deleted by mistake, so it stays open under "gone without a close". A reverted fix reopens its issue, and it comes back.
- **Thursday:** nothing changed, and the brief still lists every open item it hasn't shown for 3 days.

The ledger, the polling checks, the brief and the `tick` that runs them are the production code. The clock is simulated, so four days take seconds, and the tools are [`fixtures.json`](fixtures.json) read through [`source.py`](source.py), wired up in [`superagent.toml.template`](superagent.toml.template) as `command` connectors. On a real install those are `type = "github"`, `"todoist"`, `"gmail"` and `"whatsapp"`, or any tool of yours that prints JSON.

To see it react differently, change a morning in `fixtures.json` and run it again.
