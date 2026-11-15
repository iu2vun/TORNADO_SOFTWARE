"""
Modello dati Trace / Diagram — architettura ZNB.

- Trace: una quantità misurata + formato + scala + marker propri.
- Diagram: una finestra rettangolare che contiene 1+ trace, con una trace attiva.

Il MainWindow gestisce una lista di Diagram; ogni Diagram è reso da un
DiagramWidget (vedi diagram.py). Le trace non sono più fisse (S11+S21):
l'utente le crea, elimina e assegna liberamente ai diagram.
"""

import itertools
from dataclasses import dataclass, field
from .const import TRACE_COLORS
from .measure import default_format, meas_label

# palette per trace oltre gli S-parametri base
_PALETTE = ["#ffd23f", "#5b8def", "#ff5555", "#5fe3a1", "#b69cff", "#e8e8e8", "#c98a4b", "#7fd6ff"]
_color_cycle = itertools.cycle(_PALETTE)

_trace_counter = itertools.count(1)
_TRACE_SEQ = ["#ff8a1f", "#35c6e8", "#a6e22e", "#f55bb4", "#ffd23f", "#5b8def", "#ff5555", "#5fe3a1"]


# scala predefinita per formato: (ref, div, divisioni sopra ref, divisioni sotto ref)
# I formati non elencati vengono adattati ai dati una volta sola (needs_fit).
DEFAULT_SCALE = {
    "dB Mag":            (0.0,   10.0, 1, 9),
    "Log Mag":           (0.0,   10.0, 1, 9),
    "Magnitude (dBuV)":  (100.0, 10.0, 2, 8),
    "Lin Mag":           (1.0,   0.1,  1, 9),
    "Phase":             (0.0,   45.0, 4, 4),
    "Unwr Phase":        (0.0,   180.0, 5, 5),
    "SWR":               (1.0,   1.0,  9, 0),
    "Real":              (0.0,   0.2,  5, 5),
    "Imag":              (0.0,   0.2,  5, 5),
    "Impedance (absolute)": (0.0, 25.0, 10, 0),
    "Resistance":        (0.0,   25.0, 10, 0),
    "Reactance":         (0.0,   50.0, 5, 5),
}


def default_scale(fmt: str):
    return DEFAULT_SCALE.get(fmt)


@dataclass
class Marker:
    idx:   int = 0        # indice punto sweep (ricalcolato dalla frequenza a ogni sweep)
    freq:  float = 0.0    # frequenza impostata [Hz]
    active: bool = True
    ref:   int = -1       # indice (0-based) del marker di riferimento per il delta; -1 = valore assoluto


@dataclass
class Trace:
    """Una trace: misura + formato + scala + marker."""
    name:    str                      # es. "Trc1"
    meas:    str                      # quantità misurata (es. "S21", "K", "Z11")
    fmt:     str  = None              # formato display (None → default per meas)
    color:   str  = None
    # scala per-trace, SEMPRE fissa (nessun autoscale continuo):
    # asse Y da  ref - down*div  a  ref + up*div
    auto:    bool  = False            # mantenuto per compatibilita' setup (non usato)
    ref:     float = 0.0              # valore di riferimento
    div:     float = 10.0             # scala per divisione
    up:      int   = 1                # divisioni sopra il riferimento
    down:    int   = 9                # divisioni sotto il riferimento
    needs_fit: bool = False           # adatta una volta ai dati (poi scala fissa)
    # marker
    markers: list = field(default_factory=list)
    delta:   bool = False             # (compatibilita' setup) il delta e' ora per-marker: Marker.ref
    msel:    int  = 0                 # indice del marker selezionato
    mask:    dict = None              # maschera return loss (Mask.to_dict) o None

    def apply_default_scale(self):
        """Imposta la scala fissa predefinita del formato corrente."""
        sc = default_scale(self.fmt)
        if sc is None:
            self.needs_fit = True
        else:
            self.ref, self.div, self.up, self.down = sc
            self.needs_fit = False

    def __post_init__(self):
        if self.fmt is None:
            self.fmt = default_format(self.meas)
        self.apply_default_scale()
        if self.color is None:
            self.color = TRACE_COLORS.get(self.meas, next(_color_cycle))

    @property
    def display_meas(self) -> str:
        return meas_label(self.meas)

    def label(self) -> str:
        return f"{self.name}  {self.display_meas}  {self.fmt}"


@dataclass
class Diagram:
    """Una finestra-diagramma con le sue trace."""
    title:   str = ""
    traces:  list = field(default_factory=list)   # list[Trace]
    active:  int = 0                               # indice trace attiva

    def active_trace(self):
        if 0 <= self.active < len(self.traces):
            return self.traces[self.active]
        return None

    def add_trace(self, t):
        # colore per posizione nel diagramma: arancione, ciano, verde, rosa... (palette Tornado)
        t.color = _TRACE_SEQ[len(self.traces) % len(_TRACE_SEQ)]
        self.traces.append(t)
        self.active = len(self.traces) - 1

    def remove_active(self):
        if self.traces:
            del self.traces[self.active]
            self.active = max(0, min(self.active, len(self.traces) - 1))


def new_trace(meas: str, name: str = None) -> Trace:
    """Factory: crea una trace nuova con nome automatico Trc<N>."""
    if name is None:
        name = f"Trc{next(_trace_counter)}"
    return Trace(name=name, meas=meas)


def default_layout() -> list:
    """Layout iniziale: un diagram con una sola trace S11, scala fissa."""
    d = Diagram(title="Diagram 1")
    d.add_trace(new_trace("S11"))
    d.active = 0
    return [d]
