


from src.components.bluetooth_manager import BluetoothManager
import dbus, time, vobject, re, json, os
from src.constants import CONTACTS_CACHE, CALLS_CACHE


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

    def fetch_call_history(self, progress_cb=None):
        """
        Fetch call history from three separate PBAP folders for accurate types:
          ich (incoming/received), och (outgoing/dialed), mch (missed)
        Merges all, sorts by time descending, returns latest 150.
        Falls back to cch if individual folders fail.
        Falls back to cache if no phone connected.
        """
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
            if progress_cb: progress_cb("Connecting to phone…")
            sbus, client, session, pbap = self._create_session(addr)

            all_calls = []

            # Fetch each folder with its guaranteed type
            folders = [
                ("ich", "incoming"),   # Incoming (answered)
                ("och", "outgoing"),   # Outgoing (dialed)
                ("mch", "missed"),     # Missed
            ]

            for folder_id, forced_type in folders:
                try:
                    pbap.Select("int", folder_id)
                    count = int(pbap.GetSize())
                    limit = min(count, 60)   # 60 from each = up to 180 total
                    if limit == 0:
                        continue
                    if progress_cb:
                        progress_cb(f"Downloading {folder_id.upper()} ({limit} records)…")
                    tmp = f"/tmp/lp_calls_{folder_id}.vcf"
                    self._pull_and_wait(sbus, pbap, tmp)
                    parsed = self._parse_call_vcf(tmp)
                    # Override type with the folder's guaranteed type
                    for c in parsed:
                        c["type"] = forced_type
                    all_calls.extend(parsed)
                except Exception as e:
                    if progress_cb: progress_cb(f"⚠ {folder_id}: {str(e)[-40:]}")

            try: client.RemoveSession(session)
            except: pass

            if not all_calls:
                # Fallback: try combined cch folder
                if progress_cb: progress_cb("Trying combined folder…")
                sbus2, client2, session2, pbap2 = self._create_session(addr)
                pbap2.Select("int", "cch")
                count = int(pbap2.GetSize())
                if progress_cb: progress_cb(f"Downloading {min(count,100)} call records…")
                self._pull_and_wait(sbus2, pbap2, "/tmp/lp_calls_cch.vcf")
                all_calls = self._parse_call_vcf("/tmp/lp_calls_cch.vcf")
                try: client2.RemoveSession(session2)
                except: pass

            # Sort by time descending (most recent first), keep latest 150
            def sort_key(c):
                t = c.get("time", "")
                return t if t else "0"
            all_calls.sort(key=sort_key, reverse=True)
            all_calls = all_calls[:150]

            self.call_history = all_calls
            try:
                with open(CALLS_CACHE, 'w') as f:
                    json.dump(all_calls, f, ensure_ascii=False)
            except: pass

            incoming = sum(1 for c in all_calls if c["type"] == "incoming")
            outgoing = sum(1 for c in all_calls if c["type"] == "outgoing")
            missed   = sum(1 for c in all_calls if c["type"] == "missed")
            return all_calls, f"✅ {len(all_calls)} calls — ↙{incoming} ↗{outgoing} ✕{missed}"

        except Exception as e:
            if os.path.exists(CALLS_CACHE):
                try:
                    with open(CALLS_CACHE) as f:
                        calls = json.load(f)
                    return calls, f"⚠ Error syncing, cached data ({len(calls)} calls)"
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
        """
        Parse PBAP call-history VCF.
        Format in VCF:
          X-IRMC-CALL-DATETIME;MISSED:20260503T154135
          X-IRMC-CALL-DATETIME;RECEIVED:...
          X-IRMC-CALL-DATETIME;DIALED:...

        vobject folds the param into the key string, e.g.:
          key becomes 'x-irmc-call-datetime_missed'
        So we check the key string for the type keyword.
        We also do a raw-text pass as a reliable fallback.
        """
        # --- PASS 1: raw text scan for reliable type detection ---
        # Build map: stripped_phone_digits -> (type, time)
        raw_map = {}   # last-10-digits_of_number -> (type, time)
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                raw_lines = [l.strip() for l in f]
            current_digits = None
            for line in raw_lines:
                upper = line.upper()
                if upper.startswith('TEL'):
                    num_part = line.split(':', 1)[-1].strip()
                    digits = re.sub(r'[^0-9]', '', num_part)
                    current_digits = digits[-10:] if len(digits) >= 10 else digits
                if 'X-IRMC-CALL-DATETIME' in upper and current_digits is not None:
                    if 'MISSED' in upper:
                        ctype = 'missed'
                    elif 'RECEIVED' in upper:
                        ctype = 'incoming'
                    elif 'DIALED' in upper:
                        ctype = 'outgoing'
                    else:
                        ctype = 'unknown'
                    ctime_part = line.split(':', 1)[-1].strip() if ':' in line else ''
                    raw_map[current_digits] = (ctype, ctime_part[:15])
        except Exception:
            pass

        # --- PASS 2: vobject parse for name + number ---
        calls = []
        try:
            with open(filepath, 'r', encoding='utf-8', errors='replace') as f:
                data = f.read()
            for vcard in vobject.readComponents(data):
                name = ''
                number = ''
                call_type = 'unknown'
                call_time = ''
                try:
                    if hasattr(vcard, 'fn'):
                        fn = vcard.fn.value.strip()
                        if fn:
                            name = fn
                    for tel in vcard.contents.get('tel', []):
                        raw_num = re.sub(r'[^\d+\-\s]', '', str(tel.value)).strip()
                        if raw_num:
                            number = raw_num
                    # Try vobject key — param folded into key name
                    for key, vals in vcard.contents.items():
                        key_up = key.upper()
                        if 'CALL-DATETIME' in key_up or 'X-IRMC' in key_up:
                            for v in vals:
                                raw_val = str(v.value).strip()
                                call_time = raw_val[:15] if raw_val else ''
                                if 'MISSED' in key_up:
                                    call_type = 'missed'
                                elif 'RECEIVED' in key_up:
                                    call_type = 'incoming'
                                elif 'DIALED' in key_up:
                                    call_type = 'outgoing'
                except Exception:
                    pass

                # Fallback: use raw_map lookup by last-10-digits
                if call_type == 'unknown' and number:
                    digits = re.sub(r'[^0-9]', '', number)
                    key10 = digits[-10:] if len(digits) >= 10 else digits
                    if key10 in raw_map:
                        call_type, ctime_raw = raw_map[key10]
                        if not call_time:
                            call_time = ctime_raw

                if number or name:
                    calls.append({
                        'name': name or number or 'Unknown',
                        'number': number,
                        'type': call_type,
                        'time': call_time,
                    })
        except Exception:
            pass
        return calls
