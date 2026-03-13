"""
Windows system tray icon for keybridgeBT.

Shows connection state and provides quit action.
Uses pystray + Pillow.

See docs/ARCHITECTURE.md §5.11 and docs/TASKS.md Task 19.
"""

import logging
import threading

log = logging.getLogger(__name__)

try:
    import pystray
    from PIL import Image, ImageDraw
except ImportError:
    pystray = None
    log.warning("pystray/Pillow not available — tray icon disabled")


def _create_icon_image(color: str) -> "Image.Image":
    """Create a simple colored circle icon."""
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse([8, 8, 56, 56], fill=color)
    return img


def run_tray(daemon):
    """Run the system tray icon. Call from a background thread."""
    if pystray is None:
        log.warning("Cannot run tray — pystray not installed")
        return

    def on_quit(icon, item):
        daemon.stop()
        icon.stop()

    def get_status(item):
        return "🔗 Connected" if daemon.is_connected else "⏳ Waiting…"

    def get_port(item):
        return f"Port: {daemon.port_name}"

    icon = pystray.Icon(
        "keybridgeBT",
        _create_icon_image("green" if daemon.is_connected else "gray"),
        "keybridgeBT Receiver",
        menu=pystray.Menu(
            pystray.MenuItem(get_status, None, enabled=False),
            pystray.MenuItem(get_port, None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", on_quit),
        ),
    )

    # Update icon color periodically
    def updater():
        import time
        while icon.visible:
            color = "green" if daemon.is_connected else "gray"
            icon.icon = _create_icon_image(color)
            time.sleep(2)

    threading.Thread(target=updater, daemon=True).start()
    icon.run()
