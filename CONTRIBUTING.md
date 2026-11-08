# Contributing

Bug reports, connector requests and pull requests are all welcome. This is a small project with one maintainer, so small, focused pull requests land fastest. For anything bigger than a fix, open an issue first so we can agree on the shape.

## Setup

You need Python 3.11+ and [uv](https://docs.astral.sh/uv/). Hermes isn't needed for the tests or the demo.

```bash
git clone https://github.com/AirKyzzZ/hermes-superagent
cd hermes-superagent
python3 demo/run_demo.py
```

## Checks

CI runs these four on Ubuntu and macOS with Python 3.11 and 3.14. Run all of them before you push. The format check also covers Python blocks inside Markdown files, so it can fail on a docs-only change.

```bash
uv run --no-project --with-requirements requirements-dev.txt ruff check .
uv run --no-project --with-requirements requirements-dev.txt ruff format --check .
uv run --no-project --with-requirements requirements-dev.txt mypy
uv run --no-project --with-requirements requirements-dev.txt pytest -q
```

Two more CI jobs load the plugin through real Hermes (`hermes plugins doctor` and `hermes plugins validate`) and run the router and worker path end to end with a fake model. You don't need to run them locally, see [the workflow](.github/workflows/ci.yml).

The plugin has no runtime dependencies, and it should stay that way. Dev tools go in `requirements-dev.txt`, never in `pyproject.toml`: Hermes rebuilds its shared environment when a plugin's `pyproject.toml` declares dependency groups.

## Style

- `mypy --strict` and ruff with a 120-character line.
- Clear names instead of comments. A comment is for a hidden constraint or a workaround, in one line.
- New behaviour comes with a test. Before touching the ledger or polling, read the [reliability invariants](docs/dogfood-design.md#reliability-invariants): each one has a test that has to keep passing.
- Test data holds no real names, emails, tokens or IDs.
- Commits and pull request titles follow conventional commits, lowercase after the colon: `feat: read calendars from an ics url`.

## Add a connector

A connector reads one tool into the ledger. Most tools need no code at all: if the tool prints JSON, a `command` block reads it.

```toml
[connectors.notes]
type = "command"
every = "10m"
argv = ["my-tool", "list", "--open", "--json"]
items = "results"
fields = { id = "id", title = "name", url = "link", status = "state" }
confirm_argv = ["my-tool", "show", "{id}", "--json"]
```

`superagent doctor` polls it once without recording anything. Every key is described in the [configuration table](README.md#configuration). If it works for you, share the block in a [connector issue](https://github.com/AirKyzzZ/hermes-superagent/issues/new?template=connector.yml) so others can use it too.

A built-in type is worth writing when a block can't express what the tool needs, such as pagination, a total to check against, or a lookup to confirm that one item closed. It's one file in [`superagent/connectors/`](superagent/connectors/):

- `KEYS` lists the settings it accepts, and unknown keys are refused.
- `build(spec)` checks the settings and returns an object with `timeout`, `poll(cursor) -> Poll` and `confirm(external_id) -> Upstream`.
- It's registered in `connector_types()` in [`superagent/connectors/__init__.py`](superagent/connectors/__init__.py).
- It runs the tool through `run_json` in [`process.py`](superagent/connectors/process.py): no shell, a timeout, and only the environment variables it names.

Every connector keeps these rules, and its tests prove them:

- `complete=True` only when the poll saw everything: every page, and a count that matches the total the tool reports. Otherwise it adds a problem, and nothing gets marked gone.
- `confirm` answers `closed` only when the source says so. Not found is `unknown`, and an error raises, so a token that lost access can't close anything.
- An id is checked against a strict pattern before it's passed to a command.
- Tests run against a fake CLI with fixtures recorded from the real tool and scrubbed, like [`tests/fake_gh.py`](tests/fake_gh.py) and [`tests/fake_glab.py`](tests/fake_glab.py).

[`gitlab.py`](superagent/connectors/gitlab.py) is a complete example, including the README row and `setup` detection in [`starter.py`](superagent/starter.py). [ROADMAP.md](ROADMAP.md) lists the connectors people want.

## Security

Please don't open a public issue for a vulnerability, see [SECURITY.md](SECURITY.md).
