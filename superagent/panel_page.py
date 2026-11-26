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
.tick.you { color: var(--you); } .tick.bad { color: var(--bad); }
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
.icon { width: 14px; height: 14px; flex: none; stroke: currentColor; fill: none; stroke-width: 2;
  stroke-linecap: round; stroke-linejoin: round; vertical-align: -2px; }
.clickable { cursor: pointer; }
.clickable:hover { border-color: var(--accent); }
.clickable:focus-visible, button:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
.projects { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
.project { background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 11px;
  display: grid; gap: 8px; text-align: left; color: var(--fg); font: inherit; }
.project.you { border-color: color-mix(in srgb, var(--you) 60%, var(--line)); }
.mono-badge { width: 30px; height: 30px; border-radius: 8px; display: grid; place-items: center; flex: none;
  font: 700 12px var(--mono); color: #fff; background: hsl(var(--hue) 55% 42%);
  box-shadow: inset 0 0 0 1px hsl(var(--hue) 60% 60% / .5); }
.counts { display: flex; gap: 10px; flex-wrap: wrap; color: var(--muted); font: 11.5px var(--mono); }
.counts span { display: inline-flex; gap: 4px; align-items: center; }
.counts .hot { color: var(--you); } .counts .run { color: var(--run); }
.strip { display: flex; height: 5px; border-radius: 3px; overflow: hidden; background: var(--surface-2); gap: 1px; }
.strip i { display: block; height: 100%; }
.strip .run { background: var(--run); } .strip .you { background: var(--you); } .strip .bad { background: var(--bad); }
.strip .idle { background: var(--muted); } .strip .item { background: var(--line); }
.badge { font: 600 10.5px var(--mono); color: var(--bg); background: var(--you); border-radius: 999px;
  padding: 1px 7px; }
.layer { position: fixed; inset: 0; display: flex; justify-content: flex-end; background: rgb(0 0 0 / .45); }
.sheet { background: var(--bg); width: min(560px, 100%); height: 100%; overflow-y: auto; padding: 14px;
  display: grid; gap: 14px; align-content: start; border-left: 1px solid var(--line); }
.sheet header { display: block; position: sticky; top: 0; background: var(--bg); padding-bottom: 6px; z-index: 1; }
.sheet h1 { font-size: 15px; }
.iconbtn { background: var(--surface-2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px;
  padding: 4px 7px; display: inline-flex; align-items: center; gap: 4px; font: 600 11.5px var(--font);
  cursor: pointer; }
.timeline { display: grid; gap: 0; }
.event { display: grid; grid-template-columns: 52px 92px minmax(0, 1fr); gap: 8px; padding: 6px 0;
  border-top: 1px solid var(--line); font-size: 12.5px; }
.event .kind { font: 11px var(--mono); color: var(--accent); }
.event .when { font: 11px var(--mono); color: var(--muted); }
.event .text { overflow-wrap: anywhere; white-space: pre-wrap; }
details { background: var(--surface); border: 1px solid var(--line); border-radius: 8px; padding: 8px 10px; }
summary { cursor: pointer; font-weight: 600; font-size: 12px; }
pre { font: 11.5px/1.5 var(--mono); white-space: pre-wrap; overflow-wrap: anywhere; margin: 8px 0 0; color: var(--fg);
  max-height: 420px; overflow-y: auto; }
.actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
.btn { background: var(--surface-2); color: var(--fg); border: 1px solid var(--line); border-radius: 6px;
  padding: 5px 10px; font: 600 12px var(--font); cursor: pointer; }
.btn:hover:not(:disabled) { border-color: var(--accent); }
.btn:disabled { opacity: .5; cursor: default; }
.btn.primary { background: var(--accent); border-color: var(--accent); color: #fff; }
.btn.danger { color: var(--bad); border-color: var(--bad); }
textarea { width: 100%; min-height: 84px; resize: vertical; background: var(--surface-2); color: var(--fg);
  border: 1px solid var(--line); border-radius: 6px; padding: 7px 8px; font: 12.5px/1.45 var(--font); }
textarea:focus-visible { outline: 2px solid var(--accent); outline-offset: 1px; }
.notice { margin: 0; padding: 6px 10px; border-radius: 6px; border: 1px solid currentColor; font-size: 12px; }
.notice.ok { color: var(--good); } .notice.bad { color: var(--bad); }
</style>
</head>
<body>
<svg width="0" height="0" style="position:absolute" aria-hidden="true">
<symbol id="i-worker" viewBox="0 0 24 24"><rect x="5" y="5" width="14" height="14" rx="2"/><path d="M9 9h6v6H9z"/>
<path d="M9 2v3M15 2v3M9 19v3M15 19v3M2 9h3M2 15h3M19 9h3M19 15h3"/></symbol>
<symbol id="i-session" viewBox="0 0 24 24"><rect x="3" y="4" width="18" height="16" rx="2"/>
<path d="m7 9 3 3-3 3M13 15h4"/>
</symbol>
<symbol id="i-pr" viewBox="0 0 24 24"><circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/>
<circle cx="18" cy="18" r="2.5"/><path d="M6 8.5v7M18 15.5V9a3 3 0 0 0-3-3h-4m2-2-2 2 2 2"/></symbol>
<symbol id="i-item" viewBox="0 0 24 24"><circle cx="12" cy="12" r="8"/><circle cx="12" cy="12" r="2"/></symbol>
<symbol id="i-alert" viewBox="0 0 24 24"><path d="M12 3 2 20h20L12 3z"/><path d="M12 10v4M12 17v.5"/></symbol>
<symbol id="i-close" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6 6 18"/></symbol>
<symbol id="i-back" viewBox="0 0 24 24"><path d="m14 6-6 6 6 6"/></symbol>
</svg>
<div class="panel" id="panel"><p class="empty">Loading the panel...</p></div>
<script src="panel.js"></script>
</body>
</html>
"""

SCRIPT = r"""
"use strict";
const token = decodeURIComponent(location.hash.slice(1));
const REFRESH_MS = 5000;
const NOTICE_MS = 8000;
const ANSWER_LIMIT = 2000;
const state = {snapshot: null, project: null, ticket: null, detail: null, open: {brief: false, log: true},
  drafts: {}, confirm: null, busy: false, notice: null};
const esc = (value) => String(value ?? "").replace(/[&<>"']/g,
  (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c]));
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"></use></svg>`;
const safeUrl = (url) => /^https?:\/\//i.test(url || "") ? url : "";
const link = (url, label) => safeUrl(url)
  ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>` : "";

function ago(at, now) {
  if (typeof at !== "number") return "";
  const s = Math.max(0, now - at);
  if (s < 60) return `${Math.round(s)} s`;
  if (s < 3600) return `${Math.round(s / 60)} min`;
  if (s < 172800) return `${Math.round(s / 3600)} h`;
  return `${Math.round(s / 86400)} d`;
}

const clock = (at) => typeof at === "number" ? new Date(at * 1000).toTimeString().slice(0, 5) : "--:--";
const KINDS = {escalation: "Escalated", blocked: "Worker blocked", session: "Claude Code", source: "Source",
  overdue: "Overdue"};
const TONES = {running: "run", starting: "run", queued: "idle", parked: "you", working: "run", background: "run",
  waiting: "you", stuck: "bad", limited: "bad", gone: "bad", idle: "idle", ended: "idle"};
const tone = (value) => Object.hasOwn(TONES, value) ? TONES[value] : "idle";
const TICK_TONES = {warn: "you", fail: "bad"};

function hue(name) {
  let h = 0;
  for (const c of String(name)) h = (h * 31 + c.codePointAt(0)) % 360;
  return h;
}

function monogram(name) {
  const letters = String(name).replace(/[^\p{L}\p{N}]+/gu, " ").trim().split(" ").map((w) => w[0]).join("");
  const label = (letters || "?").slice(0, 2).toUpperCase();
  return `<span class="mono-badge" style="--hue: ${hue(name)}">${esc(label)}</span>`;
}

function section(title, count, body) {
  const counter = count === null ? "" : ` <span class="count">${esc(count)}</span>`;
  return `<section><h2>${esc(title)}${counter}</h2>${body}</section>`;
}

const empty = (text) => `<p class="empty">${esc(text)}</p>`;
const actionsOn = () => Boolean(state.snapshot && state.snapshot.actions);
const button = (act, label, cls, value = "") => `<button type="button" class="btn ${esc(cls)}"
  data-act="${esc(act)}" data-value="${esc(value)}"${state.busy ? " disabled" : ""}>${esc(label)}</button>`;
const notice = () => state.notice
  ? `<p class="notice ${esc(state.notice.tone)}">${esc(state.notice.text)}</p>` : "";

function cardAction(card) {
  if (!actionsOn()) return "";
  if (card.kind === "source" && card.held) return button("accept", "Accept", "primary", card.connector);
  if (card.kind === "overdue" && card.handle) return button("snooze", "Snooze until tomorrow", "", card.handle);
  return "";
}

function attentionCard(card, now) {
  const ref = card.ticket || card.handle || "";
  const open = card.ticket ? ` clickable" role="button" tabindex="0" data-ticket="${esc(card.ticket)}` : "";
  const action = cardAction(card);
  return `<div class="card ${esc(card.tone)}${open}">
    <div class="row"><span class="chip ${esc(card.tone)}">${esc(KINDS[card.kind] || card.kind)}</span>
      <span class="grow title">${esc(card.title)}</span><span class="age">${esc(ago(card.since, now))}</span></div>
    <div class="sub">${esc(card.detail)}${ref ? ` · <span class="mono">${esc(ref)}</span>` : ""}
      ${card.url ? ` · ${link(card.url, "open")}` : ""}</div>
    ${card.quote ? `<div class="quote">${esc(card.quote)}</div>` : ""}
    ${action ? `<div class="actions">${action}</div>` : ""}
  </div>`;
}

function projectStats(project, s) {
  const workers = s.workers.filter((w) => project.workers.includes(w.ticket));
  const sessions = s.sessions.filter((x) => project.sessions.includes(x.handle));
  const prs = project.items.filter((i) => i.kind === "pr");
  return {workers, sessions, prs, waiting: project.waiting};
}

function projectCard(project, s, now) {
  const {workers, sessions, prs, waiting} = projectStats(project, s);
  const running = workers.filter((w) => w.state === "running").length
    + sessions.filter((x) => x.state === "working").length;
  const segments = [...workers.map((w) => tone(w.state)), ...sessions.map((x) => tone(x.state)),
    ...project.items.slice(0, 12).map(() => "item")];
  return `<button type="button" class="project clickable${waiting ? " you" : ""}" data-project="${esc(project.name)}">
    <div class="row">${monogram(project.name)}<span class="grow title">${esc(project.name)}</span>
      ${waiting ? `<span class="badge">${esc(waiting)}</span>` : ""}</div>
    <div class="counts">
      <span class="${running ? "run" : ""}" title="working now">${icon("worker")}${esc(running)}</span>
      <span class="${waiting ? "hot" : ""}" title="waiting on you">${icon("alert")}${esc(waiting)}</span>
      <span title="Claude Code sessions">${icon("session")}${esc(sessions.length)}</span>
      <span title="open pull requests">${icon("pr")}${esc(prs.length)}</span>
      <span title="open items">${icon("item")}${esc(project.items.length)}</span>
    </div>
    <div class="strip">${segments.map((cls) => `<i class="${esc(cls)}" style="flex: 1"></i>`).join("")}</div>
    <div class="sub">${project.last ? `active ${esc(ago(project.last, now))} ago` : "no activity yet"}</div>
  </button>`;
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
  const where = worker ? [entry.project, entry.ticket].filter(Boolean).join(" · ")
    : [entry.host, entry.branch].filter(Boolean).join(" · ");
  const open = worker ? ` clickable" role="button" tabindex="0" data-ticket="${esc(entry.ticket)}` : "";
  return `<div class="row${open}"><span class="who">${worker ? "worker" : "claude"}</span>
    <span class="grow">${esc(what)} <span class="sub mono">${esc(where)}</span></span>
    <span class="age">${esc(ago(entry.since, now))}</span></div>`;
}

function workerCard(w, now) {
  const pct = w.stall_after > 0 ? Math.round(Math.min(1, w.quiet_for / w.stall_after) * 100) : 0;
  const detail = w.state === "parked" ? w.question : w.last;
  return `<div class="card clickable" role="button" tabindex="0" data-ticket="${esc(w.ticket)}">
    <div class="row"><span class="chip ${esc(tone(w.state))}">${esc(w.state)}</span>
      <span class="grow title">${esc(w.goal)}</span><span class="age">${esc(ago(w.since, now))}</span></div>
    <div class="sub"><span class="mono">${esc(w.ticket)}</span>${w.project ? ` · ${esc(w.project)}` : ""}
      ${w.attempt ? ` · attempt ${esc(w.attempt)}` : ""}${detail ? ` · ${esc(detail)}` : ""}</div>
    ${w.state === "running"
      ? `<div class="bar" title="silence against the stall threshold"><i class="${pct > 70 ? "warn" : ""}"
          style="width:${esc(pct)}%"></i></div>`
      : ""}
  </div>`;
}

function sessionRows(sessions, now) {
  return sessions.map((x) => `<div class="row"><span class="chip ${esc(tone(x.state))}">${esc(x.state)}</span>
    <span class="grow">${esc(x.project)} <span class="sub mono">${esc(x.branch)}</span></span>
    <span class="age">${esc(x.host)} · ${esc(ago(x.since, now))}</span></div>`).join("");
}

function sourcesBlock(sources, now) {
  if (!sources.length) return empty("No connectors in superagent.toml.");
  return `<div class="card"><table>${sources.map((x) => {
    const level = x.failing ? "bad" : x.warnings.length ? "you" : "ok";
    return `<tr><td><span class="dot ${level}"></span>${esc(x.name)}</td>
      <td class="grow sub">${esc(x.warnings[0] || x.type)}</td><td class="num">${esc(x.open)}</td>
      <td class="num sub">${esc(x.last_ok ? ago(x.last_ok, now) : "never")}</td></tr>`;
  }).join("")}</table></div>`;
}

function itemRows(items, now) {
  return items.map((i) => `<div class="row">${icon(i.kind === "pr" ? "pr" : "item")}
    <span class="grow">${safeUrl(i.url) ? link(i.url, i.title) : esc(i.title)}</span>
    <span class="age">${i.due ? `due ${esc(i.due)} · ` : ""}${esc(i.state)} · ${esc(ago(i.since, now))}</span></div>`)
    .join("");
}

function header(s, you) {
  const now = s.generated_at;
  const finished = typeof s.tick.finished === "number" && s.tick.status === "ok" ? s.tick.finished : null;
  const tickTone = Object.hasOwn(TICK_TONES, s.tick.status) ? TICK_TONES[s.tick.status] : "";
  const tickText = finished ? `last tick ${ago(finished, now)} ago` : s.tick.text || "no tick yet";
  const pills = s.missing ? "" : `<div class="pills"><span class="pill you"><b>${esc(you)}</b> need you</span>
    <span class="pill"><b>${esc(s.flow.workers.running)}</b> workers</span>
    <span class="pill"><b>${esc(s.sessions.filter((x) => x.state === "working").length)}</b> sessions working</span>
    </div>`;
  return `<header><div class="mark" aria-hidden="true"></div><h1>superagent</h1><span class="live"></span>
    <span class="tick ${esc(tickTone)}">${esc(tickText)}</span>
    <span class="spacer"></span>${pills}</header>${s.problem ? `<div class="banner">${esc(s.problem)}</div>` : ""}
    ${notice()}`;
}

function overview(s) {
  const now = s.generated_at;
  const ledger = s.ledger;
  const stats = [["new", ledger.new], ["waiting", ledger.waiting], ["due today", ledger.due_today],
    ["gone", ledger.gone], ["unseen", ledger.stale]];
  return section("Needs you", s.attention.length, s.attention.length
      ? `<div class="list">${s.attention.map((card) => attentionCard(card, now)).join("")}</div>`
      : empty("Nothing waits on you."))
    + section("Projects", s.projects.length, s.projects.length
      ? `<div class="projects">${s.projects.map((p) => projectCard(p, s, now)).join("")}</div>`
      : empty("No project yet: workers, sessions and repo items show up here."))
    + section("Now", null, `<div class="flow">${flow(s.flow)}<div class="now">${s.now.length
      ? s.now.map((entry) => nowRow(entry, now)).join("") : empty("Nothing running.")}</div></div>`)
    + `<div class="cols"><div class="col">`
    + section("Workers", s.workers.length, s.workers.length
      ? `<div class="list">${s.workers.map((w) => workerCard(w, now)).join("")}</div>` : empty("No open task."))
    + section("Claude Code sessions", s.sessions.length, s.sessions.length
      ? `<div class="card">${sessionRows(s.sessions, now)}</div>`
      : empty("No agents connector, or no session in the last 7 days."))
    + `</div><div class="col">`
    + section("Ledger", null, `<div class="stats">${stats.map(([label, n]) =>
      `<div class="stat"><b>${esc(n)}</b><span>${esc(label)}</span></div>`).join("")}</div>`)
    + section("Sources", s.sources.length, sourcesBlock(s.sources, now))
    + section("Feed", null, `<div class="feed">${s.feed.map((e) => `<div><span class="who">${esc(clock(e.at))}</span>
      <span class="who">${esc(e.who)}</span><span>${esc(e.text)}</span></div>`).join("")}</div>`)
    + `</div></div>`;
}

function sheet(title, lead, body, back) {
  return `<div class="layer" data-close="${back ? "ticket" : "project"}"><div class="sheet" role="dialog"
    aria-modal="true" aria-label="${esc(title)}"><header><div class="row">${lead}
    <span class="grow title">${esc(title)}</span>
    <button type="button" class="iconbtn" data-close="${back ? "ticket" : "project"}">
      ${icon(back ? "back" : "close")}${back ? "Back" : "Close"}</button></div></header>
    ${notice()}${body}</div></div>`;
}

function projectSheet(s) {
  const project = s.projects.find((p) => p.name === state.project);
  if (!project) return "";
  const now = s.generated_at;
  const {workers, sessions, prs} = projectStats(project, s);
  const blockers = workers.filter((w) => w.state === "parked");
  const others = project.items.filter((i) => i.kind !== "pr");
  return sheet(project.name, monogram(project.name),
    (blockers.length ? section("Blockers", blockers.length, `<div class="list">${blockers.map((w) => `
      <div class="card you clickable" role="button" tabindex="0" data-ticket="${esc(w.ticket)}">
      <div class="title">${esc(w.goal)}</div><div class="quote">${esc(w.question)}</div></div>`).join("")}</div>`) : "")
    + section("Workers", workers.length, workers.length
      ? `<div class="list">${workers.map((w) => workerCard(w, now)).join("")}</div>` : empty("No open task here."))
    + section("Claude Code sessions", sessions.length, sessions.length
      ? `<div class="card">${sessionRows(sessions, now)}</div>` : empty("No session here."))
    + section("Pull requests", prs.length, prs.length ? `<div class="card">${itemRows(prs, now)}</div>`
      : empty("No open pull request in the ledger."))
    + section("Issues and tasks", others.length, others.length
      ? `<div class="card">${itemRows(others, now)}</div>` : empty("Nothing else open.")), false);
}

function folded(key, title, text) {
  return `<details data-key="${esc(key)}"${state.open[key] ? " open" : ""}><summary>${esc(title)}</summary>
    <pre data-key="${esc(key)}">${esc(text)}</pre></details>`;
}

function ticketActions(d) {
  if (!actionsOn() || !["open", "waiting"].includes(d.status)) return "";
  const answer = d.status === "waiting" ? `<textarea data-answer="${esc(d.ticket)}" maxlength="${ANSWER_LIMIT}"
    rows="4" aria-label="Your answer" placeholder="Your answer, the worker resumes with it"></textarea>
    <div class="actions">${button("answer", "Send answer", "primary")}</div>` : "";
  const cancel = state.confirm === d.ticket
    ? `<span class="sub">Stop this task and its worker?</span>${button("cancel", "Yes, cancel it", "danger")}
      ${button("keep", "Keep it", "")}`
    : button("ask-cancel", "Cancel task", "danger");
  return section("Actions", null, `<div class="card">${answer}<div class="actions">${cancel}</div></div>`);
}

function ticketSheet(s) {
  const d = state.detail;
  const now = s.generated_at;
  if (!d || d.ticket !== state.ticket) return sheet(state.ticket, icon("worker"), empty("Loading..."), true);
  if (d.error) return sheet(state.ticket, icon("worker"), `<div class="banner">${esc(d.error)}</div>`, true);
  const attempts = d.attempts.map((a) => `attempt ${esc(a.attempt)} ${esc(a.status)}`).join(" · ");
  return sheet(d.goal, icon("worker"),
    `<div class="sub">${[`<span class="mono">${esc(d.ticket)}</span>`, esc(d.status), esc(d.project), esc(d.channel),
      attempts].filter(Boolean).join(" · ")}</div>`
    + section("Conversation", d.timeline.length, `<div class="timeline">${d.timeline.map((e) => `<div class="event">
      <span class="when" title="${esc(ago(e.at, now))} ago">${esc(clock(e.at))}</span>
      <span class="kind">${esc(e.kind)}</span><span class="text">${esc(e.text)}</span></div>`).join("")}</div>`)
    + ticketActions(d)
    + folded("brief", "Brief the worker started from", d.brief || "No brief on disk.")
    + folded("log", "Worker output, last lines", d.log || "No output yet."), true);
}

function render() {
  const s = state.snapshot;
  if (!s) return;
  const you = (s.attention || []).length;
  document.title = you ? `(${you}) superagent` : "superagent";
  const panel = document.getElementById("panel");
  if (s.missing) {
    panel.innerHTML = header(s, you) + `<div class="banner">${esc(s.missing)}</div>`;
    return;
  }
  const scrolls = new Map([...document.querySelectorAll(".layer[data-close] .sheet, pre[data-key]")]
    .map((el) => [el.closest(".layer").dataset.close + (el.dataset.key || ""), el.scrollTop]));
  const typing = document.activeElement && document.activeElement.matches("textarea[data-answer]");
  const caret = typing ? [document.activeElement.selectionStart, document.activeElement.selectionEnd] : null;
  panel.innerHTML = header(s, you) + overview(s)
    + (state.project ? projectSheet(s) : "") + (state.ticket ? ticketSheet(s) : "");
  const box = document.querySelector("textarea[data-answer]");
  if (box) {
    box.value = state.drafts[box.dataset.answer] || "";
    if (caret) {
      box.focus({preventScroll: true});
      box.setSelectionRange(caret[0], caret[1]);
    }
  }
  for (const el of document.querySelectorAll(".layer[data-close] .sheet, pre[data-key]")) {
    const key = el.closest(".layer").dataset.close + (el.dataset.key || "");
    if (scrolls.has(key)) el.scrollTop = scrolls.get(key);
  }
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

async function getJson(path) {
  const response = await fetch(path, {headers: {"X-Superagent-Token": token}, cache: "no-store"});
  if (response.status === 403) throw new Error("Wrong or missing token: open the URL superagent panel printed.");
  const body = await response.json();
  if (!response.ok) throw new Error(body.error || `The panel server answered ${response.status}.`);
  return body;
}

async function postJson(path, body) {
  const response = await fetch(path, {method: "POST", cache: "no-store", body: JSON.stringify(body),
    headers: {"X-Superagent-Token": token, "Content-Type": "application/json"}});
  const reply = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(reply.error || `The panel server answered ${response.status}.`);
  return reply;
}

async function loadTicket() {
  if (!state.ticket) return;
  const id = state.ticket;
  try {
    state.detail = await getJson(`api/ticket?id=${encodeURIComponent(id)}`);
  } catch (error) {
    state.detail = {ticket: id, error: error.message};
  }
}

async function refresh() {
  try {
    state.snapshot = await getJson("api/snapshot");
    await loadTicket();
  } catch (error) {
    return offline(error instanceof TypeError
      ? "Can't reach the panel server. Is superagent panel still running?" : error.message);
  }
  try {
    render();
  } catch (error) {
    offline(`The panel couldn't draw this snapshot: ${error.message}`);
  }
}

function say(text, tone) {
  const current = {text, tone};
  state.notice = current;
  setTimeout(() => {
    if (state.notice !== current) return;
    state.notice = null;
    render();
  }, NOTICE_MS);
}

function tomorrow() {
  const day = new Date();
  day.setDate(day.getDate() + 1);
  day.setHours(0, 0, 0, 0);
  return day.toISOString();
}

const DONE = {answer: "Sent", cancel: "Cancelled", accept: "Accepted, it applies on the next poll",
  snooze: "Snoozed until tomorrow"};

async function act(body, done = () => {}) {
  if (state.busy) return;
  state.busy = true;
  render();
  try {
    await postJson("api/action", body);
    done();
    say(DONE[body.action], "ok");
  } catch (error) {
    say(error instanceof TypeError ? "Can't reach the panel server." : error.message, "bad");
  } finally {
    state.busy = false;
  }
  render();
  await refresh();
}

async function onAction(target) {
  const kind = target.dataset.act;
  const ticket = state.ticket;
  if (kind === "ask-cancel" || kind === "keep") {
    state.confirm = kind === "ask-cancel" ? ticket : null;
    render();
  } else if (kind === "cancel") {
    state.confirm = null;
    await act({action: "cancel", ticket});
  } else if (kind === "answer") {
    const text = (state.drafts[ticket] || "").trim();
    if (!text) {
      say("Write an answer first.", "bad");
      return render();
    }
    await act({action: "answer", ticket, text}, () => delete state.drafts[ticket]);
  } else if (kind === "accept") {
    await act({action: "accept", connector: target.dataset.value});
  } else if (kind === "snooze") {
    await act({action: "snooze", item: target.dataset.value, until: tomorrow()});
  }
}

function close(layer) {
  if (layer === "ticket") {
    state.ticket = null;
    state.detail = null;
    state.confirm = null;
  } else {
    state.project = null;
  }
  render();
}

document.addEventListener("click", async (event) => {
  const action = event.target.closest("[data-act]");
  if (action) return onAction(action);
  const target = event.target.closest("[data-ticket], [data-project], [data-close]");
  if (!target || event.target.closest("a")) return;
  if (target.dataset.close && (target.classList.contains("iconbtn") || event.target === target)) {
    close(target.dataset.close);
  } else if (target.dataset.ticket) {
    state.ticket = target.dataset.ticket;
    state.detail = null;
    state.confirm = null;
    render();
    await loadTicket();
    render();
  } else if (target.dataset.project) {
    state.project = target.dataset.project;
    render();
  }
});

document.addEventListener("input", (event) => {
  if (event.target.matches("textarea[data-answer]")) state.drafts[event.target.dataset.answer] = event.target.value;
});

document.addEventListener("toggle", (event) => {
  if (event.target.matches("details[data-key]")) state.open[event.target.dataset.key] = event.target.open;
}, true);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && (state.ticket || state.project)) close(state.ticket ? "ticket" : "project");
  if (event.key === "Enter" && event.target.matches("[role=button]")) event.target.click();
});

refresh();
setInterval(refresh, REFRESH_MS);
"""
