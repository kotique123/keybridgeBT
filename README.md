# keybridgeBT

Forward your Mac keyboard and trackpad to a Windows machine over **Wi-Fi TCP** — encrypted, low-latency, zero-config after first key exchange.

![macOS](https://img.shields.io/badge/macOS-13.0%2B-blue?logo=apple)
![Windows](https://img.shields.io/badge/Windows-10%2B-0078D6?logo=windows)
![Python](https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white)
![License](https://img.shields.io/badge/license-MIT-green)

## What It Does

keybridgeBT lets you use your Mac's keyboard and trackpad to control a nearby Windows machine. It captures HID keyboard reports and trackpad events on macOS, encrypts them, and streams them over a **TCP socket** to a Windows receiver that injects them as native keystrokes and mouse events.

**Use cases:** dual-machine setups, KVM-free workflows, controlling a Windows desktop from a MacBook sitting next to it.

> v2 replaces the Bluetooth RFCOMM transport with Wi-Fi TCP for dramatically improved reliability and latency.

## Architecture

```
┌─────────────────────────────┐           Wi-Fi / LAN TCP            ┌─────────────────────────────┐
│        Mac (sender)         │ ◄──────────────────────────────────► │      Windows (receiver)      │
│                             │    encrypted packets (PyNaCl)         │                             │
│  ┌───────────┐ ┌──────────┐ │    [type][seqno][ciphertext]         │  ┌───────────┐ ┌──────────┐ │
│  │ HID Reader│ │ Trackpad │ │                                       │  │Key Inject │ │Mouse Inj │ │
│  │  (hidapi) │ │(CGEvent) │ │                                       │  │(SendInput)│ │(SendInput)│ │
│  └─────┬─────┘ └────┬─────┘ │                                       │  └─────▲─────┘ └────▲─────┘ │
│        │             │       │                                       │        │             │       │
│        ▼             ▼       │                                       │        │             │       │
│  ┌─────────────────────────┐ │                                       │  ┌─────────────────────────┐ │
│  │   Encrypt → Pack → Send │ │ ────────────────────────────────────► │  │ Recv → Unpack → Decrypt │ │
│  └─────────────────────────┘ │                                       │  └─────────────────────────┘ │
│                             │                                       │                             │
│  ┌──────────┐ ┌───────────┐ │                                       │  ┌──────────┐ ┌───────────┐ │
│  │ Menubar  │ │  Hotkey   │ │                                       │  │Sys. Tray │ │Rate Limit │ │
│  │  (rumps) │ │  (⌃⌥K)   │ │                                       │  │(pystray) │ │(20 key/s) │ │
│  └──────────┘ └───────────┘ │                                       │  └──────────┘ └───────────┘ │
└─────────────────────────────┘                                       └─────────────────────────────┘
```

## Security Model

- **End-to-end encryption:** All packets encrypted with PyNaCl `crypto_secretstream` (XChaCha20-Poly1305)
- **Key exchange:** X25519 keypair per device, exchanged via QR code + fingerprint confirmation on first run
- **Key storage:** macOS Keychain / Windows Credential Manager — never in plaintext files
- **Network security:** No credentials on the wire; even an attacker on the same LAN cannot decrypt or inject packets without the shared key
- **Input validation:** HID keycode whitelist, 20 key/s rate limiter, sequence number validation

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
- Wi-Fi or Ethernet (same network as Windows machine)
- **Accessibility** permission (for hotkey + trackpad capture)
- **Input Monitoring** permission (for HID keyboard access)

### Windows (receiver)
- Windows 10+
- Python 3.11+
- Wi-Fi or Ethernet (same network as Mac)
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
py -m keybridgebt_win --host <mac-ip>
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
Press **⌃⌥K** (Ctrl+Option+K) to pause/resume forwarding. When paused, your keyboard and trackpad work on the Mac as normal.

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
listen_host: "0.0.0.0"      # bind address
listen_port: 9741            # TCP port
hotkey_keycode: 40           # K (macOS virtual keycode)
hotkey_modifiers: 0x0C0000   # Ctrl+Option
log_level: INFO
```

### `win-receiver/config.yaml`
```yaml
host: "192.168.1.5"          # Mac's IP address (null = prompt on startup)
port: 9741                   # TCP port (must match mac listen_port)
max_key_events_per_second: 20
log_level: INFO
```

Or pass the host on the command line: `py -m keybridgebt_win --host 192.168.1.5`

## Project Structure

```
keybridgeBT/
├── mac-sender/
│   ├── keybridgebt_mac/
│   │   ├── main.py              ← daemon orchestrator
│   │   ├── hid_reader.py        ← keyboard HID capture (hidapi)
│   │   ├── trackpad_reader.py   ← trackpad capture (CGEventTap)
│   │   ├── tcp_server.py        ← TCP server (stdlib socket)
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
│   │   ├── tcp_client.py        ← TCP client (stdlib socket)
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
│   ├── docs/
│   │   ├── ARCHITECTURE-v2.md   ← full technical specification (v2)
│   │   ├── TASKS-v2.md          ← ordered implementation task list (v2)
│   │   └── RUNNING.md           ← build/run instructions
├── LICENSE
└── README.md
```

## Troubleshooting

| Issue | Fix |
|---|---|
| "No Apple keyboard HID device found" | Check that Input Monitoring is granted in System Settings |
| "Failed to create event tap" | Grant **Accessibility** in System Settings → Privacy & Security |
| Windows cannot connect to Mac | Check both on same network; find Mac IP from menu bar icon; run `ping <mac-ip>` from Windows |
| Firewall blocks port 9741 | Allow port 9741 in macOS Firewall / Windows Defender Firewall |
| Keys stuck after disconnect | The receiver auto-releases all keys on disconnect. If stuck, restart the receiver. |
| Decryption failures | Re-run setup on both sides to regenerate keys |

## License

MIT — see [LICENSE](LICENSE).
