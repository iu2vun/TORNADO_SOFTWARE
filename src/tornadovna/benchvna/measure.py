"""
Quantità misurabili stile R&S ZNB.

Una trace può misurare una qualsiasi di queste quantità, tutte derivate
dalla matrice S misurata (2 porte). Riferimento: ZNB manual §4.3.

Categorie:
  - S-parameters:     S11, S21, S12, S22
  - Ratios:           b2/a1, b1/a1, ...  (= S-param, ma non error-corrected)
  - Wave quantities:  a1, b1, a2, b2 Src Port j  (NON error-corrected)
  - Converted Z:      Z11..Z22  (da singolo S-param)
  - Converted Y:      Y11..Y22
  - Z-parameters:     matrice Z completa (da matrice S)
  - Y-parameters:     matrice Y completa
  - Stability:        K, μ1, μ2  (funzioni reali degli S-param)

Ogni quantità è calcolata a partire da un dict {Sij: ndarray complex}.
"""

import numpy as np
from .const import Z0


# ---------------------------------------------------------------------------
# Categorie e default format (ZNB §4.2.3.3)
# ---------------------------------------------------------------------------
# default_fmt: formato auto-selezionato quando si sceglie la quantità
MEAS_CATALOG = {
    # S-parameters (complex, dimensionless) → default dB Mag
    "S11": dict(cat="S-param",   default_fmt="dB Mag", complex=True),
    "S21": dict(cat="S-param",   default_fmt="dB Mag", complex=True),
    "S12": dict(cat="S-param",   default_fmt="dB Mag", complex=True),
    "S22": dict(cat="S-param",   default_fmt="dB Mag", complex=True),

    # Converted impedances Zij (complex, dimensioned) → default Lin Mag
    "Z11": dict(cat="Conv Z",    default_fmt="Lin Mag", complex=True),
    "Z21": dict(cat="Conv Z",    default_fmt="Lin Mag", complex=True),
    "Z12": dict(cat="Conv Z",    default_fmt="Lin Mag", complex=True),
    "Z22": dict(cat="Conv Z",    default_fmt="Lin Mag", complex=True),

    # Converted admittances Yij → default Lin Mag
    "Y11": dict(cat="Conv Y",    default_fmt="Lin Mag", complex=True),
    "Y21": dict(cat="Conv Y",    default_fmt="Lin Mag", complex=True),
    "Y12": dict(cat="Conv Y",    default_fmt="Lin Mag", complex=True),
    "Y22": dict(cat="Conv Y",    default_fmt="Lin Mag", complex=True),

    # Z-parameters (matrice completa) → default Lin Mag
    "Zmat11": dict(cat="Z-param", default_fmt="Lin Mag", complex=True, label="Z11"),
    "Zmat21": dict(cat="Z-param", default_fmt="Lin Mag", complex=True, label="Z21"),
    "Zmat12": dict(cat="Z-param", default_fmt="Lin Mag", complex=True, label="Z12"),
    "Zmat22": dict(cat="Z-param", default_fmt="Lin Mag", complex=True, label="Z22"),

    # Y-parameters (matrice completa) → default Lin Mag
    "Ymat11": dict(cat="Y-param", default_fmt="Lin Mag", complex=True, label="Y11"),
    "Ymat21": dict(cat="Y-param", default_fmt="Lin Mag", complex=True, label="Y21"),
    "Ymat12": dict(cat="Y-param", default_fmt="Lin Mag", complex=True, label="Y12"),
    "Ymat22": dict(cat="Y-param", default_fmt="Lin Mag", complex=True, label="Y22"),

    # Stability factors (real) → default Lin Mag
    "K":   dict(cat="Stability", default_fmt="Lin Mag", complex=False),
    "mu1": dict(cat="Stability", default_fmt="Lin Mag", complex=False, label="µ1"),
    "mu2": dict(cat="Stability", default_fmt="Lin Mag", complex=False, label="µ2"),
}

# organizzazione per il menu Meas (categoria → lista quantità)
MEAS_MENU = {
    "S-Parameter":   ["S11", "S21", "S12", "S22"],
    "Conv. Imped.":  ["Z11", "Z21", "Z12", "Z22"],
    "Conv. Admit.":  ["Y11", "Y21", "Y12", "Y22"],
    "Z-Parameter":   ["Zmat11", "Zmat21", "Zmat12", "Zmat22"],
    "Y-Parameter":   ["Ymat11", "Ymat21", "Ymat12", "Ymat22"],
    "Stability":     ["K", "mu1", "mu2"],
}


def meas_label(name: str) -> str:
    """Etichetta visibile per una quantità (usa 'label' se presente)."""
    info = MEAS_CATALOG.get(name, {})
    return info.get("label", name)


def default_format(name: str) -> str:
    return MEAS_CATALOG.get(name, {}).get("default_fmt", "dB Mag")


# ---------------------------------------------------------------------------
# Calcolo della quantità dalla matrice S
# ---------------------------------------------------------------------------
def _sm(traces: dict, key: str, n: int) -> np.ndarray:
    """Estrae Sij dal dict, o zeri se assente."""
    v = traces.get(key)
    if v is None:
        return np.zeros(n, dtype=complex)
    return np.asarray(v, dtype=complex)


def compute(name: str, traces: dict, z0: float = Z0) -> np.ndarray | None:
    """
    Calcola la quantità 'name' (complessa) dalla matrice S in 'traces'.
    traces: {"S11": ndarray, "S21": ..., "S12": ..., "S22": ...}
    Ritorna ndarray complex, o None se i dati necessari mancano.
    Le quantità reali (stability) sono ritornate come complex con parte imm. 0.
    """
    # se è direttamente un S-parametro presente, ritorna
    if name in ("S11", "S21", "S12", "S22"):
        return traces.get(name)

    # determina la lunghezza
    any_s = next((traces[k] for k in ("S11","S21","S12","S22") if k in traces), None)
    if any_s is None:
        return None
    n = len(any_s)

    S11 = _sm(traces, "S11", n); S21 = _sm(traces, "S21", n)
    S12 = _sm(traces, "S12", n); S22 = _sm(traces, "S22", n)

    # ── Converted impedances Zij = z0·(1+Sij)/(1-Sij)  (matched-circuit) ──
    if name in ("Z11", "Z21", "Z12", "Z22"):
        s = _sm(traces, "S" + name[1:], n)
        return z0 * (1.0 + s) / (1.0 - s + 1e-30)

    # ── Converted admittances Yij = 1/Zij ──
    if name in ("Y11", "Y21", "Y12", "Y22"):
        s = _sm(traces, "S" + name[1:], n)
        z = z0 * (1.0 + s) / (1.0 - s + 1e-30)
        return 1.0 / (z + 1e-30)

    # ── Z-parameters (matrice completa da matrice S) ──
    # Z = z0 · (I+S)·(I-S)^-1   calcolato elemento per elemento per 2 porte
    if name.startswith("Zmat"):
        Zm = _s_to_z(S11, S21, S12, S22, z0)
        return Zm[name[-2:]]

    # ── Y-parameters (matrice completa) = inversa di Z ──
    if name.startswith("Ymat"):
        Ym = _s_to_y(S11, S21, S12, S22, z0)
        return Ym[name[-2:]]

    # ── Stability factors (reali) ──
    if name == "K":
        delta = S11 * S22 - S12 * S21
        num = 1.0 - np.abs(S11)**2 - np.abs(S22)**2 + np.abs(delta)**2
        den = 2.0 * np.abs(S12 * S21) + 1e-30
        return (num / den).astype(complex)
    if name == "mu1":
        delta = S11 * S22 - S12 * S21
        num = 1.0 - np.abs(S11)**2
        den = np.abs(S22 - delta * np.conj(S11)) + np.abs(S12 * S21) + 1e-30
        return (num / den).astype(complex)
    if name == "mu2":
        delta = S11 * S22 - S12 * S21
        num = 1.0 - np.abs(S22)**2
        den = np.abs(S11 - delta * np.conj(S22)) + np.abs(S12 * S21) + 1e-30
        return (num / den).astype(complex)

    return None


def _s_to_z(S11, S21, S12, S22, z0):
    """
    Conversione matrice S → matrice Z per 2 porte (elemento per elemento).
        Z = z0 (I + S)(I - S)^{-1}
    Ritorna dict {"11","21","12","22"} di ndarray complex.
    """
    # (I - S)
    a11 = 1.0 - S11; a12 = -S12
    a21 = -S21;      a22 = 1.0 - S22
    det = a11 * a22 - a12 * a21 + 1e-30
    # inversa di (I - S)
    i11 =  a22 / det; i12 = -a12 / det
    i21 = -a21 / det; i22 =  a11 / det
    # (I + S)
    b11 = 1.0 + S11; b12 = S12
    b21 = S21;       b22 = 1.0 + S22
    # Z = z0 · (I+S)·(I-S)^-1
    Z11 = z0 * (b11 * i11 + b12 * i21)
    Z12 = z0 * (b11 * i12 + b12 * i22)
    Z21 = z0 * (b21 * i11 + b22 * i21)
    Z22 = z0 * (b21 * i12 + b22 * i22)
    return {"11": Z11, "21": Z21, "12": Z12, "22": Z22}


def _s_to_y(S11, S21, S12, S22, z0):
    """Matrice Y = inversa della matrice Z."""
    Z = _s_to_z(S11, S21, S12, S22, z0)
    z11, z21, z12, z22 = Z["11"], Z["21"], Z["12"], Z["22"]
    det = z11 * z22 - z12 * z21 + 1e-30
    return {
        "11":  z22 / det, "12": -z12 / det,
        "21": -z21 / det, "22":  z11 / det,
    }
