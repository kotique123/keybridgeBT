"""
macOS menu-bar tray icon for keybridgeBT.

Shows forwarding state, connection info (IP:port), and provides controls.
Uses rumps for a native-feeling status bar experience.

See docs/ARCHITECTURE-v2.md §5.8 for spec.
"""

import socket
import rumps
import logging

log = logging.getLogger(__name__)


class KeyBridgeTray(rumps.App):
    """Menu-bar app for keybridgeBT mac-sender."""

    def __init__(self, daemon):
        super().__init__(
            "keybridgeBT",
            title="⌨️➡️",
            quit_button=None,
        )
        self._daemon = daemon

        # Status items (non-clickable)
        self._status_item = rumps.MenuItem("Status: Starting…")
        self._status_item.set_callback(None)

        self._connection_item = rumps.MenuItem("Connection: Waiting…")
        self._connection_item.set_callback(None)
        self._listen_item = rumps.MenuItem(self._get_listen_label())
        self._listen_item.set_callback(None)

        self._copy_ip_item = rumps.MenuItem("Copy IP:port", callback=self._on_copy_ip)
        # Toggle button
        self._toggle_item = rumps.MenuItem(
            "⏸ Pause Forwarding", callback=self._on_toggle
        )

        # Hotkey info (non-clickable)
        self._hotkey_info = rumps.MenuItem("Hotkey: ⌃⌥K")
        self._hotkey_info.set_callback(None)

        # Settings
        self._settings_item = rumps.MenuItem("Settings…", callback=self._on_settings)

        # Quit
        self._quit_item = rumps.MenuItem("Quit keybridgeBT", callback=self._on_quit)

        self.menu = [
            self._status_item,
            self._connection_item,
            self._listen_item,
            self._copy_ip_item,
            None,                   # separator
            self._toggle_item,
            self._hotkey_info,
            None,                   # separator
            self._settings_item,
            self._quit_item,
        ]

        # Refresh timer (every 1s)
        self._timer = rumps.Timer(self._refresh_status, 1)
        self._timer.start()

    def _refresh_status(self, _):
        """Update menu items to reflect current daemon state."""
        forwarding = self._daemon.is_forwarding
        connected = self._daemon.is_connected

        # Title icon
        if not connected:
            self.title = "⌨️❌"
        elif forwarding:
            self.title = "⌨️➡️"
        else:
            self.title = "⌨️⏸"

        # Status text
        if forwarding:
            self._status_item.title = "Status: 🟢 Forwarding to Windows"
        else:
            self._status_item.title = "Status: ⏸ Input on Mac"

        # Connection text
        if connected:
            self._connection_item.title = "Connection: 🔗 Connected"
        else:
            self._connection_item.title = "Connection: ⏳ Waiting…"

        # Keep listen address fresh (IP may change)
        self._listen_item.title = self._get_listen_label()

        # Toggle button text
        if forwarding:
            self._toggle_item.title = "⏸ Pause Forwarding"
        else:
            self._toggle_item.title = "▶️ Resume Forwarding"

    def _on_toggle(self, _):
        self._daemon.toggle_forwarding()
        log.info("Forwarding toggled via tray")

    def _get_listen_label(self) -> str:
        """Return e.g. 'Listening on 192.168.1.5:9741'."""
        try:
            local_ip = socket.gethostbyname(socket.gethostname())
        except OSError:
            local_ip = "0.0.0.0"
        port = getattr(self._daemon, "_config", {}).get("listen_port", 9741)
        return f"Listening on {local_ip}:{port}"

    def _on_copy_ip(self, _):
        """Copy the IP:port string to the clipboard."""
        import subprocess
        label = self._get_listen_label().replace("Listening on ", "")
        subprocess.run(["pbcopy"], input=label.encode(), check=False)
        log.info("Copied IP:port to clipboard: %s", label)

    def _on_settings(self, _):
        rumps.Window(
            title="keybridgeBT Settings",
            message="Hotkey and connection settings.",
            default_text=f"Hotkey: Ctrl+Option+K\nListen address: {self._get_listen_label().replace('Listening on ', '')}",
            ok="Close",
            dimensions=(320, 100),
        ).run()

    def _on_quit(self, _):
        log.info("Quitting keybridgeBT")
        self._daemon.stop()
        rumps.quit_application()


def run_tray(daemon):
    """Launch the menu-bar tray app (blocks on the main thread)."""
    app = KeyBridgeTray(daemon)
    app.run()
