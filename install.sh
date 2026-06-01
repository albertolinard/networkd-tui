#!/usr/bin/env bash
#
# Installer for networkd-tui — a Textual UI for systemd-networkd.
#
# Creates a self-contained virtualenv under ~/.local/share/networkd-tui,
# installs the app into it, and drops a launcher at ~/.local/bin/networkd-tui
# so you can run the tool by simply typing:  networkd-tui
#
# Re-running it upgrades an existing install. Pass --uninstall to remove.

set -euo pipefail

APP_NAME="networkd-tui"
ENTRY="networkd_tui.py"
DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/$APP_NAME"
BIN_DIR="$HOME/.local/bin"
LAUNCHER="$BIN_DIR/$APP_NAME"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

c_ok()   { printf '\033[32m✓\033[0m %s\n' "$1"; }
c_info() { printf '\033[36m·\033[0m %s\n' "$1"; }
c_warn() { printf '\033[33m!\033[0m %s\n' "$1"; }

uninstall() {
    rm -f "$LAUNCHER"
    rm -f "${XDG_DATA_HOME:-$HOME/.local/share}/applications/$APP_NAME.desktop"
    rm -rf "$DATA_DIR"
    c_ok "Removed $LAUNCHER, desktop entry, and $DATA_DIR"
    exit 0
}

[[ "${1:-}" == "--uninstall" ]] && uninstall

command -v python3 >/dev/null || { c_warn "python3 not found"; exit 1; }

c_info "Installing into $DATA_DIR"
mkdir -p "$DATA_DIR" "$BIN_DIR"

if [[ ! -d "$DATA_DIR/venv" ]]; then
    c_info "Creating virtualenv"
    python3 -m venv "$DATA_DIR/venv"
fi

c_info "Installing dependencies (Textual)"
"$DATA_DIR/venv/bin/python" -m pip install -q --upgrade pip >/dev/null
"$DATA_DIR/venv/bin/python" -m pip install -q -r "$SRC_DIR/requirements.txt"

c_info "Copying application files"
install -m 644 "$SRC_DIR/$ENTRY" "$DATA_DIR/$ENTRY"

c_info "Writing launcher $LAUNCHER"
cat > "$LAUNCHER" <<EOF
#!/usr/bin/env bash
exec "$DATA_DIR/venv/bin/python" "$DATA_DIR/$ENTRY" "\$@"
EOF
chmod +x "$LAUNCHER"

c_info "Installing desktop entry"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APP_DIR"
if command -v omarchy-launch-tui >/dev/null; then
    # On Omarchy, open in the styled default terminal with a clean app-id.
    sed -e 's|^Exec=.*|Exec=omarchy-launch-tui networkd-tui|' \
        -e 's|^Terminal=.*|Terminal=false|' \
        "$SRC_DIR/$APP_NAME.desktop" > "$APP_DIR/$APP_NAME.desktop"
else
    install -m 644 "$SRC_DIR/$APP_NAME.desktop" "$APP_DIR/$APP_NAME.desktop"
fi
command -v update-desktop-database >/dev/null && \
    update-desktop-database "$APP_DIR" >/dev/null 2>&1 || true

c_ok "Installed. Run it with:  $APP_NAME  (or the app launcher)"

case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) c_warn "$BIN_DIR is not on your PATH."
       c_warn "Add this to your shell rc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
