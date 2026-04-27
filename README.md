<p align="center">
  <img src="https://raw.githubusercontent.com/VashuTheGreat/LinuxPhone/main/dist/icons/icon.svg" width="100" alt="LinuxPhone Icon"/>
</p>

<h1 align="center">📱 LinuxPhone</h1>

<p align="center">
  <b>Linux ke liye native Bluetooth Phone Companion</b><br/>
  Apne Android phone ke contacts, calls aur dial pad — seedha Linux desktop pe
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Platform-Linux-blue?logo=linux" />
  <img src="https://img.shields.io/badge/Python-3.10%2B-green?logo=python" />
  <img src="https://img.shields.io/badge/GTK-4.0%20%2F%20Adwaita-orange" />
  <img src="https://img.shields.io/badge/Bluetooth-BlueZ%20%2B%20PBAP-informational" />
  <img src="https://img.shields.io/badge/License-MIT-lightgrey" />
</p>

---

## ⚡ Install (One Command)

```bash
git clone https://github.com/VashuTheGreat/LinuxPhone.git && cd LinuxPhone && bash install.sh
```

> **Requirements:** Ubuntu / Debian based Linux, Bluetooth adapter

Install hone ke baad:
- Terminal se chalao: `linuxphone`
- Ya app menu mein **"LinuxPhone"** search karo

---

## ✨ Features

| Feature | Status |
|---|---|
| 📒 Contacts sync via Bluetooth (PBAP) | ✅ Working |
| 📞 Recent calls — Incoming / Outgoing / Missed | ✅ Working |
| 🔢 Dial Pad — number dial karo | ✅ Working |
| 🔵 Bluetooth device connect / disconnect | ✅ Working |
| 📦 Offline cache — bina Bluetooth ke bhi contacts dikhte hain | ✅ Working |
| 🔍 Contact search | ✅ Working |
| 📲 Call back button (call history se) | ✅ Working |
| 📞 Make calls via HFP / oFono | ✅ Working (oFono required) |
| 💬 SMS / Messages | 🔄 Planned |

---

## 🖥️ Screenshots

> _Coming soon_

---

## 📋 How to Use

### 1. Phone pair karo
Pehle phone ko Linux ke saath Bluetooth se pair karo:
```bash
bluetoothctl
> scan on
> pair XX:XX:XX:XX:XX:XX
> connect XX:XX:XX:XX:XX:XX
```

### 2. App kholo
```bash
linuxphone
```

### 3. Contacts sync karo
- **Contacts** tab mein jao → **"Sync Contacts"** button dabao
- Phone pe permission allow karo
- Contacts load honge aur cache mein save ho jaayenge

### 4. Call history dekho
- **Recent Calls** tab mein jao → **"Sync Calls"** button dabao
- **All / Incoming / Outgoing / Missed** filter se sort karo

### 5. Call karo
- Contacts mein phone icon dabao
- Ya **Dial Pad** tab se number type karo

---

## 📦 Dependencies

`install.sh` automatically install kar deta hai:

| Package | Kaam |
|---|---|
| `python3-gi`, `gir1.2-gtk-4.0` | GTK4 UI |
| `gir1.2-adw-1` | Adwaita design |
| `python3-dbus` | Bluetooth DBus interface |
| `python3-vobject` | vCard / VCF parsing |
| `bluez` | Bluetooth stack |
| `ofono` | Calling support (HFP) — optional |

---

## 🗂️ Cache

Contacts aur calls `~/.cache/linuxphone/` mein JSON format mein save hote hain:
```
~/.cache/linuxphone/
├── contacts.json   ← synced contacts
└── calls.json      ← recent call history
```
Iska matlab: **phone connected na ho tab bhi** app contacts dikhayega.

---

## 🔧 Manual Run (bina install ke)

```bash
git clone https://github.com/VashuTheGreat/LinuxPhone.git
cd LinuxPhone
python3 linuxphone_gui.py
```

---

## 🗑️ Uninstall

```bash
bash uninstall.sh
```

---

## 🛠️ Tech Stack

- **Python 3** — core language
- **GTK4 + Adwaita (libadwaita)** — modern GNOME UI
- **BlueZ** — Linux Bluetooth stack
- **PBAP (Phone Book Access Profile)** — contacts & call history fetch
- **oFono + HFP** — voice calling
- **DBus** — system service communication

---

## 🤝 Contributing

Pull requests welcome! Koi bug mile ya feature suggest karna ho to [Issues](https://github.com/VashuTheGreat/LinuxPhone/issues) mein batao.

---

## 📄 License

MIT License — free to use, modify aur distribute karo.

---

<p align="center">Built with ❤️ for Linux users who want their phone on their desktop</p>
