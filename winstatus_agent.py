#!/usr/bin/env python3
"""winstatus-agent: espone via HTTP sulla LAN lo stato del laptop Windows.

Riporta: coperchio aperto/chiuso, schermo acceso/spento, tasti premuti di
recente, attivita' di mouse/tastiera, sessione bloccata. Non registra MAI
il contenuto dei tasti, solo il fatto che siano stati premuti.

Avvio:  python winstatus_agent.py [--port 8450] [--token SEGRETO]
"""

import argparse
import ctypes
import json
import os
import socket
import sys
import threading
import time
from ctypes import wintypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

LID_GUID = "{BA3E0F4D-B817-4094-A2D1-D56379E6A0F3}"          # GUID_LIDSWITCH_STATE_CHANGE
DISPLAY_GUID = "{6FE69556-704A-47A0-8F24-C28D936FDA47}"       # GUID_CONSOLE_DISPLAY_STATE

WM_POWERBROADCAST = 0x0218
PBT_POWERSETTINGCHANGE = 0x8013
WH_KEYBOARD_LL = 13
WM_KEYDOWN = 0x0100
WM_SYSKEYDOWN = 0x0104
DESKTOP_SWITCHDESKTOP = 0x0080

DISCOVERY_PORT = 8451
DISCOVERY_PING = b"WINSTATUS_DISCOVER_V1"
KEY_WINDOW_S = 5.0
ACTIVE_WINDOW_S = 5.0


def utcnow():
    return datetime.now(timezone.utc).isoformat()


class State:
    def __init__(self):
        self.lock = threading.Lock()
        self.lid_closed = None
        self.lid_updated = None
        self.display_state = None  # "on" | "off" | "dim"
        self.display_updated = None
        self.last_key_ms = None
        self.last_mouse_ms = None
        self._load()

    @property
    def path(self):
        base = os.environ.get("LOCALAPPDATA", os.path.expanduser("~"))
        return os.path.join(base, "winstatus-agent", "state.json")

    def _load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            with self.lock:
                self.lid_closed = data.get("lid_closed")
                self.lid_updated = data.get("lid_updated")
                self.display_state = data.get("display_state")
                self.display_updated = data.get("display_updated")
        except (OSError, ValueError):
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with self.lock:
                data = {
                    "lid_closed": self.lid_closed,
                    "lid_updated": self.lid_updated,
                    "display_state": self.display_state,
                    "display_updated": self.display_updated,
                }
            with open(self.path, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
        except OSError:
            pass

    def set_lid(self, closed):
        with self.lock:
            self.lid_closed = bool(closed)
            self.lid_updated = utcnow()
        self._save()

    def set_display(self, value):
        with self.lock:
            self.display_state = value
            self.display_updated = utcnow()
        self._save()

    def note_key(self, now_ms):
        with self.lock:
            self.last_key_ms = now_ms

    def note_mouse(self, now_ms):
        with self.lock:
            self.last_mouse_ms = now_ms

    def tick64(self):
        return ctypes.windll.kernel32.GetTickCount64()


def last_input_age_s():
    """Secondi dall'ultimo input (mouse o tastiera) via GetLastInputInfo."""
    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    lii = LASTINPUTINFO()
    lii.cbSize = ctypes.sizeof(lii)
    if not user32.GetLastInputInfo(ctypes.byref(lii)):
        return None
    now32 = kernel32.GetTickCount() & 0xFFFFFFFF
    return ((now32 - lii.dwTime) & 0xFFFFFFFF) / 1000.0


def session_locked():
    user32 = ctypes.windll.user32
    handle = user32.OpenInputDesktop(0, False, DESKTOP_SWITCHDESKTOP)
    if handle:
        user32.CloseDesktop(handle)
        return False
    return True


def battery_info():
    class SYSTEM_POWER_STATUS(ctypes.Structure):
        _fields_ = [
            ("ACLineStatus", ctypes.c_byte),
            ("BatteryFlag", ctypes.c_byte),
            ("BatteryLifePercent", ctypes.c_byte),
            ("Reserved0", ctypes.c_byte),
            ("BatteryLifeTime", ctypes.c_uint32),
            ("BatteryFullLifeTime", ctypes.c_uint32),
        ]

    sps = SYSTEM_POWER_STATUS()
    if not ctypes.windll.kernel32.GetSystemPowerStatus(ctypes.byref(sps)):
        return None
    return {"ac_plugged": bool(sps.ACLineStatus), "battery_percent": sps.BatteryLifePercent}


def power_window_thread(state):
    """Finestra nascosta che riceve WM_POWERBROADCAST (coperchio + schermo)."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    WNDPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, wintypes.WPARAM, wintypes.LPARAM
    )

    class POWERBROADCAST_SETTING(ctypes.Structure):
        _fields_ = [
            ("PowerSetting", ctypes.GUID),
            ("DataLength", wintypes.DWORD),
            ("Data", ctypes.c_ubyte * 1),
        ]

    class WNDCLASSW(ctypes.Structure):
        _fields_ = [
            ("style", wintypes.UINT),
            ("lpfnWndProc", WNDPROC),
            ("cbClsExtra", ctypes.c_int),
            ("cbWndExtra", ctypes.c_int),
            ("hInstance", wintypes.HINSTANCE),
            ("hIcon", wintypes.HICON),
            ("hCursor", ctypes.c_void_p),
            ("hbrBackground", wintypes.HBRUSH),
            ("lpszMenuName", wintypes.LPCWSTR),
            ("lpszClassName", wintypes.LPCWSTR),
        ]

    def wndproc(hwnd, msg, wparam, lparam):
        if msg == WM_POWERBROADCAST and wparam == PBT_POWERSETTINGCHANGE:
            setting = ctypes.cast(lparam, ctypes.POINTER(POWERBROADCAST_SETTING)).contents
            guid = str(setting.PowerSetting).upper()
            value = setting.Data[0]
            if guid == LID_GUID:
                state.set_lid(bool(value))
            elif guid == DISPLAY_GUID:
                state.set_display({0: "off", 1: "on", 2: "dim"}.get(value))
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    proc_ref = WNDPROC(wndproc)
    hinstance = kernel32.GetModuleHandleW(None)
    wc = WNDCLASSW()
    wc.lpfnWndProc = proc_ref
    wc.hInstance = hinstance
    wc.lpszClassName = "WinStatusAgentWnd"
    if not user32.RegisterClassW(ctypes.byref(wc)):
        return
    hwnd = user32.CreateWindowExW(
        0, wc.lpszClassName, "WinStatusAgent", 0,
        0, 0, 0, 0, None, None, hinstance, None,
    )
    if not hwnd:
        return
    for guid_str in (LID_GUID, DISPLAY_GUID):
        user32.RegisterPowerSettingNotification(
            hwnd, ctypes.byref(ctypes.GUID(guid_str)), 0
        )
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def keyboard_hook_thread(state):
    """Hook tastiera a basso livello: registra solo il timestamp dell'ultimo tasto."""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32

    HOOKPROC = ctypes.WINFUNCTYPE(
        ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    )
    user32.SetWindowsHookExW.restype = ctypes.c_void_p
    user32.SetWindowsHookExW.argtypes = [
        ctypes.c_int, HOOKPROC, wintypes.HINSTANCE, wintypes.DWORD
    ]
    user32.CallNextHookEx.restype = ctypes.c_ssize_t
    user32.CallNextHookEx.argtypes = [
        ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM
    ]

    def proc(code, wparam, lparam):
        if code >= 0 and wparam in (WM_KEYDOWN, WM_SYSKEYDOWN):
            state.note_key(kernel32.GetTickCount64())
        return user32.CallNextHookEx(None, code, wparam, lparam)

    proc_ref = HOOKPROC(proc)
    hook = user32.SetWindowsHookExW(WH_KEYBOARD_LL, proc_ref, None, 0)
    if not hook:
        return
    msg = wintypes.MSG()
    while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
        user32.TranslateMessage(ctypes.byref(msg))
        user32.DispatchMessageW(ctypes.byref(msg))


def mouse_poller_thread(state, interval=0.2):
    user32 = ctypes.windll.user32
    prev = None
    while True:
        pt = wintypes.POINT()
        if user32.GetCursorPos(ctypes.byref(pt)):
            if prev is not None and (pt.x, pt.y) != prev:
                state.note_mouse(state.tick64())
            prev = (pt.x, pt.y)
        time.sleep(interval)


def discovery_thread(http_port):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    try:
        sock.bind(("", DISCOVERY_PORT))
    except OSError:
        return
    while True:
        try:
            data, addr = sock.recvfrom(1024)
            if data == DISCOVERY_PING:
                reply = json.dumps({"hostname": socket.gethostname(), "port": http_port})
                sock.sendto(reply.encode("utf-8"), addr)
        except OSError:
            continue


def build_status(state):
    now_ms = state.tick64()
    with state.lock:
        key_age = None if state.last_key_ms is None else max(0.0, (now_ms - state.last_key_ms) / 1000.0)
        mouse_age = None if state.last_mouse_ms is None else max(0.0, (now_ms - state.last_mouse_ms) / 1000.0)
        lid_closed = state.lid_closed
        lid_updated = state.lid_updated
        display_state = state.display_state
        display_updated = state.display_updated

    idle = last_input_age_s()

    def recent(age, window):
        return age is not None and age <= window

    return {
        "hostname": socket.gethostname(),
        "timestamp": utcnow(),
        "lid": {"closed": lid_closed, "updated": lid_updated},
        "display": {"state": display_state, "updated": display_updated},
        "session_locked": session_locked(),
        "input": {
            "idle_s": idle,
            "last_key_age_s": key_age,
            "last_mouse_age_s": mouse_age,
            "keys_recent": recent(key_age, KEY_WINDOW_S),
            "mouse_recent": recent(mouse_age, ACTIVE_WINDOW_S) or (idle is not None and idle <= ACTIVE_WINDOW_S and not recent(key_age, ACTIVE_WINDOW_S)),
            "any_recent": idle is not None and idle <= ACTIVE_WINDOW_S,
        },
        "power": battery_info(),
    }


HTML_PAGE = """<!DOCTYPE html>
<html lang="it"><head><meta charset="utf-8">
<meta http-equiv="refresh" content="2">
<title>Stato laptop</title>
<style>
body{{font-family:Segoe UI,sans-serif;background:#111;color:#eee;max-width:480px;margin:40px auto}}
h1{{font-size:1.2em}} td{{padding:6px 14px}} tr:nth-child(odd){{background:#1c1c1c}}
.ok{{color:#7C6}} .bad{{color:#e66}} .mid{{color:#ec6}}
</style></head><body>
<h1>Stato di {hostname}</h1>
<table><tr><td>Coperchio</td><td class="{lid_cls}">{lid}</td></tr>
<tr><td>Schermo</td><td class="{disp_cls}">{display}</td></tr>
<tr><td>Sessione bloccata</td><td class="{lock_cls}">{locked}</td></tr>
<tr><td>Tasti premuti</td><td class="{key_cls}">{keys}</td></tr>
<tr><td>Mouse/input attivo</td><td class="{act_cls}">{active}</td></tr>
<tr><td>Inattivo da</td><td>{idle}</td></tr>
<tr><td>Alimentazione</td><td>{power}</td></tr></table>
<p style="color:#777">Aggiornamento automatico ogni 2 s &middot; {ts}</p>
</body></html>"""


def fmt_age(seconds):
    if seconds is None:
        return "sconosciuto"
    if seconds < 1:
        return "ora"
    if seconds < 60:
        return f"{seconds:.0f} s"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min"
    return f"{seconds / 3600:.1f} h"


def render_html(status):
    lid = status["lid"]["closed"]
    display = status["display"]["state"]
    locked = status["session_locked"]
    inp = status["input"]
    power = status["power"]
    return HTML_PAGE.format(
        hostname=status["hostname"],
        lid="chiuso" if lid else ("aperto" if lid is not None else "sconosciuto"),
        lid_cls="bad" if lid else ("ok" if lid is not None else "mid"),
        display={"on": "acceso", "off": "spento", "dim": "dimmed"}.get(display, "sconosciuto"),
        disp_cls={"on": "ok", "off": "bad"}.get(display, "mid"),
        locked="sì" if locked else "no",
        lock_cls="bad" if locked else "ok",
        keys="sì" if inp["keys_recent"] else f"no (ultimo: {fmt_age(inp['last_key_age_s'])})",
        key_cls="bad" if inp["keys_recent"] else "ok",
        active="sì" if inp["any_recent"] else f"no (idle {fmt_age(inp['idle_s'])})",
        act_cls="bad" if inp["any_recent"] else "ok",
        idle=fmt_age(inp["idle_s"]),
        power=("rete" if power and power["ac_plugged"] else "batteria") if power else "n/d",
        ts=status["timestamp"],
    )


class Handler(BaseHTTPRequestHandler):
    token = None

    def do_GET(self):
        if self.token:
            supplied = None
            if "X-Token" in self.headers:
                supplied = self.headers["X-Token"]
            elif "?" in self.path:
                from urllib.parse import parse_qs, urlparse
                supplied = parse_qs(urlparse(self.path).query).get("token", [None])[0]
            if supplied != self.token:
                self.send_error(403)
                return

        status = build_status(STATE)
        body = json.dumps(status, ensure_ascii=False).encode("utf-8")
        path = self.path.split("?")[0]
        if path == "/status" or self.headers.get("Accept", "").startswith("application/json"):
            ctype = "application/json; charset=utf-8"
        else:
            body = render_html(status).encode("utf-8")
            ctype = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


STATE = None


def main():
    global STATE
    parser = argparse.ArgumentParser(description="Agente stato laptop per LAN")
    parser.add_argument("--port", type=int, default=8450)
    parser.add_argument("--token", default=None, help="richiede token in query o header X-Token")
    args = parser.parse_args()

    if os.name != "nt":
        print("Questo agente funziona solo su Windows.", file=sys.stderr)
        raise SystemExit(1)

    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("0.0.0.0", args.port))
    except OSError:
        print(f"winstatus-agent è già in esecuzione sulla porta {args.port}, esco.")
        return 0
    finally:
        probe.close()

    STATE = State()
    threading.Thread(target=power_window_thread, args=(STATE,), daemon=True).start()
    threading.Thread(target=keyboard_hook_thread, args=(STATE,), daemon=True).start()
    threading.Thread(target=mouse_poller_thread, args=(STATE,), daemon=True).start()
    threading.Thread(target=discovery_thread, args=(args.port,), daemon=True).start()

    Handler.token = args.token
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    print(f"winstatus-agent in ascolto su 0.0.0.0:{args.port} ({socket.gethostname()})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
