"""
Main daemon orchestrator for mac-sender.

Wires together: HID reader, trackpad reader, RFCOMM server, crypto,
packet builder, hotkey toggle, and tray icon.

See docs/ARCHITECTURE.md §4.10 and docs/TASKS.md Task 20.
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
from .bt_server import RFCOMMServer
from .hid_reader import HIDKeyboardReader
from .trackpad_reader import TrackpadReader
from .toggle import HotkeyMonitor

log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "service_name": "keybridgeBT",
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

        self.service_name = self._config["service_name"]

        # Load shared key
        from . import keychain
        self._shared_key = keychain.load_shared_key()

        # Components
        self._rfcomm = RFCOMMServer(service_name=self._config["service_name"])
        self._rfcomm.set_callbacks(
            on_connect=self._on_client_connected,
            on_disconnect=self._on_client_disconnected,
        )
        self._keyboard = HIDKeyboardReader(callback=self._on_keyboard_report)
        self._trackpad = TrackpadReader(callback=self._on_pointer_event)
        self._hotkey = HotkeyMonitor(
            callback=self.toggle_forwarding,
            keycode=self._config["hotkey_keycode"],
            modifiers=self._config["hotkey_modifiers"],
        )

    @property
    def is_forwarding(self) -> bool:
        return self._forwarding

    @property
    def is_connected(self) -> bool:
        return self._rfcomm.is_connected

    def toggle_forwarding(self):
        with self._lock:
            self._forwarding = not self._forwarding
            state = "FORWARDING" if self._forwarding else "PAUSED"
        log.info("State changed: %s", state)

    def start(self):
        log.info("Starting keybridgeBT mac-sender daemon")

        if self._shared_key is None:
            log.error("No shared key found — run setup first")
            sys.exit(1)

        self._rfcomm.start()

        try:
            self._keyboard.start()
        except (RuntimeError, OSError) as e:
            log.error("Keyboard capture failed: %s", e)

        try:
            self._trackpad.start()
        except Exception as e:
            log.error("Trackpad capture failed: %s", e)

        self._hotkey.start()
        log.info("All components started")

    def stop(self):
        log.info("Stopping keybridgeBT mac-sender daemon")
        self._hotkey.stop()
        self._trackpad.stop()
        self._keyboard.stop()
        self._rfcomm.stop()
        log.info("Daemon stopped")

    def _on_client_connected(self):
        """New BT connection — create fresh encryptor, send header, reset seqno."""
        with self._lock:
            self._seqno = 0
            self._encryptor = StreamEncryptor(self._shared_key)
            header = self._encryptor.header
        # Send the stream header as a raw (unframed) bootstrap
        self._rfcomm.send(header)
        log.info("Crypto session started, header sent (%d bytes)", len(header))

    def _on_client_disconnected(self):
        """BT disconnect — reset crypto state."""
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
        self._rfcomm.send(framed)

    def _on_keyboard_report(self, report: bytes):
        if not self._forwarding or not self._rfcomm.is_connected:
            return
        self._send_packet(TYPE_KEYBOARD, report)

    def _on_pointer_event(self, buttons, dx, dy, scroll_v, scroll_h):
        if not self._forwarding or not self._rfcomm.is_connected:
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


def main():
    config = load_config()
    log_level = config.get("log_level", "INFO")
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

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
