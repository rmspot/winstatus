#!/usr/bin/env python3
"""Registra un'attività pianificata Windows che tiene vivo l'agente.

- Avvio automatico all'accesso di QUALSIASI utente (trigger LogonTrigger generico)
- Se il watchdog crasha, Utilità di pianificazione lo riavvia entro 1 minuto (fino a 999 volte)
- Il watchdog a sua volta riavvia l'agente se il processo si chiude

Uso (da un terminale ESEGUIRO COME AMMINISTRATORE):
    python install_task.py               # installa e avvia subito
    python install_task.py --uninstall   # rimuove attività e processi

Senza admin non serve se si usa start_agent.bat nella cartella Startup,
ma quella copre solo l'utente corrente.
"""

import ctypes
import os
import subprocess
import sys
import tempfile

TASK_NAME = "WinStatusAgent"
HERE = os.path.dirname(os.path.abspath(__file__))
WATCHDOG = os.path.join(HERE, "watchdog.py")
USERS_GROUP_SID = "S-1-5-32-545"

TASK_XML = """<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <GroupId>{group_sid}</GroupId>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>{pythonw}</Command>
      <Arguments>"{watchdog}"{extra}</Arguments>
      <WorkingDirectory>{here}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def is_admin():
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def elevate():
    params = " ".join(f'"{a}"' for a in sys.argv[1:] if a != "--elevated")
    script = os.path.abspath(sys.argv[0])
    ret = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", sys.executable, f'"{script}" {params}', None, 1
    )
    if ret <= 32:
        print("Impossibile ottenere i permessi di amministratore (annullato?).")
        sys.exit(1)
    sys.exit(0)


def pythonw_exe():
    exe = sys.executable
    candidate = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return candidate if os.path.exists(candidate) else exe


def schtasks(*args):
    result = subprocess.run(["schtasks", *args], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"schtasks {' '.join(args)} fallito:")
        print(result.stdout.strip())
        print(result.stderr.strip())
    return result.returncode == 0


def kill_agent_processes():
    script = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*winstatus_agent.py*' "
        "-or $_.CommandLine -like '*watchdog.py*' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True,
    )


def uninstall():
    ok = schtasks("/Delete", "/TN", TASK_NAME, "/F")
    kill_agent_processes()
    print("Attività rimossa e processi terminati." if ok else
          "Attività non trovata (forse già rimossa); processi terminati comunque.")


def install(extra_args):
    pythonw = pythonw_exe()
    if not os.path.exists(WATCHDOG):
        print(f"watchdog.py non trovato in {HERE}")
        sys.exit(1)
    xml = TASK_XML.format(
        group_sid=USERS_GROUP_SID,
        pythonw=pythonw,
        watchdog=WATCHDOG,
        extra=(" " + " ".join(extra_args)) if extra_args else "",
        here=HERE,
    )
    fd, xml_path = tempfile.mkstemp(suffix=".xml")
    with os.fdopen(fd, "w", encoding="utf-16") as fh:
        fh.write(xml)
    try:
        if not schtasks("/Create", "/TN", TASK_NAME, "/XML", xml_path, "/F"):
            sys.exit(1)
    finally:
        os.unlink(xml_path)
    print(f"Attività '{TASK_NAME}' creata:")
    print(f"  - avvio all'accesso di qualsiasi utente")
    print(f"  - riavvio automatico entro 1 minuto se il watchdog crasha")
    print(f"  - watchdog: {pythonw} \"{WATCHDOG}\"")
    if schtasks("/Run", "/TN", TASK_NAME):
        print("Attività avviata subito.")
    print(f"Log del watchdog: %LOCALAPPDATA%\\winstatus-agent\\watchdog.log")


def main():
    args = [a for a in sys.argv[1:]]
    if "--uninstall" in args:
        if not is_admin():
            elevate()
        uninstall()
        return
    extra = [a for a in args if a not in ("--elevated",)]
    if not is_admin():
        elevate()
    install(extra)


if __name__ == "__main__":
    main()
