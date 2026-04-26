#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  LinuxPhone — Installer
#  Bluetooth Phone Companion for Linux (GTK4/Adwaita)
#  Usage: bash install.sh
# ═══════════════════════════════════════════════════════════════

set -e

APP_NAME="LinuxPhone"
APP_ID="io.github.linuxphone"
INSTALL_DIR="$HOME/.local/share/linuxphone"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m'

echo ""
echo -e "${BLUE}╔══════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   LinuxPhone — Installer             ║${NC}"
echo -e "${BLUE}║   Bluetooth Phone Companion for Linux ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════╝${NC}"
echo ""

# ── Step 1: Check Python 3 ─────────────────────────────────────
echo -e "${YELLOW}[1/5] Checking Python 3...${NC}"
if ! command -v python3 &>/dev/null; then
    echo -e "${RED}✗ Python 3 not found. Install it with:${NC}"
    echo "  sudo apt install python3"
    exit 1
fi
PYVER=$(python3 --version)
echo -e "${GREEN}✓ $PYVER${NC}"

# ── Step 2: Check/Install system packages ──────────────────────
echo -e "${YELLOW}[2/5] Checking system dependencies...${NC}"

MISSING_PKG=()

check_pkg() {
    python3 -c "$1" &>/dev/null || MISSING_PKG+=("$2")
}

check_pkg "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk" \
    "python3-gi python3-gi-cairo gir1.2-gtk-4.0"

check_pkg "import gi; gi.require_version('Adw','1'); from gi.repository import Adw" \
    "gir1.2-adw-1"

check_pkg "import dbus" \
    "python3-dbus"

check_pkg "import vobject" \
    "python3-vobject"

if [ ${#MISSING_PKG[@]} -gt 0 ]; then
    echo -e "${YELLOW}Installing missing packages (sudo required)...${NC}"
    echo "  Packages: ${MISSING_PKG[*]}"
    sudo apt-get update -qq
    for pkg in "${MISSING_PKG[@]}"; do
        sudo apt-get install -y $pkg || {
            echo -e "${RED}✗ Failed to install: $pkg${NC}"
            echo "  Try manually: sudo apt install $pkg"
        }
    done
else
    echo -e "${GREEN}✓ All system dependencies present${NC}"
fi

# ── Step 3: Check BlueZ, obexd, oFono (optional) ──────────────
echo -e "${YELLOW}[3/5] Checking Bluetooth services...${NC}"

# BlueZ — zaruri hai
if ! command -v bluetoothctl &>/dev/null; then
    echo -e "${YELLOW}  ⚠ bluez not found — install bluez:${NC}"
    echo "    sudo apt install bluez"
else
    echo -e "${GREEN}  ✓ bluez OK${NC}"
fi

# bluetooth service running?
if systemctl is-active --quiet bluetooth 2>/dev/null; then
    echo -e "${GREEN}  ✓ bluetooth service running${NC}"
else
    echo -e "${YELLOW}  ⚠ bluetooth service not running${NC}"
    echo "    Start with: sudo systemctl start bluetooth"
fi

# oFono — optional, sirf calling ke liye chahiye
if command -v ofonod &>/dev/null || systemctl is-active --quiet ofono 2>/dev/null; then
    echo -e "${GREEN}  ✓ oFono found (calling supported)${NC}"
else
    echo -e "${YELLOW}  ⚠ oFono not found — installing...${NC}"
    sudo apt-get install -y ofono && \
        echo -e "${GREEN}  ✓ oFono installed (calling supported)${NC}" || \
        echo -e "${YELLOW}  ⚠ oFono install failed — calling won't work, contacts will still sync${NC}"
fi

# ── Step 4: Install app files ──────────────────────────────────
echo -e "${YELLOW}[4/5] Installing LinuxPhone...${NC}"

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

# Copy main script
cp "$(dirname "$0")/linuxphone_gui.py" "$INSTALL_DIR/linuxphone_gui.py"
chmod +x "$INSTALL_DIR/linuxphone_gui.py"

# Create launcher script in ~/.local/bin
cat > "$BIN_DIR/linuxphone" << 'LAUNCHER'
#!/bin/bash
exec python3 "$HOME/.local/share/linuxphone/linuxphone_gui.py" "$@"
LAUNCHER
chmod +x "$BIN_DIR/linuxphone"

# Create SVG icon
cat > "$ICON_DIR/${APP_ID}.svg" << 'SVG'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="128" height="128" viewBox="0 0 128 128"
     xmlns="http://www.w3.org/2000/svg">
  <rect width="128" height="128" rx="28" fill="#3584e4"/>
  <text x="64" y="90" font-size="72" text-anchor="middle"
        font-family="sans-serif">📱</text>
</svg>
SVG

# Create .desktop entry
cat > "$DESKTOP_DIR/${APP_ID}.desktop" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=LinuxPhone
GenericName=Bluetooth Phone Companion
Comment=Sync contacts and calls from your phone via Bluetooth
Exec=$BIN_DIR/linuxphone
Icon=${APP_ID}
Terminal=false
Categories=Utility;GTK;Network;
Keywords=bluetooth;phone;contacts;calls;pbap;
StartupWMClass=linuxphone
DESKTOP

# Update icon/desktop caches silently
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
gtk-update-icon-cache -f -t "$ICON_DIR/../.." 2>/dev/null || true

echo -e "${GREEN}✓ Installed to $INSTALL_DIR${NC}"

# ── Step 5: PATH check ─────────────────────────────────────────
echo -e "${YELLOW}[5/5] Checking PATH...${NC}"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo -e "${YELLOW}  ⚠ $BIN_DIR is not in your PATH.${NC}"
    echo "  Add this to your ~/.bashrc or ~/.zshrc:"
    echo -e "  ${BLUE}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
    echo "  Then run: source ~/.bashrc"
else
    echo -e "${GREEN}✓ PATH OK${NC}"
fi

# ── Done ───────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   ✓ LinuxPhone installed!            ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════╝${NC}"
echo ""
echo "  Run from terminal : linuxphone"
echo "  Run from app menu : Search 'LinuxPhone'"
echo ""
echo -e "${YELLOW}Bluetooth tips:${NC}"
echo "  • Pair your phone first via: bluetoothctl"
echo "  • Keep phone screen unlocked during sync"
echo "  • Allow contact sharing when phone prompts"
echo ""
