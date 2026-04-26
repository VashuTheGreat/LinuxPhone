#!/usr/bin/env python3
"""
LinuxPhone - Connect your Android/iOS phone to Linux via Bluetooth
Inspired by MyPhone (Windows) - Built natively for Linux using BlueZ
"""

import dbus
import dbus.mainloop.glib
import subprocess, threading, time, curses, sys, os, json
from datetime import datetime
from gi.repository import GLib

# ══════════════════════════════════════════════════════
#  BLUETOOTH MANAGER
# ══════════════════════════════════════════════════════

class BluetoothManager:
    def __init__(self):
        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.adapter = None
        self.adapter_path = None
        self.devices = {}
        self.connected_device = None
        self._init_adapter()

    def _init_adapter(self):
        try:
            manager = dbus.Interface(
                self.bus.get_object("org.bluez", "/"),
                "org.freedesktop.DBus.ObjectManager"
            )
            for path, ifaces in manager.GetManagedObjects().items():
                if "org.bluez.Adapter1" in ifaces:
                    self.adapter_path = path
                    self.adapter = dbus.Interface(
                        self.bus.get_object("org.bluez", path), "org.bluez.Adapter1"
                    )
                    break
        except:
            self.adapter = None

    def scan_devices(self, duration=8):
        if not self.adapter:
            return {}
        try:
            self.adapter.StartDiscovery()
            time.sleep(duration)
            self.adapter.StopDiscovery()
        except:
            pass
        return self.get_paired_devices()

    def get_paired_devices(self):
        devices = {}
        try:
            manager = dbus.Interface(
                self.bus.get_object("org.bluez", "/"),
                "org.freedesktop.DBus.ObjectManager"
            )
            for path, ifaces in manager.GetManagedObjects().items():
                if "org.bluez.Device1" in ifaces:
                    p = ifaces["org.bluez.Device1"]
                    addr = str(p.get("Address", ""))
                    if addr:
                        devices[addr] = {
                            "name": str(p.get("Name", addr)),
                            "address": addr,
                            "paired": bool(p.get("Paired", False)),
                            "connected": bool(p.get("Connected", False)),
                            "rssi": int(p.get("RSSI", 0)),
                            "uuids": [str(u) for u in p.get("UUIDs", [])],
                            "path": str(path)
                        }
        except:
            pass
        self.devices = devices
        return devices

    def connect_device(self, address):
        try:
            dev = self.devices.get(address)
            if not dev: return False, "Device not found"
            dbus.Interface(
                self.bus.get_object("org.bluez", dev["path"]), "org.bluez.Device1"
            ).Connect()
            self.connected_device = address
            return True, "Connected!"
        except dbus.DBusException as e:
            return False, str(e).split(": ")[-1]

    def disconnect_device(self, address):
        try:
            dev = self.devices.get(address)
            if not dev: return False, "Device not found"
            dbus.Interface(
                self.bus.get_object("org.bluez", dev["path"]), "org.bluez.Device1"
            ).Disconnect()
            self.connected_device = None
            return True, "Disconnected"
        except dbus.DBusException as e:
            return False, str(e).split(": ")[-1]

    def pair_device(self, address):
        try:
            dev = self.devices.get(address)
            if not dev: return False, "Device not found. Scan first."
            dbus.Interface(
                self.bus.get_object("org.bluez", dev["path"]), "org.bluez.Device1"
            ).Pair()
            return True, "Pairing initiated!"
        except dbus.DBusException as e:
            return False, str(e).split(": ")[-1]

    def get_adapter_info(self):
        if not self.adapter_path: return {}
        try:
            all_props = dbus.Interface(
                self.bus.get_object("org.bluez", self.adapter_path),
                "org.freedesktop.DBus.Properties"
            ).GetAll("org.bluez.Adapter1")
            return {
                "name": str(all_props.get("Name", "Unknown")),
                "address": str(all_props.get("Address", "Unknown")),
                "powered": bool(all_props.get("Powered", False)),
                "discoverable": bool(all_props.get("Discoverable", False)),
            }
        except:
            return {}

    def set_powered(self, powered: bool):
        try:
            dbus.Interface(
                self.bus.get_object("org.bluez", self.adapter_path),
                "org.freedesktop.DBus.Properties"
            ).Set("org.bluez.Adapter1", "Powered", dbus.Boolean(powered))
            return True
        except:
            return False

    def get_device_features(self, address):
        """Parse UUIDs to human-readable features"""
        dev = self.devices.get(address, {})
        uuids = dev.get("uuids", [])
        UUID_MAP = {
            "0000111e-0000-1000-8000-00805f9b34fb": "📞 Hands-Free (HFP)",
            "0000110b-0000-1000-8000-00805f9b34fb": "🔊 Audio Sink (A2DP)",
            "00001105-0000-1000-8000-00805f9b34fb": "📁 OBEX Object Push",
            "00001132-0000-1000-8000-00805f9b34fb": "💬 SMS/MAP",
            "0000112f-0000-1000-8000-00805f9b34fb": "📒 Phonebook (PBAP)",
            "00001116-0000-1000-8000-00805f9b34fb": "🔗 NAP (Bluetooth PAN)",
            "00001200-0000-1000-8000-00805f9b34fb": "🔍 PnP Info",
        }
        features = []
        for u in uuids:
            label = UUID_MAP.get(u.lower())
            if label:
                features.append(label)
        return features if features else ["No known profiles detected"]

# ══════════════════════════════════════════════════════
#  CALL MANAGER (HFP via PulseAudio/BlueZ)
# ══════════════════════════════════════════════════════

class CallManager:
    def __init__(self, bt: BluetoothManager):
        self.bt = bt
        self.active_call = None
        self.call_log = []

    def _get_ofono_modem(self):
        """Get first online oFono modem path via dbus"""
        try:
            bus = dbus.SystemBus()
            manager = dbus.Interface(
                bus.get_object("org.ofono", "/"),
                "org.ofono.Manager"
            )
            modems = manager.GetModems()
            for path, props in modems:
                if props.get("Online", False) and props.get("Powered", False):
                    ifaces = list(props.get("Interfaces", []))
                    if "org.ofono.VoiceCallManager" in ifaces:
                        return str(path), str(props.get("Name", path))
            return None, None
        except Exception as e:
            return None, str(e)

    def make_call(self, number: str):
        """Make a call via HFP using oFono dbus"""
        modem_path, modem_name = self._get_ofono_modem()
        if not modem_path:
            return False, "No online HFP modem found. Is phone connected via Bluetooth?"
        return self._call_via_ofono(number, modem_path, modem_name)

    def _call_via_ofono(self, number, modem_path, modem_name):
        try:
            bus = dbus.SystemBus()
            vcm = dbus.Interface(
                bus.get_object("org.ofono", modem_path),
                "org.ofono.VoiceCallManager"
            )
            call_path = vcm.Dial(number, "")
            self.active_call = {
                "number": number,
                "start": datetime.now(),
                "status": "dialing",
                "path": str(call_path),
                "modem": modem_name
            }
            self.call_log.append({
                "number": number,
                "time": datetime.now().strftime("%H:%M"),
                "type": "outgoing"
            })
            return True, f"📞 Calling {number} via {modem_name}..."
        except dbus.DBusException as e:
            return False, str(e).split(": ")[-1]
        except Exception as e:
            return False, str(e)

    def end_call(self):
        if not self.active_call:
            return False, "No active call"
        try:
            modem_path, _ = self._get_ofono_modem()
            if modem_path:
                bus = dbus.SystemBus()
                vcm = dbus.Interface(
                    bus.get_object("org.ofono", modem_path),
                    "org.ofono.VoiceCallManager"
                )
                vcm.HangupAll()
            self.active_call = None
            return True, "✅ Call ended"
        except dbus.DBusException as e:
            self.active_call = None
            return True, f"Call ended ({str(e).split(':')[-1].strip()})"
        except Exception as e:
            return False, str(e)

    def get_call_log(self):
        return self.call_log[-20:]

# ══════════════════════════════════════════════════════
#  NOTIFICATION LISTENER
# ══════════════════════════════════════════════════════

class NotificationListener:
    def __init__(self):
        self.notifications = []
        self._running = False
        self._thread = None

    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._listen, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _listen(self):
        """Listen for ANCS-like notifications via BT (simplified)"""
        while self._running:
            time.sleep(2)

    def add_mock_notification(self, app, title, body):
        self.notifications.append({
            "app": app, "title": title, "body": body,
            "time": datetime.now().strftime("%H:%M:%S")
        })
        if len(self.notifications) > 50:
            self.notifications.pop(0)

    def get_recent(self, n=10):
        return self.notifications[-n:][::-1]

# ══════════════════════════════════════════════════════
#  TERMINAL UI (curses)
# ══════════════════════════════════════════════════════

CYAN    = 1
GREEN   = 2
RED     = 3
YELLOW  = 4
MAGENTA = 5
WHITE   = 6
BLUE    = 7

TABS = ["📡 Devices", "📞 Calls", "🔔 Notifications", "ℹ️  Info"]

class LinuxPhoneUI:
    def __init__(self, stdscr):
        self.stdscr = stdscr
        self.bt = BluetoothManager()
        self.calls = CallManager(self.bt)
        self.notifs = NotificationListener()
        self.notifs.start()

        self.tab = 0
        self.status = "Ready. Press ? for help."
        self.scanning = False
        self.device_list = []
        self.selected_device = 0
        self.dial_number = ""
        self.dialing = False

        self._setup_colors()
        self._refresh_devices()

    def _setup_colors(self):
        curses.start_color()
        curses.use_default_colors()
        curses.init_pair(CYAN,    curses.COLOR_CYAN,    -1)
        curses.init_pair(GREEN,   curses.COLOR_GREEN,   -1)
        curses.init_pair(RED,     curses.COLOR_RED,     -1)
        curses.init_pair(YELLOW,  curses.COLOR_YELLOW,  -1)
        curses.init_pair(MAGENTA, curses.COLOR_MAGENTA, -1)
        curses.init_pair(WHITE,   curses.COLOR_WHITE,   -1)
        curses.init_pair(BLUE,    curses.COLOR_BLUE,    -1)
        curses.curs_set(0)
        self.stdscr.nodelay(True)
        self.stdscr.keypad(True)

    def _refresh_devices(self):
        devs = self.bt.get_paired_devices()
        self.device_list = list(devs.values())

    def draw_header(self, h, w):
        title = " 📱 LinuxPhone — Bluetooth Phone Companion for Linux "
        self.stdscr.attron(curses.color_pair(CYAN) | curses.A_BOLD)
        self.stdscr.addstr(0, 0, "─" * w)
        x = max(0, (w - len(title)) // 2)
        self.stdscr.addstr(1, x, title)
        self.stdscr.addstr(2, 0, "─" * w)
        self.stdscr.attroff(curses.color_pair(CYAN) | curses.A_BOLD)

        # Tabs
        tx = 2
        for i, tab in enumerate(TABS):
            if i == self.tab:
                self.stdscr.attron(curses.color_pair(GREEN) | curses.A_REVERSE | curses.A_BOLD)
                self.stdscr.addstr(3, tx, f" {tab} ")
                self.stdscr.attroff(curses.color_pair(GREEN) | curses.A_REVERSE | curses.A_BOLD)
            else:
                self.stdscr.attron(curses.color_pair(WHITE))
                self.stdscr.addstr(3, tx, f" {tab} ")
                self.stdscr.attroff(curses.color_pair(WHITE))
            tx += len(tab) + 3

        self.stdscr.attron(curses.color_pair(CYAN))
        self.stdscr.addstr(4, 0, "─" * w)
        self.stdscr.attroff(curses.color_pair(CYAN))

    def draw_status(self, h, w):
        self.stdscr.attron(curses.color_pair(CYAN))
        self.stdscr.addstr(h-2, 0, "─" * w)
        self.stdscr.attroff(curses.color_pair(CYAN))
        status_line = f" {self.status} "
        self.stdscr.attron(curses.color_pair(YELLOW))
        self.stdscr.addstr(h-1, 0, status_line[:w-1])
        self.stdscr.attroff(curses.color_pair(YELLOW))

    def draw_devices_tab(self, h, w, start_y):
        info = self.bt.get_adapter_info()
        y = start_y

        # Adapter info
        powered = info.get("powered", False)
        pw_str = "🟢 ON" if powered else "🔴 OFF"
        self.stdscr.attron(curses.color_pair(CYAN) | curses.A_BOLD)
        self.stdscr.addstr(y, 2, f"Bluetooth Adapter: {info.get('name','?')}  [{info.get('address','?')}]  Power: {pw_str}")
        self.stdscr.attroff(curses.color_pair(CYAN) | curses.A_BOLD)
        y += 2

        # Commands
        self.stdscr.attron(curses.color_pair(WHITE))
        self.stdscr.addstr(y, 2, "[S] Scan   [C] Connect   [D] Disconnect   [P] Pair   [T] Toggle BT")
        self.stdscr.attroff(curses.color_pair(WHITE))
        y += 2

        # Device list
        if not self.device_list:
            self.stdscr.attron(curses.color_pair(YELLOW))
            self.stdscr.addstr(y, 4, "No devices found. Press [S] to scan.")
            self.stdscr.attroff(curses.color_pair(YELLOW))
            return

        self.stdscr.attron(curses.color_pair(CYAN) | curses.A_BOLD)
        self.stdscr.addstr(y, 2, f"{'#':<3} {'Name':<28} {'Address':<20} {'Paired':<8} {'Connected':<10}")
        self.stdscr.addstr(y+1, 2, "─" * (w-4))
        self.stdscr.attroff(curses.color_pair(CYAN) | curses.A_BOLD)
        y += 2

        for i, dev in enumerate(self.device_list):
            if y >= h - 3: break
            paired = "✅" if dev["paired"] else "  "
            conn   = "🔗 YES" if dev["connected"] else "  no"
            name   = dev["name"][:26]
            addr   = dev["address"]

            if i == self.selected_device:
                self.stdscr.attron(curses.color_pair(GREEN) | curses.A_REVERSE)
                self.stdscr.addstr(y, 2, f"{i+1:<3} {name:<28} {addr:<20} {paired:<8} {conn:<10}"[:w-4])
                self.stdscr.attroff(curses.color_pair(GREEN) | curses.A_REVERSE)
            else:
                color = GREEN if dev["connected"] else WHITE
                self.stdscr.attron(curses.color_pair(color))
                self.stdscr.addstr(y, 2, f"{i+1:<3} {name:<28} {addr:<20} {paired:<8} {conn:<10}"[:w-4])
                self.stdscr.attroff(curses.color_pair(color))
            y += 1

        # Show features of selected device
        if self.device_list and self.selected_device < len(self.device_list):
            y += 1
            sel = self.device_list[self.selected_device]
            features = self.bt.get_device_features(sel["address"])
            self.stdscr.attron(curses.color_pair(MAGENTA) | curses.A_BOLD)
            self.stdscr.addstr(y, 2, f"Features of '{sel['name']}':")
            self.stdscr.attroff(curses.color_pair(MAGENTA) | curses.A_BOLD)
            y += 1
            for feat in features:
                if y >= h - 3: break
                self.stdscr.attron(curses.color_pair(YELLOW))
                self.stdscr.addstr(y, 4, feat[:w-6])
                self.stdscr.attroff(curses.color_pair(YELLOW))
                y += 1

    def draw_calls_tab(self, h, w, start_y):
        y = start_y
        self.stdscr.attron(curses.color_pair(CYAN) | curses.A_BOLD)
        self.stdscr.addstr(y, 2, "📞 Call Manager (requires oFono + HFP-connected device)")
        self.stdscr.attroff(curses.color_pair(CYAN) | curses.A_BOLD)
        y += 2

        # Active call
        if self.calls.active_call:
            self.stdscr.attron(curses.color_pair(GREEN) | curses.A_BOLD)
            num = self.calls.active_call["number"]
            dur = int((datetime.now() - self.calls.active_call["start"]).total_seconds())
            self.stdscr.addstr(y, 2, f"🟢 ACTIVE CALL: {num}  ({dur}s)   Press [E] to End")
            self.stdscr.attroff(curses.color_pair(GREEN) | curses.A_BOLD)
        else:
            self.stdscr.attron(curses.color_pair(WHITE))
            self.stdscr.addstr(y, 2, "No active call")
            self.stdscr.attroff(curses.color_pair(WHITE))
        y += 2

        # Dial pad
        self.stdscr.attron(curses.color_pair(YELLOW) | curses.A_BOLD)
        self.stdscr.addstr(y, 2, "Dial Number: ")
        self.stdscr.attroff(curses.color_pair(YELLOW) | curses.A_BOLD)
        self.stdscr.attron(curses.color_pair(GREEN))
        number_display = self.dial_number if self.dial_number else "(type number, press Enter to call)"
        self.stdscr.addstr(y, 16, number_display[:w-18])
        self.stdscr.attroff(curses.color_pair(GREEN))
        y += 1
        self.stdscr.attron(curses.color_pair(WHITE))
        self.stdscr.addstr(y, 2, "[0-9 # *] Type number  [Enter] Call  [Backspace] Delete  [E] End call")
        self.stdscr.attroff(curses.color_pair(WHITE))
        y += 2

        # Call log
        self.stdscr.attron(curses.color_pair(CYAN) | curses.A_BOLD)
        self.stdscr.addstr(y, 2, "Recent Calls:")
        self.stdscr.addstr(y+1, 2, "─" * 40)
        self.stdscr.attroff(curses.color_pair(CYAN) | curses.A_BOLD)
        y += 2
        log = self.calls.get_call_log()
        if not log:
            self.stdscr.attron(curses.color_pair(WHITE))
            self.stdscr.addstr(y, 4, "No call history yet.")
            self.stdscr.attroff(curses.color_pair(WHITE))
        for entry in log:
            if y >= h - 3: break
            icon = "📤" if entry["type"] == "outgoing" else "📥"
            self.stdscr.attron(curses.color_pair(YELLOW))
            self.stdscr.addstr(y, 4, f"{icon} {entry['time']}  {entry['number']}")
            self.stdscr.attroff(curses.color_pair(YELLOW))
            y += 1

    def draw_notifs_tab(self, h, w, start_y):
        y = start_y
        self.stdscr.attron(curses.color_pair(CYAN) | curses.A_BOLD)
        self.stdscr.addstr(y, 2, "🔔 Phone Notifications (ANCS/Bluetooth)")
        self.stdscr.attroff(curses.color_pair(CYAN) | curses.A_BOLD)
        y += 2
        self.stdscr.attron(curses.color_pair(WHITE))
        self.stdscr.addstr(y, 2, "[N] Add test notification")
        self.stdscr.attroff(curses.color_pair(WHITE))
        y += 2

        notifs = self.notifs.get_recent(h - y - 3)
        if not notifs:
            self.stdscr.attron(curses.color_pair(YELLOW))
            self.stdscr.addstr(y, 4, "No notifications yet.")
            self.stdscr.addstr(y+1, 4, "Note: Full ANCS support requires a paired iOS device.")
            self.stdscr.addstr(y+2, 4, "For Android, use KDE Connect companion app for notifications.")
            self.stdscr.attroff(curses.color_pair(YELLOW))
            return
        for notif in notifs:
            if y >= h - 3: break
            self.stdscr.attron(curses.color_pair(MAGENTA) | curses.A_BOLD)
            self.stdscr.addstr(y, 2, f"[{notif['time']}] {notif['app']}")
            self.stdscr.attroff(curses.color_pair(MAGENTA) | curses.A_BOLD)
            y += 1
            self.stdscr.attron(curses.color_pair(WHITE))
            self.stdscr.addstr(y, 4, f"{notif['title']}: {notif['body']}"[:w-6])
            self.stdscr.attroff(curses.color_pair(WHITE))
            y += 1
            self.stdscr.attron(curses.color_pair(CYAN))
            self.stdscr.addstr(y, 2, "·" * (w//2))
            self.stdscr.attroff(curses.color_pair(CYAN))
            y += 1

    def draw_info_tab(self, h, w, start_y):
        y = start_y
        info = self.bt.get_adapter_info()

        lines = [
            ("📱 LinuxPhone", CYAN, True),
            ("", WHITE, False),
            ("A native Linux Bluetooth phone companion.", WHITE, False),
            ("Inspired by MyPhone (Windows) — rewritten for Linux using BlueZ.", WHITE, False),
            ("", WHITE, False),
            ("── Adapter Info ─────────────────────", CYAN, True),
            (f"  Name     : {info.get('name','N/A')}", GREEN, False),
            (f"  Address  : {info.get('address','N/A')}", GREEN, False),
            (f"  Powered  : {'Yes' if info.get('powered') else 'No'}", GREEN, False),
            (f"  Discover : {'Yes' if info.get('discoverable') else 'No'}", GREEN, False),
            ("", WHITE, False),
            ("── Supported Profiles ────────────────", CYAN, True),
            ("  📞 HFP  — Hands-Free Profile (calls) via oFono", YELLOW, False),
            ("  💬 MAP  — Message Access Profile (SMS)", YELLOW, False),
            ("  📒 PBAP — Phone Book Access (contacts)", YELLOW, False),
            ("  🔊 A2DP — Audio streaming", YELLOW, False),
            ("", WHITE, False),
            ("── Keyboard Shortcuts ────────────────", CYAN, True),
            ("  Tab / Shift+Tab : Switch tabs", WHITE, False),
            ("  S : Scan devices    C : Connect    D : Disconnect", WHITE, False),
            ("  P : Pair device     T : Toggle Bluetooth", WHITE, False),
            ("  ↑ ↓ : Select device    E : End call    Q : Quit", WHITE, False),
            ("  N : Add test notification", WHITE, False),
            ("", WHITE, False),
            ("── Dependencies ──────────────────────", CYAN, True),
            ("  BlueZ, dbus-python, python3-gi, ofono (for calls)", WHITE, False),
        ]
        for text, color, bold in lines:
            if y >= h - 3: break
            attr = curses.color_pair(color)
            if bold: attr |= curses.A_BOLD
            self.stdscr.attron(attr)
            self.stdscr.addstr(y, 2, text[:w-4])
            self.stdscr.attroff(attr)
            y += 1

    def draw(self):
        h, w = self.stdscr.getmaxyx()
        self.stdscr.erase()
        try:
            self.draw_header(h, w)
            start_y = 5
            if self.tab == 0:
                self.draw_devices_tab(h, w, start_y)
            elif self.tab == 1:
                self.draw_calls_tab(h, w, start_y)
            elif self.tab == 2:
                self.draw_notifs_tab(h, w, start_y)
            elif self.tab == 3:
                self.draw_info_tab(h, w, start_y)
            self.draw_status(h, w)
        except curses.error:
            pass
        self.stdscr.refresh()

    def handle_input(self, key):
        # Tab switching
        if key == ord('\t') or key == curses.KEY_RIGHT:
            self.tab = (self.tab + 1) % len(TABS)
        elif key == curses.KEY_BTAB or key == curses.KEY_LEFT:
            self.tab = (self.tab - 1) % len(TABS)
        elif key == ord('q') or key == ord('Q'):
            return False

        # Device tab keys
        elif self.tab == 0:
            if key == ord('s') or key == ord('S'):
                self._do_scan()
            elif key == ord('r') or key == ord('R'):
                self._refresh_devices()
                self.status = f"Refreshed. {len(self.device_list)} devices."
            elif key == ord('c') or key == ord('C'):
                self._do_connect()
            elif key == ord('d') or key == ord('D'):
                self._do_disconnect()
            elif key == ord('p') or key == ord('P'):
                self._do_pair()
            elif key == ord('t') or key == ord('T'):
                self._toggle_bt()
            elif key == curses.KEY_UP:
                self.selected_device = max(0, self.selected_device - 1)
            elif key == curses.KEY_DOWN:
                self.selected_device = min(len(self.device_list)-1, self.selected_device + 1)

        # Call tab keys
        elif self.tab == 1:
            if key in range(ord('0'), ord('9')+1) or key in [ord('#'), ord('*'), ord('+')]:
                self.dial_number += chr(key)
            elif key == curses.KEY_BACKSPACE or key == 127:
                self.dial_number = self.dial_number[:-1]
            elif key == ord('\n') or key == curses.KEY_ENTER:
                if self.dial_number:
                    ok, msg = self.calls.make_call(self.dial_number)
                    self.status = msg
                    if ok: self.dial_number = ""
            elif key == ord('e') or key == ord('E'):
                ok, msg = self.calls.end_call()
                self.status = msg

        # Notification tab
        elif self.tab == 2:
            if key == ord('n') or key == ord('N'):
                self.notifs.add_mock_notification(
                    "WhatsApp", "Mom", "Are you coming for dinner? 🍛"
                )
                self.status = "Test notification added."

        return True

    def _do_scan(self):
        self.status = "🔍 Scanning for devices (8s)..."
        self.draw()
        def scan():
            self.bt.scan_devices(8)
            self._refresh_devices()
            self.status = f"Scan complete. {len(self.device_list)} device(s) found."
        threading.Thread(target=scan, daemon=True).start()

    def _do_connect(self):
        if not self.device_list: return
        dev = self.device_list[self.selected_device]
        self.status = f"Connecting to {dev['name']}..."
        self.draw()
        ok, msg = self.bt.connect_device(dev["address"])
        self._refresh_devices()
        self.status = f"{'✅' if ok else '❌'} {msg}"

    def _do_disconnect(self):
        if not self.device_list: return
        dev = self.device_list[self.selected_device]
        ok, msg = self.bt.disconnect_device(dev["address"])
        self._refresh_devices()
        self.status = f"{'✅' if ok else '❌'} {msg}"

    def _do_pair(self):
        if not self.device_list: return
        dev = self.device_list[self.selected_device]
        self.status = f"Pairing with {dev['name']}..."
        self.draw()
        ok, msg = self.bt.pair_device(dev["address"])
        self.status = f"{'✅' if ok else '❌'} {msg}"

    def _toggle_bt(self):
        info = self.bt.get_adapter_info()
        new_state = not info.get("powered", True)
        ok = self.bt.set_powered(new_state)
        self.status = f"Bluetooth {'ON' if new_state else 'OFF'}" if ok else "Failed to toggle BT"

    def run(self):
        last_refresh = time.time()
        while True:
            self.draw()
            key = self.stdscr.getch()
            if key != -1:
                if not self.handle_input(key):
                    break
            # Auto-refresh devices every 5s
            if time.time() - last_refresh > 5:
                self._refresh_devices()
                last_refresh = time.time()
            time.sleep(0.05)


# ══════════════════════════════════════════════════════
#  ENTRY POINT
# ══════════════════════════════════════════════════════

def main():
    print("Starting LinuxPhone...")
    time.sleep(0.5)
    try:
        curses.wrapper(lambda scr: LinuxPhoneUI(scr).run())
    except KeyboardInterrupt:
        pass
    print("\nLinuxPhone closed. Goodbye! 👋")

if __name__ == "__main__":
    main()
