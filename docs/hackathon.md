# Hermes Agent Plugin Hackathon

Rules from the [announcement](https://x.com/NousResearch/status/2118094256438936062), as posted by Nous Research.

| Item | Rule |
| --- | --- |
| Deadline | 5PM EST on Wednesday, November 11, 2026 (22:00 UTC, 23:00 in Paris) |
| Length | 7 days, every kind of plugin is eligible |
| Judging | Usefulness, reliability, polish and creativity. Judges favour plugins that do more than a prompt or a skill could. The entry is the plugin itself |
| Listing | Accepted entries go in the Hermes Marketplace |
| Afterwards | A thread for every entry in the Nous Research Discord when submissions close |

| Place | Prize |
| --- | --- |
| 1st | $10,000, neon sign, merch, $1,000 credits |
| 2nd | $5,000, neon sign, merch, $1,000 credits |
| 3rd | $2,500, neon sign, merch, $1,000 credits |
| 4th | $1,500, merch, $500 credits |
| 5th | $1,000, merch, $500 credits |
| Runners up (5) | Merch, $250 credits |

## To enter

The owner does all three steps. Nothing in this repo opens the PR, posts or submits.

1. Open a PR adding the plugin to the catalog, following the [catalog submission guide](https://hermes-agent.nousresearch.com/docs/developer-guide/plugins/catalog-submission).
2. Post a demo video on X tagging @NousResearch. Keep it under 90 seconds and show the plugin working.
3. Submit both links through the [form](https://form.typeform.com/to/FwJLvOln).

The form can go in as soon as the PR is open. Catalog checks run once a maintainer starts them, and entries are reviewed every few days.

## Before submitting

**Still open before the demo:** one run with a live model and a real chat platform. CI already runs the whole path on real Hermes with a fake model.

| Catalog rule | State | Evidence |
| --- | --- | --- |
| Loadable plugin, validation passes | Ready | CI runs `hermes plugins validate --install-deps .` on Hermes v0.22.7, with one caution: the sanitizer regex in `framing.py` |
| 2. Exact SHA pin | At submission | See the steps below |
| 3. No self-updating code | Ready | The plugin fetches nothing |
| 5. Submitted by the owner | At submission | The owner opens the PR |
| 6. Declared capabilities match | Ready | `tests/test_catalog_entry.py`, plus validation in CI |
| 9. No core overrides | Ready | Checked by validation |
| 10. Dependencies | Ready | No runtime dependencies |
| 11. Credentials through Hermes | Ready | The plugin holds no credentials and calls models through `ctx.llm` |
| 12. Approvals respected | Ready | Workers start from an environment allowlist that drops YOLO settings, tested in `tests/test_worker.py` |
| 13. Risky behaviour disclosed | Ready | [What it does on your machine](../README.md#what-it-does-on-your-machine), the entry description and the PR text below |
| 14. Truthful compatibility | Ready | `tests/test_catalog_entry.py` keeps the floor and version in step |
| 17. No executable payloads | Ready | No binaries, bytecode or archives in the tree |

The plugin page renders the README through an allowlist that drops raw HTML, so the README keeps its content in Markdown and the HTML only decorates it on GitHub. Mermaid shows as code and alerts as plain quotes there.

## Submission steps

1. Confirm CI is green on `main` and nothing above is still open.
2. Copy [catalog-entry.yaml](catalog-entry.yaml) to `plugin-catalog/hermes-superagent.yaml` in a fork of `NousResearch/hermes-agent`, and pin it. Both the `sha` and the `image` URL take the same commit:

   ```bash
   sha=$(git -C /path/to/hermes-superagent rev-parse origin/main)
   sed -i '' "s/0000000000000000000000000000000000000000/$sha/g" plugin-catalog/hermes-superagent.yaml
   ```

3. Open the PR with one file changed, using the text below.

## PR description draft

```markdown
Adds hermes-superagent, a plugin that brings DeepSeek's Super-agent design to Hermes: one resident router that talks to people and never executes, isolated workers that report only to it, a sender gate plus team-written trigger rules, and an external watchdog that checks real progress and goal drift, then nudges, restarts or escalates.

It also keeps an item ledger fed by connectors (GitHub through `gh`, any tool that prints JSON, and Claude Code sessions on several machines), so nothing a source shows is silently lost: polls that can't prove they're complete mark nothing gone, vanished items are checked one by one, and a daily brief lists every open item at least every few days.

Hermes surfaces: `ctx.register_tool` (7 tools), the hooks `pre_gateway_dispatch`, `pre_llm_call`, `pre_tool_call` and `post_tool_call`, `ctx.llm`, `ctx.register_cli_command` and `ctx.register_automation_blueprint`. No core patches.

Disclosure, also in the README:
- Spawns long-running worker processes from the argv the operator configures, and runs the outbound send command and the connector commands the operator configures.
- The `agents` connector sends a read-only probe script through the host command the operator configures, usually `ssh`. The Claude Code hook it reads is printed by the plugin and installed by the operator.
- Only when the operator sets `relaunch = true`, it types one short nudge into a Claude Code session stopped by an API error or a rate limit, after checking the pane and screen.
- Stops or restarts its own workers by process group.
- Spends tokens on the profile's model during ticks, and three calls per worker brief, capped per day.
- Writes its database and worker directories under the configured home.
- Inert until a profile sets a role. No telemetry, no network calls of its own, no stored credentials.
- `hermes plugins validate` reports one caution: the invisible-character regex in `framing.py`, which is the sanitizer itself.
- `assets/art` holds the scripts that built the cover from a public-domain Met image. The plugin never runs them.
```
