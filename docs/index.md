# Sanya

A Discord bot with a character: a small, black, foul-mouthed piglet who lives at an inn, listens to
everything said in the server, remembers the people in it, and answers when she is spoken to. She
runs on your own model server and your own memory service, on your own PC.

| Guide | What it is for |
|---|---|
| [Setup](SETUP.md) | Everything needed to run her, in order: the Discord application, the model server, the memory service and her bank, keys, the config, first start, and what to look at when something is wrong. The first-run wizard, `python -m sanya setup`, does the middle of it for you. |
| [Configuration](CONFIGURATION.md) | Every setting in `config\sanya.yaml`, section by section. |
| [Her character](PERSONA.md) | The persona files, her fixed lines, the security check, and how to change her. |
| [The specification](SETTINGS.md) | Every behaviour, as her owner decided it, with the risks accepted. Read it before switching her on in a server you answer for. |
| [Message flow](FLOW.md) | How one message moves through the bot, as a diagram and as steps. |
| [Sanya Monitor](MONITOR.md) | The page on your PC that shows what she hears, answers and remembers. |

The short version, for a PC that has Python or [uv](https://docs.astral.sh/uv/):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup-env.ps1   # the Python environment
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\setup.ps1       # the wizard: token, servers, model, memory, keys
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\start.ps1       # her, in this window
```

Her character is her owner's: she swears in nearly every message and will not admit to being a
bot. Everything about her is text under `persona\`, and everything she does is a setting under
`config\`.
