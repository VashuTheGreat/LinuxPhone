
import dbus
from datetime import datetime
from gi.repository import GLib
import re
# ══════════════════════════════════════════════════════
#  CALL MANAGER
# ══════════════════════════════════════════════════════



class CallManager:
    def __init__(self):
        self.active_call = None
        self.call_log = []
        self._bus = dbus.SystemBus()
        self._incoming_cb = None   # set by window to show incoming call popup
        self._watch_calls()

    def set_incoming_callback(self, cb):
        """cb(number, call_path) called when an incoming call arrives"""
        self._incoming_cb = cb

    def set_call_removed_callback(self, cb):
        """cb(call_path) called when a call is disconnected/removed"""
        self._removed_cb = cb

    def set_call_state_changed_callback(self, cb):
        """cb(call_path, state) called when a call's state changes (e.g. to 'active')"""
        self._state_cb = cb

    def _watch_calls(self):
        """Subscribe to oFono VoiceCallManager CallAdded and CallRemoved signals on system bus"""
        try:
            self._bus.add_signal_receiver(
                self._on_call_added,
                signal_name="CallAdded",
                dbus_interface="org.ofono.VoiceCallManager",
                bus_name="org.ofono"
            )
            self._bus.add_signal_receiver(
                self._on_call_removed,
                signal_name="CallRemoved",
                dbus_interface="org.ofono.VoiceCallManager",
                bus_name="org.ofono"
            )
            self._bus.add_signal_receiver(
                self._on_call_property_changed,
                signal_name="PropertyChanged",
                dbus_interface="org.ofono.VoiceCall",
                bus_name="org.ofono",
                path_keyword="call_path"
            )
        except Exception as e:
            print(f"[CallManager] Could not subscribe to Call signals: {e}")

    def _on_call_added(self, call_path, props):
        """Fires when oFono announces a new call (incoming or outgoing)"""
        state = str(props.get("State", ""))
        line_id = str(props.get("LineIdentification", "Unknown"))
        name = str(props.get("Name", ""))
        display = name if name else line_id
        
        # Save display name for later state changes
        if not hasattr(self, '_call_names'):
            self._call_names = {}
        self._call_names[str(call_path)] = display
        
        if state == "incoming" and getattr(self, '_incoming_cb', None):
            GLib.idle_add(self._incoming_cb, display, str(call_path))

    def _on_call_removed(self, call_path):
        """Fires when a call is destroyed/disconnected"""
        if getattr(self, '_removed_cb', None):
            GLib.idle_add(self._removed_cb, str(call_path))
            
    def _on_call_property_changed(self, name, value, call_path=None):
        if name == "State" and call_path:
            state = str(value)
            display = getattr(self, '_call_names', {}).get(str(call_path), "Unknown")
            if getattr(self, '_state_cb', None):
                GLib.idle_add(self._state_cb, str(call_path), state, display)

    def answer(self, call_path):
        """Answer an incoming call by its oFono object path"""
        try:
            call_iface = dbus.Interface(
                self._bus.get_object("org.ofono", call_path),
                "org.ofono.VoiceCall"
            )
            call_iface.Answer()
            self.active_call = {"number": call_path, "name": call_path,
                                "start": datetime.now(), "path": call_path}
            return True, "Call answered"
        except dbus.DBusException as e:
            return False, str(e).split(": ")[-1]

    def reject(self, call_path):
        """Reject / hang up a specific incoming call"""
        try:
            call_iface = dbus.Interface(
                self._bus.get_object("org.ofono", call_path),
                "org.ofono.VoiceCall"
            )
            call_iface.Hangup()
            return True, "Call rejected"
        except dbus.DBusException as e:
            return False, str(e).split(": ")[-1]

    def _get_modem(self):
        try:
            mgr = dbus.Interface(self._bus.get_object("org.ofono", "/"), "org.ofono.Manager")
            for path, props in mgr.GetModems():
                if props.get("Online") and props.get("Powered"):
                    ifaces = list(props.get("Interfaces", []))
                    if "org.ofono.VoiceCallManager" in ifaces:
                        return str(path), str(props.get("Name", path))
        except: pass
        return None, None

    def call(self, number):
        number = re.sub(r'[^\d+]', '', number)
        if not number: return False, "Invalid number"
        path, name = self._get_modem()
        if not path: return False, "No HFP modem. Is phone connected?"
        try:
            vcm = dbus.Interface(self._bus.get_object("org.ofono", path),
                                 "org.ofono.VoiceCallManager")
            call_path = vcm.Dial(number, "")
            self.active_call = {"number": number, "name": name,
                                "start": datetime.now(), "path": str(call_path)}
            self.call_log.insert(0, {"number": number, "time": datetime.now().strftime("%H:%M %d/%m"),
                                     "type": "outgoing", "modem": name})
            return True, f"Calling {number} via {name}…"
        except dbus.DBusException as e:
            return False, str(e).split(": ")[-1]

    def hangup(self):
        path, _ = self._get_modem()
        try:
            if path:
                dbus.Interface(self._bus.get_object("org.ofono", path),
                               "org.ofono.VoiceCallManager").HangupAll()
        except: pass
        self.active_call = None
        return True, "Call ended"
