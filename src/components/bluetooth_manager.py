

from datetime import datetime
import time
import dbus

# ══════════════════════════════════════════════════════
#  BLUETOOTH MANAGER
# ══════════════════════════════════════════════════════

class BluetoothManager:
    def __init__(self):
        self.bus = dbus.SystemBus()
        self.adapter_path = None
        self.adapter = None
        self.devices = {}
        self._init_adapter()

    def _init_adapter(self):
        try:
            mgr = dbus.Interface(self.bus.get_object("org.bluez", "/"),
                                 "org.freedesktop.DBus.ObjectManager")
            for path, ifaces in mgr.GetManagedObjects().items():
                if "org.bluez.Adapter1" in ifaces:
                    self.adapter_path = path
                    self.adapter = dbus.Interface(
                        self.bus.get_object("org.bluez", path), "org.bluez.Adapter1")
                    break
        except: pass

    def get_devices(self):
        devs = {}
        try:
            mgr = dbus.Interface(self.bus.get_object("org.bluez", "/"),
                                 "org.freedesktop.DBus.ObjectManager")
            for path, ifaces in mgr.GetManagedObjects().items():
                if "org.bluez.Device1" in ifaces:
                    p = ifaces["org.bluez.Device1"]
                    addr = str(p.get("Address", ""))
                    if addr:
                        devs[addr] = {
                            "name": str(p.get("Name", addr)),
                            "address": addr,
                            "paired": bool(p.get("Paired", False)),
                            "connected": bool(p.get("Connected", False)),
                            "uuids": [str(u).lower() for u in p.get("UUIDs", [])],
                            "path": str(path)
                        }
        except: pass
        self.devices = devs
        return devs

    def connect(self, addr):
        try:
            dev = self.devices.get(addr)
            if not dev: return False, "Not found"
            dbus.Interface(self.bus.get_object("org.bluez", dev["path"]),
                           "org.bluez.Device1").Connect()
            return True, "Connected"
        except dbus.DBusException as e:
            return False, str(e).split(": ")[-1]

    def disconnect(self, addr):
        try:
            dev = self.devices.get(addr)
            if not dev: return False, "Not found"
            dbus.Interface(self.bus.get_object("org.bluez", dev["path"]),
                           "org.bluez.Device1").Disconnect()
            return True, "Disconnected"
        except dbus.DBusException as e:
            return False, str(e).split(": ")[-1]

    def scan(self, duration=8):
        try:
            self.adapter.StartDiscovery()
            time.sleep(duration)
            self.adapter.StopDiscovery()
        except: pass
        return self.get_devices()

    def adapter_info(self):
        if not self.adapter_path: return {}
        try:
            props = dbus.Interface(self.bus.get_object("org.bluez", self.adapter_path),
                                   "org.freedesktop.DBus.Properties")
            a = props.GetAll("org.bluez.Adapter1")
            return {"name": str(a.get("Name","?")),
                    "address": str(a.get("Address","?")),
                    "powered": bool(a.get("Powered", False))}
        except: return {}

