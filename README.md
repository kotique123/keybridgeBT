# keybridgeBT

Forward your Mac keyboard and trackpad to a Windows machine over Bluetooth — encrypted, low-latency, zero-config after first pairing.

![macOS](https://img.shields.io/badge/macOS-13.0%2B-blue?logo=apple)
![Windows](https://img.shields.io/badge/Windows-10%2B-0078D6?logo=windows)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

## What It Does

keybridgeBT lets you use your Mac's keyboard and trackpad to control a nearby Windows machine. It captures HID keyboard reports and trackpad events on macOS, encrypts them, and streams them over Bluetooth RFCOMM to a Windows receiver that injects them as native keystrokes and mouse events.

**Use cases:** dual-machine setups, KVM-free workflows, controlling a Windows desktop from a MacBook sitting next to it.

## Architecture

```
┌─────────────────────────────┐         Bluetooth RFCOMM          ┌─────────────────────────────┐
│        Mac (sender)         │ ◄──────────────────────────────► │      Windows (receiver)      │
│                             │    encrypted packets (PyNaCl)     │                             │
│  ┌───────────┐ ┌──────────┐ │    [type][seqno][ciphertext]     │  ┌───────────┐ ┌──────────┐ │
│  │ HID Reader│ │ Trackpad │ │                                   │  │Key Inject │ │Mouse Inj │ │
│  │  (hidapi) │ │(CGEvent) │ │                                   │  │(SendInput)│ │(SendInput)│ │
│  └─────┬─────┘ └────┬─────┘ │                                   │  └─────▲─────┘ └────▲─────┘ │
│        │             │       │                                   │        │             │       │
│        ▼             ▼       │                                   │        │             │       │
│  ┌─────────────────────────┐ │                                   │  ┌─────────────────────────┐ │
│  │   Encrypt → Pack → Send │ │ ——————————————————————————————→  │  │ Recv → Unpack → Decrypt │ │
│  └─────────────────────────┘ │                                   │  └─────────────────────────┘ │
│                             │                                   │                             │
│  ┌──────────┐ ┌───────────┐ │                                   │  ┌──────────┐ ┌───────────┐ │
│  │ Menubar  │ │  Hotkey   │ │                                   │  │Sys. Tray │ │Rate Limit │ │
│  │  (rumps) │ │ (⌘⇧F12)  │ │                                   │  │(pystray) │ │(20 key/s) │ │
│  └──────────┘ └───────────┘ │                                   │  └──────────┘ └───────────┘ │
└─────────────────────────────┘                                   └─────────────────────────────┘
```

## Security Model

- **End-to-end encryption:** All packets encrypted with PyNaCl `crypto_secretstream` (XChaCha20-Poly1305)
- **Key exchange:** X25519 keypair per device, exchanged via QR code + fingerprint confirmation on first run
- **Key storage:** macOS Keychain / Windows Credential Manager — never in plaintext files
- **Transport security:** BT link-level encryption enforced before accepting connections
- **Input validation:** HID keycode whitelist, 20 key/s rate limiter, sequence number validation
- **Post-pairing:** Mac goes non-discoverable after first successful pairing

## Wire Protocol

```
Frame:   [length (2B LE)] [packet]
Packet:  [type (1B)] [seqno (4B LE)] [encrypted payload]

Type 0x01 — Keyboard: 8-byte HID boot-protocol report
Type 0x02 — Pointer:  (buttons, dx, dy, scroll_v, scroll_h)
```

## Prerequisites

### macOS (sender)
- macOS 13.0+ (Ventura or later)
- Python 3.11+
- Bluetooth enabled
- **Accessibility** permission (for hotkey + trackpad capture)
- **Input Monitoring** permission (for HID keyboard access)

### Windows (receiver)
- Windows 10+
- Python 3.11+
- Bluetooth paired with the Mac
- No admin privileges required (`SendInput` works in user context)

## Installation

### Mac Sender

```bash
cd mac-sender
pip install -r requirements.txt
```

**As a launchd service (recommended):**
```bash
chmod +x install.sh
sudo ./install.sh
```

**Manual run:**
```bash
python -m keybridgebt_mac
```

### Windows Receiver

```bash
cd win-receiver
pip install -r requirements.txt
python -m keybridgebt_win
```

## First-Run Setup

Both sides need to exchange public keys once:

1. **Mac:** Run the sender — it will launch the setup wizard automatically
2. **Mac:** A QR code appears with the Mac's public key
3. **Windows:** Run the receiver — paste the Mac's public key when prompted
4. **Windows:** Copy the Windows public key shown and paste it on the Mac
5. **Both:** Confirm the 6-digit fingerprint matches on both screens
6. **Done.** Keys are stored securely. Future launches connect automatically.

## Usage

### Hotkey Toggle
Press **⌘⇧F12** (Cmd+Shift+F12) to pause/resume forwarding. When paused, your keyboard and trackpad work on the Mac as normal.

### Menu Bar (Mac)
Click the ⌨️ icon in the menu bar to see:
- Status: 🟢 Forwarding / ⏸ Paused
- Connection: 🔗 Connected / ⏳ Waiting
- Toggle button
- Settings
- Quit

### System Tray (Windows)
Right-click the tray icon to see connection status and quit.

## Configuration

### `mac-sender/config.yaml`
```yaml
service_name: keybridgeBT
hotkey_keycode: 111          # F12 (macOS virtual keycode)
hotkey_modifiers: 0x180000   # Cmd+Shift
log_level: INFO
```

### `win-receiver/config.yaml`
```yaml
com_port: null               # null = auto-detect BT serial port
max_key_events_per_second: 20
log_level: INFO
```

## Project Structure

```
keybridgeBT/
├── mac-sender/
│   ├── keybridgebt_mac/
│   │   ├── main.py              ← daemon orchestrator
│   │   ├── hid_reader.py        ← keyboard HID capture (hidapi)
│   │   ├── trackpad_reader.py   ← trackpad capture (CGEventTap)
│   │   ├── bt_server.py         ← RFCOMM server (IOBluetooth)
│   │   ├── packet.py            ← wire packet builder + framing
│   │   ├── crypto.py            ← PyNaCl encryption
│   │   ├── keychain.py          ← macOS Keychain storage
│   │   ├── setup_wizard.py      ← first-run key exchange
│   │   ├── toggle.py            ← hotkey monitor (CGEventTap)
│   │   └── menubar.py           ← rumps tray icon
│   ├── config.yaml
│   ├── requirements.txt
│   ├── install.sh
│   └── com.keybridgebt.sender.plist
├── win-receiver/
│   ├── keybridgebt_win/
│   │   ├── main.py              ← daemon orchestrator
│   │   ├── bt_client.py         ← RFCOMM client (pyserial)
│   │   ├── packet.py            ← wire packet parser + deframing
│   │   ├── crypto.py            ← PyNaCl decryption
│   │   ├── credential_store.py  ← Windows Credential Manager
│   │   ├── setup_wizard.py      ← first-run key exchange
│   │   ├── keycode_map.py       ← HID→VK mapping + whitelist
│   │   ├── key_injector.py      ← keyboard injection (SendInput)
│   │   ├── mouse_injector.py    ← pointer injection (SendInput)
│   │   ├── rate_limiter.py      ← sliding-window rate limiter
│   │   └── tray.py              ← pystray system tray
│   ├── config.yaml
│   └── requirements.txt
├── docs/
│   ├── ARCHITECTURE.md          ← full technical specification
│   └── TASKS.md                 ← ordered implementation task list
├── LICENSE
└── README.md
```

## Troubleshooting

| Issue | Fix |
|---|---|
| "No Apple keyboard HID device found" | Check that HID access is granted. Try running with `sudo` once to verify. |
| "Failed to create event tap" | Grant **Accessibility** in System Settings → Privacy & Security |
| Windows doesn't see the BT port | Pair the Mac in Windows Bluetooth settings first, then check Device Manager for COM ports |
| Keys stuck after disconnect | The receiver auto-releases all keys on disconnect. If stuck, restart the receiver. |
| Decryption failures | Re-run setup on both sides to regenerate keys |

## License

MIT — see [LICENSE](LICENSE).
