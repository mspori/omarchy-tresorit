# Tresorit for Omarchy

An Omarchy Shell bar plugin for monitoring and controlling the Tresorit Linux
client. It is designed as a Tresorit counterpart to Omarchy's built-in Dropbox
widget.

## Features

- Show whether Tresorit is installed, running, and authenticated.
- Display current transfer activity and sync errors.
- Show files currently being processed and their Tresorit transfer status.
- Keep a private, bounded history of files whose completion the plugin observed.
- List locally available tresors and open their folders.
- Group tresors into synced and not-synced sections.
- Start or stop the Tresorit daemon.
- Choose a local folder and start or stop synchronization for individual tresors.
- Sign in through the Tresorit CLI with password/TOTP or SSO.

When a synced tresor is switched off, the plugin remembers its former local
folder so it can safely be switched on again. For a cloud-only tresor, use the
folder button to choose its local destination; the plugin never guesses one.

## Installation

Install the plugin from its Git repository and enable it in the bar:

```bash
omarchy plugin add <git-url> --enable
```

Omarchy asks for confirmation because shell plugins run as unsandboxed user
code. Review the repository before enabling it.

For local development, a Git checkout can be installed with a file URL:

```bash
omarchy plugin add file:///absolute/path/to/michaelspori.tresorit --enable
```

## Usage

- Left-click the bar icon to open the panel.
- Right-click it to refresh status.
- Click a synced or linked tresor to open its local folder. Click an unlinked
  cloud-only tresor—or one whose previous folder is unavailable—to choose its
  local sync folder.
- Switch between the **Tresors** and **Files** tabs with the mouse or Left/Right.
  The Files tab shows active transfers and the most recently observed completed
  files; click a resolvable file to open it in its default application.
- Use the trailing switch to turn synchronization for that tresor on or off.
- Use the folder button on a cloud-only tresor to select its local sync folder.
- Use the folder-with-pencil button on a linked or synced tresor to change its
  destination. Active syncs are stopped and restarted, with rollback to the
  previous folder if the new start fails and its safe state can be restored.
- Use the header switch to start or stop the Tresorit daemon. This interrupts
  all Tresorit synchronization and Tresorit Drive; it is separate from the
  per-tresor switches.

The panel supports arrow-key navigation, Enter to open the selected tresor or file,
`S` to change its sync selection, `F` to choose or change its local folder,
`R` to refresh, and `L` to open the CLI login when signed out.

Tresorit's CLI only reports files that are currently being processed; it does
not provide completed-file history or completion timestamps. The plugin records
a file as previously synced when it disappears from a later successful transfer
poll while its tresor remains healthy and synced. The displayed time is therefore
the time completion was observed locally, and transfers completed while the
plugin is not polling may be absent. File paths are opened only when they resolve
to an existing file inside the tresor's current local sync folder.
The **Synced file history** plugin setting controls how many completed entries
are retained, from 10 to 200 (50 by default). Active transfers are never limited.

### Sync-folder safety

Tresorit requires an explicit local folder when synchronization is enabled.
The plugin stores folders it has already observed in:

```text
${XDG_STATE_HOME:-~/.local/state}/omarchy/michaelspori.tresorit/sync-paths.json
```

The state directory and file are user-private. Account addresses are stored as
one-way hashes, and paths are scoped by that account and a stable tresor
identifier. A remembered folder must still exist and be writable before the
plugin will pass it back to Tresorit. New
selections are also rejected if they overlap another synced tresor, Tresorit
Drive, the filesystem root, or the home folder itself. The selected folder is
shown for confirmation before synchronization starts.

## Requirements

- Omarchy with the Quickshell-based Omarchy Shell plugin system.
- Tresorit for Linux with `tresorit-cli` installed.
- Zenity for the directory chooser and confirmation dialog.
- Python 3.

## Development

Run the parser/model tests and Omarchy validator:

```bash
python3 -m unittest discover -s tests -v
node tests/test_model.js
omarchy plugin validate .
```

The helper calls the CLI with argument arrays rather than shell commands. Tests
and bug reports should never include raw CLI output: it can contain account
addresses, tresor names, owners, and local paths.

## License

[MIT](LICENSE)
