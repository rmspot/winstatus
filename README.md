# winstatus — stato di un laptop Windows via LAN

Sai in tempo reale se un altro laptop sulla stessa rete ha:

1. **il coperchio chiuso** (evento `GUID_LIDSWITCH_STATE_CHANGE` di Windows)
2. **lo schermo spento** (evento `GUID_CONSOLE_DISPLAY_STATE`: acceso / spento / attenuato)
3. **tasti premuti** di recente (hook tastiera a basso livello, solo il *tempo* dell'ultimo tasto — mai il contenuto)
4. **mouse mosso o tasti premuti** (polling cursore + `GetLastInputInfo`)

In più: sessione bloccata (Win+L), alimentazione rete/batteria, inattività.
L'agente espone anche una pagina web raggiungibile dal browser:
`http://IP-DEL-LAPTOP:8450/` (si aggiorna ogni 2 secondi).

## Com'è fatto

| File | Dove gira | A cosa serve |
|---|---|---|
| `winstatus_agent.py` | laptop **monitorato** (Windows) | server HTTP: pubblica lo stato su `0.0.0.0:8450` |
| `watchdog.py` | laptop monitorato | supervisore: riavvia l'agente se crasha (backoff 2→60 s) |
| `install_task.py` | laptop monitorato | registra l'attività pianificata (avvio per qualsiasi utente + riavvio automatico) |
| `check_status.py` | qualsiasi laptop (solo Python stdlib) | client: interroga l'agente |
| `start_agent.bat` | laptop monitorato | avvio manuale senza finestra console |
| `setup_firewall.bat` | laptop monitorato | apre le porte 8450/TCP e 8451/UDP (rete privata) |

## Installazione sul laptop monitorato (Windows)

1. Installa **Python 3.9+** da [python.org](https://www.python.org/downloads/) spuntando *"Add python.exe to PATH"*.
2. Copia i file in una cartella, ad esempio `C:\winstatus`.
3. Esegui **`setup_firewall.bat`** come amministratore (clic destro → *Esegui come amministratore*).
4. Avvio automatico permanente — in un terminale **come amministratore**:

   ```bat
   cd C:\winstatus
   python install_task.py
   ```

   Crea l'attività pianificata **WinStatusAgent** che:
   - parte all'accesso di **qualsiasi utente** (nessuna finestra visibile);
   - viene riavviata entro 1 minuto dall'Utilità di pianificazione se il watchdog crasha;
   - il watchdog a sua volta riavvia l'agente con backoff (2 s → 60 s) se il processo si chiude;
   - se l'agente è già attivo, le nuove istanze escono subito (controllo sulla porta).

   I parametri opzionali vengono passati all'agente: `python install_task.py --token "frase segreta"`.
   Rimozione: `python install_task.py --uninstall`.

5. Prova manuale (senza attività pianificata): `python winstatus_agent.py` oppure `start_agent.bat`.
6. Alternativa senza permessi amministratore (avvio automatico solo per l'utente
   corrente, senza riavvio dell'Utilità di pianificazione): `Win+R` →
   `shell:startup` → invio → copia dentro un collegamento a `start_agent.bat`.

## Uso dall'altro laptop

```bash
python check_status.py --discover               # trova automaticamente i laptop con l'agente
python check_status.py 192.168.1.50             # stato del laptop con quell'IP
python check_status.py 192.168.1.50 --watch 2   # aggiorna ogni 2 s
python check_status.py 192.168.1.50 --window 10 # "premuti/attivo" = ultimi 10 s
python check_status.py 192.168.1.50 --token "frase segreta"
```

Esempio di output:

```
Host                : DESKTOP-ABC123
1) Coperchio        : aperto
2) Schermo          : acceso
   Sessione bloccata: no
3) Tasti            : non premuti da 2 min fa
4) Mouse o tastiera : inattivo da 2 min fa
   Ultimo tasto     : 2 min fa
   Ultimo mouse     : 35 s fa
Alimentazione       : rete (100%)
```

Oppure apri nel browser: `http://192.168.1.50:8450/`

## Note importanti

- **Coperchio chiuso + sospensione**: se Windows va in sospensione alla
  chiusura del coperchio, il laptop smette di rispondere alla rete. Per
  monitorarlo a coperchio chiuso imposta *Impostazioni → Sistema →
  Alimentazione → "Alla chiusura del coperchio: Non eseguire alcuna azione"*.
- **Stato iniziale**: coperchio e schermo si aggiornano per evento; l'ultimo
  stato noto viene salvato in `%LOCALAPPDATA%\winstatus-agent\state.json`.
  Se non è mai arrivato un evento, "coperchio" resta "sconosciuto" finché
  non viene aperto/chiuso una volta.
- **Permessi**: l'agente deve girare nella sessione utente normale (l'attività
  pianificata lo fa). L'hook tastiera non legge *cosa* digiti, solo *quando*
  viene premuto un tasto.
- **Sicurezza**: pensato per la rete di casa; usa `--token` se sulla LAN ci
  sono altri dispositivi. Le porte sono aperte solo per il profilo di rete
  *privata*.
- Se il client risponde *"il laptop è spento, in sospensione o l'agente non
  gira?"* → probabilmente il laptop è in sospensione o l'agente non è avviato.
- **Log e diagnostica**: `%LOCALAPPDATA%\winstatus-agent\watchdog.log`
  registra ogni avvio/riavvio (ultime 200 righe). Verifica dell'attività:
  `schtasks /Query /TN WinStatusAgent /V`.
- **Limiti dell'avvio automatico**: l'agente parte al primo accesso utente,
  non prima: la lettura di tasti e mouse richiede la sessione interattiva.
  Prima di un accesso lo stato non è raggiungibile dalla rete.
- **Cambio utente rapido**: l'agente segue la sessione in cui è partito per
  primo; con cambio utente rapido i dati di input si riferiscono alla prima
  sessione.
