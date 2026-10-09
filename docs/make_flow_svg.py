"""Draw docs/flow.svg from the step list below.

Run it after changing a step, so the picture and FLOW.md stay in step:

    python docs/make_flow_svg.py

Standard library only. The layout is one column of steps with the stores and
services each step talks to beside it.
"""
from __future__ import annotations

from html import escape
from pathlib import Path

# (title, subtitle, side) where side is (title, subtitle, direction) or None.
# direction: "in" = side feeds the step, "out" = step writes to the side, "both".
START = ("Any message in the server", "pinged or not; private messages never arrive")
END = ("Turn finished", "she waits until someone addresses her again")
GROUPS = [
    ("Intake", [
        ("Admission", "switched-on servers only; no repeats", None),
        ("Room log", "every message recorded, with what it answers", ("Room log store", "SQLite, kept 7 days", "out")),
        ("Addressed to Sanya?", "a mention or a reply, not in a no-reply channel",
         ("Not addressed", "heard; pictures kept", "out")),
        ("Bot exchange rule", "a bot gets a reply, then a plain answer", None),
        ("Batching", "no wait, one turn at a time per channel", None),
        ("Files and quoted message", "up to four files, type-checked and kept", ("File store", "on disk, never deleted", "out")),
        ("Security check", "flagged: no tools, nothing to memory", ("Model server", "one-word verdict", "both")),
    ]),
    ("Building the prompt", [
        ("Channel conversation", "one per channel, last 40 messages", ("History store", "SQLite, kept 30 days", "in")),
        ("Room context", "last 30 minutes, 2 newest pictures", ("Room log store", "what was said nearby", "in")),
        ("Image and emoji chance", "asked, 25% chance, or off; emoji 15%", ("Ledgers", "cooldowns, no-GIF wishes", "in")),
        ("Recall", "8 facts about the speaker and the topic", ("Hindsight", "facts only, no summaries", "in")),
        ("Prompt assembly", "persona, rules, scene, who she is answering", ("Persona files", "SOUL VOICE POLICY RULES", "in")),
    ]),
    ("Model loop", [
        ("Model call", "up to eight; empty or stage note: one retry", ("Model server", "one call at a time", "both")),
        ("Tool calls", "three per answer; none when flagged", ("Tools", "web, files, memory, images", "both")),
    ]),
    ("Outbox", [
        ("Egress gate", "secrets, paths, backstage talk: fixed line", ("Fixed lines", "refusal, brush-off, error", "in")),
        ("Send", "reply, image, emoji; nobody is pinged", ("Discord", "a reply without a ping", "out")),
        ("Save turn", "message, reply, notes, facts she had; ledgers", ("History store", "same SQLite file", "out")),
    ]),
]
BACKGROUND = ("Background: only when nobody has addressed her for two minutes", [
    ("Describe overheard pictures", "one at a time; the file is kept", ("Model server", "vision call", "both")),
    ("Take back deleted messages", "the batch is sent again without them", ("Hindsight", "documents replaced", "out")),
    ("Memory batch: members' lines", "facts about people; claims about her dropped", ("Hindsight", "document 1 of a batch", "out")),
    ("Memory batch: her own lines", "only facts about her alone are kept", ("Hindsight", "document 2 of a batch", "out")),
    ("Hearsay expiry and merge", "hearsay out after 90 days; hourly merge", ("Hindsight", "retire and merge calls", "out")),
    ("Housekeeping", "log 7 days, history 30, status every 4 h", ("SQLite and Discord", "expiry, presence", "out")),
])
# Steps joined by a two-way arrow (the model and its tools loop).
TWO_WAY = {("Model call", "Tool calls")}

WIDTH = 680
MAIN_X, MAIN_W = 40, 340
SIDE_X, SIDE_W = 440, 200
BOX_H, STEP = 56, 76
GROUP_X, GROUP_W = 24, 372


def layout():
    """Return (boxes, arrows, groups, height). Coordinates are final."""
    boxes, arrows, groups = [], [], []

    def add_step(y, step, kind="step"):
        title, sub, side = step
        boxes.append((MAIN_X, y, MAIN_W, kind, title, sub))
        if side:
            boxes.append((SIDE_X, y, SIDE_W, "stop" if side[0] == "Not addressed" else "store", side[0], side[1]))
            mid = y + BOX_H // 2
            arrows.append(("h", MAIN_X + MAIN_W + 4, mid, SIDE_X - 4, mid, side[2]))

    y = 30
    boxes.append((MAIN_X, y, MAIN_W, "end", START[0], START[1]))
    prev_bottom, prev_title = y + BOX_H, None
    top = prev_bottom + 20
    for name, steps in GROUPS:
        first = top + 32
        groups.append((GROUP_X, top, GROUP_W, 32 + len(steps) * STEP - (STEP - BOX_H) + 16, name))
        for i, step in enumerate(steps):
            sy = first + i * STEP
            both = (prev_title, step[0]) in TWO_WAY
            arrows.append(("v", MAIN_X + MAIN_W // 2, prev_bottom + 2, MAIN_X + MAIN_W // 2, sy - 4,
                           "both" if both else "out"))
            add_step(sy, step)
            prev_bottom, prev_title = sy + BOX_H, step[0]
        top = prev_bottom + 16 + 20
    y = top
    arrows.append(("v", MAIN_X + MAIN_W // 2, prev_bottom + 2, MAIN_X + MAIN_W // 2, y - 4, "out"))
    boxes.append((MAIN_X, y, MAIN_W, "end", END[0], END[1]))

    top = y + BOX_H + 44
    name, steps = BACKGROUND
    first = top + 32
    groups.append((GROUP_X, top, WIDTH - 2 * GROUP_X, 32 + len(steps) * STEP - (STEP - BOX_H) + 16, name))
    for i, step in enumerate(steps):
        add_step(first + i * STEP, step)
    height = first + len(steps) * STEP - (STEP - BOX_H) + 16 + 24
    return boxes, arrows, groups, height


def check(boxes):
    """Rough width check so no label overflows its box."""
    for x, y, w, kind, title, sub in boxes:
        if len(title) * 8.2 > w - 16 or len(sub) * 6.4 > w - 16:
            raise SystemExit(f"label too long for its box: {title!r} / {sub!r}")


STYLE = """
  .bg{fill:#ffffff}
  text{font-family:'Segoe UI',-apple-system,Roboto,Helvetica,Arial,sans-serif}
  .t{font-size:14px;font-weight:600}.s{font-size:12px}.g{font-size:12px;fill:#5f5e5a}
  .step rect{fill:#eeedfe;stroke:#534ab7}.step .t{fill:#3c3489}.step .s{fill:#534ab7}
  .store rect{fill:#e1f5ee;stroke:#0f6e56}.store .t{fill:#085041}.store .s{fill:#0f6e56}
  .end rect,.stop rect{fill:#f1efe8;stroke:#5f5e5a}.end .t,.stop .t{fill:#2c2c2a}.end .s,.stop .s{fill:#5f5e5a}
  .grp{fill:none;stroke:#888780;stroke-dasharray:4 4}
  .ar{stroke:#5f5e5a;stroke-width:1.5;fill:none}.ah{stroke:#5f5e5a;stroke-width:1.5;fill:none}
  @media (prefers-color-scheme: dark){
    .bg{fill:#1f1f1d}.g{fill:#b4b2a9}
    .step rect{fill:#3c3489;stroke:#afa9ec}.step .t{fill:#eeedfe}.step .s{fill:#cecbf6}
    .store rect{fill:#085041;stroke:#5dcaa5}.store .t{fill:#e1f5ee}.store .s{fill:#9fe1cb}
    .end rect,.stop rect{fill:#444441;stroke:#b4b2a9}.end .t,.stop .t{fill:#f1efe8}.end .s,.stop .s{fill:#d3d1c7}
    .grp{stroke:#888780}.ar,.ah{stroke:#b4b2a9}
  }
"""


def render(boxes, arrows, groups, height):
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" '
           f'viewBox="0 0 {WIDTH} {height}" role="img">',
           "<title>Sanya message flow</title>",
           "<desc>Every server message is logged and later feeds memory; a message addressed to Sanya "
           "passes through intake, the building of the prompt, the model loop and the outbox.</desc>",
           f"<style>{STYLE}</style>",
           '<defs><marker id="a" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="6" markerHeight="6" '
           'orient="auto-start-reverse"><path class="ah" d="M2 1L8 5L2 9" stroke-linecap="round" '
           'stroke-linejoin="round"/></marker></defs>',
           f'<rect class="bg" x="0" y="0" width="{WIDTH}" height="{height}"/>']
    for x, y, w, h, name in groups:
        out.append(f'<rect class="grp" x="{x}" y="{y}" width="{w}" height="{h}" rx="12"/>')
        out.append(f'<text class="g" x="{x + 16}" y="{y + 16}" dominant-baseline="central">{escape(name)}</text>')
    for _, x1, y1, x2, y2, direction in arrows:
        ends = {"out": ' marker-end="url(#a)"', "in": ' marker-start="url(#a)"',
                "both": ' marker-start="url(#a)" marker-end="url(#a)"'}[direction]
        out.append(f'<line class="ar" x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"{ends}/>')
    for x, y, w, kind, title, sub in boxes:
        cx = x + w // 2
        out.append(f'<g class="{kind}"><rect x="{x}" y="{y}" width="{w}" height="{BOX_H}" rx="8" stroke-width="1"/>'
                   f'<text class="t" x="{cx}" y="{y + 19}" text-anchor="middle" dominant-baseline="central">{escape(title)}</text>'
                   f'<text class="s" x="{cx}" y="{y + 38}" text-anchor="middle" dominant-baseline="central">{escape(sub)}</text></g>')
    out.append("</svg>")
    return "\n".join(out) + "\n"


def main():
    boxes, arrows, groups, height = layout()
    check(boxes)
    target = Path(__file__).with_name("flow.svg")
    target.write_text(render(boxes, arrows, groups, height), encoding="utf-8", newline="\n")
    print(f"wrote {target} ({len(boxes)} boxes, {len(arrows)} arrows, {height}px tall)")


if __name__ == "__main__":
    main()
