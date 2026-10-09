// The Live view: how much has passed each stage of her pipeline, and everything she does as it happens.

import {
  fill, state, h, icon, get, clock, span, bytes, ago, many, count, channel, member, server, say, chip, empty, sameDay, dayName,
  memoryChip, when, preview, troubleChip,
} from "./core.js";
import { openTrace, openBatch } from "./drawer.js";

const STAGES = [
  { key: "heard", name: "Heard", glyph: "ear", about: "Every message in the server is read and recorded in the room log." },
  { key: "addressed", name: "Said to her", glyph: "at", about: "Messages that mention her or reply to her." },
  { key: "check", name: "Security check", glyph: "shield", about: "A separate model call judges each message said to her." },
  { key: "recall", name: "Recall", glyph: "recall", about: "Facts about the speaker and the topic are fetched from memory." },
  { key: "model", name: "Model", glyph: "model", about: "Calls to the model server to write her reply." },
  { key: "tools", name: "Tools", glyph: "tool", about: "Searches, pages read, images found, memory looked up or forgotten." },
  { key: "sent", name: "Replies sent", glyph: "send", about: "Replies that went out to Discord." },
  { key: "memory", name: "To memory", glyph: "memory", aside: true, about: "Messages handed to the memory service in batches, in the background." },
];
const FILTERS = [["all", "Everything"], ["replies", "Replies"], ["heard", "Heard"], ["memory", "Memory"], ["system", "System"], ["problems", "Problems"]];
const KINDS = { replies: ["turn"], heard: ["heard", "dropped"], memory: ["memory", "picture"], system: ["system"] };
const SHOWN = 400;

let filter = "all";
let stages = {};
let feedBox, moreBox, nowBox, memoryBox, dayBox, captionBox;
let rows = new Map(); // item id -> { rev, node }
let fresh = new Set();
let remembered = null;
let rememberedAt = 0;
let exhausted = false;

const nowSeconds = () => state.status?.now || Date.now() / 1000;
const isReplying = (item) => (state.status?.replying || []).some((one) => one.turn === item.turn);
const inTrouble = (item) => Boolean(item.level);

// Which stage a turn in progress is at, judged by the last thing it logged.
function stageOf(item) {
  const last = item.last || {};
  switch (last.what) {
    case undefined: case "turn_started": case "file_stored": return "check";
    case "model_call": case "model_call_failed":
      if (last.purpose === "check") return "check";
      return last.what === "model_call" && !last.tool_calls ? "sent" : last.tool_calls ? "tools" : "model";
    case "security_check": return "recall";
    case "recall": case "recall_failed": return "model";
    case "search": case "image_search": return "tools";
    case "tool_call": return "model";
    default: return "sent";
  }
}

// ---------- wording one item

function fields(item, skip = []) {
  return Object.entries(item.fields || {}).filter(([key]) => !skip.includes(key))
    .map(([key, value]) => chip(`${say.words(key)}: ${value}`));
}

function describe(item) {
  const where = h("span.chan", channel(item.channel));
  const text = (words, gone = "(no longer in the room log)") =>
    words ? h("div.quote", words) : item.text === undefined ? h("div.quote.gone", gone) : null;
  switch (item.kind) {
    case "heard": {
      const chips = [];
      if (item.attachments) chips.push(chip(many(item.attachments, "file"), "", "", "file"));
      if (item.unanswered) chips.push(chip("not answered: " + say.unanswered(item.unanswered), "warn"));
      const memory = memoryChip(item);
      if (memory) chips.push(memory);
      return {
        glyph: item.addressed ? "at" : "ear", tone: item.addressed ? "accent" : "",
        title: [h("b", member(item.author, item.name)), item.bot ? " (bot)" : "", " in ", where,
          item.addressed ? ` · said to her` : ""],
        lines: [text(item.text)], chips, open: () => openTrace(item.message),
      };
    }
    case "dropped":
      return {
        glyph: "off", tone: "",
        title: ["Message not taken in", item.channel ? [" in ", where] : ""],
        lines: [h("div.quote", say.dropped(item.reason))], chips: [],
      };
    case "turn": {
      const chips = [];
      if (item.flagged) chips.push(chip(item.reason === "flagged" ? "flagged by the security check" : "flagged: " + say.security(item.reason), "bad", "No tools this turn; nothing of it goes to memory.", "shield"));
      if (item.facts !== undefined && item.facts !== null) chips.push(chip(many(item.facts, "fact") + " recalled", "", "", "recall"));
      if (item.calls) chips.push(chip(many(item.calls, "model call"), "", "", "model"));
      for (const name of item.tools || []) chips.push(chip(say.tool(name), "info", "", "tool"));
      for (const words of item.searched || []) chips.push(chip(`searched: ${words}`, "", "The words of a search she made for this reply.", "recall"));
      if (item.image) chips.push(chip("image posted", "violet", "", "picture"));
      if (item.sticker) chips.push(chip(item.sticker === "alone" ? "sticker only" : "sticker sent", "violet", "One of the server's own stickers.", "smile"));
      if (item.reaction) chips.push(chip(item.reaction === "alone" ? "emoji only" : "emoji reaction", "violet", "", "smile"));
      if (item.replaced) chips.push(chip("reply replaced", "warn", say.replaced(item.replaced)));
      if (item.held_back) chips.push(chip(`${item.held_back} held back from memory`, "warn"));
      if (item.messages > 1) chips.push(chip(`${item.messages} messages together`));
      const trouble = troubleChip(item.problems, item.level);
      if (trouble) chips.push(trouble);
      // A turn with no ending logged is in progress only while she is running, and only for so long.
      const open = item.state === "running";
      const running = open && isReplying(item);
      const shown = open && !running ? "interrupted" : item.state;
      const tone = running ? "accent" : shown === "sent" ? "good" : ["failed", "error"].includes(shown) ? "bad" : "warn";
      return {
        glyph: "reply", tone,
        title: ["Reply to ", h("b", member(item.author, item.name)), " in ", where,
          shown !== "sent" ? [" · ", h("span", say.state(shown))] : ""],
        lines: [text(item.text, "(their message is no longer in the room log)"),
          item.reply ? h("div.quote.her", item.reply) : null],
        chips, side: running ? span((nowSeconds() - item.at) * 1000) : span(item.ms), live: running, open: () => openTrace(item.message),
      };
    }
    case "memory": {
      const f = item.fields || {};
      switch (item.what) {
        case "memory_batch":
          return {
            glyph: "memory", tone: "info", side: span(f.ms),
            title: [h("b", many(f.messages, "message")), " from ", where, " went to memory"],
            lines: [], chips: f.dropped ? [chip(many(f.dropped, "fact") + " not kept", "warn", "Requests put to her, claims about her, or her words about others.")] : [],
          };
        case "memory_taken_back":
          return { glyph: "trash", tone: "warn", title: ["A deleted message was taken back out of memory"], lines: [h("div.quote", say.takenBack(f.outcome))], chips: [] };
        case "memory_failed":
          return { glyph: "alert", tone: "bad", title: ["The memory service did not answer"], lines: [h("div.quote", `During: ${say.words(f.job)}. ${say.words(f.error)}. She waits a few minutes and tries again.`)], chips: [] };
        case "memory_merge_asked":
          return { glyph: "memory", tone: "", title: ["Asked the memory service to merge recent facts into summaries"], lines: [], chips: [] };
        case "hearsay_expired":
          return { glyph: "clock", tone: "", title: [f.facts ? `${many(f.facts, "old unconfirmed fact")} retired` : "Checked for old hearsay: none to retire"], lines: [], chips: [] };
        case "message_deleted":
          return {
            glyph: "trash", tone: "warn", title: [many(f.messages, "message"), " deleted in ", where], chips: [],
            lines: [h("div.quote", `${f.logged ? "Removed from the room log and the conversation." : "It was not in the room log."}` +
              (f.batches ? " What memory drew from it will be taken back." : "") + (f.waiting ? " It was waiting for a reply; that is dropped." : ""))],
          };
        default:
          return { glyph: "memory", tone: item.trouble ? "bad" : "", title: [say.words(item.what)], lines: [], chips: fields(item) };
      }
    }
    case "picture": {
      const chips = [];
      if (item.bytes) chips.push(chip(bytes(item.bytes)));
      if (item.described) chips.push(chip(`described in ${span(item.ms)}`, "good"));
      else if (item.trouble) chips.push(chip("describing it failed", "bad"));
      else chips.push(chip("waiting to be described", "warn"));
      return {
        glyph: "picture", tone: "violet",
        title: [item.stored === "sent" ? "File kept from " : "Picture kept from ", where],
        lines: [item.shows && item.file ? h("div.shotline", preview({ id: item.file, name: item.name || "picture", description: item.description }),
          h("div.quote", item.description || "")) : item.description ? h("div.quote", item.description) : null], chips,
        open: item.message ? () => openTrace(item.message) : null,
      };
    }
    case "system": {
      if (item.what === "start") {
        const servers = Object.entries(item.servers || {});
        const on = servers.filter(([, what]) => what === "ready").map(([id]) => server(id) || id);
        const parts = [];
        if (item.connected) parts.push(`connected to Discord in ${span((item.connected - item.at) * 1000)}`);
        if (servers.length) parts.push(`${many(servers.length, "server")}, ${on.length ? "switched on in " + on.join(", ") : "none switched on"}`);
        const caught = Object.values(item.caught_up || {}).reduce((sum, n) => sum + (n || 0), 0);
        if (Object.keys(item.caught_up || {}).length) parts.push(`caught up on ${many(caught, "recent message")}`);
        if ((item.services || []).length) parts.push("search services: " + item.services.join(", "));
        return { glyph: "power", tone: "good", title: [h("b", "Sanya started")], lines: [h("div.quote", parts.join(" · "))], chips: [] };
      }
      const titles = {
        stopped: "Sanya stopped", reconnected: "Reconnected to Discord", status_changed: "Changed the line under her name",
        room_log_expired: `${many(item.fields?.rows, "message")} older than a week left the room log`,
      };
      return {
        glyph: item.what === "stopped" ? "power" : "clock", tone: item.what === "stopped" ? "bad" : "",
        title: [titles[item.what] || say.words(item.what)], lines: [],
        chips: titles[item.what] ? [] : fields(item),
      };
    }
    default: {
      const f = item.fields || {};
      const title = item.what === "handler_error" ? `Something failed in ${say.words(f.where || "the bot")}: ${f.error}`
        : item.what === "files_folder_large" ? `The folder of kept files has passed ${f.gb} GB`
          : say.words(item.what);
      return { glyph: "alert", tone: "bad", title: [title, item.channel ? [" in ", where] : ""], lines: [],
        chips: ["handler_error", "files_folder_large"].includes(item.what) ? [] : fields(item) };
    }
  }
}

function row(item) {
  const spec = describe(item);
  const node = h("article.row" + (spec.tone ? ".tone-" + spec.tone : "") + (spec.open ? ".opens" : "") + (fresh.has(item.id) ? ".fresh" : ""),
    { onclick: spec.open || null },
    h("time", clock(item.at)),
    h("span.glyph", icon(spec.glyph, 15)),
    h("div.body", h("div.title", spec.title), spec.lines, spec.chips.length ? h("div.chips", spec.chips) : null),
    h("div.side" + (spec.live ? ".live" : ""), spec.side || ""));
  return node;
}

// ---------- drawing

function visible() {
  const wanted = KINDS[filter];
  const list = [];
  for (const item of state.items.values()) {
    if (filter === "problems") {
      if (!inTrouble(item)) continue;
    } else if (wanted) {
      if (!wanted.includes(item.kind)) continue;
    } else if (item.kind === "heard" && item.turn && state.items.has("t:" + item.turn)) {
      continue; // shown as part of its reply turn
    }
    list.push(item);
  }
  list.sort((a, b) => b.at - a.at);
  return list;
}

function drawFeed() {
  const list = visible();
  const nodes = [];
  let day = null;
  for (const item of list.slice(0, SHOWN)) {
    if (day === null || !sameDay(day, item.at)) {
      day = item.at;
      nodes.push(h("div.day", dayName(item.at)));
    }
    let kept = rows.get(item.id);
    if (!kept || kept.rev !== item.rev || fresh.has(item.id) || item.state === "running") {
      kept = { rev: item.rev, node: row(item) };
      rows.set(item.id, kept);
    }
    nodes.push(kept.node);
  }
  if (!nodes.length) nodes.push(empty(filter === "problems" ? "Nothing has gone wrong in what the monitor has read." : "Nothing here yet.", filter === "problems" ? "check" : "clock"));
  fill(feedBox, ...nodes);
  fresh = new Set();
  fill(moreBox, exhausted || !list.length ? h("span.faint", list.length ? "That is as far back as the log goes." : "")
    : h("button.btn", { onclick: older }, "Show older"));
}

async function older() {
  const list = visible();
  if (!list.length) return;
  try {
    const data = await get("/api/feed", { guild: state.guild, before: list[list.length - 1].at, show: filter === "all" ? "" : filter });
    for (const item of data.items) if (!state.items.has(item.id)) state.items.set(item.id, item);
    exhausted = !data.more;
    drawFeed();
  } catch (error) {
    fill(moreBox, h("span.faint", error.message));
  }
}

function drawStrip(changed) {
  const stats = state.stats || {};
  const numbers = {
    heard: [stats.heard, stats.dropped ? `${stats.dropped} not taken in` : "recorded"],
    addressed: [stats.addressed, ""],
    check: [stats.turns, stats.flagged ? `${stats.flagged} flagged` : "none flagged"],
    recall: [stats.recalled, ""],
    model: [stats.calls, ""],
    tools: [stats.tools, ""],
    sent: [stats.sent, stats.median_ms ? `usually ${span(stats.median_ms)}` : stats.failed ? `${stats.failed} failed` : ""],
    memory: [stats.remembered, stats.batches ? many(stats.batches, "batch", "batches") : ""],
  };
  const working = new Set((state.status?.replying || []).map((one) => state.items.get("t:" + one.turn)).filter(Boolean).map(stageOf));
  const flashed = new Set();
  for (const item of changed || []) {
    if (item.kind === "heard") flashed.add(item.addressed ? "addressed" : "heard");
    else if (item.kind === "turn") flashed.add(item.state === "running" ? stageOf(item) : "sent");
    else if (item.kind === "memory" && item.what === "memory_batch") flashed.add("memory");
  }
  for (const stage of STAGES) {
    const box = stages[stage.key];
    const [number, note] = numbers[stage.key];
    box.number.textContent = count(number);
    box.note.textContent = note;
    box.node.classList.toggle("lit", Boolean(number));
    box.node.classList.toggle("working", working.has(stage.key));
    if (flashed.has(stage.key)) {
      box.node.classList.remove("flash");
      void box.node.offsetWidth; // start the animation again
      box.node.classList.add("flash");
    }
  }
  const active = (state.status?.servers || []).find((one) => one.id === state.guild);
  captionBox.textContent = `The last 24 hours in ${active ? active.name : "this server"}. A message passes the stages left to right; memory is filled in the background.`;
}

function latest(test) {
  let best = null;
  for (const item of state.items.values()) if (test(item) && (!best || item.at > best.at)) best = item;
  return best;
}

function drawNow() {
  const status = state.status;
  if (!status) return;
  const now = status.now;
  const replying = status.replying || [];
  let head;
  const lines = [];
  if (status.process.state === "stopped") {
    head = h("div.big", icon("power", 18), "Stopped");
    lines.push(h("div.muted", "Nothing is being read or answered. Press Start at the top to bring her online."));
  } else if (replying.length) {
    const turn = state.items.get("t:" + replying[0].turn) || {};
    const stage = STAGES.find((one) => one.key === stageOf(turn));
    head = h("div.big", icon("reply", 18), "Answering");
    lines.push(h("div", h("b", member(turn.author, turn.name)), " in ", channel(replying[0].channel)));
    lines.push(h("div.muted", `${stage ? stage.name : "Working"} · ${span((now - replying[0].at) * 1000)} so far`));
  } else {
    head = h("div.big", icon("ear", 18), "Listening");
    lines.push(h("div.muted", "Reading every message; answering when she is mentioned or replied to."));
  }
  const heard = latest((item) => item.kind === "heard");
  const sent = latest((item) => item.kind === "turn" && item.state === "sent");
  const batch = latest((item) => item.kind === "memory" && item.what === "memory_batch");
  fill(nowBox, head, ...lines,
    h("dl.facts",
      h("dt", "Last message heard"), h("dd", heard ? ago(heard.at, now) : "none yet"),
      h("dt", "Last reply"), h("dd", sent ? `${ago(sent.at, now)} · ${span(sent.ms)}` : "none yet"),
      h("dt", "Last batch to memory"), h("dd", batch ? ago(batch.at, now) : "none yet"),
      h("dt", "Connected to Discord"), h("dd", status.connected_at ? when(status.connected_at) : "—")));

  const stats = state.stats || {};
  fill(dayBox, 
    h("dl.facts",
      h("dt", "Replies"), h("dd", count(stats.sent)),
      h("dt", "Usual reply time"), h("dd", stats.median_ms ? span(stats.median_ms) : "—"),
      h("dt", "Flagged by the check"), h("dd", count(stats.flagged)),
      h("dt", "Pictures kept"), h("dd", `${count(stats.pictures)} (${count(stats.described)} described)`),
      h("dt", "Problems"), h("dd", stats.problems ? h("button.chip.bad", { onclick: () => setFilter("problems") }, count(stats.problems)) : "none"),
      h("dt", "Hiccups she got over"), h("dd", stats.hiccups ? h("button.chip.warn", { onclick: () => setFilter("problems") }, count(stats.hiccups)) : "none")));
}

function drawMemory() {
  if (!remembered) return;
  const rules = remembered.rules || {};
  const waiting = remembered.waiting || [];
  const total = waiting.reduce((sum, one) => sum + one.waiting, 0);
  const lines = waiting.slice(0, 6).map((one) => h("div",
    h("div.waitline", h("span", channel(one.channel)), h("span.muted", `${one.waiting} waiting`)),
    h("div.meter", h("i", { vars: { "--fill": `${Math.min(100, (one.waiting / (rules.batch_messages || 20)) * 100)}%` } }))));
  const stats = remembered.stats;
  fill(memoryBox, 
    total ? h("div.muted", `${many(total, "message")} waiting. A channel's batch goes at ${rules.batch_messages} messages or after ${rules.flush_minutes} quiet minutes.`)
      : h("div.muted", "Nothing is waiting: everything heard has gone to memory or is kept out."),
    ...lines,
    h("dl.facts",
      h("dt", "Bank"), h("dd", remembered.bank || "none for this server"),
      h("dt", "Facts in the bank"), h("dd", stats ? count(stats.total_nodes) : remembered.error ? "service not answering" : "—"),
      h("dt", "Batches sent"), h("dd", count((remembered.batches || []).length))));
}

async function loadMemory() {
  rememberedAt = Date.now();
  try {
    remembered = await get("/api/memory", { guild: state.guild });
    Object.assign(state.labels, remembered.labels || {});
    drawMemory();
  } catch (error) {
    memoryBox && fill(memoryBox, h("div.faint", error.message));
  }
}

function setFilter(name) {
  filter = name;
  exhausted = false;
  document.querySelectorAll(".feedcard .seg button").forEach((button) => button.classList.toggle("on", button.dataset.name === name));
  drawFeed();
}

// ---------- the view

export function mount(root, arg) {
  if (/^\d+$/.test(arg || "")) openTrace(arg); // "#/live/<message id>" opens that message
  rows = new Map();
  stages = {};
  exhausted = false;
  const strip = h("section.card.strip", STAGES.map((stage) => {
    const number = h("div.number", "0");
    const note = h("div.note");
    const node = h("div.stage" + (stage.aside ? ".aside" : ""), { title: stage.about },
      h("div.node", icon(stage.glyph, 19)), number, h("div.name", stage.name), note);
    stages[stage.key] = { node, number, note };
    return node;
  }), (captionBox = h("div.caption")));
  feedBox = h("div.feed");
  moreBox = h("div.more");
  nowBox = h("div.inner.now");
  memoryBox = h("div.inner.now");
  dayBox = h("div.inner.now");
  root.append(strip, h("div.cols",
    h("section.card.feedcard",
      h("header", h("h2", "Activity"), h("div.grow"),
        h("div.seg", FILTERS.map(([name, label]) => h("button" + (name === filter ? ".on" : ""), { "data-name": name, onclick: () => setFilter(name) }, label)))),
      feedBox, moreBox),
    h("aside.rail",
      h("section.card", h("header", h("h2", "Right now")), nowBox),
      h("section.card", h("header", h("h2", "Waiting for memory")), memoryBox),
      h("section.card", h("header", h("h2", "Last 24 hours")), dayBox))));
  update([], true);
  loadMemory();
}

export function update(changed, reset) {
  if (!feedBox) return;
  if (reset) rows = new Map();
  else fresh = new Set(changed.map((item) => item.id));
  drawStrip(reset ? [] : changed);
  if (reset || changed.length) drawFeed();
  drawNow();
  const moved = !reset && changed.some((item) => item.kind === "memory" || item.kind === "heard");
  if (moved && Date.now() - rememberedAt > 10000) loadMemory();
}

export function tick(ticks) {
  if (ticks % 15 === 0) loadMemory();
  // A turn in progress shows its running time, in the feed and beside it.
  drawNow();
  if ([...state.items.values()].some((item) => item.kind === "turn" && item.state === "running")) drawFeed();
}

export function unmount() {
  feedBox = null;
}
