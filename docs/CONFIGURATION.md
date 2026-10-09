# Configuration

Every setting in `config\sanya.yaml`, section by section. There are no defaults in code: a
missing or unknown key stops her from starting, and so does a wrong type. Ids are strings in
quotes. Times are in seconds, minutes, hours or days as the key says. Where a value was chosen
knowing a risk, [SETTINGS.md](SETTINGS.md) says what the risk is.

She reads the file when she starts. Change a value, then stop and start her.

## discord

| Key | Meaning |
|---|---|
| `owner_user_id` | Your account. The owner may have what she remembers about anyone listed and forgotten; everyone else only what is about themselves. Empty means nobody has that right. |
| `active_servers` | The servers she is switched on in. `null` means every server she is in. Elsewhere she neither listens nor answers. |
| `bots_as_people` | Bot accounts treated as people: what they say can become facts, and the bot exchange rule skips them. Every other bot is context only, and she never stores what it says. |
| `status_hours` | How often the line under her name changes, picked from `persona\status.yaml` for the time of day. |
| `no_reply_channels` | Channels she listens in but never answers in. A listed channel also silences the threads under it. |

## intake

| Key | Meaning |
|---|---|
| `batch_wait_seconds` | How long she waits for more messages from the same member before answering. 0 means she starts at once. |
| `max_turn_restarts` | How often a reply she has begun may be started over when the member writes again. 0 means never: the new message waits and gets its own reply. `null` means no limit. |
| `bot_exchange_ceiling` | How many times in a row she will answer bots in a channel before a person has spoken there. `null` means no ceiling, so two bots can go back and forth without end. |
| `attachments.max_count` | Files from one message that she takes in. Extra ones are noted as not accepted. |
| `attachments.max_mb` | The largest file she accepts. |
| `attachments.open_hours` | How long after it was sent she can open a file again with her tool. The file stays on disk; she never deletes it. |
| `attachments.download_seconds` | How long a file sent to her has to download. |

## room_log

The room log is every message in every channel she can read, kept in her database whether or
not it was said to her.

| Key | Meaning |
|---|---|
| `keep_days` | How long a line is kept. The look-back tool and the monitor reach this far. |
| `embed_chars` | How much of an embed's title and description is recorded with a message. |

## context

What she is shown of the channel around the message she answers.

| Key | Meaning |
|---|---|
| `minutes`, `max_messages` | The recent chat she is shown, from the room log. |
| `pictures` | The newest overheard pictures shown to her with a turn, once each. |
| `line_chars` | One overheard line is cut to this. |
| `quote_chars` | How much of a replied-to message she is shown. |
| `named_others` | How many other people the closing note "You are answering X, not ..." names. |

## conversation

One conversation per channel, shared by everyone in it: what was said to her and what she
answered.

| Key | Meaning |
|---|---|
| `window_messages` | How many of its latest messages the model is sent. |
| `keep_days` | How long the conversation is kept. |

## model

| Key | Meaning |
|---|---|
| `base_url` | The chat completions server, up to and including `/v1`. |
| `name` | The model id the server offers. |
| `max_calls_per_reply` | Model calls one reply may take, counting the calls between tool uses. The last is made without tools. |
| `retries` | How often a failed call is tried again, with a new seed and a note, before her "model failed" line goes out. |
| `timeout_seconds` | One call, including its wait in the server's queue. |
| `max_reply_tokens` | Thinking and reply together. A ceiling, not a target: ordinary banter uses a few hundred. |
| `thinking` | Whether the model thinks before answering. The security check and picture descriptions always run without. |
| `tools_per_call` | Tool requests honoured from one model answer; more are refused. |

## tools

| Key | Meaning |
|---|---|
| `search_order` | Which search services to try, in order, from `brave`, `tavily` and `duckduckgo`. A service with no key in `.env`, or at its cap, is passed over. |
| `search_monthly_caps` | Searches a month per service, so that a free allowance is never exceeded. |
| `search_results`, `search_chars` | What one search returns to her. |
| `page_chars` | A page, a text file or a PDF is cut at this. |
| `page_max_bytes`, `fetch_timeout_seconds`, `max_redirects` | Limits on one download. Every redirect is checked like a new address; private addresses are refused. |
| `browser_identity` | The User-Agent she presents to websites. |
| `look_back_lines` | Lines she is shown when she looks back through the room log. |
| `names_in_searches` | Whether a search may contain a member's name. The words of her searches are kept in the event log. |

## files

| Key | Meaning |
|---|---|
| `gif_frames` | Stills shown to the model for one GIF. |
| `max_megapixels`, `max_gif_frames` | A bigger picture or a longer GIF is refused. |
| `reminder_gb` | An event is logged when `data\files` passes this size. She never deletes a file. |
| `text_endings` | The text files she reads. |
| `describe_days` | A picture older than this is no longer described in the background. |
| `description_chars` | A picture's description is cut at this. |

## security_check

A separate model call screens each message addressed to her, with the instruction in
`persona\SECURITY_CHECK.md`. It answers SAFE, FLAG or BYPASS. A flagged turn loses her memory
tools and is kept out of memory; a BYPASS turn loses every tool.

| Key | Meaning |
|---|---|
| `enabled` | Whether the check runs at all. |
| `timeout_seconds` | How long the check may take, including its wait for the model server. |
| `flag_when_failed` | Whether a check that timed out, failed or gave no clear answer counts as FLAG. |

## media

| Key | Meaning |
|---|---|
| `spontaneous_chance` | The chance that a light-hearted turn lets her add an unasked image. |
| `cooldown_minutes` | The gap between unasked images in a channel. |
| `repeat_hours` | The same image or sticker is not posted unasked twice within this. No limit when someone asks. |
| `optional_searches`, `optional_candidates` | On an unasked turn: how many searches she may make and how many images she is shown from them. |
| `requested_candidates` | Images she is shown from one search when a member asked for one. |
| `requested_images` | Images one reply may carry when a member asks for several. Unasked it is always one. |
| `search_results` | Results examined from one image search, to find enough that pass the checks. |
| `download_seconds` | One found image has this long to arrive; they are all fetched at once. |
| `preview_px` | The size of the stills she chooses from. |
| `ledger_keep_days` | How long the record of posted images is kept. |

## emoji

| Key | Meaning |
|---|---|
| `chance` | The chance that a short, light-hearted message lets her add a reaction. |
| `cooldown_minutes` | The gap between reactions in a channel. |
| `short_chars` | A message longer than this is not short and gets no reaction. |

## send

| Key | Meaning |
|---|---|
| `max_messages` | A long reply is cut after this many Discord messages, with one of her sign-off lines. |
| `model_names` | Names of the model she runs on. A reply that says one of them is replaced by her refusal line. Whole words only, whatever the capitals; list the id and each half of it. |

## memory

| Key | Meaning |
|---|---|
| `hindsight_url` | The memory service. |
| `banks` | Server id to bank name. A server with no entry has no memory at all. |
| `recall_facts` | Facts put before her with each message. |
| `recall_lines`, `recall_chars` | How far back in the conversation memory's question reaches (members' lines only), and the cut on the whole question. |
| `recall_asker_facts` | Facts about the person she is answering, fetched by their name and put first. 0 for none. |
| `recall_own_facts` | Of the topic facts, at most this many may be about nobody but herself. |
| `quiet_minutes` | Background work (memory, picture descriptions, merging) runs only after this long without anyone addressing her. |
| `batch_messages`, `flush_minutes` | A channel's chat goes to memory when this many messages are waiting, or when the oldest has waited this long. |
| `hearsay_keep_days` | A fact marked unconfirmed (one person's claim about another) is removed after this. |
| `merge_idle_minutes`, `merge_every_minutes` | When she asks the service to merge facts into summaries. She is never shown the summaries. |
| `list_facts` | Facts shown when she lists what she remembers about someone. |
| `back_off_minutes` | After the memory service fails, background work leaves it alone this long. |
| `recall_seconds`, `read_seconds`, `write_seconds` | Time limits on the service: recall before a reply (past it she answers with no facts), a listing or look-up, and a write, which waits for the model server. |
| `standing_wish_words` | A record that someone asked her to do something is kept as a fact only when it has one of these words: then it is a wish about how she treats them ("don't swear at me"). |

## logs

| Key | Meaning |
|---|---|
| `keep_days` | How long `logs\events.log` and its daily files are kept. The log holds ids, counts and timings, never message text, except the words of her own searches. |

## paths

| Key | Meaning |
|---|---|
| `data_dir`, `log_dir` | Where her databases, files and logs go, relative to the project folder. |

## Sanya Monitor

`config\monitor.yaml` is the monitor's own file: the port, how far back it reads, whether to
open the browser, whether to list switched-off servers, and names to show for server and channel
ids. See [MONITOR.md](MONITOR.md).
