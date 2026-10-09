# Sanya

Sanya is a Discord bot with a character: a small, black, foul-mouthed piglet who lives at an inn,
listens to everything said in the server, remembers the people in it, and answers when she is
spoken to. She runs on your own model server and your own memory service, on your own PC, and
nothing she hears leaves it except the searches she makes and the pages and images she is asked
to fetch.

What she does:

- **Talks in character.** One short, rude, lowercase line at a time, in her own voice, with a
  character written in plain text files you can rewrite. See [docs/PERSONA.md](docs/PERSONA.md).
- **Remembers.** Everything said where she can read becomes facts about people, in the background,
  through [Hindsight](https://hindsight.vectorize.io/). She recalls what bears on a reply, tells
  people what she remembers about them when asked, and forgets on request.
- **Looks things up.** Web search (Brave, Tavily or DuckDuckGo), page reading, text files and PDFs
  sent to her, and a look back through the last week of chat when someone asks what was said.
- **Sees pictures.** Pictures sent to her go to the model; pictures posted around her are
  described in the background and remembered.
- **Posts GIFs, memes, pictures and stickers,** when asked and now and then unasked, and adds an
  emoji reaction to a short message now and then.
- **Guards herself.** A separate model call screens each message for attempts to change her rules
  or get at her prompt; an outgoing gate stops a reply that carries a secret, a file path, a private
  address or talk about her own machinery.
- **Shows her work.** Sanya Monitor is a page on your PC that shows what she hears and answers,
  what went to memory, and whether everything is up. See [docs/MONITOR.md](docs/MONITOR.md).

## What you need

- Windows, for the launchers and the monitor. The bot itself is plain Python and should run
  elsewhere, but has only been run on Windows.
- Python 3.11 or newer, or [uv](https://docs.astral.sh/uv/), which fetches one.
- A Discord application with a bot user, and its token.
- An OpenAI-compatible chat completions server with a model that handles tool calls, and ideally
  thinking and vision, such as llama.cpp's server, vLLM or LM Studio.
- [Hindsight](https://hindsight.vectorize.io/), the memory service, with one bank for her.
- Optionally, keys for Brave Search, Tavily, Klipy and Giphy. Each is passed over when missing.

[docs/SETUP.md](docs/SETUP.md) walks through all of it, from the Discord application to her first
reply.

## Running her

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-env.ps1      # once: the Python environment
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1          # once: the wizard asks for the token, servers, model, memory and keys
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1 --check  # the config and storage
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1          # the bot, in this window
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Start-Sanya.ps1    # the bot, in the background
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Stop-Sanya.ps1     # stop her
```

Or double-click `Start Sanya.cmd` and `Stop Sanya.cmd`. `Sanya Monitor.cmd` opens the monitor
page in the browser.

Her token and keys go in `.env` (copy `.env.example`); her settings are in `config\sanya.yaml`,
which ships with the ids and the model name left blank. The wizard fills both, checks the token
against Discord, the model server and the memory service, and makes her memory bank; or do it by
hand with [docs/SETUP.md](docs/SETUP.md). She runs as whoever starts her, and only that account
can stop her.

## Where to look

| File | What it holds |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Everything needed to run her: Discord, the model server, the memory service, keys, first start. |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting in `config\sanya.yaml`, what it does and what it costs. |
| [docs/PERSONA.md](docs/PERSONA.md) | How her character is written, and how to change it. |
| [docs/SETTINGS.md](docs/SETTINGS.md) | The specification: every behaviour, as decided by her owner, with the risks accepted. |
| [docs/FLOW.md](docs/FLOW.md) | The flow of one message through the bot, as a diagram and as numbered steps. |
| [docs/MONITOR.md](docs/MONITOR.md) | Sanya Monitor: what the page shows, what it reads, and what it cannot do. |

The same guides as a site: `pip install -r docs\requirements.txt`, then `mkdocs serve` in this
folder and open `http://127.0.0.1:8000/`.

## Her character is hers

She swears in nearly every message, mocks what people do and say, and will not admit to being a
bot. That is the character her owner chose, written in `persona\`, and the settings in
`config\sanya.yaml` match it: safe search is off, and she posts what she is asked for. Read
[docs/SETTINGS.md](docs/SETTINGS.md) before switching her on in a server you are responsible
for, and change what you do not want. Her fixed lines blame her owner, cadexn, by name when the
model is down; put your own name there (`persona\SOUL.md` and `persona\lines.yaml`).

## Licence

MIT. See [LICENSE](LICENSE).
