# keybridgeBT v2 — Ordered Task Breakdown (Wi-Fi TCP)

Only 10 tasks — everything not listed here is **unchanged** from v1 and already
working. The entire rewrite is scoped to the transport layer swap.

---

## Phase 1 — New Transport Layer (Tasks 1–2)

### Task 1: Mac `tcp_server.py` — TCP server
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/tcp_server.py` |
| **Platform** | macOS |
| **Libraries** | `socket`, `threading` (stdlib only) |
| **Depends on** | Nothing |
| **Replaces** | `bt_server.py` |
| **Must match API** | Same public interface as `RFCOMMServer` so `main.py` needs minimal changes |

```python
class TCPServer:
    """Single-client TCP server for streaming encrypted HID data."""

    def __init__(self, host: str = "0.0.0.0", port: int = 9741):
        self._host = host
        self._port = port
        self._server_sock: socket.socket | None = None
        self._client_sock: socket.socket | None = None
        self._connected = threading.Event()
        self._lock = threading.Lock()
        self._running = False
        self._thread: threading.Thread | None = None
        self._on_connect_callback = None
        self._on_disconnect_callback = None

    @property
    def is_connected(self) -> bool: ...

    @property
    def listen_address(self) -> str:
        """Return 'host:port' string for display in the menu bar."""

    def set_callbacks(self, on_connect=None, on_disconnect=None) -> None: ...

    def start(self) -> None:
        """Bind, listen, and accept connections in a background thread."""

    def stop(self) -> None:
        """Close all sockets and stop the accept loop."""

    def send(self, data: bytes) -> bool:
        """Send raw bytes to the connected client. Thread-safe."""

    def wait_for_connection(self, timeout: float = None) -> bool: ...

    def _accept_loop(self) -> None:
        """
        1. Create SO_REUSEADDR server socket, bind, listen(1)
        2. Loop: accept → set TCP_NODELAY + SO_KEEPALIVE
                → fire on_connect → monitor for disconnect
                → fire on_disconnect → re-accept
        3. Only one client at a time (close old before accepting new)
        """

    def _monitor_client(self) -> None:
        """
        Blocking read loop to detect client disconnect.
        We don't expect incoming data from the client (unidirectional stream),
        but recv() returning b"" signals TCP FIN.
        """
```

**Behaviour:**
- Bind to `host:port`, one client at a time
- When a client connects: fire `on_connect` callback
- Detect disconnect via `recv()` returning `b""` → fire `on_disconnect`, re-accept
- `send()` thread-safe, returns `False` if no client connected
- Set `TCP_NODELAY` on the client socket to disable Nagle's algorithm (low latency)
- Set `SO_KEEPALIVE` with short intervals to detect dead connections
- Wrap `send()` in a try/except for `BrokenPipeError` / `ConnectionResetError` → fire disconnect

**Security notes:**
- No TLS — encryption is handled by the application-layer `crypto_secretstream`
- Bind to `0.0.0.0` so it works on any network interface; the user can restrict to a specific IP in config
- Accept loop checks `self._running` to allow clean shutdown

---

### Task 2: Win `tcp_client.py` — TCP client
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/tcp_client.py` |
| **Platform** | Windows |
| **Libraries** | `socket`, `threading` (stdlib only) |
| **Depends on** | Nothing |
| **Replaces** | `bt_client.py` |
| **Must match API** | Same public interface as `RFCOMMClient` so `main.py` needs minimal changes |

```python
class TCPClient:
    """TCP client that connects to the mac-sender and reads raw data."""

    def __init__(self, host: str, port: int = 9741, callback=None):
        self._host = host
        self._port = port
        self._callback = callback
        self._sock: socket.socket | None = None
        self._connected = threading.Event()
        self._running = False
        self._thread: threading.Thread | None = None
        self._on_connect_callback = None
        self._on_disconnect_callback = None

    @property
    def is_connected(self) -> bool: ...

    @property
    def server_address(self) -> str:
        """Return 'host:port' string for display in the tray."""

    def set_callbacks(self, on_connect=None, on_disconnect=None) -> None: ...

    def start(self) -> None:
        """Connect in a background thread with auto-reconnect."""

    def stop(self) -> None: ...

    def _connect_loop(self) -> None:
        """
        1. Loop while self._running:
        2. Create socket, set TCP_NODELAY
        3. connect((host, port))
        4. Fire on_connect callback
        5. _read_loop() — blocks until disconnect
        6. Fire on_disconnect callback
        7. Exponential backoff: 1s → 2s → 4s → … → 30s max
        """

    def _read_loop(self) -> None:
        """
        Block on recv(4096), deliver to self._callback.
        Return when recv returns b"" (disconnect) or error.
        """
```

**Behaviour:**
- Connect to `host:port`, deliver raw bytes to `callback`
- Auto-reconnect with exponential backoff (1s → 30s)
- `TCP_NODELAY` on the socket
- Fire `on_connect` / `on_disconnect` callbacks
- No `send()` needed — this is a receive-only client

**Security notes:**
- Validate that `host` is not empty before connecting
- Handle `ConnectionRefusedError`, `TimeoutError`, `OSError` gracefully → retry

---

## Phase 2 — Wire Up Transport to Daemons (Tasks 3–4)

### Task 3: Mac `main.py` — swap transport
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/main.py` |
| **Depends on** | Task 1 |
| **Changes** | |

1. Replace `from .bt_server import RFCOMMServer` with `from .tcp_server import TCPServer`
2. Replace `RFCOMMServer(service_name=...)` with `TCPServer(host=..., port=...)`
3. Add `listen_host` and `listen_port` to `DEFAULT_CONFIG`
4. Log the Mac's IP address + port at startup so the user can configure Windows
5. Remove `set_non_discoverable()` call from `_on_client_connected` (BT-specific)
6. Remove `_enforce_link_encryption()` reference (handled by crypto layer now)

Everything else stays identical — `_on_client_connected`, `_on_client_disconnected`,
`_send_packet`, `_on_keyboard_report`, `_on_pointer_event` are unchanged.

---

### Task 4: Win `main.py` — swap transport
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/main.py` |
| **Depends on** | Task 2 |
| **Changes** | |

1. Replace `from .bt_client import RFCOMMClient` with `from .tcp_client import TCPClient`
2. Replace `RFCOMMClient(port=..., callback=...)` with `TCPClient(host=..., port=..., callback=...)`
3. Replace `com_port` in `DEFAULT_CONFIG` with `host` and `port`
4. Add `--host` CLI argument to `__main__.py` for quick override
5. Remove `--list-ports` CLI flag (no COM ports in v2)

Everything else stays identical — `_on_connected`, `_on_disconnected`, `_on_raw_data`,
`_process_data` are unchanged.

---

## Phase 3 — Config & Dependencies (Tasks 5–6)

### Task 5: Update config files
| | |
|---|---|
| **Files** | `mac-sender/config.yaml`, `win-receiver/config.yaml` |
| **Depends on** | Tasks 3, 4 |

**`mac-sender/config.yaml`:**
```yaml
listen_host: "0.0.0.0"
listen_port: 9741
hotkey_keycode: 111
hotkey_modifiers: 0x180000
log_level: INFO
```

**`win-receiver/config.yaml`:**
```yaml
host: null                     # Mac's IP address — null = prompt on startup
port: 9741
max_key_events_per_second: 20
log_level: INFO
```

---

### Task 6: Update requirements files + pyproject.toml
| | |
|---|---|
| **Files** | `mac-sender/requirements.txt`, `win-receiver/requirements.txt`, both `pyproject.toml` |
| **Depends on** | Tasks 3, 4 |

**mac-sender:** Remove `pyobjc-framework-IOBluetooth>=10.0`
**win-receiver:** Remove `pyserial>=3.5`

Update the `dependencies` lists in both `pyproject.toml` files to match.

---

## Phase 4 — UI Updates (Tasks 7–8)

### Task 7: Mac `menubar.py` — show IP:port
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/menubar.py` |
| **Depends on** | Task 3 |
| **Changes** | |

- Replace BT connection status item with: `Listening on {ip}:{port}`
- Show local IP address (auto-detect via `socket.gethostbyname(socket.gethostname())` or netifaces)
- Add "Copy IP:port" menu item for easy pasting on Windows

---

### Task 8: Win `tray.py` — show server address
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/tray.py` |
| **Depends on** | Task 4 |
| **Changes** | |

- Replace COM port info with: `Server: {host}:{port}`
- Show connection status: 🔗 Connected to `host:port` / ⏳ Connecting…

---

## Phase 5 — Tests & Docs (Tasks 9–10)

### Task 9: Transport integration tests
| | |
|---|---|
| **File** | `tests/test_tcp_transport.py` |
| **Depends on** | Tasks 1, 2 |
| **Test scenarios** | |

1. **Connect / disconnect round-trip:** Start TCPServer, connect TCPClient, verify `on_connect` fires on both sides, close client, verify `on_disconnect` fires
2. **Data delivery:** Server sends 100 framed packets → client receives all 100 intact
3. **Auto-reconnect:** Start server, connect client, kill client socket, verify client reconnects and `on_connect` fires again
4. **Full pipeline:** Server sends stream header + 5 encrypted keyboard packets → client deframes, decrypts, verifies plaintext matches
5. **Concurrent sends:** Multiple threads call `server.send()` simultaneously → no data corruption (thread safety)
6. **Clean shutdown:** Call `stop()` on both sides → threads join within 5 seconds, no exceptions

All tests run locally (localhost) — no network required.

---

### Task 10: Update docs + README
| | |
|---|---|
| **Files** | `docs/RUNNING.md`, `README.md` |
| **Depends on** | All previous tasks |
| **Changes** | |

- Replace all Bluetooth setup instructions with Wi-Fi TCP instructions
- Update architecture diagram in README
- Update config reference
- Remove BT troubleshooting section, add TCP troubleshooting (firewall, IP discovery)
- Keep key exchange instructions unchanged (they are transport-independent)

---

## Dependency Graph

```
Phase 1 (no deps):         T1, T2
Phase 2 (T1→T3, T2→T4):   T3, T4
Phase 3 (T3+T4):           T5, T6
Phase 4 (T3→T7, T4→T8):   T7, T8
Phase 5 (all above):       T9, T10
```

| Parallelisable | Tasks |
|---|---|
| Group A (zero deps) | T1, T2 |
| Group B (needs transport) | T3, T4 |
| Group C (needs daemon) | T5, T6, T7, T8 |
| Group D (needs everything) | T9, T10 |

---

## Files Changed Summary

| Action | File |
|---|---|
| **NEW** | `mac-sender/keybridgebt_mac/tcp_server.py` |
| **NEW** | `win-receiver/keybridgebt_win/tcp_client.py` |
| **NEW** | `tests/test_tcp_transport.py` |
| **MODIFIED** | `mac-sender/keybridgebt_mac/main.py` |
| **MODIFIED** | `win-receiver/keybridgebt_win/main.py` |
| **MODIFIED** | `win-receiver/keybridgebt_win/__main__.py` |
| **MODIFIED** | `mac-sender/keybridgebt_mac/menubar.py` |
| **MODIFIED** | `win-receiver/keybridgebt_win/tray.py` |
| **MODIFIED** | `mac-sender/config.yaml` |
| **MODIFIED** | `win-receiver/config.yaml` |
| **MODIFIED** | `mac-sender/requirements.txt` |
| **MODIFIED** | `win-receiver/requirements.txt` |
| **MODIFIED** | `mac-sender/pyproject.toml` |
| **MODIFIED** | `win-receiver/pyproject.toml` |
| **MODIFIED** | `docs/RUNNING.md` |
| **MODIFIED** | `README.md` |
| **UNCHANGED** | All 20+ other source files (crypto, packet, HID, injectors, etc.) |
