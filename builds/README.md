# keybridgeBT v2 — Build Guide

This directory contains scripts that produce **self-contained distributable packages** for each platform. The packages include a Python virtual environment with all dependencies pre-installed, so the target machine only needs Python 3.11+ — no internet access required at runtime.

> v2 uses Wi-Fi TCP transport. No Bluetooth pairing or COM port setup required.

---

## Directory Layout

```
builds/
├── README.md          ← this file
├── mac/
│   └── build.sh       ← macOS build script  (produces builds/dist/keybridgebt-mac-<ver>.tar.gz)
└── win/
    └── build.ps1      ← Windows build script (produces builds/dist/keybridgebt-win-<ver>.zip)
```

Build artifacts are written to `builds/dist/` (git-ignored).

---

## Prerequisites

### Both platforms
| Requirement | Version |
|---|---|
| Python | 3.11 or later |
| pip | bundled with Python |
| internet access | only needed at build time to pull packages |

### macOS build machine
- Xcode Command Line Tools: `xcode-select --install`
- Homebrew recommended but not required

### Windows build machine
- PowerShell 5.1+ (built in on Windows 10/11)
- Python from python.org (not the Microsoft Store stub)

---

## Building the Mac Sender

Run from the **repository root**:

```bash
bash builds/mac/build.sh
```

What it does:
1. Creates `builds/dist/keybridgebt-mac-<version>/`
2. Copies `mac-sender/keybridgebt_mac/` and `mac-sender/config.yaml`
3. Creates an isolated Python venv at `dist/keybridgebt-mac-<version>/.venv/`
4. Installs all dependencies from `mac-sender/requirements.txt`
5. Writes a `run.sh` launcher inside the package
6. Archives everything to `builds/dist/keybridgebt-mac-<version>.tar.gz`

Deploy to the target Mac:
```bash
tar xzf builds/dist/keybridgebt-mac-<version>.tar.gz -C /opt/keybridgebt/
cd /opt/keybridgebt/keybridgebt-mac-<version>
./run.sh            # run directly
# OR
sudo ./install.sh   # install as a launchd service (recommended for daily use)
```

---

## Building the Windows Receiver

Run from the **repository root** in a PowerShell terminal:

```powershell
.\builds\win\build.ps1
```

What it does:
1. Creates `builds\dist\keybridgebt-win-<version>\`
2. Copies `win-receiver\keybridgebt_win\` and `win-receiver\config.yaml`
3. Creates an isolated Python venv
4. Installs all dependencies from `win-receiver\requirements.txt`
5. Writes a `run.bat` launcher inside the package
6. Archives everything to `builds\dist\keybridgebt-win-<version>.zip`

Deploy to the target Windows machine:
```powershell
Expand-Archive builds\dist\keybridgebt-win-<version>.zip -DestinationPath C:\keybridgebt\
# Run with the Mac's IP address:
C:\keybridgebt\keybridgebt-win-<version>\run.bat --host 192.168.1.5
# Or set host in config.yaml inside the package, then just:
C:\keybridgebt\keybridgebt-win-<version>\run.bat
```

> **Finding the Mac's IP:** After starting the mac-sender, its IP is shown in the menu-bar icon ("Listening on …") and in the startup log output.

---

## Verifying a Build

After building, run the test suite against the installed packages to confirm nothing is broken:

```bash
# From repo root
python3 -m pytest tests/ -v
```

The security validation suite (`tests/test_session_security.py`) specifically verifies cryptographic session properties without requiring any hardware.

---

## Version Numbering

The build scripts read the version from `VERSION` at the repo root (defaulting to `0.1.0` if absent). To set a version before building:

```bash
echo "1.0.0" > VERSION
bash builds/mac/build.sh
```

---

## dist/ Contents (git-ignored)

The `builds/dist/` directory is listed in `.gitignore`. Do not commit build artifacts — distribute the archives through GitHub Releases or a secure file transfer instead.

---

## Security Notes

- Build artifacts contain a complete Python environment; inspect with `pip list --path dist/.venv/lib/...` before deploying to production.
- Never bundle the shared key or `config.yaml` with credentials into a released artifact — keys are stored in the OS keychain on first run.
- Verify archive checksums (SHA-256) after transfer:
  ```bash
  shasum -a 256 builds/dist/keybridgebt-mac-*.tar.gz
  ```
