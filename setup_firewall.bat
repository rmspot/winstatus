@echo off
rem Eseguire come AMMINISTRATORE: apre le porte dell'agente solo per la rete privata.
netsh advfirewall firewall add rule name="WinStatus Agent HTTP" dir=in action=allow protocol=TCP localport=8450 profile=private
netsh advfirewall firewall add rule name="WinStatus Agent Discovery" dir=in action=allow protocol=UDP localport=8451 profile=private
echo.
echo Regole firewall create.
pause
