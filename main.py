# ── CSS ──────────────────────────────────────────────
from src.constants import CSS
from src.components.linuxphone_gui import LinuxPhoneApp
from gi.repository import Gtk, Gdk

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