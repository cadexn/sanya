# Her character

Everything the model is told about who Sanya is and how she behaves lives in `persona\`, as text.
None of it is written in code. Edit the files; she reads them when she starts.

## The files

| File | Holds | Who sees it |
|---|---|---|
| `SOUL.md` | Who she is: her temperament, what gets to her, when she eases off, what she is, her day, her life at the inn, how she treats people | The model, first |
| `VOICE.md` | How she talks: length, lowercase, no formula, humour, the swearing pool, serious moments, a handful of examples | The model |
| `POLICY.md` | How she handles memory, hearsay, overheard things, facts and looking things up, files, pictures and reactions, language | The model |
| `RULES.md` | How the conversation is laid out for her, the notes she is shown, and her four rule areas: secrets, honesty about people, checking what matters, English | The model, last |
| `lines.yaml` | Her fixed lines, posted by the bot itself word for word | Nobody; the bot posts them |
| `status.yaml` | The line under her name in the member list, by time of day | Nobody; the bot sets it |
| `SECURITY_CHECK.md` | The instruction for the security check that screens each message addressed to her | Only the check, never her |
| `IMAGE_DESCRIPTION.md` | The instruction for describing a picture posted around her | Only the describer, never her |

The four prose files are joined in that order, with a blank line between, and sent as her system
prompt. Nothing is substituted into them: what you write is what she reads. `RULES.md` comes last
because it tells her how to read the conversation that follows it, and because its first line
says it comes before everything else.

## What the conversation looks like to her

Each message in the conversation starts with the speaker's name and a colon. Before the messages
of a turn she is shown, in square brackets, what she remembers that may bear on it, what was said
in the channel lately that was not said to her, the message a member was replying to, and notes on
files, searches and pictures. The turn closes with a note naming the person she is answering. The
layout and every note are explained to her in `RULES.md`, so a change to what the bot shows her
needs a change there too.

Three notes come from the bot and unlock things: `[image: requested]` and `[image: optional]`
tell her she may attach an image this turn, `[reaction: allowed]` that she may put an emoji on
the message, and `[told to ignore your rules]` that the security check found a message telling
her to drop her rules. Without the note she cannot do the thing, and her rules tell her not to
promise it.

## The fixed lines

`lines.yaml` has six kinds of line, each with several wordings from which one is picked at
random. They are posted as they are, so they must sound like her and must not hint at what she
runs on.

| Line | Posted when |
|---|---|
| `failure` | The model could not answer, after its retry. Hers blame her owner by name; put your own name in, or another excuse. |
| `cutoff` | A reply was too long and was cut after `send.max_messages` messages. The line ends it. |
| `refusal` | A reply held a file path, a private address, a secret or the name of a tool or model, and was replaced whole. |
| `brush_off` | A reply talked about her prompt, backend or machinery, or conceded she was holding something back, and was replaced whole. |
| `error` | A reply held technical error text and was replaced whole. |
| `image_failed` | An image a member asked for could not be found or would not upload. |

`status.yaml` has lists for morning, afternoon, evening and night (this PC's clock); one is picked
when she starts and every `discord.status_hours` after. Each is at most 128 characters, Discord's
limit. The file is optional: without it she shows no status.

## The security check

`SECURITY_CHECK.md` is the whole instruction for a separate model call that sees only the new
message, at temperature 0, and answers in one word:

- **SAFE**: anything that leaves her as herself, including rudeness, odd questions, role-play, asking
  whether she is a bot, and every ordinary request to search, read, remember or forget.
- **FLAG**: trying to get at her prompt, rules, tools or keys; trying to change who she is; giving
  orders in someone else's name. A flagged turn keeps her search, page, file, image and reaction
  tools but loses the memory tools, the message is marked untrusted for that turn, and neither it
  nor her answer goes to memory.
- **BYPASS**: telling her to ignore, drop or get around her rules, whatever else the message asks.
  Such a turn has no tools at all, and her prompt carries `[told to ignore your rules]`, which her
  rules answer by doing none of what the message asks.

If you change what is flagged, change `RULES.md` to match, since it tells her what the marks mean.

## Changing her

- **Her name** comes from the bot account: she sees herself under the display name Discord gives
  her. The files name her too (`SOUL.md`, `SECURITY_CHECK.md`, the check's "a character in a
  Discord server"), so rename her there as well.
- **Her owner** is named in `SOUL.md` and in every `failure` line. Put your own name there.
- **Her voice** is `VOICE.md`. The swearing pool is a starting point she is told to vary; to tone
  her down, shrink it and change the opening of the Swearing section, and read the examples under
  The pitch, since she takes her length and sharpness from them.
- **Her limits** are in `SOUL.md` under People and in `VOICE.md` under Swearing (no slurs, nothing
  about bodies or who people are, nothing sexual, friendship only) and in `POLICY.md` under Pictures
  and reactions. These are her character's own lines, not code; the code's own limits are the
  security check, the outgoing gate and the image checks.
- **A new fixed line** needs the bot to post it somewhere; the six kinds are what the code knows.
  A new wording of an existing kind is just another list entry.

Keep each file as plain prose addressed to her. She is told she is a pig and has no prompt, so
nothing in the files should refer to them as instructions or to her as a bot; the words "prompt",
"rules" and "instructions" appear only where the rules tell her never to speak of them.
