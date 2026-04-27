#!/usr/bin/env python3
"""
LinuxPhone Desktop — GTK4/Adwaita GUI
Bluetooth Phone Companion for Linux
Contacts (PBAP) + Calls (HFP/oFono) + Devices
"""

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gtk, Adw, GLib, Gdk, Gio, GObject, Pango
import dbus, dbus.mainloop.glib
import threading, subprocess, time, vobject, io, re, json, os
from datetime import datetime

CACHE_DIR = os.path.expanduser("~/.cache/linuxphone")
os.makedirs(CACHE_DIR, exist_ok=True)
CONTACTS_CACHE = os.path.join(CACHE_DIR, "contacts.json")
CALLS_CACHE    = os.path.join(CACHE_DIR, "calls.json")

dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)

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


# ══════════════════════════════════════════════════════
#  PBAP CONTACTS FETCHER
# ══════════════════════════════════════════════════════

class PBAPFetcher:
    """Fetch contacts & call history from phone via Bluetooth PBAP"""

    def __init__(self, bt: BluetoothManager):
        self.bt = bt
        self.contacts = []
        self.call_history = []

    def _get_connected_addr(self):
        devs = self.bt.get_devices()
        pbap_uuid = "0000112f-0000-1000-8000-00805f9b34fb"
        for addr, dev in devs.items():
            if dev["connected"] and any(pbap_uuid in u for u in dev["uuids"]):
                return addr, dev["name"]
        # fallback: any connected device
        for addr, dev in devs.items():
            if dev["connected"]:
                return addr, dev["name"]
        return None, None

    def _create_session(self, addr):
        sbus = dbus.SessionBus()
        client = dbus.Interface(
            sbus.get_object("org.bluez.obex", "/org/bluez/obex"),
            "org.bluez.obex.Client1"
        )
        session_path = client.CreateSession(addr, {"Target": dbus.String("pbap")})
        pbap = dbus.Interface(
            sbus.get_object("org.bluez.obex", session_path),
            "org.bluez.obex.PhonebookAccess1"
        )
        return sbus, client, session_path, pbap

    def _pull_and_wait(self, sbus, pbap, dest_path):
        """Pull phonebook and wait for completion by polling file size"""
        import os
        transfer_path, _ = pbap.PullAll(dest_path, dbus.Dictionary({}, signature='sv'))
        # Poll until file stops growing (transfer complete)
        prev_size = -1
        stable_count = 0
        for _ in range(120):
            time.sleep(0.5)
            try:
                size = os.path.getsize(dest_path)
                if size > 0 and size == prev_size:
                    stable_count += 1
                    if stable_count >= 3:
                        break
                else:
                    stable_count = 0
                prev_size = size
            except:
                pass
        return dest_path

    def fetch_contacts(self, progress_cb=None):
        addr, name = self._get_connected_addr()
        if not addr:
            # Try loading from cache
            if os.path.exists(CONTACTS_CACHE):
                try:
                    with open(CONTACTS_CACHE) as f:
                        contacts = json.load(f)
                    return contacts, f"📦 Loaded {len(contacts)} contacts from cache (phone not connected)"
                except: pass
            return [], "No connected device found"
        try:
            if progress_cb: progress_cb(f"Connecting to {name}…")
            sbus, client, session, pbap = self._create_session(addr)
            pbap.Select("int", "pb")
            count = int(pbap.GetSize())
            if progress_cb: progress_cb(f"Downloading {count} contacts…")
            self._pull_and_wait(sbus, pbap, "/tmp/lp_contacts.vcf")
            if progress_cb: progress_cb("Parsing contacts…")
            contacts = self._parse_vcf("/tmp/lp_contacts.vcf")
            self.contacts = contacts
            # Save to JSON cache
            try:
                with open(CONTACTS_CACHE, 'w') as f:
                    json.dump(contacts, f, ensure_ascii=False)
            except: pass
            try: client.RemoveSession(session)
            except: pass
            return contacts, f"✅ Loaded {len(contacts)} contacts from {name}"
        except Exception as e:
            # Fallback to cache
            if os.path.exists(CONTACTS_CACHE):
                try:
                    with open(CONTACTS_CACHE) as f:
                        contacts = json.load(f)
                    return contacts, f"⚠ Error syncing, showing cached data ({len(contacts)} contacts)"
                except: pass
            return [], f"❌ Error: {str(e).split(':')[-1].strip()}"

    def fetch_call_history(self, folder="cch", progress_cb=None):
        addr, name = self._get_connected_addr()
        if not addr:
            if os.path.exists(CALLS_CACHE):
                try:
                    with open(CALLS_CACHE) as f:
                        calls = json.load(f)
                    return calls, f"📦 Loaded {len(calls)} calls from cache (phone not connected)"
                except: pass
            return [], "No connected device found"
        try:
            if progress_cb: progress_cb("Fetching call history…")
            sbus, client, session, pbap = self._create_session(addr)
            pbap.Select("int", folder)
            count = int(pbap.GetSize())
            limit = min(count, 100)
            if progress_cb: progress_cb(f"Downloading {limit} call records…")
            self._pull_and_wait(sbus, pbap, "/tmp/lp_calls.vcf")
            if progress_cb: progress_cb("Parsing call history…")
            detailed = self._parse_call_vcf("/tmp/lp_calls.vcf")
            self.call_history = detailed
            try:
                with open(CALLS_CACHE, 'w') as f:
                    json.dump(detailed, f, ensure_ascii=False)
            except: pass
            try: client.RemoveSession(session)
            except: pass
            return detailed, f"✅ Loaded {len(detailed)} recent calls"
        except Exception as e:
            if os.path.exists(CALLS_CACHE):
                try:
                    with open(CALLS_CACHE) as f:
                        calls = json.load(f)
                    return calls, f"⚠ Error syncing, showing cached data ({len(calls)} calls)"
                except: pass
            return [], f"❌ {str(e).split(':')[-1].strip()}"

    def _parse_vcf(self, filepath):
        contacts = []
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                data = f.read()
            for vcard in vobject.readComponents(data):
                name = ""
                phones = []
                try:
                    if hasattr(vcard, 'fn') and vcard.fn.value.strip():
                        name = vcard.fn.value.strip()
                    elif hasattr(vcard, 'n'):
                        n = vcard.n.value
                        parts = [n.given, n.family, n.additional]
                        name = " ".join(p for p in parts if p).strip()
                except: pass
                try:
                    for tel in vcard.contents.get('tel', []):
                        num = re.sub(r'[^\d+\-\s\(\)]', '', str(tel.value)).strip()
                        if num and num not in phones:
                            phones.append(num)
                except: pass
                if name or phones:
                    contacts.append({
                        "name": name or (phones[0] if phones else "Unknown"),
                        "phones": phones,
                        "initial": (name[0].upper() if name else "#")
                    })
        except Exception as e:
            pass
        # Remove duplicates by name+phone, sort
        seen = set()
        unique = []
        for c in contacts:
            key = c["name"] + (c["phones"][0] if c["phones"] else "")
            if key not in seen:
                seen.add(key)
                unique.append(c)
        unique.sort(key=lambda c: c["name"].lower())
        return unique

    def _parse_call_vcf(self, filepath):
        calls = []
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                data = f.read()
            for vcard in vobject.readComponents(data):
                name = ""
                number = ""
                call_type = "unknown"
                call_time = ""
                try:
                    if hasattr(vcard, 'fn'):
                        name = vcard.fn.value.strip()
                    for tel in vcard.contents.get('tel', []):
                        number = re.sub(r'[^\d+\-\s]', '', str(tel.value)).strip()
                        # Check X-IRMC-CALL-DATETIME
                    for key, vals in vcard.contents.items():
                        if 'call-datetime' in key.lower() or 'x-irmc' in key.lower():
                            for v in vals:
                                raw = str(v.value)
                                # MISSED/RECEIVED/DIALED tag
                                params = str(getattr(v, 'params', {}))
                                if 'MISSED' in params.upper(): call_type = 'missed'
                                elif 'RECEIVED' in params.upper(): call_type = 'incoming'
                                elif 'DIALED' in params.upper(): call_type = 'outgoing'
                                call_time = raw[:15] if raw else ""
                except: pass
                if number or name:
                    calls.append({
                        "name": name or number or "Unknown",
                        "number": number,
                        "type": call_type,
                        "time": call_time
                    })
        except: pass
        return calls


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

    def _watch_calls(self):
        """Subscribe to oFono VoiceCallManager.CallAdded signal on system bus"""
        try:
            self._bus.add_signal_receiver(
                self._on_call_added,
                signal_name="CallAdded",
                dbus_interface="org.ofono.VoiceCallManager",
                bus_name="org.ofono",
                path_keyword="sender_path"
            )
        except Exception as e:
            print(f"[CallManager] Could not subscribe to CallAdded: {e}")

    def _on_call_added(self, call_path, props, sender_path=None):
        """Fires when oFono announces a new call (incoming or outgoing)"""
        state = str(props.get("State", ""))
        line_id = str(props.get("LineIdentification", "Unknown"))
        name = str(props.get("Name", ""))
        display = name if name else line_id
        if state == "incoming" and self._incoming_cb:
            GLib.idle_add(self._incoming_cb, display, str(call_path))

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


# ══════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════

class LinuxPhoneWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="LinuxPhone")
        self.set_default_size(900, 650)
        self.set_size_request(700, 500)

        self.bt = BluetoothManager()
        self.pbap = PBAPFetcher(self.bt)
        self.calls = CallManager()
        self.all_contacts = []
        self._call_timer_id = None

        self._build_ui()
        GLib.timeout_add(15000, self._auto_refresh_devices)
        # Load from cache on startup — no Bluetooth needed
        GLib.idle_add(self._load_from_cache)
        # Hook incoming call popup
        self.calls.set_incoming_callback(self._show_incoming_call_popup)

    # ── UI BUILD ──────────────────────────────────────

    def _build_ui(self):
        # Top-level box
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_content(root)

        # Header bar
        hb = Adw.HeaderBar()
        hb.set_title_widget(Adw.WindowTitle(title="LinuxPhone", subtitle="Bluetooth Phone Companion"))
        root.append(hb)

        # Refresh button
        refresh_btn = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh devices")
        refresh_btn.connect("clicked", lambda _: self._refresh_devices())
        hb.pack_start(refresh_btn)

        # Scan button
        scan_btn = Gtk.Button(icon_name="bluetooth-symbolic", tooltip_text="Scan for new devices")
        scan_btn.connect("clicked", lambda _: self._start_scan())
        hb.pack_start(scan_btn)

        # Navigation split view
        self.nav_view = Adw.NavigationSplitView()
        root.append(self.nav_view)
        self.nav_view.set_vexpand(True)

        # Sidebar
        sidebar_page = Adw.NavigationPage(title="LinuxPhone")
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_page.set_child(sidebar_box)
        self.nav_view.set_sidebar(sidebar_page)

        # Sidebar list
        self.nav_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_list.connect("row-selected", self._on_nav_selected)
        sidebar_box.append(self.nav_list)

        pages = [
            ("contact-new-symbolic",               "Contacts"),
            ("call-start-symbolic",                "Recent Calls"),
            ("audio-input-microphone-symbolic",    "Dial Pad"),
            ("bluetooth-symbolic",                 "Devices"),
        ]
        for icon, label in pages:
            row = Adw.ActionRow(title=label)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.set_activatable(True)
            self.nav_list.append(row)

        # Status badge in sidebar
        self.status_label = Gtk.Label(label="", css_classes=["caption", "dim-label"],
                                      xalign=0, margin_start=12, margin_bottom=8, wrap=True)
        sidebar_box.append(self.status_label)

        # Content area (stack)
        content_page = Adw.NavigationPage(title="Contacts & Calls")
        self.content_page = content_page
        self.nav_view.set_content(content_page)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        content_page.set_child(self.stack)

        self.stack.add_named(self._build_contacts_page(), "contacts")
        self.stack.add_named(self._build_calls_page(), "calls")
        self.stack.add_named(self._build_dialpad_page(), "dialpad")
        self.stack.add_named(self._build_devices_page(), "devices")

        self.stack.set_visible_child_name("contacts")
        self.nav_list.select_row(self.nav_list.get_row_at_index(0))

        self._refresh_devices()

    # ── CONTACTS PAGE ─────────────────────────────────

    def _build_contacts_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Toolbar
        tb = Adw.ToolbarView()
        box.append(tb)
        tb.set_vexpand(True)

        top_bar = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        top_bar.set_title_widget(Gtk.Label(label="Contacts", css_classes=["heading"]))
        tb.add_top_bar(top_bar)

        # Sync button
        sync_btn = Gtk.Button(label="Sync Contacts", icon_name="emblem-synchronizing-symbolic",
                              css_classes=["suggested-action"], margin_end=6)
        sync_btn.connect("clicked", lambda _: self._fetch_contacts())
        top_bar.pack_end(sync_btn)

        # Widgets (not yet parented)
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search contacts…",
                                            margin_start=12, margin_end=12,
                                            margin_top=8, margin_bottom=8)
        self.search_entry.connect("search-changed", self._on_search)

        self.contacts_list = Gtk.ListBox(css_classes=["boxed-list"],
                                         margin_start=12, margin_end=12, margin_bottom=12)
        self.contacts_list.set_filter_func(self._filter_contact)
        self.contacts_list.set_selection_mode(Gtk.SelectionMode.NONE)

        self.contacts_empty = Adw.StatusPage(
            title="No Contacts",
            description="Connect your phone and tap \"Sync Contacts\"",
            icon_name="contact-new-symbolic",
            vexpand=True
        )
        self.contacts_empty.set_visible(True)
        self.contacts_list.set_visible(False)

        self.call_banner = Adw.Banner(title="📞 Calling…", button_label="End Call", revealed=False)
        self.call_banner.connect("button-clicked", lambda _: self._end_call())

        # ToastOverlay as the toolbar's content (single parent)
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_vexpand(True)
        tb.set_content(self.toast_overlay)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.toast_overlay.set_child(inner)
        inner.append(self.call_banner)
        inner.append(self.search_entry)

        sw = Gtk.ScrolledWindow(vexpand=True)
        inner.append(sw)
        sw.set_child(self.contacts_list)
        inner.append(self.contacts_empty)

        return box

    def _escape(self, text):
        """Escape special XML/markup characters for GTK labels"""
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def _make_contact_row(self, contact):
        row = Adw.ActionRow()
        row.set_title(self._escape(contact["name"] or "Unknown"))

        phones = contact["phones"]
        if phones:
            row.set_subtitle(self._escape(phones[0]))
        row.set_activatable(True)

        # Avatar with initial
        avatar = Adw.Avatar(size=40, text=contact["name"], show_initials=True)
        row.add_prefix(avatar)

        # Call button
        if phones:
            call_btn = Gtk.Button(icon_name="call-start-symbolic",
                                  css_classes=["flat", "circular"],
                                  tooltip_text=f"Call {phones[0]}",
                                  valign=Gtk.Align.CENTER)
            call_btn.connect("clicked", lambda _, p=phones[0]: self._make_call(p))
            row.add_suffix(call_btn)

        # If multiple phones, add a menu
        if len(phones) > 1:
            menu_btn = Gtk.MenuButton(icon_name="view-more-symbolic",
                                      css_classes=["flat", "circular"],
                                      valign=Gtk.Align.CENTER)
            menu = Gio.Menu()
            for p in phones:
                menu.append(p, f"app.call::{p}")
            menu_btn.set_menu_model(menu)
            row.add_suffix(menu_btn)

        row._contact = contact
        return row


    # ── RECENT CALLS PAGE ─────────────────────────────

    def _build_calls_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        tb = Adw.ToolbarView()
        box.append(tb)
        tb.set_vexpand(True)

        top_bar = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        top_bar.set_title_widget(Gtk.Label(label="Recent Calls", css_classes=["heading"]))
        tb.add_top_bar(top_bar)

        # Sync calls button
        sync_btn = Gtk.Button(label="Sync Calls", icon_name="emblem-synchronizing-symbolic",
                              css_classes=["suggested-action"], margin_end=6)
        sync_btn.connect("clicked", lambda _: self._fetch_calls())
        top_bar.pack_end(sync_btn)

        # Filter tabs: All / Incoming / Outgoing / Missed
        filter_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6,
                             margin_start=12, margin_end=12, margin_top=8, margin_bottom=4,
                             homogeneous=True)
        self.call_filter_btns = {}
        for key, label in [("all","All"), ("incoming","Incoming"), ("outgoing","Outgoing"), ("missed","Missed")]:
            btn = Gtk.ToggleButton(label=label, css_classes=["flat"])
            btn.connect("toggled", self._on_call_filter_toggled, key)
            filter_box.append(btn)
            self.call_filter_btns[key] = btn
        self.call_filter_btns["all"].set_active(True)
        self._active_call_filter = "all"

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        content.append(filter_box)
        tb.set_content(content)

        sw = Gtk.ScrolledWindow(vexpand=True)
        content.append(sw)

        self.calls_list = Gtk.ListBox(css_classes=["boxed-list"],
                                      margin_start=12, margin_end=12,
                                      margin_top=4, margin_bottom=12)
        self.calls_list.set_filter_func(self._filter_call_row)
        self.calls_list.set_selection_mode(Gtk.SelectionMode.NONE)
        sw.set_child(self.calls_list)

        # Empty state
        self.calls_empty = Adw.StatusPage(
            title="No Call History",
            description="Connect your phone and tap \"Sync Calls\"",
            icon_name="call-start-symbolic",
            vexpand=True
        )
        content.append(self.calls_empty)
        self.calls_empty.set_visible(True)
        self.calls_list.set_visible(False)

        return box

    def _on_call_filter_toggled(self, btn, key):
        if not btn.get_active():
            return
        # Untoggle others
        for k, b in self.call_filter_btns.items():
            if k != key:
                b.handler_block_by_func(self._on_call_filter_toggled)
                b.set_active(False)
                b.handler_unblock_by_func(self._on_call_filter_toggled)
        self._active_call_filter = key
        if hasattr(self, 'calls_list'):
            self.calls_list.invalidate_filter()

    def _filter_call_row(self, row):
        f = self._active_call_filter
        if f == "all": return True
        call = getattr(row, '_call', None)
        if not call: return True
        return call.get("type", "") == f

    def _make_call_row(self, call):
        row = Adw.ActionRow()
        name = self._escape(call.get("name") or call.get("number") or "Unknown")
        number = self._escape(call.get("number", ""))
        row.set_title(name)

        # Subtitle: type + time
        ctype = call.get("type", "unknown")
        ctime = call.get("time", "")
        # Format time if available (YYYYMMDDTHHMMSS)
        time_str = ""
        if ctime and len(ctime) >= 8:
            try:
                dt = datetime.strptime(ctime[:15], "%Y%m%dT%H%M%S")
                time_str = dt.strftime("%d %b, %I:%M %p")
            except:
                time_str = ctime[:15]
        subtitle = time_str if time_str else number
        row.set_subtitle(subtitle)

        # Type icon prefix
        if ctype == "incoming":
            icon_name = "call-start-symbolic"
            color_class = "success"
            label_text = "↙ Incoming"
        elif ctype == "outgoing":
            icon_name = "call-start-symbolic"
            color_class = "accent"
            label_text = "↗ Outgoing"
        elif ctype == "missed":
            icon_name = "call-missed-symbolic"
            color_class = "error"
            label_text = "✗ Missed"
        else:
            icon_name = "call-start-symbolic"
            color_class = "dim-label"
            label_text = "Call"

        type_label = Gtk.Label(label=label_text,
                               css_classes=["caption", color_class],
                               xalign=0)
        icon = Gtk.Image(icon_name=icon_name, css_classes=[color_class])
        prefix_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             valign=Gtk.Align.CENTER, spacing=2)
        prefix_box.append(icon)
        row.add_prefix(prefix_box)

        # Call back button
        num = call.get("number", "")
        if num:
            cb_btn = Gtk.Button(icon_name="call-start-symbolic",
                                css_classes=["flat", "circular"],
                                tooltip_text=f"Call back {num}",
                                valign=Gtk.Align.CENTER)
            cb_btn.connect("clicked", lambda _, n=num: self._make_call(n))
            row.add_suffix(cb_btn)

        row._call = call
        return row

    def _fetch_calls(self):
        self._toast("Fetching call history…")
        def do():
            calls, msg = self.pbap.fetch_call_history(progress_cb=lambda m: GLib.idle_add(self._toast, m))
            GLib.idle_add(self._load_calls_ui, calls, msg)
        threading.Thread(target=do, daemon=True).start()

    def _load_calls_ui(self, calls, msg):
        while True:
            child = self.calls_list.get_first_child()
            if child is None: break
            self.calls_list.remove(child)

        if calls:
            for c in calls:
                row = self._make_call_row(c)
                self.calls_list.append(row)
            self.calls_list.set_visible(True)
            self.calls_empty.set_visible(False)
        else:
            self.calls_list.set_visible(False)
            self.calls_empty.set_visible(True)
        self._toast(msg)

    # ── DIAL PAD PAGE ─────────────────────────────────

    def _build_dialpad_page(self):
        tb = Adw.ToolbarView()
        top_bar = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        top_bar.set_title_widget(Gtk.Label(label="Dial Pad", css_classes=["heading"]))
        tb.add_top_bar(top_bar)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0,
                        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                        vexpand=True, margin_top=20, margin_bottom=20)
        tb.set_content(outer)

        # Number display
        self.dial_entry = Gtk.Entry(css_classes=["title-2"],
                                    xalign=0.5, editable=True,
                                    width_chars=16, max_length=20,
                                    margin_bottom=16)
        self.dial_entry.set_placeholder_text("Enter number…")
        outer.append(self.dial_entry)

        # Keypad grid
        grid = Gtk.Grid(row_spacing=8, column_spacing=8, halign=Gtk.Align.CENTER)
        outer.append(grid)

        keys = [
            ("1",""),  ("2","ABC"), ("3","DEF"),
            ("4","GHI"),("5","JKL"),("6","MNO"),
            ("7","PQRS"),("8","TUV"),("9","WXYZ"),
            ("*",""),  ("0","+"),  ("#",""),
        ]
        for i, (digit, sub) in enumerate(keys):
            btn = Gtk.Button(css_classes=["pill"], width_request=72, height_request=60)
            vb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER)
            vb.append(Gtk.Label(label=digit, css_classes=["title-3"]))
            if sub:
                vb.append(Gtk.Label(label=sub, css_classes=["caption", "dim-label"]))
            btn.set_child(vb)
            btn.connect("clicked", lambda _, d=digit: self._dial_key(d))
            grid.attach(btn, i % 3, i // 3, 1, 1)

        # Action buttons row
        action_row = Gtk.Box(spacing=12, halign=Gtk.Align.CENTER, margin_top=16)
        outer.append(action_row)

        # Backspace
        bs_btn = Gtk.Button(icon_name="edit-clear-symbolic",
                            css_classes=["circular", "flat"],
                            width_request=56, height_request=56)
        bs_btn.connect("clicked", lambda _: self._dial_backspace())
        action_row.append(bs_btn)

        # Call button
        self.call_btn = Gtk.Button(icon_name="call-start-symbolic",
                                   css_classes=["circular", "suggested-action"],
                                   width_request=72, height_request=72)
        self.call_btn.connect("clicked", lambda _: self._dial_call())
        action_row.append(self.call_btn)

        # End call button (hidden by default)
        self.end_btn = Gtk.Button(icon_name="call-stop-symbolic",
                                  css_classes=["circular", "destructive-action"],
                                  width_request=72, height_request=72,
                                  visible=False)
        self.end_btn.connect("clicked", lambda _: self._end_call())
        action_row.append(self.end_btn)

        # Call status label
        self.call_status_label = Gtk.Label(label="", css_classes=["caption"],
                                           margin_top=8)
        outer.append(self.call_status_label)

        return tb

    # ── DEVICES PAGE ──────────────────────────────────

    def _build_devices_page(self):
        tb = Adw.ToolbarView()
        top_bar = Adw.HeaderBar(show_start_title_buttons=False, show_end_title_buttons=False)
        top_bar.set_title_widget(Gtk.Label(label="Bluetooth Devices", css_classes=["heading"]))
        tb.add_top_bar(top_bar)

        sw = Gtk.ScrolledWindow(vexpand=True)
        tb.set_content(sw)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                      margin_start=12, margin_end=12, margin_top=12, margin_bottom=12)
        sw.set_child(box)

        # Adapter info card
        self.adapter_group = Adw.PreferencesGroup(title="Bluetooth Adapter")
        box.append(self.adapter_group)

        self.adapter_row = Adw.ActionRow(title="Loading…")
        self.adapter_group.add(self.adapter_row)

        # Devices list
        self.devices_group = Adw.PreferencesGroup(title="Paired Devices")
        box.append(self.devices_group)

        self.device_rows = {}
        return tb


    # ── LOGIC ─────────────────────────────────────────

    def _on_nav_selected(self, listbox, row):
        if row is None: return
        idx = row.get_index()
        pages = ["contacts", "calls", "dialpad", "devices"]
        titles = ["Contacts", "Recent Calls", "Dial Pad", "Devices"]
        if idx < len(pages):
            self.stack.set_visible_child_name(pages[idx])
            self.content_page.set_title(titles[idx])

    def _refresh_devices(self):
        devs = self.bt.get_devices()
        info = self.bt.adapter_info()
        GLib.idle_add(self._update_devices_ui, devs, info)
        return True

    def _auto_refresh_devices(self):
        threading.Thread(target=self._refresh_devices, daemon=True).start()
        return True  # repeat

    def _update_devices_ui(self, devs, info):
        # Update adapter row
        name = info.get("name", "Unknown")
        addr = info.get("address", "?")
        powered = "🟢 On" if info.get("powered") else "🔴 Off"
        self.adapter_row.set_title(name)
        self.adapter_row.set_subtitle(f"{addr}  •  {powered}")

        # Remove old device rows
        for row in list(self.device_rows.values()):
            self.devices_group.remove(row)
        self.device_rows.clear()

        connected_names = []
        for addr, dev in devs.items():
            row = Adw.ActionRow(title=self._escape(dev["name"]))
            status = "🔗 Connected" if dev["connected"] else ("✅ Paired" if dev["paired"] else "🔵 Nearby")
            row.set_subtitle(f"{addr}  •  {status}")

            icon = Gtk.Image(icon_name="phone-symbolic" if dev["connected"] else "bluetooth-symbolic")
            row.add_prefix(icon)

            if dev["connected"]:
                dc_btn = Gtk.Button(label="Disconnect", css_classes=["flat", "destructive-action"],
                                    valign=Gtk.Align.CENTER)
                dc_btn.connect("clicked", lambda _, a=addr: self._dev_disconnect(a))
                row.add_suffix(dc_btn)
                connected_names.append(dev["name"])
            elif dev["paired"]:
                con_btn = Gtk.Button(label="Connect", css_classes=["flat", "suggested-action"],
                                     valign=Gtk.Align.CENTER)
                con_btn.connect("clicked", lambda _, a=addr: self._dev_connect(a))
                row.add_suffix(con_btn)

            self.devices_group.add(row)
            self.device_rows[addr] = row

        if connected_names:
            self.status_label.set_text("Connected: " + ", ".join(connected_names))
        else:
            self.status_label.set_text("No devices connected")

    def _dev_connect(self, addr):
        def do():
            ok, msg = self.bt.connect(addr)
            GLib.idle_add(self._toast, msg)
            self._refresh_devices()
        threading.Thread(target=do, daemon=True).start()

    def _dev_disconnect(self, addr):
        def do():
            ok, msg = self.bt.disconnect(addr)
            GLib.idle_add(self._toast, msg)
            self._refresh_devices()
        threading.Thread(target=do, daemon=True).start()

    def _start_scan(self):
        self._toast("Scanning for 8 seconds…")
        def do():
            self.bt.scan(8)
            GLib.idle_add(self._toast, "Scan complete")
            self._refresh_devices()
        threading.Thread(target=do, daemon=True).start()

    # ── CONTACTS ──────────────────────────────────────

    def _load_from_cache(self):
        """On startup: silently load contacts + calls from JSON cache if available"""
        if os.path.exists(CONTACTS_CACHE):
            try:
                with open(CONTACTS_CACHE) as f:
                    contacts = json.load(f)
                if contacts:
                    self._load_contacts_ui(contacts, f"📦 {len(contacts)} contacts (cached)")
            except: pass
        if os.path.exists(CALLS_CACHE):
            try:
                with open(CALLS_CACHE) as f:
                    calls = json.load(f)
                if calls:
                    self._load_calls_ui(calls, f"📦 {len(calls)} calls (cached)")
            except: pass
        return False  # run once

    def _fetch_contacts(self):
        self._toast("Connecting to phonebook…")
        def do():
            contacts, msg = self.pbap.fetch_contacts(
                progress_cb=lambda m: GLib.idle_add(self._toast, m)
            )
            GLib.idle_add(self._load_contacts_ui, contacts, msg)
        threading.Thread(target=do, daemon=True).start()

    def _fetch_calls(self):
        self._toast("Fetching call history…")
        def do():
            calls, msg = self.pbap.fetch_call_history(
                progress_cb=lambda m: GLib.idle_add(self._toast, m)
            )
            GLib.idle_add(self._load_calls_ui, calls, msg)
        threading.Thread(target=do, daemon=True).start()

    def _load_contacts_ui(self, contacts, msg):
        self.all_contacts = contacts
        self._contacts_loaded = 0

        while True:
            child = self.contacts_list.get_first_child()
            if child is None: break
            self.contacts_list.remove(child)

        if not contacts:
            self.contacts_list.set_visible(False)
            self.contacts_empty.set_visible(True)
            self._toast(msg)
            return

        self.contacts_list.set_visible(True)
        self.contacts_empty.set_visible(False)
        self._toast(msg)

        # Load first batch immediately, rest via idle
        self._append_contact_batch()

    def _append_contact_batch(self):
        """Append up to 15 contacts at a time — called via GLib.idle_add so UI stays responsive"""
        BATCH = 15
        contacts = self.all_contacts
        start = self._contacts_loaded
        end = min(start + BATCH, len(contacts))

        for c in contacts[start:end]:
            row = self._make_contact_row(c)
            self.contacts_list.append(row)

        self._contacts_loaded = end

        # If more remain, schedule next batch (idle priority = after UI events)
        if end < len(contacts):
            GLib.idle_add(self._append_contact_batch)
            return False  # don't repeat via timeout
        return False

    def _on_search(self, entry):
        self.contacts_list.invalidate_filter()

    def _filter_contact(self, row):
        query = self.search_entry.get_text().lower().strip()
        if not query: return True
        contact = getattr(row, '_contact', None)
        if not contact: return True
        return (query in contact["name"].lower() or
                any(query in p for p in contact["phones"]))

    # ── CALLS ─────────────────────────────────────────

    def _make_call(self, number):
        ok, msg = self.calls.call(number)
        self._toast(msg)
        if ok:
            self.call_banner.set_title(f"📞 Calling {number}…")
            self.call_banner.set_revealed(True)
            self.call_btn.set_visible(False)
            self.end_btn.set_visible(True)
            self.call_status_label.set_text(msg)
            self._start_call_timer()

    def _end_call(self):
        ok, msg = self.calls.hangup()
        self._toast(msg)
        self.call_banner.set_revealed(False)
        self.call_btn.set_visible(True)
        self.end_btn.set_visible(False)
        self.call_status_label.set_text("")
        if self._call_timer_id:
            GLib.source_remove(self._call_timer_id)
            self._call_timer_id = None

    def _start_call_timer(self):
        def tick():
            if self.calls.active_call:
                dur = int((datetime.now() - self.calls.active_call["start"]).total_seconds())
                m, s = divmod(dur, 60)
                self.call_status_label.set_text(f"Call duration: {m:02d}:{s:02d}")
                self.call_banner.set_title(f"📞 {self.calls.active_call['number']}  {m:02d}:{s:02d}")
                return True
            return False
        self._call_timer_id = GLib.timeout_add(1000, tick)

    def _dial_key(self, key):
        cur = self.dial_entry.get_text()
        self.dial_entry.set_text(cur + key)
        self.dial_entry.set_position(-1)

    def _dial_backspace(self):
        cur = self.dial_entry.get_text()
        self.dial_entry.set_text(cur[:-1])

    def _dial_call(self):
        number = self.dial_entry.get_text().strip()
        if number:
            self._make_call(number)

    def _toast(self, msg):
        toast = Adw.Toast(title=str(msg)[:100], timeout=3)
        try:
            self.toast_overlay.add_toast(toast)
        except: pass
        self.status_label.set_text(str(msg)[:80])

    # ── INCOMING CALL POPUP ───────────────────────────

    def _lookup_contact_name(self, number):
        """Return contact name for a number using last-10-digit matching"""
        clean = re.sub(r'[^\d]', '', number)  # only digits
        if not clean:
            return None
        suffix = clean[-10:]  # last 10 digits for comparison
        for c in getattr(self, 'all_contacts', []):
            # Skip contacts whose name looks like a number
            name = c.get('name', '')
            if not name or re.fullmatch(r'[\d\s\+\-\.\(\)]+', name):
                continue
            for p in c.get('phones', []):
                p_digits = re.sub(r'[^\d]', '', p)
                if p_digits and p_digits[-10:] == suffix:
                    return name
        return None

    def _show_incoming_call_popup(self, number, call_path):
        """Show a full-screen-style incoming call dialog with Answer/Reject"""
        # Prevent duplicate popups
        if hasattr(self, '_incoming_dialog') and self._incoming_dialog:
            try: self._incoming_dialog.close()
            except: pass

        dialog = Adw.Dialog()
        dialog.set_title("Incoming Call")
        dialog.set_content_width(340)
        dialog.set_content_height(260)
        self._incoming_dialog = dialog
        self._incoming_call_path = call_path

        # Resolve name from contacts if available
        contact_name = self._lookup_contact_name(number)
        display_name = contact_name if contact_name else number
        toolbar = Adw.ToolbarView()
        hdr = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        hdr.set_title_widget(Adw.WindowTitle(title="Incoming Call", subtitle="via Bluetooth HFP"))
        toolbar.add_top_bar(hdr)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                      vexpand=True, margin_top=16, margin_bottom=24,
                      margin_start=24, margin_end=24)
        toolbar.set_content(box)
        dialog.set_child(toolbar)

        # Avatar / icon
        avatar = Adw.Avatar(size=72, text=display_name, show_initials=True)
        box.append(avatar)

        # Number / name label
        num_label = Gtk.Label(label=self._escape(display_name),
                              css_classes=["title-2"],
                              wrap=True, justify=Gtk.Justification.CENTER)
        box.append(num_label)

        # Show number as subtitle if name was resolved
        if contact_name:
            sub_label = Gtk.Label(label=self._escape(number),
                                  css_classes=["dim-label"],
                                  wrap=True, justify=Gtk.Justification.CENTER)
            box.append(sub_label)

        # Pulsing status label
        status_lbl = Gtk.Label(label="📞 Ringing…",
                               css_classes=["dim-label"])
        box.append(status_lbl)

        # Buttons row
        btn_row = Gtk.Box(spacing=32, halign=Gtk.Align.CENTER)
        box.append(btn_row)

        # Reject (red)
        reject_btn = Gtk.Button(icon_name="call-stop-symbolic",
                                css_classes=["circular", "destructive-action"],
                                width_request=72, height_request=72,
                                tooltip_text="Reject")
        reject_lbl = Gtk.Label(label="Reject")
        reject_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                             halign=Gtk.Align.CENTER)
        reject_box.append(reject_btn)
        reject_box.append(reject_lbl)
        btn_row.append(reject_box)

        # Answer (green)
        answer_btn = Gtk.Button(icon_name="call-start-symbolic",
                                css_classes=["circular", "suggested-action"],
                                width_request=72, height_request=72,
                                tooltip_text="Answer")
        answer_lbl = Gtk.Label(label="Answer")
        answer_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4,
                             halign=Gtk.Align.CENTER)
        answer_box.append(answer_btn)
        answer_box.append(answer_lbl)
        btn_row.append(answer_box)

        def on_answer(_):
            ok, msg = self.calls.answer(self._incoming_call_path)
            self._toast(msg)
            if ok:
                self.call_banner.set_title(f"📞 {display_name}")
                self.call_banner.set_revealed(True)
                self.call_btn.set_visible(False)
                self.end_btn.set_visible(True)
                self.call_status_label.set_text(msg)
                self._start_call_timer()
            dialog.close()
            self._incoming_dialog = None

        def on_reject(_):
            ok, msg = self.calls.reject(self._incoming_call_path)
            self._toast(msg)
            dialog.close()
            self._incoming_dialog = None

        answer_btn.connect("clicked", on_answer)
        reject_btn.connect("clicked", on_reject)
        dialog.connect("closed", lambda _: setattr(self, '_incoming_dialog', None))

        dialog.present(self)
        return False  # GLib.idle_add return


# ══════════════════════════════════════════════════════
#  APPLICATION
# ══════════════════════════════════════════════════════

class LinuxPhoneApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id="io.github.linuxphone",
                         flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.connect("activate", self.on_activate)

    def on_activate(self, app):
        win = LinuxPhoneWindow(app)
        win.present()


# ── CSS ──────────────────────────────────────────────

CSS = """
.contact-avatar {
    background-color: @accent_bg_color;
    color: @accent_fg_color;
    border-radius: 50%;
}
"""

def main():
    app = LinuxPhoneApp()
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode())
    Gtk.StyleContext.add_provider_for_display(
        Gdk.Display.get_default(), provider,
        Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    app.run(None)

if __name__ == "__main__":
    main()
