#!/usr/bin/env python3
"""Supervisore di winstatus_agent.py: riavvia l'agente se crasha o viene chiuso.

Usato dall'attività pianificata (install_task.py) ma funziona anche da solo:
    python watchdog.py [--port 8450] [--token ...]

Log: %LOCALAPPDATA%\\winstatus-agent\\watchdog.log
"""

import os
import subprocess
import sys
import time

AGENT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "winstatus_agent.py")
LOG_MAX_LINES = 200


def pythonw_exe():
    exe = sys.executable
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return candidate if os.path.exists(candidate) else exe


def log_dir():
    return os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "winstatus-agent")


def log(message):
    try:
        os.makedirs(log_dir(), exist_ok=True)
        path = os.path.join(log_dir(), "watchdog.log")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()[-LOG_MAX_LINES:]
        except OSError:
            lines = []
        lines.append(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}\n")
        with open(path, "w", encoding="utf-8") as fh:
            fh.writelines(lines[-LOG_MAX_LINES:])
    except OSError:
        pass


def main():
    if not os.path.exists(AGENT):
        log(f"ERRORE: agente non trovato in {AGENT}")
        return 2

    cmd = [pythonw_exe(), AGENT] + sys.argv[1:]
    log_dir_path = os.path.dirname(AGENT)
    backoff = 2.0
    log(f"watchdog avviato, comando: {' '.join(cmd)}")

    while True:
        log(f"avvio agente")
        started = time.time()
        try:
            proc = subprocess.Popen(cmd, cwd=log_dir_path)
            code = proc.wait()
        except Exception as exc:
            code = -1
            log(f"ERRORE avvio subprocess: {exc}")
        if code == 0:
            log("agente terminato con codice 0 (già in esecuzione altrove): watchdog esce")
            return 0
        ran = time.time() - started
        if ran > 600:
            backoff = 2.0
        log(f"agente terminato con codice {code} dopo {ran:.0f}s, riavvio tra {backoff:.0f}s")
        time.sleep(backoff)
        backoff = min(backoff * 2, 60.0)


if __name__ == "__main__":
    sys.exit(main())
