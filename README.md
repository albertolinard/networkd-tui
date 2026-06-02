# networkd-tui

A terminal UI for **systemd-networkd** (and **systemd-resolved**), built with
[Textual](https://textual.textualize.io/). It's a thin, pretty front-end over
`networkctl` / `resolvectl` that operates on the declarative configs in
`/etc/systemd/network/`.

Read-only views need no privileges. Edits, reload, reconfigure and restart use
`sudo` (passwordless sudo recommended, otherwise run from a terminal where you
can authenticate).

![networkd-tui screenshot](docs/screenshot.svg)

## Features

- Live list of managed links with color-coded operational/setup state.
- Per-link status detail (updates as you move the cursor).
- **Guided editor** (press Enter on a link): flip **DHCP ↔ Static**, set the
  **IP address/gateway**, and set **DNS** servers from a form — no INI editing.
  Reads the link's current config, writes it back preserving other directives,
  then reloads and reconfigures.
- Browse and **edit** raw `/etc/systemd/network/*.network|.netdev|.link` via
  `networkctl edit` (validates and offers to reload) for advanced changes.
- Scaffold a **new `.network`** file (DHCP or static) from a small form.
- **Reconfigure** a link, **reload** networkd, or **restart** the service.
- **DNS** view (`resolvectl status`).

## Keys

| Key | Action |
|-----|--------|
| `q` / `Esc` | Quit |
| `g` | Refresh |
| `Enter` | **Edit** selected link — DHCP/Static, IP, gateway, DNS |
| `i` | Inspect (full status of selected link) |
| `R` | Reconfigure selected link |
| `r` | Reload networkd |
| `Ctrl+R` | Restart `systemd-networkd` |
| `c` | Config files browser (raw edit / new) |
| `d` | DNS status (resolved) |

Inside the edit form: `Ctrl+S` save · `Esc` cancel.

## Install

```bash
./install.sh
```

Creates a venv at `~/.local/share/networkd-tui`, installs Textual, drops a
launcher at `~/.local/bin/networkd-tui`, and installs a desktop entry (on
Omarchy it launches via `omarchy-launch-tui`; elsewhere it opens in a
terminal). Then just run:

```bash
networkd-tui
```

…or launch "Networkd TUI" from your app launcher.

Uninstall with `./install.sh --uninstall`.

## License

[MIT](LICENSE) © Alberto Linard
