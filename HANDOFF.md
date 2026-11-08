# Handoff: packaging and release lane

Branch: `fix/pip-uv-install`, cut from `origin/main` at `e08bc83`. Delete this file before the PR merges.

Lane: trivially installable and trustworthy. Packaging that installs from git in a clean venv, CI, a CHANGELOG, a v0.1.0 release-notes draft, and the catalog entry as a file. No tag, no GitHub release, no catalog PR: Maxime does those.

## Done on this branch (WIP, nothing merged)

- `pyproject.toml`: `[tool.setuptools.packages.find]`, a `superagent` console script, a `hermes_agent.plugins` entry point, `readme` and `urls`.
- `tests/e2e/check_install.py`: installs into clean pip and uv venvs, then checks away from the source that the package imports from site-packages, the hook, probe and relaunch scripts are there, the entry point loads, and `superagent setup` and `status` run.
- `.github/workflows/ci.yml`: `hermes-compat` and `e2e` now run on both 0.22.7 (the floor) and 0.23.0 (latest), plus a new `packaging` job.
- `.gitignore`: `build/`, `dist/`, `*.egg-info/`.

## Evidence

| Check | Result |
| --- | --- |
| `pip install git+https://...@e08bc83` before the change | Fails, setuptools refuses the flat layout (multiple top-level packages) |
| `check_install.py` from the worktree path, after the change | Passes for pip and uv |
| Full checks on `e08bc83` before the change | 655 tests pass, ruff format, ruff check and mypy clean |
| After the change | Only `ruff format --check .`, `ruff check .` and `tests/test_catalog_entry.py` were run, all clean. The full suite and mypy have not been rerun |
| `hermes plugins validate .` on Hermes 0.23.0, throwaway `HERMES_HOME`, tree at `e08bc83` | Passed, 5 scanner cautions (below) |
| Actions availability | Enabled, public repo, every recent run green. The billing endpoint needs the `user` token scope, so exact minutes were not read, but nothing is blocked |

## Not verified

- None of the CI changes have run. `ci.yml` triggers on `pull_request` and pushes to `main`, so pushing this branch runs nothing until a PR exists.
- Hermes discovering the pip-installed plugin through the entry point. Only the CI `packaging` job tests this, and `hermes plugins enable hermes-superagent` in that job is a guess that may need adjusting.
- `pip install` and `uvx` from the actual git URL of this branch. The passing run used a local path.
- `hermes plugins install AirKyzzZ/hermes-superagent --enable` in a clean home. The run was stopped after about 5 minutes on the overloaded Mac with nothing written, so the result says nothing either way. Retry it with a throwaway `HERMES_HOME`.

## Next

1. Rerun the full check set, push, and open the PR so CI runs. Fix what the `packaging` job and the 0.23.0 legs show. Drop the entry point if Hermes doesn't load it that way.
2. Run the git-URL installs: `pip install git+https://github.com/AirKyzzZ/hermes-superagent@fix/pip-uv-install` in a clean venv, and `uvx --from git+https://github.com/AirKyzzZ/hermes-superagent@fix/pip-uv-install superagent --help`.
3. Add the pip and uv one-liners to the README install section. Be clear that `hermes plugins install` stays the supported path, and that a pip install has to land in the Hermes environment for the entry point to count.
4. Correct the "one caution" claim at `README.md:254` and `docs/hackathon.md:38` and `:82`. Rule 7 of the catalog has the reviewer read these, so the list has to be accurate.
5. Write `CHANGELOG.md` and `docs/release-notes-v0.1.0.md` from the 42 commits on `main`. No tag or release.
6. Catalog entry: `docs/catalog-entry.yaml` already exists and CI validates it. Its `sha` and `image` stay as zeros until Maxime submits. Update the PR text in `docs/hackathon.md`, which still lists only GitHub, GitLab, JSON tools and Claude Code as connectors, while Todoist, Gmail and WhatsApp have since landed. Mention that the last two read mail and chats when configured. Accepted entries also carry a `title:` field and ours has none, worth adding if the validator allows it.
7. If a 0.23.0 leg fails, fix it or tell Maxime before submitting: `requires_hermes` is `>=0.22.7`, so the plugin claims to work there.

## Scanner cautions on 0.23.0

All are pattern hits, none is executable risk:

- `curl_pipe_shell`, `docs/dogfood-design.md:325`, prose about an injected instruction
- `destructive_home_rm`, `tests/test_briefwriter.py:112`, input to the lint test
- `format_filesystem`, `superagent/briefwriter.py:45`, the command-detection regex
- `hardcoded_secret`, `README.md:319`, a placeholder webhook secret
- `invisible_unicode`, `superagent/framing.py:9`, the sanitizer

## Open PRs

None from this lane. #42 (`docs/ci-badge`) is open on the repo and belongs to another lane.

## Gotchas

- `origin/main` had moved past this branch's base when it was pushed (#43, #44). Merge it in before opening the PR. Line numbers in this file are from `e08bc83`, and #44 changed the README.
- Keep `build-system`, `optional-dependencies`, `dependency-groups` and `[tool.uv] package` out of `pyproject.toml`. Hermes would rebuild its shared env on enable, and `test_plugin_puts_nothing_into_the_shared_hermes_environment` fails. pip and uv fall back to setuptools without a `build-system` table, and `[tool.setuptools]`, `[project.scripts]` and `[project.entry-points]` don't trigger the rebuild.
- If the directory plugin and the entry point share a name, Hermes keeps the directory.
- Dev tools run through `uv run --no-project --with-requirements requirements-dev.txt`. `ruff format --check .` also formats Python blocks inside Markdown. The full pytest run took about 5 minutes on the Mac.
- Merging: `gh pr merge --squash --author-email 52069616+AirKyzzZ@users.noreply.github.com`, then check the author on the resulting commit. Merge `main` into a pushed branch, never rebase it.
- `docs/hackathon.md` pins with `sed -i ''`, which is macOS syntax. On WSL use `sed -i`.
- `check_install.py` expects a `bin/` venv layout, so run it in WSL, not native Windows.
- Don't install or test against a Hermes that is in daily use without a throwaway `HERMES_HOME`. The classifier refused building pristine Hermes copies locally on the Mac, so floor-version checks went through the CI install action.
- Don't change repo settings (`gh repo edit`), those are Maxime's.
