"""
Main daemon orchestrator for mac-sender.

Wires together: HID reader, trackpad reader, TCP server, crypto,
packet builder, hotkey toggle, and tray icon.

See docs/ARCHITECTURE-v2.md §3.1 and §5.10 for spec.
"""

import logging
import struct
import threading
import time
import signal
import sys
import os

import yaml

from .packet import TYPE_KEYBOARD, TYPE_POINTER, build_packet, frame_packet, next_seqno
from .crypto import StreamEncryptor
from .tcp_server import TCPServer
from .keyboard_tap import KeyboardTap
from .trackpad_reader import TrackpadReader

log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "listen_host": "0.0.0.0",
    "listen_port": 9741,
    "hotkey_keycode": 111,          # F12
    "hotkey_modifiers": 0x180000,   # Cmd+Shift
    "log_level": "INFO",
}


class Daemon:
    """keybridgeBT mac-sender daemon."""

    def __init__(self, config=None):
        self._config = {**DEFAULT_CONFIG, **(config or {})}
        self._forwarding = True
        self._lock = threading.Lock()
        self._seqno = 0
        self._encryptor = None

        # Load shared key
        from . import keychain
        self._shared_key = keychain.load_shared_key()

        # Components
        self._tcp = TCPServer(
            host=self._config["listen_host"],
            port=self._config["listen_port"],
        )
        self._tcp.set_callbacks(
            on_connect=self._on_client_connected,
            on_disconnect=self._on_client_disconnected,
        )
        self._keyboard = KeyboardTap(
            report_callback=self._on_keyboard_report,
            toggle_callback=self.toggle_forwarding,
            hotkey_keycode=self._config["hotkey_keycode"],
            hotkey_modifiers=self._config["hotkey_modifiers"],
        )
        self._trackpad = TrackpadReader(callback=self._on_pointer_event)

    @property
    def is_forwarding(self) -> bool:
        return self._forwarding

    @property
    def is_connected(self) -> bool:
        return self._tcp.is_connected

    @property
    def listen_address(self) -> str:
        return self._tcp.listen_address

    def toggle_forwarding(self):
        with self._lock:
            self._forwarding = not self._forwarding
            state = "FORWARDING" if self._forwarding else "PAUSED"
        # Update seizure state on both input readers
        self._keyboard.seize = self._forwarding
        self._trackpad.seize = self._forwarding
        log.info("State changed: %s (input %s)",
                 state, "seized" if self._forwarding else "released")

    def start(self):
        log.info("Starting keybridgeBT mac-sender daemon")

        if self._shared_key is None:
            log.error("No shared key found — run setup first")
            sys.exit(1)

        self._tcp.start()
        # Log the listen address so the user can configure the Windows side
        import socket as _socket
        try:
            local_ip = _socket.gethostbyname(_socket.gethostname())
        except OSError:
            local_ip = self._config["listen_host"]
        log.info("Listening on %s:%d (local IP: %s)",
                 self._config["listen_host"],
                 self._config["listen_port"],
                 local_ip)

        try:
            self._keyboard.start()
        except Exception as e:
            log.error("Keyboard capture failed: %s", e)

        try:
            self._trackpad.start()
        except Exception as e:
            log.error("Trackpad capture failed: %s", e)

        log.info("All components started — press Cmd+Shift+F12 to toggle input seizure")

    def stop(self):
        log.info("Stopping keybridgeBT mac-sender daemon")
        self._trackpad.stop()
        self._keyboard.stop()
        self._tcp.stop()
        log.info("Daemon stopped")

    def _on_client_connected(self):
        """New TCP connection — create fresh encryptor, send header, reset seqno."""
        with self._lock:
            self._seqno = 0
            self._encryptor = StreamEncryptor(self._shared_key)
            header = self._encryptor.header
        # Send the stream header as a raw (unframed) bootstrap
        self._tcp.send(header)
        log.info("Crypto session started, header sent (%d bytes)", len(header))

    def _on_client_disconnected(self):
        """TCP disconnect — reset crypto state."""
        with self._lock:
            self._encryptor = None
            self._seqno = 0
        log.info("Crypto session ended")

    def _send_packet(self, ptype: int, plaintext: bytes):
        """Encrypt, build, frame, and send a packet."""
        with self._lock:
            enc = self._encryptor
            if enc is None:
                return
            ciphertext = enc.encrypt(plaintext)
            seqno = self._seqno
            self._seqno = next_seqno(self._seqno)

        packet = build_packet(ptype, seqno, ciphertext)
        framed = frame_packet(packet)
        self._tcp.send(framed)

    def _on_keyboard_report(self, report: bytes):
        if not self._forwarding or not self._tcp.is_connected:
            return
        self._send_packet(TYPE_KEYBOARD, report)

    def _on_pointer_event(self, buttons, dx, dy, scroll_v, scroll_h):
        if not self._forwarding or not self._tcp.is_connected:
            return
        plaintext = struct.pack("<BhhBB",
                                buttons & 0xFF,
                                max(-32768, min(32767, dx)),
                                max(-32768, min(32767, dy)),
                                scroll_v & 0xFF,
                                scroll_h & 0xFF)
        self._send_packet(TYPE_POINTER, plaintext)


def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "..", "config.yaml")
    config_path = os.path.normpath(config_path)
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return yaml.safe_load(f) or {}
    return {}


def _setup_logging(log_level_str: str):
    """Configure logging. Writes to a file when running inside a .app bundle."""
    level = getattr(logging, log_level_str, logging.INFO)
    fmt = "%(asctime)s [%(name)s] %(levelname)s: %(message)s"

    # Detect .app bundle: the executable lives inside Contents/MacOS/
    in_app = ".app/Contents/" in (os.environ.get("__CFBundleIdentifier", "")
                                  or sys.executable or "")
    if not in_app:
        in_app = ".app/Contents/" in os.path.abspath(__file__)

    if in_app:
        log_dir = os.path.expanduser("~/Library/Logs/keybridgeBT")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, "sender.log")
        logging.basicConfig(
            level=level, format=fmt,
            handlers=[
                logging.FileHandler(log_file),
                logging.StreamHandler(),
            ],
        )
        logging.getLogger(__name__).info("Logging to %s", log_file)
    else:
        logging.basicConfig(level=level, format=fmt)


def main():
    config = load_config()
    _setup_logging(config.get("log_level", "INFO"))

    # Check if first-run setup is needed
    from . import keychain
    if not keychain.has_completed_setup():
        log.info("First run detected — starting setup wizard")
        from .setup_wizard import run_setup
        if not run_setup():
            log.error("Setup failed or cancelled")
            sys.exit(1)

    daemon = Daemon(config)

    def signal_handler(sig, frame):
        log.info("Signal received, shutting down…")
        daemon.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    daemon.start()

    # Run tray UI on the main thread (required by AppKit)
    try:
        from .menubar import run_tray
        run_tray(daemon)
    except ImportError:
        log.warning("rumps not available, running headless")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            daemon.stop()
