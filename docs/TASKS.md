# keybridgeBT — Ordered Task Breakdown

Each task is scoped to one or two tightly related files, specifies exact
inputs/outputs/dependencies, and is ordered so no task depends on an
incomplete prior task.

---

## Phase 1 — Shared Primitives

### Task 1: Mac `packet.py` — Wire packet builder
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/packet.py` |
| **Platform** | macOS |
| **Libraries** | `struct` (stdlib) |
| **Depends on** | Nothing |
| **Inputs** | Packet type (`0x01`/`0x02`), sequence number (uint32), encrypted payload (bytes) |
| **Outputs** | Length-prefixed wire bytes: `[len(2)] [type(1)] [seqno(4)] [ciphertext]` |
| **Functions to implement** | |

```
HEADER_FMT = "<BL"  # type(1) + seqno(4)

def build_packet(ptype: int, seqno: int, encrypted_payload: bytes) -> bytes:
    """Build a wire packet: [type][seqno][encrypted_payload]."""

def frame_packet(packet: bytes) -> bytes:
    """Length-prefix a packet: [len_u16_le][packet]."""

def next_seqno(current: int) -> int:
    """Increment and return next sequence number (wraps at 2^32)."""
```

**Security notes:**
- Sequence number must be monotonically increasing per session
- Must reset to 0 on each new connection/session
- Maximum packet size check: reject if total exceeds 65535

---

### Task 2: Win `packet.py` — Wire packet parser
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/packet.py` |
| **Platform** | Windows |
| **Libraries** | `struct` (stdlib) |
| **Depends on** | Nothing |
| **Inputs** | Raw bytes from serial stream |
| **Outputs** | Parsed `(ptype, seqno, encrypted_payload)` tuples |
| **Functions to implement** | |

```
class PacketReader:
    """Stateful reader that deframes length-prefixed packets from a byte stream."""

    def __init__(self):
        self._buffer = bytearray()
        self._last_seqno = -1

    def feed(self, data: bytes) -> list[tuple[int, int, bytes]]:
        """Feed raw bytes, return list of complete parsed packets."""

    def validate_seqno(self, seqno: int) -> bool:
        """Return True if seqno is strictly greater than last seen."""

    def reset(self):
        """Reset state for a new session."""
```

**Security notes:**
- Must validate sequence numbers: drop any packet where `seqno <= last_seqno`
- Must handle partial reads gracefully (buffer until complete)
- Must reject packets larger than 65535 bytes

---

### Task 3: Mac `crypto.py` — Encryption module
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/crypto.py` |
| **Platform** | macOS |
| **Libraries** | `PyNaCl` (`nacl.public`, `nacl.bindings.crypto_secretstream_xchacha20poly1305`, `nacl.utils`) |
| **Depends on** | Nothing |
| **Inputs** | Peer public key (bytes), plaintext payload (bytes) |
| **Outputs** | Stream header (bytes, sent once per session), ciphertext (bytes per packet) |
| **Functions to implement** | |

```
def generate_keypair() -> tuple[bytes, bytes]:
    """Generate X25519 keypair. Returns (private_key, public_key)."""

def derive_shared_key(private_key: bytes, peer_public_key: bytes) -> bytes:
    """X25519 DH → 32-byte shared secret → crypto_secretstream key."""

def compute_fingerprint(shared_key: bytes) -> str:
    """First 6 decimal digits of SHA-256 of the shared key."""

class StreamEncryptor:
    """Wraps crypto_secretstream push state for a single session."""

    def __init__(self, shared_key: bytes): ...
    @property
    def header(self) -> bytes:
        """Stream header to send to receiver at session start."""
    def encrypt(self, plaintext: bytes) -> bytes:
        """Encrypt one message, returns ciphertext."""
```

**Security notes:**
- Key material must never be logged or printed
- Stream header is 24 bytes and must be sent once per connection before any data
- Each `StreamEncryptor` instance is single-use per session; create a new one on reconnect

---

### Task 4: Win `crypto.py` — Decryption module
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/crypto.py` |
| **Platform** | Windows |
| **Libraries** | `PyNaCl` |
| **Depends on** | Nothing |
| **Inputs** | Stream header (bytes), ciphertext (bytes per packet) |
| **Outputs** | Plaintext payload (bytes) |
| **Functions to implement** | |

```
def generate_keypair() -> tuple[bytes, bytes]:
    """Generate X25519 keypair. Returns (private_key, public_key)."""

def derive_shared_key(private_key: bytes, peer_public_key: bytes) -> bytes:
    """X25519 DH → 32-byte shared secret → crypto_secretstream key."""

def compute_fingerprint(shared_key: bytes) -> str:
    """First 6 decimal digits of SHA-256 of the shared key."""

class StreamDecryptor:
    """Wraps crypto_secretstream pull state for a single session."""

    def __init__(self, shared_key: bytes, header: bytes): ...
    def decrypt(self, ciphertext: bytes) -> bytes | None:
        """Decrypt one message. Returns None on auth failure."""
```

**Security notes:**
- Must reject ciphertext that fails authentication (returns None, caller drops packet)
- New `StreamDecryptor` on each reconnect, initialized with the new header
- Decryption failure should be logged at WARNING level but not raise

---

## Phase 2 — Infrastructure / Storage

### Task 5: Mac `keychain.py` — macOS Keychain store
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/keychain.py` |
| **Platform** | macOS |
| **Libraries** | `keyring` |
| **Depends on** | Nothing |
| **Inputs/Outputs** | Store/retrieve base64-encoded key material |
| **Functions to implement** | |

```
SERVICE = "com.keybridgebt.sender"

def store_private_key(key: bytes) -> None: ...
def load_private_key() -> bytes | None: ...
def store_public_key(key: bytes) -> None: ...
def load_public_key() -> bytes | None: ...
def store_peer_public_key(key: bytes) -> None: ...
def load_peer_public_key() -> bytes | None: ...
def store_shared_key(key: bytes) -> None: ...
def load_shared_key() -> bytes | None: ...
def has_completed_setup() -> bool:
    """True if all keys are present in keychain."""
```

**Security notes:**
- All values base64-encoded before storage
- Uses macOS Keychain via keyring — locked behind user login
- Never log key values

---

### Task 6: Win `credential_store.py` — Windows Credential Manager store
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/credential_store.py` |
| **Platform** | Windows |
| **Libraries** | `keyring` |
| **Depends on** | Nothing |
| **Inputs/Outputs** | Store/retrieve base64-encoded key material |
| **Functions to implement** | |

```
SERVICE = "com.keybridgebt.receiver"

def store_private_key(key: bytes) -> None: ...
def load_private_key() -> bytes | None: ...
def store_public_key(key: bytes) -> None: ...
def load_public_key() -> bytes | None: ...
def store_peer_public_key(key: bytes) -> None: ...
def load_peer_public_key() -> bytes | None: ...
def store_shared_key(key: bytes) -> None: ...
def load_shared_key() -> bytes | None: ...
def has_completed_setup() -> bool: ...
```

**Security notes:**
- Same pattern as Task 5 but backed by Windows Credential Manager
- Never log key values

---

### Task 7: Mac `bt_server.py` — RFCOMM server
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/bt_server.py` |
| **Platform** | macOS |
| **Libraries** | `pyobjc-framework-IOBluetooth`, `objc` |
| **Depends on** | Nothing (standalone Bluetooth infrastructure) |
| **Inputs** | Data bytes to send |
| **Outputs** | Connected/disconnected events, incoming data notifications |
| **Classes to implement** | |

```
class RFCOMMDelegate(NSObject):
    """ObjC delegate for IOBluetoothRFCOMMChannel events."""
    def rfcommChannelOpenComplete_status_(self, channel, status): ...
    def rfcommChannelClosed_(self, channel): ...
    def rfcommChannelData_data_length_(self, channel, data, length): ...

class RFCOMMServer:
    """Publish an RFCOMM service and stream data to connected clients."""

    def __init__(self, service_name: str = "keybridgeBT"): ...

    @property
    def is_connected(self) -> bool: ...

    def start(self) -> None:
        """Publish SDP service, start listening (background thread)."""

    def stop(self) -> None: ...

    def send(self, data: bytes) -> bool:
        """Send raw bytes to the connected client. Thread-safe."""

    def wait_for_connection(self, timeout: float = None) -> bool: ...

    def set_non_discoverable(self) -> None:
        """Set Mac to non-discoverable after first pairing."""

    def _enforce_link_encryption(self, device) -> bool:
        """Check that BT link-level encryption is active."""
```

**Security notes:**
- Must call `_enforce_link_encryption` before accepting channel data
- Must call `set_non_discoverable` after first successful pairing
- `writeSync_length_` for reliable delivery
- Handle delegate callbacks on the IOBluetooth run loop thread
- Thread-safe `send()` protected by a lock

---

### Task 8: Win `bt_client.py` — RFCOMM client
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/bt_client.py` |
| **Platform** | Windows |
| **Libraries** | `pyserial`, `serial.tools.list_ports` |
| **Depends on** | Nothing |
| **Inputs** | COM port name or auto-detect |
| **Outputs** | Raw bytes to a callback |
| **Classes to implement** | |

```
class RFCOMMClient:
    """Connect to BT serial COM port and read length-prefixed packets."""

    def __init__(self, port: str = None, callback=None): ...

    @property
    def is_connected(self) -> bool: ...

    def start(self) -> None:
        """Background thread: connect and read packets."""

    def stop(self) -> None: ...

    def _connect_loop(self) -> None:
        """Auto-reconnect with exponential backoff (max 30s)."""

    def _read_loop(self) -> None:
        """Read length-prefixed packets and deliver to callback."""

    @staticmethod
    def _detect_bt_port() -> str | None:
        """Auto-detect Bluetooth serial port by scanning COM ports."""

    def _read_exact(self, n: int) -> bytes | None:
        """Read exactly n bytes, return None on timeout/disconnect."""
```

**Security notes:**
- Exponential backoff prevents reconnection storms
- Must handle partial reads (length prefix may span multiple reads)
- No data validation here — that's the packet parser's job

---

## Phase 3 — Core I/O Logic

### Task 9: Mac `hid_reader.py` — Keyboard HID capture
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/hid_reader.py` |
| **Platform** | macOS |
| **Libraries** | `hidapi` (`hid` package) |
| **Depends on** | Nothing |
| **Inputs** | Apple keyboard HID device |
| **Outputs** | 8-byte HID boot-protocol reports to a callback |
| **Classes to implement** | |

```
APPLE_VID = 0x05AC
USAGE_PAGE_KEYBOARD = 0x01
USAGE_KEYBOARD = 0x06

class HIDKeyboardReader:
    def __init__(self, callback: Callable[[bytes], None]): ...
    def start(self) -> None:
        """Open HID device (non-seizing) and read in background thread."""
    def stop(self) -> None: ...
    def _find_apple_keyboard(self) -> dict | None: ...
    def _read_loop(self) -> None: ...
```

**Security notes:**
- **Do NOT use `kIOHIDOptionsTypeSeizeDevice`** — non-seizing so crashes don't lock the keyboard
- Must handle device disconnect gracefully (log and exit loop, don't crash)
- `set_nonblocking(False)` with a timeout to allow clean shutdown

---

### Task 10: Mac `trackpad_reader.py` — Trackpad capture via CGEventTap
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/trackpad_reader.py` |
| **Platform** | macOS |
| **Libraries** | `Quartz` (pyobjc) |
| **Depends on** | Nothing |
| **Inputs** | macOS trackpad/mouse events |
| **Outputs** | `(buttons, dx, dy, scroll_v, scroll_h)` to a callback |
| **Classes to implement** | |

```
class TrackpadReader:
    def __init__(self, callback: Callable[[int, int, int, int, int], None]): ...
    def start(self) -> None:
        """Create CGEventTap for pointer events, run in background thread."""
    def stop(self) -> None: ...
    def _tap_callback(self, proxy, event_type, event, refcon) -> event: ...
```

**Event types to tap:**
- `kCGEventMouseMoved` — dx, dy deltas
- `kCGEventLeftMouseDown/Up` — button 1
- `kCGEventRightMouseDown/Up` — button 2
- `kCGEventOtherMouseDown/Up` — button 3
- `kCGEventLeftMouseDragged`, `kCGEventRightMouseDragged`, `kCGEventOtherMouseDragged` — movement while buttons held
- `kCGEventScrollWheel` — `scrollWheelEventDeltaAxis1` (vertical), `scrollWheelEventDeltaAxis2` (horizontal)

**How to extract deltas:**
```python
dx = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaX)
dy = Quartz.CGEventGetIntegerValueField(event, Quartz.kCGMouseEventDeltaY)
```

**Security notes:**
- Requires Accessibility permission; fail gracefully with clear error if tap creation fails
- The tap callback runs on the CFRunLoop thread — keep it fast, no blocking I/O
- If the tap gets disabled by the system (e.g., permissions revoked), detect and re-enable

---

### Task 11: Win `keycode_map.py` — HID-to-VK mapping + whitelist
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/keycode_map.py` |
| **Platform** | Windows |
| **Libraries** | None (pure data) |
| **Depends on** | Nothing |
| **Inputs** | HID usage IDs |
| **Outputs** | Windows VK codes |
| **Data to implement** | |

```
HID_TO_VK: dict[int, int]       # HID usage ID → VK code (full keyboard)
HID_MOD_TO_VK: dict[int, int]   # modifier bit → VK code
EXTENDED_VK: set[int]            # VK codes needing KEYEVENTF_EXTENDEDKEY
VALID_HID_RANGE: set[int]        # whitelist of all legitimate HID keyboard codes
```

Coverage required:
- Letters: 0x04–0x1D → VK 0x41–0x5A
- Digits: 0x1E–0x27 → VK 0x30–0x39
- Symbols: 0x2D–0x38 → OEM VK codes
- Control: Enter, Esc, Backspace, Tab, Space, Caps Lock
- Function: F1–F24 (0x3A–0x73)
- Navigation: Ins, Home, PgUp, Del, End, PgDn, Arrows
- Numpad: NumLock, KP 0–9, KP operators, KP Enter
- Media: Mute, Vol Up, Vol Down
- Modifiers: 8 bits → 8 VK codes

`VALID_HID_RANGE` = all keys in `HID_TO_VK` ∪ all modifier bit positions that map to real VKs.

**Security notes:**
- This is the **validation whitelist** — any HID code not in `VALID_HID_RANGE` must be rejected before injection
- Must be exhaustive but conservative — only include real keyboard codes, no vendor-specific or reserved ranges

---

### Task 12: Win `key_injector.py` — Keyboard injection
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/key_injector.py` |
| **Platform** | Windows |
| **Libraries** | `ctypes` (Win32 `user32.SendInput`) |
| **Depends on** | Task 11 (`keycode_map.py`) |
| **Inputs** | 8-byte HID keyboard report (plaintext, already decrypted) |
| **Outputs** | Injected key events via `SendInput` |
| **Classes to implement** | |

```
class KeyInjector:
    def __init__(self): ...
    def inject_report(self, report: bytes) -> None:
        """Diff against prev state, validate keycodes, inject press/release."""
    def release_all(self) -> None:
        """Release all held keys (call on disconnect)."""
    @staticmethod
    def _make_key_input(vk: int, down: bool) -> INPUT: ...
```

**Security notes:**
- **Must validate** every HID keycode in the report against `VALID_HID_RANGE` before looking up VK code
- Reject (skip) any key not in the whitelist — log at WARNING
- Does NOT require admin privileges — `SendInput()` works for the current desktop session
- `release_all()` is critical safety: call on disconnect to prevent stuck keys

---

### Task 13: Win `mouse_injector.py` — Pointer injection
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/mouse_injector.py` |
| **Platform** | Windows |
| **Libraries** | `ctypes` (Win32 `user32.SendInput`) |
| **Depends on** | Nothing |
| **Inputs** | `(buttons, dx, dy, scroll_v, scroll_h)` from decrypted pointer report |
| **Outputs** | Injected mouse events via `SendInput` |
| **Classes to implement** | |

```
class MouseInjector:
    def __init__(self): ...
    def inject_pointer(self, buttons, dx, dy, scroll_v, scroll_h) -> None: ...
    def release_all(self) -> None: ...
```

**Security notes:**
- Movement deltas are relative — no absolute positioning that could target specific UI elements
- `release_all()` releases all buttons on disconnect
- Scroll values converted to Windows `WHEEL_DELTA` units (120 per notch)

---

### Task 14: Win `rate_limiter.py` — Event rate limiter
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/rate_limiter.py` |
| **Platform** | Windows |
| **Libraries** | `time` (stdlib), `collections.deque` |
| **Depends on** | Nothing |
| **Inputs** | Called once per keyboard event |
| **Outputs** | `True` (allow) or `False` (drop) |
| **Functions to implement** | |

```
class RateLimiter:
    """Sliding-window rate limiter for keyboard events."""

    def __init__(self, max_events: int = 20, window_seconds: float = 1.0): ...

    def allow(self) -> bool:
        """Record an event and return whether it should be allowed."""

    def reset(self) -> None:
        """Clear state (call on reconnect)."""
```

**Security notes:**
- Only applies to keyboard events (type `0x01`) — pointer events are exempt
- 20 events/second is generous for human typing (world record is ~15 keys/sec)
- Prevents abuse if the channel is hijacked and flooded with synthetic events
- Uses `time.monotonic()` to avoid clock-skew issues

---

## Phase 4 — First-Run Setup

### Task 15: Mac `setup_wizard.py` — Key exchange (Mac side)
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/setup_wizard.py` |
| **Platform** | macOS |
| **Libraries** | `qrcode`, `Pillow`, `PyNaCl` |
| **Depends on** | Task 3 (`crypto.py`), Task 5 (`keychain.py`) |
| **Inputs** | User interaction |
| **Outputs** | Keys stored in macOS Keychain |
| **Functions to implement** | |

```
def run_setup() -> bool:
    """
    Interactive first-run setup:
    1. Generate keypair (crypto.generate_keypair)
    2. Display public key as QR code (PNG via qrcode lib, shown in Preview or Tk)
    3. Prompt user to paste the Windows public key
    4. Derive shared key (crypto.derive_shared_key)
    5. Display 6-digit fingerprint (crypto.compute_fingerprint)
    6. User confirms → store all keys in keychain
    Returns True on success.
    """
```

**Security notes:**
- QR code display should use a minimal Tk window or save to a temp PNG (not write to a world-readable location)
- The user-pasted public key must be validated (correct length, valid base64)
- Fingerprint confirmation prevents MITM during key exchange

---

### Task 16: Win `setup_wizard.py` — Key exchange (Windows side)
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/setup_wizard.py` |
| **Platform** | Windows |
| **Libraries** | `PyNaCl` |
| **Depends on** | Task 4 (`crypto.py`), Task 6 (`credential_store.py`) |
| **Inputs** | User interaction |
| **Outputs** | Keys stored in Windows Credential Manager |
| **Functions to implement** | |

```
def run_setup() -> bool:
    """
    Interactive first-run setup:
    1. Generate keypair
    2. Prompt user to paste Mac's public key (from QR or manual copy)
    3. Display own public key for Mac to accept
    4. Derive shared key
    5. Display 6-digit fingerprint — user confirms it matches Mac's display
    6. Store all keys
    Returns True on success.
    """
```

**Security notes:**
- Same validation as Task 15 — verify pasted key format
- Fingerprint must match the Mac's display exactly

---

## Phase 5 — User Experience

### Task 17: Mac `toggle.py` — Hotkey monitor
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/toggle.py` |
| **Platform** | macOS |
| **Libraries** | `Quartz` (CGEventTap) |
| **Depends on** | Nothing |
| **Inputs** | Global keyboard events |
| **Outputs** | Calls a toggle callback when hotkey is pressed |
| **Classes to implement** | |

```
DEFAULT_KEYCODE = 111      # macOS vkeycode for F12
DEFAULT_MODIFIERS = kCGEventFlagMaskCommand | kCGEventFlagMaskShift

class HotkeyMonitor:
    def __init__(self, callback, keycode=DEFAULT_KEYCODE,
                 modifiers=DEFAULT_MODIFIERS): ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
```

**Security notes:**
- Requires Accessibility permission
- Consumes the hotkey event (returns `None` from tap) so it doesn't propagate
- Must detect if tap gets disabled and re-enable it

---

### Task 18: Mac `menubar.py` — rumps tray icon
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/menubar.py` |
| **Platform** | macOS |
| **Libraries** | `rumps` |
| **Depends on** | Nothing (reads state from daemon object) |
| **Inputs** | Daemon state: `is_forwarding`, `is_connected` |
| **Outputs** | Visual feedback, toggle/quit actions |
| **Classes to implement** | |

```
class KeyBridgeTray(rumps.App):
    def __init__(self, daemon): ...

def run_tray(daemon) -> None:
    """Block on the main thread running the rumps app."""
```

Menu items:
- Status: 🟢 Forwarding / ⏸ Paused
- Connection: 🔗 Connected / ⏳ Waiting / ❌ Disconnected
- Toggle Forwarding (button)
- Hotkey: ⌘⇧F12
- Settings...
- Quit

Title icons: `⌨️➡️` (forwarding), `⌨️⏸` (paused), `⌨️❌` (disconnected)

**Security notes:**
- No sensitive data displayed in the menu
- Settings dialog is cosmetic placeholder for v0.1

---

### Task 19: Win `tray.py` — pystray system tray
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/tray.py` |
| **Platform** | Windows |
| **Libraries** | `pystray`, `Pillow` |
| **Depends on** | Nothing |
| **Inputs** | Daemon state: `is_connected` |
| **Outputs** | Visual feedback, quit action |
| **Functions to implement** | |

```
def run_tray(daemon) -> None:
    """Run system tray icon (blocking, call from main thread or background)."""
```

Menu items:
- Status: 🔗 Connected / ⏳ Waiting
- COM port info
- Quit

**Security notes:**
- No sensitive data (keys, BT addresses) in tray menu

---

## Phase 6 — Integration (Main Orchestrators)

### Task 20: Mac `main.py` — Daemon orchestrator
| | |
|---|---|
| **File** | `mac-sender/keybridgebt_mac/main.py` + `__main__.py` |
| **Platform** | macOS |
| **Libraries** | All mac-sender modules |
| **Depends on** | Tasks 1, 3, 5, 7, 9, 10, 15, 17, 18 |
| **Responsibility** | Wire everything together |
| **Classes to implement** | |

```
class Daemon:
    def __init__(self, config: dict): ...

    @property
    def is_forwarding(self) -> bool: ...
    @property
    def is_connected(self) -> bool: ...
    @property
    def service_name(self) -> str: ...

    def toggle_forwarding(self) -> None: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...

    # Internal callbacks
    def _on_keyboard_report(self, report: bytes) -> None:
        """If forwarding+connected: encrypt→pack→frame→send."""
    def _on_pointer_event(self, buttons, dx, dy, sv, sh) -> None:
        """If forwarding+connected: encrypt→pack→frame→send."""
    def _on_client_connected(self) -> None:
        """Create new StreamEncryptor, send header, reset seqno."""
    def _on_client_disconnected(self) -> None:
        """Destroy encryptor, reset seqno, log."""

def load_config() -> dict: ...
def main() -> None: ...
```

**Startup sequence:**
1. Load config from `config.yaml`
2. Check if setup is complete (`keychain.has_completed_setup()`) — if not, run `setup_wizard.run_setup()`
3. Load shared key from keychain
4. Start RFCOMM server
5. Start HID keyboard reader
6. Start trackpad reader
7. Start hotkey monitor
8. Run menubar tray on main thread

**On new connection:**
1. Enforce link encryption
2. Create fresh `StreamEncryptor`
3. Send stream header (special unencrypted bootstrap packet)
4. Reset sequence counter to 0
5. After first pairing: set non-discoverable

**On disconnect:**
1. Reset encryptor and seqno
2. Log, resume listening

**Security notes:**
- Config loaded with safe YAML (`yaml.safe_load`)
- No fallback to unencrypted mode — if keys are missing, abort

---

### Task 21: Win `main.py` — Daemon orchestrator
| | |
|---|---|
| **File** | `win-receiver/keybridgebt_win/main.py` + `__main__.py` |
| **Platform** | Windows |
| **Libraries** | All win-receiver modules |
| **Depends on** | Tasks 2, 4, 6, 8, 11, 12, 13, 14, 16, 19 |
| **Responsibility** | Wire everything together |
| **Classes to implement** | |

```
class Daemon:
    def __init__(self, config: dict): ...

    @property
    def is_connected(self) -> bool: ...

    def start(self) -> None: ...
    def stop(self) -> None: ...

    def _on_raw_data(self, data: bytes) -> None:
        """Feed to PacketReader, process complete packets."""

    def _process_packet(self, ptype: int, seqno: int, ciphertext: bytes) -> None:
        """Decrypt → validate → route to injector."""
```

**Startup sequence:**
1. Load config
2. Check setup complete — if not, run `setup_wizard.run_setup()`
3. Load shared key
4. Start RFCOMM client
5. Start tray icon

**On new connection:**
1. Read stream header (first packet from Mac)
2. Create fresh `StreamDecryptor`
3. Reset packet reader sequence state

**Packet processing pipeline:**
1. `PacketReader.feed(raw_data)` → list of `(ptype, seqno, ciphertext)`
2. `PacketReader.validate_seqno(seqno)` → drop if False
3. `StreamDecryptor.decrypt(ciphertext)` → drop if None (auth failure)
4. Route by type:
   - `0x01`: validate HID codes against whitelist → `RateLimiter.allow()` → `KeyInjector.inject_report()`
   - `0x02`: unpack pointer fields → `MouseInjector.inject_pointer()`

**On disconnect:**
- `KeyInjector.release_all()`
- `MouseInjector.release_all()`
- `RateLimiter.reset()`
- `PacketReader.reset()`

**Security notes:**
- Every packet passes through: seqno validation → decryption → type routing → keycode whitelist → rate limiter → injection
- Five layers of defense before any keystroke reaches the OS
- No fallback to unencrypted mode

---

## Phase 7 — Deployment

### Task 22: Mac `com.keybridgebt.sender.plist` + `install.sh`
| | |
|---|---|
| **Files** | `mac-sender/com.keybridgebt.sender.plist`, `mac-sender/install.sh` |
| **Platform** | macOS |
| **Depends on** | Task 20 |
| **Deliverables** | |

**`com.keybridgebt.sender.plist`:**
```xml
Label: com.keybridgebt.sender
ProgramArguments: /usr/bin/python3 -m keybridgebt_mac
WorkingDirectory: /opt/keybridgebt/mac-sender
RunAtLoad: true
KeepAlive: true
UserName: _keybridgebt           ← dedicated low-privilege user
StandardOutPath: /var/log/keybridgebt/sender.log
StandardErrorPath: /var/log/keybridgebt/sender.log
```

**`install.sh`:**
1. Create user `_keybridgebt` (if not exists)
2. Copy files to `/opt/keybridgebt/mac-sender/`
3. `pip install -r requirements.txt`
4. Create log directory with correct ownership
5. Install plist to `/Library/LaunchDaemons/`
6. Load with `launchctl load`

**Security notes:**
- Runs as `_keybridgebt`, not root or the main user
- `KeepAlive` ensures auto-restart on crash
- Log directory owned by `_keybridgebt`
- The dedicated user needs Accessibility and Input Monitoring permissions

---

### Task 23: Requirements files
| | |
|---|---|
| **Files** | `mac-sender/requirements.txt`, `win-receiver/requirements.txt` |
| **Platform** | Both |
| **Depends on** | All previous tasks (to know final dependency set) |

**`mac-sender/requirements.txt`:**
```
hidapi>=0.14
pyobjc-framework-IOBluetooth>=10.0
pyobjc-framework-Quartz>=10.0
PyNaCl>=1.5.0
rumps>=0.4.0
keyring>=25.0
qrcode[pil]>=7.4
Pillow>=10.0
pyyaml>=6.0
```

**`win-receiver/requirements.txt`:**
```
pyserial>=3.5
PyNaCl>=1.5.0
keyring>=25.0
pystray>=0.19
Pillow>=10.0
pyyaml>=6.0
```

---

### Task 24: Config files
| | |
|---|---|
| **Files** | `mac-sender/config.yaml`, `win-receiver/config.yaml` |
| **Platform** | Both |
| **Depends on** | All previous tasks |

**`mac-sender/config.yaml`:**
```yaml
service_name: keybridgeBT
hotkey_keycode: 111          # F12
hotkey_modifiers: 0x180000   # Cmd+Shift
log_level: INFO
```

**`win-receiver/config.yaml`:**
```yaml
com_port: null               # null = auto-detect
max_key_events_per_second: 20
log_level: INFO
```

---

### Task 25: `README.md`
| | |
|---|---|
| **File** | `README.md` |
| **Platform** | Both |
| **Depends on** | All previous tasks |

Contents:
1. Project description with badges
2. Architecture diagram (ASCII)
3. Security model summary
4. Prerequisites (macOS permissions, Windows BT pairing)
5. Installation (Mac + Windows)
6. First-run setup (key exchange)
7. Usage (hotkey, tray icon)
8. Configuration
9. Troubleshooting
10. License

---

## Phase 8 — Verification

### Task 26: Integration test plan
| | |
|---|---|
| **Platform** | Both |
| **Depends on** | All previous tasks |

**Test scenarios to verify:**

1. **Protocol round-trip:** Build a keyboard packet on Mac side → parse on Windows side → verify fields match
2. **Crypto round-trip:** Encrypt on Mac → decrypt on Windows with same shared key → plaintext matches
3. **Sequence validation:** Feed packets with seqno [1, 2, 2, 5, 3, 6] → only [1, 2, 5, 6] accepted
4. **Rate limiter:** Feed 25 events in 1 second → first 20 accepted, last 5 rejected
5. **Keycode whitelist:** Inject report with HID code 0xFF → rejected; HID code 0x04 (A) → accepted
6. **Full pipeline (Mac):** HID report → callback → encrypt → pack → frame → raw bytes
7. **Full pipeline (Win):** Raw bytes → deframe → parse → validate seqno → decrypt → validate keycode → rate limit → inject
8. **Reconnection:** Simulate disconnect → verify sequence numbers reset, crypto state reset, keys released
9. **Hotkey toggle:** Verify forwarding pauses/resumes
10. **Keychain round-trip:** Store key → load key → verify match

---

## Dependency Graph Summary

```
Phase 1 (no deps):     T1, T2, T3, T4
Phase 2 (no deps):     T5, T6, T7, T8
Phase 3 (T11 → T12):   T9, T10, T11, T12, T13, T14
Phase 4 (T3+T5→T15, T4+T6→T16): T15, T16
Phase 5 (no deps):     T17, T18, T19
Phase 6 (all above):   T20, T21
Phase 7 (T20→T22):     T22, T23, T24, T25
Phase 8 (all):         T26
```

Tasks within the same phase can be parallelized across agents with no
conflicts, as long as inter-task dependencies are respected:

| Parallelizable groups | Tasks |
|---|---|
| Group A (zero deps) | T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, T11, T13, T14, T17, T18, T19 |
| Group B (needs T11) | T12 |
| Group C (needs T3+T5) | T15 |
| Group D (needs T4+T6) | T16 |
| Group E (needs all core) | T20, T21 |
| Group F (needs T20/T21) | T22, T23, T24, T25 |
| Group G (needs everything) | T26 |
