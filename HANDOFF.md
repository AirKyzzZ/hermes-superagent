# Handoff: growth lane

Branch: `docs/growth-handoff`, from `main`. It holds only this file, so don't merge it.

The lane's drafts (`docs/growth/audit.md`, `launch-kit.md`, `first-issues.md`) are on the local branch `docs/growth` on the Mac, commit `6608744`. That branch is kept off this public repo on purpose, so never push it. Maxime has it as `docs-growth.bundle`. To restore it in a clone of this repo:

```bash
git fetch /path/to/docs-growth.bundle docs/growth:docs/growth
```

## Done

All merged with CI green:

- #38 CONTRIBUTING, ROADMAP, SECURITY, issue and PR templates
- #39 README first screen, demo GIF, quickstart, "why not just..."
- #40 flaky `test_demo` fix
- #41 pitch lines that don't wrap
- #43 `setup` keeps the machine's timezone without `--brief`
- #44 slimmer README header

Also on `docs/growth`: five good-first-issue drafts, rewritten against `main` and dry-run with a stubbed `gh`. None is opened.

## Next

Write `docs/growth/directories.md` on the private branch: the awesome-lists, directories and Discord channels that accept Hermes Agent plugins, with exact submission requirements and links. Not started in the repo: the research run was stopped unfinished when the lane moved, and nothing from it was kept. Start from sections 2 and 3 of the launch kit, re-check each venue against its primary source, and say which Discord and Reddit rules can't be verified. Then link it from the launch kit and correct any facts that changed.

## Maxime's alone

Don't do these, prepare them: public repo settings (homepage, social preview, private vulnerability reporting, the `connector` label), opening the first issues, every post and form, the catalog PR.

The description and topics are already live. `gh repo edit` ran on 2026-11-08 before his answer that he does public repo settings himself arrived. He decides whether to keep them. The command to undo it is in section 1 of the launch kit.

## Open PRs

None from this lane. #42 (`docs/ci-badge`) belongs to another lane.

## Gotchas

- Before merging, run all four checks from CONTRIBUTING.md, including `ruff format --check .`.
- Merge with `gh pr merge --squash --author-email 52069616+AirKyzzZ@users.noreply.github.com`, then check the author on `main`.
- Pushed branches get `main` merged in, never rebased or force pushed.
- A restored `docs/growth` must have no upstream. It used to track `origin/main`, where a bare `git push` would have published it.
- Only `[brief] hour` needs a brief channel now, `timezone` and `stale_after` don't (#43).
- The "about 230px higher" in #44 comes from the image ratios. Headless Chrome produced no screenshot, so it wasn't measured on GitHub.
- The Taskwarrior block in the fifth issue only ran against a stand-in script, as the issue says.
