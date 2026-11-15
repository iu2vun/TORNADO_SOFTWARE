# TornadoVNA — Stato del progetto

Frontend desktop Python/PySide6 per il VNA open-source **LibreVNA**, con GUI
ispirata al Rohde & Schwarz ZNB. Sostituisce completamente LibreVNA-GUI.

- **Sviluppatore:** Riccardo (IU2VUN)
- **Ambiente:** Windows, PyCharm, Python in `.venv`
- **Hardware:** LibreVNA S/N 206F33933743, FW 1.6.4, HW v1B, 2 porte, VID:PID 1209:4121

---

## Come si avvia

```powershell
python main.py            # USB diretto (default) — nessun LibreVNA-GUI
python main.py --scpi     # via LibreVNA-GUI (server SCPI porta 19542)
python main.py --demo     # dati sintetici, nessun hardware
python main.py --fullscreen
```
Batch equivalenti: `start_usb.bat`, `start_scpi.bat`, `start_demo.bat`.

### Dipendenze
```powershell
pip install PySide6 pyqtgraph numpy pyusb
```
- `libusb-1.0.dll` nella cartella `VNA_Suite\` (accanto a main.py)
- WinUSB installato sul device via Zadig (una volta sola)

### ⚠ Nota interprete PyCharm
La Run Configuration di PyCharm a volte usa **Python 3.14** (che pyusb 1.3.1 non
supporta) invece del `.venv`. Sintomo: `No module named usb.core`.
Fix: lanciare dal terminale integrato, oppure impostare l'interprete della Run
Config su `.venv\Scripts\python.exe`.

---

## Architettura

Due backend intercambiabili, stessa interfaccia Qt (segnali/slot), UI identica:

1. **USB diretto** (default, `benchvna/usb/`) — driver Python puro con pyusb,
   protocollo binario reverse-engineered dal firmware. Nessuna dipendenza esterna.
2. **SCPI** (`benchvna/scpi.py`, `benchvna/worker.py`) — parla con LibreVNA-GUI
   via TCP porta 19542.

### Struttura file (`VNA_Suite/`)
```
main.py              entry point, selezione backend (--scpi/--demo/default USB)
diagnose.py          diagnostica connessione SCPI
test_usb.py          diagnostica driver USB standalone (molto utile per debug)
start_*.bat          launcher

benchvna/
  __init__.py
  const.py           Z0=50, ALL_TRACES, TRACE_COLORS (palette R&S)
  mainwindow.py      MainWindow: diagramma, softkey, marker, channel bar (560 righe)
  ui_widgets.py      SoftkeyPanel (con paginazione), NumericEntry, DiagramArea
  style.py           QSS stile R&S ZNB (nero, softkey laterali)
  formats.py         21 formati traccia con formule esatte da LibreVNA
  smith.py           SmithChart (pyqtgraph)
  export.py          Touchstone .s1p/.s2p + CSV
  backend.py         BackendProcess: avvia LibreVNA-GUI per modalità SCPI
  scpi.py            client TCP SCPI
  worker.py          VnaWorker — worker SCPI (thread separato)
  cal_dialog.py      CalibrationDialog — finestra guidata OSL/SOLT

  usb/
    protocol.py      protocollo binario: packet encode/decode, CRC32,
                     SweepSettings, DeviceInfo, VNADatapoint, decode_at
    driver.py        LibreVNAUSBDriver — pyusb, thread RX, publish sweep
    worker.py        VnaWorkerUSB — worker USB + integrazione calibrazione

  cal/
    standards.py     modelli Open/Short/Load/Through (polinomi C/L, offset line)
    engine.py        CalEngine — motore OSL/SOLT 12 termini, save/load JSON
    procedure.py     CalProcedure — acquisizione guidata standard
```

---

## Protocollo USB LibreVNA (verificato dal sorgente firmware v1.6.5)

Pacchetto: `[0x5A][len 2B LE][type 1B][payload][CRC32 4B]`
- `len` = lunghezza totale inclusi header (4B) e CRC (4B)
- CRC32 poly 0xEDB88320; **VNADatapoint (type 27) ha CRC = 0** (ottimizzazione)
- VID:PID validi: 0483:564E, 0483:4121, 1209:4121
- Endpoint bulk: OUT 0x01, IN 0x81 (dati), IN 0x82 (log, ignorato)

Comandi chiave: REQUEST_DEVICE_INFO (15), DEVICE_INFO (5), SWEEP_SETTINGS (2),
INITIATE_SWEEP (32), SET_IDLE (20), VNA_DATAPOINT (27), ACK (7), NACK (10).

### VNADatapoint → S-parametri
Ogni datapoint contiene N valori complessi, ciascuno con un **descrittore** 1 byte:
- bit [7:5] = stage
- bit [4:0] = port mask (port1=0x01, port2=0x02, reference=0x10)

Descrittori reali osservati sul device:
```
0x01 = stage0 port1 measured    0x21 = stage1 port1 measured
0x02 = stage0 port2 measured    0x22 = stage1 port2 measured
0x13 = stage0 reference         0x33 = stage1 reference
```
Ricostruzione: `S_ij = measured(porta_ricevente_j) / reference(porta_eccitante_i)`
- stage 0 = eccitazione porta 1 → S11, S21
- stage 1 = eccitazione porta 2 → S12, S22

---

## Calibrazione SOLT/OSL a 12 termini

Motore Python (`cal/engine.py`) con formule identiche a `calibration.cpp`:
- **computeOSL**: termini D (directivity), S (source match), R (reflection tracking)
  per porta, dai 3 standard Open/Short/Load
- **computeSOLT**: aggiunge L (load match), T (transmission tracking), I (isolation)
  dalla misura Through
- **correct()**: applica la correzione via matrici a/b e inversione

Verificato: con cal ideale l'errore di correzione è esattamente 0; con errori
sistematici simulati (D/S/R noti) la correzione li rimuove perfettamente
(Open corretto → 1.0 = 0 dB piatto).

Standard con modelli reali (`cal/standards.py`): Open con fringing capacitance
polinomiale (C0+C1·f+C2·f²+C3·f³), Short con series inductance, Load con R/L/C,
Through con delay+loss. Kit attualmente **ideale** (Open=+1, Short=−1, Load=0).
→ TODO: inserire i parametri reali del kit di Riccardo se disponibili.

Flusso UI: Cal → Prepara → per ogni standard (connetti fisicamente → Misura) →
Attiva cal → (opzionale) Salva .cal. La correzione si applica automaticamente
in `_emit_from_list` prima di `sweepReady`.

---

## Formati traccia implementati (21, in `formats.py`)

Disabled, Magnitude (dB), Magnitude (dBuV), Magnitude (linear), Phase,
Unwrapped Phase, VSWR, Real, Imaginary, Impedance (absolute), Resistance,
Reactance, Capacitance, Inductance, Quality Factor, Group delay,
Impulse Response (Real), Impulse Response (Magnitude), Step Response,
Smith, Impedance (polar).

Formule prese da LibreVNA `util.h`/`traceaxis.cpp`. Group delay via regressione
lineare su 5 campioni.

---

## Bug risolti (cronologia)

1. Entry file `benchvna.py` collideva col package → rinominato `main.py`
2. Comandi SCPI v1.6.5 diversi (`:DEVice:`, `:VNA:ACQuisition:` ecc.)
3. SCPI: i comandi SET non ricevono risposta (Result::Empty = silenzio) →
   `cmd()` non deve aspettare risposta
4. pyqtgraph "X and Y shape mismatch" → sweep in transizione, allineamento a
   lunghezza minima comune + parse_trace robusto a terne incomplete
5. `No module named usb.core` in PyCharm = interprete Python 3.14 vs .venv;
   import usb resi lazy (dentro le funzioni)
6. "No backend available" → serve libusb-1.0.dll + WinUSB via Zadig
7. Watchdog TX: pyusb/WinUSB a volte ignora il timeout di write() → thread
   demone con timeout reale di 2s
8. **Collisione descrittore reference**: il ref (0x13) contiene i bit di tutte
   le porte, quindi matchava anche come "measured port1" (0x13 & 0x01 = 0x01).
   Fix: escludere i descrittori con bit reference quando si cerca un measured
9. **Re-trigger dannoso**: il firmware sweeppa già in continuo dopo il primo
   INITIATE_SWEEP (~22 sweep in 5s). Il re-trigger generava NACK → rimosso
10. **calSetup duplicato**: c'erano DUE definizioni di calSetup, la seconda
    (no-op `pass`) sovrascriveva la prima → cal non partiva. Rimossa
11. **Cal ondulatoria**: read_sweep prendeva sweep misti/parziali; riscritto
    per catturare uno sweep pulito completo
12. **Dialog cal raccolto dal GC**: era variabile locale → reso self._cal_dlg

---

## Ottimizzazioni velocità USB (ultima sessione)

Tre colli di bottiglia risolti:
1. **`del buf[:n]` O(n) per pacchetto** → parser a offset `decode_at()`, un solo
   `del` per blocco USB invece di uno per pacchetto (~1.6x sul solo parsing)
2. **Doppia decodifica + queue Qt con lock per ogni datapoint** → il thread RX
   decodifica una volta sola e pubblica sweep completi via `get_latest_sweep()`;
   la UI legge con un solo lock per sweep
3. **Tick UI 50ms (20Hz)** → 16ms (~60Hz)

Meccanismo: `_rx_loop` accumula i datapoint per point_num, rileva fine sweep
(point_num torna indietro) e chiama `_publish_sweep()`. Il worker in `_poll_sweep`
legge `get_latest_sweep()` e emette solo se il serial è nuovo.

## Softkey con paginazione (ultima sessione)

`SoftkeyPanel` mostra 10 slot. Il menu Format ha 22 voci → il pulsante
"◀ Indietro" finiva fuori vista. Ora paginazione automatica: "Altro ▼" / "▲ Su",
con Indietro sempre visibile in fondo.

---

## TODO (prossimi passi)

Riferimento: manuale R&S ZNB (sezioni 4.2.3 formati, 4.3 misure, 5.x GUI).

1. **Trace configurabili** — rimuovere le 2 trace fisse (S11+S21 hardcoded),
   permettere add/remove/select arbitrari stile ZNB Trace Config.
   Stato attuale: `mainwindow.py` usa `self._active = ["S11","S21"]` e
   `self._sel`. Serve un modello trace dinamico.

2. **Meas softtool ZNB** — oltre agli S-parametri, aggiungere le categorie di
   misura dello ZNB:
   - Ratios (bi/aj Src Port k)
   - Wave quantities (ai/bi Src Port j) — NON error-corrected
   - Converted impedances Zij / admittances Yij
   - Z-parameters / Y-parameters (matrice completa)
   - Stability factors K, μ1, μ2 (funzioni reali degli S-param):
     K = (1−|S11|²−|S22|²+|Δ|²)/(2|S12·S21|), Δ = S11·S22−S12·S21
   - Formato di default auto-selezionato per quantità (dB Mag per S-param,
     Lin Mag per Z/Y, Real/Lin Mag per stability)

3. **Formati ZNB mancanti** — Log Mag, Inverted Smith, Delay Derivation

4. **Kit di calibrazione reale** — inserire i parametri del kit di Riccardo
   (delay, C0/C1.., L0/L1.., resistance) al posto del kit ideale

## 2026-11-09 — TornadoVNA (GUI Tornado Suite)
- Programma rinominato TornadoVNA (Tornado Laboratori). Package interno `benchvna`, tema in `tornado_ui/`.
- GUI: ribbon (Misura, Tracce, Marker, Analisi, File), pannello Stimolo/Traccia attiva, grafici su fondo canvas.
- Logo vettoriale ricavato dal DWG (`benchvna/logo_data.py`); `make_icon.py` genera assets/TornadoVNA.ico (solo fulmine arancione su nero), logo_tornado.png/svg.
- Marker: tabella (frequenza digitabile, delta per-marker, valore), clic sul grafico, marker->centro/start/stop/ref, marker sui picchi, salvataggio nel setup.
- Splash 10 s (benchvna/splash.py, --no-splash per saltarlo). build.bat: barra di avanzamento reale (tools/step.ps1).
- Stato strumento: DeviceStatus (lock PLL, ADC, unlevel, rif. esterno, temperature, alimentazione se presente) via START/STOP_STATUS_UPDATES;
  modalita' Periodico (3 s) / Continuo / Spento; card nel pannello, tab "Stato", storico temperature + eventi + CSV, deriva termica dalla cal.
  Prossimo: protocollo PC->ESP32 (Waveshare ESP32-S3-LCD-4.3) per pannello di stato e comandi.

## Display ESP32 (Waveshare ESP32-S3-LCD-4.3) - Arduino IDE
- PC: `benchvna/esp_link.py` (pyserial, JSON a righe 115200) + ribbon Stato > Display > "Display ESP32...". Testato con seriale virtuale.
- ESP32: `firmware/TornadoVNA_Display/TornadoVNA_Display.ino` (GFX Library for Arduino >=1.5, ArduinoJson >=7).
- NON verificato su hardware: pinout RGB, CH422G (0x24/0x38, bit EXIO), timing pannello, indirizzo GT911. Controllare se schermo nero.
