// Sanya Monitor: the frame of the page, the look every two seconds, and the Start and Stop button.

import { fill, state, h, icon, get, post, lasting, toast, confirmBox, count } from "./core.js";
import { closeDrawer } from "./drawer.js";
import * as live from "./live.js";
import * as talk from "./talk.js";
import * as memory from "./memory.js";
import * as health from "./health.js";

const VIEWS = {
  live: { title: "Live", glyph: "pulse", view: live },
  talk: { title: "Conversations", glyph: "chat", view: talk },
  memory: { title: "Memory", glyph: "memory", view: memory },
  health: { title: "Health", glyph: "shield", view: health },
};
const EVERY = 2000; // milliseconds between looks
const KEPT = 2500; // items held in the page before the oldest are let go

// ---------- theme

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("sanya.monitor.theme", theme);
  fill(themeButton, icon(theme === "light" ? "moon" : "sun", 16));
  themeButton.title = theme === "light" ? "Switch to the dark look" : "Switch to the light look";
}
const themeButton = h("button.btn.bare", { onclick: () => setTheme(document.documentElement.dataset.theme === "light" ? "dark" : "light") });
setTheme(localStorage.getItem("sanya.monitor.theme") || (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark"));

// ---------- the frame

const pillBox = h("div");
const serverBox = h("div");
const controlBox = h("div");
const tabsBox = h("nav.tabs");
const bannerBox = h("div");
const root = h("div");
document.getElementById("app").append(
  h("div.top",
    h("div.bar",
      h("div.brand", h("img", { src: "/static/icon.svg", alt: "" }), "Sanya Monitor"),
      pillBox, h("div.grow"), serverBox, controlBox, themeButton),
    tabsBox),
  h("main", bannerBox, root));

let current = null;
let currentName = "";
let serverMark = "";
let controlMark = "";

function route() {
  const [name, arg] = location.hash.replace(/^#\/?/, "").split("/");
  return { name: VIEWS[name] ? name : "live", arg };
}

function show() {
  const { name, arg } = route();
  current?.unmount?.();
  closeDrawer();
  current = VIEWS[name].view;
  currentName = name;
  fill(root);
  drawTabs();
  if (state.guild) current.mount(root, arg);
}

function drawTabs() {
  const problems = state.stats?.problems || 0;
  fill(tabsBox, ...Object.entries(VIEWS).map(([name, view]) =>
    h("button.tab" + (name === currentName ? ".on" : ""), { onclick: () => { location.hash = "#/" + name; } },
      icon(view.glyph, 15), view.title,
      name === "health" && problems ? h("span.count", { title: "Problems in the last 24 hours" }, count(problems)) : null)));
}

function drawTop() {
  const status = state.status;
  let tone = "";
  let text = "Connecting…";
  if (state.offline) {
    tone = "bad";
    text = "The monitor is not answering";
  } else if (status) {
    const up = status.started_at ? ` · up ${lasting(status.now - status.started_at)}` : "";
    if (status.process.state === "running") {
      tone = status.replying.length ? "busy" : "good";
      text = (status.replying.length ? "Answering" : "Running") + up;
    } else if (status.process.state === "other_account") {
      tone = "warn";
      text = "Running under another Windows account";
    } else {
      tone = "bad";
      text = "Stopped";
    }
  }
  fill(pillBox, h("span.pill" + (tone ? "." + tone : ""), h("span.dot"), text));
  if (!status) return;

  // The server picker and the button are only rebuilt when they change, so an open list is not snatched away.
  const mark = JSON.stringify(status.servers) + state.guild;
  if (mark !== serverMark) {
    serverMark = mark;
    fill(serverBox, h("select.pick", { title: "Which server to look at", onchange: (event) => pickServer(event.target.value) },
      status.servers.map((one) => h("option", { value: one.id, selected: one.id === state.guild },
        one.name + (one.active ? "" : " (switched off)")))));
  }
  const process = status.process.state;
  const busy = status.control.busy || acting;
  const control = process + busy;
  if (control !== controlMark) {
    controlMark = control;
    fill(controlBox, 
      busy ? h("button.btn", { disabled: true }, icon("clock", 14), "Working…")
        : process === "stopped" ? h("button.btn.go", { onclick: () => act("start"), title: "Runs scripts\\Start-Sanya.ps1, as the Start launcher does" }, icon("play", 14), "Start Sanya")
          : process === "running" ? h("button.btn.halt", { onclick: () => act("stop"), title: "Runs scripts\\Stop-Sanya.ps1, as the Stop launcher does" }, icon("stop", 14), "Stop Sanya")
            : h("button.btn", { disabled: true, title: "She was started under another Windows account. Only that account can stop her." }, icon("off", 14), "Cannot stop from here"));
  }

  const picked = status.servers.find((one) => one.id === state.guild);
  const banners = [];
  if (process === "stopped") banners.push(h("div.banner.bad", icon("power"), "Sanya is stopped. Nothing is being read, answered or remembered until she is started."));
  if (process === "other_account") banners.push(h("div.banner", icon("alert"), "A process with her number is running under another Windows account. This account can watch what she records, but cannot see or stop the process."));
  if (picked && !picked.active) banners.push(h("div.banner", icon("off"), `${picked.name} is switched off for her: she reads and answers nothing there. What is shown is from when it was on.`));
  if (picked && !picked.data) banners.push(h("div.banner", icon("alert"), `Nothing has been recorded for ${picked.name} yet.`));
  fill(bannerBox, ...banners);
}

function pickServer(id) {
  state.guild = id;
  localStorage.setItem("sanya.monitor.guild", id);
  state.items.clear();
  state.head = null;
  state.stats = null;
  if (location.hash.startsWith("#/talk/")) location.hash = "#/talk";
  look().then(show);
}

// ---------- start and stop

let acting = false;

async function act(action) {
  const servers = (state.status?.servers || []).filter((one) => one.active).map((one) => one.name).join(", ") || "her servers";
  const sure = action === "stop"
    ? await confirmBox("Stop Sanya?", `She goes offline in ${servers} until she is started again. A reply she is writing at this moment is lost.`, "Stop her", "bad")
    : await confirmBox("Start Sanya?", `She comes online in ${servers} and starts reading and answering.`, "Start her", "good");
  if (!sure) return;
  acting = true;
  drawTop();
  try {
    const done = await post("/api/control", { action });
    toast(done.output || (done.ok ? "Done." : "It did not work."), done.ok ? "good" : "bad");
  } catch (error) {
    toast(error.message, "bad");
  }
  acting = false;
  await look();
}

// ---------- the look

let looking = false;
let ticks = 0;

async function look() {
  if (looking) return;
  looking = true;
  try {
    const data = await get("/api/poll", { guild: state.guild, after: state.head });
    state.offline = false;
    const first = data.guild !== state.guild;
    if (first) {
      state.guild = data.guild;
      localStorage.setItem("sanya.monitor.guild", data.guild);
    }
    if (data.reset) state.items.clear();
    for (const item of data.items) state.items.set(item.id, item);
    if (state.items.size > KEPT) {
      const oldest = [...state.items.values()].sort((a, b) => a.at - b.at).slice(0, state.items.size - KEPT);
      for (const item of oldest) state.items.delete(item.id);
    }
    state.head = data.head;
    state.status = data.status;
    state.stats = data.stats;
    state.labels = data.labels;
    drawTop();
    drawTabs();
    if (first) show();
    else current?.update?.(data.items, data.reset);
  } catch (error) {
    state.offline = error.status === 0;
    drawTop();
  } finally {
    looking = false;
  }
}

setInterval(() => {
  if (document.hidden) return;
  ticks += 1;
  look().then(() => current?.tick?.(ticks));
}, EVERY);
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) look();
});
window.addEventListener("hashchange", show);

show();
look();
