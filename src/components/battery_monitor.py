"""
Battery & Signal Monitor — reads from oFono Handsfree + NetworkRegistration
BatteryChargeLevel: 0–5 (0=empty, 5=full)
NetworkStrength: 0–5 (maps to signal bars)
"""

import dbus
from gi.repository import GLib


class BatteryMonitor:
    """Poll oFono Handsfree for battery level and NetworkRegistration for signal."""

    BATTERY_ICONS = ["battery-empty-symbolic", "battery-caution-symbolic",
                     "battery-low-symbolic", "battery-good-symbolic",
                     "battery-good-symbolic", "battery-full-symbolic"]

    SIGNAL_ICONS = ["network-cellular-offline-symbolic",
                    "network-cellular-signal-weak-symbolic",
                    "network-cellular-signal-ok-symbolic",
                    "network-cellular-signal-good-symbolic",
                    "network-cellular-signal-excellent-symbolic",
                    "network-cellular-signal-excellent-symbolic"]

    def __init__(self):
        self._bus = dbus.SystemBus()
        self._battery_level = -1   # 0–5, -1 = unknown
        self._signal_strength = -1  # 0–5 (ASCII 0x30–0x35)
        self._carrier = ""
        self._callbacks = []       # (battery_cb, signal_cb)

    def add_callback(self, battery_cb, signal_cb):
        """Register callbacks: battery_cb(level 0-5), signal_cb(strength 0-5, carrier str)"""
        self._callbacks.append((battery_cb, signal_cb))

    def _get_modem_path(self):
        """Return the path of the first online HFP modem, or None."""
        try:
            mgr = dbus.Interface(self._bus.get_object("org.ofono", "/"),
                                 "org.ofono.Manager")
            for path, props in mgr.GetModems():
                if props.get("Online") and props.get("Powered"):
                    ifaces = [str(i) for i in props.get("Interfaces", [])]
                    if "org.ofono.Handsfree" in ifaces:
                        return str(path)
        except Exception:
            pass
        return None

    def poll(self):
        """Called periodically to read battery + signal. Returns True to repeat."""
        path = self._get_modem_path()
        if not path:
            return True

        # Battery from Handsfree
        try:
            hf = dbus.Interface(self._bus.get_object("org.ofono", path),
                                "org.ofono.Handsfree")
            props = hf.GetProperties()
            # BatteryChargeLevel is dbus.Byte value 0-5
            raw = props.get("BatteryChargeLevel", -1)
            level = int(raw) if raw is not None else -1
            if level != self._battery_level:
                self._battery_level = level
                for battery_cb, _ in self._callbacks:
                    GLib.idle_add(battery_cb, level)
        except Exception:
            pass

        # Signal + Carrier from NetworkRegistration
        try:
            nr = dbus.Interface(self._bus.get_object("org.ofono", path),
                                "org.ofono.NetworkRegistration")
            props = nr.GetProperties()
            # Strength is a byte value whose ord() gives roughly 0–100
            raw_strength = props.get("Strength", 0)
            strength_pct = int(raw_strength) if raw_strength else 0
            # Convert 0–100 to 0–5 bars
            signal_bars = min(5, strength_pct // 20)
            carrier = str(props.get("Name", ""))

            if signal_bars != self._signal_strength or carrier != self._carrier:
                self._signal_strength = signal_bars
                self._carrier = carrier
                for _, signal_cb in self._callbacks:
                    GLib.idle_add(signal_cb, signal_bars, carrier)
        except Exception:
            pass

        return True  # Repeat

    def get_battery_icon(self, level):
        """Return symbolic icon name for battery level 0–5."""
        if level < 0:
            return "battery-missing-symbolic"
        return self.BATTERY_ICONS[min(level, 5)]

    # HFP BatteryChargeLevel is 0–5 (coarse). Each level covers a 20% range.
    # Level 2 = 21–40%, Level 3 = 41–60%, etc.
    # The phone itself decides which level to report — we can't get exact %.
    BATTERY_RANGES = [
        ("<20%",   "0–20%"),    # level 0
        ("~20%",   "1–20%"),    # level 1
        ("~40%",   "21–40%"),   # level 2  ← 45% still here until phone reports level 3
        ("~60%",   "41–60%"),   # level 3
        ("~80%",   "61–80%"),   # level 4
        ("~100%",  "81–100%"),  # level 5
    ]

    def get_battery_percent(self, level):
        """Return approximate percent label for HFP battery level 0–5.
        HFP only provides a 0-5 coarse level — exact % not available via Bluetooth."""
        if level < 0:
            return "?%", "Battery unknown"
        label, range_str = self.BATTERY_RANGES[min(level, 5)]
        tooltip = (f"Battery level: {label}\n"
                   f"Bluetooth HFP reports a 0–5 scale.\n"
                   f"Level {level} ≈ {range_str}\n"
                   f"Exact % not available via Bluetooth.")
        return label, tooltip

    def get_signal_icon(self, bars):
        """Return symbolic icon name for signal bars 0–5."""
        if bars < 0:
            return "network-cellular-offline-symbolic"
        return self.SIGNAL_ICONS[min(bars, 5)]
