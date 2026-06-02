#!/usr/bin/env python3
"""
networkd-tui — a terminal UI for systemd-networkd (+ resolved).

Browse links, inspect their live status, view and edit the declarative configs
in /etc/systemd/network/, reload/reconfigure, and check DNS — all built on
networkctl/resolvectl. Read-only views need no privileges; edits and
reload/reconfigure/restart use sudo.

Keys: q quit · g refresh · enter edit (DHCP/IP/DNS) · i inspect
      R reconfigure · r reload · ctrl+r restart · c configs · d DNS
"""

import os
import subprocess
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import (DataTable, Footer, Header, Input, Label, ListItem,
                             ListView, Select, Static)

NETDIR = Path("/etc/systemd/network")
EDITOR = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nvim"

OPER_STYLE = {
    "routable": "bold green", "configured": "bold green", "carrier": "yellow",
    "degraded": "yellow", "dormant": "yellow", "no-carrier": "red",
    "off": "red", "missing": "red", "unmanaged": "dim", "pending": "yellow",
}


# ─────────────────────────────── shell helpers ───────────────────────────────

def run(cmd):
    """Run a command; return (returncode, combined stdout+stderr)."""
    try:
        p = subprocess.run(cmd, capture_output=True, text=True)
        return p.returncode, (p.stdout + p.stderr)
    except FileNotFoundError as exc:
        return 127, str(exc)


def list_links():
    """Return managed links (excluding loopback) as a list of dicts."""
    rc, out = run(["networkctl", "list", "--no-legend", "--no-pager"])
    rows = []
    if rc != 0:
        return rows
    for line in out.splitlines():
        tok = line.split()
        if tok and not tok[0].lstrip("●*").isdigit() and len(tok) >= 6:
            tok = tok[1:]                       # drop a status bullet if present
        if len(tok) >= 5 and tok[1] != "lo":
            rows.append(dict(idx=tok[0], name=tok[1], type=tok[2],
                             oper=tok[3], setup=tok[4]))
    return rows


def link_status(name):
    rc, out = run(["networkctl", "status", "--no-pager", name])
    return out.strip() or f"(no status for {name})"


def config_files():
    if not NETDIR.is_dir():
        return []
    return sorted(p.name for p in NETDIR.iterdir()
                  if p.suffix in (".network", ".netdev", ".link"))


def link_network_file(name):
    """Path to the .network file applied to a link, or None if unmanaged."""
    rc, out = run(["networkctl", "status", "--no-pager", name])
    for line in out.splitlines():
        s = line.strip()
        if s.startswith("Network File:"):
            val = s.split(":", 1)[1].strip()
            return None if val in ("n/a", "", "(unknown)") else val
    return None


def current_settings(text):
    """Parse DHCP / Address / Gateway / DNS out of a .network's [Network]."""
    s = {"dhcp": False, "address": "", "gateway": "", "dns": []}
    in_net = False
    for line in text.splitlines():
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            in_net = t.lower() == "[network]"
            continue
        if not in_net or not t or t.startswith("#") or "=" not in t:
            continue
        k, v = (x.strip() for x in t.split("=", 1))
        kl = k.lower()
        if kl == "dhcp":
            s["dhcp"] = v.lower() in ("yes", "true", "ipv4", "ipv6")
        elif kl == "address" and not s["address"]:
            s["address"] = v
        elif kl == "gateway":
            s["gateway"] = v
        elif kl == "dns":
            s["dns"].extend(v.split())
    return s


def rebuild_network(text, dhcp, address, gateway, dns):
    """Return `text` with DHCP/Address/Gateway/DNS in [Network] replaced by the
    given values, preserving every other section and directive."""
    managed = ("dhcp", "address", "gateway", "dns")
    new_keys = []
    if dhcp:
        new_keys.append("DHCP=yes")
    else:
        if address:
            new_keys.append(f"Address={address}")
        if gateway:
            new_keys.append(f"Gateway={gateway}")
    if dns:
        new_keys.append("DNS=" + " ".join(dns))

    out, name, buf, have_net = [], None, [], False

    def flush():
        nonlocal have_net
        if name is None:
            out.extend(buf)
            return
        out.append(f"[{name}]")
        if name.lower() == "network":
            have_net = True
            for ln in buf:
                t = ln.strip()
                if "=" in t and not t.startswith("#") \
                        and t.split("=", 1)[0].strip().lower() in managed:
                    continue
                out.append(ln)
            out.extend(new_keys)
        else:
            out.extend(buf)

    for line in text.splitlines():
        t = line.strip()
        if t.startswith("[") and t.endswith("]"):
            flush()
            name, buf = t[1:-1], []
        else:
            buf.append(line)
    flush()

    if not have_net:
        out += ["", "[Network]", *new_keys]
    return "\n".join(out).rstrip("\n") + "\n"


# ──────────────────────────────── modals ────────────────────────────────────

class TextModal(ModalScreen):
    """A scrollable, read-only text panel (status dumps, DNS, etc.)."""

    BINDINGS = [("escape", "dismiss", "Close"), ("q", "dismiss", "Close")]

    def __init__(self, title, body, border="cyan"):
        super().__init__()
        self._title, self._body, self._border = title, body, border

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="modal-box"):
            yield Static(Panel(Text(self._body), title=self._title,
                               border_style=self._border, padding=(1, 2)))

    def action_dismiss(self) -> None:
        self.app.pop_screen()


class NewFileModal(ModalScreen):
    """Form to scaffold a new .network file."""

    BINDINGS = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        with Vertical(id="new-form"):
            yield Static("New .network file", classes="modal-title")
            yield Label("Filename:", classes="lbl")
            yield Input(placeholder="30-myiface", id="fname")
            yield Label("Match interface (Name=):", classes="lbl")
            yield Input(placeholder="eth0  ·  en*  ·  wlan0", id="match")
            yield Label("Addressing:", classes="lbl")
            yield Select([("DHCP", "dhcp"), ("Static", "static")],
                         value="dhcp", allow_blank=False, id="mode")
            yield Label("Static Address/CIDR:", classes="lbl")
            yield Input(placeholder="192.168.1.50/24", id="addr")
            yield Label("Gateway:", classes="lbl")
            yield Input(placeholder="192.168.1.1", id="gw")
            yield Static("Enter = write · Esc = cancel", classes="hint")

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def on_input_submitted(self, _event) -> None:
        self._write()

    def _write(self) -> None:
        name = self.query_one("#fname", Input).value.strip()
        match = self.query_one("#match", Input).value.strip()
        if not name or not match:
            self.app.notify("Filename and interface are required",
                            severity="warning")
            return
        if not name.endswith(".network"):
            name += ".network"
        body = ["[Match]", f"Name={match}", "", "[Network]"]
        if self.query_one("#mode", Select).value == "dhcp":
            body.append("DHCP=yes")
        else:
            addr = self.query_one("#addr", Input).value.strip()
            gw = self.query_one("#gw", Input).value.strip()
            if addr:
                body.append(f"Address={addr}")
            if gw:
                body.append(f"Gateway={gw}")
        content = "\n".join(body) + "\n"
        target = NETDIR / name
        # Write atomically via a temp file + sudo install.
        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(content)
            tmppath = tmp.name
        rc, out = run(["sudo", "install", "-m", "0644", tmppath, str(target)])
        os.unlink(tmppath)
        if rc == 0:
            self.app.notify(f"Created {name}")
            run(["sudo", "networkctl", "reload"])
            self.app.pop_screen()
            self.app.refresh_links()
        else:
            self.app.notify(f"Write failed: {out.strip()}", severity="error")


class SettingsModal(ModalScreen):
    """Guided per-link editor: DHCP/static toggle, address, gateway, DNS."""

    BINDINGS = [
        ("escape", "cancel", "Cancel"),
        ("ctrl+s", "save", "Save"),
    ]

    def __init__(self, link):
        super().__init__()
        self.link = link
        self.path = link_network_file(link)
        self.text = ""
        if self.path:
            try:
                self.text = Path(self.path).read_text()
            except OSError:
                self.text = ""
        self.cur = current_settings(self.text)

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-form"):
            yield Static(f"Network settings · {self.link}",
                         classes="modal-title")
            src = self.path or "(no file yet — one will be created)"
            yield Static(src, classes="hint")
            yield Label("Addressing:", classes="lbl")
            yield Select([("DHCP (automatic)", "dhcp"),
                          ("Static (manual)", "static")],
                         value=("dhcp" if self.cur["dhcp"] else "static"),
                         allow_blank=False, id="mode")
            yield Label("IP address / CIDR (static only):", classes="lbl")
            yield Input(value=self.cur["address"], id="addr",
                        placeholder="192.168.1.50/24")
            yield Label("Gateway (static only):", classes="lbl")
            yield Input(value=self.cur["gateway"], id="gw",
                        placeholder="192.168.1.1")
            yield Label("DNS servers (space/comma separated):", classes="lbl")
            yield Input(value=" ".join(self.cur["dns"]), id="dns",
                        placeholder="1.1.1.1 9.9.9.9")
            yield Static("Ctrl+S save · Esc cancel", classes="hint")

    def on_mount(self) -> None:
        self._sync_enabled(self.query_one("#mode", Select).value)

    def on_select_changed(self, event) -> None:
        self._sync_enabled(event.value)

    def _sync_enabled(self, mode) -> None:
        static = mode == "static"
        for wid in ("addr", "gw"):
            self.query_one(f"#{wid}", Input).disabled = not static

    def action_cancel(self) -> None:
        self.app.pop_screen()

    def on_input_submitted(self, _event) -> None:
        self.action_save()

    def action_save(self) -> None:
        mode = self.query_one("#mode", Select).value
        dhcp = mode == "dhcp"
        addr = self.query_one("#addr", Input).value.strip()
        gw = self.query_one("#gw", Input).value.strip()
        dns = [d for d in self.query_one("#dns", Input).value
               .replace(",", " ").split() if d]
        if not dhcp and not addr:
            self.app.notify("Static mode needs an IP address/CIDR",
                            severity="warning")
            return

        base = self.text
        path = self.path
        if not path:
            path = str(NETDIR / f"30-{self.link}.network")
            base = f"[Match]\nName={self.link}\n"
        content = rebuild_network(base, dhcp, addr, gw, dns)

        import tempfile
        with tempfile.NamedTemporaryFile("w", delete=False) as tmp:
            tmp.write(content)
            tmppath = tmp.name
        rc, out = run(["sudo", "install", "-m", "0644", tmppath, path])
        os.unlink(tmppath)
        if rc != 0:
            self.app.notify(f"Write failed: {out.strip()}", severity="error")
            return
        run(["sudo", "networkctl", "reload"])
        run(["sudo", "networkctl", "reconfigure", self.link])
        self.app.notify(f"Applied to {self.link} "
                        f"({'DHCP' if dhcp else 'static'})")
        self.app.pop_screen()
        self.app.refresh_links()


class ConfigsScreen(ModalScreen):
    """Browse files in /etc/systemd/network with a live preview; edit/new."""

    BINDINGS = [
        ("escape", "back", "Back"),
        ("e", "edit", "Edit"),
        ("enter", "edit", "Edit"),
        ("n", "new", "New file"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Config files · /etc/systemd/network",
                     classes="modal-title")
        with Horizontal(id="cfg-body"):
            yield ListView(id="cfg-list")
            yield VerticalScroll(Static(id="cfg-preview"), id="cfg-preview-box")
        yield Static("e/enter edit · n new · esc back", classes="hint")

    def on_mount(self) -> None:
        self._reload_list()

    def _reload_list(self) -> None:
        lv = self.query_one("#cfg-list", ListView)
        lv.clear()
        files = config_files()
        for f in files:
            item = ListItem(Label(f))
            item.cfg_name = f
            lv.append(item)
        if files:
            lv.index = 0
            self._preview(files[0])
        else:
            self.query_one("#cfg-preview", Static).update(
                Text("No config files.", style="dim"))

    def _preview(self, fname) -> None:
        try:
            text = (NETDIR / fname).read_text()
        except OSError as exc:
            text = f"(cannot read: {exc})"
        self.query_one("#cfg-preview", Static).update(
            Panel(Text(text), title=fname, border_style="cyan", padding=(1, 2)))

    def on_list_view_highlighted(self, event) -> None:
        if event.item is not None:
            self._preview(event.item.cfg_name)

    def _current(self):
        lv = self.query_one("#cfg-list", ListView)
        item = lv.highlighted_child
        return getattr(item, "cfg_name", None)

    def action_edit(self) -> None:
        fname = self._current()
        if fname:
            self.app.edit_target(fname)
            self._preview(fname)

    def action_new(self) -> None:
        self.app.push_screen(NewFileModal())

    def action_back(self) -> None:
        self.app.pop_screen()


# ──────────────────────────────── main app ──────────────────────────────────

class NetworkdTUI(App):
    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #links { width: 46%; border-right: solid $panel; }
    #detail-box { width: 1fr; padding: 0 1; }
    #detail { height: auto; }
    DataTable { height: 1fr; }
    .lbl { color: $text-muted; padding: 1 1 0 1; }
    .hint { color: $text-muted; text-style: italic; padding: 0 1; }
    .modal-title { text-style: bold; color: $accent; padding: 1 1 0 2; }
    #new-form, #settings-form { width: 64; height: auto; border: round $accent;
                background: $surface; padding: 1 2; margin: 2 4; }
    Input:disabled { opacity: 0.5; }
    #cfg-body { height: 1fr; }
    #cfg-list { width: 34%; border-right: solid $panel; }
    #cfg-preview-box { width: 1fr; padding: 0 1; }
    TextModal #modal-box { padding: 1 2; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("escape", "quit", "Quit"),
        ("g", "refresh", "Refresh"),
        ("enter", "settings", "Edit (DHCP/IP/DNS)"),
        ("i", "status", "Inspect"),
        ("R", "reconfigure", "Reconfigure"),
        ("r", "reload", "Reload"),
        ("ctrl+r", "restart", "Restart svc"),
        ("c", "configs", "Configs"),
        ("d", "dns", "DNS"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            yield DataTable(id="links", zebra_stripes=True, cursor_type="row")
            with VerticalScroll(id="detail-box"):
                yield Static(id="detail")
        yield Footer()

    def on_mount(self) -> None:
        self._svc = ""
        self.update_title()
        table = self.query_one("#links", DataTable)
        table.add_columns("#", "Link", "Type", "Operational", "Setup")
        self.refresh_links()
        self.set_interval(5, self.update_title)

    # ── data / view ──────────────────────────────────────────────────────

    def update_title(self) -> None:
        rc, out = run(["systemctl", "is-active", "systemd-networkd"])
        self._svc = out.strip()
        self.title = "systemd-networkd"
        self.sub_title = f"service: {self._svc}"

    def refresh_links(self) -> None:
        table = self.query_one("#links", DataTable)
        prev = table.cursor_row if table.row_count else 0
        table.clear()
        self._links = list_links()
        for r in self._links:
            oper = Text(r["oper"], style=OPER_STYLE.get(r["oper"], ""))
            setup = Text(r["setup"], style=OPER_STYLE.get(r["setup"], ""))
            table.add_row(r["idx"], Text(r["name"], style="bold"),
                          r["type"], oper, setup, key=r["name"])
        if table.row_count:
            table.move_cursor(row=min(prev, table.row_count - 1))
            self._render_detail(self._current_name())
        else:
            self.query_one("#detail", Static).update(
                Panel(Text("No managed links found.", style="dim"),
                      border_style="cyan"))

    def _current_name(self):
        table = self.query_one("#links", DataTable)
        if not table.row_count:
            return None
        return self._links[table.cursor_row]["name"]

    def _render_detail(self, name) -> None:
        if not name:
            return
        self.query_one("#detail", Static).update(
            Panel(Text(link_status(name)), title=f"Link · {name}",
                  border_style="cyan", padding=(1, 2)))

    def on_data_table_row_highlighted(self, event) -> None:
        if event.row_key and event.row_key.value:
            self._render_detail(event.row_key.value)

    def on_data_table_row_selected(self, event) -> None:
        # Enter on the links table opens the guided editor.
        if event.row_key and event.row_key.value:
            self.push_screen(SettingsModal(event.row_key.value))

    # ── actions ──────────────────────────────────────────────────────────

    def action_refresh(self) -> None:
        self.refresh_links()
        self.update_title()
        self.notify("Refreshed")

    def action_settings(self) -> None:
        name = self._current_name()
        if name:
            self.push_screen(SettingsModal(name))

    def action_status(self) -> None:
        name = self._current_name()
        if name:
            self.push_screen(TextModal(f"Status · {name}", link_status(name)))

    def action_reconfigure(self) -> None:
        name = self._current_name()
        if not name:
            return
        rc, out = run(["sudo", "networkctl", "reconfigure", name])
        self._after(rc, f"Reconfigured {name}", out)

    def action_reload(self) -> None:
        rc, out = run(["sudo", "networkctl", "reload"])
        self._after(rc, "networkd reloaded", out)

    def action_restart(self) -> None:
        rc, out = run(["sudo", "systemctl", "restart", "systemd-networkd"])
        self._after(rc, "systemd-networkd restarted", out)

    def action_configs(self) -> None:
        self.push_screen(ConfigsScreen())

    def action_dns(self) -> None:
        rc, out = run(["resolvectl", "status"])
        self.push_screen(TextModal("DNS · systemd-resolved",
                                   out.strip() or "(resolvectl unavailable)"))

    def _after(self, rc, ok_msg, out) -> None:
        if rc == 0:
            self.notify(ok_msg)
        else:
            self.notify(f"Failed: {out.strip()[:200]}", severity="error")
        self.refresh_links()

    # ── editor (suspend the TUI, hand off to $EDITOR via networkctl) ──────

    def edit_target(self, target) -> None:
        env = {**os.environ, "SYSTEMD_EDITOR": EDITOR, "EDITOR": EDITOR}
        with self.suspend():
            subprocess.run(["sudo", "-E", "networkctl", "edit", target], env=env)
        self.refresh_links()
        self.notify(f"Edited {target}")


def main():
    NetworkdTUI().run()


if __name__ == "__main__":
    main()
