#!/usr/bin/env python3
"""Client per winstatus-agent: interroga un laptop Windows sulla stessa LAN.

Uso:
    python check_status.py --discover          # trova i laptop sulla rete
    python check_status.py 192.168.1.50        # interroga un host specifico
    python check_status.py 192.168.1.50 --window 10 --json
"""

import argparse
import json
import socket
import sys
import urllib.request

DISCOVERY_PORT = 8451
DISCOVERY_PING = b"WINSTATUS_DISCOVER_V1"
DEFAULT_PORT = 8450


def discover(timeout=1.5):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(0.2)
    found = {}
    deadline = __import__("time").time() + timeout
    while __import__("time").time() < deadline:
        try:
            sock.sendto(DISCOVERY_PING, ("255.255.255.255", DISCOVERY_PORT))
        except OSError:
            pass
        try:
            while True:
                data, addr = sock.recvfrom(1024)
                reply = json.loads(data.decode("utf-8"))
                found[addr[0]] = reply
        except socket.timeout:
            pass
        except (OSError, ValueError):
            pass
    sock.close()
    return found


def fetch(host, port, token, timeout=4.0):
    url = f"http://{host}:{port}/status"
    if token:
        url += f"?token={token}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fmt_age(seconds):
    if seconds is None:
        return "sconosciuto"
    if seconds < 1:
        return "adesso"
    if seconds < 60:
        return f"{seconds:.0f} s fa"
    if seconds < 3600:
        return f"{seconds / 60:.0f} min fa"
    return f"{seconds / 3600:.1f} h fa"


def print_status(status, window):
    lid = status["lid"]["closed"]
    display = status["display"]["state"]
    inp = status["input"]
    locked = status["session_locked"]
    power = status.get("power") or {}

    lid_txt = "CHIUSO" if lid else ("aperto" if lid is not None else "sconosciuto (nessun evento dall'avvio dell'agente)")
    disp_txt = {"on": "acceso", "off": "SPENTO", "dim": "attenuato"}.get(display, "sconosciuto")
    keys_txt = f"premuti negli ultimi {window:.0f} s" if inp["last_key_age_s"] is not None and inp["last_key_age_s"] <= window \
        else f"non premuti da {fmt_age(inp['last_key_age_s'])}"
    active_txt = "attivo" if inp["idle_s"] is not None and inp["idle_s"] <= window \
        else f"inattivo da {fmt_age(inp['idle_s'])}"

    print(f"Host                : {status['hostname']}")
    print(f"1) Coperchio        : {lid_txt}")
    print(f"2) Schermo          : {disp_txt}")
    print(f"   Sessione bloccata: {'sì' if locked else 'no'}")
    print(f"3) Tasti            : {keys_txt}")
    print(f"4) Mouse o tastiera : {active_txt}")
    print(f"   Ultimo tasto     : {fmt_age(inp['last_key_age_s'])}")
    print(f"   Ultimo mouse     : {fmt_age(inp['last_mouse_age_s'])}")
    if power:
        src = "rete" if power.get("ac_plugged") else "batteria"
        print(f"Alimentazione       : {src} ({power.get('battery_percent', '?')}%)")


def main():
    parser = argparse.ArgumentParser(description="Interroga winstatus-agent sulla LAN")
    parser.add_argument("host", nargs="?", help="IP del laptop da monitorare")
    parser.add_argument("--discover", action="store_true", help="cerca agenti sulla rete")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--token", default=None)
    parser.add_argument("--window", type=float, default=5.0,
                        help="finanza in secondi per 'premuti/attivo' (default 5)")
    parser.add_argument("--json", action="store_true", help="output JSON grezzo")
    parser.add_argument("--watch", type=float, default=None,
                        help="aggiorna ogni N secondi finché interrotto")
    args = parser.parse_args()

    if args.discover or not args.host:
        print("Ricerca agenti sulla rete...")
        found = discover()
        if not found:
            print("Nessun agente trovato. Assicurati che winstatus_agent.py sia in esecuzione "
                  "sull'altro laptop e che il firewall consenta le porte 8450/8451.")
            sys.exit(1)
        for ip, info in found.items():
            print(f"  {ip}  ->  {info.get('hostname', '?')} (porta {info.get('port', DEFAULT_PORT)})")
        if not args.host:
            if len(found) == 1:
                args.host = next(iter(found))
                args.port = next(iter(found.values())).get("port", DEFAULT_PORT)
                print(f"Uso automaticamente {args.host}\n")
            else:
                print("\nPiù host trovati: rilancia con: python check_status.py <IP>")
                return

    try:
        while True:
            try:
                status = fetch(args.host, args.port, args.token)
            except Exception as exc:
                print(f"Errore: {exc}. Il laptop è spento, in sospensione o l'agente non gira?")
                sys.exit(2)
            if args.json:
                print(json.dumps(status, indent=2, ensure_ascii=False))
            else:
                print_status(status, args.window)
            if not args.watch:
                break
            __import__("time").sleep(args.watch)
            print("\n" + "-" * 50)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
