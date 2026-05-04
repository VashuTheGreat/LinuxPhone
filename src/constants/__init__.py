
import os
CACHE_DIR = os.path.expanduser("~/.cache/linuxphone")
os.makedirs(CACHE_DIR, exist_ok=True)
CONTACTS_CACHE = os.path.join(CACHE_DIR, "contacts.json")
CALLS_CACHE    = os.path.join(CACHE_DIR, "calls.json")
SMS_CACHE      = os.path.join(CACHE_DIR, "sms.json")


CSS = """
/* ── Device card in sidebar ─────────────────────── */
.device-card {
    background-color: alpha(@accent_bg_color, 0.15);
    border-radius: 12px;
    padding: 10px 12px;
    margin: 6px 8px;
    border: 1px solid alpha(@accent_bg_color, 0.3);
}
.device-name {
    font-weight: bold;
    font-size: 13px;
}
.device-sub {
    font-size: 11px;
    opacity: 0.7;
}
.battery-badge {
    font-size: 11px;
    font-weight: bold;
    padding: 2px 6px;
    border-radius: 8px;
    background: alpha(@success_bg_color, 0.25);
    color: @success_color;
}
.battery-low {
    background: alpha(@error_bg_color, 0.25);
    color: @error_color;
}
.signal-badge {
    font-size: 10px;
    opacity: 0.75;
}
/* ── Media player card ──────────────────────────── */
.media-card {
    background-color: alpha(@card_bg_color, 0.5);
    border-radius: 14px;
    padding: 12px;
    margin: 6px 8px;
    border: 1px solid alpha(@borders, 0.5);
}
.media-title {
    font-weight: bold;
    font-size: 13px;
}
.media-artist {
    font-size: 11px;
    opacity: 0.7;
}
.media-btn {
    border-radius: 50%;
    min-width: 36px;
    min-height: 36px;
    padding: 0;
}
.media-play-btn {
    border-radius: 50%;
    min-width: 44px;
    min-height: 44px;
    padding: 0;
}
/* ── Incoming call pulsing ring ─────────────────── */
@keyframes pulse-ring {
    0%   { opacity: 1.0; }
    50%  { opacity: 0.4; }
    100% { opacity: 1.0; }
}
.pulsing {
    animation: pulse-ring 1.2s ease-in-out infinite;
}
/* ── SMS / messages ─────────────────────────────── */
.sms-bubble-me {
    background-color: alpha(@accent_bg_color, 0.8);
    color: @accent_fg_color;
    border-radius: 18px 18px 4px 18px;
    padding: 8px 14px;
    margin: 2px 0;
}
.sms-bubble-them {
    background-color: alpha(@card_bg_color, 0.8);
    border-radius: 18px 18px 18px 4px;
    padding: 8px 14px;
    margin: 2px 0;
}
.sms-time {
    font-size: 10px;
    opacity: 0.55;
    margin-top: 2px;
}
/* ── Contact avatar ─────────────────────────────── */
.contact-avatar {
    background-color: @accent_bg_color;
    color: @accent_fg_color;
    border-radius: 50%;
}
/* ── Nav sidebar highlight ──────────────────────── */
.nav-active-row {
    background: alpha(@accent_bg_color, 0.2);
    border-radius: 8px;
}
/* ── Status dot ─────────────────────────────────── */
.dot-connected {
    color: @success_color;
    font-size: 10px;
}
.dot-disconnected {
    color: @error_color;
    font-size: 10px;
}
"""