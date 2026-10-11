![Sanya](docs/banner.png)

# Sanya

[![licence](https://img.shields.io/github/license/cadexn/sanya)](https://opensource.org/license/mit)
[![release](https://img.shields.io/github/v/release/cadexn/sanya?label=release&style=flat)](https://github.com/cadexn/sanya/releases)
[![docs](https://github.com/cadexn/sanya/actions/workflows/docs.yml/badge.svg)](https://github.com/cadexn/sanya/actions/workflows/docs.yml)
[![python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![OpenSSF Scorecard](https://img.shields.io/ossf-scorecard/github.com/cadexn/sanya?label=openssf%20scorecard)](https://scorecard.dev/viewer/?uri=github.com/cadexn/sanya)

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

## What she looks like

Every message she hears, every reply with its timings, and what went to memory, as Sanya Monitor
shows them (on made-up data):

![The Live view: what she heard and answered, stage by stage](docs/monitor-live.png)

One reply followed from the message to memory: the security check, recall, each model call, the
tools she used, the facts she had, and her words:

![Following one message](docs/monitor-trace.png)

What she remembers, fact by fact, and what is waiting to go to memory:

![The Memory view](docs/monitor-memory.png)

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
     In the [Discord developer portal](https://discord.com/developers/applications): **New
     Application**, name it; under **Bot** add the bot user, switch on **Message Content Intent**,
     and copy the token (it goes in `.env`, nowhere else); under **OAuth2** make an invite link with
     the `bot` scope and the permissions View Channels, Send Messages, Send Messages in Threads, Read
     Message History, Attach Files, Add Reactions and Use External Emojis, open it, and add her to
     your server.
   - An OpenAI-compatible chat completions server with a model that handles tool calls (and ideally
     thinking and vision), running and reachable, for example llama.cpp's server, vLLM or LM Studio.
   - [Hindsight](https://hindsight.vectorize.io/), the memory service, running and reachable.
   - Optionally, keys for Brave Search, Tavily, Klipy and Giphy.

   [docs/SETUP.md](docs/SETUP.md) says how to get each one.

### With the launchers

2. **Double-click `Setup.cmd`.** It makes the Python environment, then asks for the bot token,
   which servers she is on, your account id, the model server, the memory service and the optional
   keys, checks each one against its service (and creates her memory bank), writes `.env` and
   `config\sanya.yaml`, and ends by checking that everything loads. Run it again any time to change
   an answer. [docs/SETUP.md](docs/SETUP.md) explains each thing it asks for.

3. **Double-click `Start Sanya.cmd`.** She runs in the background; the window says whether she
   came up. Then @mention her in a channel.

4. **Watch and stop.** `Sanya Monitor.cmd` opens the page that shows what she hears and answers;
   `Stop Sanya.cmd` stops her. She runs as whoever starts her, and only that account can stop her.

### By hand, in PowerShell

The launchers run these; run them yourself for the same result, or to see her events in the window.

2. **Make the Python environment** (once):

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-env.ps1
   ```

3. **Fill in `.env` and `config\sanya.yaml`**, either with the wizard:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1
   ```

   or by hand: copy `.env.example` to `.env` and put the token and keys in it, then fill the blanks
   in `config\sanya.yaml` (your account id, the servers, the model server and model id, the memory
   service and her bank, the model's names), as [docs/SETUP.md](docs/SETUP.md) describes, including
   how to make her memory bank. Then check it:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1 --check
   ```

4. **Start her**, in this window with her events shown, or in the background:

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Start-Sanya.ps1
   ```

5. **Watch and stop**:

   ```powershell
   .venv\Scripts\python.exe -m monitor
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts\Stop-Sanya.ps1
   ```

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
for, and change what you do not want. When the model is down her fixed lines blame the innkeeper, in her own world.

## Licence

MIT. See [LICENSE](LICENSE).
