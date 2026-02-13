# HumanitZ Discord Bot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.12+](https://img.shields.io/badge/python-3.12+-blue.svg)](https://www.python.org/downloads/)
[![discord.py](https://img.shields.io/badge/discord.py-2.6+-blue.svg)](https://github.com/Rapptz/discord.py)

> **[繁體中文版 README](README.zh-TW.md)**

A Discord bot for [HumanitZ](https://store.steampowered.com/app/1935610/HumanitZ/) dedicated game servers. Provides real-time server status monitoring, bidirectional chat bridge, and player tracking — all powered by RCON.

## Features

- **📊 Live Server Status Embed** — Auto-updating Discord message showing server name, player count, season/weather, AI stats (zombies, bandits, animals), game time, FPS, and system resources (CPU/RAM/disk/network)
- **💬 Bidirectional Chat Bridge** — Relay messages between in-game chat and a Discord channel in real time, with echo prevention and @mention sanitization
- **📈 Player Count Chart** — 24-hour (configurable) history chart with Discord-themed dark styling
- **👥 Player Online Duration** — Shows how long each player has been connected, parsed from server logs
- **🗄️ SQLite Database** — Persistent storage for player count history, chat logs, and player session events with automatic data pruning
- **🌐 Internationalization** — English and Traditional Chinese (繁體中文) UI support
- **📝 Daily Rotated Logs** — Configurable log retention with daily rotation

## Architecture

```
src/humanitz_bot/
├── __main__.py          # Entry point, logging setup, signal handling
├── bot.py               # Discord bot initialization, cog loading
├── config.py            # Settings from .env with validation
├── rcon_client.py       # Source RCON protocol (optimized for HumanitZ)
├── cogs/
│   ├── server_status.py # Status embed auto-update loop (30s default)
│   └── chat_bridge.py   # Chat bridge polling loop (5s default)
├── services/
│   ├── database.py      # SQLite with WAL mode + thread safety
│   ├── rcon_service.py  # Async RCON wrapper with auto-reconnect
│   ├── chart_service.py # Matplotlib chart generation
│   ├── player_tracker.py# Online duration from PlayerConnectedLog.txt
│   └── system_stats.py  # CPU, memory, disk, network via psutil
└── utils/
    ├── chat_parser.py   # fetchchat markup parser + dedup differ
    ├── formatters.py    # Progress bars, duration, emoji maps
    └── i18n.py          # en + zh-TW translations
```

## Prerequisites

- **Python 3.12+**
- **[uv](https://docs.astral.sh/uv/)** — Fast Python package manager
- A **HumanitZ dedicated server** with RCON enabled
- A **Discord Bot Token** ([create one here](https://discord.com/developers/applications))

> **Note:** Windows is supported but has not been tested in production. If you encounter any issues, please [open an issue](https://github.com/Minidoracat/humanitz-bot/issues).

## Quick Start

### 1. Clone the repository

```bash
git clone https://github.com/Minidoracat/humanitz-bot.git
cd humanitz-bot
```

### 2. Install dependencies

```bash
uv sync
```

### 3. Configure

```bash
cp .env.example .env
```

Edit `.env` and fill in your values:

| Variable | Required | Description |
|----------|----------|-------------|
| `DISCORD_TOKEN` | ✅ | Bot token from Discord Developer Portal |
| `STATUS_CHANNEL_ID` | ✅ | Channel for the status embed |
| `CHAT_CHANNEL_ID` | ✅ | Channel for the chat bridge |
| `RCON_PASSWORD` | ✅ | RCON password (from `GameServerSettings.ini`) |
| `RCON_HOST` | | RCON address (default: `127.0.0.1`) |
| `RCON_PORT` | | RCON port (default: `8888`) |
| `STATUS_MESSAGE_ID` | | Pin the status embed to a specific message (leave blank to auto-create) |
| `LOCALE` | | `en` or `zh-TW` (default: `en`) |
| `PLAYER_LOG_PATH` | | Path to `PlayerConnectedLog.txt` |

See [`.env.example`](.env.example) for all options with detailed descriptions.

### 4. Run

```bash
uv run python -m humanitz_bot
```

### Run with Docker

```bash
cp .env.example .env   # edit .env first
docker compose up -d
```

> **Note:** System stats (CPU, memory, disk) will reflect the container's resources, not the host machine.

### HumanitZ Server Configuration

Make sure RCON is enabled in your `GameServerSettings.ini`:

```ini
RCONEnabled=true
RConPort=8888
RCONPass=your_password_here
```

### Discord Bot Permissions

The bot requires these permissions (intents):
- **Message Content** — Read messages for chat bridge
- **Send Messages** — Send chat and status messages
- **Embed Links** — Display status embed
- **Attach Files** — Upload player count chart

Enable **Message Content Intent** in Discord Developer Portal → Bot → Privileged Gateway Intents.

## RCON Protocol Notes

HumanitZ uses a modified Source RCON protocol with some quirks:

- Response `request_id` is always `0` (non-standard)
- Does **not** respond to empty commands (end-marker technique unusable)
- Auth flow: server sends `RESPONSE_VALUE` (type=0) then `AUTH_RESPONSE` (type=2)
- ~3 second response delay per command

The bot handles all of these automatically.

## Data Storage

| Path | Content | Tracked by Git |
|------|---------|----------------|
| `data/humanitz_bot.db` | SQLite database (player counts, chat logs, sessions) | ❌ |
| `data/status_state.json` | Persisted status message ID for restart resilience | ❌ |
| `tmp/player_chart.png` | Latest player count chart (overwritten each cycle) | ❌ |
| `logs/bot.log` | Application logs (daily rotation) | ❌ |

All runtime data is excluded from git via `.gitignore`.

## License

[MIT](LICENSE) © [Minidoracat](https://github.com/Minidoracat)