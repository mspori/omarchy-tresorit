# Tresorit for Omarchy

An Omarchy Shell bar plugin for monitoring and controlling the Tresorit Linux
client. It is designed as a Tresorit counterpart to Omarchy's built-in Dropbox
widget.

## Planned first release

- Show whether Tresorit is installed, running, and authenticated.
- Display current transfer activity and sync errors.
- List locally available tresors and open their folders.
- Start or stop the Tresorit daemon.
- Start or stop synchronization for individual tresors.
- Hand account management off to the Tresorit desktop application.

When a synced tresor is switched off, the plugin remembers its former local
folder so it can safely be switched on again. Cloud-only tresors without a
known local folder must first be configured in Tresorit; the plugin never
guesses a sync destination.

## Requirements

- Omarchy with the Quickshell-based Omarchy Shell plugin system.
- Tresorit for Linux with `tresorit-cli` installed.
- Python 3.

## Development

Validate the plugin without installing it:

```bash
omarchy plugin validate .
```

The plugin is under active development and is not ready to install yet.

## License

[MIT](LICENSE)
