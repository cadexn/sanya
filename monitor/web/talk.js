// The Conversations view: per channel, everything she heard, and the conversation as she is given it.

import {
  fill, state, h, icon, get, clock, ago, span, many, channel, member, hue, say, chip, empty, sameDay, dayName, memoryChip,
  preview, troubleChip,
} from "./core.js";
import { openTrace } from "./drawer.js";

const ONLY = [["", "All"], ["addressed", "Said to her"], ["own", "Her replies"], ["pending", "Waiting for memory"], ["done", "In memory"], ["skip", "Kept out"]];

let listBox, headBox, chatBox, olderBox, scrollBox;
let channels = [];
let chosen = "";
let mode = "heard"; // "heard": the room log; "told": the conversation she is given
let only = "";
let q = "";
let lines = new Map(); // id -> message or entry
let drawn = new Map(); // id -> { mark, node }: a row is built once and reused until its message changes
let more = false;
let rules = null; // the bot's memory rules, for explaining "waiting"
let asked = 0;

const key = (line) => (mode === "heard" ? line.id : "r" + line.row);

function avatar(id, name, own) {
  return h("div.avatar", { vars: { "--hue": String(hue(id)) } }, own ? "S" : (name || "?").trim().charAt(0).toUpperCase() || "?");
}

function heardRow(message) {
  const chips = [];
  if (message.own) {
    if (message.reply_to) chips.push(chip("a reply", "accent", "Click to follow the message it answered.", "reply"));
  } else if (message.addressed || message.via) {
    chips.push(chip("said to her" + (message.via ? ": " + say.via(message.via) : ""), "accent", "", "at"));
  }
  if (message.unanswered) chips.push(chip("not answered: " + say.unanswered(message.unanswered), "warn"));
  if (message.turn) {
    const turn = message.turn;
    if (turn.state === "sent") chips.push(chip(`answered in ${span(turn.ms)}`, "good", "", "check"));
    else chips.push(chip(say.state(turn.state), turn.state === "running" ? "accent" : "bad"));
    if (turn.flagged) chips.push(chip("flagged", "bad", "The security check flagged this message.", "shield"));
    // The dice let her add something unasked to this reply; whether she did is in the reply's trace.
    if ((turn.allowed || []).includes("image")) chips.push(chip("unasked image allowed", "violet", "The dice allowed an unasked image on this reply. Click to see what came of it.", "picture"));
    if ((turn.allowed || []).includes("reaction")) chips.push(chip("reaction allowed", "violet", "The dice allowed an emoji reaction on this reply. Click to see what came of it.", "smile"));
    const trouble = troubleChip(turn.problems, turn.level);
    if (trouble) chips.push(trouble);
  } else if (message.answered) chips.push(chip("answered", "good", "", "check"));
  const memory = memoryChip(message, rules);
  if (memory) chips.push(memory);
  // A kept picture shows as a small preview with what she was told it shows; any other file by its name.
  const shown = (message.files || []).filter((file) => file.shows).map((file) =>
    h("div.shotline", preview(file), h("div.muted", file.description ? "She was told: " + file.description : "Not described yet.")));
  const files = (message.files || []).filter((file) => !file.shows).map((file) => chip(file.name, "violet", "", "file"));
  if (!files.length && !shown.length && message.attachments?.length) files.push(chip(many(message.attachments.length, "file") + " (not kept)", "", message.attachments.join(", "), "file"));
  const name = message.own ? "Sanya" : member(message.author, message.name);
  return h("div.msg" + (message.own ? ".own" : "") + (message.addressed ? ".addressed" : ""),
    { onclick: () => openTrace(message.own && message.reply_to ? message.reply_to : message.id) },
    avatar(message.author, name, message.own),
    h("div",
      h("div.who", h("b", name), message.bot && !message.own ? chip("bot") : null, h("time", clock(message.at))),
      message.text ? h("div.text", message.text) : h("div.text.none", "(no words)"),
      shown,
      files.length ? h("div.chips", files) : null,
      chips.length ? h("div.chips", chips) : null));
}

function toldRow(entry) {
  const own = entry.role === "assistant";
  const name = own ? "Sanya" : member(entry.author, entry.name);
  return h("div.msg" + (own ? ".own" : ""), { onclick: entry.id ? () => openTrace(entry.id) : null },
    avatar(entry.author, name, own),
    h("div",
      h("div.who", h("b", name), h("time", clock(entry.at)),
        entry.untrusted ? chip("untrusted", "bad", "Flagged by the security check: she is told to treat it with care.", "shield") : null),
      h("div.text", entry.text),
      entry.notes ? h("div.note", "Her notes on the tools she used: " + entry.notes) : null));
}

// The chat reads top to bottom, oldest first, in its own scrolling box. A fresh channel opens at the
// newest message; a refresh leaves the reader where they are unless they are at the bottom; and
// older messages are added above without moving what is being read.
function draw(how = "refresh") {
  const fromEnd = scrollBox.scrollHeight - scrollBox.scrollTop;
  const atEnd = fromEnd - scrollBox.clientHeight < 80;
  const list = [...lines.values()].sort((a, b) => (a.at - b.at) || (mode === "told" ? a.row - b.row : 0));
  const nodes = [];
  let day = null;
  for (const line of list) {
    if (day === null || !sameDay(day, line.at)) {
      day = line.at;
      nodes.push(h("div.day", dayName(line.at)));
    }
    const mark = JSON.stringify(line);
    let kept = drawn.get(key(line));
    if (!kept || kept.mark !== mark) {
      kept = { mark, node: mode === "heard" ? heardRow(line) : toldRow(line) };
      drawn.set(key(line), kept);
    }
    nodes.push(kept.node);
  }
  if (!nodes.length) {
    nodes.push(empty(mode === "heard"
      ? (q || only ? "No message here matches." : "Nothing from this channel is in the room log. It keeps a week.")
      : "Nobody has spoken to her in this channel within the conversation's age limit.", "chat"));
  }
  fill(chatBox, ...nodes);
  fill(olderBox, more ? h("button.btn", { onclick: older }, "Show older") : null);
  if (how === "fresh" || (how === "refresh" && atEnd)) scrollBox.scrollTop = scrollBox.scrollHeight;
  else if (how === "older") scrollBox.scrollTop = scrollBox.scrollHeight - fromEnd;
}

async function fetchPage(before) {
  if (mode === "heard") {
    const data = await get("/api/room", { guild: state.guild, channel: chosen, only, q, before });
    rules = data.rules;
    return { list: data.messages, more: data.more, labels: data.labels };
  }
  const data = await get("/api/told", { guild: state.guild, channel: chosen, before });
  return { list: data.entries, more: data.more, labels: data.labels };
}

async function load(fresh = true) {
  if (!chosen) return;
  const mine = ++asked;
  try {
    const page = await fetchPage();
    if (mine !== asked) return;
    Object.assign(state.labels, page.labels || {});
    if (fresh) {
      lines = new Map();
      drawn = new Map();
      more = page.more;
    }
    for (const line of page.list) lines.set(key(line), line);
    draw(fresh ? "fresh" : "refresh");
  } catch (error) {
    if (mine === asked && fresh) fill(chatBox, h("div.banner.bad", icon("alert"), error.message));
  }
}

async function older() {
  const list = [...lines.values()];
  if (!list.length) return;
  const before = mode === "heard" ? Math.min(...list.map((line) => line.at)) : Math.min(...list.map((line) => line.row));
  try {
    const page = await fetchPage(before);
    for (const line of page.list) lines.set(key(line), line);
    more = page.more;
    draw("older");
  } catch (error) {
    fill(olderBox, h("span.faint", error.message));
  }
}

function drawHead() {
  const picked = channels.find((one) => one.id === chosen);
  const search = h("input.field", {
    type: "search", placeholder: "Find words…", value: q,
    onkeydown: (event) => {
      if (event.key === "Enter") {
        q = event.target.value.trim();
        load();
      }
    },
  });
  fill(headBox, 
    h("h2", chosen ? channel(chosen) : "Pick a channel"),
    picked ? h("span.faint", `${many(picked.messages, "message")} heard this week`) : null,
    h("div.grow"),
    h("div.seg",
      h("button" + (mode === "heard" ? ".on" : ""), { onclick: () => setMode("heard"), title: "Every message in the channel, as recorded in the room log." }, "Everything she heard"),
      h("button" + (mode === "told" ? ".on" : ""), { onclick: () => setMode("told"), title: "The channel's conversation: what was said to her and what she answered. This is what the model is given." }, "What she is given")),
    mode === "heard" ? h("div.toolbar",
      h("div.seg", ONLY.map(([value, label]) => h("button" + (only === value ? ".on" : ""), {
        onclick: () => {
          only = value;
          drawHead();
          load();
        },
      }, label))), search) : null);
}

function setMode(next) {
  mode = next;
  lines = new Map();
  drawHead();
  load();
}

function drawList() {
  const now = state.status?.now || Date.now() / 1000;
  fill(listBox, ...(channels.length ? channels.map((one) =>
    h("button" + (one.id === chosen ? ".on" : ""), { onclick: () => { location.hash = "#/talk/" + one.id; } },
      h("span.cname", channel(one.id)),
      one.waiting ? chip(String(one.waiting), "warn", `${one.waiting} waiting for memory`) : h("span"),
      h("span.csub", `${ago(one.last, now)} · ${one.messages} heard · ${one.addressed} to her`)))
    : [empty("No channel has anything recorded yet.", "chat")]));
}

async function loadChannels() {
  try {
    const data = await get("/api/channels", { guild: state.guild });
    Object.assign(state.labels, data.labels || {});
    channels = data.channels;
    if (!chosen && channels.length) {
      chosen = channels[0].id;
      drawHead();
      load();
    }
    drawList();
  } catch (error) {
    fill(listBox, h("div.banner.bad", icon("alert"), error.message));
  }
}

export function mount(root, arg) {
  chosen = /^\d+$/.test(arg || "") ? arg : "";
  lines = new Map();
  q = "";
  listBox = h("nav.card.chanlist");
  headBox = h("header");
  chatBox = h("div.chat");
  olderBox = h("div.more");
  scrollBox = h("div.chatscroll", olderBox, chatBox);
  root.append(h("div.talk", listBox, h("section.card", headBox, scrollBox)));
  drawHead();
  loadChannels();
  load();
}

export function tick(ticks) {
  if (!listBox) return;
  if (ticks % 3 === 0) load(false); // the newest page again: new messages, and marks that have changed
  if (ticks % 8 === 0) loadChannels();
}

export function unmount() {
  listBox = null;
}
