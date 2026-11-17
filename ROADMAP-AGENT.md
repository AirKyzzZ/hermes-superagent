# Maintainer agent roadmap

The working plan of the agent that maintains this repo between the owner's reviews. It's ranked by what gets a stranger from "found it" to "running it", then by what keeps it running once they do. [ROADMAP.md](ROADMAP.md) stays the public, contributor-facing list. Last reviewed 2026-11-17.

## What the signals say

- **6 stars, 2 forks, no issues yet.** People look, few talk. Nobody has reported an install failure, which more likely means few have tried the Hermes path than that it works for everyone.
- **One fork runs it for real.** It's 54 commits ahead and they're all generic fixes or features a production user needed: a cap on running workers with a queue, `{ticket}` in the worker command, stopping a worker's whole process tree, plain-text relays with links stripped, role checks on the tools, a database reopened after a version reset. That's the clearest list of what the next serious user hits. Each item below is written fresh here, none of it is copied.
- **The catalog entry merged on 2026-11-11, pinned at `83544d7` (v0.1.0).** Anyone who installs by name gets v0.1.0, without the fixes in #72 and #73. #73 matters most: `setup` couldn't find a Hermes installed with its own installer, which is the documented way to install Hermes.
- **Hermes shipped v0.23.1 (2026-11-10) and v0.23.2 (2026-11-16).** CI tests 0.22.7 and 0.23.0 only. 0.23.2 rolls up about 2,000 PRs, including module moves with shims, profile rename fixes and new plugin API (`ctx.register_idle_task`, `ctx.redact_text()`, an `on_session_open` hook). The curated notes come with v0.24.0, so the new API waits for them.
- **The README still says "not in the catalog"** in the comparison table, and the install line uses the GitHub path rather than the catalog name.

## Ranked

| # | Item | Why it ranks here | Size |
| --- | --- | --- | --- |
| 1 | CI on Hermes 0.23.2, the latest release | Everyone who installs today gets 0.23.2. If it broke something, that's the first bug every new user hits, and nothing would tell us | S |
| 2 | v0.1.1 and a catalog bump | Installing by name ships the #73 bug to installer-installed Hermes. Needs the owner: the tag, the release and the catalog PR are his | S, his go |
| 3 | README and docs say what's true after the hackathon | "Not in the catalog" is wrong, and installing by name is the shortest path there is. The hackathon framing is over | S |
| 4 | `[worker] max_running` and a queue | Ten tasks at once means ten model sessions at once, and the cost or the rate limit lands all at once. Without a cap, the router is risky to leave alone, which is the whole point. The fork needed it first | M |
| 5 | Stop a worker's whole process tree | A worker that runs `setsid`, a daemon or a dev server leaves its group, and today survives a stop or a restart. Reliability is what the watchdog promises | M |
| 6 | Claude Code as the worker | Most people who'd run "more agents than they can watch" run Claude Code. `superagent report` already exists for agents outside Hermes; a tested `[worker] argv` recipe, `{ticket}` and `{workdir}` in the command, and a doc turn that into a headline feature | M |
| 7 | An ICS calendar connector | Listed as wanted. Any calendar shared as a link, no login, no CLI to install | S |
| 8 | A Markdown task file connector | `- [ ]` lines in a file: Obsidian, a TODO.md, a notes repo. The cheapest way to try the ledger on your own data with no account at all | S |
| 9 | Plain-text relays with links stripped | A worker's report is model output built from untrusted text, and it reaches a chat as a message from your bot. A link in it is a phishing vector the gate can't see | M |
| 10 | A quota gate on CodexBar | Listed under Next. New work waits when the Claude quota is under a floor, and the brief says what's held | M |
| 11 | Adopt the new Hermes plugin API | `register_idle_task` could replace the cron line, which is a setup step people get wrong. Waits for v0.24.0's notes | M, later |
| 12 | `setup --team` for the three-profile Hermes install | The Hermes setup is about ten commands and hasn't run end to end. One command that prints, then applies, them would remove the biggest drop-off. Needs proof in the e2e job first | L, later |

## Not now

- **PyPI.** `uvx hermes-superagent` would read better than a git URL, but it needs the owner's PyPI account and trusted publishing set up. A workflow can be prepared when he wants it.
- **A web dashboard.** The brief and the agents board already go to chat, where people read them.
- **Durable subagents in Hermes 0.23.2** overlap with workers and the watchdog. Worth a look once v0.24.0 documents them, not a reason to rewrite now.

## How the agent works

One branch and one PR per item, CI green, a short description. Merges wait for the owner. Replies to issues or PRs from other people are drafted for him, never posted. Releases, the catalog, X and repo settings are his.
