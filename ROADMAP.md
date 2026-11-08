# Roadmap

Where this is going and where help is most useful. What's already done is ticked in the [README](README.md#roadmap).

## Now

- **A first live run.** CI proves the router and worker path on real Hermes with a fake model. A real model and chat platform haven't driven it yet.
- **The Hermes Plugin Hackathon entry,** due 2026-11-11.

## Connectors

Each connector reads one tool into the ledger, so nothing it shows gets lost. Most can start as a `command` block with no code, see [CONTRIBUTING.md](CONTRIBUTING.md#add-a-connector). "Info" sources show in the brief without anyone owning their items.

| Source | Likely tool | An item is | State |
| --- | --- | --- | --- |
| GitHub | `gh` | an open issue or PR assigned to you, asking for your review, or mentioning you | Done |
| GitLab | `glab` | an assigned issue or merge request, a review request, a pending todo | Done |
| Claude Code | a hook and an ssh probe | a session, with its state: working, waiting, stuck, rate-limited, gone | Done |
| Any JSON CLI | `command` | whatever the tool lists | Done |
| Todoist | `td` | an active task | In review, [#35](https://github.com/AirKyzzZ/hermes-superagent/pull/35) |
| Gmail | `gog`, one block per account | an inbox thread, closed once it's archived or trashed | In review, [#36](https://github.com/AirKyzzZ/hermes-superagent/pull/36) |
| Google Calendar | `gog` | an event in the next few days, info | Wanted |
| Any calendar shared as a link | an ICS URL | an event, info | Wanted |
| WhatsApp | `wacli`, read only | a chat with a listed contact whose last message isn't yours, closed once you reply | In review, [#36](https://github.com/AirKyzzZ/hermes-superagent/pull/36) |
| Zepp | a `command` preset | last night's sleep and other daily readings, info | Wanted |
| Anki | a `command` preset | today's due cards, info | Wanted |
| CodexBar | a `command` preset | coding-agent usage and quota, info | Wanted |

Mail and chat never start work on their own, because anyone can write to you. The owner can still assign an item from them. Something else you'd want in the ledger, such as Linear, Jira or a Markdown task file? [Open a connector issue](https://github.com/AirKyzzZ/hermes-superagent/issues/new?template=connector.yml).

## Next

- **Claude Code workers over ssh.** A worker that is a real Claude Code session in tmux or cmux on another machine, watched by the same watchdog.
- **A quota gate.** When CodexBar reports the Claude quota under a floor, new work waits and the brief says how much is held.
- **A demo video** for the hackathon.
