"""
TCP client for win-receiver (v2).

Connects to the mac-sender's TCP server over Wi-Fi / LAN and delivers
raw bytes to a callback. Auto-reconnects with exponential backoff.
Replaces the Bluetooth RFCOMM client from v1.

Drop-in replacement for RFCOMMClient — same public API:
  start(), stop(), is_connected, server_address,
  set_callbacks(on_connect, on_disconnect)
  Constructor: TCPClient(host, port, callback)

See docs/ARCHITECTURE-v2.md §3.2 for full spec.
"""

import logging
import socket
import threading
import time

log = logging.getLogger(__name__)

DEFAULT_PORT = 9741
_BACKOFF_MIN = 1.0    # seconds
_BACKOFF_MAX = 30.0   # seconds


class TCPClient:
    """TCP client that connects to the mac-sender and reads raw data."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, callback=None):
        if not host:
            raise ValueError("host must not be empty")
        self._host = host
        self._port = port
        self._callback = callback
        self._sock: socket.socket | None = None
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._on_connect_callback = None
        self._on_disconnect_callback = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        return self._connected.is_set()

    @property
    def server_address(self) -> str:
        """Return 'host:port' string for display in the tray."""
        return f"{self._host}:{self._port}"

    def set_callbacks(self, on_connect=None, on_disconnect=None) -> None:
        self._on_connect_callback = on_connect
        self._on_disconnect_callback = on_disconnect

    def start(self) -> None:
        """Connect in a background thread with auto-reconnect."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._connect_loop, name="TCPClient-connect", daemon=True
        )
        self._thread.start()
        log.info("TCPClient started, connecting to %s:%d", self._host, self._port)

    def stop(self) -> None:
        """Stop the client and close the socket."""
        self._running = False
        self._connected.clear()
        self._close_socket()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        log.info("TCPClient stopped")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _connect_loop(self) -> None:
        """Connect with exponential backoff, reconnect on disconnect."""
        backoff = _BACKOFF_MIN
        while self._running:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(10.0)  # connection timeout
                log.info("Connecting to %s:%d …", self._host, self._port)
                sock.connect((self._host, self._port))
                sock.settimeout(None)  # blocking reads after connection
            except (ConnectionRefusedError, TimeoutError, OSError) as e:
                sock.close()
                if not self._running:
                    break
                log.warning("Cannot connect to %s:%d: %s — retry in %.0fs",
                            self._host, self._port, e, backoff)
                self._interruptible_sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)
                continue

            # Connected
            backoff = _BACKOFF_MIN  # reset on successful connection
            with self._lock:
                self._sock = sock
            self._connected.set()
            log.info("Connected to %s:%d", self._host, self._port)

            if self._on_connect_callback:
                try:
                    self._on_connect_callback()
                except Exception:
                    log.exception("on_connect callback raised")

            self._read_loop(sock)

            # Disconnected
            self._connected.clear()
            self._close_socket()
            if self._on_disconnect_callback:
                try:
                    self._on_disconnect_callback()
                except Exception:
                    log.exception("on_disconnect callback raised")

            if self._running:
                log.info("Disconnected — reconnecting in %.0fs", backoff)
                self._interruptible_sleep(backoff)
                backoff = min(backoff * 2, _BACKOFF_MAX)

    def _read_loop(self, sock: socket.socket) -> None:
        """Block on recv, deliver bytes to callback. Return on disconnect."""
        while self._running:
            try:
                data = sock.recv(4096)
                if data == b"":
                    log.info("Server closed connection (TCP FIN)")
                    return
                if self._callback:
                    try:
                        self._callback(data)
                    except Exception:
                        log.exception("data callback raised")
            except OSError:
                if self._running:
                    log.debug("TCPClient socket error during read")
                return

    def _close_socket(self) -> None:
        with self._lock:
            sock = self._sock
            self._sock = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _interruptible_sleep(self, seconds: float) -> None:
        """Sleep in small increments so stop() can interrupt quickly."""
        deadline = time.monotonic() + seconds
        while self._running and time.monotonic() < deadline:
            time.sleep(0.1)
