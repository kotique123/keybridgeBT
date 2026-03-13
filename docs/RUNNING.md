# keybridgeBT v2 — Running Guide

This guide covers every step from a fresh machine to a working forwarding session.
v2 uses **Wi-Fi TCP** instead of Bluetooth, giving you a rock-solid LAN connection.

---

## Table of Contents

1. [System requirements](#1-system-requirements)
2. [Installation — Mac sender](#2-installation--mac-sender)
3. [Installation — Windows receiver](#3-installation--windows-receiver)
4. [First-run key exchange](#4-first-run-key-exchange)
5. [Running the software](#5-running-the-software)
6. [macOS service management](#6-macos-service-management)
7. [Hotkey and tray controls](#7-hotkey-and-tray-controls)
8. [Configuration reference](#8-configuration-reference)
9. [Viewing logs](#9-viewing-logs)
10. [Running the test suite](#10-running-the-test-suite)
11. [Troubleshooting](#11-troubleshooting)

---

## 1. System Requirements

| | Mac (sender) | Windows (receiver) |
|---|---|---|
| OS | macOS 13.0+ (Ventura) | Windows 10 / 11 |
| Python | 3.11 or later | 3.11 or later |
| Network | Wi-Fi or Ethernet (same LAN) | Wi-Fi or Ethernet (same LAN) |
| Bluetooth | Not required | Not required |
| Permissions | Accessibility + Input Monitoring | None (no admin required) |

**Check your Python version:**

```bash
# macOS
python3 --version

# Windows (PowerShell)
py --version
```

---

## 2. Installation — Mac Sender

### Option A: macOS App (recommended)

Build a native `.app` bundle that triggers macOS permission prompts automatically:

```bash
bash mac-sender/build_app.sh
```

This creates `builds/dist/keybridgeBT.app`. To install:

```bash
cp -R builds/dist/keybridgeBT.app /Applications/
open /Applications/keybridgeBT.app
```

On first launch, macOS will show native prompts for:
- **Input Monitoring** — to capture keyboard HID reports
- **Accessibility** — to capture trackpad events and the global hotkey

Grant both. Permissions stick to the app permanently — no Terminal configuration needed.

> The `.app` bundles a Python virtual environment with all dependencies, so the target Mac only needs Python 3.11+ installed system-wide.

### Option B: Direct (development / testing)

```bash
cd mac-sender
pip install -e .
```

This installs `keybridgebt_mac` into your active Python environment so `python3 -m keybridgebt_mac` works from any directory.

If you prefer an isolated virtual environment:

```bash
cd mac-sender
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

> **Permissions note:** When running via Terminal, you must manually add Terminal.app (or iTerm2) to both **Input Monitoring** and **Accessibility** in System Settings → Privacy & Security.

### Option C: System service

Installs to `/opt/keybridgebt/`, runs as the dedicated low-privilege user `_keybridgebt`, restarts automatically on crash.

```bash
cd mac-sender
chmod +x install.sh
sudo ./install.sh
```

The installer:
- Creates the `_keybridgebt` system user
- Copies files to `/opt/keybridgebt/mac-sender/`
- Creates a Python virtual environment there
- Creates `/var/log/keybridgebt/` for logs
- Installs and loads `com.keybridgebt.sender.plist` into `/Library/LaunchDaemons/`

### Option D: From a build archive

```bash
tar xzf builds/dist/keybridgebt-mac-<version>.tar.gz
cd keybridgebt-mac-<version>
./run.sh                          # run directly
# or
sudo ./install.sh                 # install as a service
```

---

## 3. Installation — Windows Receiver

### Option A: Direct

```powershell
cd win-receiver
pip install -e .
```

This installs `keybridgebt_win` into your active Python environment so `py -m keybridgebt_win` works from any directory.

If you prefer an isolated virtual environment:

```powershell
cd win-receiver
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e .
```

### Option B: From a build archive

1. Copy `keybridgebt-win-<version>.zip` to the Windows machine
2. Extract it (right-click → Extract All, or `Expand-Archive` in PowerShell)
3. Run `run.bat` or `run.ps1`

No admin privileges are required. `SendInput` works in the current user session.

---

## 4. First-Run Key Exchange

Both sides must complete a one-time setup to exchange public keys. After this, future launches connect automatically.

**Run both sides at the same time and follow the prompts:**

### Step 1 — Start the Mac sender

```bash
# Direct:
python3 -m keybridgebt_mac
# or via run.sh:
./mac-sender/run.sh
```

The setup wizard launches automatically when no keys are found in the Keychain.

### Step 2 — Mac displays a QR code

A window appears showing the Mac's **public key** as a QR code. Keep it visible.

### Step 3 — Start the Windows receiver

```powershell
py -m keybridgebt_win
```

### Step 4 — Windows pastes the Mac's public key

When prompted, either:
- Scan the QR code with your phone and paste the decoded text, or
- Copy the base64 string from the Mac terminal and paste it on Windows.

### Step 5 — Windows shows its public key

The Windows side displays its own public key. Copy it.

### Step 6 — Mac accepts the Windows public key

Paste the Windows public key into the Mac terminal prompt.

### Step 7 — Confirm the fingerprint

Both sides display a **6-digit fingerprint** derived from the shared secret.

> **Security critical:** The fingerprints must match exactly on both screens.  
> If they differ, abort setup — a man-in-the-middle may be present.

### Step 8 — Keys stored

Both sides store their keys securely:
- Mac → **macOS Keychain** (service `com.keybridgebt.sender`)
- Windows → **Windows Credential Manager** (service `com.keybridgebt.receiver`)

Future launches skip setup and connect automatically.

---

## 5. Running the Software

### Mac — foreground (dev/test)

```bash
# From repo root
python3 -m keybridgebt_mac

# From mac-sender directory with venv active
python3 -m keybridgebt_mac

# From a build package
./run.sh
```

The daemon starts, binds to `0.0.0.0:9741`, and waits for the Windows receiver to connect.
The log shows your local IP address:
```
INFO: Listening on 0.0.0.0:9741 (local IP: 192.168.1.5)
```
Note this IP — you need it to configure the Windows receiver.

### Windows — foreground

```powershell
# With IP from the Mac log output:
py -m keybridgebt_win --host 192.168.1.5

# Or set host in config.yaml first, then just:
py -m keybridgebt_win

# Run from a build package:
.\run.bat
```

If `host` is null in `config.yaml` and no `--host` flag is provided, the receiver will prompt:
```
Enter Mac IP address (e.g. 192.168.1.10): _
```

### Normal startup sequence

```
Mac sender                         Windows receiver
──────────────────────────────     ──────────────────────────────
Load config                        Load config
Check keychain → keys found        Check credentials → keys found
Bind TCP 0.0.0.0:9741              Start TCP client (connect to Mac IP)
Start HID reader                   (reconnecting…)
Start trackpad reader
Start hotkey monitor
Load menubar tray       ←──────→   (connected)
Status: ⏳ Waiting
                        ←──connect
Status: 🔗 Connected               Status: 🔗 Connected
Status: 🟢 Forwarding              (ready)
```

---

## 6. macOS Service Management

When installed as a launchd service, use these commands:

```bash
# Check service status
sudo launchctl list | grep keybridgebt

# View last exit code and PID
sudo launchctl print system/com.keybridgebt.sender

# Stop the service
sudo launchctl bootout system /Library/LaunchDaemons/com.keybridgebt.sender.plist

# Start the service
sudo launchctl bootstrap system /Library/LaunchDaemons/com.keybridgebt.sender.plist

# Restart
sudo launchctl kickstart -k system/com.keybridgebt.sender

# Uninstall completely
sudo launchctl bootout system /Library/LaunchDaemons/com.keybridgebt.sender.plist
sudo rm /Library/LaunchDaemons/com.keybridgebt.sender.plist
sudo rm -rf /opt/keybridgebt/
sudo dscl . -delete /Users/_keybridgebt
```

---

## 7. Hotkey and Tray Controls

### Mac menu bar

Click the **⌨️** icon in the menu bar:

| Item | Description |
|---|---|
| 🟢 Forwarding / ⏸ Paused | Current forwarding state |
| 🔗 Connected / ⏳ Waiting | Bluetooth connection state |
| Toggle Forwarding | Pause or resume |
| Hotkey: ⌘⇧F12 | Reminder |
| Quit | Stop the daemon |

### Global hotkey

**⌘ + ⇧ + F12** (Cmd + Shift + F12) — toggles forwarding on/off from any application.

When paused, your Mac keyboard and trackpad work normally on the Mac. Press again to resume forwarding.

> Requires **Accessibility** permission. macOS will prompt for it on first use.

### Windows system tray

Right-click the tray icon to see the connection status and quit.

---

## 8. Configuration Reference

### `mac-sender/config.yaml`

```yaml
service_name: keybridgeBT          # RFCOMM service name advertised via SDP
hotkey_keycode: 111                 # macOS virtual keycode — 111 = F12
hotkey_modifiers: 0x180000          # Cmd (0x100000) + Shift (0x80000)
log_level: INFO                     # DEBUG | INFO | WARNING | ERROR
```

To change the hotkey, look up the macOS virtual keycode for your preferred key and replace `hotkey_keycode`. The modifier mask is a bitmask of `CGEventFlags` values.

### `win-receiver/config.yaml`

```yaml
com_port: null                      # null = auto-detect Bluetooth COM port
                                    # explicit: "COM5"
max_key_events_per_second: 20       # rate limiter ceiling (keyboard only)
log_level: INFO
```

If auto-detection fails to find the right port, set `com_port` explicitly. In Device Manager, look under **Ports (COM & LPT)** for the Bluetooth serial device.

---

## 9. Viewing Logs

### macOS service logs

```bash
# Live tail
tail -f /var/log/keybridgebt/sender.log

# Last 50 lines
tail -50 /var/log/keybridgebt/sender.log

# Filter for errors only
grep -E "ERROR|WARNING" /var/log/keybridgebt/sender.log
```

### macOS foreground run

Logs print to stdout/stderr directly in the terminal.

### Windows

Logs print to the console where you launched `py -m keybridgebt_win`. To capture to a file:

```powershell
py -m keybridgebt_win 2>&1 | Tee-Object -FilePath keybridgebt.log
```

---

## 10. Running the Test Suite

The test suite runs on any platform and does **not** require Bluetooth hardware.

```bash
# From repo root — install test dependencies once
pip install -r requirements-test.txt

# Run all tests
python3 -m pytest tests/ -v

# Run only the security validation tests
python3 -m pytest tests/test_session_security.py -v

# Run all tests with detailed failure output
python3 -m pytest tests/ -v --tb=long
```

Expected result: **62 tests, all passing.**

### Test file overview

| File | What it covers |
|---|---|
| `tests/test_protocol_crypto.py` | Protocol round-trip, crypto mechanics, seqno validation, rate limiter, keycode whitelist |
| `tests/test_pipeline_reconnect.py` | Full Mac/Win pipelines, reconnection state reset, hotkey toggle, keychain round-trip |
| `tests/test_session_security.py` | Session isolation, authentication, nonce uniqueness, MITM detection, wrong-key rejection, stream ordering, replay prevention, header uniqueness |
| `tests/test_tcp_transport.py` | TCPServer/TCPClient connect/disconnect, data delivery, auto-reconnect, full encrypted pipeline, concurrent sends, clean shutdown |

---

## 11. Troubleshooting

### "No keyboard HID device found"

The HID reader cannot open the Apple keyboard.

- Go to **System Settings → Privacy & Security → Input Monitoring**
- Add your terminal app (Terminal.app, iTerm2) or the `_keybridgebt` user's process
- Re-run the daemon

### "CGEventTap creation failed" or trackpad not forwarding

The trackpad capture requires Accessibility permission.

- Go to **System Settings → Privacy & Security → Accessibility**
- Add your terminal app or the service process
- Re-run; the tap should register automatically

### Windows cannot connect to the Mac

1. Confirm both machines are on the **same network** (same Wi-Fi router or Ethernet switch)
2. Find the Mac's IP: check the menu-bar icon ("Listening on …") or run `ipconfig getifaddr en0` in Terminal
3. Test connectivity: `ping <mac-ip>` from a Windows Command Prompt
4. If ping fails, check the Mac **firewall**: System Settings → Network → Firewall → allow incoming connections on port 9741
5. Set `host: "<mac-ip>"` in `win-receiver/config.yaml` (or pass `--host <mac-ip>` on the command line)

### Firewall blocking port 9741

**macOS:** System Settings → Network → Firewall → Firewall Options → add the Python executable or set it to allow all incoming connections.

**Windows:** If Windows Defender Firewall blocks the connection, add an outbound rule for port 9741 in Windows Defender Firewall.

### Connection drops / keeps reconnecting

- Check Wi-Fi signal strength; consider using Ethernet for lower latency
- If your router has "AP isolation" or "client isolation" enabled, hosts on the same network can't talk to each other — disable it
- Check `log_level: DEBUG` in both config files for per-packet timing

### Keys were lost / need to redo setup

Delete the stored keys and run setup again:

```bash
# Mac — remove from Keychain
python3 -c "
import keyring
svc = 'com.keybridgebt.sender'
for name in ['private_key', 'public_key', 'peer_public_key', 'shared_key']:
    keyring.delete_password(svc, name)
print('Keychain entries removed')
"
```

```powershell
# Windows — remove from Credential Manager
python -c "
import keyring
svc = 'com.keybridgebt.receiver'
for name in ['private_key', 'public_key', 'peer_public_key', 'shared_key']:
    keyring.delete_password(svc, name)
print('Credential Manager entries removed')
"
```

Then restart both daemons — the setup wizard will run automatically.

### Forwarding is active but keystrokes arrive on Windows with delay

- Increase `max_key_events_per_second` in `win-receiver/config.yaml` if typing is being rate-limited
- A high-latency Wi-Fi link can add delay — check signal quality or use Ethernet
- Check `log_level: DEBUG` in both config files to see per-packet timing

### Service crashed / greyed out in menu bar

```bash
# Check exit code
sudo launchctl print system/com.keybridgebt.sender

# Check log for the last error
tail -30 /var/log/keybridgebt/sender.log

# Restart
sudo launchctl kickstart -k system/com.keybridgebt.sender
```

`KeepAlive: true` in the plist will restart the service automatically, but examining the log first helps diagnose crash loops.
