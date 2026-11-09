# Your GitHub

The other examples run on made-up data. This one shows what's open on your own GitHub: everything assigned to you, waiting for your review or mentioning you, and what has gone longest without an update.

```bash
python3 examples/your-github/run.py
```

It needs the [`gh` CLI](https://cli.github.com), logged in. No Hermes, model or other credentials.

It only reads, through your existing `gh` login, using the same `github` connector a real install uses. Nothing is written back, nothing is sent anywhere, and its temporary database is deleted when it ends.

To get this every morning, with what changed since yesterday, see [Install](../../README.md#install).
