# Tornado Serial Sniffer
Programma indipendente ricavato dalla Tornado Suite (stesso codice, stessa interfaccia, tema HP 9845C (schermo nero, 8 colori puri, effetto CRT)). Cartella autonoma:
tutto il necessario e il `build.bat` per l'eseguibile.

## Uso
    pip install -r requirements.txt
    python main.py [file ...]          (--admin / --no-admin per l'UAC)

## Eseguibile Windows
`build.bat` -> `dist\TornadoSniffer\TornadoSniffer.exe` (cartella) oppure `build.bat onefile` -> `dist\TornadoSniffer.exe`.
Crea `.venv`, installa le dipendenze, esegue `pytest tests` e compila. Log in `build.log`.
Per usare un'unica venv per piu' programmi: `set TS_VENV=C:\percorso\venv` prima di lanciare build.bat.

## Debug e manutenzione
- Log: `%APPDATA%\TornadoSuite\tools\sniffer\logs\app.log` (rotazione automatica, tutte le eccezioni incluse).
- Nel programma **Ctrl+Shift+D** apre la Diagnostica (versioni, porte COM, VISA, ultime righe di log): «Copia diagnostica» e incolla in chat.
- `pytest -q tests`: test di fumo (avvio e chiusura in offscreen).
- Codice: `tornado_suite/modules/sniffer_mod.py` (registrazione) e `tornado_suite/apps/...` (logica e GUI); `tornado_suite/core` e
  `tornado_diag` sono il nucleo comune (tema, icone, impostazioni, diagnostica).
- Cartella generata da `make_tools.py` (in `TornadoTools\`): una correzione fatta nella suite arriva qui rigenerando.
