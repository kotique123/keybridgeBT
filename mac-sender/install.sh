#!/bin/bash
# keybridgeBT mac-sender install script
# Creates a dedicated user, installs files, and loads the launchd service.
set -euo pipefail

INSTALL_DIR="/opt/keybridgebt/mac-sender"
LOG_DIR="/var/log/keybridgebt"
SERVICE_USER="_keybridgebt"
PLIST_SRC="com.keybridgebt.sender.plist"
PLIST_DST="/Library/LaunchDaemons/com.keybridgebt.sender.plist"

echo "=== keybridgeBT mac-sender installer ==="

# Must be root
if [[ $EUID -ne 0 ]]; then
    echo "Error: run with sudo"
    exit 1
fi

# 1. Create service user (if needed)
if ! dscl . -read /Users/$SERVICE_USER &>/dev/null; then
    echo "Creating user $SERVICE_USER…"
    # Find a free UID in the daemon range (400–499)
    NEXT_UID=400
    while dscl . -list /Users UniqueID | awk '{print $2}' | grep -q "^${NEXT_UID}$"; do
        NEXT_UID=$((NEXT_UID + 1))
    done
    dscl . -create /Users/$SERVICE_USER
    dscl . -create /Users/$SERVICE_USER UniqueID "$NEXT_UID"
    dscl . -create /Users/$SERVICE_USER PrimaryGroupID 20
    dscl . -create /Users/$SERVICE_USER UserShell /usr/bin/false
    dscl . -create /Users/$SERVICE_USER RealName "keybridgeBT Service"
    dscl . -create /Users/$SERVICE_USER NFSHomeDirectory /var/empty
    echo "  Created UID=$NEXT_UID"
else
    echo "User $SERVICE_USER already exists"
fi

# 2. Install files
echo "Installing to $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR"
cp -R keybridgebt_mac "$INSTALL_DIR/"
cp config.yaml "$INSTALL_DIR/" 2>/dev/null || true
cp requirements.txt "$INSTALL_DIR/"
chown -R $SERVICE_USER:staff "$INSTALL_DIR"

# 3. Install Python dependencies
echo "Installing Python dependencies…"
pip3 install --quiet -r "$INSTALL_DIR/requirements.txt"

# 4. Create log directory
echo "Setting up log directory…"
mkdir -p "$LOG_DIR"
chown $SERVICE_USER:staff "$LOG_DIR"

# 5. Install and load launchd plist
echo "Installing launchd service…"
cp "$PLIST_SRC" "$PLIST_DST"
chown root:wheel "$PLIST_DST"
chmod 644 "$PLIST_DST"

# Unload if already loaded
launchctl unload "$PLIST_DST" 2>/dev/null || true
launchctl load "$PLIST_DST"

echo ""
echo "=== Installation complete ==="
echo "Service loaded. Check status with:"
echo "  sudo launchctl list | grep keybridgebt"
echo "  tail -f $LOG_DIR/sender.log"
echo ""
echo "NOTE: You must grant Accessibility and Input Monitoring permissions"
echo "to the Python binary used by this service."
