# Tornado Visualizer
Programma indipendente (NON fa parte della Tornado Suite): visualizza PCB e schemi Altium (`.PcbDoc`, `.SchDoc`) e file di produzione
Gerber RS-274X / Excellon. Interfaccia nero/arancione Tornado.

## Uso
    pip install -r requirements.txt
    python main.py [file ... | cartella]

- **Altium:** tracce, archi, pad, via, rame, designatori, reti (clic = evidenzia la rete), componenti (clic = centra), schemi con pin, fili, net label, power port.
- **Gerber/Excellon:** layer per ruolo e colore (da metadati X2 o dal nome), cartella intera, macro, regioni, step&repeat, fori e slot.
- **Comuni:** zoom con rotella, pan trascinando, doppio clic = adatta, **D** misura (due clic), **M** vista da sotto, mm/mil, esporta PNG e PDF (vettoriale).

## Eseguibile Windows
`build.bat` -> `dist\TornadoVisualizer\TornadoVisualizer.exe` (oppure `build.bat onefile`). Crea `.venv`, installa le dipendenze,
esegue `pytest tests` e compila. Log in `build.log`.

## Struttura
- `tornado_suite/apps/altium_core` lettore PcbDoc/SchDoc (OLE2, formato ricavato da file reali) - `tornado_suite/apps/gerber_core` lettore Gerber/Excellon.
- `tornado_suite/apps/visualizer` finestra e tela. `tornado_suite/core` e `tornado_diag` sono il nucleo comune (tema, icone, diagnostica, Ctrl+Shift+D).
- Limiti noti: pad con forma custom, testi PCB, librerie SchLib/PcbLib; Gerber: polarita' clear a pittura nera, macro thermal approssimata, niente X2/netlist.
