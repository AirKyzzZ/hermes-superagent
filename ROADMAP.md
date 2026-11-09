# Roadmap

Where this is going and where help is most useful. What's already done is ticked in the [README](README.md#roadmap).

## Now

- **A real chat message through the router.** CI proves the router and worker path on real Hermes with a fake model, and [one recorded run](docs/live-run.md) took a worker, the watchdog and a Discord relay through a real model. That task skipped the router's model turn, so a message on a real chat platform is next.
- **The Hermes Plugin Hackathon entry,** due 2026-11-11.

## Connectors

Each connector reads one tool into the ledger, so nothing it shows gets lost. [docs/connectors.md](docs/connectors.md) has a copy-paste block for each one that's done. Most new ones can start as a `command` block with no code, see [CONTRIBUTING.md](CONTRIBUTING.md#add-a-connector). "Info" sources show in the brief without anyone owning their items.

| Source | Likely tool | An item is | State |
| --- | --- | --- | --- |
| GitHub | `gh` | an open issue or PR assigned to you, asking for your review, or mentioning you | Done |
| GitLab | `glab` | an assigned issue or merge request, a review request, a pending todo | Done |
| Claude Code | a hook and an ssh probe | a session, with its state: working, waiting, stuck, rate-limited, gone | Done |
| Any JSON CLI | `command` | whatever the tool lists | Done |
| Todoist | `td` | an active task | In review, [#35](https://github.com/AirKyzzZ/hermes-superagent/pull/35) |
| Gmail | `gog`, one block per account | an inbox thread, closed once it's archived or trashed | In review, [#36](https://github.com/AirKyzzZ/hermes-superagent/pull/36) |
| Google Calendar | `gog`, one block per account | an event in the next few days, info | Done |
| Any calendar shared as a link | an ICS URL | an event, info | Wanted |
| WhatsApp | `wacli`, read only | a chat with a listed contact whose last message isn't yours, closed once you reply | In review, [#36](https://github.com/AirKyzzZ/hermes-superagent/pull/36) |
| Zepp | a `command` preset | last night's sleep and other daily readings, info | Wanted |
| Anki | AnkiConnect through `curl` | today's due cards, info | Done |
| CodexBar | `codexbar` | coding-agent usage and quota, info | Done |

Mail and chat never start work on their own, because anyone can write to you. The owner can still assign an item from them. Something else you'd want in the ledger, such as Linear, Jira or a Markdown task file? [Open a connector issue](https://github.com/AirKyzzZ/hermes-superagent/issues/new?template=connector.yml).

## Next

- **Claude Code workers over ssh.** A worker that is a real Claude Code session in tmux or cmux on another machine, watched by the same watchdog.
- **A quota gate.** When CodexBar reports the Claude quota under a floor, new work waits and the brief says how much is held.
- **A demo video** for the hackathon.
