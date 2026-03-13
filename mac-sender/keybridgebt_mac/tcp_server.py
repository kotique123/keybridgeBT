"""
TCP server for mac-sender (v2).

Single-client TCP server that streams encrypted HID data to the Windows
receiver over Wi-Fi / LAN. Replaces the Bluetooth RFCOMM server from v1.

Drop-in replacement for RFCOMMServer — same public API:
  start(), stop(), send(), is_connected, wait_for_connection(),
  set_callbacks(on_connect, on_disconnect)

See docs/ARCHITECTURE-v2.md §3.1 for full spec.
"""

import logging
import socket
import threading

log = logging.getLogger(__name__)

# Default port — unregistered in IANA, above 1024 (no root needed).
DEFAULT_PORT = 9741


class TCPServer:
    """Single-client TCP server for streaming encrypted HID data."""

    def __init__(self, host: str = "0.0.0.0", port: int = DEFAULT_PORT):
        self._host = host
        self._port = port
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._client_addr = None
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
    def listen_address(self) -> str:
        """Return 'host:port' string for display in the menu bar."""
        return f"{self._host}:{self._port}"

    def set_callbacks(self, on_connect=None, on_disconnect=None) -> None:
        self._on_connect_callback = on_connect
        self._on_disconnect_callback = on_disconnect

    def start(self) -> None:
        """Bind, listen, and accept connections in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._accept_loop, name="TCPServer-accept", daemon=True
        )
        self._thread.start()
        log.info("TCPServer started on %s:%d", self._host, self._port)

    def stop(self) -> None:
        """Close all sockets and stop the accept loop."""
        self._running = False
        self._connected.clear()
        self._close_client()
        self._close_server()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None
        log.info("TCPServer stopped")

    def send(self, data: bytes) -> bool:
        """Send raw bytes to the connected client. Thread-safe."""
        with self._lock:
            sock = self._client_sock
        if sock is None:
            return False
        try:
            sock.sendall(data)
            return True
        except (BrokenPipeError, ConnectionResetError, OSError) as e:
            log.warning("Send failed (%s) — disconnecting client", e)
            self._handle_disconnect()
            return False

    def wait_for_connection(self, timeout: float = None) -> bool:
        """Block until a client connects (or timeout expires). Returns True if connected."""
        return self._connected.wait(timeout=timeout)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _accept_loop(self) -> None:
        """Create server socket, bind, listen, and accept clients in a loop."""
        try:
            self._server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.settimeout(1.0)  # so we can check _running periodically
            self._server_sock.bind((self._host, self._port))
            self._server_sock.listen(1)
            log.info("TCPServer listening on %s:%d", self._host, self._port)
        except OSError as e:
            log.error("TCPServer failed to bind on %s:%d: %s", self._host, self._port, e)
            self._running = False
            return

        while self._running:
            try:
                client_sock, addr = self._server_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                if self._running:
                    log.exception("TCPServer accept error")
                break

            log.info("Client connected from %s:%d", *addr)

            # If there's already a client, drop the old one first
            self._close_client()

            with self._lock:
                self._client_sock = client_sock
                self._client_addr = addr

            # Tune the socket for low-latency streaming
            try:
                client_sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                client_sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            except OSError:
                pass  # not fatal

            self._connected.set()
            if self._on_connect_callback:
                try:
                    self._on_connect_callback()
                except Exception:
                    log.exception("on_connect callback raised")

            # Block here until the client disconnects
            self._monitor_client()

        self._close_server()

    def _monitor_client(self) -> None:
        """Block reading from the client socket to detect disconnection.

        We don't expect incoming data (unidirectional stream), but
        recv() returning b'' signals TCP FIN from the client.
        """
        with self._lock:
            sock = self._client_sock

        if sock is None:
            return

        while self._running:
            try:
                data = sock.recv(256)
                if data == b"":
                    log.info("Client disconnected (TCP FIN)")
                    break
                # Unexpected data from client — ignore silently
            except OSError:
                if self._running:
                    log.debug("Client socket error during monitor")
                break

        self._handle_disconnect()

    def _handle_disconnect(self) -> None:
        """Called when the active client disconnects."""
        was_connected = self._connected.is_set()
        self._connected.clear()
        self._close_client()
        if was_connected and self._on_disconnect_callback:
            try:
                self._on_disconnect_callback()
            except Exception:
                log.exception("on_disconnect callback raised")

    def _close_client(self) -> None:
        """Close and forget the client socket."""
        with self._lock:
            sock = self._client_sock
            self._client_sock = None
            self._client_addr = None
        if sock:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    def _close_server(self) -> None:
        """Close the server listening socket."""
        sock = self._server_sock
        self._server_sock = None
        if sock:
            try:
                sock.close()
            except OSError:
                pass
