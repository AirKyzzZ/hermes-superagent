PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>superagent</title>
<style>
:root {
  --bg: #0f0f22; --surface: #17173a; --surface-2: #1f1f4a; --line: #2c2c5c; --fg: #ececfb; --muted: #9a9ab4;
  --accent: #6c63ff; --accent-deep: #1a10b8; --you: #ffb547; --bad: #ff6b81; --good: #3ddc97; --run: #7aa2ff;
  --font: ui-sans-serif, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  --mono: ui-monospace, "SF Mono", Menlo, Consolas, monospace;
  color-scheme: dark;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #f4f4fd; --surface: #ffffff; --surface-2: #ececfb; --line: #d9d9ef; --fg: #14142a; --muted: #5d5d7a;
    --accent: #1a10b8; --accent-deep: #0a0a9a; --you: #b86e00; --bad: #c8233f; --good: #0f8a5a; --run: #2f55d4;
    color-scheme: light;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; }
body { background: var(--bg); color: var(--fg); font: 13px/1.45 var(--font); }
.panel { max-width: 1180px; margin: 0 auto; padding: 12px 14px 28px; display: grid; gap: 14px; }
header { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
h1 { font-size: 14px; margin: 0; }
.mark { width: 22px; height: 22px; border-radius: 6px;
  background: radial-gradient(circle at 30% 30%, var(--accent) 0 2px, transparent 2.5px) 0 0/6px 6px,
  var(--accent-deep); }
.tick { color: var(--muted); font-family: var(--mono); font-size: 11.5px; }
.live { width: 7px; height: 7px; border-radius: 50%; background: var(--good); }
.live.off { background: var(--bad); }
.spacer { flex: 1; }
.pills { display: flex; gap: 6px; flex-wrap: wrap; }
.pill { font-family: var(--mono); font-size: 11px; padding: 2px 8px; border-radius: 999px; background: var(--surface-2);
  color: var(--muted); }
.pill b { color: var(--fg); font-weight: 600; }
.pill.you b { color: var(--you); }
.banner { padding: 8px 11px; border-radius: 8px; border: 1px solid var(--bad); color: var(--bad); }
.cols { display: grid; gap: 14px; }
@media (min-width: 900px) { .cols { grid-template-columns: minmax(0, 1.1fr) minmax(0, 1fr); align-items: start; } }
.col { display: grid; gap: 14px; min-width: 0; }
h2 { font-size: 11px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin: 0 0 8px;
  display: flex; gap: 8px; align-items: baseline; }
h2 .count { font-family: var(--mono); color: var(--fg); letter-spacing: 0; }
.list { display: grid; gap: 6px; }
.empty { color: var(--muted); font-size: 12px; padding: 4px 0; }
.card { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 9px 11px; display: grid;
  gap: 4px; min-width: 0; }
.card.you { border-left: 3px solid var(--you); }
.card.bad { border-left: 3px solid var(--bad); }
.row { display: flex; gap: 8px; align-items: baseline; min-width: 0; }
.grow { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.title { font-weight: 600; }
.sub { color: var(--muted); font-size: 12px; overflow-wrap: anywhere; }
.mono { font-family: var(--mono); font-size: 11.5px; }
.age { font-family: var(--mono); font-size: 11px; color: var(--muted); white-space: nowrap; }
.quote { font-size: 12.5px; padding: 6px 8px; background: var(--surface-2); border-radius: 6px;
  overflow-wrap: anywhere; }
.chip { font-family: var(--mono); font-size: 10.5px; padding: 1px 7px; border-radius: 4px; white-space: nowrap;
  border: 1px solid currentColor; }
.chip.run { color: var(--run); } .chip.you { color: var(--you); } .chip.bad { color: var(--bad); }
.chip.ok { color: var(--good); } .chip.idle { color: var(--muted); }
a { color: var(--accent); }
.flow { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 10px;
  overflow-x: auto; }
.flow svg { display: block; width: 100%; max-width: 600px; min-width: 440px; height: auto; }
.flow .label { font: 600 11px var(--font); fill: var(--fg); }
.flow .num { font: 10.5px var(--mono); fill: var(--muted); }
.flow .node { fill: var(--surface-2); stroke: var(--line); }
.flow .node.hot { stroke: var(--you); }
.flow .edge { stroke: var(--line); stroke-width: 1.5; fill: none; }
.now { display: grid; gap: 4px; margin-top: 8px; }
.now .who { font-family: var(--mono); font-size: 10.5px; color: var(--accent); width: 54px; flex: none; }
.bar { height: 4px; background: var(--surface-2); border-radius: 2px; overflow: hidden; }
.bar i { display: block; height: 100%; background: var(--run); }
.bar i.warn { background: var(--you); }
.host { font-size: 11px; color: var(--muted); font-family: var(--mono); margin: 6px 0 2px; }
table { width: 100%; border-collapse: collapse; font-size: 12px; }
td { padding: 5px 4px; border-top: 1px solid var(--line); white-space: nowrap; }
td.grow { white-space: normal; width: 100%; }
td.num { text-align: right; font-family: var(--mono); font-variant-numeric: tabular-nums; }
.dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; margin-right: 6px; vertical-align: 1px; }
.dot.ok { background: var(--good); } .dot.bad { background: var(--bad); } .dot.you { background: var(--you); }
.stats { display: grid; grid-template-columns: repeat(auto-fit, minmax(64px, 1fr)); gap: 6px; }
.stat { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 7px 8px; }
.stat b { display: block; font: 600 17px var(--mono); }
.stat span { font-size: 10.5px; color: var(--muted); }
.feed { display: grid; font-size: 12px; }
.feed div { display: grid; grid-template-columns: 44px 88px minmax(0, 1fr); gap: 8px; padding: 3px 0;
  border-top: 1px solid var(--line); }
.feed .who { color: var(--muted); font-family: var(--mono); font-size: 11px; overflow: hidden;
  text-overflow: ellipsis; }
</style>
</head>
<body>
<div class="panel" id="panel"><p class="empty">Loading the panel...</p></div>
<script src="panel.js"></script>
</body>
</html>
"""

SCRIPT = r"""
"use strict";
const token = decodeURIComponent(location.hash.slice(1));
const REFRESH_MS = 5000;
const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));

function ago(at, now) {
  if (typeof at !== "number") return "";
  const s = Math.max(0, now - at);
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}

const safeUrl = (url) => /^https?:\/\//i.test(url || "") ? url : "";
const KINDS = {escalation: "Escalated", blocked: "Worker blocked", session: "Claude Code", source: "Source",
  overdue: "Overdue"};
const TONES = {running: "run", starting: "run", queued: "idle", parked: "you", working: "run", background: "run",
  waiting: "you", stuck: "bad", limited: "bad", gone: "bad", idle: "idle", ended: "idle"};

function attentionCard(card, now) {
  const url = safeUrl(card.url);
  const ref = card.ticket || card.handle || "";
  return `<div class="card ${esc(card.tone)}">
    <div class="row"><span class="chip ${esc(card.tone)}">${esc(KINDS[card.kind] || card.kind)}</span>
      <span class="grow title">${esc(card.title)}</span><span class="age">${esc(ago(card.since, now))}</span></div>
    <div class="sub">${esc(card.detail)}${ref ? ` · <span class="mono">${esc(ref)}</span>` : ""}
      ${url ? ` · <a href="${esc(url)}" target="_blank" rel="noopener noreferrer">open</a>` : ""}</div>
    ${card.quote ? `<div class="quote">${esc(card.quote)}</div>` : ""}
  </div>`;
}

function flow(f) {
  const nodes = [
    ["Sources", [`${f.sources.polling} polling`, `${f.sources.failing} failing`], f.sources.failing > 0],
    ["Gate", [`${f.gate.admitted} let in`, `${f.gate.dropped} dropped`], false],
    ["Router", [`${f.router.dispatched} dispatched`, `${f.router.forwarded} forwarded`], false],
    ["Workers", [`${f.workers.running} running`, `${f.workers.queued} queued · ${f.workers.parked} parked`],
      f.workers.parked > 0],
    ["Watchdog", [`${f.watchdog.nudges} nudges · ${f.watchdog.restarts} restarts`,
      `${f.watchdog.escalations} escalated`], f.watchdog.escalations > 0],
    ["Relay", [`${f.relay.sent} sent`, `${f.relay.waiting} waiting`], f.relay.waiting > 0],
  ];
  const w = 150, h = 52, gapX = 18, gapY = 22, perRow = 3;
  const pos = nodes.map((_, i) => {
    const row = Math.floor(i / perRow), col = row % 2 === 0 ? i % perRow : perRow - 1 - (i % perRow);
    return [8 + col * (w + gapX), 8 + row * (h + gapY)];
  });
  let edges = "";
  for (let i = 0; i < nodes.length - 1; i++) {
    const [x1, y1] = pos[i], [x2, y2] = pos[i + 1];
    const d = y1 === y2
      ? (x2 > x1 ? `M${x1 + w} ${y1 + h / 2} H${x2}` : `M${x1} ${y1 + h / 2} H${x2 + w}`)
      : `M${x1 + w / 2} ${y1 + h} V${y2}`;
    edges += `<path class="edge" d="${d}"/>`;
  }
  const boxes = nodes.map(([name, lines, hot], i) => {
    const [x, y] = pos[i];
    return `<rect class="node${hot ? " hot" : ""}" x="${x}" y="${y}" width="${w}" height="${h}" rx="7"/>
      <text class="label" x="${x + 10}" y="${y + 17}">${esc(name)}</text>
      ${lines.map((line, n) => `<text class="num" x="${x + 10}" y="${y + 31 + n * 13}">${esc(line)}</text>`).join("")}`;
  }).join("");
  const width = 16 + perRow * w + (perRow - 1) * gapX, height = 16 + 2 * h + gapY;
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Today's flow from sources to relay">
    ${edges}${boxes}</svg>`;
}

function nowRow(entry, now) {
  const worker = entry.kind === "worker";
  const what = worker ? entry.goal : entry.project;
  const where = worker ? entry.ticket : [entry.host, entry.branch].filter(Boolean).join(" · ");
  return `<div class="row"><span class="who">${worker ? "worker" : "claude"}</span>
    <span class="grow">${esc(what)} <span class="sub mono">${esc(where)}</span></span>
    <span class="age">${esc(ago(entry.since, now))}</span></div>`;
}

function workerCard(w, now) {
  const pct = w.stall_after > 0 ? Math.round(Math.min(1, w.quiet_for / w.stall_after) * 100) : 0;
  const detail = w.state === "parked" ? w.question : w.last;
  return `<div class="card">
    <div class="row"><span class="chip ${TONES[w.state] || "idle"}">${esc(w.state)}</span>
      <span class="grow title">${esc(w.goal)}</span><span class="age">${esc(ago(w.since, now))}</span></div>
    <div class="sub"><span class="mono">${esc(w.ticket)}</span>${w.attempt ? ` · attempt ${esc(w.attempt)}` : ""}
      ${detail ? ` · ${esc(detail)}` : ""}</div>
    ${w.state === "running"
      ? `<div class="bar" title="silence against the stall threshold"><i class="${pct > 70 ? "warn" : ""}"
          style="width:${pct}%"></i></div>`
      : ""}
  </div>`;
}

function sessionsBlock(sessions, now) {
  if (!sessions.length) return `<p class="empty">No agents connector, or no session in the last 7 days.</p>`;
  const hosts = [...new Set(sessions.map((s) => s.host))];
  return `<div class="card">${hosts.map((host) => `<div class="host">${esc(host)}</div>` + sessions
    .filter((s) => s.host === host)
    .map((s) => `<div class="row"><span class="chip ${TONES[s.state] || "idle"}">${esc(s.state)}</span>
      <span class="grow">${esc(s.project)} <span class="sub mono">${esc(s.branch)}</span></span>
      <span class="age">${esc(ago(s.since, now))}</span></div>`).join("")).join("")}</div>`;
}

function sourcesBlock(sources, now) {
  if (!sources.length) return `<p class="empty">No connectors in superagent.toml.</p>`;
  return `<div class="card"><table>${sources.map((s) => {
    const tone = s.failing ? "bad" : s.warnings.length ? "you" : "ok";
    return `<tr><td><span class="dot ${tone}"></span>${esc(s.name)}</td>
      <td class="grow sub">${esc(s.warnings[0] || s.type)}</td><td class="num">${esc(s.open)}</td>
      <td class="num sub">${esc(s.last_ok ? ago(s.last_ok, now) : "never")}</td></tr>`;
  }).join("")}</table></div>`;
}

function section(title, count, body) {
  const counter = count === null ? "" : ` <span class="count">${esc(count)}</span>`;
  return `<section><h2>${esc(title)}${counter}</h2>${body}</section>`;
}

function pills(s, you) {
  const working = s.sessions.filter((x) => x.state === "working").length;
  return `<div class="pills"><span class="pill you"><b>${you}</b> need you</span>
    <span class="pill"><b>${s.flow.workers.running}</b> workers</span>
    <span class="pill"><b>${working}</b> sessions working</span></div>`;
}

function render(s) {
  const now = s.generated_at;
  const finished = typeof s.tick.finished === "number" ? s.tick.finished : null;
  const you = (s.attention || []).length;
  document.title = you ? `(${you}) superagent` : "superagent";
  const head = `<header><div class="mark" aria-hidden="true"></div><h1>superagent</h1>
    <span class="live"></span>
    <span class="tick">${finished ? `last tick ${esc(ago(finished, now))} ago` : "no tick yet"}</span>
    <span class="spacer"></span>
    ${s.missing ? "" : pills(s, you)}
    </header>${s.problem ? `<div class="banner">${esc(s.problem)}</div>` : ""}`;
  if (s.missing) {
    document.getElementById("panel").innerHTML = head + `<div class="banner">${esc(s.missing)}</div>`;
    return;
  }
  const ledger = s.ledger;
  const stats = [["new", ledger.new], ["waiting", ledger.waiting], ["due today", ledger.due_today],
    ["gone", ledger.gone], ["unseen", ledger.stale]];
  document.getElementById("panel").innerHTML = head
    + section("Now", null, `<div class="flow">${flow(s.flow)}<div class="now">${s.now.length
      ? s.now.map((entry) => nowRow(entry, now)).join("") : `<p class="empty">Nothing running.</p>`}</div></div>`)
    + `<div class="cols"><div class="col">`
    + section("Needs you", you, s.attention.length
      ? `<div class="list">${s.attention.map((card) => attentionCard(card, now)).join("")}</div>`
      : `<p class="empty">Nothing waits on you.</p>`)
    + section("Workers", s.workers.length, s.workers.length
      ? `<div class="list">${s.workers.map((w) => workerCard(w, now)).join("")}</div>`
      : `<p class="empty">No open task.</p>`)
    + `</div><div class="col">`
    + section("Claude Code sessions", s.sessions.length, sessionsBlock(s.sessions, now))
    + section("Ledger", null, `<div class="stats">${stats.map(([label, n]) =>
      `<div class="stat"><b>${esc(n)}</b><span>${esc(label)}</span></div>`).join("")}</div>`)
    + section("Sources", s.sources.length, sourcesBlock(s.sources, now))
    + section("Feed", null, `<div class="feed">${s.feed.map((e) => `<div>
      <span class="who">${esc(new Date(e.at * 1000).toTimeString().slice(0, 5))}</span>
      <span class="who">${esc(e.who)}</span><span>${esc(e.text)}</span></div>`).join("")}</div>`)
    + `</div></div>`;
}

function offline(message) {
  const dot = document.querySelector(".live");
  if (dot) dot.classList.add("off");
  let banner = document.getElementById("offline");
  if (!banner) {
    banner = document.createElement("div");
    banner.id = "offline";
    banner.className = "banner";
    document.getElementById("panel").prepend(banner);
  }
  banner.textContent = message;
}

async function refresh() {
  try {
    const response = await fetch("api/snapshot", {headers: {"X-Superagent-Token": token}, cache: "no-store"});
    if (response.status === 403) return offline("Wrong or missing token: open the URL superagent panel printed.");
    const body = await response.json();
    if (!response.ok) return offline(body.error || `The panel server answered ${response.status}.`);
    render(body);
  } catch (error) {
    offline("Can't reach the panel server. Is superagent panel still running?");
  }
}

refresh();
setInterval(refresh, REFRESH_MS);
"""
