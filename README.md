# Werewolf player bot

A Telegram userbot that automatically joins and plays [Werewolf](https://t.me/werewolfbot) games using multiple accounts. Built with [Kurigram](https://github.com/KurimuzonAkuma/kurigram) (Pyrogram fork).

## Features

- **Multi-account support** — Control multiple Telegram accounts as game players simultaneously
- **Auto-join** — Automatically detects new games in monitored groups and joins with configured worker accounts
- **Auto-play** — Responds to in-game inline keyboard prompts with randomized choices
- **Target system** — Set a specific player as a priority target, or force targeting human players over bots
- **ID card tracking** — Tracks players who have revealed identity cards and avoids targeting them
- **Bot avoidance** — Identifies other bot accounts in the game and prefers targeting real players
- **Per-group configuration** — Independent settings (enabled/disabled, worker count) for each monitored group
- **Redis-backed state** — Persists game join keys across restarts using Redis

## Prerequisites

- Python 3.10+
- Redis server running on localhost
- Telegram API credentials (`api_id` and `api_hash` from [my.telegram.org](https://my.telegram.org))
- One or more Telegram accounts to use as players

## Installation

```bash
pip install -r requirements.txt
```

## Configuration

Copy the default config and fill in your credentials:

```bash
cp config.ini.default config.ini
```

Edit `config.ini`:

```ini
[account]
api_id = YOUR_API_ID
api_hash = YOUR_API_HASH
count = 3                  # Number of player accounts
owner = YOUR_TELEGRAM_ID   # Your user ID for owner commands
listen_to = [-100xxx]      # List of group chat IDs to monitor
```

On first run, each account (`werewolf0`, `werewolf1`, ...) will prompt for phone number and login code.

## Usage

```bash
python player.py
```

Optional flags:

| Flag | Description |
|------|-------------|
| `--debug` | Enable debug logging |
| `--detail` | Enable detailed markup logging |

## Commands

These commands are sent as messages and handled by the first account (owner only unless noted):

| Command | Where | Description |
|---------|-------|-------------|
| `/target <name>` | Owner DM | Set a target player by name (partial match) |
| `/target h` | Owner DM | Toggle force-target-human mode |
| `/target` | Owner DM | Clear the current target |
| `/resend <account>` | Monitored group | Re-send join command for a specific account |
| `/debug` | Owner DM | Toggle debug logging level |
| `/off` | Monitored group | Toggle auto-join on/off for this group |
| `/setw <n>` | Monitored group | Set number of worker accounts for this group |

## License

[![](https://www.gnu.org/graphics/agplv3-155x51.png)](https://www.gnu.org/licenses/agpl-3.0.txt)

Copyright (C) 2020-2026 KunoiSayami

This program is free software: you can redistribute it and/or modify it under the terms of the GNU Affero General Public License as published by the Free Software Foundation, either version 3 of the License, or any later version.

This program is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General Public License for more details.

You should have received a copy of the GNU Affero General Public License along with this program. If not, see <https://www.gnu.org/licenses/>.
