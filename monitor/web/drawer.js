// The side panel: one message followed from arriving to memory, and one memory batch opened up.

import {
  fill, state, h, icon, get, preview, when, clock, span, bytes, many, channel, member, say, chip, empty, fromIso,
} from "./core.js";

const titleBox = h("h2");
const content = h("div.content");
const shade = h("div.shade", { onclick: closeDrawer });
const panel = h("aside.drawer", { "aria-label": "Details" },
  h("header", titleBox, h("button.btn.bare", { onclick: closeDrawer, title: "Close (Esc)" }, icon("x", 18))),
  content);
document.body.append(shade, panel);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") closeDrawer();
});

let asked = 0; // counts openings, so that a slow answer to an earlier click is not shown over a later one

export function openDrawer(title, ...nodes) {
  titleBox.textContent = title;
  fill(content, ...nodes.flat().filter(Boolean));
  content.scrollTop = 0;
  shade.classList.add("show");
  panel.classList.add("show");
}

export function closeDrawer() {
  asked += 1;
  shade.classList.remove("show");
  panel.classList.remove("show");
}

const loading = () => h("div.empty", icon("clock", 22), h("p", "Reading…"));
const failed = (error) => h("div.banner.bad", icon("alert"), error.message || String(error));

// ---------- pieces

function fileLines(files, names) {
  const lines = (files || []).map((file) => {
    const line = h("div",
      h("div.fileline", icon(file.mime?.startsWith("image/") ? "picture" : "file", 14), file.name,
        chip(file.kind === "overheard" ? "overheard" : "sent to her"), h("span", bytes(file.size))));
    if (file.description) line.append(h("div.muted", "What she was told it shows: " + file.description));
    else if (file.mime?.startsWith("image/") && file.kind === "overheard") line.append(h("div.faint", "Not described yet."));
    if (file.shows) line.append(h("div.previews", preview(file)));
    return line;
  });
  if (!lines.length && names?.length) {
    return h("div.fileline", icon("file", 14), `${many(names.length, "file")} attached: ${names.join(", ")} (not kept)`);
  }
  return lines;
}

export function saidCard(message, files) {
  return h("div.said" + (message.own ? ".her" : ""),
    h("div.who", h("b", message.own ? "Sanya" : member(message.author, message.name)),
      message.bot && !message.own ? chip("bot") : null, h("span", when(message.at))),
    message.text ? h("div.text", message.text) : h("div.text.gone", "(no words)"),
    fileLines(files, message.attachments));
}

function leg(kind, glyph, title, about, ...inside) {
  const parts = inside.flat().filter(Boolean);
  return h("div.leg" + (kind ? "." + kind : ""), h("div.mark", icon(glyph, 15)),
    h("div", h("h3", title), about ? h("div.about", about) : null, parts.length ? h("div.inside", parts) : null));
}

export function factRow(fact, onBatch) {
  const retired = fact.state !== "valid";
  const own = String(fact.document || "").endsWith("-own");
  const batch = String(fact.document || "").replace(/-own$/, "");
  const at = fromIso(fact.at);
  // The service ends a fact with the people it involves; they are shown as names beside it.
  const [text, involving] = String(fact.text).split(/\s*\|\s*Involving:\s*/);
  const people = involving ? involving.split(",").map((name) => name.trim().replace(/\.$/, "")).filter(Boolean) : fact.entities || [];
  return h("div.fact" + (retired ? ".retired" : ""),
    h("div.text", text),
    h("div.meta",
      at ? h("span", when(at)) : null,
      people.map((name) => chip(name)),
      fact.type === "observation" ? chip("merged summary: she is never shown this", "violet") : null,
      own ? chip("from her own words", "accent") : null,
      retired ? chip("not kept" + (fact.reason ? ": " + fact.reason : ""), "bad") : null,
      fact.merged ? chip("merged into a summary", "violet") : null,
      /^\d+-\d+-\d+$/.test(batch) && onBatch
        ? h("button.chip", { onclick: () => onBatch(batch) }, icon("memory", 12), "from " + channel(batch.split("-")[1]))
        : null));
}

// ---------- the steps of a reply turn

function track(...bars) {
  return h("div.track", bars.map(([kind, from, wide]) => h("i." + kind, {
    vars: { "--from": `${Math.max(0, Math.min(100, from))}%`, "--wide": `${Math.max(0.6, Math.min(100, wide))}%` },
  })));
}

function stepList(turn) {
  const steps = turn.steps || [];
  const start = turn.at;
  const last = steps.length ? steps[steps.length - 1].at : start;
  const total = Math.max(turn.ms || (last - start) * 1000, 1);
  const rows = [];
  let check = null;
  let call = 0;
  const row = (step, label, detail, bars, tone = "") => {
    rows.push(h("div.step" + (tone || step.trouble ? ".bad" : ""),
      h("div.label", label), h("div.took", step.ms !== undefined ? span(step.ms) : clock(step.at)),
      detail ? h("div.detail", detail) : null, bars ? track(...bars) : null));
  };
  // The words of a search, where she recorded them (from 9 October 2026).
  const searched = (step) => (step.words ? [`searched for “${step.words}”`] : []);
  const placed = (step, kind) => {
    const end = (step.at - start) * 1000;
    const ms = step.ms || 0;
    return [[kind, ((end - ms) / total) * 100, (ms / total) * 100]];
  };
  for (const step of steps) {
    switch (step.what) {
      case "file_stored":
        row(step, "File kept for this turn", `${step.mime}, ${bytes(step.bytes)}`);
        break;
      case "turn_choices": {
        // What the turn was allowed and on what dice, with what came of it. She records this since
        // her first start after 9 October 2026; it says what she could do, never why she did it.
        const sent = turn.sent || {};
        const dice = (roll) => (roll === null || roll === undefined ? "no dice thrown" : `dice ${Number(roll).toFixed(2)}`);
        const came = (outcome, table) => (outcome ? ` → ${table(outcome)}` : "");
        const image = [say.imageMode(step.image_mode), dice(step.image_roll)];
        if (step.image_cooldown) image.push("channel cooling down");
        if (step.no_media) image.push("the member wants none unasked");
        const reaction = [step.reaction_allowed ? "allowed" : "not allowed", dice(step.reaction_roll)];
        if (!step.reaction_short) reaction.push("message not short");
        if (step.reaction_cooldown) reaction.push("channel cooling down");
        const parts = [
          `image: ${image.join(", ")}${came(sent.image_outcome, say.imageOutcome)}` +
            (turn.sticker ? "; one of the server's stickers went in its place" : ""),
          `reaction: ${reaction.join(", ")}${came(sent.reaction_outcome, say.reactionOutcome)}`,
          (step.tools === false ? "tools: none, the turn was flagged"
            : (turn.offered || []).length ? `tools offered per call: ${turn.offered.join(", ")}` : "tools: allowed") +
            (step.memory_tools === false ? " (no memory tools: the turn was flagged)" : ""),
        ];
        if (sent.tool_calls !== undefined) {
          parts.push(`${many(sent.tool_calls, "tool request")} run` + (sent.tools_refused ? `, ${sent.tools_refused} refused by code` : ""));
        }
        if (step.quote) parts.push("a replied-to message was shown");
        if (step.attachments) parts.push(many(step.attachments, "file") + " attached");
        row(step, "Choices", parts.join(" · "));
        break;
      }
      case "model_call":
        if (step.purpose === "check") {
          check = step;
          break;
        }
        {
          call += 1;
          const reading = step.prompt_ms || 0;
          const writing = step.predicted_ms || 0;
          const waiting = Math.max(0, (step.ms || 0) - reading - writing);
          const from = (((step.at - start) * 1000 - (step.ms || 0)) / total) * 100;
          const parts = [
            `read ${step.prompt_tokens} tokens in ${span(reading)}`,
            `wrote ${step.completion_tokens} in ${span(writing)}` +
              (step.reasoning_tokens ? ` (${step.reasoning_tokens} of them thinking)` : ""),
          ];
          if (waiting >= 150) parts.push(`waited ${span(waiting)} for the model server`);
          if (step.offered !== undefined) parts.push(step.offered ? `${many(step.offered, "tool")} offered` : "no tools offered");
          if (step.tool_calls) parts.push(`asked for ${many(step.tool_calls, "tool")}`);
          if (step.attempt > 1) parts.push("second try");
          row(step, `Model call ${call}`, parts.join(" · "), [
            ["wait", from, (waiting / total) * 100],
            ["read", from + (waiting / total) * 100, (reading / total) * 100],
            ["write", from + ((waiting + reading) / total) * 100, (writing / total) * 100],
          ]);
        }
        break;
      case "model_call_failed":
        row(step, step.purpose === "check" ? "Security check's model call failed" : "Model call failed",
          `${say.modelError(step.error)}${step.purpose === "reply" ? "; she was asked once more" : ""}`,
          placed(step, "wait"), "bad");
        break;
      case "security_check":
        row(step, step.flagged ? "Security check: flagged" : "Security check: " + say.security(step.reason),
          step.flagged
            // Until 9 October 2026 a flagged turn had no tools at all; since then it loses only the memory tools.
            ? step.reason === "bypass"
              ? "The message told her to ignore her rules, her limits or her character. This turn had no tools at all and she was told to do none of what it asked; neither the message nor her answer is offered to memory."
              : `${say.security(step.reason)}. This turn had no memory tools (before 9 October 2026, no tools at all), and neither the message nor her answer is offered to memory.`
            : ["check_failed", "check_unclear"].includes(step.reason) ? "It gave no verdict, and since 9 October 2026 that does not flag the message."
              : check ? `A separate model call judged the message (${check.prompt_tokens} tokens read).` : "",
          [["check", 0, (step.ms / total) * 100]], step.flagged ? "bad" : "");
        break;
      case "recall":
        // Since 9 October 2026 memory is asked with the last few lines of the conversation as well.
        row(step, "Memory recall", `${many(step.facts, "fact")} about the speaker and the topic went into her prompt` +
          (step.asker === undefined ? "" : step.asker_failed ? " · the question about the person she was answering failed, so these are the topic's alone"
            : ` · ${step.asker} of them fetched by the name of the person she was answering`) +
          (step.lines === undefined ? "" : step.lines ? ` · memory was asked with the new message and ${many(step.lines, "earlier line")} of the members'`
            : " · memory was asked with the new message alone"),
          placed(step, "recall"));
        break;
      case "recall_failed":
        row(step, "Memory recall failed", `${say.words(step.error)}; she answered without her memory`, placed(step, "recall"), "bad");
        break;
      case "search":
        row(step, `Web search (${step.service})`, [...searched(step), many(step.results, "result")].join(" · "), placed(step, "tool"));
        break;
      case "search_failed":
        row(step, `Web search failed (${step.service})`, [...searched(step), say.words(step.error)].join(" · "), null, "bad");
        break;
      case "search_skipped":
        row(step, `Web search skipped (${step.service})`, say.words(step.reason), null, "bad");
        break;
      case "image_search": {
        const parts = [...searched(step), `${step.results} found, ${step.usable} usable`];
        // A site that refused the download: the search service's own small still of the image was used.
        if (step.spare) parts.push(`${step.spare} of them the search service's own copy, as the site refused the download`);
        row(step, `Image search (${step.kind}, ${step.service})`, parts.join(" · "), placed(step, "tool"));
        break;
      }
      case "image_search_failed":
        row(step, step.service ? `Image search failed (${step.kind}, ${step.service})` : "Image search failed",
          [...searched(step), say.words(step.error)].join(" · "), null, "bad");
        break;
      case "tool_call":
        row(step, "Tool: " + say.tool(step.name),
          !step.ok ? `did not work: ${say.words(step.error)}`
            : step.answered && step.name === "attach_image" ? "her line came with it, so no further model call was needed"
              : step.answered ? "this was her whole answer, so no further model call was needed"
                : step.lines !== undefined ? `${many(step.lines, "line")} of the last week's chat found; the words looked for are not kept` : "",
          step.ms ? placed(step, "tool") : null, step.ok ? "" : "bad");
        break;
      case "memory_held_back":
        row(step, "Recent chat held back from memory", `${many(step.messages, "message")} of the member's will not be stored`);
        break;
      case "reply_replaced":
        row(step, "Reply replaced on the way out", `${say.replaced(step.reason)}, so one of her fixed lines was sent instead`, null, "bad");
        break;
      case "image_posted":
        row(step, "Image posted", `${bytes(step.bytes)}${step.spontaneous ? ", her own idea" : ", asked for"}`);
        break;
      case "image_failed":
        row(step, "Image would not upload", bytes(step.bytes), null, "bad");
        break;
      case "sticker_sent":
        // Which sticker it was shows with her reply below: her message in the room log notes its name.
        row(step, "Sticker sent", step.alone ? "one of the server's own, as her whole answer" : "one of the server's own, with her reply");
        break;
      case "sticker_failed":
        row(step, "Sticker refused by Discord", step.alone ? "she had given no words with it, so nothing went with it" : "her words went without it", null, "bad");
        break;
      case "reaction_added":
        row(step, "Emoji reaction added", step.alone ? "as her whole answer" : "on top of her reply");
        break;
      case "reaction_failed":
        row(step, "Emoji reaction failed", say.words(step.error), null, "bad");
        break;
      case "reply_sent": {
        const parts = [`${many(step.sent, "message")}, ${many(step.chars, "character")}`];
        if (step.cut) parts.push("cut short with her cut-off line");
        if (step.model_failed) parts.push("the model failed, so her fixed line went out");
        if (step.plain) parts.push("as a plain message, not a reply");
        parts.push(`she was given ${step.window} conversation messages, ${step.overheard} lines of room chat and ` +
          many(step.pictures, "picture"));
        row(step, "Reply sent", parts.join(" · "));
        break;
      }
      case "reply_dropped":
        row(step, "Reply dropped", step.reason === "message_deleted" ? "the member deleted their message first" : say.words(step.reason), null, "bad");
        break;
      case "handler_error":
        row(step, "Error", `${say.words(step.where || "")} ${step.error || ""}`.trim(), null, "bad");
        break;
      default:
        row(step, say.words(step.what), Object.entries(step).filter(([key]) => !["what", "at", "ms", "trouble"].includes(key))
          .map(([key, value]) => `${say.words(key)}: ${value}`).join(" · "));
    }
  }
  if (!rows.length) return empty("No steps were logged for this turn.");
  return [h("div.steps", rows),
    h("div.legend", h("span", h("i.check"), "security check"), h("span", h("i.recall"), "recall"),
      h("span", h("i.wait"), "waiting for the model"), h("span", h("i"), "reading the prompt"),
      h("span", h("i.write"), "writing"), h("span", h("i.tool"), "tools"))];
}

// What she had from memory for a reply: the facts put before her, and those a memory tool returned.
function fromMemory(data) {
  const had = data.recalled;
  const recalled = data.turn?.facts || 0;
  if (had === null || had === undefined) {
    return h("div.faint", "Which facts she had from memory is kept for the replies she makes after her next restart.");
  }
  if (!had.length) {
    return recalled ? h("div.faint", "The facts themselves are not on record for this reply: it was made before she began keeping " +
      "them, or they have left her memory since.") : null;
  }
  const groups = new Map();
  for (const one of had) {
    if (!groups.has(one.source)) groups.set(one.source, []);
    groups.get(one.source).push(one);
  }
  return h("div.said", h("div.who", h("b", "What she had from memory")),
    [...groups].map(([source, facts]) => [
      h("div.muted", source === "recall" ? "Put before her with the message:" : `Returned when she used "${say.tool(source)}":`),
      h("ul.had", facts.map((fact) => h("li", fact.text.split(/\s*\|\s*Involving:/)[0])))]));
}

// ---------- one message, followed

export async function openTrace(messageId, hop = 0) {
  const mine = ++asked;
  if (!hop) openDrawer("Following a message", loading());
  let data;
  try {
    data = await get("/api/trace", { guild: state.guild, message: messageId });
  } catch (error) {
    if (mine === asked) openDrawer("Following a message", failed(error));
    return;
  }
  if (mine !== asked) return;
  // One of her own messages: what there is to follow is the message it answered.
  if (!hop && data.message?.own && data.message.reply_to) return openTrace(data.message.reply_to, 1);
  Object.assign(state.labels, data.labels || {});
  openDrawer("Following a message", trace(messageId, data));
}

function trace(messageId, data) {
  const { message, heard, turn, saved = {}, files = {}, memory = {} } = data;
  const rules = data.rules || {};
  const kept = saved[messageId];
  const legs = [];

  if (heard) legs.push(leg("", "ear", "Read from Discord", `${when(heard.at)} · taken in`));
  else if (data.caught_up) {
    legs.push(leg("", "ear", "Read while catching up",
      "She read this from the channel's recent chat when she started. It gives her context for her first replies; " +
      "it is not offered to memory, and a mention in it is not answered."));
  } else legs.push(leg("no", "ear", "Read from Discord", "This is from before the monitor's log begins, so how it arrived is not known."));

  if (message) {
    legs.push(leg("", "file", "Recorded in the room log", `Kept for ${rules.room_log_days || 7} days.`, saidCard(message, files[messageId])));
  } else if (kept) {
    legs.push(leg("no", "file", "No longer in the room log",
      "The room log keeps a week, or the message was deleted. The conversation still has what was said:",
      saidCard({ ...kept, own: false }, [])));
  } else legs.push(leg("no", "file", "Not in the room log", "It has left the room log, or was deleted in Discord."));

  const via = heard?.addressed;
  if (message?.own) legs.push(leg("her", "reply", "One of her own messages", message.reply_to ? "" : "Nothing recorded about what it answered."));
  else if (via || message?.addressed) legs.push(leg("her", "at", "Said to her", via ? `The member ${say.via(via)}.` : ""));
  else if (heard?.unanswered) legs.push(leg("wait", "at", "Said to her, but not answered", say.unanswered(heard.unanswered) + "."));
  else legs.push(leg("no", "at", "Not said to her", "Heard only. She answers when she is mentioned or replied to, never unprompted."));

  if (turn) {
    const others = (data.asked || []).filter((one) => one.id !== messageId);
    const about = [when(turn.at), turn.ms ? `${span(turn.ms)} from start to sent` : say.state(turn.state)];
    if (turn.messages > 1) about.push(`${turn.messages} messages answered together`);
    if (turn.restarts) about.push(`started again ${many(turn.restarts, "time")} because the member kept writing`);
    legs.push(leg(turn.state === "sent" ? "her" : turn.state === "running" ? "wait" : "bad", "reply",
      "Reply turn: " + say.state(turn.state), about.join(" · "),
      others.length ? [h("div.muted", "Answered together with:"), others.map((one) => saidCard(one, files[one.id]))] : null,
      stepList(turn),
      fromMemory(data),
      (data.replies || []).map((one) => saidCard(one, files[one.id]))));
  } else if ((via || message?.addressed) && !message?.own) {
    legs.push(leg("wait", "reply", "No reply turn in the log",
      (data.replies || []).length ? "Her answer is in the room log:" : "Either it is still waiting its turn, or the log of it has gone.",
      (data.replies || []).map((one) => saidCard(one, files[one.id]))));
  }

  if (kept) {
    legs.push(leg("", "chat", "Saved to the channel's conversation",
      `Kept ${rules.conversation_days || 30} days and shared by everyone in the channel; the newest part is given to her with each reply.` +
      (kept.untrusted ? " It carries the untrusted mark from the security check." : ""),
      kept.notes ? h("div.said", h("div.who", "Her notes on the tools she used"), h("div.text", kept.notes)) : null));
  } else if (turn && turn.state === "sent" && !message?.own) {
    legs.push(leg("no", "chat", "Not in the conversation", "It has passed the conversation's age limit, or the message was deleted."));
  }

  if (memory.state === "pending") {
    legs.push(leg("wait", "memory", "Waiting to go to memory",
      `It goes once the channel has ${rules.batch_messages} messages waiting or has been quiet for ${rules.flush_minutes} minutes, ` +
      `and nobody has spoken to her for ${rules.quiet_minutes} minutes.`));
  } else if (memory.state === "done") {
    const batch = memory.batch;
    const about = batch
      ? `In a batch of ${many(batch.messages, "message")} at ${when(batch.at)}` +
        (batch.took_ms !== undefined ? `, which took ${span(batch.took_ms)}` : "") +
        (batch.dropped ? ` and had ${many(batch.dropped, "fact")} dropped` : "") + "."
      : "Its batch is no longer on record.";
    legs.push(leg("", "memory", "Sent to memory", about,
      batch ? h("div", h("button.btn", { onclick: () => openBatch(batch.document) }, icon("memory", 14), "See what memory made of the batch")) : null));
  } else if (memory.state === "skip") {
    legs.push(leg("no", "memory", "Kept out of memory", say.keptOut(memory.kept_out) + "."));
  } else legs.push(leg("no", "memory", "Memory", "It is no longer in the room log, so where it stood with memory is not known."));

  return h("div.journey", legs);
}

// ---------- one memory batch

export async function openBatch(documentId) {
  const mine = ++asked;
  openDrawer("Memory batch", loading());
  let data;
  try {
    data = await get("/api/memory/batch", { guild: state.guild, document: documentId });
  } catch (error) {
    if (mine === asked) openDrawer("Memory batch", failed(error));
    return;
  }
  if (mine !== asked) return;
  Object.assign(state.labels, data.labels || {});
  const { batch, messages } = data;
  const kept = [...(data.people?.kept || []), ...(data.own?.kept || [])];
  const retired = [...(data.people?.retired || []), ...(data.own?.retired || [])];
  const hers = messages.filter((one) => one.own).length;
  openDrawer(`Memory batch from ${channel(batch.channel)}`,
    h("dl.facts",
      h("dt", "Sent"), h("dd", when(batch.at)),
      h("dt", "Messages"), h("dd", `${batch.messages}${hers ? ` (${hers} of them hers)` : ""}`),
      batch.took_ms !== undefined ? [h("dt", "Took"), h("dd", span(batch.took_ms))] : null,
      h("dt", "Facts kept"), h("dd", data.error ? "?" : String(kept.length)),
      h("dt", "Facts not kept"), h("dd", data.error ? "?" : String(retired.length)),
      batch.curated ? [h("dt", "Forgotten from"), h("dd", "a member had her forget a fact of this batch")] : null),
    data.error ? h("div.banner.bad", icon("alert"), `The memory service is not answering (${data.error}), so its facts cannot be shown.`) : null,
    h("div.muted", "The batch goes to the memory service as two documents: what the members said, and her own lines. " +
      "From her lines only facts about her alone are kept; from the members' lines, requests put to her and claims about her are dropped."),
    h("section.card", h("header", h("h2", `Facts kept (${kept.length})`)),
      kept.length ? kept.map((fact) => factRow(fact)) : empty(data.error ? "Not available." : "Nothing was kept from this batch.", "memory")),
    retired.length ? h("section.card", h("header", h("h2", `Facts not kept (${retired.length})`)), retired.map((fact) => factRow(fact))) : null,
    h("section.card", h("header", h("h2", "The chat that went"),
      messages.length < batch.messages ? chip(`${batch.messages - messages.length} no longer in the room log`, "muted") : null),
      h("div.inner", h("div.stack", messages.length ? messages.map((one) => saidCard(one, [])) : empty("These messages have left the room log.")))));
}
