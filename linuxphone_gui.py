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
import threading, subprocess, time, vobject, io, re
from datetime import datetime

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
    """Fetch contacts from phone via Bluetooth PBAP (OBEX)"""

    def __init__(self, bt: BluetoothManager):
        self.bt = bt
        self.contacts = []   # list of {"name": str, "phones": [str]}
        self._session_path = None

    def _get_connected_addr(self):
        devs = self.bt.get_devices()
        pbap_uuid = "0000112f-0000-1000-8000-00805f9b34fb"
        for addr, dev in devs.items():
            if dev["connected"] and any(pbap_uuid in u for u in dev["uuids"]):
                return addr, dev["name"]
        return None, None

    def fetch(self, progress_cb=None):
        """Fetch all contacts. Returns list of contact dicts."""
        addr, name = self._get_connected_addr()
        if not addr:
            return [], "No PBAP-capable connected device found"

        try:
            bus = dbus.SessionBus()
        except:
            try:
                bus = dbus.SystemBus()
            except Exception as e:
                return [], str(e)

        try:
            # Create OBEX session
            client = dbus.Interface(
                bus.get_object("org.bluez.obex", "/org/bluez/obex"),
                "org.bluez.obex.Client1"
            )
            if progress_cb: progress_cb("Connecting to phonebook...")

            session_path = client.CreateSession(addr, {"Target": dbus.String("pbap")})
            self._session_path = session_path

            pbap = dbus.Interface(
                bus.get_object("org.bluez.obex", session_path),
                "org.bluez.obex.PhonebookAccess1"
            )

            if progress_cb: progress_cb("Selecting phonebook...")
            pbap.Select("int", "pb")

            if progress_cb: progress_cb("Downloading contacts...")
            # Pull all contacts as vCard
            transfer_path, props = pbap.PullAll(
                "/tmp/linuxphone_contacts.vcf",
                {"Format": dbus.String("vcard30"),
                 "Fields": dbus.Array(["VERSION","FN","TEL"], signature='s')}
            )

            # Wait for transfer
            for _ in range(60):
                time.sleep(0.5)
                try:
                    t_props = dbus.Interface(
                        bus.get_object("org.bluez.obex", transfer_path),
                        "org.freedesktop.DBus.Properties"
                    ).GetAll("org.bluez.obex.Transfer1")
                    status = str(t_props.get("Status", ""))
                    if status == "complete":
                        break
                    elif status == "error":
                        return [], "Transfer failed"
                except: pass

            # Parse vCard file
            contacts = self._parse_vcf("/tmp/linuxphone_contacts.vcf")
            self.contacts = contacts

            # Cleanup session
            try: client.RemoveSession(session_path)
            except: pass

            return contacts, f"Loaded {len(contacts)} contacts from {name}"

        except dbus.DBusException as e:
            err = str(e)
            if "obex" in err.lower() or "bluez" in err.lower():
                # Fallback: use obexftp CLI
                return self._fetch_via_cli(addr, progress_cb)
            return [], f"DBus error: {err.split(':')[-1].strip()}"
        except Exception as e:
            return self._fetch_via_cli(addr, progress_cb)

    def _fetch_via_cli(self, addr, progress_cb=None):
        """Fallback: fetch via obexftp command line"""
        if progress_cb: progress_cb("Trying obexftp fallback...")
        try:
            result = subprocess.run(
                ["obexftp", "--bluetooth", addr, "--channel", "19",
                 "--get", "telecom/pb.vcf"],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0 and result.stdout:
                contacts = self._parse_vcf_string(result.stdout)
                self.contacts = contacts
                return contacts, f"Loaded {len(contacts)} contacts (CLI)"
            return [], "obexftp failed. Try: sudo apt install obexftp"
        except FileNotFoundError:
            return [], "Install obexftp: sudo apt install obexftp"
        except Exception as e:
            return [], str(e)

    def _parse_vcf(self, filepath):
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                return self._parse_vcf_string(f.read())
        except: return []

    def _parse_vcf_string(self, data):
        contacts = []
        try:
            for vcard in vobject.readComponents(data):
                name = ""
                phones = []
                try:
                    if hasattr(vcard, 'fn'):
                        name = str(vcard.fn.value).strip()
                    elif hasattr(vcard, 'n'):
                        n = vcard.n.value
                        name = f"{n.given} {n.family}".strip()
                except: pass

                try:
                    for tel in vcard.contents.get('tel', []):
                        num = re.sub(r'[^\d+\-\s]', '', str(tel.value)).strip()
                        if num: phones.append(num)
                except: pass

                if name or phones:
                    contacts.append({
                        "name": name or phones[0],
                        "phones": phones,
                        "initial": (name[0].upper() if name else "#")
                    })
        except Exception as e:
            pass

        contacts.sort(key=lambda c: c["name"].lower())
        return contacts


# ══════════════════════════════════════════════════════
#  CALL MANAGER
# ══════════════════════════════════════════════════════

class CallManager:
    def __init__(self):
        self.active_call = None
        self.call_log = []
        self._bus = dbus.SystemBus()

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
        GLib.timeout_add(3000, self._auto_refresh_devices)

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
            ("call-start-symbolic",    "Contacts & Calls"),
            ("audio-input-microphone-symbolic", "Dial Pad"),
            ("bluetooth-symbolic",     "Devices"),
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
        top_bar.set_title_widget(Gtk.Label(label="Contacts & Calls", css_classes=["heading"]))
        tb.add_top_bar(top_bar)

        # Sync button
        sync_btn = Gtk.Button(label="Sync Contacts", icon_name="emblem-synchronizing-symbolic",
                              css_classes=["suggested-action"], margin_end=6)
        sync_btn.connect("clicked", lambda _: self._fetch_contacts())
        top_bar.pack_end(sync_btn)

        # Content
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        tb.set_content(content)

        # Search bar
        self.search_entry = Gtk.SearchEntry(placeholder_text="Search contacts…",
                                            margin_start=12, margin_end=12,
                                            margin_top=8, margin_bottom=8)
        self.search_entry.connect("search-changed", self._on_search)
        content.append(self.search_entry)

        # Contacts list in scrolled window
        sw = Gtk.ScrolledWindow(vexpand=True)
        content.append(sw)

        self.contacts_list = Gtk.ListBox(css_classes=["boxed-list"],
                                         margin_start=12, margin_end=12, margin_bottom=12)
        self.contacts_list.set_filter_func(self._filter_contact)
        self.contacts_list.set_selection_mode(Gtk.SelectionMode.NONE)
        sw.set_child(self.contacts_list)

        # Empty state
        self.contacts_empty = Adw.StatusPage(
            title="No Contacts",
            description="Connect your phone and tap \"Sync Contacts\"",
            icon_name="contact-new-symbolic",
            vexpand=True
        )
        content.append(self.contacts_empty)
        self.contacts_empty.set_visible(True)
        self.contacts_list.set_visible(False)

        # Active call banner
        self.call_banner = Adw.Banner(title="📞 Calling…", button_label="End Call",
                                      revealed=False)
        self.call_banner.connect("button-clicked", lambda _: self._end_call())
        content.prepend(self.call_banner)

        # Toast overlay wrapping content
        self.toast_overlay = Adw.ToastOverlay()
        self.toast_overlay.set_child(content)
        self.toast_overlay.set_vexpand(True)
        box.append(self.toast_overlay)

        return box

    def _make_contact_row(self, contact):
        row = Adw.ActionRow()
        row.set_title(contact["name"] or "Unknown")

        phones = contact["phones"]
        if phones:
            row.set_subtitle(phones[0])
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
        pages = ["contacts", "dialpad", "devices"]
        titles = ["Contacts & Calls", "Dial Pad", "Devices"]
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
            row = Adw.ActionRow(title=dev["name"])
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

    def _fetch_contacts(self):
        self._toast("Connecting to phonebook…")
        def do():
            contacts, msg = self.pbap.fetch(
                progress_cb=lambda m: GLib.idle_add(self._toast, m)
            )
            GLib.idle_add(self._load_contacts_ui, contacts, msg)
        threading.Thread(target=do, daemon=True).start()

    def _load_contacts_ui(self, contacts, msg):
        self.all_contacts = contacts
        # Clear old rows
        while True:
            child = self.contacts_list.get_first_child()
            if child is None: break
            self.contacts_list.remove(child)

        if contacts:
            for c in contacts:
                row = self._make_contact_row(c)
                self.contacts_list.append(row)
            self.contacts_list.set_visible(True)
            self.contacts_empty.set_visible(False)
        else:
            self.contacts_list.set_visible(False)
            self.contacts_empty.set_visible(True)

        self._toast(msg)

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
