# PROJECT KNOWLEDGE BASE

**Generated:** 2026-02-27
**Commit:** 7be1fda
**Branch:** main

## OVERVIEW

HumanitZ Discord bot — real-time server status embed, bidirectional chat bridge, player tracking via RCON. Python 3.12 + discord.py 2.6 + uv package manager, src-layout.

## STRUCTURE

```
humanitz-bot/
├── src/humanitz_bot/
│   ├── __main__.py          # Entry: logging, signals, asyncio.run(main())
│   ├── bot.py               # Factory: create_bot() → intents, cog loading
│   ├── config.py            # Settings dataclass from .env with validation
│   ├── rcon_client.py       # Sync TCP socket RCON (HumanitZ-specific quirks)
│   ├── cogs/
│   │   ├── server_status.py # 30s tasks.loop: RCON fetch → embed → chart
│   │   └── chat_bridge.py   # 5s tasks.loop: fetchchat diff + on_message relay
│   ├── services/
│   │   ├── rcon_service.py  # Async wrapper: auto-reconnect, structured parsing
│   │   ├── database.py      # SQLite WAL + threading.Lock, 3 tables
│   │   ├── chart_service.py # Matplotlib 24h player chart (Discord dark theme)
│   │   ├── player_tracker.py# Parse PlayerConnectedLog.txt tail for online duration
│   │   └── system_stats.py  # psutil: CPU/RAM/disk/network with delta calc
│   └── utils/
│       ├── chat_parser.py   # ChatDiffer: snapshot-based dedup + HumanitZ markup
│       ├── formatters.py    # Progress bars, duration, emoji maps (pure functions)
│       └── i18n.py          # Hardcoded dict i18n: en + zh-TW
├── data/                    # Runtime: SQLite DB + status_state.json (gitignored)
├── tmp/                     # Temp: player_chart.png (overwritten each cycle)
├── logs/                    # Daily-rotated bot.log (gitignored)
├── Dockerfile               # python:3.12-slim + uv, two-layer cache
└── docker-compose.yml       # restart: unless-stopped, 3 volume mounts
```

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add new bot feature | `cogs/` | Create cog file + add to `bot.py` cogs list |
| Add RCON command | `services/rcon_service.py` | Follow `fetch_all()` pattern with `asyncio.to_thread` |
| Add translation string | `utils/i18n.py` | Add key to BOTH `en` and `zh-TW` dicts |
| Add new .env setting | `config.py` + `.env.example` + `.env.example.zh-TW` + README tables | ALL FOUR must be updated |
| Parse new chat event | `utils/chat_parser.py` | Add regex + `ChatEventType` enum member |
| Add embed field | `cogs/server_status.py` `_build_embed()` | Mind `_EMBED_FIELD_LIMIT = 1024` |
| Modify chart style | `services/chart_service.py` | `_DISCORD_DARK`, `_DISCORD_BLURPLE` constants |
| Debug RCON protocol | `rcon_client.py` | Set `LOG_LEVEL=DEBUG` in .env for full packet dumps |

## BOOT SEQUENCE

```
uv run python -m humanitz_bot
  │
  ├─ Settings.from_env()          # Validates .env, exits on missing required
  ├─ setup_logging()              # TimedRotatingFileHandler + stdout
  ├─ set_locale()                 # Global i18n state
  ├─ create_bot(settings)
  │   ├─ bot.settings = settings  # Dynamic attr (type: ignore)
  │   ├─ load_extension(server_status)
  │   │   └─ Inits: RconService, Database, ChartService, PlayerTracker
  │   └─ load_extension(chat_bridge)
  │       └─ Inits: ChatDiffer only (RCON/DB borrowed from ServerStatusCog)
  └─ bot.start(token)
      └─ on_ready → starts both tasks.loop (status 30s, chat 5s)
```

## DATA FLOW

**Status loop** (30s): `rcon.fetch_all()` (info + Players + fetchchat, serial) → `player_tracker.get_online_times()` → `get_system_stats()` → `chart_service.generate_chart()` → `_build_embed()` → edit Discord message

**Chat bridge** (5s): `rcon.execute("fetchchat")` → `ChatDiffer.get_new_events()` (snapshot diff) → `channel.send()` per event

**Discord→Game**: `on_message` → strip @mentions → `rcon.execute("admin [Discord] name: msg")` → verify "Message sent!"

## CONVENTIONS

- `from __future__ import annotations` — every module, no exceptions
- Type hints everywhere: `str | None` union syntax (3.10+)
- `pathlib.Path` for all file paths, never string concatenation
- Module-level logger: `logging.getLogger("humanitz_bot.<dotted.path>")`
- Docstrings in Chinese (繁體中文), log messages in English
- Sync I/O wrapped with `asyncio.to_thread()` — RCON and DB are both sync
- Constants: `_UPPER_SNAKE_CASE` with underscore prefix (module-private)
- No slash commands — bot is purely event-driven (tasks.loop + on_message)
- `@dataclass` for data structures: Settings, ServerInfo, PlayerInfo, etc.

## ANTI-PATTERNS (THIS PROJECT)

- **DO NOT** use `assert` for None guards in production paths — `rcon_service.py:114,135` has this issue. Use explicit `if ... is None: raise` instead
- **DO NOT** add `type: ignore` without documenting why — `bot.py:36`, `chat_bridge.py:30,47` use it for dynamic `bot.settings` attribute
- **DO NOT** create RCON commands that rely on end-marker technique — HumanitZ RCON ignores empty commands, uses 3.5s timeout-based reading instead
- **DO NOT** echo admin messages back to Discord — `chat_bridge.py` skips `ADMIN_MESSAGE` events to prevent infinite loop (Discord → RCON admin → fetchchat → Discord)
- **DO NOT** add bare `except Exception: pass` — existing in `rcon_client.py:261`, swallows errors silently

## KNOWN QUIRKS

| Quirk | Location | Detail |
|-------|----------|--------|
| RCON request_id always 0 | `rcon_client.py` | HumanitZ non-standard: auth uses `resp_id != -1` instead of matching sent ID |
| ~3.5s per RCON command | `rcon_service.py` | `read_timeout=3.5` — `fetch_all()` takes ~10.5s (3 serial commands) |
| Cog coupling | `chat_bridge.py:39-54` | Borrows `rcon` and `db` from ServerStatusCog via `get_cog()` — fallback creates own RCON |
| tasks.loop placeholder interval | Both cogs | Decorator has dummy value, `on_ready` calls `change_interval()` with real config |
| Chat message truncated at 200 chars | `chat_bridge.py:154` | `max_len = 200` — far below Discord's 2000 limit |
| PlayerConnectedLog date comma | `player_tracker.py:76` | Year format `2,026` → strip commas before parsing |
| Global mutable net stats | `system_stats.py:16` | `_last_net` module-level dict — no lock, relies on `asyncio.to_thread` serialization |
| Two persistence mechanisms | `status_state.json` + SQLite | Status message ID in JSON, everything else in DB |
| uv.lock in .gitignore | `.gitignore:25` | Lock file not tracked — builds may not be reproducible |

## COMMANDS

```bash
# Development
uv sync                          # Install dependencies
uv run python -m humanitz_bot    # Run bot locally

# Docker
docker compose up -d             # Run in container
docker compose logs -f           # Tail logs

# Debug
LOG_LEVEL=DEBUG uv run python -m humanitz_bot  # Full RCON packet traces
```

## NOTES

- **No tests exist.** No pytest, no test files, no CI/CD workflows.
- **No linting configured.** No ruff/flake8/mypy/pyright in pyproject.toml.
- **Adding a new cog**: Create `src/humanitz_bot/cogs/my_cog.py` with `async def setup(bot)` → append `"humanitz_bot.cogs.my_cog"` to cogs list in `bot.py:47-50`.
- **Adding a new service**: Create in `services/`, instantiate in the cog that needs it. If shared across cogs, consider refactoring to bot-level injection.
- **RCON protocol reference**: Valve Source RCON (with HumanitZ deviations documented in `rcon_client.py` docstring).
- **Required .env fields**: `DISCORD_TOKEN`, `STATUS_CHANNEL_ID`, `CHAT_CHANNEL_ID`, `RCON_PASSWORD`. Bot exits with descriptive error if missing.
- **Placeholder detection**: Config rejects values matching `YOUR_*`, `PLACEHOLDER`, `CHANGEME`, `TODO`, `*_HERE` patterns.
