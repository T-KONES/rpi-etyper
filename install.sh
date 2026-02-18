#!/bin/bash
# etyper installer - sets up dependencies and systemd service

set -e

echo "=== etyper installer ==="

if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Please run as root (sudo bash install.sh)"
    exit 1
fi

# Install system dependencies
echo "Installing dependencies..."
apt-get update -qq
apt-get install -y \
    python3-spidev \
    python3-libgpiod \
    python3-pil \
    python3-evdev \
    python3-dbus \
    python3-gi \
    dnsmasq \
    openssl

# Disable the system dnsmasq service -- etyper starts its own instance
# only during file transfer, and the system service would conflict on port 53
if systemctl is-enabled dnsmasq &>/dev/null; then
    echo "Disabling system dnsmasq service (etyper manages its own instance)..."
    systemctl disable --now dnsmasq
fi

# Create documents directory
DOCS_DIR="$HOME/etyper_docs"
mkdir -p "$DOCS_DIR"
echo "Documents directory: $DOCS_DIR"

# Install systemd service (optional)
read -p "Install as boot service (auto-start on boot)? [y/N] " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
    SERVICE_FILE="/etc/systemd/system/etyper.service"

    # Update service file with correct path
    sed "s|__INSTALL_DIR__|$SCRIPT_DIR|g" "$SCRIPT_DIR/etyper.service" > "$SERVICE_FILE"

    systemctl daemon-reload
    systemctl enable etyper

    read -p "Start etyper now? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        systemctl start etyper
        echo "etyper started."
        echo "  Logs:    journalctl -u etyper -f"
        echo "  Stop:    sudo systemctl stop etyper"
    else
        echo "Service installed. Will start on next boot."
        echo "  Start:   sudo systemctl start etyper"
        echo "  Logs:    journalctl -u etyper -f"
    fi
else
    echo "Skipped service install."
    echo "Run manually: sudo python3 typewriter.py"
fi

echo
echo "=== Setup complete ==="
echo "Documents saved to: $DOCS_DIR"
