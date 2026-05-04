#!/bin/bash
# ═══════════════════════════════════════════════════════════════
#  LinuxPhone — Installer
#  Bluetooth Phone Companion for Linux (GTK4 / Adwaita)
#  Requires: bluez, ofono, python3-gi, python3-dbus, python3-vobject
#
#  Usage:
#    git clone https://github.com/YOUR/LinuxPhone.git
#    cd LinuxPhone
#    bash install.sh
# ═══════════════════════════════════════════════════════════════

set -e

APP_NAME="LinuxPhone"
APP_ID="io.github.linuxphone"
REPO_DIR="$(cd "$(dirname "$0")" && pwd)"   # directory where install.sh lives
INSTALL_DIR="$HOME/.local/share/linuxphone"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
BOLD='\033[1m'
NC='\033[0m'

# ── Banner ──────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}╔═══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║  📱  LinuxPhone — Installer               ║${NC}"
echo -e "${BLUE}║      Bluetooth Phone Companion for Linux  ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════╝${NC}"
echo ""

# ── Helper functions ────────────────────────────────────────────
ok()   { echo -e "${GREEN}  ✓ $*${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $*${NC}"; }
err()  { echo -e "${RED}  ✗ $*${NC}"; }
step() { echo -e "\n${BOLD}${YELLOW}[$1] $2${NC}"; }

# ── Step 1: Python 3 ────────────────────────────────────────────
step "1/6" "Checking Python 3..."
if ! command -v python3 &>/dev/null; then
    err "Python 3 not found."
    echo "  Install: sudo apt install python3"
    exit 1
fi
PYVER=$(python3 --version)
ok "$PYVER"

# ── Step 2: System packages ─────────────────────────────────────
step "2/6" "Checking Python / GTK dependencies..."

MISSING_PKG=()

check_py() {
    # $1 = python import snippet, $2 = apt package name(s)
    python3 -c "$1" &>/dev/null 2>&1 || MISSING_PKG+=("$2")
}

check_py "import gi; gi.require_version('Gtk','4.0'); from gi.repository import Gtk" \
    "python3-gi python3-gi-cairo gir1.2-gtk-4.0"

check_py "import gi; gi.require_version('Adw','1'); from gi.repository import Adw" \
    "gir1.2-adw-1"

check_py "import dbus" \
    "python3-dbus"

check_py "import vobject" \
    "python3-vobject"

# paplay is used for ringtone (optional)
if ! command -v paplay &>/dev/null; then
    warn "paplay not found — no ringtone on incoming calls"
    warn "Install: sudo apt install pulseaudio-utils"
fi

if [ ${#MISSING_PKG[@]} -gt 0 ]; then
    warn "Missing packages: ${MISSING_PKG[*]}"
    echo "  Installing (sudo required)..."
    sudo apt-get update -qq
    INSTALL_FAILED=()
    for pkg in "${MISSING_PKG[@]}"; do
        sudo apt-get install -y $pkg &>/dev/null && ok "Installed: $pkg" || {
            err "Failed: $pkg"
            INSTALL_FAILED+=("$pkg")
        }
    done
    if [ ${#INSTALL_FAILED[@]} -gt 0 ]; then
        err "Some packages failed — install manually:"
        echo "    sudo apt install ${INSTALL_FAILED[*]}"
        exit 1
    fi
else
    ok "All Python/GTK dependencies present"
fi

# ── Step 3: Bluetooth services ──────────────────────────────────
step "3/6" "Checking Bluetooth services (bluez + obexd + ofono)..."

# bluez — required
if command -v bluetoothctl &>/dev/null; then
    ok "bluez found"
else
    warn "bluez not found — install: sudo apt install bluez"
fi

# bluetooth service
if systemctl is-active --quiet bluetooth 2>/dev/null; then
    ok "bluetooth service running"
else
    warn "bluetooth service not running"
    echo "    Start:   sudo systemctl start bluetooth"
    echo "    Enable:  sudo systemctl enable bluetooth"
fi

# obexd — needed for PBAP (contacts / call history sync)
if systemctl is-active --quiet obex 2>/dev/null || pgrep -x obexd &>/dev/null; then
    ok "obexd running (contacts sync will work)"
else
    warn "obexd not detected — trying to start..."
    /usr/lib/bluetooth/obexd --root "$HOME" --port 44 &>/dev/null &
    sleep 1
    pgrep -x obexd &>/dev/null && ok "obexd started" || {
        warn "obexd not auto-started (contacts sync may not work)"
        echo "    Install: sudo apt install bluez-obexd"
    }
fi

# oFono — required for calling, battery, signal
if command -v ofonod &>/dev/null || systemctl is-active --quiet ofono 2>/dev/null; then
    ok "oFono found (calling + battery level supported)"
else
    warn "oFono not found — installing..."
    sudo apt-get install -y ofono &>/dev/null && {
        ok "oFono installed"
        warn "Enable oFono: sudo systemctl enable --now ofono"
    } || {
        warn "oFono install failed — calling/battery won't work, contacts will still sync"
        echo "    Try: sudo apt install ofono"
    }
fi

# oFono service
if systemctl is-active --quiet ofono 2>/dev/null; then
    ok "oFono service running"
else
    warn "oFono service not running"
    echo "    Start:  sudo systemctl start ofono"
    echo "    Enable: sudo systemctl enable ofono"
fi

# ── Step 4: Install app files ───────────────────────────────────
step "4/6" "Installing LinuxPhone to $INSTALL_DIR..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$ICON_DIR"

# Verify the repo has the expected structure
if [ ! -f "$REPO_DIR/main.py" ] || [ ! -d "$REPO_DIR/src" ]; then
    err "Could not find main.py or src/ in $REPO_DIR"
    err "Run this script from inside the LinuxPhone git repo directory."
    exit 1
fi

# Copy entire src/ and main.py
rm -rf "$INSTALL_DIR/src"
cp -r "$REPO_DIR/src"  "$INSTALL_DIR/src"
cp    "$REPO_DIR/main.py" "$INSTALL_DIR/main.py"
ok "App files copied to $INSTALL_DIR"

# Launcher script in ~/.local/bin
cat > "$BIN_DIR/linuxphone" << LAUNCHER
#!/bin/bash
# LinuxPhone launcher — auto-generated by install.sh
cd "\$HOME/.local/share/linuxphone"
exec python3 main.py "\$@"
LAUNCHER
chmod +x "$BIN_DIR/linuxphone"
ok "Launcher created: $BIN_DIR/linuxphone"

# ── Step 5: Desktop entry + icon ───────────────────────────────
step "5/6" "Creating desktop entry and icon..."

# SVG icon
mkdir -p "$ICON_DIR"
cat > "$ICON_DIR/${APP_ID}.svg" << 'SVG'
<?xml version="1.0" encoding="UTF-8"?>
<svg width="128" height="128" viewBox="0 0 128 128"
     xmlns="http://www.w3.org/2000/svg">
  <rect width="128" height="128" rx="28" fill="#3584e4"/>
  <rect x="44" y="16" width="40" height="96" rx="8" fill="white" opacity="0.15"/>
  <rect x="50" y="22" width="28" height="56" rx="4" fill="white" opacity="0.9"/>
  <circle cx="64" cy="94" r="5" fill="white" opacity="0.8"/>
  <!-- Phone handset icon -->
  <path d="M52 36 Q52 28 60 28 L68 28 Q76 28 76 36 L76 92 Q76 100 68 100 L60 100 Q52 100 52 92 Z"
        fill="none" stroke="white" stroke-width="0" opacity="0"/>
</svg>
SVG
ok "Icon created"

# .desktop file
cat > "$DESKTOP_DIR/${APP_ID}.desktop" << DESKTOP
[Desktop Entry]
Version=1.0
Type=Application
Name=LinuxPhone
GenericName=Bluetooth Phone Companion
Comment=Sync contacts, calls and media with your phone via Bluetooth
Exec=$BIN_DIR/linuxphone
Icon=${APP_ID}
Terminal=false
Categories=Utility;GTK;Network;
Keywords=bluetooth;phone;contacts;calls;sms;pbap;hfp;
StartupWMClass=linuxphone
DESKTOP

update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
gtk-update-icon-cache -f -t "$ICON_DIR/../.." 2>/dev/null || true
ok "Desktop entry created"

# ── Step 6: PATH check ──────────────────────────────────────────
step "6/6" "Checking PATH..."

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    warn "$BIN_DIR is not in your PATH"
    echo ""
    echo "  Add to your shell config (~/.bashrc or ~/.zshrc):"
    echo -e "  ${BLUE}export PATH=\"\$HOME/.local/bin:\$PATH\"${NC}"
    echo ""
    echo "  Then reload: source ~/.bashrc"
    echo ""
    # Auto-add to .bashrc if user agrees
    read -r -p "  Auto-add to ~/.bashrc now? [Y/n] " REPLY
    REPLY="${REPLY:-Y}"
    if [[ "$REPLY" =~ ^[Yy]$ ]]; then
        echo '' >> "$HOME/.bashrc"
        echo '# LinuxPhone — added by install.sh' >> "$HOME/.bashrc"
        echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$HOME/.bashrc"
        ok "Added to ~/.bashrc — restart terminal or run: source ~/.bashrc"
    fi
else
    ok "PATH already contains $BIN_DIR"
fi

# ── Done ────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║  ✓  LinuxPhone installed successfully!    ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""
echo "  Launch from terminal:  linuxphone"
echo "  Launch from app menu:  Search 'LinuxPhone'"
echo ""
echo -e "${YELLOW}First-time setup tips:${NC}"
echo "  1. Pair your phone: bluetoothctl → pair <MAC>"
echo "  2. Keep phone screen on + unlocked when syncing"
echo "  3. Accept any 'Allow contact sharing' prompts on phone"
echo "  4. For calling: sudo systemctl enable --now ofono"
echo ""
echo -e "${BLUE}Features:${NC}"
echo "  📞 Incoming / outgoing calls (answer from PC)"
echo "  👥 Contact sync via PBAP"
echo "  📋 Recent call history (color-coded)"
echo "  🎵 Media control (Play/Pause/Next via AVRCP)"
echo "  🔋 Battery level (approximate via Bluetooth HFP)"
echo "  💬 SMS receive (if phone supports oFono SMS)"
echo ""
