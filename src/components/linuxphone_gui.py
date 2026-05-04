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
from src.components.bluetooth_manager import BluetoothManager
from src.components.pba_fetcher import PBAPFetcher
from src.components.call_manager import CallManager
from src.components.battery_monitor import BatteryMonitor
from src.components.media_controller import MediaController
from src.components.sms_manager import SmsManager
from src.constants import CONTACTS_CACHE, CALLS_CACHE, SMS_CACHE
dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)


# ══════════════════════════════════════════════════════
#  MAIN WINDOW
# ══════════════════════════════════════════════════════

class LinuxPhoneWindow(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="LinuxPhone")
        self.set_default_size(1000, 680)

        self.bt = BluetoothManager()
        self.pbap = PBAPFetcher(self.bt)
        self.calls = CallManager()
        self.battery = BatteryMonitor()
        self.media = MediaController()
        self.sms = SmsManager()
        self.all_contacts = []
        self._call_timer_id = None
        self._sms_threads = []          # list of {sender, body, time}

        self._build_ui()
        GLib.timeout_add(15000, self._auto_refresh_devices)
        GLib.idle_add(self._load_from_cache)
        self.calls.set_incoming_callback(self._show_incoming_call_popup)
        # Battery: poll every 30 s
        self.battery.add_callback(self._on_battery_update, self._on_signal_update)
        GLib.timeout_add(30000, self._poll_battery)
        GLib.idle_add(self._poll_battery)
        # Media: poll every 2 s
        self.media.add_track_callback(self._on_track_update)
        self.media.add_status_callback(self._on_media_status)
        GLib.timeout_add(2000, self._poll_media)
        GLib.idle_add(self._poll_media)
        # SMS incoming
        self.sms.set_incoming_callback(self._on_sms_incoming)

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

        # ── Sidebar ──────────────────────────────────
        sidebar_page = Adw.NavigationPage(title="LinuxPhone")
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_page.set_child(sidebar_box)
        self.nav_view.set_sidebar(sidebar_page)

        # --- Device status card ---
        self._device_card = self._build_device_card()
        sidebar_box.append(self._device_card)

        # Nav list
        self.nav_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.nav_list.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.nav_list.connect("row-selected", self._on_nav_selected)
        sidebar_box.append(self.nav_list)

        pages = [
            ("contact-new-symbolic",            "Contacts"),
            ("call-start-symbolic",             "Recent Calls"),
            ("audio-input-microphone-symbolic", "Dial Pad"),
            ("message-new-symbolic",            "Messages"),
            ("bluetooth-symbolic",              "Devices"),
        ]
        for icon, label in pages:
            row = Adw.ActionRow(title=label)
            row.add_prefix(Gtk.Image(icon_name=icon))
            row.set_activatable(True)
            self.nav_list.append(row)

        # --- Media player card ---
        self._media_card = self._build_media_card()
        sidebar_box.append(self._media_card)

        # Status label
        self.status_label = Gtk.Label(label="", css_classes=["caption", "dim-label"],
                                      xalign=0, margin_start=12, margin_bottom=8, wrap=True)
        sidebar_box.append(self.status_label)

        # ── Content area (stack) ───────────────────
        content_page = Adw.NavigationPage(title="Contacts & Calls")
        self.content_page = content_page
        self.nav_view.set_content(content_page)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        content_page.set_child(self.stack)

        self.stack.add_named(self._build_contacts_page(), "contacts")
        self.stack.add_named(self._build_calls_page(), "calls")
        self.stack.add_named(self._build_dialpad_page(), "dialpad")
        self.stack.add_named(self._build_messages_page(), "messages")
        self.stack.add_named(self._build_devices_page(), "devices")

        self.stack.set_visible_child_name("contacts")
        self.nav_list.select_row(self.nav_list.get_row_at_index(0))

        self._refresh_devices()

    # ── DEVICE CARD (sidebar) ─────────────────────────

    def _build_device_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       spacing=4, css_classes=["device-card"])

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        card.append(top)

        # Phone icon
        phone_icon = Gtk.Image(icon_name="phone-symbolic", pixel_size=28)
        top.append(phone_icon)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1,
                           hexpand=True)
        top.append(info_box)

        self._dev_name_lbl = Gtk.Label(label="No Device",
                                       css_classes=["device-name"],
                                       xalign=0)
        info_box.append(self._dev_name_lbl)

        self._dev_status_lbl = Gtk.Label(label="Not connected",
                                         css_classes=["device-sub"],
                                         xalign=0)
        info_box.append(self._dev_status_lbl)

        # Battery badge
        self._battery_lbl = Gtk.Label(label="",
                                      css_classes=["battery-badge"],
                                      visible=False)
        top.append(self._battery_lbl)

        # Signal + carrier row
        self._signal_lbl = Gtk.Label(label="", css_classes=["signal-badge"],
                                     xalign=0, visible=False)
        card.append(self._signal_lbl)

        return card

    # ── MEDIA CARD (sidebar) ──────────────────────────

    def _build_media_card(self):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                       spacing=6, css_classes=["media-card"],
                       visible=False)
        self._media_card_box = card

        # Track info
        self._media_title_lbl = Gtk.Label(label="Not Playing",
                                          css_classes=["media-title"],
                                          xalign=0,
                                          ellipsize=Pango.EllipsizeMode.END,
                                          max_width_chars=22)
        card.append(self._media_title_lbl)

        self._media_artist_lbl = Gtk.Label(label="",
                                           css_classes=["media-artist"],
                                           xalign=0,
                                           ellipsize=Pango.EllipsizeMode.END,
                                           max_width_chars=22)
        card.append(self._media_artist_lbl)

        # Controls row
        btn_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=6, halign=Gtk.Align.CENTER)
        card.append(btn_row)

        prev_btn = Gtk.Button(icon_name="media-skip-backward-symbolic",
                              css_classes=["flat", "media-btn"],
                              tooltip_text="Previous")
        prev_btn.connect("clicked", lambda _: self.media.previous_track())
        btn_row.append(prev_btn)

        self._play_pause_btn = Gtk.Button(icon_name="media-playback-start-symbolic",
                                          css_classes=["flat", "media-play-btn"],
                                          tooltip_text="Play / Pause")
        self._play_pause_btn.connect("clicked", lambda _: self.media.play_pause())
        btn_row.append(self._play_pause_btn)

        next_btn = Gtk.Button(icon_name="media-skip-forward-symbolic",
                              css_classes=["flat", "media-btn"],
                              tooltip_text="Next")
        next_btn.connect("clicked", lambda _: self.media.next_track())
        btn_row.append(next_btn)

        return card


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
        num  = call.get("number", "")
        row.set_title(name)

        # Subtitle: formatted time
        ctype = call.get("type", "unknown")
        ctime = call.get("time", "")
        time_str = ""
        if ctime and len(ctime) >= 8:
            try:
                dt = datetime.strptime(ctime[:15], "%Y%m%dT%H%M%S")
                time_str = dt.strftime("%d %b, %I:%M %p")
            except:
                time_str = ctime[:15]
        row.set_subtitle(time_str if time_str else self._escape(num))

        # Color + icon based on call type
        if ctype == "incoming":
            icon_name   = "call-start-symbolic"
            color_class = "success"       # green
            label_text  = "↙ Incoming"
        elif ctype == "outgoing":
            icon_name   = "call-start-symbolic"
            color_class = "accent"        # blue/accent
            label_text  = "↗ Outgoing"
        elif ctype == "missed":
            icon_name   = "call-missed-symbolic"
            color_class = "error"         # red
            label_text  = "✕ Missed"
        else:
            icon_name   = "call-start-symbolic"
            color_class = "dim-label"
            label_text  = "Call"

        # Prefix: colored icon above type label
        icon = Gtk.Image(icon_name=icon_name, css_classes=[color_class],
                         pixel_size=18)
        type_lbl = Gtk.Label(label=label_text,
                             css_classes=["caption", color_class],
                             xalign=0.5)
        prefix_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             valign=Gtk.Align.CENTER, spacing=1,
                             width_request=68)
        prefix_box.append(icon)
        prefix_box.append(type_lbl)
        row.add_prefix(prefix_box)

        # Call-back button (suffix)
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
        pages  = ["contacts", "calls", "dialpad", "messages", "devices"]
        titles = ["Contacts", "Recent Calls", "Dial Pad", "Messages", "Devices"]
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
            self._dev_name_lbl.set_text(connected_names[0])
            self._dev_status_lbl.set_text("● Connected")
        else:
            self.status_label.set_text("No devices connected")
            self._dev_name_lbl.set_text("No Device")
            self._dev_status_lbl.set_text("Not connected")
            self._battery_lbl.set_visible(False)
            self._signal_lbl.set_visible(False)
            self._media_card_box.set_visible(False)

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
            contact_name = self._lookup_contact_name(number)
            display_name = contact_name if contact_name else number
            self._show_in_call_dialog(display_name, number, outgoing=True)

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
        # Close in-call dialog if open
        if hasattr(self, '_incall_dialog') and self._incall_dialog:
            try: self._incall_dialog.close()
            except: pass
            self._incall_dialog = None

    def _show_in_call_dialog(self, display_name, number, outgoing=True):
        """Show a persistent in-call popup with timer and hangup button"""
        # Close any existing one
        if hasattr(self, '_incall_dialog') and self._incall_dialog:
            try: self._incall_dialog.close()
            except: pass

        dialog = Adw.Dialog()
        dialog.set_title("In Call")
        dialog.set_content_width(320)
        dialog.set_content_height(300)
        dialog.set_follows_content_size(False)
        self._incall_dialog = dialog

        toolbar = Adw.ToolbarView()
        hdr = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        hdr.set_title_widget(Adw.WindowTitle(
            title="In Call",
            subtitle="via Bluetooth HFP"
        ))
        toolbar.add_top_bar(hdr)
        dialog.set_child(toolbar)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16,
                      halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                      vexpand=True,
                      margin_top=16, margin_bottom=28,
                      margin_start=24, margin_end=24)
        toolbar.set_content(box)

        # Avatar
        avatar = Adw.Avatar(size=80, text=display_name, show_initials=True)
        box.append(avatar)

        # Name
        name_lbl = Gtk.Label(label=self._escape(display_name),
                             css_classes=["title-2"],
                             wrap=True, justify=Gtk.Justification.CENTER)
        box.append(name_lbl)

        # Show number under name if name was resolved
        if display_name != number:
            num_lbl = Gtk.Label(label=self._escape(number),
                                css_classes=["dim-label"],
                                justify=Gtk.Justification.CENTER)
            box.append(num_lbl)

        # Status / timer label
        status_lbl = Gtk.Label(
            label="📞 Calling…" if outgoing else "📞 Connected",
            css_classes=["caption", "dim-label"]
        )
        box.append(status_lbl)

        # Timer label (big)
        timer_lbl = Gtk.Label(label="00:00", css_classes=["title-1"])
        timer_lbl.set_visible(not outgoing)  # hide timer until connected
        box.append(timer_lbl)

        # Hangup button
        end_btn = Gtk.Button(icon_name="call-stop-symbolic",
                             css_classes=["circular", "destructive-action"],
                             width_request=80, height_request=80)
        end_lbl = Gtk.Label(label="End Call", css_classes=["caption"])
        end_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6,
                          halign=Gtk.Align.CENTER)
        end_box.append(end_btn)
        end_box.append(end_lbl)
        box.append(end_box)

        call_start = datetime.now()

        def tick():
            if not (hasattr(self, '_incall_dialog') and self._incall_dialog):
                return False
            elapsed = int((datetime.now() - call_start).total_seconds())
            m, s = divmod(elapsed, 60)
            timer_lbl.set_text(f"{m:02d}:{s:02d}")
            timer_lbl.set_visible(True)
            status_lbl.set_text("📞 Connected")
            return True

        timer_id = GLib.timeout_add(1000, tick)

        def on_hangup(_):
            GLib.source_remove(timer_id)
            self._end_call()

        def on_closed(_):
            self._incall_dialog = None
            try: GLib.source_remove(timer_id)
            except: pass

        end_btn.connect("clicked", on_hangup)
        dialog.connect("closed", on_closed)
        dialog.present(self)

    def _start_call_timer(self):
        # Legacy — kept for compatibility, real timer now lives in _show_in_call_dialog
        pass

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

    # ── RINGTONE ───────────────────────────────────────

    RING_SOUND = "/usr/share/sounds/freedesktop/stereo/phone-incoming-call.oga"

    def _play_ringtone(self):
        """Play system ringtone in a loop until _stop_ringtone() is called."""
        self._ring_proc = None
        self._ring_stop = False
        def _loop():
            while not self._ring_stop:
                try:
                    proc = subprocess.Popen(
                        ["paplay", self.RING_SOUND],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL
                    )
                    self._ring_proc = proc
                    proc.wait()
                except Exception:
                    break
        threading.Thread(target=_loop, daemon=True).start()

    def _stop_ringtone(self):
        """Stop the ringtone loop."""
        self._ring_stop = True
        if hasattr(self, '_ring_proc') and self._ring_proc:
            try: self._ring_proc.terminate()
            except: pass
            self._ring_proc = None

    def _show_incoming_call_popup(self, number, call_path):
        """Show a full-screen-style incoming call dialog with Answer/Reject"""
        # Prevent duplicate popups
        if hasattr(self, '_incoming_dialog') and self._incoming_dialog:
            try: self._incoming_dialog.close()
            except: pass

        # 🔔 Start ringtone
        self._play_ringtone()

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
            self._stop_ringtone()               # 🔕 stop ring
            ok, msg = self.calls.answer(self._incoming_call_path)
            self._toast(msg)
            dialog.close()
            self._incoming_dialog = None
            if ok:
                self._show_in_call_dialog(display_name, number, outgoing=False)

        def on_reject(_):
            self._stop_ringtone()               # 🔕 stop ring
            ok, msg = self.calls.reject(self._incoming_call_path)
            self._toast(msg)
            dialog.close()
            self._incoming_dialog = None

        def on_closed(_):
            self._stop_ringtone()               # 🔕 stop ring if window closed
            self._incoming_dialog = None

        answer_btn.connect("clicked", on_answer)
        reject_btn.connect("clicked", on_reject)
        dialog.connect("closed", on_closed)

        dialog.present(self)
        return False  # GLib.idle_add return

    # ── BATTERY / SIGNAL CALLBACKS ────────────────────────

    def _poll_battery(self):
        threading.Thread(target=self.battery.poll, daemon=True).start()
        return True

    def _on_battery_update(self, level):
        """level 0–5; called on GLib main thread."""
        pct = self.battery.get_battery_percent(level)
        self._battery_lbl.set_text(f"🔋 {pct}")
        self._battery_lbl.set_visible(True)
        # Low battery style
        ctx = self._battery_lbl.get_style_context()
        if level <= 1:
            ctx.add_class("battery-low")
        else:
            ctx.remove_class("battery-low")

    def _on_signal_update(self, bars, carrier):
        """bars 0–5, carrier str."""
        bar_chars = ["📵", "📶", "📶", "📶", "📶", "📶"]
        icon = bar_chars[min(bars, 5)] if bars >= 0 else "📵"
        text = f"{icon} {carrier}" if carrier else icon
        self._signal_lbl.set_text(text)
        self._signal_lbl.set_visible(bool(carrier))
        # Update device card subtitle with connected device
        devs = self.bt.get_devices()
        for addr, dev in devs.items():
            if dev["connected"]:
                self._dev_name_lbl.set_text(dev["name"])
                self._dev_status_lbl.set_text("● Connected")
                return
        self._dev_status_lbl.set_text("Not connected")

    # ── MEDIA CALLBACKS ──────────────────────────────

    def _poll_media(self):
        threading.Thread(target=self.media.poll, daemon=True).start()
        return True

    def _on_track_update(self, title, artist, album, status, duration_ms, position_ms):
        has_info = bool(title and title not in ("", "Not Provided"))
        self._media_card_box.set_visible(has_info or status == "playing")
        if has_info:
            self._media_title_lbl.set_text(title)
        else:
            self._media_title_lbl.set_text("Unknown Track")
        self._media_artist_lbl.set_text(artist or album or "")
        self._update_play_btn(status)

    def _on_media_status(self, status):
        self._update_play_btn(status)
        # If something is playing, make sure card is visible
        if status == "playing":
            self._media_card_box.set_visible(True)

    def _update_play_btn(self, status):
        icon = ("media-playback-pause-symbolic" if status == "playing"
                else "media-playback-start-symbolic")
        self._play_pause_btn.set_icon_name(icon)

    # ── SMS MESSAGES PAGE ─────────────────────────────

    def _build_messages_page(self):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        tb = Adw.ToolbarView()
        box.append(tb)
        tb.set_vexpand(True)

        top_bar = Adw.HeaderBar(show_start_title_buttons=False,
                                show_end_title_buttons=False)
        top_bar.set_title_widget(Gtk.Label(label="Messages",
                                           css_classes=["heading"]))
        tb.add_top_bar(top_bar)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        tb.set_content(content)

        # Scrollable message list
        sw = Gtk.ScrolledWindow(vexpand=True)
        content.append(sw)

        self._sms_list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                                 spacing=8,
                                 margin_start=12, margin_end=12,
                                 margin_top=12, margin_bottom=8)
        sw.set_child(self._sms_list)

        # Empty state
        self._sms_empty = Adw.StatusPage(
            title="No Messages",
            description="Incoming SMS will appear here while your phone is connected via Bluetooth",
            icon_name="message-new-symbolic",
            vexpand=True
        )
        content.append(self._sms_empty)

        # Compose bar
        compose = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,
                          spacing=6,
                          margin_start=12, margin_end=12,
                          margin_bottom=12, margin_top=4)
        content.append(compose)

        self._sms_to_entry = Gtk.Entry(placeholder_text="Recipient number…",
                                       width_chars=14)
        compose.append(self._sms_to_entry)

        self._sms_body_entry = Gtk.Entry(placeholder_text="Type a message…",
                                         hexpand=True)
        self._sms_body_entry.connect("activate", self._on_sms_send)
        compose.append(self._sms_body_entry)

        send_btn = Gtk.Button(icon_name="document-send-symbolic",
                              css_classes=["suggested-action", "circular"],
                              tooltip_text="Send SMS")
        send_btn.connect("clicked", self._on_sms_send)
        compose.append(send_btn)

        if not self.sms.can_send_sms():
            compose.set_sensitive(False)
            compose.set_tooltip_text("SMS requires Bluetooth HFP + oFono MessageManager")

        return box

    def _on_sms_incoming(self, sender, body, timestamp):
        self._sms_empty.set_visible(False)
        row = self._make_sms_row(sender, body, timestamp, incoming=True)
        self._sms_list.prepend(row)
        self._toast(f"SMS from {sender}: {body[:40]}")

    def _on_sms_send(self, _):
        number = self._sms_to_entry.get_text().strip()
        body   = self._sms_body_entry.get_text().strip()
        if not number or not body:
            self._toast("Enter a recipient and message")
            return
        ok, msg = self.sms.send_sms(number, body)
        self._toast(msg)
        if ok:
            self._sms_empty.set_visible(False)
            row = self._make_sms_row("Me → " + number, body, "", incoming=False)
            self._sms_list.prepend(row)
            self._sms_body_entry.set_text("")

    def _make_sms_row(self, sender, body, timestamp, incoming=True):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)

        sender_lbl = Gtk.Label(label=self._escape(sender),
                               css_classes=["caption", "dim-label"],
                               xalign=0 if incoming else 1)
        outer.append(sender_lbl)

        bubble = Gtk.Label(label=self._escape(body),
                           wrap=True,
                           xalign=0,
                           css_classes=["sms-bubble-them" if incoming
                                        else "sms-bubble-me"],
                           halign=Gtk.Align.START if incoming else Gtk.Align.END)
        outer.append(bubble)

        if timestamp:
            ts_lbl = Gtk.Label(label=timestamp[:16],
                               css_classes=["sms-time"],
                               xalign=0 if incoming else 1)
            outer.append(ts_lbl)

        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL,
                            margin_top=4)
        outer.append(sep)
        return outer

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



