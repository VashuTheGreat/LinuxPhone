"""
Media Controller — AVRCP via BlueZ org.bluez.MediaControl1 + MediaPlayer1
Allows Play/Pause/Next/Previous on the connected phone.
Also reads track title/artist from MediaPlayer1 D-Bus properties.
"""

import dbus
import threading
from gi.repository import GLib


class MediaController:
    """Control phone media playback via Bluetooth AVRCP."""

    def __init__(self):
        self._bus = dbus.SystemBus()
        self._player_path = None
        self._control_path = None
        self._track_callbacks = []   # cb(title, artist, album, status, duration_ms, position_ms)
        self._status_callbacks = []  # cb(status: "playing"|"paused"|"stopped")
        self._last_status = None
        self._last_track = None

    def add_track_callback(self, cb):
        """cb(title, artist, album, status, duration_ms, position_ms)"""
        self._track_callbacks.append(cb)

    def add_status_callback(self, cb):
        """cb(status: str)"""
        self._status_callbacks.append(cb)

    def _find_player(self):
        """Find the first active MediaPlayer1 for a connected device."""
        try:
            mgr = dbus.Interface(self._bus.get_object("org.bluez", "/"),
                                 "org.freedesktop.DBus.ObjectManager")
            for path, ifaces in mgr.GetManagedObjects().items():
                if "org.bluez.MediaPlayer1" in ifaces:
                    props = ifaces["org.bluez.MediaPlayer1"]
                    # Prefer connected device's player
                    dev_path = str(props.get("Device", ""))
                    if dev_path:
                        dev_ifaces = mgr.GetManagedObjects().get(dev_path, {})
                        dev_props = dev_ifaces.get("org.bluez.Device1", {})
                        if dev_props.get("Connected", False):
                            self._player_path = str(path)
                            # MediaControl1 is on the device path
                            self._control_path = dev_path
                            return True
            # Fallback: any player
            for path, ifaces in mgr.GetManagedObjects().items():
                if "org.bluez.MediaPlayer1" in ifaces:
                    self._player_path = str(path)
                    # control is parent path
                    parts = str(path).rsplit("/", 1)
                    self._control_path = parts[0] if len(parts) > 1 else None
                    return True
        except Exception:
            pass
        return False

    def _get_player_props(self):
        """Return dict of MediaPlayer1 properties or {}."""
        if not self._player_path:
            if not self._find_player():
                return {}
        try:
            props_iface = dbus.Interface(
                self._bus.get_object("org.bluez", self._player_path),
                "org.freedesktop.DBus.Properties"
            )
            return dict(props_iface.GetAll("org.bluez.MediaPlayer1"))
        except Exception:
            self._player_path = None
            return {}

    def poll(self):
        """Poll MediaPlayer1 — call every ~2s from GLib.timeout_add."""
        props = self._get_player_props()
        if not props:
            return True

        status = str(props.get("Status", "unknown"))
        track = dict(props.get("Track", {}))
        position = int(props.get("Position", 0))

        title = str(track.get("Title", ""))
        artist = str(track.get("Artist", ""))
        album = str(track.get("Album", ""))
        duration = int(track.get("Duration", 0))

        # Notify status change
        if status != self._last_status:
            self._last_status = status
            for cb in self._status_callbacks:
                GLib.idle_add(cb, status)

        # Notify track change (by title+artist key)
        track_key = f"{title}|{artist}"
        if track_key != self._last_track or status != self._last_status:
            self._last_track = track_key
            for cb in self._track_callbacks:
                GLib.idle_add(cb, title, artist, album, status, duration, position)

        return True

    # ── Playback commands ─────────────────────────────

    def _send_command(self, method_name):
        """Send an AVRCP command to the phone in a background thread."""
        def do():
            try:
                if not self._control_path:
                    self._find_player()
                if self._control_path:
                    mc = dbus.Interface(
                        self._bus.get_object("org.bluez", self._control_path),
                        "org.bluez.MediaControl1"
                    )
                    getattr(mc, method_name)()
            except Exception as e:
                print(f"[MediaController] {method_name} failed: {e}")
        threading.Thread(target=do, daemon=True).start()

    def play(self):
        self._send_command("Play")

    def pause(self):
        self._send_command("Pause")

    def play_pause(self):
        """Toggle play/pause based on current status."""
        if self._last_status == "playing":
            self.pause()
        else:
            self.play()

    def next_track(self):
        self._send_command("Next")

    def previous_track(self):
        self._send_command("Previous")

    def stop(self):
        self._send_command("Stop")

    def volume_up(self):
        self._send_command("VolumeUp")

    def volume_down(self):
        self._send_command("VolumeDown")

    @property
    def is_playing(self):
        return self._last_status == "playing"

    @property
    def current_status(self):
        return self._last_status or "unknown"
