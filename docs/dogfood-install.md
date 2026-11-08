# Shadow install

The steps that put hermes-superagent next to an existing Hermes setup in shadow mode. It watches every Claude Code session on each machine, keeps a ledger, and posts to its own Discord channels. It changes nothing else: no role in the live profile, no edits to Hermes config, and no messages anywhere except the channels named below. The [dogfood design](dogfood-design.md) explains why.

## 0. Check what's there

Get these from the owner before starting, and stop if any is missing: the Discord channel ids for alerts, the daily brief, worker briefs, new tasks, the agents and a test channel; the ssh alias of each machine to watch; and, if he wants one, the uptime check's ping URL. Below they appear as `ALERTS_CHANNEL_ID`, `BRIEF_CHANNEL_ID`, `BRIEFS_CHANNEL_ID`, `TASKS_CHANNEL_ID`, `AGENTS_CHANNEL_ID`, `TEST_CHANNEL_ID` and `ALIAS`. Run `doctor`, `briefs` and the tick loop with a 600-second terminal timeout: they poll every source or call the model several times.

```bash
hermes --version                      # 0.22.7 or newer
ssh -o BatchMode=yes ALIAS python3 --version   # once per alias: python3 3.8 or newer
gh auth status                        # for the github connector
glab auth status                      # for the gitlab connector, if used
td auth status                        # for the todoist connector, see below
hermes send -q --to discord:ALERTS_CHANNEL_ID -- "superagent install test"
```

The last line proves the default profile can post to the alerts channel. Every message the plugin sends goes through that same command.

## 1. Install the plugin in the live profile

```bash
hermes plugins install AirKyzzZ/hermes-superagent --enable || hermes plugins update hermes-superagent
mkdir -p ~/superagent ~/.superagent
```

The install takes the repo's `main`, so it has every connector named below. If the plugin was installed earlier, `install` stops and `update` brings it to `main`.

Leave `role` unset. With no role, the plugin's hooks do nothing and its tools stay hidden, which CI checks on real Hermes. Everything below runs through `hermes superagent --home ~/superagent ...`, so Hermes's own config is never touched.

## 2. Write `~/superagent/superagent.toml`

Create the Discord channels first and use their ids. `setup` writes the local part, sending through the `hermes` that runs it, and never overwrites an existing file:

```bash
hermes superagent --home ~/superagent setup --alerts discord:ALERTS_CHANNEL_ID \
  --brief discord:BRIEF_CHANNEL_ID --agents-channel discord:AGENTS_CHANNEL_ID
```

Then add the heartbeat and the other machines by hand, so the file looks like this. Leave out any machine you don't have.

```toml
[outbound]
argv = ["hermes", "send", "-q", "--to", "{channel}", "--", "{text}"]

[channels]
alerts = "discord:ALERTS_CHANNEL_ID"
brief = "discord:BRIEF_CHANNEL_ID"
briefs = "discord:BRIEFS_CHANNEL_ID"

[brief]
hour = 8
timezone = "Europe/Berlin"

[heartbeat]
argv = ["curl", "-fsS", "-m", "10", "https://hc-ping.com/YOUR-CHECK-ID"]

[connectors.github]
type = "github"
every = "10m"

[connectors.gitlab]
type = "gitlab"
every = "10m"

[connectors.todoist]
type = "todoist"
every = "5m"
announce_new = true
channel = "discord:TASKS_CHANNEL_ID"

[connectors.agents]
type = "agents"
every = "90s"
channel = "discord:AGENTS_CHANNEL_ID"

[connectors.agents-mac]
type = "agents"
host = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "mac"]
every = "90s"
channel = "discord:AGENTS_CHANNEL_ID"

[connectors.agents-pc]
type = "agents"
host = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "pc"]
every = "90s"
channel = "discord:AGENTS_CHANNEL_ID"
```

`mac` and `pc` are ssh host aliases from `~/.ssh/config`. For the PC, point the alias at WSL, where Claude Code runs. Each host needs `python3`, and needs `git` if you want the board to show branches.

The `github` connector reads issues and pull requests assigned to you, asking for your review, or mentioning you, through the `gh` login of the user that runs the timer. `gh auth status` has to pass for that user, under the timer too: a token kept in a desktop keyring may need `DBUS_SESSION_BUS_ADDRESS`, which the connector passes through when it's set. If the account belongs to an organisation with SSO, authorize the token for it, or that organisation's items fail the poll.

The `gitlab` connector does the same through `glab`: issues and merge requests assigned to you, merge requests asking for your review, and pending todos, which is where GitLab puts mentions. Each todo is its own item and closes once it's marked done in GitLab. Marking most todos done at once shrinks the snapshot by more than half, so nothing is marked gone until `hermes superagent --home ~/superagent ledger accept gitlab`, and an alert says so. `glab auth status` has to pass for the timer's user too, and a token kept in the keyring needs `DBUS_SESSION_BUS_ADDRESS` the same way. Leave the section out if the owner doesn't use GitLab.

The `todoist` connector reads every open task in every project, subtasks included, through Doist's `td` CLI, the one already on the VPS: `td task list --all --json --full` and `td project list --all --json`. Nothing filters by date, so a task added by the owner or another agent in any project is in the next snapshot, and `announce_new` posts the tasks that appeared since the last poll to its channel. The connector only ever runs `task list`, `project list` and `task view`, so it never changes a task. Use the `td` login already there:

```bash
td auth status                        # must pass; don't log in again
td task list --all --json --full | head -c 300
```

Don't run `td auth login` again on the VPS: other agents add tasks through the same `td`, and a new login, read-only or not, replaces their token. If `td` isn't installed, install it with `npm install -g @doist/todoist-cli` (Node 24 or newer) and give it the owner's token through `TODOIST_API_TOKEN`, which `td` reads first and the connector passes through. Add `trusted = true` to the section only if nobody else, no agent and no integration writes in those projects, since worker briefs would treat that text as the owner's own words.

Mail and WhatsApp use the tools already on the VPS, one `gmail` block per mailbox:

```toml
[connectors.gmail-personal]
type = "gmail"
account = "OWNER_ADDRESS"
every = "10m"

[connectors.whatsapp]
type = "whatsapp"
chats = ["CONTACT_NUMBER@s.whatsapp.net"]
every = "5m"
```

`gog` runs with `--readonly` and `wacli` with `--read-only` on every call. The WhatsApp connector also checks `wacli doctor` each poll, so `wacli sync --follow` has to keep running; with a timer running `wacli sync --once` instead, add `sync = "once"`. Catching up on more than half of a source at once, such as archiving most of the inbox, holds the snapshot until `ledger accept` with that connector's name, as for GitLab. Both connectors were built from each tool's public source with synthetic test data, in `tests/fixtures/*.synthetic.json`. Before turning them on, check one real run of each against them, scrubbed of names and numbers:

```bash
gog --readonly --json --account OWNER_ADDRESS gmail search 'in:inbox' --all --count --max 500 | head -c 600
wacli --read-only --json doctor
wacli --read-only --json chats list --limit 500 | grep -c CONTACT_NUMBER   # each listed JID must appear
wacli --read-only --json messages list --chat CONTACT_NUMBER@s.whatsapp.net --limit 20
hermes superagent --home ~/superagent doctor
```

If a field is named differently, `doctor` says which thread or chat it couldn't read. Send the scrubbed output so the fixtures can be replaced with the real shape.

Google Calendar uses the same `gog`, whose login needs Calendar access. One block per account:

```toml
[connectors.calendar]
type = "gcal"
account = "OWNER_ADDRESS"
every = "30m"
```

The brief's Today section then lists today and the next two days, and nothing gets an owner or closes. Set `days` for a longer window. It reads the calendars ticked in Google Calendar, one call each, and `calendars = ["primary"]` narrows that. This one was also built from `gog`'s public source with synthetic data, so check a real run first:

```bash
gog --readonly --json --account OWNER_ADDRESS calendar calendars --all | head -c 600
gog --readonly --json --account OWNER_ADDRESS calendar events primary --days 3 --all-pages --max 250 | head -c 600
```

CodexBar shows coding-agent quota in the brief. `setup` adds it when `codexbar` is on the `PATH`. On a machine where it isn't, reach the one that has it:

```toml
[connectors.codexbar]
type = "codexbar"
every = "1h"
codexbar = ["ssh", "mac", "/opt/homebrew/bin/codexbar"]
```

It reads the providers enabled in CodexBar. A provider that reports an error, such as an expired login, fails the poll and shows under Sources while the others still record. Fix it, or list the working ones with `providers = ["claude", "grok"]`.

Anki needs the AnkiConnect add-on and Anki open. From another machine, send the request through ssh, since AnkiConnect only listens locally:

```toml
[connectors.anki]
type = "anki"
every = "1h"
curl = ["ssh", "mac", "curl"]
quiet_after = "3d"
```

A closed or busy Anki records nothing and isn't a failure, and `quiet_after` says so when it has stayed that way. Only `findCards`, `getDecks` and `getNumCardsReviewedToday` are called, since AnkiConnect's `getDeckStats` creates the decks it's asked about.

The timer below has no ssh agent. So give each alias a dedicated key with no passphrase through `IdentityFile`, and ssh to each host once by hand so its host key is saved. On a Mac, ssh sessions can't read folders that macOS protects, such as `~/Downloads`, so branches of repos kept there show as blank. Grant `sshd` Full Disk Access if you want them.

The agents channel gets one line when a session has been waiting, stuck, rate-limited or gone for two polls in a row. The line holds the project, the state and a handle, never the session's text. Set `notify_detail = true` on a connector to add the question, with anything token-shaped redacted.

The heartbeat is optional but recommended. Any uptime checker that alerts when pings stop will do, and shadow mode is meant to run with one for its first week.

## 3. Install the hook on each machine

The hook is a small dependency-free script that Claude Code runs on session events. It appends one line per event to `~/.superagent/sessions/` on that machine:
- the event and the time;
- the working directory;
- the `claude` process id;
- the pane identifiers (`TMUX_PANE` and the cmux workspace, surface, tab and panel ids);
- for events that ask something, up to 500 characters of the question.

It never prints anything, never fails a session, and copies no other environment variable.

Ask the owner before each machine. Then, for a remote host:

```bash
hermes superagent agents --hook-script | ssh mac 'mkdir -p ~/.superagent && cat > ~/.superagent/hook.py'
hermes superagent agents --hook-settings
```

Merge the printed `hooks` block into `~/.claude/settings.json` on that machine, next to any hooks already there rather than over them. Show the owner the diff first. On the VPS itself, write the script locally with `hermes superagent agents --hook-script > ~/.superagent/hook.py`.

Claude Code's settings watcher normally picks up the new hooks in sessions that are already running, so they show up after their next event. A session that still doesn't appear needs `/hooks` opened once, or a restart. The hook redacts token-shaped text, never stores a command, and deletes its own logs after 30 days.

## 4. Check it

```bash
hermes superagent --home ~/superagent doctor
hermes superagent --home ~/superagent tick
hermes superagent --home ~/superagent agents
```

`doctor` prints one line per check and exits non-zero when one fails. It runs each connector's poll once without recording anything, skipped while a tick holds the lock, so a host whose hook never ran, an ssh alias that doesn't connect or a `gh` login that lapsed shows up here with its error. The tick prints JSON. `errors` must be empty. `warnings` lists any source that fails, for example a host ssh can't reach. `agents` prints the board, sessions that need someone first.

## 5. Schedule the tick

```ini
# ~/.config/systemd/user/superagent-tick.service
[Unit]
Description=hermes-superagent tick

[Service]
Type=oneshot
ExecStart=/full/path/to/hermes superagent --home %h/superagent tick
TimeoutStartSec=660
Environment=PATH=/full/path/to/hermes/bin:/full/path/to/td/bin:/full/path/to/node/bin:/usr/local/bin:/usr/bin:/bin
```

```ini
# ~/.config/systemd/user/superagent-tick.timer
[Unit]
Description=Run the hermes-superagent tick every 2 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=2min

[Install]
WantedBy=timers.target
```

```bash
systemctl --user daemon-reload
systemctl --user enable --now superagent-tick.timer
loginctl enable-linger "$USER"
```

Fill the `PATH` with `dirname "$(command -v hermes)"`, `dirname "$(command -v td)"` and `dirname "$(command -v node)"`: `td` starts with `#!/usr/bin/env node`, so the timer needs both. If `td` runs on a token rather than its own login, put `TODOIST_API_TOKEN=...` in a file only this user can read (`chmod 600`) and add `EnvironmentFile=` with its path to the service, so the token isn't in a world-readable unit. Run step 4 once through the unit too, with `systemctl --user start superagent-tick.service`, then read `journalctl --user -u superagent-tick`. ssh agents and tool credentials can work from a shell and still fail under the timer's smaller environment.

## 6. The live-model run

CI runs everything on real Hermes with a fake model. This run swaps in the real one. Do the shadow part in the live profile first; it uses the default profile's model through `ctx.llm` and changes nothing else.

```bash
H="hermes superagent --home $HOME/superagent"
$H doctor                                  # every line ok or warn, exit code 0
$H tick                                    # "errors": []; if it says "skipped", the timer holds the lock, run it again
$H ledger list | head -20                  # items from github, gitlab, todoist and the agents
$H brief --send                            # queues the daily brief; the next tick posts it
$H ledger list --connector github | head -5
$H briefs it-XXXX --send                   # a handle from the line above; three real model calls, queued for the briefs channel
```

`--send` only queues a message; the timer's next tick, within 2 minutes, posts it. The brief's first line reads `READY`, `NEEDS APPROVAL` or `NOT READY`. An issue written by someone else should come out `NEEDS APPROVAL`, and anything the lint found is listed at the end.

Then the full path, worker and watchdog with the real model, in two throwaway profiles. The live profile and the gateway stay as they are, because `ingest` hands the event straight to the gate:

```bash
for p in sa-worker sa-watchdog; do
  hermes profile create "$p"
  hermes -p "$p" plugins install AirKyzzZ/hermes-superagent --enable
done
mkdir -p ~/superagent-live
cat > ~/superagent-live/superagent.toml <<'EOF'
[sources.live]
policy = "explicit_only"
allow = ["owner"]

[worker]
argv = ["hermes", "-p", "sa-worker", "chat", "-Q", "--query-file", "{brief}", "-t", "superagent"]

[outbound]
argv = ["hermes", "-p", "default", "send", "-q", "--to", "{channel}", "--", "{text}"]
EOF
```

The outbound names the default profile because `hermes -p sa-watchdog` hands its own profile to every command it starts, and only the default profile holds the Discord token.

`hermes profile create` copies the model choice but not always the keys. Check each profile can reach the model first:

```bash
hermes -p sa-worker chat -Q -q "reply ok"
hermes -p sa-watchdog chat -Q -q "reply ok"
```

If both answer, copy nothing. Otherwise list the key names in the default profile, without their values, and copy each one the model needs through Hermes, which keeps the value out of the conversation and out of shell redirects that its approval check stops:

```bash
grep -oE '^[A-Z0-9_]+=' ~/.hermes/.env
hermes -p sa-worker config set PROVIDER_API_KEY "$(grep -m1 '^PROVIDER_API_KEY=' ~/.hermes/.env | cut -d= -f2-)"
hermes -p sa-watchdog config set PROVIDER_API_KEY "$(grep -m1 '^PROVIDER_API_KEY=' ~/.hermes/.env | cut -d= -f2-)"
```

with the real key name in place of `PROVIDER_API_KEY`. Then give each profile its role the same way:

```bash
hermes -p sa-worker config set plugins.entries.hermes-superagent.settings.role worker
hermes -p sa-worker config set plugins.entries.hermes-superagent.settings.home "$HOME/superagent-live"
hermes -p sa-watchdog config set plugins.entries.hermes-superagent.settings.role watchdog
hermes -p sa-watchdog config set plugins.entries.hermes-superagent.settings.home "$HOME/superagent-live"
```

If `config set` refuses those paths, edit each profile's `config.yaml` with the file patch tool rather than a shell redirect, adding `entries:` under the `plugins:` key that `plugins install --enable` already wrote, next to `enabled`:

```yaml
plugins:
  enabled:
    - hermes-superagent
  entries:
    hermes-superagent:
      settings:
        role: worker
        home: /home/OWNER/superagent-live
```

Then start one task and let the watchdog relay it:

```bash
hermes -p sa-watchdog superagent ingest --source live --thread live-1 --actor owner \
  --channel discord:TEST_CHANNEL_ID --addressed \
  --text "Reply with the three largest planets, then report ready with them as the summary"
hermes -p sa-watchdog superagent status        # one open ticket, its worker running
for i in $(seq 1 20); do
  hermes -p sa-watchdog superagent tick        # judges new reports, relays results
  hermes -p sa-watchdog superagent status | grep -q '"tickets": \[\]' && break
  sleep 30
done
```

The watchdog's model judges a worker's reports while that worker is still running, so a very fast worker can finish before the judge is needed. The relay and the worker's own model calls happen either way.

The test channel should get `Done [Reply with the three largest planets … · tk-…]: …`, and the tick that relayed it lists the same text under `delivered`. The worker has only the `superagent` toolset, so it can't touch the machine. Send back the output of `doctor`, the loop's last tick, the brief's first line and that `delivered` line. To remove the test, run `hermes profile delete sa-worker --yes` and `hermes profile delete sa-watchdog --yes`, then remove `~/superagent-live`, which Hermes may ask the owner to approve.

The router's own model only runs on chat messages through the gateway. That needs a router profile bound to a chat channel, which the README's "Run it with Hermes" covers, and isn't needed for this check.

## Relaunch, once shadow mode looks right

Relaunch stays off until the owner turns it on per machine, because it types into terminals. Add to a host's agents connector:

```toml
relaunch = true
tmux = "/full/path/to/tmux"
cmux = "/Applications/cmux.app/Contents/Resources/bin/cmux"
```

`tmux` and `cmux` need full paths when ssh's `PATH` doesn't have them, which is the case for Homebrew on a Mac. A session stopped on an API error gets one `continue` after 10 minutes, and one stopped by a rate limit gets it once Claude Code says its auto-resume went stale. Every other case is posted to the agents channel as needing the owner, with the reason. A session whose process died is never restarted by the plugin, since cmux restores its own sessions and a closed tab looks the same as a crash: its line carries the `claude --resume` command instead.

## Undo

```bash
systemctl --user disable --now superagent-tick.timer
hermes plugins disable hermes-superagent
```

Then remove the hooks block from `~/.claude/settings.json` on each machine. The ledger in `~/superagent` stays until you delete it.
