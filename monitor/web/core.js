// Shared pieces: building page elements, asking the monitor for data, and wording things plainly.
// Everything a member wrote is put on the page as text, never as markup.

const KEY = document.querySelector('meta[name="monitor-key"]').content;

export const state = {
  guild: localStorage.getItem("sanya.monitor.guild") || "",
  status: null,
  stats: null,
  labels: { channels: {}, members: {}, her: "" },
  items: new Map(),
  head: null,
  offline: false,
};

// ---------- building elements

export function h(spec, props, ...kids) {
  const [tag, ...classes] = spec.split(".");
  const node = document.createElement(tag || "div");
  if (classes.length) node.className = classes.join(" ");
  if (props !== null && props !== undefined && (props instanceof Node || typeof props !== "object" || Array.isArray(props))) {
    kids.unshift(props);
    props = null;
  }
  for (const [name, value] of Object.entries(props || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (name.startsWith("on")) node.addEventListener(name.slice(2), value);
    else if (name === "class") node.className += (node.className ? " " : "") + value;
    else if (name === "vars") for (const [key, set] of Object.entries(value)) node.style.setProperty(key, set);
    else if (name in node && name !== "list") node[name] = value;
    else node.setAttribute(name, value === true ? "" : value);
  }
  add(node, kids);
  return node;
}

// Replace what a node holds. Unlike replaceChildren, a missing child is skipped and not written out as "null".
export function fill(node, ...kids) {
  node.replaceChildren();
  add(node, kids);
  return node;
}

function add(node, kids) {
  for (const kid of kids) {
    if (kid === null || kid === undefined || kid === false) continue;
    if (Array.isArray(kid)) add(node, kid);
    else node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  }
}

const SVG = "http://www.w3.org/2000/svg";
const ICONS = {
  ear: ["M3 18v-6a9 9 0 0 1 18 0v6", "M21 19a2 2 0 0 1-2 2h-1a2 2 0 0 1-2-2v-3a2 2 0 0 1 2-2h3zM3 19a2 2 0 0 0 2 2h1a2 2 0 0 0 2-2v-3a2 2 0 0 0-2-2H3z"],
  reply: ["M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"],
  at: ["M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8z", "M16 8v5a3 3 0 0 0 6 0v-1a10 10 0 1 0-3.92 7.94"],
  shield: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"],
  memory: ["M12 2c4.97 0 9 1.34 9 3s-4.03 3-9 3-9-1.34-9-3 4.03-3 9-3z", "M21 12c0 1.66-4 3-9 3s-9-1.34-9-3", "M3 5v14c0 1.66 4 3 9 3s9-1.34 9-3V5"],
  recall: ["M11 3a8 8 0 1 0 0 16a8 8 0 1 0 0-16z", "M21 21l-4.35-4.35"],
  model: ["M6 4h12a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z", "M9 9h6v6H9z", "M9 1v3M15 1v3M9 20v3M15 20v3M20 9h3M20 14h3M1 9h3M1 14h3"],
  tool: ["M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z"],
  send: ["M22 2L11 13", "M22 2l-7 20-4-9-9-4 20-7z"],
  picture: ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z", "M8.5 7a1.5 1.5 0 1 0 0 3a1.5 1.5 0 1 0 0-3z", "M21 15l-5-5L5 21"],
  power: ["M18.36 6.64a9 9 0 1 1-12.73 0", "M12 2v10"],
  alert: ["M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z", "M12 9v4", "M12 17h.01"],
  clock: ["M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20z", "M12 6v6l4 2"],
  check: ["M20 6L9 17l-5-5"],
  x: ["M18 6L6 18", "M6 6l12 12"],
  off: ["M4.93 4.93l14.14 14.14", "M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20z"],
  sun: ["M12 7a5 5 0 1 0 0 10a5 5 0 1 0 0-10z", "M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"],
  moon: ["M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"],
  play: ["M5 3l14 9-14 9V3z"],
  stop: ["M5 3h14a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2z"],
  right: ["M9 18l6-6-6-6"],
  down: ["M6 9l6 6 6-6"],
  file: ["M13 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z", "M13 2v7h7"],
  pulse: ["M22 12h-4l-3 9L9 3l-3 9H2"],
  chat: ["M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2", "M9 3a4 4 0 1 0 0 8a4 4 0 1 0 0-8z", "M23 21v-2a4 4 0 0 0-3-3.87", "M16 3.13a4 4 0 0 1 0 7.75"],
  trash: ["M3 6h18", "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6", "M10 11v6M14 11v6", "M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"],
  smile: ["M12 2a10 10 0 1 0 0 20a10 10 0 1 0 0-20z", "M8 14s1.5 2 4 2 4-2 4-2", "M9 9h.01M15 9h.01"],
};

export function icon(name, size = 16) {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("width", size);
  svg.setAttribute("height", size);
  svg.setAttribute("class", "icon");
  svg.setAttribute("aria-hidden", "true");
  for (const d of ICONS[name] || ICONS.clock) {
    const path = document.createElementNS(SVG, "path");
    path.setAttribute("d", d);
    svg.append(path);
  }
  return svg;
}

export function svg(tag, attrs, ...kids) {
  const node = document.createElementNS(SVG, tag);
  for (const [name, value] of Object.entries(attrs || {})) node.setAttribute(name, value);
  for (const kid of kids.flat()) if (kid) node.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
  return node;
}

// ---------- asking the monitor

export class Failed extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function ask(method, path, params, body) {
  const url = new URL(path, location.origin);
  for (const [name, value] of Object.entries(params || {})) {
    if (value !== undefined && value !== null && value !== "") url.searchParams.set(name, value);
  }
  const options = { method, headers: { "X-Monitor-Key": KEY }, cache: "no-store" };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(url, options);
  } catch (error) {
    throw new Failed("The monitor is not answering.", 0);
  }
  if (response.status === 403) {
    // The monitor was started again and gave out a new key: fetch the page afresh, once.
    const last = Number(sessionStorage.getItem("sanya.monitor.reloaded") || 0);
    if (Date.now() - last > 10000) {
      sessionStorage.setItem("sanya.monitor.reloaded", String(Date.now()));
      location.reload();
    }
    throw new Failed("The monitor was restarted. Reload this page.", 403);
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Failed(data.error || `The monitor answered ${response.status}.`, response.status);
  return data;
}

export const get = (path, params) => ask("GET", path, params);
export const post = (path, body) => ask("POST", path, null, body);

const pictures = new Map(); // "server:file:size" -> a promise of the picture's address within this page

// Fetched once and kept: the lists are redrawn every few seconds, and a picture must not be fetched each time.
export function pictureUrl(fileId, small = false) {
  const key = `${state.guild}:${fileId}:${small ? "small" : "full"}`;
  if (!pictures.has(key)) {
    const address = `/api/file?guild=${state.guild}&id=${fileId}${small ? "&size=small" : ""}`;
    pictures.set(key, fetch(address, { headers: { "X-Monitor-Key": KEY } })
      .then((response) => {
        if (!response.ok) throw new Failed("The picture is no longer on disk.", response.status);
        return response.blob();
      })
      .then((blob) => URL.createObjectURL(blob))
      .catch((error) => {
        pictures.delete(key);
        throw error;
      }));
  }
  return pictures.get(key);
}

// A preview is fetched when it comes near the part of the page being looked at, not before.
const nearby = new IntersectionObserver((entries) => {
  for (const entry of entries) {
    if (entry.isIntersecting) {
      nearby.unobserve(entry.target);
      entry.target.dispatchEvent(new Event("near"));
    }
  }
}, { rootMargin: "400px" });

// A small preview of a kept picture. Clicking it shows the picture whole.
export function preview(file) {
  const image = h("img", { alt: file.name });
  const box = h("button.preview", {
    title: file.description || file.name,
    onclick: (event) => {
      event.stopPropagation();
      showPicture(file);
    },
  }, image);
  const load = async () => {
    try {
      image.src = await pictureUrl(file.id, true);
      box.classList.add("loaded");
    } catch (error) {
      box.replaceWith(chip(`${file.name}: no longer on disk`, "muted", "", "picture"));
    }
  };
  // One already fetched is shown at once; any other waits until it comes near.
  if (pictures.has(`${state.guild}:${file.id}:small`)) load();
  else {
    box.addEventListener("near", load);
    nearby.observe(box);
  }
  return box;
}

export async function showPicture(file) {
  const close = () => {
    cover.remove();
    document.removeEventListener("keydown", key, true);
  };
  const key = (event) => {
    if (event.key === "Escape") {
      event.stopImmediatePropagation(); // close the picture, not the panel behind it
      close();
    }
  };
  const image = h("img.full", { alt: file.name });
  const cover = h("div.cover.dark", { onclick: close },
    h("figure", image, h("figcaption", file.description ? `${file.name} · ${file.description}` : file.name)));
  document.addEventListener("keydown", key, true);
  document.body.append(cover);
  try {
    image.src = await pictureUrl(file.id);
  } catch (error) {
    fill(cover, h("div.modal", h("p", error.message)));
  }
}

// ---------- times, sizes, names

const pad = (number) => String(number).padStart(2, "0");

export function clock(at, seconds = true) {
  const d = new Date(at * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}${seconds ? ":" + pad(d.getSeconds()) : ""}`;
}

export function sameDay(a, b) {
  return new Date(a * 1000).toDateString() === new Date(b * 1000).toDateString();
}

export function dayName(at) {
  const now = Date.now() / 1000;
  if (sameDay(at, now)) return "Today";
  if (sameDay(at, now - 86400)) return "Yesterday";
  return new Date(at * 1000).toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long" });
}

export function when(at) {
  if (!at) return "";
  const now = Date.now() / 1000;
  if (sameDay(at, now)) return clock(at);
  const d = new Date(at * 1000);
  return `${d.toLocaleDateString(undefined, { day: "numeric", month: "short" })} ${clock(at, false)}`;
}

export function fromIso(text) {
  const parsed = Date.parse(text || "");
  return Number.isNaN(parsed) ? 0 : parsed / 1000;
}

export function span(ms) {
  if (ms === null || ms === undefined) return "";
  if (ms < 1000) return `${Math.round(ms)} ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds - minutes * 60)} s`;
}

export function ago(at, now = Date.now() / 1000) {
  if (!at) return "never";
  const s = Math.max(0, now - at);
  if (s < 5) return "just now";
  if (s < 60) return `${Math.floor(s)} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min ago`;
  return `${Math.floor(s / 86400)} d ago`;
}

export function lasting(seconds) {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min`;
  if (s < 86400) return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
  return `${Math.floor(s / 86400)} d ${Math.floor((s % 86400) / 3600)} h`;
}

export function bytes(n) {
  if (n === null || n === undefined) return "";
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export const count = (n) => Number(n || 0).toLocaleString();
export const many = (n, one, more) => `${count(n)} ${Number(n) === 1 ? one : more || one + "s"}`;

export function channel(id) {
  if (!id) return "";
  return "#" + (state.labels.channels[id] || "…" + String(id).slice(-4));
}

export function member(id, name) {
  if (id && id === state.labels.her) return "Sanya";
  return name || state.labels.members[id] || (id ? "member …" + String(id).slice(-4) : "someone");
}

export function server(id) {
  const found = (state.status?.servers || []).find((one) => one.id === id);
  return found ? found.name : "";
}

export function hue(id) {
  // A colour per member: ids one apart must not come out the same shade.
  let hash = 2166136261;
  for (const letter of String(id || "")) hash = Math.imul(hash ^ letter.charCodeAt(0), 16777619);
  hash = Math.imul(hash ^ (hash >>> 16), 0x85ebca6b);
  hash = Math.imul(hash ^ (hash >>> 13), 0xc2b2ae35);
  return ((hash ^ (hash >>> 16)) >>> 0) % 360;
}

// ---------- wording

const words = (code) => String(code ?? "").replaceAll("_", " ");
const from = (table, code) => table[code] || words(code);

const VIA = { mention: "mentioned her", reply: "replied to her" };
const UNANSWERED = {
  no_reply_channel: "this channel is on the no-reply list",
  bot_ceiling: "a bot, and she had answered it as often as she may",
};
const DROPPED = {
  dm: "a private message; she does not take those",
  server_not_active: "the server is switched off for her",
  not_chat: "not a chat message (a join, a pin or the like)",
  duplicate: "already seen",
};
const SECURITY = {
  clean: "clean", flagged: "flagged", bypass: "told to ignore her rules", disabled: "check switched off",
  check_failed: "the check itself failed or timed out",
  check_unclear: "the check gave no clear answer",
};
const REPLACED = {
  backstage: "it talked about what she runs on",
  secret: "it held a secret",
  private: "it held a file path or a private address",
  encoded: "it held encoded text",
  error_text: "it held technical error text",
};
const MODEL_ERROR = {
  stage_note: "she wrote a stage note instead of a message",
  malformed: "the answer could not be read",
  TimeoutError: "the model server took too long",
};
const KEPT_OUT = {
  flagged: "the security check flagged it",
  caught_up: "read while catching up after a start: context only",
  answer_kept_out: "her answer to a message that is kept out",
  held_back: "held back when a member asked her to forget",
  bots_only: "only bots spoke: bots are never a source of facts",
  kept_out: "kept out",
};
const TAKEN_BACK = {
  batch_resent: "its batch was read again without it",
  batch_deleted: "its batch was removed from memory",
  batch_too_old: "its batch is older than the room log; the facts stay",
  batch_unknown: "its batch was not found",
};
const TOOLS = {
  web_search: "web search", read_page: "read a web page", open_file: "open a sent file",
  look_back: "look back through the chat",
  find_gif: "look for a GIF", find_meme: "look for a meme", find_picture: "look for a picture",
  attach_image: "attach an image", send_sticker: "send a sticker",
  add_reaction: "emoji reaction", recall_memory: "search her memory", list_memories: "list what she remembers",
  forget_memory: "forget a fact", forget_all_memories: "forget everything about someone",
  forget_recent: "forget what was just said", unknown: "a tool that does not exist",
};
const IMAGE_MODE = {
  off: "none allowed", optional: "allowed unasked", requested: "asked for", requested_meme: "a meme asked for",
};
const IMAGE_OUTCOME = {
  not_allowed: "none", allowed_unused: "she left it", searched_nothing: "she looked and nothing usable turned up",
  searched_unattached: "she looked and attached none", attached: "posted", upload_failed: "would not upload",
};
const REACTION_OUTCOME = { not_allowed: "none", allowed_unused: "she left it", added: "added", failed: "failed" };
// A reply that had trouble on the way: counted as a problem only when something failed outright.
export function troubleChip(count, level) {
  if (!count && level !== "serious") return null;
  if (level === "serious") return chip(many(count || 1, "problem"), "bad", "Something failed outright.", "alert");
  return chip(many(count, "hiccup"), "warn", "Something went wrong on the way and she got over it: a second try at the model, or a tool that did not work.", "alert");
}

const STATE = {
  running: "answering", sent: "sent", failed: "model failed", dropped: "dropped", restarted: "restarted",
  unfinished: "no ending logged", interrupted: "cut off by a stop", error: "error",
};

export const say = {
  via: (code) => from(VIA, code),
  unanswered: (code) => from(UNANSWERED, code),
  dropped: (code) => from(DROPPED, code),
  security: (code) => from(SECURITY, code),
  replaced: (code) => from(REPLACED, code),
  modelError: (code) => (String(code).startsWith("empty_") ? "the answer came back empty" : from(MODEL_ERROR, code)),
  keptOut: (code) => from(KEPT_OUT, code),
  takenBack: (code) => from(TAKEN_BACK, code),
  tool: (code) => from(TOOLS, code),
  state: (code) => from(STATE, code),
  imageMode: (code) => from(IMAGE_MODE, code),
  imageOutcome: (code) => from(IMAGE_OUTCOME, code),
  reactionOutcome: (code) => from(REACTION_OUTCOME, code),
  words,
};

// ---------- small shared parts

export function chip(text, tone = "", title = "", glyph = "") {
  return h("span.chip" + (tone ? "." + tone : ""), { title }, glyph ? icon(glyph, 12) : null, text);
}

export function empty(text, glyph = "clock") {
  return h("div.empty", icon(glyph, 22), h("p", text));
}

export function memoryChip(message, rules) {
  if (message.memory === "done" && message.bot && !message.own) {
    return chip("sent as context", "info", "A bot's line goes along with the batch so the chat reads right. Bots are never a source of facts.", "memory");
  }
  if (message.memory === "done") return chip("in memory", "info", "It went to the memory service in a batch.", "memory");
  if (message.memory === "pending") {
    const wait = rules
      ? `Goes to memory once the channel has ${rules.batch_messages} messages waiting or has been quiet for ` +
        `${rules.flush_minutes} minutes, and nobody has spoken to her for ${rules.quiet_minutes} minutes.`
      : "";
    return chip("waiting for memory", "warn", wait, "clock");
  }
  if (message.memory === "skip") return chip("kept out of memory", "muted", say.keptOut(message.kept_out || "kept_out"), "off");
  return null;
}

let toastTimer = 0;
export function toast(text, tone = "") {
  let box = document.querySelector(".toast");
  if (!box) {
    box = h("div.toast");
    document.body.append(box);
  }
  box.className = "toast show" + (tone ? " " + tone : "");
  fill(box, ...String(text).split("\n").map((line) => h("div", line)));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => box.classList.remove("show"), 9000);
}

export function confirmBox(title, text, yes, tone = "") {
  return new Promise((resolve) => {
    const done = (answer) => {
      cover.remove();
      document.removeEventListener("keydown", key);
      resolve(answer);
    };
    const key = (event) => {
      if (event.key === "Escape") done(false);
    };
    const cover = h("div.cover", { onclick: (event) => event.target === cover && done(false) },
      h("div.modal", { role: "dialog", "aria-modal": "true" },
        h("h3", title),
        h("p", text),
        h("div.actions",
          h("button.btn", { onclick: () => done(false) }, "Cancel"),
          h("button.btn.solid" + (tone ? "." + tone : ""), { onclick: () => done(true) }, yes))));
    document.addEventListener("keydown", key);
    document.body.append(cover);
    cover.querySelector(".solid").focus();
  });
}
