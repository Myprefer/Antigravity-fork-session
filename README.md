# Antigravity-fork-session

**English** | [简体中文](./README_zh.md)

A Sidecar UI plugin for Google Antigravity Desktop to fork conversations (including per-turn truncation), sync sessions between Desktop and CLI (`agy`), and export/import sessions across devices.

## Why

Antigravity CLI (`agy`) supports `/fork`, `/rewind`, and importing conversations from Desktop, while Antigravity Desktop currently lacks native support for:

1. **Conversation forking & per-turn branching**: When you want to branch off an earlier turn in a long session to try a different approach, there is no built-in way to fork from a specific turn without starting over or going through the CLI.
2. **Opening CLI sessions in Desktop**: Desktop (`~/.gemini/antigravity`) and CLI (`~/.gemini/antigravity-cli`) store sessions in separate local directories, so sessions continued or forked in CLI do not show up in the Desktop sidebar.

Because both clients share the same underlying SQLite + protobuf storage format, this zero-dependency Sidecar plugin brings per-turn forking, Desktop ↔ CLI sync, and cross-device transfer directly into the Desktop side pane.

## Features

- **Per-turn Fork**: View every turn (prompts + rendered Markdown replies) of the active conversation in the side pane and fork right after or before any turn.
- **Full Fork & Project Switching**: Clone any Desktop or CLI session with a fresh UUID, custom title, and target project, then notify Desktop's `ConversationService` so it appears immediately in the sidebar.
- **Desktop ↔ CLI Sync**: Import CLI sessions from `~/.gemini/antigravity-cli` into Desktop, or sync Desktop sessions to CLI.
- **Cross-device Export & Import**: Export any session to a portable `.agysess.zip` bundle via a native OS Save-As dialog (with optional `brain/` attachments), and import `.agysess.zip` or raw `.db` files on another macOS / Linux / Windows machine with automatic ID and project reassignment.
- **Auto-backup & Restore**: Automatically snapshots existing sessions before any overwrite, with one-click restore in the **Backups** tab.
- **Zero Dependencies**: Pure Python 3.9+ standard library backend + vanilla HTML/CSS/JS frontend, synced with Antigravity's light/dark theme.

## Requirements

- macOS / Linux / Windows
- Python 3.9+
- Google Antigravity Desktop (optionally with `agy` CLI for cross-surface sync)

## Installation

### macOS / Linux

```bash
git clone https://github.com/Myprefer/Antigravity-fork-session.git
cd Antigravity-fork-session
./install.sh
```

For development, use `./install.sh --symlink`. To uninstall: `./uninstall.sh`.

### Windows (PowerShell)

```powershell
git clone https://github.com/Myprefer/Antigravity-fork-session.git
cd Antigravity-fork-session
.\install.ps1
```

`install.ps1` automatically detects Python 3.9+ on your machine and writes its path into the installed `sidecar.json`. To uninstall: `.\uninstall.ps1`.

### Enable in Antigravity Desktop

1. Open **Customizations** in Antigravity Desktop and enable `Antigravity-fork-session` (`antigravity-fork-session`) along with its Sidecar.
2. Click **`+`** in the top-right corner of the right-hand side pane and select **会话 Fork 管理**.

## Standalone Mode (Without Installing the Plugin)

```bash
python3 sidecars/session-manager/server.py --port 18765
```

Then open `http://127.0.0.1:18765` in your browser.

## Environment Variables

| Variable | Description | Default |
| --- | --- | --- |
| `AGY_DESKTOP_DIR` | Antigravity Desktop data directory | `~/.gemini/antigravity` |
| `AGY_CLI_DIR` | Antigravity CLI data directory | `~/.gemini/antigravity-cli` |
| `AGY_MANAGER_PORT` | Port when running standalone | `18766` |

## Notes

- The local server binds strictly to `127.0.0.1`.
- Fork, sync, and import operations use SQLite's online `.backup()` in a staging directory and replace files atomically without modifying source databases.
- This project reads and writes Antigravity's local SQLite and protobuf storage format; future upstream storage changes may require updates.

## License

[MIT](./LICENSE)
