"""First-run setup:  python -m sanya setup

Asks for what she needs, checks each thing against the service it belongs to, and writes
.env and config/sanya.yaml at the end. Nothing leaves this PC except the checks: the token
goes to Discord, one small request to the model server, and the bank's settings to the memory
service. Run it again at any time: what is already set is offered to keep.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional

from .config import ConfigError, _read_env_file, load_config
from .persona import load_persona
from .store.db import Store

ROOT = Path(__file__).resolve().parent.parent
CONFIG = ROOT / "config" / "sanya.yaml"
ENV = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"
MISSION = ROOT / "persona" / "MEMORY_MISSION.md"
DISCORD = "https://discord.com/api/v10"
# Application flags that say the Message Content intent is switched on (full, or limited to under 100 servers).
MESSAGE_CONTENT = (1 << 18) | (1 << 19)
KEYS = (("BRAVE_API_KEY", "Brave Search, for web search, pictures and memes"),
        ("TAVILY_API_KEY", "Tavily, for web search"),
        ("KLIPY_API_KEY", "Klipy, for GIFs and still memes"),
        ("GIPHY_API_KEY", "Giphy, for GIFs"))
# The memory bank's settings, as she runs with them; docs/SETUP.md says why each one.
BANK = {"retain_extraction_mode": "concise", "retain_chunk_size": 3000, "enable_graph_retrieval": True,
        "enable_temporal_retrieval": True, "enable_observations": True}
BANK_CONFIG = {"enable_auto_consolidation": False, "consolidation_max_memories_per_round": 24,
               "consolidation_llm_parallelism": 1, "retain_chunk_batch_size": 1, "store_document_text": False,
               "recall_include_chunks": False, "audit_log_enabled": False}


class Stop(Exception):
    """The person chose to stop, or something they must fix first."""


# --- talking to the person -------------------------------------------------------------------

def say(text: str = "") -> None:
    print(text, flush=True)


def ask(prompt: str, default: str = "") -> str:
    shown = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{shown}: ").strip()
    except EOFError:
        raise Stop("no answer") from None
    return answer or default


def ask_secret(prompt: str) -> str:
    """A token or key, typed in the clear like any other answer, so that it can be seen and pasted. It is
    never printed back, and goes only into .env."""
    return ask(prompt)


def yes(prompt: str, default: bool = True) -> bool:
    answer = ask(f"{prompt} ({'Y/n' if default else 'y/N'})").lower()
    return default if not answer else answer.startswith("y")


def choose(prompt: str, options: list[str], one: bool = False) -> list[int]:
    """Numbers picked from a list, or every one of them for "all". With ``one``, a single number, 1 by default."""
    for number, option in enumerate(options, 1):
        say(f"  {number}. {option}")
    while True:
        answer = ask(prompt, "1" if one else "all").lower()
        if one and answer.isdigit() and 1 <= int(answer) <= len(options):
            return [int(answer) - 1]
        if one:
            say(f"Give one number from 1 to {len(options)}.")
            continue
        if answer == "all":
            return list(range(len(options)))
        picked = [int(part) - 1 for part in re.findall(r"\d+", answer)]
        if picked and all(0 <= index < len(options) for index in picked):
            return sorted(set(picked))
        say("Give numbers from the list, separated by spaces, or 'all'.")


# --- talking to services ---------------------------------------------------------------------

def http(method: str, url: str, *, headers: Optional[dict] = None, body: Any = None,
         timeout: float = 20) -> tuple[int, Any]:
    """Status and decoded body. A connection that fails gives status 0 and the reason."""
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("User-Agent", "Sanya setup (local)")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    # This PC's own services are reached directly, never through a proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({})) if "127.0.0.1" in url or "localhost" in url \
        else urllib.request.build_opener()
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.status, _decoded(response.read())
    except urllib.error.HTTPError as error:
        return error.code, _decoded(error.read())
    except (urllib.error.URLError, OSError, ValueError) as error:
        return 0, str(getattr(error, "reason", error))


def _decoded(raw: bytes) -> Any:
    text = raw.decode("utf-8", "replace")
    try:
        return json.loads(text)
    except ValueError:
        return text


def _detail(data: Any) -> str:
    if isinstance(data, dict):
        for key in ("message", "detail", "error"):
            if key in data:
                return str(data[key])[:200]
    return str(data)[:200]


# --- the steps ---------------------------------------------------------------------------------

def discord_step(env: dict[str, str]) -> tuple[str, list[dict]]:
    say("\n1. Discord")
    token = env.get("DISCORD_BOT_TOKEN", "")
    if token and yes("A bot token is already in .env. Keep it?"):
        pass
    else:
        say("The bot token is under Bot in the Discord developer portal. It stays on this PC, in .env.")
        token = ask_secret("Bot token")
    if not token:
        raise Stop("She cannot start without a token.")
    headers = {"Authorization": f"Bot {token}"}
    status, me = http("GET", f"{DISCORD}/users/@me", headers=headers)
    if status != 200:
        raise Stop(f"Discord refused the token (HTTP {status}: {_detail(me)}). Check it and run this again.")
    say(f"Discord knows this token as {me.get('username', '?')}.")
    status, app = http("GET", f"{DISCORD}/applications/@me", headers=headers)
    if status == 200 and not int(app.get("flags", 0)) & MESSAGE_CONTENT:
        say("WARNING: the Message Content intent is not switched on for this application. Without it she cannot")
        say("read what anyone writes. Switch it on under Bot in the developer portal, then run this again.")
    status, guilds = http("GET", f"{DISCORD}/users/@me/guilds", headers=headers)
    if status != 200 or not isinstance(guilds, list):
        say(f"Could not list her servers (HTTP {status}). Add her to a server with the invite link in docs/SETUP.md.")
        guilds = []
    elif not guilds:
        say("She is in no server yet. Add her with the invite link in docs/SETUP.md; she can still be set up now.")
    else:
        say("She is in these servers:")
    return token, guilds


def servers_step(guilds: list[dict]) -> tuple[Optional[list[str]], list[dict]]:
    """Which servers she is switched on in: a list of ids, or None for every server."""
    if not guilds:
        return None, []
    picked = choose("Switch her on in which? Numbers, or 'all'", [f"{g.get('name', '?')} ({g.get('id')})" for g in guilds])
    chosen = [guilds[index] for index in picked]
    if len(chosen) == len(guilds) and len(guilds) > 1 and yes(
            "Also any server she joins later, without editing the config?", default=False):
        return None, chosen
    return [str(g["id"]) for g in chosen], chosen


def owner_step(current: str) -> str:
    say("\n2. The owner")
    say("Your own Discord account id (Developer Mode on, right-click your name, Copy User ID). The owner may")
    say("have what she remembers about anyone listed and forgotten; everyone else only their own. Empty for nobody.")
    while True:
        answer = ask("Your account id", current)
        if answer == "" or answer.isdigit():
            return answer
        say("An id is digits only.")


def model_step(current_url: str, current_name: str) -> dict[str, Any]:
    say("\n3. The model server")
    while True:
        base_url = ask("Chat completions address, up to and including /v1", current_url or "http://127.0.0.1:8080/v1").rstrip("/")
        status, listing = http("GET", f"{base_url}/models")
        if status == 200:
            break
        say(f"No model list at {base_url}/models (HTTP {status}: {_detail(listing)}). Is the server running?")
        if not yes("Try another address?"):
            raise Stop("The model server must be running for this step.")
    ids = [item.get("id", "") for item in (listing.get("data", []) if isinstance(listing, dict) else []) if item.get("id")]
    if ids:
        say("The server offers:")
        name = ids[choose("Which model? One number", ids, one=True)[0]]
    else:
        name = ask("The server lists no models. The model id to send", current_name)
    say("One small request, to see what the server answers with...")
    tool = {"type": "function", "function": {"name": "ping", "description": "Answers with pong.",
                                             "parameters": {"type": "object", "properties": {}}}}
    status, answer = http("POST", f"{base_url}/chat/completions", timeout=120, body={
        "model": name, "max_tokens": 64, "tools": [tool],
        "messages": [{"role": "user", "content": "Call the tool named ping, then say ok."}]})
    thinking = False
    if status != 200:
        say(f"The request failed (HTTP {status}: {_detail(answer)}).")
        if "tool" in _detail(answer).lower():
            say("It looks like the server does not take tools. Without tool calls she can neither search, use her memory tools nor post images.")
    else:
        message = ((answer.get("choices") or [{}])[0].get("message") or {}) if isinstance(answer, dict) else {}
        if message.get("tool_calls"):
            say("Tool calls work.")
        else:
            say("The server accepted the tools; the model did not call one on this try. Fine for a first start.")
        thinking = bool(message.get("reasoning_content") or message.get("reasoning"))
        say("The model thinks before answering." if thinking else "No separate thinking came back.")
    thinking = yes("Run her with thinking on?", default=thinking)
    short = name.rsplit("/", 1)[-1]
    suggested = [name] + ([short] if short != name else [])
    say("Names of the model she must never say. The id itself is one; add the halves people would recognise.")
    words = ask("Model names, separated by commas", ", ".join(suggested))
    model_names = [word.strip() for word in words.split(",") if word.strip()]
    return {"model.base_url": base_url, "model.name": name, "model.thinking": thinking, "send.model_names": model_names}


def memory_step(env: dict[str, str], current_url: str, chosen: list[dict]) -> dict[str, Any]:
    say("\n4. The memory service (Hindsight)")
    key = env.get("HINDSIGHT_API_KEY", "")
    while True:
        url = ask("Hindsight address", current_url or "http://127.0.0.1:8888").rstrip("/")
        status, health = http("GET", f"{url}/health")
        if status == 200:
            break
        say(f"No answer at {url}/health (HTTP {status}: {_detail(health)}).")
        if not yes("Try another address?"):
            raise Stop("The memory service must be running for this step; or run this again later and skip it.")
    if key and yes("An API key for it is already in .env. Keep it?"):
        pass
    else:
        say("If it needs an API key, paste it here: a right-click pastes into a PowerShell window.")
        key = ask_secret("Its API key, if it needs one (empty for none)")
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    # A key the service refuses can be typed again; the earlier answers are not lost.
    for attempt in range(3):
        status, probe = http("GET", f"{url}/v1/default/banks", headers=headers)
        if status not in (401, 403):
            break
        say("The memory service wants an API key, or refuses this one." + (" Last try." if attempt == 2 else ""))
        if attempt == 2:
            raise Stop("The memory service refused the key three times.")
        key = ask_secret("Its API key")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    banks: dict[str, str] = {}
    if not chosen:
        say("No server chosen, so no bank is set up now. Put the server id and a bank name under memory.banks later.")
        return {"memory.hindsight_url": url, "HINDSIGHT_API_KEY": key, "memory.banks": banks}
    mission = MISSION.read_text(encoding="utf-8").strip()
    for guild in chosen:
        guild_id = str(guild["id"])
        default = "sanya" if len(chosen) == 1 else f"sanya-{guild_id[-4:]}"
        bank = ask(f"Bank name for {guild.get('name', guild_id)}", default)
        status, existing = http("GET", f"{url}/v1/default/banks/{bank}/config", headers=headers)
        if status in (401, 403):
            raise Stop("The memory service wants an API key, or refuses this one.")
        if status == 200:
            say(f"The bank '{bank}' exists.")
            if not yes("Apply her settings to it? (Its facts stay.)"):
                banks[guild_id] = bank
                continue
        elif status != 404:
            say(f"Could not look at the bank (HTTP {status}: {_detail(existing)}). Skipping it; set it up by hand.")
            banks[guild_id] = bank
            continue
        elif not yes(f"Create the bank '{bank}' with her settings?"):
            banks[guild_id] = bank
            continue
        status, made = http("PUT", f"{url}/v1/default/banks/{bank}", headers=headers,
                            body={"name": bank, "retain_mission": mission, **BANK})
        if status != 200:
            say(f"Creating or updating the bank failed (HTTP {status}: {_detail(made)}). See docs/SETUP.md for the settings.")
            banks[guild_id] = bank
            continue
        status, patched = http("PATCH", f"{url}/v1/default/banks/{bank}/config", headers=headers,
                               body={"updates": BANK_CONFIG})
        if status == 200:
            say(f"Bank '{bank}' is ready. Memory defence (redacting sensitive data) is one setting this cannot apply;")
            say("switch it on in Hindsight's own settings if you want it, as docs/SETUP.md describes.")
        else:
            say(f"The bank exists but its settings could not all be applied (HTTP {status}: {_detail(patched)}).")
            say("Apply the ones under 'Her bank' in docs/SETUP.md by hand.")
        banks[guild_id] = bank
    return {"memory.hindsight_url": url, "HINDSIGHT_API_KEY": key, "memory.banks": banks}


def keys_step(env: dict[str, str]) -> dict[str, str]:
    say("\n5. Keys for outside services (each optional; empty to skip)")
    values = {}
    for name, what in KEYS:
        if env.get(name) and yes(f"{name} is already in .env ({what}). Keep it?"):
            values[name] = env[name]
            continue
        values[name] = ask_secret(f"{name}: {what}")
    return values


# --- writing the two files ----------------------------------------------------------------------

def patch_config(text: str, updates: dict[str, Any]) -> str:
    """Set values in the config file's text, keeping every comment. Keys are dotted: section.key."""
    for dotted, value in updates.items():
        section, key = dotted.split(".", 1)
        pattern = re.compile(rf"^(?P<section>{re.escape(section)}:[^\n]*\n(?:(?:[ \t]+[^\n]*|)\n)*?)"
                             rf"(?P<indent>[ \t]+){re.escape(key)}:[ \t]*(?P<value>[^#\n]*?)(?P<comment>[ \t]+#[^\n]*)?$",
                             re.M)
        match = pattern.search(text)
        if match is None:
            raise ConfigError(f"{dotted} is not in {CONFIG.name}")
        rendered = _yaml_value(value)
        start, end = match.start("indent"), match.end()
        comment = match.group("comment") or ""
        text = text[:start] + f"{match.group('indent')}{key}: {rendered}{comment}" + text[end:]
    return text


def _yaml_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)  # a flow sequence or mapping, which YAML reads
    return json.dumps(str(value))


def write_env(values: dict[str, str]) -> None:
    """Write .env, keeping any line that is not one of ours. Starts from .env.example when there is none."""
    source = ENV if ENV.exists() else ENV_EXAMPLE
    lines = source.read_text(encoding="utf-8-sig").splitlines() if source.exists() else []
    done = set()
    out = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in values:
            out.append(f"{key}={values[key]}")
            done.add(key)
        else:
            out.append(line)
    for key, value in values.items():
        if key not in done:
            out.append(f"{key}={value}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")


# --- the whole thing ------------------------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    say("Sanya: first-run setup. Everything asked for stays on this PC, in .env and config\\sanya.yaml.")
    say("Nothing is written until the end; Ctrl+C stops with nothing changed.")
    try:
        env = _read_env_file(ENV)
        import yaml
        current = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
        token, guilds = discord_step(env)
        active, chosen = servers_step(guilds)
        owner = owner_step(str(current.get("discord", {}).get("owner_user_id") or ""))
        model = model_step(str(current.get("model", {}).get("base_url") or ""), str(current.get("model", {}).get("name") or ""))
        memory = memory_step(env, str(current.get("memory", {}).get("hindsight_url") or ""), chosen)
        keys = keys_step(env)
    except Stop as stop:
        say(f"\nStopped: {stop} Nothing was written.")
        return 2
    except KeyboardInterrupt:
        say("\nStopped. Nothing was written.")
        return 2

    updates = {"discord.owner_user_id": owner, "discord.active_servers": active,
               **model, "memory.hindsight_url": memory["memory.hindsight_url"], "memory.banks": memory["memory.banks"]}
    CONFIG.write_text(patch_config(CONFIG.read_text(encoding="utf-8"), updates), encoding="utf-8")
    write_env({"DISCORD_BOT_TOKEN": token, "HINDSIGHT_API_KEY": memory["HINDSIGHT_API_KEY"], **keys})
    say(f"\nWritten: {ENV.name} and {CONFIG.relative_to(ROOT)}.")

    try:
        config = load_config(CONFIG)
        load_persona(ROOT / "persona")
        store = Store(ROOT / config.paths.data_dir if not Path(config.paths.data_dir).is_absolute() else Path(config.paths.data_dir))
        for guild_id in config.memory.banks:
            store.guild(guild_id)
        store.close()
    except ConfigError as error:
        say(f"The config does not load: {error}")
        return 2
    say("config ok; persona ok; storage ok")
    say("\nNext: 'Start Sanya.cmd' starts her in the background, 'Stop Sanya.cmd' stops her, and 'Sanya Monitor.cmd'")
    say("shows what she is doing. Mention her in a channel and she answers. docs/SETUP.md has the rest.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
