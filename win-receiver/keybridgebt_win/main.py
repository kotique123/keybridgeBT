"""
Main daemon orchestrator for win-receiver.

Wires together: RFCOMM client, crypto, packet parser, key injector,
mouse injector, rate limiter, and tray icon.

See docs/ARCHITECTURE.md §5.10 and docs/TASKS.md Task 21.
"""

import logging
import struct
import threading
import time
import signal
import sys
import os

import yaml

from .packet import PacketReader, TYPE_KEYBOARD, TYPE_POINTER
from .crypto import StreamDecryptor, STREAM_HEADER_LEN
from .bt_client import RFCOMMClient
from .key_injector import KeyInjector
from .mouse_injector import MouseInjector
from .rate_limiter import RateLimiter

log = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "com_port": None,               # None = auto-detect
    "max_key_events_per_second": 20,
    "log_level": "INFO",
}


class Daemon:
    """keybridgeBT win-receiver daemon."""

    def __init__(self, config=None):
        self._config = {**DEFAULT_CONFIG, **(config or {})}
        self._lock = threading.Lock()
        self._decryptor = None
        self._awaiting_header = True
        self._header_buf = bytearray()

        # Load shared key
        from . import credential_store
        self._shared_key = credential_store.load_shared_key()

        # Components
        self._packet_reader = PacketReader()
        self._key_injector = KeyInjector()
        self._mouse_injector = MouseInjector()
        self._rate_limiter = RateLimiter(
            max_events=self._config["max_key_events_per_second"]
        )

        self._client = RFCOMMClient(
            port=self._config["com_port"],
            callback=self._on_raw_data,
        )
        self._client.set_callbacks(
            on_connect=self._on_connected,
            on_disconnect=self._on_disconnected,
        )

    @property
    def is_connected(self) -> bool:
        return self._client.is_connected

    @property
    def port_name(self) -> str:
        return self._client.port_name

    def start(self):
        log.info("Starting keybridgeBT win-receiver daemon")

        if self._shared_key is None:
            log.error("No shared key found — run setup first")
            sys.exit(1)

        self._client.start()
        log.info("Receiver started, waiting for connection…")

    def stop(self):
        log.info("Stopping keybridgeBT win-receiver daemon")
        self._key_injector.release_all()
        self._mouse_injector.release_all()
        self._client.stop()
        log.info("Receiver stopped")

    def _on_connected(self):
        """New BT connection — prepare to receive stream header."""
        with self._lock:
            self._awaiting_header = True
            self._header_buf = bytearray()
            self._decryptor = None
            self._packet_reader.reset()
            self._rate_limiter.reset()
        log.info("Connected, awaiting crypto stream header…")

    def _on_disconnected(self):
        """BT disconnect — release all keys, reset state."""
        self._key_injector.release_all()
        self._mouse_injector.release_all()
        with self._lock:
            self._decryptor = None
            self._awaiting_header = True
            self._header_buf = bytearray()
            self._packet_reader.reset()
            self._rate_limiter.reset()
        log.info("Disconnected, all keys/buttons released")

    def _on_raw_data(self, data: bytes):
        """Handle raw bytes from the serial stream."""
        with self._lock:
            if self._awaiting_header:
                self._header_buf.extend(data)
                if len(self._header_buf) >= STREAM_HEADER_LEN:
                    header = bytes(self._header_buf[:STREAM_HEADER_LEN])
                    leftover = bytes(self._header_buf[STREAM_HEADER_LEN:])
                    try:
                        self._decryptor = StreamDecryptor(self._shared_key, header)
                        self._awaiting_header = False
                        log.info("Crypto session established")
                    except Exception:
                        log.exception("Failed to initialize decryptor")
                        return
                    if leftover:
                        self._process_data(leftover)
                return
            self._process_data(data)

    def _process_data(self, data: bytes):
        """Process framed data through the pipeline."""
        if self._decryptor is None:
            log.warning("_process_data called with no active decryptor, dropping")
            return
        packets = self._packet_reader.feed(data)
        for ptype, seqno, ciphertext in packets:
            # Validate sequence number
            if not self._packet_reader.validate_seqno(seqno):
                continue

            # Decrypt
            plaintext = self._decryptor.decrypt(ciphertext)
            if plaintext is None:
                continue

            # Route by type
            if ptype == TYPE_KEYBOARD:
                if not self._rate_limiter.allow():
                    log.debug("Rate limited keyboard event")
                    continue
                self._key_injector.inject_report(plaintext)

            elif ptype == TYPE_POINTER:
                if len(plaintext) >= 7:
                    buttons, dx, dy, sv, sh = struct.unpack("<BhhBB", plaintext[:7])
                    self._mouse_injector.inject_pointer(buttons, dx, dy, sv, sh)


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
    from . import credential_store
    if not credential_store.has_completed_setup():
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

    # Run tray icon
    try:
        from .tray import run_tray
        run_tray(daemon)
    except ImportError:
        log.warning("pystray not available, running headless")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            daemon.stop()
