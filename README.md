# Tresorit for Omarchy

An unofficial Omarchy plugin for controlling the Tresorit Linux client. It
brings daemon status, transfer activity, selective folder sync, and CLI login
into a panel modelled after Omarchy's built-in Dropbox widget.

This project is not affiliated with or endorsed by Tresorit.

## Features

- Shows whether Tresorit is installed, running, authenticated, or restricted.
- Displays per-tresor transfer progress, sync errors, and active files.
- Keeps a private, bounded history of files whose completion it observed.
- Opens locally available tresors and files from the panel.
- Starts and stops the Tresorit daemon or individual tresor syncs.
- Chooses, changes, remembers, and forgets local sync folders with safety checks.
- Signs in from a terminal with password/TOTP or SSO.
- Supports mouse, keyboard, and Omarchy Shell IPC controls.

## Requirements

- Omarchy (tested with Omarchy 4.0).
- [Tresorit for Linux](https://tresorit.com/download/linux) and its
  `tresorit-cli`. The plugin searches `PATH` and Tresorit's default install path,
  `~/.local/share/tresorit/tresorit-cli`.
- Python 3, Zenity, and `xdg-open` (included with Omarchy by default).

Tresorit documents its Linux CLI, dependencies, and commands in its
[Linux CLI guide](https://support.tresorit.com/hc/en-us/articles/360009330614-Using-Tresorit-CLI-for-Linux).

## Installation

Install and enable the plugin from GitHub:

```bash
omarchy plugin add https://github.com/mspori/omarchy-tresorit.git --enable
```

Omarchy asks for confirmation because shell plugins run as unsandboxed user
code. Review the code before enabling it.

Update or remove it later with:

```bash
omarchy plugin update mspori.tresorit
omarchy plugin remove mspori.tresorit
```

## Usage

| Control | Action |
|---|---|
| Left-click the bar icon | Open or close the panel |
| Right-click the bar icon | Refresh status |
| Header switch | Start or stop the Tresorit daemon |
| Tresor row | Open its local folder, or choose one if none is usable |
| Folder button | Choose or change the local sync folder |
| Tresor switch | Start or stop syncing that tresor |
| Forget button | Forget a stopped tresor's remembered folder |
| File row | Open the local file when it can be resolved safely |

The **Tresors** tab groups folders by local sync state. The **Files** tab lists
active transfers and recently observed completions.

Keyboard controls while the panel is open:

| Key | Action |
|---|---|
| Arrow keys | Move through the header, tabs, and rows |
| Enter | Activate the selected control or row |
| `S` | Start or stop the selected tresor sync |
| `F` | Choose or change the selected tresor's local folder |
| Delete | Forget the selected stopped tresor's linked folder |
| `R` | Refresh status |
| `L` | Open CLI login when signed out |
| Escape | Close the panel |

The daemon switch interrupts every Tresorit sync and Tresorit Drive. It is
separate from the per-tresor switches.

### Sync-folder safety

Tresorit requires an explicit local folder when sync is enabled; the plugin
never guesses one. Before passing a folder to Tresorit, it verifies that the
folder exists, is writable, and does not overlap:

- the filesystem root or the home folder itself;
- Tresorit Drive;
- another synced tresor; or
- a folder remembered for another tresor.

Changing a sync folder stops the current sync and starts it at the new
destination. If the new start fails, the plugin restores the former selection
and attempts to restart it. A timeout is treated as indeterminate and does not
launch a competing rollback. The plugin does not move or delete the old local
folder; existing content in the new folder may be merged and uploaded by
Tresorit, so the destination is always shown for confirmation first.

The forget action only removes the plugin's remembered link. It never deletes
local or cloud files.

### File activity and history

Tresorit's CLI reports files only while it is processing them; it provides no
completed-file history or completion timestamps. The plugin records a file as
completed when it disappears from a later successful poll while its tresor
remains healthy and synced. The displayed time is when completion was observed,
not necessarily when the transfer actually finished. Transfers completed while
the plugin was not polling may be absent.

## Settings

Configure the widget in **Setup › Plugins** or in its entry in
`~/.config/omarchy/shell.json`.

| Setting | Default | Range | Purpose |
|---|---:|---:|---|
| `refreshIntervalSec` | 30 seconds | 10–3600 | General status polling interval |
| `fileHistoryLimit` | 50 files | 10–200 | Maximum observed completions retained |

While the Files tab is open, active file details are polled every two seconds.

## Local data and privacy

The plugin talks only to the locally installed `tresorit-cli`; it makes no
network requests of its own. Login credentials are handled by Tresorit's CLI,
and the plugin does not store them.

Remembered sync folders and observed file activity are stored in:

```text
${XDG_STATE_HOME:-~/.local/state}/omarchy/mspori.tresorit/sync-paths.json
```

The directory is created with mode `0700` and the file with mode `0600`. It
contains local folder paths, tresor and file names, observation timestamps, and
a SHA-256 account identifier—but not the account address in clear text. Data is
scoped by account and tresor identifier.

CLI output can contain account addresses, tresor names, owners, and local paths.
Please redact it before opening a bug report.

## Troubleshooting

- **CLI not installed:** confirm that
  `~/.local/share/tresorit/tresorit-cli status` works. If Tresorit is installed
  elsewhere, add its directory to `PATH` or set `TRESORIT_CLI` before starting
  Omarchy Shell.
- **Plugin missing from the bar:** run `omarchy plugin list`, enable it with
  `omarchy plugin enable mspori.tresorit --section right`, then run
  `omarchy restart shell` if needed.
- **A folder is rejected:** use a dedicated, writable directory that does not
  contain, or sit inside, another Tresorit sync location.
- **Status is stale after a command:** use right-click or `R` to refresh. Some
  Tresorit operations continue after the CLI call returns or times out.

## License

[MIT](LICENSE)

Tresorit is a trademark of Tresorit. All other trademarks belong to their
respective owners.

## Development disclosure

This plugin was built largely with OpenAI Codex under human direction, review,
and testing.
