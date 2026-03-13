# keybridgeBT — Technical Specification

## 1. System Overview

keybridgeBT is a two-component system that forwards a Mac keyboard and trackpad input to a Windows machine over Bluetooth RFCOMM.

| Component | Platform | Role |
|---|---|---|
| **mac-sender** | macOS | Captures keyboard HID reports + trackpad events, encrypts, streams over BT RFCOMM |
| **win-receiver** | Windows | Receives RFCOMM stream, decrypts, injects keystrokes + mouse events via `SendInput()` |

Both are Python. Fully decoupled — the only communication channel is raw bytes over RFCOMM.

---

## 2. Wire Protocol

Every packet on the RFCOMM channel has this structure:

```
┌──────────┬────────────┬──────────────────────────────┐
│ type (1B)│ seqno (4B) │ encrypted_payload (variable)  │
└──────────┴────────────┴──────────────────────────────┘
```

| Field | Size | Description |
|---|---|---|
| `type` | 1 byte | `0x01` = keyboard, `0x02` = pointer |
| `seqno` | 4 bytes | Unsigned 32-bit LE, monotonically increasing per session, resets on reconnect |
| `encrypted_payload` | variable | PyNaCl `crypto_secretstream` ciphertext of the inner report |

### 2.1 Inner Keyboard Report (plaintext, 8 bytes)

Standard HID boot-protocol keyboard report:

```
[modifier, reserved, key1, key2, key3, key4, key5, key6]
```

- `modifier` — bitmask: bit 0 = LCtrl, bit 1 = LShift, bit 2 = LAlt, bit 3 = LGUI, bits 4–7 = right variants
- `reserved` — always `0x00`
- `key1..key6` — up to 6 simultaneous HID Usage IDs (page 0x07); `0x00` = no key

### 2.2 Inner Pointer Report (plaintext, 7 bytes)

```
[buttons, dx_lo, dx_hi, dy_lo, dy_hi, scroll_v, scroll_h]
```

- `buttons` — bitmask: bit 0 = left, bit 1 = right, bit 2 = middle
- `dx`, `dy` — signed 16-bit LE relative deltas
- `scroll_v`, `scroll_h` — signed 8-bit scroll deltas

### 2.3 Framing

Packets are length-prefixed on the RFCOMM stream to handle partial reads:

```
┌────────────┬─────────────────────────┐
│ length (2B)│ packet (type+seqno+enc) │
└────────────┴─────────────────────────┘
```

`length` is an unsigned 16-bit LE value giving the total size of the packet that follows. Max packet size: 65535 bytes (practical max ≈ 100 bytes).

---

## 3. Security Architecture

### 3.1 Key Management

- Both sides generate an **X25519 keypair** on first run
- Mac stores keypair in **macOS Keychain** via the `keyring` library
- Windows stores keypair in **Windows Credential Manager** via the `keyring` library
- Keys are **never** stored in plaintext files

### 3.2 First-Launch Key Exchange (Setup Wizard)

1. Mac generates keypair, displays its public key as a **QR code** (in a window or terminal)
2. Windows scans or manually pastes the Mac's public key
3. Windows sends its public key back to the Mac (displayed for manual confirmation, or via a one-time BT exchange)
4. Both sides compute a shared secret via X25519 Diffie-Hellman
5. Both display a **short numeric fingerprint** (first 6 digits of the shared secret hash) simultaneously
6. User confirms the fingerprints match on both screens
7. Both sides derive a `crypto_secretstream` key from the shared secret and store it in the system keychain

### 3.3 Session Encryption

- All data packets (after initial key exchange) use **PyNaCl `crypto_secretstream_xchacha20poly1305`**
- This provides:
  - Authenticated encryption (tamper-proof)
  - Built-in replay/reordering protection at the crypto layer
  - Forward secrecy per session (new stream header on each connection)
- Sequence numbers provide an **additional** replay protection layer at the packet level

### 3.4 Bluetooth Transport Security

- Mac must enforce **BT link-level encryption** via IOBluetooth before accepting any channel connection
- After first successful pairing, Mac sets itself to **non-discoverable** mode
- The RFCOMM channel is effectively double-encrypted (BT link + application crypto_secretstream)

### 3.5 Input Validation (Windows)

- All HID keycodes are validated against a **whitelist** of legitimate keyboard usage IDs before injection
- Any keycode outside the normal keyboard range (0x04–0x73, 0x7F–0x81) is silently dropped
- A **rate limiter** rejects bursts exceeding 20 key events/second — no legitimate human types faster
- Out-of-order or duplicate sequence numbers cause silent packet drop

---

## 4. Mac Sender — Detailed Design

### 4.1 HID Keyboard Reader (`hid_reader.py`)

- Library: **hidapi** (via `hid` Python package)
- Opens the Apple keyboard HID device (vendor `0x05AC`, usage page `0x01`, usage `0x06`)
- **Does NOT use `kIOHIDOptionsTypeSeizeDevice`** — non-seizing access so a daemon crash doesn't leave the keyboard dead
- Reads 8-byte boot-protocol keyboard reports in a background thread
- Delivers raw reports to a callback

### 4.2 Trackpad Reader (`trackpad_reader.py`)

- Library: **Quartz** (via pyobjc `Quartz` framework)
- Uses **`CGEventTap`** — NOT raw HID. Apple's trackpad uses proprietary multitouch HID extensions that don't map to standard usage IDs. CGEventTap gives clean x/y deltas, scroll axes, and button state.
- Taps into `kCGEventMouseMoved`, `kCGEventLeftMouseDragged`, `kCGEventRightMouseDragged`, `kCGEventOtherMouseDragged`, `kCGEventScrollWheel`, `kCGEventLeftMouseDown/Up`, `kCGEventRightMouseDown/Up`, `kCGEventOtherMouseDown/Up`
- Extracts: dx (deltaX), dy (deltaY), button state, scrollWheelEventDeltaAxis1 (vertical), scrollWheelEventDeltaAxis2 (horizontal)
- Delivers pointer events to a callback
- Requires **Accessibility** permission

### 4.3 Bluetooth RFCOMM Server (`bt_server.py`)

- Library: **pyobjc IOBluetooth** bindings
- Publishes an SDP service record for RFCOMM
- Enforces link-level encryption before accepting a channel (checks `IOBluetoothDevice.isConnected()` + encryption status)
- Sets Mac to non-discoverable after first successful pairing
- Handles client disconnect gracefully — resumes listening without restart
- Sends length-prefixed packets via `writeSync_length_`
- Thread-safe `send(data)` method

### 4.4 Packet Layer (`packet.py`)

- Builds wire packets: `[type(1)] [seqno(4)] [encrypted_payload]`
- Wraps with length prefix for framing: `[length(2)] [packet]`
- Maintains a per-session sequence counter (uint32), resets on reconnect
- Provides `pack_keyboard(report, crypto_state)` and `pack_pointer(buttons, dx, dy, sv, sh, crypto_state)` functions

### 4.5 Crypto (`crypto.py`)

- Library: **PyNaCl** (`nacl.utils`, `nacl.public`, `nacl.bindings.crypto_secretstream`)
- Generates X25519 keypair
- Derives shared secret from local private key + remote public key
- Creates a `crypto_secretstream` push state (sender) per session
- Encrypts payloads with `crypto_secretstream_xchacha20poly1305_push`
- Sends the stream header to the receiver at the start of each connection (special handshake packet)

### 4.6 Keychain (`keychain.py`)

- Library: **keyring**
- Stores/retrieves: own private key, own public key, peer public key, derived shared key
- Service name: `com.keybridgebt.sender`
- All keys stored as base64-encoded strings

### 4.7 Setup Wizard (`setup_wizard.py`)

- Libraries: **qrcode**, **Pillow** (for QR display), **PyNaCl**
- On first run (no keys in keychain):
  1. Generate keypair
  2. Display public key as QR code (using a small Tk window or terminal)
  3. Prompt user to enter the Windows public key (pasted)
  4. Compute shared secret, display 6-digit numeric fingerprint
  5. User confirms match, keys are stored in keychain

### 4.8 Menu Bar (`menubar.py`)

- Library: **rumps**
- Shows status icon: ⌨️➡️ (forwarding), ⌨️⏸ (paused), ⌨️❌ (disconnected)
- Menu items: Status line, Connection line, Toggle button, Hotkey info, Settings, Quit
- Refreshes every 1 second via `rumps.Timer`

### 4.9 Hotkey Toggle (`toggle.py`)

- Library: **Quartz** (`CGEventTap`)
- Default: **Cmd+Shift+F12** (configurable)
- Creates a `CGEventTap` at `kCGSessionEventTap` to intercept the hotkey
- On match: toggles forwarding state, consumes the event
- Requires **Accessibility** permission

### 4.10 Main / Daemon (`main.py`)

- Orchestrates all components: starts RFCOMM server, HID reader, trackpad reader, hotkey monitor
- On keyboard/pointer callback: if forwarding + connected → pack + encrypt + send
- On client disconnect: release all keys, reset crypto stream, reset sequence numbers, wait for reconnect
- Runs `menubar.run_tray(daemon)` on the main thread (required by AppKit)

### 4.11 launchd Plist (`com.keybridgebt.sender.plist`)

- Runs as a dedicated low-privilege user (not the main user)
- `KeepAlive: true` — auto-restart on crash
- Stdout/stderr → `/var/log/keybridgebt/sender.log`

---

## 5. Windows Receiver — Detailed Design

### 5.1 RFCOMM Client (`bt_client.py`)

- Library: **pyserial**
- Connects to a Bluetooth serial COM port (auto-detect or configured)
- Reads length-prefixed packets from the stream
- Auto-reconnects on disconnect (exponential backoff, capped at 30s)
- Delivers raw packets to a callback

### 5.2 Packet Layer (`packet.py`)

- Parses wire packets: reads `[length(2)]`, then `[type(1)] [seqno(4)] [encrypted_payload]`
- Validates sequence numbers: must be strictly greater than previous; drops duplicates/out-of-order silently
- Returns `(type, plaintext_payload)` after decryption

### 5.3 Crypto (`crypto.py`)

- Library: **PyNaCl**
- Generates X25519 keypair
- Derives shared secret from local private key + remote public key
- Creates a `crypto_secretstream` pull state (receiver) per session
- Decrypts payloads with `crypto_secretstream_xchacha20poly1305_pull`
- Receives the stream header from the sender at the start of each connection

### 5.4 Credential Store (`credential_store.py`)

- Library: **keyring**
- Stores/retrieves: own private key, own public key, peer public key, derived shared key
- Service name: `com.keybridgebt.receiver`

### 5.5 Setup Wizard (`setup_wizard.py`)

- Libraries: **pyzbar** or manual paste, **PyNaCl**
- On first run:
  1. Generate keypair
  2. Prompt user to scan/paste the Mac's public key
  3. Display own public key for the Mac to accept
  4. Compute shared secret, display 6-digit numeric fingerprint
  5. User confirms match, keys are stored in credential manager

### 5.6 Keycode Map (`keycode_map.py`)

- Pure data file: `HID_TO_VK` dict (HID usage ID → Windows VK code)
- `HID_MOD_TO_VK` dict (modifier bits → VK codes)
- `EXTENDED_VK` set (VK codes needing `KEYEVENTF_EXTENDEDKEY`)
- `VALID_HID_RANGE` set — whitelist of legitimate keyboard HID codes
- Coverage: full keyboard — letters, digits, symbols, F1–F24, nav cluster, numpad, media keys

### 5.7 Key Injector (`key_injector.py`)

- Library: **ctypes** (Win32 `SendInput`)
- Takes an 8-byte HID keyboard report
- Diffs against previous report to detect press/release transitions
- Maps HID codes → VK codes via `keycode_map.py`
- **Validates** every keycode against `VALID_HID_RANGE` before injection — rejects out-of-range
- Creates `INPUT` structures with `KEYEVENTF_EXTENDEDKEY` for extended keys
- `release_all()` — safety method to release all held keys on disconnect

### 5.8 Mouse Injector (`mouse_injector.py`)

- Library: **ctypes** (Win32 `SendInput`)
- Takes `(buttons, dx, dy, scroll_v, scroll_h)`
- Diffs button state to detect press/release
- Injects `MOUSEEVENTF_MOVE`, `MOUSEEVENTF_*DOWN/UP`, `MOUSEEVENTF_WHEEL`, `MOUSEEVENTF_HWHEEL`
- `release_all()` — releases all held buttons on disconnect

### 5.9 Rate Limiter (`rate_limiter.py`)

- Sliding window rate limiter
- Tracks keyboard event timestamps
- Rejects bursts exceeding **20 key events/second**
- Returns `True` (allow) or `False` (drop) for each event
- Separate from pointer events (pointer events are not rate-limited)

### 5.10 Main / Daemon (`main.py`)

- Orchestrates: RFCOMM client, crypto, packet parser, key injector, mouse injector, rate limiter, tray icon
- On packet received: decrypt → validate seqno → check type → validate keycode/rate-limit → inject
- On disconnect: release all keys/buttons, reset crypto stream, reset sequence counter
- Shows system tray icon with connection state

### 5.11 System Tray (`tray.py` — optional, or integrated into `main.py`)

- Library: **pystray** + **Pillow**
- Shows connection state: 🔗 Connected / ⏳ Waiting / ❌ Error
- Menu: Status, COM port info, Quit

---

## 6. Dependency Summary

### mac-sender `requirements.txt`

```
hidapi
pyobjc-framework-IOBluetooth
pyobjc-framework-Quartz
PyNaCl
rumps
keyring
qrcode[pil]
pyyaml
```

### win-receiver `requirements.txt`

```
pyserial
PyNaCl
keyring
pystray
Pillow
pyyaml
```

---

## 7. File Structure

```
keybridgeBT/
├── .gitignore
├── LICENSE
├── README.md
├── mac-sender/
│   ├── requirements.txt
│   ├── config.yaml                        ← runtime config (hotkey, log level)
│   ├── com.keybridgebt.sender.plist       ← launchd service definition
│   ├── install.sh                         ← creates user, installs plist
│   └── keybridgebt_mac/
│       ├── __init__.py
│       ├── __main__.py                    ← entry: python -m keybridgebt_mac
│       ├── main.py                        ← daemon orchestrator
│       ├── hid_reader.py                  ← HID keyboard capture (hidapi)
│       ├── trackpad_reader.py             ← trackpad capture (CGEventTap)
│       ├── bt_server.py                   ← RFCOMM server (IOBluetooth)
│       ├── packet.py                      ← wire packet build + length framing
│       ├── crypto.py                      ← PyNaCl encrypt / keygen / handshake
│       ├── keychain.py                    ← keyring store/retrieve
│       ├── setup_wizard.py                ← first-run QR key exchange
│       ├── toggle.py                      ← hotkey monitor (CGEventTap)
│       └── menubar.py                     ← rumps tray icon
├── win-receiver/
│   ├── requirements.txt
│   ├── config.yaml
│   └── keybridgebt_win/
│       ├── __init__.py
│       ├── __main__.py                    ← entry: python -m keybridgebt_win
│       ├── main.py                        ← daemon orchestrator
│       ├── bt_client.py                   ← RFCOMM client (pyserial)
│       ├── packet.py                      ← wire packet parse + length deframe
│       ├── crypto.py                      ← PyNaCl decrypt / keygen / handshake
│       ├── credential_store.py            ← keyring store/retrieve
│       ├── setup_wizard.py                ← first-run key exchange
│       ├── keycode_map.py                 ← HID→VK tables + whitelist
│       ├── key_injector.py                ← keyboard injection (SendInput)
│       ├── mouse_injector.py              ← pointer injection (SendInput)
│       ├── rate_limiter.py                ← sliding-window rate limiter
│       └── tray.py                        ← pystray system tray
└── docs/
    └── ARCHITECTURE.md                    ← this file
```
