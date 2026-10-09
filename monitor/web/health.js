// The Health view: is she up, are the services answering, how long replies take and where the time goes.

import { fill, state, h, svg, icon, get, when, clock, span, ago, lasting, bytes, many, count, channel, say, chip, empty } from "./core.js";
import { openTrace } from "./drawer.js";

let box;
let hours = 24;
let data = null;

const PHASES = [["check", "security check", "check"], ["recall", "recall", "recall"], ["waiting", "waiting for the model", "wait"],
  ["reading", "reading the prompt", ""], ["writing", "writing", "write"], ["tools", "tools", "tool"], ["rest", "everything else", "rest"]];
const PURPOSE = { reply: "Writing a reply", check: "Security check", describe: "Describing a picture" };

function tile(label, value, sub, tone, glyph) {
  return h("div.card.tile" + (tone ? "." + tone : ""),
    h("div.label", icon(glyph, 14), label), h("div.value" + (String(value).length > 11 ? ".small" : ""), value), h("div.sub", sub || ""));
}

function serviceTile(label, service, glyph, extra) {
  if (!service) return tile(label, "?", "", "", glyph);
  const detail = typeof service.detail === "object" && service.detail ? Object.entries(service.detail).slice(0, 2).map(([key, value]) => `${say.words(key)}: ${value}`).join(" · ") : String(service.detail || "");
  return tile(label, service.ok ? "Answering" : "Not answering", service.ok ? `${extra || detail} · answered in ${span(service.ms)}` : detail, service.ok ? "good" : "bad", glyph);
}

function chart(replies, since, until) {
  if (!replies.length) return empty("No replies in this time.", "send");
  const W = 1200, H = 250, L = 46, R = 12, T = 12, B = 26;
  const sorted = replies.map((reply) => reply.ms).sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];
  const top = Math.max(2000, sorted[Math.min(sorted.length - 1, Math.floor(sorted.length * 0.98))] * 1.15);
  const x = (at) => L + ((at - since) / Math.max(1, until - since)) * (W - L - R);
  const y = (ms) => T + (1 - Math.min(ms, top) / top) * (H - T - B);
  const parts = [];
  for (let step = 0; step <= 4; step += 1) {
    const ms = (top / 4) * step;
    parts.push(svg("line", { class: "grid", x1: L, x2: W - R, y1: y(ms), y2: y(ms) }));
    parts.push(svg("text", { x: L - 6, y: y(ms) + 4, "text-anchor": "end" }, span(ms)));
  }
  for (let step = 0; step <= 4; step += 1) {
    const at = since + ((until - since) / 4) * step;
    parts.push(svg("text", { x: x(at), y: H - 6, "text-anchor": step === 0 ? "start" : step === 4 ? "end" : "middle" },
      until - since > 86400 * 1.5 ? when(at) : clock(at, false)));
  }
  parts.push(svg("line", { class: "mid", x1: L, x2: W - R, y1: y(median), y2: y(median) }));
  for (const reply of replies) {
    const kind = reply.state === "failed" ? "failed" : reply.flagged ? "flagged" : reply.tools ? "tools" : "";
    const dot = svg("circle", { cx: x(reply.at), cy: y(reply.ms), r: 4, class: kind },
      svg("title", {}, `${when(reply.at)} · ${span(reply.ms)}${reply.tools ? " · used tools" : ""}${reply.flagged ? " · flagged" : ""}${reply.state === "failed" ? " · model failed" : ""}`));
    dot.style.cursor = "pointer";
    dot.addEventListener("click", () => openTrace(reply.turn.split("-")[0]));
    parts.push(dot);
  }
  return [svg("svg", { class: "chart", viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": "Reply times" }, parts),
    h("div.legend", h("span", h("i.write"), "plain reply"), h("span", h("i"), "used tools"), h("span", h("i.tool"), "flagged"), h("span", h("i.bad"), "model failed"),
      h("span", `dashed line: the usual time, ${span(median)}`), h("span.faint", "click a dot to follow that reply"))];
}

function phases(spent) {
  const total = Object.values(spent || {}).reduce((sum, value) => sum + value, 0);
  if (!total) return empty("No replies in this time.", "clock");
  return [
    h("div.bars", PHASES.map(([key, label, kind]) => h("i" + (kind ? "." + kind : ""), { title: `${label}: ${span(spent[key])}`, vars: { "--wide": `${(spent[key] / total) * 100}%` } }))),
    h("div.legend", PHASES.filter(([key]) => spent[key] > 0).map(([key, label, kind]) => h("span", h("i" + (kind ? "." + kind : "")), `${label} ${span(spent[key])}`))),
    h("div.muted", `An average reply takes ${span(total)}. "Waiting for the model" is time her request spent in the model server's queue or on the way: the server answers one request at a time and is shared.`)];
}

function problemLine(item) {
  const f = item.fields || {};
  let text;
  if (item.kind === "turn") {
    const wrong = (item.steps || []).filter((step) => step.trouble).map((step) => say.words(step.what) + (step.error ? ` (${say.words(step.error)})` : ""));
    text = `Reply turn in ${channel(item.channel)}: ${wrong.length ? wrong.join(", ") : say.state(item.state)}`;
  } else if (item.what === "handler_error") text = `Something failed in ${say.words(f.where || "the bot")}: ${f.error}`;
  else if (item.what === "memory_failed") text = `Memory service did not answer during ${say.words(f.job)}: ${say.words(f.error)}`;
  else if (item.kind === "picture") text = `A picture from ${channel(item.channel)} could not be described`;
  else text = say.words(item.what) + " " + Object.entries(f).map(([key, value]) => `${say.words(key)}: ${value}`).join(" · ");
  const hiccup = item.level === "hiccup";
  return h("tr" + (item.kind === "turn" ? ".opens" : "") + (hiccup ? ".quiet" : ""), { onclick: item.kind === "turn" ? () => openTrace(item.message) : null },
    h("td.mono", when(item.at)),
    h("td", hiccup ? chip("got over", "warn", "It went wrong on the way and she carried on: not counted as a problem.") : chip("failed", "bad")),
    h("td", text));
}

function draw() {
  if (!box || !data) return;
  const status = data.status;
  const now = status.now;
  const process = status.process;
  const log = data.log;
  const reply = log.calls?.reply;
  const running = process.state === "running";
  const restarts = log.starts.length;
  const serious = log.problems.filter((item) => item.level === "serious").length;
  const hiccups = log.problems.length - serious;
  const usage = data.usage;
  const storage = data.storage;
  const database = data.database;

  const tiles = h("div.tiles",
    tile("Sanya", running ? "Running" : process.state === "other_account" ? "Other account" : "Stopped",
      running ? `up ${status.started_at ? lasting(now - status.started_at) : "?"} · process ${process.pid}` +
        (restarts > 1 ? ` · started ${restarts} times in this period` : "")
        : process.state === "other_account" ? "running under another Windows account; this one cannot see or stop it" : "not reading or answering",
      running ? "good" : process.state === "other_account" ? "warn" : "bad", "power"),
    tile("Discord", status.connected_at && running ? "Connected" : "—", status.connected_at ? `since ${when(status.connected_at)} · last event ${ago(status.last_event, now)}` : "no connection logged", status.connected_at && running ? "good" : "", "pulse"),
    serviceTile("Model server", data.services.model, "model", reply ? `replies usually ${span(reply.median_ms)}` : ""),
    serviceTile("Memory service", data.services.memory, "memory"),
    tile("Replies", count(log.replies.length), log.replies.length ? `in the last ${hours >= 48 ? hours / 24 + " days" : hours + " hours"}` : "none in this period", "", "send"),
    tile("Problems", count(serious), (serious ? "listed below" : "nothing failed outright") + (hiccups ? ` · ${many(hiccups, "hiccup")} she got over` : ""),
      serious ? "bad" : "good", "alert"));

  const calls = Object.entries(log.calls || {});
  const callTable = calls.length ? h("table.list",
    h("thead", h("tr", h("th", "Model call"), h("th.num", "Calls"), h("th.num", "Failed"), h("th.num", "Usual"), h("th.num", "Slow"), h("th.num", "Waiting"), h("th.num", "Tokens read"), h("th.num", "Written"))),
    h("tbody", calls.map(([purpose, call]) => h("tr",
      h("td", PURPOSE[purpose] || say.words(purpose)), h("td.num", count(call.count)),
      h("td.num", call.failed ? chip(String(call.failed), "bad") : "0"),
      h("td.num", span(call.median_ms)), h("td.num", span(call.slow_ms)), h("td.num", span(call.median_wait_ms)),
      h("td.num", count(call.prompt_tokens)), h("td.num", count(call.written_tokens))))))
    : empty("No model calls in this time.", "model");

  const searches = usage.services.length ? usage.services.map((service) => {
    const share = service.cap ? Math.min(100, (service.used / service.cap) * 100) : 0;
    return h("div",
      h("div.waitline", h("span", service.name), h("span.muted", service.cap ? `${count(service.used)} of ${count(service.cap)}` : `${count(service.used)} (no cap set)`)),
      h("div.meter" + (share >= 100 ? ".bad" : share >= 80 ? ".warn" : ""), h("i", { vars: { "--fill": `${share}%` } })));
  }) : [h("div.faint", "No searches counted this month.")];
  if (!usage.read) searches.push(h("div.faint", "The counts are read between replies; she is answering just now."));

  const stored = h("dl.facts",
    h("dt", "Kept files"), h("dd", `${bytes(storage.files.bytes)} in ${many(storage.files.count, "file")}`),
    storage.databases.map((one) => [h("dt", `Database ${(status.servers.find((s) => s.id === one.guild) || {}).name || one.guild}`), h("dd", bytes(one.bytes))]),
    h("dt", "Logs"), h("dd", `${bytes(storage.logs.bytes)} in ${many(storage.logs.count, "file")}`));
  const held = database ? h("dl.facts",
    h("dt", "Room log (a week)"), h("dd", `${count(database.room_log)} messages`),
    h("dt", "  waiting for memory"), h("dd", count(database.memory.pending)),
    h("dt", "  in memory"), h("dd", count(database.memory.done)),
    h("dt", "  kept out"), h("dd", count(database.memory.skip)),
    h("dt", "Conversation (30 days)"), h("dd", `${count(database.history)} entries`),
    h("dt", "Kept files on record"), h("dd", `${count(database.files)}${database.undescribed ? ` (${database.undescribed} pictures not described yet)` : ""}`),
    h("dt", "Members seen"), h("dd", count(database.member_names)),
    h("dt", "Batches to memory"), h("dd", count(database.memory_batches))) : h("div.faint", "This server has no database yet.");

  fill(box, 
    h("div.toolbar", h("div.grow"), h("div.seg", [[24, "24 hours"], [72, "3 days"], [168, "7 days"]].map(([value, label]) =>
      h("button" + (hours === value ? ".on" : ""), { onclick: () => { hours = value; load(); } }, label)))),
    tiles,
    data.console ? h("section.card", h("header", h("h2", "Her last words before stopping"), h("span.faint", "logs\\console.err.log")), h("div.inner.console", data.console)) : null,
    h("section.card", h("header", h("h2", "Reply times")), h("div.inner", chart(log.replies, now - hours * 3600, now))),
    h("div.halves",
      h("section.card", h("header", h("h2", "Where a reply's time goes")), h("div.inner.stack", phases(log.phases))),
      h("section.card", h("header", h("h2", "Searches this month"), h("span.faint", usage.month)), h("div.inner.now", searches))),
    h("section.card", h("header", h("h2", "Model calls")), callTable),
    h("div.halves",
      h("section.card", h("header", h("h2", "Stored on disk")), h("div.inner", stored)),
      h("section.card", h("header", h("h2", "In this server's database")), h("div.inner", held))),
    h("section.card", h("header", h("h2", "What went wrong"), h("span.faint", log.problems.length ? "newest first" : "")),
      log.problems.length ? h("table.list", h("tbody", log.problems.map(problemLine))) : empty("Nothing in this period.", "check")));
}

async function load() {
  try {
    data = await get("/api/health", { guild: state.guild, hours });
    draw();
  } catch (error) {
    box && fill(box, h("div.banner.bad", icon("alert"), error.message));
  }
}

export function mount(root) {
  box = h("div.stack");
  root.append(box);
  box.append(empty("Reading…"));
  load();
}

export function tick(ticks) {
  if (box && ticks % 8 === 0) load();
}

export function unmount() {
  box = null;
}
