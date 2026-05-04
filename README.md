# 📱 LinuxPhone

**Bluetooth Phone Companion for Linux** — GTK4/Adwaita desktop app that connects to your Android phone via Bluetooth and gives you Phone Link-style features natively on Linux.

---

## ✨ Features

| Feature | Status |
|---|---|
| 📞 Answer / reject incoming calls from PC | ✅ |
| 🔔 Ringtone plays on PC when call comes in | ✅ |
| ☎️ Make outgoing calls from PC | ✅ |
| 👥 Contact sync (Bluetooth PBAP) | ✅ |
| 📋 Recent call history — color-coded Incoming/Outgoing/Missed | ✅ |
| 🎵 Media control: Play/Pause/Next/Prev (Bluetooth AVRCP) | ✅ |
| 🔋 Battery level display (approximate, Bluetooth HFP) | ✅ |
| 📶 Network carrier + signal | ✅ |
| 💬 SMS receive (requires oFono MessageManager) | ✅ |

---

## 📋 Requirements

- Linux with BlueZ Bluetooth stack
- Python 3.10+
- GTK4 + Libadwaita
- oFono (for calls, battery, signal)
- obexd / bluez-obexd (for contacts sync)

---

## 🚀 Installation

```bash
git clone https://github.com/VashuTheGreat/LinuxPhone.git
cd LinuxPhone
bash install.sh
```

The installer will:
1. Check Python 3 and GTK4/Adwaita libraries
2. Install missing `python3-gi`, `python3-dbus`, `python3-vobject` packages
3. Check and guide you through bluez, obexd, oFono setup
4. Install app to `~/.local/share/linuxphone/`
5. Create a `linuxphone` launcher in `~/.local/bin/`
6. Add a desktop entry (app menu shortcut)
7. Optionally add `~/.local/bin` to your PATH

After install:
```bash
linuxphone        # from terminal
# OR search "LinuxPhone" in your app menu
```

---

## 🔧 Manual Dependencies

```bash
# Ubuntu / Debian / Linux Mint
sudo apt install \
    python3-gi python3-gi-cairo \
    gir1.2-gtk-4.0 gir1.2-adw-1 \
    python3-dbus python3-vobject \
    bluez bluez-obexd ofono \
    pulseaudio-utils

# Enable services
sudo systemctl enable --now bluetooth ofono
```

---

## 📱 First-Time Phone Setup

1. **Pair your phone** with your PC first:
   ```bash
   bluetoothctl
   > scan on
   > pair XX:XX:XX:XX:XX:XX
   > trust XX:XX:XX:XX:XX:XX
   > connect XX:XX:XX:XX:XX:XX
   ```

2. **Accept permissions** on your phone when prompted:
   - Allow contact sharing (for PBAP sync)
   - Allow media info sharing (for AVRCP)

3. **Keep phone screen unlocked** during first contact sync

4. **oFono HFP profile** — phone must support Bluetooth HFP (Hands-Free Profile). All modern Android phones do.

---

## 🔋 Battery Note

Battery is read via Bluetooth HFP `AT+CIEV` indicator which only provides a **0–5 coarse scale** (not exact %). Each step covers ~20% range:

| HFP Level | Approximate Battery |
|---|---|
| 5 | 81–100% |
| 4 | 61–80% |
| 3 | 41–60% |
| 2 | 21–40% |
| 1 | 1–20% |
| 0 | Critical |

This is a Bluetooth protocol limitation — exact % is not available via standard HFP without a companion app (like Sefirah/KDE Connect).

---

## 🏗️ Project Structure

```
LinuxPhone/
├── main.py                          # Entry point
├── install.sh                       # Installer
├── uninstall.sh                     # Uninstaller
└── src/
    ├── components/
    │   ├── linuxphone_gui.py        # Main GTK4 window + UI
    │   ├── bluetooth_manager.py     # BlueZ adapter management
    │   ├── call_manager.py          # HFP calls via oFono
    │   ├── pba_fetcher.py           # PBAP contacts + call history
    │   ├── battery_monitor.py       # Battery + signal via oFono
    │   ├── media_controller.py      # AVRCP media control
    │   └── sms_manager.py           # SMS via oFono
    └── constants/
        └── __init__.py              # CSS + cache paths
```

---

## 🗑️ Uninstall

```bash
bash uninstall.sh
```

---

## 📄 License

MIT License — see [LICENSE](LICENSE)

---

## 🙏 Credits

Inspired by [Sefirah](https://github.com/shrimqy/Sefirah) — a Windows Phone Link alternative.
