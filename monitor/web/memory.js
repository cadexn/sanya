// The Memory view: what is waiting to go, the batches that went, and the facts in the bank.

import { fill, state, h, icon, get, when, span, ago, many, count, channel, chip, empty, fromIso } from "./core.js";
import { openBatch, factRow } from "./drawer.js";

let tilesBox, waitingBox, batchesBox, factsBox, factsMore;
let overview = null;
let search = { q: "", state: "", kind: "", offset: 0, total: 0, items: [] };

function tile(label, value, sub, tone = "", glyph = "memory") {
  return h("div.card.tile" + (tone ? "." + tone : ""),
    h("div.label", icon(glyph, 14), label), h("div.value" + (String(value).length > 9 ? ".small" : ""), value), h("div.sub", sub || ""));
}

function drawOverview() {
  if (!overview || !tilesBox) return;
  const stats = overview.stats;
  const rules = overview.rules || {};
  const now = state.status?.now || Date.now() / 1000;
  const kinds = stats?.nodes_by_fact_type || {};
  const summaries = stats?.total_observations || 0;
  const facts = stats ? (stats.total_nodes || 0) - (kinds.observation || 0) : null;
  const written = stats ? fromIso(stats.last_memory_write_at) : 0;
  fill(tilesBox, 
    tile("Memory bank", overview.bank || "none", overview.bank ? "one bank for this server" : "this server has no bank, so nothing is remembered", overview.bank ? "" : "warn"),
    tile("Facts", facts === null ? "?" : count(facts), overview.error ? `the memory service is not answering (${overview.error})` : "what she can recall", overview.error ? "bad" : ""),
    tile("Batches of chat", stats ? count(stats.total_documents) : "?", "documents the facts were drawn from", "", "file"),
    tile("Merged summaries", stats ? count(summaries) : "?", stats?.pending_consolidation ? `${count(stats.pending_consolidation)} facts not merged yet; she is shown facts only` : "she is shown facts only, never these", "", "memory"),
    tile("Last written", written ? ago(written, now) : "never", stats?.failed_operations ? `${stats.failed_operations} failed operations` : stats?.pending_operations ? `${stats.pending_operations} operations in progress` : "nothing in progress", stats?.failed_operations ? "bad" : "", "clock"));

  const waiting = overview.waiting || [];
  fill(waitingBox, 
    waiting.length ? h("table.list",
      h("thead", h("tr", h("th", "Channel"), h("th.num", "Waiting"), h("th", "Oldest"), h("th", "Newest"))),
      h("tbody", waiting.map((one) => h("tr",
        h("td", channel(one.channel)),
        h("td.num", `${one.waiting} of ${rules.batch_messages}`),
        h("td", ago(one.oldest, now)), h("td", ago(one.newest, now))))))
      : empty("Nothing is waiting. Everything heard has gone to memory or is kept out.", "check"),
    h("div.inner.muted", `A channel's chat goes to memory once ${rules.batch_messages} messages are waiting or it has been quiet for ${rules.flush_minutes} minutes. ` +
      `Background work only runs when nobody has spoken to her for ${rules.quiet_minutes} minutes.` +
      (overview.removals ? ` ${many(overview.removals, "batch", "batches")} waiting to be taken back after a deleted message.` : "")));

  const batches = overview.batches || [];
  fill(batchesBox, batches.length ? h("table.list",
    h("thead", h("tr", h("th", "Sent"), h("th", "Channel"), h("th.num", "Messages"), h("th.num", "Not kept"), h("th.num", "Took"))),
    h("tbody", batches.map((batch) => h("tr.opens", { onclick: () => openBatch(batch.document), title: "See the chat that went and the facts drawn from it" },
      h("td", when(batch.at)),
      h("td", channel(batch.channel), batch.curated ? [" ", chip("a fact forgotten", "warn")] : null),
      h("td.num", String(batch.messages)),
      h("td.num", batch.dropped === undefined ? "—" : String(batch.dropped)),
      h("td.num", batch.took_ms === undefined ? "—" : span(batch.took_ms))))))
    : empty("No batch has gone to memory from this server yet.", "memory"));
}

function drawFacts(error) {
  if (!factsBox) return;
  if (error) {
    fill(factsBox, h("div.inner", h("div.banner.bad", icon("alert"), error.message)));
    fill(factsMore);
    return;
  }
  fill(factsBox, ...(search.items.length ? search.items.map((fact) => factRow(fact, openBatch))
    : [empty(search.q ? "Nothing has those words." : search.state ? "No fact has been retired." : search.kind ? "The service has merged no summaries yet." : "The bank is empty.", "memory")]));
  fill(factsMore, 
    h("span.faint", search.total ? `${count(search.items.length)} of ${count(search.total)}` : ""),
    search.items.length < search.total ? h("button.btn", { onclick: () => loadFacts(false) }, "Show more") : null);
}

async function loadFacts(fresh = true) {
  if (fresh) search = { ...search, offset: 0, items: [], total: 0 };
  try {
    const data = await get("/api/memory/facts", { guild: state.guild, q: search.q, state: search.state, kind: search.kind, offset: search.offset });
    search.items = [...search.items, ...data.items];
    search.total = data.total;
    search.offset = search.items.length;
    drawFacts();
  } catch (error) {
    drawFacts(error);
  }
}

async function loadOverview() {
  try {
    overview = await get("/api/memory", { guild: state.guild });
    Object.assign(state.labels, overview.labels || {});
    drawOverview();
  } catch (error) {
    tilesBox && fill(tilesBox, h("div.banner.bad", icon("alert"), error.message));
  }
}

export function mount(root) {
  search = { q: "", state: "", kind: "", offset: 0, total: 0, items: [] };
  tilesBox = h("div.tiles");
  waitingBox = h("div");
  batchesBox = h("div");
  factsBox = h("div");
  factsMore = h("div.more");
  const input = h("input.field", {
    type: "search", placeholder: "Find a word…",
    onkeydown: (event) => {
      if (event.key === "Enter") {
        search.q = event.target.value.trim();
        loadFacts();
      }
    },
  });
  const toggle = h("div.seg",
    h("button.on", { onclick: (event) => pick(event, "", ""), title: "The facts she can recall." }, "Facts"),
    h("button", { onclick: (event) => pick(event, "invalidated", ""), title: "Facts taken out of recall: dropped by her rules, forgotten at a member's wish, or expired hearsay." }, "Not kept"),
    h("button", { onclick: (event) => pick(event, "", "observation"), title: "Summaries the memory service merges out of the facts. She is never shown these." }, "Summaries"));
  function pick(event, value, kind) {
    search.state = value;
    search.kind = kind;
    toggle.querySelectorAll("button").forEach((button) => button.classList.toggle("on", button === event.currentTarget));
    loadFacts();
  }
  root.append(tilesBox, h("div.halves",
    h("div.stack",
      h("section.card", h("header", h("h2", "Waiting to go to memory")), waitingBox),
      h("section.card", h("header", h("h2", "Batches sent"), h("span.faint", "click one to open it")), batchesBox)),
    h("section.card", h("header", h("h2", "Facts in the bank"), h("div.grow"), toggle, input), factsBox, factsMore)));
  loadOverview();
  loadFacts();
}

export function tick(ticks) {
  if (tilesBox && ticks % 8 === 0) loadOverview();
}

export function unmount() {
  tilesBox = null;
  factsBox = null;
}
