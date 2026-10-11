![Sanya](docs/banner.png)

# Sanya

[![licence](https://img.shields.io/github/license/cadexn/sanya)](https://opensource.org/license/mit)
[![release](https://img.shields.io/github/v/release/cadexn/sanya?label=release)](https://github.com/cadexn/sanya/releases)
[![docs](https://github.com/cadexn/sanya/actions/workflows/docs.yml/badge.svg)](https://github.com/cadexn/sanya/actions/workflows/docs.yml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)

**Guides: https://cadexn.github.io/sanya/** (setup, configuration, her character, the specification, the monitor)

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

## Running her

1. **Check what you have.** She needs all of these before anything else works:
   - Windows (the launchers and the monitor are Windows-only; the bot itself is plain Python).
   - Python 3.11 or newer on PATH, or [uv](https://docs.astral.sh/uv/), which fetches one. Either
     of these installs it, in PowerShell (open a new window afterwards so PATH is refreshed):

     ```powershell
     winget install --id Python.Python.3.12 -e          # Python, from the Microsoft package manager
     ```

     ```powershell
     powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"   # uv
     ```

     Check with `python --version` or `uv --version`.
   - A Discord application with a bot user, its token, and the Message Content intent switched on.
   - An OpenAI-compatible chat completions server with a model that handles tool calls (and ideally
     thinking and vision), running and reachable, for example llama.cpp's server, vLLM or LM Studio.
   - [Hindsight](https://hindsight.vectorize.io/), the memory service, running and reachable.
   - Optionally, keys for Brave Search, Tavily, Klipy and Giphy.

   [docs/SETUP.md](docs/SETUP.md) says how to get each one.

2. **Make the Python environment** (once).

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-env.ps1
   ```

3. **Run the wizard** (once). It asks for the bot token, which servers she is on, the model server,
   the memory service and the optional keys, checks each one, and writes `.env` and
   `config\sanya.yaml`. The services it checks are described in [docs/SETUP.md](docs/SETUP.md).

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```

4. **Check** that the config, the persona and the storage are in order.

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1 --check
   ```

5. **Start her.** Double-click `Start Sanya.cmd`; she runs in the background. To see her events in a
   window instead, use `scripts\start.ps1`. Then @mention her in a channel.

6. **Watch and stop.** `Sanya Monitor.cmd` opens the page that shows what she hears and answers;
   `Stop Sanya.cmd` stops her. She runs as whoever starts her, and only that account can stop her.

## Where to look

| File | What it holds |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Everything needed to run her: Discord, the model server, the memory service, keys, first start. |
| [docs/CONFIGURATION.md](docs/CONFIGURATION.md) | Every setting in `config\sanya.yaml`, what it does and what it costs. |
| [docs/PERSONA.md](docs/PERSONA.md) | How her character is written, and how to change it. |
| [docs/SETTINGS.md](docs/SETTINGS.md) | The specification: every behaviour, as decided by her owner, with the risks accepted. |
| [docs/FLOW.md](docs/FLOW.md) | The flow of one message through the bot, as a diagram and as numbered steps. |
| [docs/MONITOR.md](docs/MONITOR.md) | Sanya Monitor: what the page shows, what it reads, and what it cannot do. |

## Her character is hers

She swears in nearly every message, mocks what people do and say, and will not admit to being a
bot. That is the character her owner chose, written in `persona\`, and the settings in
`config\sanya.yaml` match it: safe search is off, and she posts what she is asked for. Read
[docs/SETTINGS.md](docs/SETTINGS.md) before switching her on in a server you are responsible
for, and change what you do not want. Her fixed lines blame her owner, cadexn, by name when the
model is down; put your own name there (`persona\SOUL.md` and `persona\lines.yaml`).

## Licence

MIT. See [LICENSE](LICENSE).
