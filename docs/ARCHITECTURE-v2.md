# keybridgeBT v2 — Technical Specification (Wi-Fi TCP Transport)

## 1. System Overview

keybridgeBT forwards Mac keyboard and trackpad input to a Windows machine.
v2 replaces the Bluetooth RFCOMM transport with **Wi-Fi TCP**, keeping every
other layer (crypto, HID capture, key injection, rate limiter, UI) unchanged.

| Component | Platform | Role |
|---|---|---|
| **mac-sender** | macOS 13+ | Captures keyboard HID + trackpad, encrypts, streams over TCP |
| **win-receiver** | Windows 10+ | Receives TCP stream, decrypts, injects keystrokes + mouse via `SendInput` |

Both are Python 3.11+. The only communication channel is a single TCP socket
carrying length-prefixed encrypted packets.

### 1.1 Why Wi-Fi Instead of Bluetooth

| | BT RFCOMM (v1) | Wi-Fi TCP (v2) |
|---|---|---|
| Reliability | Frequent disconnects on Apple Silicon | Rock-solid on any LAN |
| Latency | ~8–15 ms | ~1–3 ms (LAN) |
| Setup | Pair, create outgoing COM port | Enter IP or use mDNS |
| macOS support | IOBluetooth deprecated, brittle | Standard BSD sockets |
| Works over Ethernet | No | Yes |
| Range | ~10 m | Entire LAN / VLAN |

---

## 2. Wire Protocol (unchanged from v1)

Every packet on the TCP stream has this structure:

```
┌──────────┬────────────┬──────────────────────────────┐
│ type (1B)│ seqno (4B) │ encrypted_payload (variable)  │
└──────────┴────────────┴──────────────────────────────┘
```

| Field | Size | Description |
|---|---|---|
| `type` | 1 byte | `0x01` = keyboard, `0x02` = pointer |
| `seqno` | 4 bytes | Unsigned 32-bit LE, monotonically increasing, resets on reconnect |
| `encrypted_payload` | variable | PyNaCl `crypto_secretstream` ciphertext |

### 2.1 Inner Keyboard Report (plaintext, 8 bytes)

Standard HID boot-protocol keyboard report:
```
[modifier, reserved, key1, key2, key3, key4, key5, key6]
```

### 2.2 Inner Pointer Report (plaintext, 7 bytes)

```
[buttons(1), dx(2 LE), dy(2 LE), scroll_v(1), scroll_h(1)]
```

### 2.3 Framing

Length-prefixed on the TCP stream:
```
┌────────────┬─────────────────────────┐
│ length (2B)│ packet (type+seqno+enc) │
└────────────┴─────────────────────────┘
```

### 2.4 Session Bootstrap

On each new TCP connection:
1. Mac sends a raw (unframed) **24-byte stream header** — the `crypto_secretstream` init header
2. Windows reads exactly 24 bytes, initialises `StreamDecryptor`
3. All subsequent data is length-prefixed encrypted packets

---

## 3. Transport Architecture (NEW in v2)

### 3.1 Mac Side — TCP Server (`tcp_server.py`)

```
┌─────────────────────────────────────────────────┐
│                  TCPServer                       │
│                                                  │
│  listen_host: str   (default "0.0.0.0")         │
│  listen_port: int   (default 9741)              │
│  _server_sock: socket                            │
│  _client_sock: socket | None                     │
│  _connected: threading.Event                     │
│  _lock: threading.Lock                           │
│                                                  │
│  start() → background thread: accept loop        │
│  stop()  → close sockets                         │
│  send(data: bytes) → bool                        │
│  wait_for_connection(timeout) → bool             │
│  is_connected → bool                             │
│  set_callbacks(on_connect, on_disconnect)         │
│                                                  │
│  Accept loop:                                    │
│    1. bind + listen (backlog=1)                  │
│    2. accept one client                          │
│    3. fire on_connect callback                   │
│    4. read loop (optional: for bidirectional)    │
│    5. on disconnect: fire callback, re-accept    │
│                                                  │
│  Single-client only (newest connection wins)     │
└─────────────────────────────────────────────────┘
```

**Key design decisions:**
- Binds to `0.0.0.0:9741` by default (configurable)
- Accepts **one client at a time** — if a new connection arrives, the old one is closed
- `send()` is thread-safe (protected by a lock)
- TCP_NODELAY enabled to minimise latency
- SO_KEEPALIVE enabled with short intervals to detect dead connections fast
- No TLS — encryption is handled at the application layer by `crypto_secretstream`

### 3.2 Windows Side — TCP Client (`tcp_client.py`)

```
┌─────────────────────────────────────────────────┐
│                  TCPClient                       │
│                                                  │
│  host: str          (Mac's IP / hostname)        │
│  port: int          (default 9741)              │
│  _sock: socket | None                            │
│  _connected: threading.Event                     │
│  _lock: threading.Lock                           │
│                                                  │
│  start() → background thread: connect loop       │
│  stop()  → close socket                          │
│  is_connected → bool                             │
│  set_callbacks(on_connect, on_disconnect)         │
│                                                  │
│  Connect loop:                                   │
│    1. connect to host:port                       │
│    2. fire on_connect callback                   │
│    3. read loop: recv → callback(data)           │
│    4. on disconnect: fire callback, reconnect    │
│    5. exponential backoff (1s → 30s max)         │
└─────────────────────────────────────────────────┘
```

**Key design decisions:**
- Connects to the Mac's IP address (configured or discovered via mDNS)
- Auto-reconnects with exponential backoff (1s → 2s → 4s → … → 30s cap)
- TCP_NODELAY enabled
- Delivers raw bytes to the same callback interface as the old `RFCOMMClient`
- Identical `set_callbacks(on_connect, on_disconnect)` API

### 3.3 mDNS / Bonjour Service Discovery (optional, Phase 2)

- Mac advertises `_keybridgebt._tcp.local.` on port 9741 via `zeroconf`
- Windows discovers automatically — no manual IP entry required
- Falls back to manual IP configuration if mDNS is unavailable
- **Deferred to Phase 2** — manual IP config is sufficient for v2.0

### 3.4 Port Choice

Default port: **9741** (unregistered in IANA, above 1024 so no root needed).
Configurable in both `config.yaml` files.

---

## 4. Security Architecture

### 4.1 Key Management (unchanged)

- Both sides generate an **X25519 keypair** on first run
- Mac stores in **macOS Keychain** (`keyring`, service `com.keybridgebt.sender`)
- Windows stores in **Windows Credential Manager** (`keyring`, service `com.keybridgebt.receiver`)
- Keys never stored in plaintext files

### 4.2 First-Launch Key Exchange (unchanged)

1. Mac generates keypair, displays public key as QR code
2. Windows scans/pastes Mac's public key
3. Windows displays its public key; Mac pastes it
4. Both compute shared secret via X25519 DH
5. Both display 6-digit fingerprint for confirmation
6. Keys stored in OS keychain

### 4.3 Session Encryption (unchanged)

- PyNaCl `crypto_secretstream_xchacha20poly1305`
- New stream header on each TCP connection
- Authenticated encryption + replay protection at the crypto layer
- Sequence numbers provide additional replay protection at the packet layer

### 4.4 Network Security Considerations (NEW in v2)

| Threat | Mitigation |
|---|---|
| Eavesdropping on LAN | All payloads encrypted with XChaCha20-Poly1305 — only length prefix is visible |
| Packet injection | Every packet authenticated; tampered packets rejected by AEAD |
| Replay attack | Sequence numbers + stream-bound crypto state |
| Unauthorised connection | Only clients with the pre-exchanged shared key can produce valid packets |
| Port scanning reveals service | Acceptable — the service is encrypted and authenticated. An attacker can see a listening port but cannot interact meaningfully |
| MITM during key exchange | 6-digit fingerprint verification |

### 4.5 Input Validation (unchanged)

- HID keycode whitelist before injection
- 20 key/s rate limiter
- Sequence number validation

---

## 5. Mac Sender — Component Design

### 5.1 HID Keyboard Reader (`hid_reader.py`) — unchanged
- hidapi, non-seizing, Apple VID detection (including Apple Silicon VID=0x0000)
- 8-byte boot-protocol reports

### 5.2 Trackpad Reader (`trackpad_reader.py`) — unchanged
- Quartz CGEventTap, listen-only
- dx/dy deltas, buttons, scroll

### 5.3 TCP Server (`tcp_server.py`) — NEW (replaces `bt_server.py`)
- See §3.1 above
- Same callback interface as `RFCOMMServer`

### 5.4 Packet Layer (`packet.py`) — unchanged
- `build_packet()`, `frame_packet()`, `next_seqno()`

### 5.5 Crypto (`crypto.py`) — unchanged
- `generate_keypair()`, `derive_shared_key()`, `compute_fingerprint()`
- `StreamEncryptor(shared_key)` with `.header` and `.encrypt()`

### 5.6 Keychain (`keychain.py`) — unchanged

### 5.7 Setup Wizard (`setup_wizard.py`) — unchanged

### 5.8 Menu Bar (`menubar.py`) — minor update
- Show IP address + port in the status menu instead of BT connection info

### 5.9 Hotkey Toggle (`toggle.py`) — unchanged

### 5.10 Main Daemon (`main.py`) — minor update
- Replace `RFCOMMServer` import with `TCPServer`
- Add `listen_host` and `listen_port` config fields
- Everything else identical

### 5.11 launchd Plist — unchanged

---

## 6. Windows Receiver — Component Design

### 6.1 TCP Client (`tcp_client.py`) — NEW (replaces `bt_client.py`)
- See §3.2 above
- Same callback interface as `RFCOMMClient`

### 6.2 Packet Layer (`packet.py`) — unchanged
- `PacketReader` with `feed()`, `validate_seqno()`, `reset()`

### 6.3 Crypto (`crypto.py`) — unchanged

### 6.4 Credential Store (`credential_store.py`) — unchanged

### 6.5 Setup Wizard (`setup_wizard.py`) — unchanged

### 6.6 Keycode Map (`keycode_map.py`) — unchanged

### 6.7 Key Injector (`key_injector.py`) — unchanged

### 6.8 Mouse Injector (`mouse_injector.py`) — unchanged

### 6.9 Rate Limiter (`rate_limiter.py`) — unchanged

### 6.10 Main Daemon (`main.py`) — minor update
- Replace `RFCOMMClient` import with `TCPClient`
- Replace `com_port` config with `host` + `port`
- Everything else identical

### 6.11 System Tray (`tray.py`) — minor update
- Show IP:port instead of COM port info

---

## 7. Configuration

### `mac-sender/config.yaml`
```yaml
listen_host: "0.0.0.0"      # bind address ("0.0.0.0" = all interfaces)
listen_port: 9741            # TCP port
service_name: keybridgeBT   # for mDNS (future)
hotkey_keycode: 111          # F12
hotkey_modifiers: 0x180000   # Cmd+Shift
log_level: INFO
```

### `win-receiver/config.yaml`
```yaml
host: null                   # Mac's IP — null = prompt on startup or use mDNS
port: 9741                   # TCP port
max_key_events_per_second: 20
log_level: INFO
```

---

## 8. Dependency Changes

### mac-sender `requirements.txt` (v2)

```
hidapi>=0.14
pyobjc-framework-Quartz>=10.0
PyNaCl>=1.5.0
rumps>=0.4.0
keyring>=25.0
qrcode[pil]>=7.4
Pillow>=10.0
pyyaml>=6.0
```

**Removed:** `pyobjc-framework-IOBluetooth` (no longer needed)

### win-receiver `requirements.txt` (v2)

```
PyNaCl>=1.5.0
keyring>=25.0
pystray>=0.19
Pillow>=10.0
pyyaml>=6.0
```

**Removed:** `pyserial` (no longer needed)

---

## 9. Data Flow (v2)

```
Mac Sender                                              Windows Receiver
─────────────────────────────────────────               ──────────────────────────────────────
                                                        
HID Reader ─── 8-byte report ──┐                        ┌── plaintext report ── KeyInjector
                                │                        │
Trackpad Reader ── event ──────┤                        ├── pointer fields ──── MouseInjector
                                ▼                        │
                         Daemon._send_packet()           │
                           │                              │
                    ┌──────┼──────────┐              ┌───┼──────────────┐
                    │  encrypt(plain) │              │  decrypt(cipher) │
                    │  build_packet() │              │  validate_seqno  │
                    │  frame_packet() │              │  rate_limit      │
                    └──────┬──────────┘              └───▲──────────────┘
                           │                              │
                           ▼                              │
                      TCP Server                    TCP Client
                    (0.0.0.0:9741)  ════════════  (connect to Mac IP)
                                    Wi-Fi / LAN
```

---

## 10. Migration from v1

| v1 file | v2 action |
|---|---|
| `mac-sender/keybridgebt_mac/bt_server.py` | Replaced by `tcp_server.py` |
| `win-receiver/keybridgebt_win/bt_client.py` | Replaced by `tcp_client.py` |
| `mac-sender/keybridgebt_mac/main.py` | Update imports + config keys |
| `win-receiver/keybridgebt_win/main.py` | Update imports + config keys |
| `mac-sender/keybridgebt_mac/menubar.py` | Show IP:port in menu |
| `win-receiver/keybridgebt_win/tray.py` | Show IP:port in menu |
| `mac-sender/config.yaml` | Replace BT config with `listen_host`/`listen_port` |
| `win-receiver/config.yaml` | Replace `com_port` with `host`/`port` |
| `mac-sender/requirements.txt` | Remove `pyobjc-framework-IOBluetooth` |
| `win-receiver/requirements.txt` | Remove `pyserial` |
| All other files | **No changes** |
