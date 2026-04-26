#!/bin/bash
# LinuxPhone — Uninstaller

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

echo -e "${YELLOW}Removing LinuxPhone...${NC}"
rm -rf "$HOME/.local/share/linuxphone"
rm -f  "$HOME/.local/bin/linuxphone"
rm -f  "$HOME/.local/share/applications/io.github.linuxphone.desktop"
rm -f  "$HOME/.local/share/icons/hicolor/scalable/apps/io.github.linuxphone.svg"
update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
echo -e "${GREEN}✓ LinuxPhone removed.${NC}"
