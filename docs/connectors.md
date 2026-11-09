# Connectors

A connector reads one tool into the ledger. Paste a block below into `superagent.toml`, then check it. `doctor` polls every connector once and records nothing:

```bash
hermes superagent --home ~/superagent doctor
```

`setup` writes the GitHub, GitLab, Todoist, CodexBar and Claude Code blocks for you when it finds those tools. Without Hermes, use `python3 -m superagent` from the clone in place of `hermes superagent`.

| Source | Type | Needs | An item is | Kind |
| --- | --- | --- | --- | --- |
| [GitHub](#github) | `github` | `gh` logged in | an open issue or PR assigned to you, asking for your review, or mentioning you | tracked |
| [GitLab](#gitlab) | `gitlab` | `glab` logged in | an assigned issue or merge request, a review request, a pending todo | tracked |
| [Todoist](#todoist) | `todoist` | `td` logged in | an active task | tracked |
| [Gmail](#gmail) | `gmail` | `gog` logged in | an inbox thread, closed once it's archived or trashed | tracked |
| [WhatsApp](#whatsapp) | `whatsapp` | `wacli` linked and syncing | a chat with a listed contact whose last message isn't yours | tracked |
| [Claude Code](#claude-code) | `agents` | the hook from `superagent agents --hook-script` | a session and its state: working, waiting, stuck, rate-limited, gone | tracked |
| [Google Calendar](#google-calendar) | `gcal` | `gog` logged in with Calendar access | an event in the next few days | info |
| [CodexBar](#codexbar) | `codexbar` | `codexbar` | a provider's quota | info |
| [Anki](#anki) | `anki` | Anki open with the AnkiConnect add-on | today's due cards | info |
| [Anything else](#anything-that-prints-json) | `command` | a command that prints JSON | whatever it lists | either |

A tracked item stays open until its source confirms it closed or you close it with a reason. An info item shows under Today in the brief and nobody owns it.

Connectors only read. The one exception is `relaunch = true` on `agents`, off by default, which types `continue` into a stopped session.

Gmail, WhatsApp, Google Calendar and Anki were built from each tool's source with synthetic test data and haven't run against a real account yet. Read what `doctor` says the first time you turn one on.

Every setting is in the [configuration table](../README.md#configuration).

## GitHub

```toml
[connectors.github]
type = "github"
every = "10m"
```

## GitLab

```toml
[connectors.gitlab]
type = "gitlab"
every = "10m"
```

## Todoist

```toml
[connectors.todoist]
type = "todoist"
every = "5m"
```

## Gmail

One block per mailbox.

```toml
[connectors.gmail-personal]
type = "gmail"
account = "you@example.com"
every = "10m"
```

## WhatsApp

List each chat by its JID. Reading a message on your phone changes nothing, and a chat closes once you reply.

```toml
[connectors.whatsapp]
type = "whatsapp"
chats = ["33600000000@s.whatsapp.net"]
every = "5m"
```

## Claude Code

One block per machine. Leave `host` out for this one. `doctor` fails until the hook has written something, see [the shadow install guide](dogfood-install.md).

```toml
[connectors.agents]
type = "agents"
every = "90s"

[connectors.agents-mac]
type = "agents"
host = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "mac"]
every = "90s"
```

## Google Calendar

One block per account. Lists today and the next two days from the calendars ticked in Google Calendar. Set `days` for a longer window, or `calendars = ["primary"]` to narrow it.

```toml
[connectors.calendar]
type = "gcal"
account = "you@example.com"
every = "30m"
```

## CodexBar

Reads the providers enabled in CodexBar. One that reports an error, such as an expired login, fails the poll while the others still record. `providers = ["claude"]` narrows it.

```toml
[connectors.codexbar]
type = "codexbar"
every = "1h"
```

When it runs on another machine:

```toml
[connectors.codexbar-mac]
type = "codexbar"
every = "1h"
codexbar = ["ssh", "mac", "/opt/homebrew/bin/codexbar"]
```

## Anki

A closed or busy Anki records nothing and isn't a failure, so `quiet_after` is how you hear that it has stayed closed. New cards and daily limits aren't counted, so the number can differ from Anki's home screen.

```toml
[connectors.anki]
type = "anki"
every = "1h"
quiet_after = "3d"
```

AnkiConnect only listens locally, so from another machine send the request through ssh:

```toml
[connectors.anki-mac]
type = "anki"
every = "1h"
quiet_after = "3d"
curl = ["ssh", "mac", "curl"]
```

## Anything that prints JSON

A `command` block needs no code. `items` is the dotted path to the list, and `fields` maps `id` and `title` to paths or `{path}` templates. With `confirm_argv`, an item that leaves the list is looked up, and only a closed one is closed.

```toml
[connectors.notes]
type = "command"
every = "10m"
argv = ["my-tool", "list", "--open", "--json"]
items = "results"
fields = { id = "id", title = "name", url = "link", status = "state" }
confirm_argv = ["my-tool", "show", "{id}", "--json"]
```

Add `track = false` for a reading, such as last night's sleep from a script of your own. Each new `id` is a new line under Today:

```toml
[connectors.sleep]
type = "command"
every = "6h"
track = false
argv = ["ssh", "mac", "some-health-cli", "last-night", "--json"]
items = "nights"
fields = { id = "date", title = "Sleep {duration}, HRV {hrv}" }
```

Got a block that works for a tool others use? Share it in a [connector issue](https://github.com/AirKyzzZ/hermes-superagent/issues/new?template=connector.yml). To write a built-in type, see [CONTRIBUTING.md](../CONTRIBUTING.md#add-a-connector).
