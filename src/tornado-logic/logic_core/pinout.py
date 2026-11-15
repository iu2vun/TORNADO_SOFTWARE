"""Pinout del connettore dell'analizzatore (header 2x5, passo 2,54 mm) e cavo a 10 fili colorati.

Fonti per i valori iniziali (da verificare sull'esemplare reale, le unità differiscono):
- SparkFun TOL-15033/18627 e Shillehtek: fili nero…bianco = pin 1…10 del connettore; CH0 nero, CH1 marrone,
  CH2 rosso, CH3 arancione, CH4 giallo, CH5 verde, CH6 blu, CH7 viola, GND grigio (o bianco).
- Segnalazioni: su alcune unità uno dei due GND non è massa (SparkFun), e il pin 5 può essere GND o ingresso
  di clock esterno (recensione 24M-8CH). Quindi l'assegnazione è modificabile e va controllata con un tester.
- Verificato sull'esemplare di Riccardo: CH0…CH7 sui pin 1…8; il pin 9 (filo bianco nel suo nastro) NON è massa,
  quindi nel pinout iniziale è NC e l'unica massa è il pin 10.
- Le sigle stampate sul case sono CH0–CH7 oppure CH1–CH8; nel software CH1…CH8 = bit 0…7 (CH0 hardware = CH1 software).
"""
from __future__ import annotations

from .capture import N_CHANNELS

N_PINS = 10
FUNCTIONS = tuple(f"CH{i}" for i in range(N_CHANNELS)) + ("GND", "NC")

# (nome, esadecimale) del filo n. 1…10 del cavo in dotazione, nell'ordine del nastro
WIRE_COLORS = (("nero", "#1a1a1a"), ("marrone", "#8b5a2b"), ("rosso", "#e53935"), ("arancione", "#fb8c00"),
               ("giallo", "#fdd835"), ("verde", "#43a047"), ("blu", "#1e88e5"), ("viola", "#8e24aa"),
               ("grigio", "#9e9e9e"), ("bianco", "#f5f5f5"))

DEFAULT_PINOUT = ("CH0", "CH1", "CH2", "CH3", "CH4", "CH5", "CH6", "CH7", "NC", "GND")

# Posizione dei pin nel connettore: i pin dispari sulla riga alta, i pari su quella bassa, pin 1 a sinistra.
def pin_position(pin: int) -> tuple[int, int]:
    """(colonna, riga) di un pin 1…10; riga 0 = alta (dispari), riga 1 = bassa (pari)."""
    if not 1 <= pin <= N_PINS:
        raise ValueError(f"pin {pin} fuori range 1..{N_PINS}")
    return (pin - 1) // 2, (pin - 1) % 2


def software_name(fn: str) -> str:
    """CH0…CH7 (sigle hardware) -> CH1…CH8 (nomi nel software); GND/NC invariati."""
    if fn.startswith("CH") and fn[2:].isdigit():
        return f"CH{int(fn[2:]) + 1}"
    return fn


def channel_index(fn: str) -> int | None:
    """Indice di canale 0…7 (bit nei campioni) per 'CHk', altrimenti None."""
    return int(fn[2:]) if fn.startswith("CH") and fn[2:].isdigit() and int(fn[2:]) < N_CHANNELS else None


def check_pinout(assign: tuple[str, ...] | list[str]) -> list[str]:
    """Anomalie dell'assegnazione: lunghezza, funzioni sconosciute, canali duplicati o mancanti, GND assente."""
    if len(assign) != N_PINS:
        return [f"servono {N_PINS} pin, ricevuti {len(assign)}"]
    out = [f"pin {i + 1}: funzione '{f}' sconosciuta" for i, f in enumerate(assign) if f not in FUNCTIONS]
    for ch in FUNCTIONS[:N_CHANNELS]:
        pins = [i + 1 for i, f in enumerate(assign) if f == ch]
        if len(pins) > 1:
            out.append(f"{ch} assegnato a più pin ({', '.join(map(str, pins))})")
        elif not pins:
            out.append(f"{ch} non assegnato")
    if "GND" not in assign:
        out.append("nessun pin GND: serve almeno una massa in comune con il circuito")
    return out
