"""
SMS Manager — reads/sends SMS via oFono MessageManager
"""
import dbus, json, os, threading
from gi.repository import GLib
from src.constants import SMS_CACHE

class SmsManager:
    def __init__(self):
        self._bus = dbus.SystemBus()
        self._incoming_cb = None
        self._watch_sms()

    def set_incoming_callback(self, cb):
        """cb(sender, body, timestamp)"""
        self._incoming_cb = cb

    def _get_modem_path(self):
        try:
            mgr = dbus.Interface(self._bus.get_object("org.ofono", "/"),
                                 "org.ofono.Manager")
            for path, props in mgr.GetModems():
                if props.get("Online") and props.get("Powered"):
                    ifaces = [str(i) for i in props.get("Interfaces", [])]
                    if "org.ofono.MessageManager" in ifaces:
                        return str(path)
        except Exception:
            pass
        return None

    def _watch_sms(self):
        try:
            self._bus.add_signal_receiver(
                self._on_incoming_message,
                signal_name="IncomingMessage",
                dbus_interface="org.ofono.MessageManager",
                bus_name="org.ofono",
            )
        except Exception as e:
            print(f"[SmsManager] Could not subscribe to IncomingMessage: {e}")

    def _on_incoming_message(self, message, info):
        sender = str(info.get("Sender", "Unknown"))
        body = str(message)
        timestamp = str(info.get("SentTime", ""))
        if self._incoming_cb:
            GLib.idle_add(self._incoming_cb, sender, body, timestamp)

    def send_sms(self, number, text):
        """Send an SMS. Returns (ok, msg)."""
        def do():
            path = self._get_modem_path()
            if not path:
                return False, "No modem with MessageManager"
            try:
                mm = dbus.Interface(self._bus.get_object("org.ofono", path),
                                    "org.ofono.MessageManager")
                mm.SendMessage(number, text)
                return True, f"SMS sent to {number}"
            except dbus.DBusException as e:
                return False, str(e).split(": ")[-1]
        threading.Thread(target=do, daemon=True).start()
        return True, "Sending…"

    def can_send_sms(self):
        return self._get_modem_path() is not None
