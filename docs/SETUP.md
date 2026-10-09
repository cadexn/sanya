# Setting her up

Everything needed to run Sanya, in order. Each step says how to check it before going on. The
whole thing is a Discord application, a model server, a memory service, this folder, and a file
of keys.

**The quick way.** Do steps 1 to 4 (the application, the model server, the memory service, this
folder's Python environment), then run the wizard:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
```

It asks for the token and checks it against Discord, lists the servers she is in and lets you
pick, asks for your account id, finds the models your server offers and tries one small request
with tools, reaches the memory service and creates her bank with the settings below, asks for the
optional keys, and only then writes `.env` and `config\sanya.yaml`. Steps 5 and 6 say what it
did; nothing stops you doing them by hand instead.

## 1. The Discord application

1. Open the [Discord developer portal](https://discord.com/developers/applications) and create an
   application. Under **Bot**, add a bot user. She uses the bot's display name as her own, so call
   it Sanya, or whatever you rename her to (see [PERSONA.md](PERSONA.md)).
2. Still under **Bot**, switch on **Message Content Intent**. It is a privileged intent, and
   without it she cannot read what anyone writes. She does not need the Presence or Server Members
   intents. Uncheck **Public Bot** unless you want strangers to be able to add her.
3. Copy the bot token. It goes in `.env` in step 5, and nowhere else.
4. Under **OAuth2**, make an invite link with the `bot` scope and these permissions: View
   Channels, Send Messages, Send Messages in Threads, Read Message History, Attach Files, Add
   Reactions, Use External Emojis. Open the link and add her to your server.

She asks Discord for four intents: guilds, guild messages, message content, and emojis and
stickers (so she can use the server's own emoji and send its stickers). She ignores direct
messages entirely.

Note two ids from Discord, with Developer Mode on under **Advanced** in your user settings:
right-click the server for its id, and your own name for yours. Both go in the config in step 6.

## 2. The model server

She talks to an OpenAI-compatible chat completions endpoint and nothing else. What it must do:

| She sends | What the server must support |
|---|---|
| `POST /v1/chat/completions` with `model`, `messages`, `max_tokens` | The ordinary form. |
| `tools` in the request, `tool_calls` in the answer | Tool calling. Without it she can neither search nor post images nor use her memory tools. |
| `temperature` on some calls | The security check and picture descriptions run at 0, so that the same message always gets the same verdict. Her replies use the server's default sampling. |
| `reasoning_effort: "none"` on the check and the describer | How she switches thinking off for the calls that must answer in a word. A server that ignores the field still works, more slowly. |
| Thinking returned in a separate field (`reasoning_content`), not in the text | With `model.thinking: true` she expects the model's thinking outside the reply. A model that writes its thinking into the text will post some of it. If your model does not think, set `model.thinking: false`. |
| `image_url` parts in a user message | Vision, for pictures sent to her and for describing pictures posted around her. Without it those two things fail and she says so. |
| `usage` in the answer | Token counts, for the monitor. Optional. |

She was built and run against a local server answering one request at a time, with a model of
about 4B parameters that thinks, calls tools and sees pictures. A reply takes two model calls at
least (the check and the reply), more when she uses tools, so a slow server shows in her reply
times. The limits for one call are in the config (`model.timeout_seconds`, `model.max_reply_tokens`).

Check it: `GET http://127.0.0.1:8080/v1/models` lists the model ids the server offers. The id
goes in `model.name`; the address in `model.base_url`.

## 3. The memory service

Her long-term memory is [Hindsight](https://hindsight.vectorize.io/), an open-source memory
service. She was written against version 0.10. Install it as its documentation says (it needs a
model of its own for extracting facts, which can be the same server as above, and runs its
embeddings and reranker locally), and start it; by default she expects it at
`http://127.0.0.1:8888`. If it runs with an API key, the key goes in `.env` as `HINDSIGHT_API_KEY`.

### Her bank

Hindsight keeps memories in banks. She uses one bank per server, named in the config under
`memory.banks`. **She does not create or configure the bank herself**: she only reads and writes
one that exists. The wizard makes it (`PUT /v1/default/banks/<name>` with the first five
settings, then `PATCH /v1/default/banks/<name>/config` with the rest). By hand, use the same two
calls, or Hindsight's control panel, with these settings:

| Setting | Value | Why |
|---|---|---|
| `retain_extraction_mode` | `concise` | Short facts, one per thing said. |
| `retain_mission` | The text of `persona\MEMORY_MISSION.md` | It tells the extractor what a fact is, what is hearsay, and to skip banter. Word for word. |
| `retain_chunk_size` | `3000` | A batch of chat is about that long. |
| `retain_chunk_batch_size` | `1` | One batch at a time. |
| `enable_graph_retrieval` | `true` | |
| `enable_temporal_retrieval` | `true` | |
| `enable_observations` | `true` | |
| `enable_auto_consolidation` | `false` | She asks for merging herself, in quiet periods (`memory.merge_idle_minutes`). |
| `consolidation_max_memories_per_round` | `24` | How many facts one merge round takes. |
| `consolidation_llm_parallelism` | `1` | |
| `store_document_text` | `false` | The chat itself is not kept in the memory service, only the facts drawn from it. |
| `recall_include_chunks` | `false` | |
| `audit_log_enabled` | `false` | Otherwise the service keeps the text of every recall query. |
| `memory_defense` | on, with `sensitive_data` set to `redact` | A password said in chat does not become a fact. The wizard does not set this one; do it in Hindsight's own settings. |

What she calls on the bank: `POST memories/recall`, `POST memories` (a batch of chat as a
document), `GET memories` and `GET memories/<id>`, `PATCH memories/<id>` with `state: invalidated`
(what "forgetting" does: the fact leaves recall but stays in the service's archive), `DELETE
documents/<id>` (a deleted message takes its batch back), and `POST consolidate`.

On the service itself, these were the settings she ran with: one model call at a time, a 600
second model timeout, reasoning off for the service's own calls, embeddings and reranker on the
CPU, the background worker on with two slots and one of them reserved for merging.

Check it: `GET http://127.0.0.1:8888/health` answers, and `GET /v1/default/banks/<name>/config`
shows the settings above.

## 4. This folder

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-env.ps1
```

This makes `.venv` with the exact package versions in `requirements.lock`, using `uv` if it is on
PATH (it fetches a Python 3.11 if the PC has none) and otherwise the `python` on PATH, which must
be 3.11 or newer. Anywhere but Windows: `python3 -m venv .venv` and
`.venv/bin/pip install -r requirements.lock`, then run her with `.venv/bin/python -m sanya`.

## 5. Keys

Copy `.env.example` to `.env` and fill it in. Only the bot token is required.

| Key | For | Without it |
|---|---|---|
| `DISCORD_BOT_TOKEN` | Connecting to Discord | She does not start. |
| `HINDSIGHT_API_KEY` | The memory service, if it needs one | Leave empty otherwise. |
| `BRAVE_API_KEY` | Web search, picture search and memes ([Brave Search API](https://brave.com/search/api/)) | Search falls through to Tavily, then DuckDuckGo; picture and meme searches use what is left. |
| `TAVILY_API_KEY` | Web search ([Tavily](https://tavily.com/)) | Falls through to DuckDuckGo. |
| `KLIPY_API_KEY` | GIFs and still memes ([Klipy](https://klipy.com/)) | GIFs come from Giphy. |
| `GIPHY_API_KEY` | GIFs ([Giphy](https://developers.giphy.com/)) | With neither GIF key she posts no GIFs. |

`.env` is ignored by git. Every value in it is also a secret she must never post: the outgoing
gate checks each reply against them.

## 6. The config

`config\sanya.yaml` ships with every value her owner chose, and these left blank for you:

| Setting | Put |
|---|---|
| `discord.owner_user_id` | Your Discord account id, in quotes. The owner may have what she remembers about anyone listed and forgotten; everyone else only their own. |
| `discord.active_servers` | `null` to let her listen everywhere she is, or a list of server ids in quotes. |
| `model.base_url`, `model.name` | Your model server and the model id from step 2. |
| `memory.hindsight_url`, `memory.banks` | The memory service, and your server id mapped to the bank from step 3, for example `{"123456789012345678": sanya}`. A server with no bank has no memory. |
| `send.model_names` | The names of your model, so that she never says them, for example `["llama-3.3", "70b-instruct"]`. |

Keep the quotes around ids, or YAML reads them as numbers and the config is refused. Every other
setting is explained in [CONFIGURATION.md](CONFIGURATION.md); read the ones under **Risk
accepted** in [SETTINGS.md](SETTINGS.md) before switching her on in a server you answer for.

Check it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1 --check
```

It reads the config and the persona, opens the storage, and prints `config ok; persona ok;
storage ok`, or says exactly which setting is missing, unknown or wrong. It does not connect to
anything.

## 7. First start

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
```

This runs her in the window, with her events on the screen. Look for `connected`, then
`guild_ready` for your server. Mention her in a channel; her reply should come within a few
seconds on an idle model server. `Ctrl+C` stops her.

Then, for every day: `Start Sanya.cmd` runs her in the background and `Stop Sanya.cmd` stops her
(only the Windows account that started her can). There is no automatic restart. `Sanya
Monitor.cmd` opens the monitor page, which shows every message, every reply with its timings, and
what went to memory; see [MONITOR.md](MONITOR.md). To see the page before she has recorded
anything, `.venv\Scripts\python.exe monitor\demo\demo.py` serves it on made-up data.

## What she writes to disk

| Where | What | Kept |
|---|---|---|
| `data\guilds\<server>.db` | The room log, her conversations, ledgers, members' names | Room log 7 days, conversations 30 days |
| `data\files\<server>\<day>\` | Every picture and file posted where she can read, and the files sent to her | Until you delete them; she can open one for 7 days |
| `data\usage.json` | Searches made this month, per service | Rolls over monthly |
| `logs\events.log` | What happened: ids, counts, timings and the words of her own searches, never message text | 30 days |
| The memory bank | The facts drawn from chat | Until forgotten; hearsay 90 days |

## When something is wrong

| Symptom | Look at |
|---|---|
| `config error: ...` at start | The message names the setting. `--check` says the same without connecting. |
| `Sanya is already running` | Another copy holds `data\sanya.lock`. Stop it with `Stop Sanya.cmd` from the account that started it. |
| She connects but never answers | Is the server in `discord.active_servers` (or is it `null`)? Is the channel in `discord.no_reply_channels`? Did you @mention her or reply to her message? Her name in plain text does not count. The event log says `guild_inactive` for a server she is switched off in. |
| She answers with "asleep in my pen" lines | The model server did not answer within `model.timeout_seconds`, twice. The monitor's Health tab shows whether it is up. |
| `model_call` events fail at once | `model.name` is not an id the server offers, or `model.base_url` is wrong. |
| She never remembers anything | `memory.banks` has no entry for the server, the bank does not exist, or the memory service is down. Memory is written only after two minutes without anyone addressing her (`memory.quiet_minutes`); the monitor's Memory tab shows what is waiting. |
| Pictures are never described | The model has no vision, or `files.describe_days` passed. |
| No GIFs | No `KLIPY_API_KEY` or `GIPHY_API_KEY` in `.env`; the `search_services` event at start says which keys were found. |
