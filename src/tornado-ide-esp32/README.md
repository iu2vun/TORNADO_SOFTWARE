# Tornado IDE ESP32/Arduino
Programma indipendente ricavato dalla Tornado Suite (stesso codice, stessa interfaccia nero/arancione). Cartella autonoma:
tutto il necessario e il `build.bat` per l'eseguibile.

## Uso
    pip install -r requirements.txt
    python main.py [file ...]          (--admin / --no-admin per l'UAC)

## Eseguibile Windows
`build.bat` -> `dist\TornadoESPIDE\TornadoESPIDE.exe` (cartella) oppure `build.bat onefile` -> `dist\TornadoESPIDE.exe`.
Crea `.venv`, installa le dipendenze, esegue `pytest tests` e compila. Log in `build.log`.
Per usare un'unica venv per piu' programmi: `set TS_VENV=C:\percorso\venv` prima di lanciare build.bat.

## Debug e manutenzione
- Log: `%APPDATA%\TornadoSuite\tools\espide\logs\app.log` (rotazione automatica, tutte le eccezioni incluse).
- Nel programma **Ctrl+Shift+D** apre la Diagnostica (versioni, porte COM, VISA, ultime righe di log): «Copia diagnostica» e incolla in chat.
- `pytest -q tests`: test di fumo (avvio e chiusura in offscreen).
- Codice: `tornado_suite/modules/espide_mod.py` (registrazione) e `tornado_suite/apps/...` (logica e GUI); `tornado_suite/core` e
  `tornado_diag` sono il nucleo comune (tema, icone, impostazioni, diagnostica).
- Cartella generata da `make_tools.py` (in `TornadoTools\`): una correzione fatta nella suite arriva qui rigenerando.

Nota: PlatformIO Core si installa dall'IDE: **Strumenti > Installa PlatformIO automaticamente** (crea un ambiente dedicato
in `%LOCALAPPDATA%\TornadoSuite\pio-venv` con un Python adatto, tipicamente 3.12). In alternativa `py -m pip install platformio`
e **Altro > Percorso di PlatformIO** per indicare `pio.exe`.

## Funzioni dell'IDE
- **Editor**: completamento (automatico dopo 3 lettere, Ctrl+Spazio manuale) con i simboli del progetto, del framework Arduino e
  delle librerie; **F12** o Ctrl+clic = vai alla definizione; Ctrl+F / Ctrl+H trova e sostituisci; Ctrl+Shift+I formatta
  (clang-format se installato); **F9** o clic nel margine = breakpoint. Il completamento e' basato su un indice a espressioni
  regolari, non su un compilatore: propone nomi noti, non i membri di un oggetto.
- **Cerca** (Ctrl+Shift+F): ricerca e sostituzione in tutto il progetto, con filtro file, maiuscole, parola intera, regex.
- **Memoria**: occupazione Flash/RAM, variazione rispetto alla build precedente, storico (`.tornado/size_history.json`),
  analisi dell'ELF (sezioni, simboli piu' grandi, somma per libreria/file) con `size`/`nm` della toolchain.
- **Grafico**: numeri stampati sulla seriale (`12.5`, `1,2,3`, `temp:21.5 umid=40`), pausa, scala automatica, export CSV.
  Il salvataggio del log della seriale e' il pulsante **Log** del Monitor seriale.
- **Git**: stato, stage per file (spunta), commit, differenze, cronologia; i file modificati sono colorati nell'albero.
- **Strumenti**: analisi statica (`pio check`), test (`pio test`), indice dei simboli (compiledb), file system
  (`buildfs`/`uploadfs`, cartella `data/`), **OTA** (crea l'ambiente `<env>-ota` con `upload_protocol = espota`),
  tabella delle **partizioni** (editor con verifica di allineamenti/sovrapposizioni e modelli), progetti recenti.
- **Debug** (F5): `pio debug --interface=gdb` con passi, pila, variabili locali, breakpoint e riga corrente evidenziata.
  Richiede sonda JTAG o USB-JTAG integrato (S3/C3/C6) e le opzioni `debug_*` (Strumenti > Debug: configura).
- **Nuovo progetto** con modelli: server web JSON, OTA, Telegram, dati per il grafico, display CYD. I pin sono segnaposto da
  verificare sulla propria scheda.
- Barra di avanzamento della compilazione: dalla seconda build il totale dei file e' noto e la barra e' in percentuale.

## Build (Standard Tornado v2)
`build.bat` = cartella + installer; `build.bat onefile` = file unico; `build.bat skipdeps` salta il controllo delle dipendenze di sistema.
Se manca Inno Setup 6 il bat lancia `installa_dipendenze.bat` (amministratore, log in `installa_dipendenze.log`). Log build: `build.log`.
