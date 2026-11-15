"""
Modelli degli standard di calibrazione.

Formule identiche a calstandard.cpp / LibreVNA v1.6.5.
Riferimento: Keysight AN "Specifying Cal Standards and Kits"

Open:   Z = -j/(2π·f·C_fringing),   C_fringing = C0 + C1·f + C2·f² + C3·f³
Short:  Z = j·2π·f·L_series,         L_series   = L0 + L1·f + L2·f² + L3·f³
Load:   Z = R + j·2π·f·L  (con C parallelo opzionale)
Through: linea di trasmissione con delay e loss

Ogni standard ha delay [ps] e loss [dB/sqrt(GHz)] per il tratto di cavo.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional
import cmath

Z0 = 50.0   # impedenza di riferimento


# ---------------------------------------------------------------------------
# Funzione offset (transmission line) — identica a Util::addTransmissionLine
# ---------------------------------------------------------------------------
def _add_tx_line(gamma_in: complex, z0_line: float,
                 delay_s: float, loss_db_sqrtghz: float, freq: float) -> complex:
    """
    Aggiunge l'effetto di una linea di trasmissione al coefficiente di riflessione.
    delay_s: ritardo in secondi
    loss_db_sqrtghz: perdita in dB/sqrt(GHz)
    """
    if delay_s == 0.0 and loss_db_sqrtghz == 0.0:
        return gamma_in
    w = 2.0 * np.pi * freq
    f_sqrt = np.sqrt(max(freq / 1e9, 0.0))
    alpha = loss_db_sqrtghz * 1e9 / 2.0   # NdB/sqrt(GHz) → Np/sqrt(Hz)  (appx)
    Z_c = complex(z0_line + (alpha / (2 * w)) * f_sqrt if w > 0 else z0_line,
                  -(alpha / (2 * w)) * f_sqrt if w > 0 else 0.0)
    gamma_c = complex(alpha * delay_s / (2 * z0_line) * f_sqrt,
                      w * delay_s + alpha * delay_s / (2 * z0_line) * f_sqrt)
    p = cmath.exp(-gamma_c)
    Gamma_z = (Z_c - Z0) / (Z_c + Z0)
    denom = 1.0 - p * p * Gamma_z * Gamma_z
    if abs(denom) < 1e-30:
        return gamma_in
    Sxx = Gamma_z * (1.0 - p * p) / denom
    Sxy = p * (1.0 - Gamma_z * Gamma_z) / denom
    # riflessione vista attraverso la linea
    return Sxx + Sxy * Sxy * gamma_in / (1.0 - Sxx * gamma_in)


@dataclass
class OpenStandard:
    """Open con fringing capacitance polinomiale (coefficienti in unità SI base)."""
    delay_ps: float = 0.0        # offset delay [ps]
    loss:     float = 0.0        # offset loss  [dB/sqrt(GHz)]
    C0: float = 0.0              # [fF]
    C1: float = 0.0              # [fF/GHz]
    C2: float = 0.0              # [fF/GHz²]
    C3: float = 0.0              # [fF/GHz³]

    def s11(self, freq: float) -> complex:
        C = (self.C0 * 1e-15
             + self.C1 * 1e-27 * freq
             + self.C2 * 1e-36 * freq**2
             + self.C3 * 1e-45 * freq**3)
        if C == 0.0:
            gamma = complex(1.0, 0.0)
        else:
            Z_open = complex(0.0, -1.0 / (2.0 * np.pi * freq * C)) if freq > 0 else complex(1e15, 0)
            gamma = (Z_open - Z0) / (Z_open + Z0)
        return _add_tx_line(gamma, Z0, self.delay_ps * 1e-12, self.loss, freq)


@dataclass
class ShortStandard:
    """Short con series inductance polinomiale."""
    delay_ps: float = 0.0
    loss:     float = 0.0
    L0: float = 0.0              # [pH]
    L1: float = 0.0              # [pH/GHz]
    L2: float = 0.0              # [pH/GHz²]
    L3: float = 0.0              # [pH/GHz³]

    def s11(self, freq: float) -> complex:
        L = (self.L0 * 1e-12
             + self.L1 * 1e-24 * freq
             + self.L2 * 1e-33 * freq**2
             + self.L3 * 1e-42 * freq**3)
        Z_short = complex(0.0, 2.0 * np.pi * freq * L)
        gamma = (Z_short - Z0) / (Z_short + Z0)
        return _add_tx_line(gamma, Z0, self.delay_ps * 1e-12, self.loss, freq)


@dataclass
class LoadStandard:
    """Load resistivo con L serie e C parallelo opzionali."""
    delay_ps:   float = 0.0
    loss:       float = 0.0
    resistance: float = 50.0
    Lseries:    float = 0.0      # [pH]
    Cparallel:  float = 0.0      # [fF]
    C_first:    bool  = False    # ordine LC (vedi calstandard.cpp)

    def s11(self, freq: float) -> complex:
        Z = complex(self.resistance, 0.0)
        L = self.Lseries * 1e-12
        C = self.Cparallel * 1e-15
        w = 2.0 * np.pi * freq
        if self.C_first:
            Z += complex(0.0, w * L)
        if C > 0 and w > 0:
            Z_c = complex(0.0, -1.0 / (w * C))
            Z = (Z * Z_c) / (Z + Z_c)
        if not self.C_first:
            Z += complex(0.0, w * L)
        gamma = (Z - Z0) / (Z + Z0)
        return _add_tx_line(gamma, Z0, self.delay_ps * 1e-12, self.loss, freq)


@dataclass
class ThroughStandard:
    """Through: linea di trasmissione con delay e loss."""
    delay_ps: float = 0.0
    loss:     float = 0.0        # [dB/sqrt(GHz)]

    def sparams(self, freq: float) -> tuple[complex, complex]:
        """Ritorna (S11, S21) della linea di trasmissione."""
        if self.delay_ps == 0.0 and self.loss == 0.0:
            return complex(0.0), complex(1.0)
        w = 2.0 * np.pi * freq
        f_sqrt = np.sqrt(max(freq / 1e9, 0.0))
        alpha = self.loss * 1e9
        delay_s = self.delay_ps * 1e-12
        Z_c = complex(Z0 + (alpha / (2 * w)) * f_sqrt if w > 0 else Z0,
                      -(alpha / (2 * w)) * f_sqrt if w > 0 else 0.0)
        gamma_l = complex(alpha * delay_s / (2 * Z0) * f_sqrt,
                          w * delay_s + alpha * delay_s / (2 * Z0) * f_sqrt)
        p = cmath.exp(-gamma_l)
        Gamma = (Z_c - Z0) / (Z_c + Z0)
        denom = 1.0 - p * p * Gamma * Gamma
        if abs(denom) < 1e-30:
            return complex(0.0), complex(1.0)
        S11 = Gamma * (1.0 - p * p) / denom
        S21 = p * (1.0 - Gamma * Gamma) / denom
        return S11, S21


@dataclass
class CalKit:
    """Kit di calibrazione: raccoglie gli standard per ogni porta."""
    open:    OpenStandard    = field(default_factory=OpenStandard)
    short:   ShortStandard   = field(default_factory=ShortStandard)
    load:    LoadStandard    = field(default_factory=LoadStandard)
    through: ThroughStandard = field(default_factory=ThroughStandard)

    @classmethod
    def ideal(cls) -> "CalKit":
        """Kit ideale: Open=+1, Short=-1, Load=0, Through=trasparente."""
        return cls()


# ---------------------------------------------------------------------------
# Serializzazione e preset di kit
# ---------------------------------------------------------------------------
# Unità: delay [ps], loss [GOhm/s] (come nelle tabelle Keysight), C0 [fF],
# C1 [1e-27 F/Hz], C2 [1e-36 F/Hz^2], C3 [1e-45 F/Hz^3], L0 [pH],
# L1 [1e-24 H/Hz], L2 [1e-33 H/Hz^2], L3 [1e-42 H/Hz^3].
import dataclasses as _dc


def _kit_to_dict(kit: "CalKit") -> dict:
    return {"open": _dc.asdict(kit.open), "short": _dc.asdict(kit.short),
            "load": _dc.asdict(kit.load), "through": _dc.asdict(kit.through)}


def _kit_from_dict(d: dict) -> "CalKit":
    def mk(cls, src):
        names = {f.name for f in _dc.fields(cls)}
        return cls(**{k: v for k, v in (src or {}).items() if k in names})
    return CalKit(open=mk(OpenStandard, d.get("open")),
                  short=mk(ShortStandard, d.get("short")),
                  load=mk(LoadStandard, d.get("load")),
                  through=mk(ThroughStandard, d.get("through")))


CalKit.to_dict = _kit_to_dict
CalKit.from_dict = staticmethod(_kit_from_dict)


def _preset(open_, short_) -> dict:
    return {"open": open_, "short": short_, "load": {"resistance": 50.0}, "through": {}}


# Fonte: Keysight "VNA Calibration Kit Definitions" (tabelle Standard definitions).
# Il carico e' ideale (broadband load, offset 0): vale anche per l'HP 909F.
CAL_KIT_PRESETS: dict[str, dict] = {
    "Ideale (tutti zero)": _preset({}, {}),
    "Keysight 85032B/E - N maschio": _preset(
        dict(delay_ps=17.40, loss=0.7, C0=62.14, C1=-143.07, C2=82.92, C3=0.76),
        dict(delay_ps=17.80, loss=2.1002)),
    "Keysight 85032B/E - N femmina": _preset(
        dict(delay_ps=0.0, loss=0.7, C0=119.09, C1=-36.955, C2=26.258, C3=5.5136),
        dict(delay_ps=0.093, loss=0.7)),
    "Keysight 85032F - N maschio": _preset(
        dict(delay_ps=40.856, loss=0.93, C0=89.939, C1=2536.8, C2=-264.99, C3=13.4),
        dict(delay_ps=45.955, loss=1.087, L0=3.3998, L1=-496.4808, L2=34.8314, L3=-0.7847)),
    "Keysight 85032F - N femmina": _preset(
        dict(delay_ps=41.170, loss=0.93, C0=89.939, C1=2536.8, C2=-264.99, C3=13.4),
        dict(delay_ps=45.955, loss=1.087, L0=3.3998, L1=-496.4808, L2=34.8314, L3=-0.7847)),
    "Keysight 85033E / 85052D - 3.5 mm (SMA)": _preset(
        dict(delay_ps=29.243, loss=2.2, C0=49.433, C1=-310.13, C2=23.168, C3=-0.15966),
        dict(delay_ps=31.785, loss=2.36, L0=2.0765, L1=-108.54, L2=2.1705, L3=-0.01)),
}
