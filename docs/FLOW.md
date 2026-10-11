# Message flow

How one Discord message moves through the bot. Every message goes into the room log and, later,
into memory. Only a message addressed to Sanya goes on to produce a reply.

The values behind each box (how many messages, how long, what chance) are in
[SETTINGS.md](SETTINGS.md). This file shows the order of the steps.

## Diagram

![Sanya message flow](flow.svg)

The picture is [flow.svg](flow.svg). It
reads top to bottom: the left column is the path of a message, and the boxes on the right are the
stores and services each step uses. The dashed box at the bottom is background work, which is fed
by the room log and not by the reply.

### The same flow in Mermaid

For viewers that draw Mermaid, such as Gitea. Solid arrows are the path of a message being
answered; dotted arrows are background work.

```mermaid
flowchart TD
    subgraph INTAKE["Intake"]
        ADM["Admission<br/>switched-on servers only, no repeats"]
        LOG["Room log<br/>every message, with what it answers"]
        ADDR{"Addressed to her,<br/>in a channel she may reply in?"}
        BOTS["Bot exchange rule<br/>a reply, then a plain answer"]
        BATCH["Batching<br/>no wait, one turn at a time per channel"]
        ATT["Files and quoted message<br/>up to four, type-checked and kept"]
        SEC["Security check<br/>flagged: no tools, nothing to memory"]
    end

    subgraph PROMPTING["Building the prompt"]
        SESS["Channel conversation<br/>one per channel, last 40 messages"]
        CTX["Room context<br/>last 30 minutes, 2 newest pictures"]
        MEDIA["Image and emoji chance<br/>asked, 25% chance, or off"]
        RECALL["Recall<br/>8 facts, never the merged summaries"]
        PROMPT["Prompt assembly<br/>persona, rules, scene, who she is answering"]
    end

    subgraph LOOP["Model loop"]
        MODEL["Model call<br/>up to eight, one retry with a note"]
        TOOLS["Tool calls<br/>fifteen per answer, none when flagged"]
    end

    subgraph OUTBOX["Outbox"]
        EGRESS["Egress gate<br/>secrets, paths, backstage talk: fixed line"]
        SEND["Send<br/>reply, image, emoji; nobody is pinged"]
        SAVE["Save turn<br/>message, reply, tool notes, facts she had"]
    end

    subgraph BG["Background, only after two quiet minutes"]
        IMG["Describe overheard pictures<br/>the file is kept"]
        BACK["Take back deleted messages"]
        AMB1["Memory batch: members' lines<br/>facts about people; claims about her dropped"]
        AMB2["Memory batch: her own lines<br/>only facts about her alone are kept"]
        MERGE["Hearsay expiry and merge"]
        EXPIRE["Housekeeping<br/>log 7 days, history 30, status"]
    end

    MSG(["Any message in the server"])
    HEARD(["Heard, no reply; pictures kept"])
    DONE(["Turn finished"])

    ROOM[("Room log store<br/>SQLite, 7 days")]
    HIST[("History store<br/>SQLite, 30 days")]
    FILES[("File store<br/>on disk, never deleted")]
    LEDGER[("Ledgers<br/>images, emoji, members' wishes")]
    PERSONA[("Persona files<br/>and fixed lines")]
    HS[("Hindsight<br/>one bank, facts only")]
    MODELSRV[("Model server<br/>one request at a time")]
    WEB[("Search services and public web<br/>private addresses blocked")]
    DISCORD[("Discord")]

    MSG --> ADM --> LOG --> ADDR
    ADDR -- no --> HEARD
    ADDR -- yes --> BOTS --> BATCH --> ATT --> SEC
    SEC --> SESS --> CTX --> MEDIA --> RECALL --> PROMPT
    PROMPT --> MODEL
    MODEL <--> TOOLS
    MODEL --> EGRESS --> SEND --> SAVE --> DONE

    LOG --> ROOM
    HEARD --> FILES
    ATT --> FILES
    ROOM --> CTX
    HIST --> SESS
    SAVE --> HIST
    LEDGER --> MEDIA
    SAVE --> LEDGER
    HS --> RECALL
    PERSONA --> PROMPT
    PERSONA --> EGRESS
    SEC <--> MODELSRV
    MODEL <--> MODELSRV
    TOOLS <--> WEB
    TOOLS <--> HS
    TOOLS <--> FILES
    SEND --> DISCORD

    FILES -.-> IMG
    IMG -.-> MODELSRV
    ROOM -.-> BACK
    ROOM -.-> AMB1
    ROOM -.-> AMB2
    BACK -.-> HS
    AMB1 -.-> HS
    AMB2 -.-> HS
    MERGE -.-> HS
    EXPIRE -.-> ROOM
    EXPIRE -.-> HIST
    EXPIRE -.-> DISCORD
```

## The same flow as steps

### Intake

1. **Admission.** Direct messages are dropped. A message id seen before is dropped.
2. **Room log.** The message is recorded: channel, author, time, id, text. This happens for every message, before anything decides whether she replies. The channel's name is noted with it.
3. **Addressed?** If the message does not @mention her or reply to one of her messages, or the channel is on the no-reply list, the flow ends here. She has heard it and says nothing.
4. **Bot exchange rule.** Only when the author is a bot: she answers the first ping as a reply and a follow-up once more as a plain message.
5. **Batching.** She starts on a message at once; there is no wait for further messages (there was one of 2 seconds). Each message is its own message: a new one from the same member while she is working waits and gets its own reply (until it restarted the turn with both). Messages from one member that are waiting together are answered together. Other members wait their turn.
6. **Attachments.** Up to four files are downloaded, type-checked and stored for the turn.
7. **Security check.** A separate model call judges the message. If it is flagged, or the check fails, the turn runs with tools off, and neither the message nor her answer to it is offered to memory.
8. **Room context.** Recent messages from the same channel are read from the room log.

### Conversation

9. **Channel conversation.** The shared conversation for this channel is loaded from the history store.
10. **Recency window.** Only the last 40 messages are kept for the request.
11. **Media decision.** The turn is marked off, optional or requested, using the media ledger for the cooldown.
12. **Prompt assembly.** Rules, persona, the speaker and date cue, the media cue, room context, and facts recalled from Hindsight about the speaker and the topic. The turn closes with a note naming who she is answering and who she is not.

### Model loop

13. **Model call.** The request goes to the model server. An empty answer, or one that holds a stage note of the kind she is shown, is asked for once more, with a new seed and a note at the end of the conversation telling her to write her message.
14. **Tool calls.** If the model asks for a tool, it runs and the result goes back to the model. This repeats for up to eight model calls. Tools are limited to an allowlist, web fetches refuse private addresses, and a flagged turn has no tools.
15. **Leak replacement.** A reply that talks about her prompt, backend or machinery is replaced with an in-character brush-off. So is a reply that concedes she is holding something back, when the message asked about her instructions or was flagged.
16. **Save turn.** The message, the reply and any tool results are written to the history store. A copy of the facts she had from memory for the reply is kept beside it, for the monitor; she is never shown it.

### Outbox

17. **Media grant.** An image goes out only if she found it with a tool this turn, it passed the checks, and it has not been posted in the server in the last day. An image link she writes into her text is removed.
18. **Egress gate.** A reply containing a path, private address, secret or internal tool name is replaced whole by her refusal line.
19. **Send text.** The reply goes out as a Discord reply without a ping, in at most two messages. Now and then she adds an emoji reaction to a short message, and for one that needs no words the emoji is her whole answer.
20. **Upload image.** A granted image is uploaded as a file with the reply. If she added only an emoji, the image goes out without words.

### Background

These run only after two minutes with no message addressed to her, and stop when someone addresses her.

- **Ambient memory.** One batch of recent messages per channel is written to Hindsight, which extracts the facts. It goes as two documents: what the members said, without her lines, and her own lines, from which only facts about her alone are kept. Other bots' messages are context only. Records of someone merely asking her something are dropped.
- **Overheard images.** Images posted without pinging her were downloaded on arrival. Each is described by the model and the description goes to memory with its message. The file is kept; the owner clears the folder by hand.
- **Merge facts into summaries.** The bot asks Hindsight to merge related facts, a small round at a time. She is never shown the summaries, only the facts.
- **Taking back.** When a message is deleted while it is still in the room log, its batch is sent again without it. When a member asks her to forget something, the fact is retired and the chat still waiting in that channel is held back from memory. Whenever a fact leaves memory, the copies of it kept beside her replies go too.
- **Expiry jobs.** Unconfirmed hearsay after 90 days, room log rows after 7 days, stored history after 30 days. Files are never deleted by the bot; she can open one for 7 days.
