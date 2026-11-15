"""
Registro completo dei formati di visualizzazione traccia.

Formule ricavate dal sorgente LibreVNA:
  Util/util.h          → SparamToImpedance, SparamToCapacitance, …
  Traces/traceaxis.cpp → TypeToName, Unit, conversioni
  Traces/trace.cpp     → getGroupDelay (linear regression su fase unwrapped)
"""

import numpy as np
from .const import Z0 as _Z0


# ---------------------------------------------------------------------------
# Conversioni elementari (identiche a Util::Sparam* del sorgente C++)
# ---------------------------------------------------------------------------
def _z(s, z0=_Z0):
    """Z = Z0 * (1+S) / (1-S)"""
    return z0 * (1.0 + s) / (1.0 - s + 1e-30)

def gamma_to_z(gamma, z0=_Z0):
    """Converti coefficiente di riflessione Γ in impedenza Z (scalare o array)."""
    return _z(gamma, z0)


def _db(s, _f, _z0):
    return 20.0 * np.log10(np.abs(s) + 1e-15)

def _dbuvolt(s, _f, _z0):
    # dBuV = dBm + 107 dB  (ref 50 Ω) — approssimazione da tensione sul carico 50 Ω
    dbm = 20.0 * np.log10(np.abs(s) + 1e-15)
    return dbm + 107.0

def _lin(s, _f, _z0):
    return np.abs(s)

def _phase(s, _f, _z0):
    return np.degrees(np.angle(s))

def _phase_unwrap(s, _f, _z0):
    return np.degrees(np.unwrap(np.angle(s)))

def _swr(s, _f, _z0):
    m = np.clip(np.abs(s), 0.0, 0.9999)
    return (1.0 + m) / (1.0 - m)

def _log_mag(s, _f, _z0):
    """Log Mag (ZNB): 20·log10|z|, come dB Mag ma è una voce di formato distinta."""
    return 20.0 * np.log10(np.abs(s) + 1e-15)

def _delay_derivation(s, f, _z0):
    """Delay Derivation (ZNB): derivata del group delay = seconda derivata di fase.
    -d²φ/dω²  con ω = 2πf."""
    phi = np.unwrap(np.angle(s))
    w   = 2.0 * np.pi * f
    if len(f) < 3:
        return np.zeros_like(f)
    # prima derivata dφ/dω (= -group delay), poi derivata di nuovo
    dphi_dw   = np.gradient(phi, w)
    d2phi_dw2 = np.gradient(dphi_dw, w)
    return -d2phi_dw2

def _real(s, _f, _z0):
    return np.real(s)

def _imag(s, _f, _z0):
    return np.imag(s)

def _abs_z(s, _f, z0):
    return np.abs(_z(s, z0))

def _resistance(s, _f, z0):
    return np.real(_z(s, z0))

def _reactance(s, _f, z0):
    return np.imag(_z(s, z0))

def _capacitance(s, f, z0):
    x = np.imag(_z(s, z0))
    # C = -1 / (X * 2π f)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.abs(x) > 1e-15, -1.0 / (x * 2.0 * np.pi * f), np.nan)

def _inductance(s, f, z0):
    x = np.imag(_z(s, z0))
    # L = X / (2π f)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.abs(f) > 0, x / (2.0 * np.pi * f), np.nan)

def _quality(s, _f, z0):
    z = _z(s, z0)
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.where(np.abs(np.real(z)) > 1e-15,
                        np.abs(np.imag(z) / np.real(z)), np.nan)

def _group_delay(s, f, _z0):
    """
    Metodo LibreVNA: regressione lineare su finestra di 5 campioni
    attorno ad ogni punto (unwrap locale → B1 → -B1/(2π·Δf)).
    """
    phi = np.unwrap(np.angle(s))
    n   = len(phi)
    out = np.full(n, np.nan)
    w   = 2                        # ±2 campioni, finestra 5 punti (come default LibreVNA)
    for i in range(w, n - w):
        sl  = slice(i - w, i + w + 1)
        ph_seg = phi[sl] - phi[i]  # centrata per stabilità numerica
        ix  = np.arange(-w, w + 1, dtype=float)
        # regressione lineare: phi = B0 + B1*k
        B1  = (np.dot(ix, ph_seg) / np.dot(ix, ix))
        df  = f[i] - f[i - 1]
        if df > 0:
            out[i] = -B1 / (2.0 * np.pi * df)
    # bordi: copia il valore più vicino calcolato
    out[:w]  = out[w]
    out[-w:] = out[-w - 1]
    return out

def _impulse_real(s, f, _z0):
    """IFFT della risposta in frequenza → risposta impulsiva (parte reale)."""
    return np.real(np.fft.ifft(s))

def _impulse_mag(s, f, _z0):
    return 20.0 * np.log10(np.abs(np.fft.ifft(s)) + 1e-15)

def _step(s, f, _z0):
    """Integrale cumulativo dell'impulso → step response (parte reale)."""
    return np.cumsum(np.real(np.fft.ifft(s)))


# ---------------------------------------------------------------------------
# Registro formati (nome → dict)
# 'fn'   : callable(s_array, f_array, z0) -> float array
# 'unit' : stringa unità  (da YAxis::Unit nel sorgente)
# 'label': etichetta asse Y
# 'kind' : 'rect' | 'smith' | 'polar'
# ---------------------------------------------------------------------------
FORMATS = {
    # ── Disabled ─────────────────────────────────────────────────────────────
    "Disabled":                 dict(fn=None,          unit="",   label="",            kind="disabled"),
    # ── Magnitudine ──────────────────────────────────────────────────────────
    "dB Mag":                   dict(fn=_db,           unit="dB", label="dB Mag",      kind="rect"),
    "Log Mag":                  dict(fn=_log_mag,      unit="dB", label="Log Mag",     kind="rect"),
    "Lin Mag":                  dict(fn=_lin,          unit="",   label="Lin Mag",     kind="rect"),
    "Magnitude (dBuV)":         dict(fn=_dbuvolt,      unit="dBuV",label="Mag dBuV",   kind="rect"),
    # ── Fase ─────────────────────────────────────────────────────────────────
    "Phase":                    dict(fn=_phase,        unit="°",  label="Phase",       kind="rect"),
    "Unwr Phase":               dict(fn=_phase_unwrap, unit="°",  label="Unwr Phase",  kind="rect"),
    # ── SWR ──────────────────────────────────────────────────────────────────
    "SWR":                      dict(fn=_swr,          unit="",   label="SWR",         kind="rect"),
    # ── Componenti cartesiane ─────────────────────────────────────────────────
    "Real":                     dict(fn=_real,         unit="",   label="Real",        kind="rect"),
    "Imag":                     dict(fn=_imag,         unit="",   label="Imag",        kind="rect"),
    # ── Ritardo ───────────────────────────────────────────────────────────────
    "Delay":                    dict(fn=_group_delay,  unit="s",  label="Delay",       kind="rect"),
    "Delay Derivation":         dict(fn=_delay_derivation, unit="s/Hz", label="Delay Deriv", kind="rect"),
    # ── Impedenza e componenti (solo parametri di riflessione) ───────────────
    "Impedance (absolute)":     dict(fn=_abs_z,        unit="Ω",  label="|Z|",         kind="rect"),
    "Resistance":               dict(fn=_resistance,   unit="Ω",  label="R",           kind="rect"),
    "Reactance":                dict(fn=_reactance,    unit="Ω",  label="X",           kind="rect"),
    "Capacitance":              dict(fn=_capacitance,  unit="F",  label="C",           kind="rect"),
    "Inductance":               dict(fn=_inductance,   unit="H",  label="L",           kind="rect"),
    "Quality Factor":           dict(fn=_quality,      unit="",   label="Q",           kind="rect"),
    # ── Dominio del tempo (IFFT) ──────────────────────────────────────────────
    "Impulse Response (Real)":  dict(fn=_impulse_real, unit="",   label="Impulse Re",  kind="rect"),
    "Impulse Response (Magnitude)": dict(fn=_impulse_mag, unit="dB", label="Impulse Mag", kind="rect"),
    "Step Response":            dict(fn=_step,         unit="",   label="Step",        kind="rect"),
    # ── Piani complessi ───────────────────────────────────────────────────────
    "Smith":                    dict(fn=None,          unit="",   label="Smith",       kind="smith"),
    "Inv Smith":                dict(fn=None,          unit="",   label="Inv Smith",   kind="inv_smith"),
    "Polar":                    dict(fn=None,          unit="",   label="Polar",       kind="polar"),
}

# Alias per retrocompatibilità (nomi vecchi → nuovi ZNB)
_FORMAT_ALIASES = {
    "Magnitude": "dB Mag",
    "Magnitude (linear)": "Lin Mag",
    "Unwrapped Phase": "Unwr Phase",
    "VSWR": "SWR",
    "Imaginary": "Imag",
    "Group delay": "Delay",
    "Impedance": "Polar",
}

# Ordine per il menu, raggruppato come ZNB (Cartesian poi Complex)
FORMAT_MENU_ORDER = [
    "Disabled",
    "dB Mag", "Log Mag", "Lin Mag", "Magnitude (dBuV)",
    "Phase", "Unwr Phase",
    "SWR",
    "Real", "Imag",
    "Delay", "Delay Derivation",
    "Impedance (absolute)", "Resistance", "Reactance",
    "Capacitance", "Inductance", "Quality Factor",
    "Impulse Response (Real)", "Impulse Response (Magnitude)", "Step Response",
    "Smith", "Inv Smith", "Polar",
]

DEFAULT_FORMAT = "dB Mag"


def _resolve(name: str) -> str:
    """Risolve alias (nomi vecchi → nuovi ZNB)."""
    return _FORMAT_ALIASES.get(name, name)


def transform(name: str, s: np.ndarray, freqs: np.ndarray, z0: float = _Z0) -> np.ndarray:
    """Calcola l'array display per un formato 'rect'."""
    name = _resolve(name)
    fn = FORMATS[name]["fn"]
    if fn is None:
        return np.zeros(len(s))
    return fn(s, freqs, z0)


def scalar_str(name: str, s_val: complex, freq: float = 0.0, z0: float = _Z0,
               s_arr=None, f_arr=None) -> str:
    """Stringa singolo punto per la marker-box.
    s_arr/f_arr: array completi per formati che richiedono il contesto (Group Delay).
    """
    name = _resolve(name)
    if name == "Delay":
        if s_arr is not None and f_arr is not None and len(s_arr) > 4:
            idx = int(np.argmin(np.abs(f_arr - freq)))
            v   = _group_delay(s_arr, f_arr, z0)[idx]
        else:
            return "—"
    else:
        s = np.array([s_val])
        f = np.array([freq if freq > 0 else 1.0])
        v = transform(name, s, f, z0)[0]
    u = FORMATS[name]["unit"]
    if np.isnan(v) or np.isinf(v):
        return "—"
    # Formattazione con prefisso SI automatico
    return _si(v, u)


def _si(v: float, unit: str) -> str:
    """Formatta con prefisso SI (p, n, µ, m, k, M, G)."""
    if unit in ("dB", "dBuV", "°", ""):
        return f"{v:.4g} {unit}".strip()
    a = abs(v)
    for exp, prefix in ((1e12, "T"), (1e9, "G"), (1e6, "M"), (1e3, "k"),
                         (1, ""), (1e-3, "m"), (1e-6, "µ"), (1e-9, "n"), (1e-12, "p")):
        if a >= exp or exp == 1e-12:
            return f"{v/exp:.4g} {prefix}{unit}".strip()
    return f"{v:.4g} {unit}".strip()
