#!/usr/bin/python3
"""Take a screenshot through the desktop portal (the only sanctioned way on GNOME Wayland).
Runs on the system Python (it has the GLib/Gio bindings). Prints the saved file's path on success.
Usage: screenshot_portal.py [timeout_seconds]"""
import os
import sys
import urllib.parse

import gi
gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

timeout = int(sys.argv[1]) if len(sys.argv) > 1 else 30
bus = Gio.bus_get_sync(Gio.BusType.SESSION)
sender = bus.get_unique_name()[1:].replace(".", "_")
token = f"jarvis{os.getpid()}"
loop = GLib.MainLoop()
result = {}


def on_response(conn, sender_name, path, iface, signal, params, data):
    code, results = params.unpack()
    result["code"], result["uri"] = code, results.get("uri")
    loop.quit()


bus.signal_subscribe("org.freedesktop.portal.Desktop", "org.freedesktop.portal.Request", "Response",
                     f"/org/freedesktop/portal/desktop/request/{sender}/{token}", None,
                     Gio.DBusSignalFlags.NONE, on_response, None)
try:
    bus.call_sync("org.freedesktop.portal.Desktop", "/org/freedesktop/portal/desktop",
                  "org.freedesktop.portal.Screenshot", "Screenshot",
                  GLib.Variant("(sa{sv})", ("", {"handle_token": GLib.Variant("s", token),
                                                 "interactive": GLib.Variant("b", False)})),
                  None, Gio.DBusCallFlags.NONE, -1)
except GLib.Error as e:
    print(f"error: {e.message}", file=sys.stderr)
    sys.exit(2)
GLib.timeout_add_seconds(timeout, loop.quit)
loop.run()
if result.get("code") == 0 and result.get("uri"):
    print(urllib.parse.unquote(result["uri"].removeprefix("file://")))
elif "code" in result:
    print(f"error: screenshot denied or cancelled (portal code {result['code']})", file=sys.stderr)
    sys.exit(3)
else:
    print("error: timed out waiting for the screenshot permission dialog", file=sys.stderr)
    sys.exit(4)
