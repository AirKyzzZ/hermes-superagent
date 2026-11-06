# Shadow install

The steps that put hermes-superagent next to an existing Hermes setup in shadow mode. It watches every Claude Code session on each machine, keeps a ledger, and posts to its own Discord channels. It changes nothing else: no role in the live profile, no edits to Hermes config, and no messages anywhere except the channels named below. The [dogfood design](dogfood-design.md) explains why.

## 1. Install the plugin in the live profile

```bash
hermes plugins install AirKyzzZ/hermes-superagent --enable
mkdir -p ~/superagent ~/.superagent
```

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

[brief]
hour = 8
timezone = "Europe/Berlin"

[heartbeat]
argv = ["curl", "-fsS", "-m", "10", "https://hc-ping.com/YOUR-CHECK-ID"]

[connectors.github]
type = "github"
every = "10m"

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

`doctor` prints one line per check and exits non-zero when one fails. It polls each connector once without recording anything, so a host whose hook never ran, an ssh alias that doesn't connect or a `gh` login that lapsed shows up here with its error. The tick prints JSON. `errors` must be empty. `warnings` lists any source that fails, for example a host ssh can't reach. `agents` prints the board, sessions that need someone first.

## 5. Schedule the tick

```ini
# ~/.config/systemd/user/superagent-tick.service
[Unit]
Description=hermes-superagent tick

[Service]
Type=oneshot
ExecStart=/full/path/to/hermes superagent --home %h/superagent tick
TimeoutStartSec=660
Environment=PATH=/full/path/to/hermes/bin:/usr/local/bin:/usr/bin:/bin
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

Run step 4 once through the unit too, with `systemctl --user start superagent-tick.service`, then read `journalctl --user -u superagent-tick`. ssh agents and tool credentials can work from a shell and still fail under the timer's smaller environment.

## Undo

```bash
systemctl --user disable --now superagent-tick.timer
hermes plugins disable hermes-superagent
```

Then remove the hooks block from `~/.claude/settings.json` on each machine. The ledger in `~/superagent` stays until you delete it.
