# Handoff: show the value to a stranger

Branch: `feat/runnable-examples`, based on `e08bc83`. Unfinished, nothing from this lane is merged. Delete this file before the branch is merged.

## Done

- Three runnable examples under `examples/`, no credentials, model or network: `nothing-slips`, `night-shift`, `watchdog`. Each has `run.py`, a template config, a short README, `recording.cast` and `recording.gif` (all under 1 MB).
- Shared pieces: `examples/showcase.py` (output and temp home), `examples/scripted_agent.py` (what each fake agent does per attempt), `examples/record.py` (rebuilds the recordings with `asciinema` and `agg`), `examples/README.md` (index).
- `tests/test_examples.py` runs all three.
- README has an `Examples` section and a nav link.
- Local checks on macOS before the handoff commit: `pytest` 658 passed, `ruff format --check .`, `ruff check .` and `mypy` clean. Secret and personal-data scan of the diff is clean.

## Next in this lane

1. Run the checks on WSL, the examples have only run on macOS so far. CI only runs on `main` and on pull requests, so the branch has no CI result yet.
2. Merge `origin/main` into the branch, delete `HANDOFF.md`, open the examples PR, merge it once the full check set passes.
3. Write `docs/architecture.md`, not started, as a second PR. One page with a Mermaid diagram that adds to the README's four: both halves on one map (router and workers, ledger and connectors) and the line between what a model decides and what code enforces. Also the tick phases in order from `superagent/runtime.py` `_run_phases` (poll, triage, relaunch, brief, assess, watchdog, relay, prune, alerts, heartbeat), and which example exercises what. Check that it renders with Playwright's bundled Chromium, then link it from the README's Architecture section.

## Open PRs

None from this lane. #42 (`docs/ci-badge`, another lane) is open and touches the README badges, so expect to merge it in if it lands first. `main` has already moved past this branch's base with #43 and #44, and #44 changes the README header, so the README may need a small conflict fix when `origin/main` is merged in.

## Decisions made without asking

- Sources in `nothing-slips` are `command` connectors over one JSON file, not the real `github`, `todoist`, `gmail` and `whatsapp` types behind fake CLIs, so the config stays readable and swappable. The README says so.
- `nothing-slips` patches `time.time` so four days run in seconds. The other two run on the real clock with ticks back to back, because their workers are separate processes.
- `night-shift` also shows the Claude Code sessions board, since "coding agents" covers those and the main demo doesn't show it.
- Drift is left out of `watchdog` because it needs a model. Its README says that.
- Two PRs (examples, then architecture) rather than one per example, since the examples share the kit and the recorder.

## Gotchas

- Run checks with `uv run --no-project --with-requirements requirements-dev.txt <tool>`, and include `ruff format --check .` even for docs.
- Squash merge with `gh pr merge --squash --author-email 52069616+AirKyzzZ@users.noreply.github.com`, then scan the merged commit's author. Read a scan's result before pushing, in a separate step.
- Never rebase once pushed, merge `origin/main` in instead. Force push is off the table.
- `SCRIPT` in `scripted_agent.py` is keyed on the exact goal text. A goal that doesn't match crashes the worker, and its traceback gets it parked instead of restarted.
- `stall_after_seconds = 4` in `watchdog` is deliberate. Python startup counts against it, and a slow second attempt spends the budget and escalates, which fails the test. Lower it only with that in mind.
- `nothing-slips` ticks shift by one minute per day on purpose: Thursday has to be strictly more than 3 days after Monday for the "also open" section to fire.
- Report counts in `night-shift` vary per run, so tests don't pin them. The sessions board shows `0m` because the items were only just recorded.
- Recording needs `asciinema` and `agg` on the machine. The committed recordings were made before the last `ruff format`, which only changed code layout.
- Output, recordings and fixtures must stay free of local paths and real names, the repo is public.
