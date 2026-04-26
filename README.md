# 📱 LinuxPhone

**Linux ke liye native Bluetooth Phone Companion**  
Inspired by [MyPhone (Windows)](https://github.com/BestOwl/MyPhone) — BlueZ + Python se banaya gaya

---

## 🚀 Quick Start

```bash
cd ~/LinuxPhone
python3 linuxphone.py
```

---

## ✨ Features

| Feature | Status |
|---|---|
| 📡 Bluetooth device scan & pair | ✅ Working |
| 🔗 Connect / Disconnect devices | ✅ Working |
| 📞 Calls via HFP (oFono) | ✅ Ready (needs oFono) |
| 🔔 Notifications (ANCS) | 🔄 Partial |
| 💬 SMS via MAP | 🔄 Planned |
| 📒 Contacts via PBAP | 🔄 Planned |

---

## 📦 Dependencies

```bash
# Already installed on Ubuntu 24.04:
# - python3-dbus
# - python3-gi
# - bluez / bluetoothctl

# For calling support:
sudo apt install ofono

# For Android notifications:
sudo apt install kdeconnect
```

---

## ⌨️ Keyboard Shortcuts

| Key | Action |
|---|---|
| `Tab` / `→` | Next tab |
| `Shift+Tab` / `←` | Previous tab |
| `S` | Scan for devices |
| `C` | Connect selected device |
| `D` | Disconnect selected device |
| `P` | Pair with selected device |
| `T` | Toggle Bluetooth on/off |
| `↑↓` | Navigate device list |
| `0-9 # *` | Dial number (in Calls tab) |
| `Enter` | Make call |
| `E` | End call |
| `N` | Test notification |
| `Q` | Quit |

---

## 🖥️ System Info

- **OS**: Ubuntu 24.04 LTS
- **BT Stack**: BlueZ 5.72
- **Python**: 3.12

---

Built with ❤️ by Claude for Vashu
