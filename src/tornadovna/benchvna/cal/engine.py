"""
Motore di calibrazione OSL / SOLT a 12 termini.

Formule identiche a calibration.cpp (LibreVNA v1.6.5):
  - computeOSL:   calcola D[i], S[i], R[i] per ogni porta
  - computeSOLT:  aggiunge L[i][j], T[i][j], I[i][j] per la trasmissione
  - correctMeasurement: applica la correzione al datapoint

Riferimento teorico citato nel sorgente:
  "Multi-Port Calibration Techniques for Differential Parameter Measurements
   with Network Analyzers"

Uso:
    from benchvna.cal.engine import CalEngine, CalPoint
    from benchvna.cal.standards import CalKit

    eng = CalEngine(kit=CalKit.ideal(), ports=[1, 2])

    # misura degli standard (array numpy di lunghezza N per ogni frequenza)
    eng.add_open (port=1, freqs=f, s11_meas=meas_open1)
    eng.add_short(port=1, freqs=f, s11_meas=meas_short1)
    eng.add_load (port=1, freqs=f, s11_meas=meas_load1)
    eng.add_open (port=2, freqs=f, s11_meas=meas_open2)
    eng.add_short(port=2, freqs=f, s11_meas=meas_short2)
    eng.add_load (port=2, freqs=f, s11_meas=meas_load2)
    eng.add_through(port1=1, port2=2, freqs=f,
                    s11_meas=th_s11, s21_meas=th_s21)

    eng.compute_solt()   # calcola i termini di errore

    # correzione di un datapoint
    raw = {"S11": arr_s11, "S21": arr_s21, "S12": arr_s12, "S22": arr_s22}
    cal = eng.correct(freqs, raw)
"""

import numpy as np
from dataclasses import dataclass, field
from typing import Optional
from .standards import CalKit, Z0


# ---------------------------------------------------------------------------
# Struttura dei termini di errore per una frequenza
# ---------------------------------------------------------------------------
def _cinterp(x, xp, fp):
    """Interpolazione complessa su modulo e fase (fase srotolata), come LibreVNA-GUI.
    Interpolare re/im in modo lineare con termini a fase veloce (ritardo di cavo)
    produce un'ondulazione periodica spuria sulla traccia corretta."""
    xp = np.asarray(xp, dtype=float)
    fp = np.asarray(fp, dtype=complex)
    if len(xp) == 1:
        return np.full(np.shape(x), fp[0], dtype=complex)
    mag = np.interp(x, xp, np.abs(fp))
    ph  = np.interp(x, xp, np.unwrap(np.angle(fp)))
    return mag * np.exp(1j * ph)


@dataclass
class CalPoint:
    """
    Termini di errore per una singola frequenza.
    Indici 0-based: porta 1 → indice 0, porta 2 → indice 1.

    D[i]    : Directivity (E_DF / E_DR)
    S[i]    : Source Match (E_SF / E_SR)
    R[i]    : Reflection Tracking (E_RF / E_RR)
    L[i][j] : Receiver Match per trasmissione da porta i a porta j
    T[i][j] : Transmission Tracking
    I[i][j] : Isolation
    """
    frequency: float
    D: list = field(default_factory=list)  # [complex] len = n_ports
    S: list = field(default_factory=list)
    R: list = field(default_factory=list)
    L: list = field(default_factory=list)  # [[complex]] n_ports x n_ports
    T: list = field(default_factory=list)
    I: list = field(default_factory=list)


class CalEngine:
    """
    Motore OSL/SOLT.  Riceve le misure degli standard come array numpy
    indicizzati per frequenza e calcola i termini di errore.
    """

    def __init__(self, kit: Optional[CalKit] = None, ports: list[int] = None):
        self.kit    = kit or CalKit.ideal()
        self.ports  = ports or [1, 2]       # lista porte 1-based
        self._n     = len(self.ports)
        self._pidx  = {p: i for i, p in enumerate(self.ports)}  # porta → indice

        # misure grezze: dict {porta: array complex}
        self._meas_open  : dict[int, tuple] = {}   # porta → (freqs, s11)
        self._meas_short : dict[int, tuple] = {}
        self._meas_load  : dict[int, tuple] = {}
        # through: (p1, p2) → (freqs, s11_meas, s21_meas)
        self._meas_through: dict[tuple, tuple] = {}

        # risultato: array di CalPoint (uno per frequenza)
        self.cal_points: list[CalPoint] = []
        self.freqs: Optional[np.ndarray] = None

    # ── aggiunta misure ─────────────────────────────────────────────────────
    def add_open(self, port: int, freqs: np.ndarray, s11_meas: np.ndarray):
        self._meas_open[port] = (np.array(freqs), np.array(s11_meas))

    def add_short(self, port: int, freqs: np.ndarray, s11_meas: np.ndarray):
        self._meas_short[port] = (np.array(freqs), np.array(s11_meas))

    def add_load(self, port: int, freqs: np.ndarray, s11_meas: np.ndarray):
        self._meas_load[port] = (np.array(freqs), np.array(s11_meas))

    def add_through(self, port1: int, port2: int,
                    freqs: np.ndarray,
                    s11_meas: np.ndarray, s21_meas: np.ndarray,
                    s22_meas: Optional[np.ndarray] = None,
                    s12_meas: Optional[np.ndarray] = None):
        self._meas_through[(port1, port2)] = (
            np.array(freqs),
            np.array(s11_meas), np.array(s21_meas),
            np.array(s22_meas) if s22_meas is not None else None,
            np.array(s12_meas) if s12_meas is not None else None,
        )

    # ── calcolo ─────────────────────────────────────────────────────────────
    def compute_osl(self) -> bool:
        """Calcola i termini OSL (D,S,R) — VETTORIZZATO su tutte le frequenze."""
        if not self._check_osl():
            return False

        ref_port = self.ports[0]
        freqs = self._meas_open[ref_port][0]
        self.freqs = np.asarray(freqs)
        m = len(freqs)

        # pre-alloca i CalPoint
        self.cal_points = []
        for f in freqs:
            cp = CalPoint(frequency=float(f))
            cp.D = [0j]*self._n; cp.S = [0j]*self._n; cp.R = [1+0j]*self._n
            cp.L = [[0j]*self._n for _ in range(self._n)]
            cp.T = [[1+0j]*self._n for _ in range(self._n)]
            cp.I = [[0j]*self._n for _ in range(self._n)]
            self.cal_points.append(cp)

        for i, port in enumerate(self.ports):
            # misure interpolate sui freqs (array)
            o_m = self._interp_arr(self._meas_open[port],  freqs)
            s_m = self._interp_arr(self._meas_short[port], freqs)
            l_m = self._interp_arr(self._meas_load[port],  freqs)
            # valori ideali degli standard (array)
            o_c = np.array([self.kit.open.s11(f)  for f in freqs])
            s_c = np.array([self.kit.short.s11(f) for f in freqs])
            l_c = np.array([self.kit.load.s11(f)  for f in freqs])

            denom = (l_c*o_c*(o_m-l_m) + l_c*s_c*(l_m-s_m) + o_c*s_c*(s_m-o_m))
            denom = np.where(np.abs(denom) < 1e-30, 1e-30, denom)

            D = (l_c*o_m*(s_m*(o_c-s_c) + l_m*s_c)
                 - l_c*o_c*l_m*s_m
                 + o_c*l_m*s_c*(s_m-o_m)) / denom
            S = (l_c*(o_m-s_m) + o_c*(s_m-l_m) + s_c*(l_m-o_m)) / denom
            delta = (l_c*l_m*(o_m-s_m) + o_c*o_m*(s_m-l_m) + s_c*s_m*(l_m-o_m)) / denom
            R = D*S - delta

            for k in range(m):
                self.cal_points[k].D[i] = D[k]
                self.cal_points[k].S[i] = S[k]
                self.cal_points[k].R[i] = R[k]
        return True

    def compute_solt(self) -> bool:
        """Termini SOLT completi (OSL + through) — VETTORIZZATO."""
        if not self.compute_osl():
            return False

        freqs = self.freqs
        m = len(freqs)

        for i, p1 in enumerate(self.ports):
            for j, p2 in enumerate(self.ports):
                if i == j:
                    continue
                th_fwd = self._meas_through.get((p1, p2))
                th_rev = self._meas_through.get((p2, p1))

                if th_fwd is not None:
                    S11_m = self._interp_arr((th_fwd[0], th_fwd[1]), freqs)
                    S21_m = self._interp_arr((th_fwd[0], th_fwd[2]), freqs)
                elif th_rev is not None:
                    src11 = th_rev[3] if th_rev[3] is not None else th_rev[1]
                    src21 = th_rev[4] if th_rev[4] is not None else th_rev[2]
                    S11_m = self._interp_arr((th_rev[0], src11), freqs)
                    S21_m = self._interp_arr((th_rev[0], src21), freqs)
                else:
                    continue

                th = np.array([self.kit.through.sparams(f) for f in freqs])
                S11_ideal = th[:, 0]; S21_ideal = th[:, 1]; S22_ideal = S11_ideal
                delta_S = S11_ideal*S22_ideal - S21_ideal*S21_ideal

                Di = np.array([self.cal_points[k].D[i] for k in range(m)])
                Si = np.array([self.cal_points[k].S[i] for k in range(m)])
                Ri = np.array([self.cal_points[k].R[i] for k in range(m)])

                num_L = (S11_m - Di)*(1.0 - Si*S11_ideal) - S11_ideal*Ri
                den_L = (S11_m - Di)*(S22_ideal - Si*delta_S) - delta_S*Ri
                den_L = np.where(np.abs(den_L) < 1e-30, 1e-30, den_L)
                L = num_L / den_L

                S21i = np.where(np.abs(S21_ideal) < 1e-30, 1e-30, S21_ideal)
                T = S21_m * (1.0 - Si*S11_ideal - L*S22_ideal + Si*L*delta_S) / S21i

                for k in range(m):
                    self.cal_points[k].L[i][j] = L[k]
                    self.cal_points[k].T[i][j] = T[k]
                    self.cal_points[k].I[i][j] = 0j
        return True

    @staticmethod
    def _interp_arr(meas_tuple, freqs):
        """Interpola (freqs_meas, values) su un array di frequenze target."""
        return _cinterp(freqs, meas_tuple[0], meas_tuple[1])

    # ── applicazione correzione ─────────────────────────────────────────────
    def correct(self, freqs: np.ndarray,
                raw: dict[str, np.ndarray]) -> dict[str, np.ndarray]:
        """
        Applica la calibrazione — VETTORIZZATA su tutte le frequenze.

        Nessun loop Python sui punti: i termini di errore sono espansi in array
        e l'inversione della matrice a (2x2) è calcolata in forma chiusa.
        Questo è ordini di grandezza più veloce del loop punto-per-punto e
        gestisce agevolmente 4501 punti a ogni sweep.
        """
        if not self.cal_points:
            return raw

        n = self._n
        if n == 1:
            return self._correct_1port(freqs, raw)

        # ── espandi i termini di errore su tutte le frequenze ──
        cal_f = self.freqs
        # np.interp non gestisce complex direttamente → interpola re/im separati
        def cinterp(vals_per_cp):
            return _cinterp(freqs, cal_f, vals_per_cp)
        D = [cinterp([cp.D[i] for cp in self.cal_points]) for i in range(n)]
        S_ = [cinterp([cp.S[i] for cp in self.cal_points]) for i in range(n)]
        R = [cinterp([cp.R[i] for cp in self.cal_points]) for i in range(n)]
        L = [[cinterp([cp.L[i][j] for cp in self.cal_points]) for j in range(n)] for i in range(n)]
        T = [[cinterp([cp.T[i][j] for cp in self.cal_points]) for j in range(n)] for i in range(n)]
        I = [[cinterp([cp.I[i][j] for cp in self.cal_points]) for j in range(n)] for i in range(n)]

        m = len(freqs)
        def get(name):
            v = raw.get(name)
            return np.asarray(v, dtype=complex) if v is not None else np.zeros(m, dtype=complex)

        # matrice S misurata come array di elementi
        # S[j,i] = misurato ricevente j, eccitante i (0-based)
        Sm = {}
        for i in range(n):
            for j in range(n):
                name = f"S{self.ports[j]}{self.ports[i]}"
                val = get(name)
                if i != j:
                    val = val - I[i][j]
                Sm[(j, i)] = val

        # assembla a[j,i], b[j,i] (array)
        a = {}; b = {}
        for i in range(n):
            for j in range(n):
                if i == j:
                    a[(j, i)] = 1.0 + S_[i] / R[i] * (Sm[(j, i)] - D[i])
                    b[(j, i)] = (1.0 / R[i]) * (Sm[(j, i)] - D[i])
                else:
                    a[(j, i)] = L[i][j] * Sm[(j, i)] / T[i][j]
                    b[(j, i)] = Sm[(j, i)] / T[i][j]

        # S_cal = b · a^-1  (2x2 in forma chiusa, per array)
        if n == 2:
            a11, a12 = a[(0,0)], a[(0,1)]
            a21, a22 = a[(1,0)], a[(1,1)]
            det = a11 * a22 - a12 * a21 + 1e-30
            # inversa
            ia11, ia12 =  a22/det, -a12/det
            ia21, ia22 = -a21/det,  a11/det
            b11, b12 = b[(0,0)], b[(0,1)]
            b21, b22 = b[(1,0)], b[(1,1)]
            Sc = {
                (0,0): b11*ia11 + b12*ia21,
                (0,1): b11*ia12 + b12*ia22,
                (1,0): b21*ia11 + b22*ia21,
                (1,1): b21*ia12 + b22*ia22,
            }
        else:
            # fallback generico (n≠2): loop solo se necessario
            return self._correct_generic(freqs, raw)

        out = {}
        for i in range(n):
            for j in range(n):
                name = f"S{self.ports[j]}{self.ports[i]}"
                if name in raw:
                    out[name] = Sc[(j, i)]
        # mantieni eventuali chiavi non toccate
        for k in raw:
            if k not in out:
                out[k] = raw[k]
        return out

    def _correct_1port(self, freqs, raw):
        """Correzione OSL 1-porta vettorizzata: S11c = (S11m - D)/(R + S·(S11m - D))."""
        cp_f = self.freqs
        def cinterp(vals):
            return _cinterp(freqs, cp_f, vals)
        D = cinterp([cp.D[0] for cp in self.cal_points])
        S = cinterp([cp.S[0] for cp in self.cal_points])
        R = cinterp([cp.R[0] for cp in self.cal_points])
        out = dict(raw)
        key = "S" + f"{self.ports[0]}{self.ports[0]}"
        if key in raw:
            m = np.asarray(raw[key], dtype=complex)
            out[key] = (m - D) / (R + S * (m - D) + 1e-30)
        return out

    def _correct_generic(self, freqs, raw):
        """Fallback loop per n≠2 (raro)."""
        out = {k: np.zeros(len(freqs), dtype=complex) for k in raw}
        n = self._n
        for k, f in enumerate(freqs):
            cp = self._interp_point(f)
            S = np.zeros((n, n), dtype=complex)
            for i in range(n):
                for j in range(n):
                    name = f"S{self.ports[j]}{self.ports[i]}"
                    if name in raw:
                        val = raw[name][k]
                        if i != j:
                            val -= cp.I[i][j]
                        S[j, i] = val
            a = np.zeros((n, n), dtype=complex)
            b = np.zeros((n, n), dtype=complex)
            for i in range(n):
                for j in range(n):
                    if i == j:
                        a[j, i] = 1.0 + cp.S[i]/cp.R[i]*(S[j,i]-cp.D[i])
                        b[j, i] = (1.0/cp.R[i])*(S[j,i]-cp.D[i])
                    else:
                        a[j, i] = cp.L[i][j]*S[j,i]/cp.T[i][j]
                        b[j, i] = S[j,i]/cp.T[i][j]
            try:
                Sc = b @ np.linalg.inv(a)
            except np.linalg.LinAlgError:
                Sc = S
            for i in range(n):
                for j in range(n):
                    name = f"S{self.ports[j]}{self.ports[i]}"
                    if name in out:
                        out[name][k] = Sc[j, i]
        return out

    # ── salva / carica ──────────────────────────────────────────────────────
    def save(self, path: str):
        """Salva i termini di errore in formato JSON."""
        import json
        data = {
            "ports": self.ports,
            "freqs": self.freqs.tolist() if self.freqs is not None else [],
            "points": []
        }
        for cp in self.cal_points:
            data["points"].append({
                "f":  cp.frequency,
                "D":  [[x.real, x.imag] for x in cp.D],
                "S":  [[x.real, x.imag] for x in cp.S],
                "R":  [[x.real, x.imag] for x in cp.R],
                "L":  [[[x.real, x.imag] for x in row] for row in cp.L],
                "T":  [[[x.real, x.imag] for x in row] for row in cp.T],
                "I":  [[[x.real, x.imag] for x in row] for row in cp.I],
            })
        with open(path, "w") as f:
            json.dump(data, f)

    def load(self, path: str):
        """Carica i termini di errore da JSON."""
        import json
        with open(path) as f:
            data = json.load(f)
        self.ports  = data["ports"]
        self._n     = len(self.ports)
        self._pidx  = {p: i for i, p in enumerate(self.ports)}
        self.freqs  = np.array(data["freqs"])
        self.cal_points = []
        def c(pair): return complex(pair[0], pair[1])
        for pt in data["points"]:
            cp = CalPoint(frequency=pt["f"])
            cp.D = [c(x) for x in pt["D"]]
            cp.S = [c(x) for x in pt["S"]]
            cp.R = [c(x) for x in pt["R"]]
            cp.L = [[c(x) for x in row] for row in pt["L"]]
            cp.T = [[c(x) for x in row] for row in pt["T"]]
            cp.I = [[c(x) for x in row] for row in pt["I"]]
            self.cal_points.append(cp)

    # ── helpers ─────────────────────────────────────────────────────────────
    def _check_osl(self) -> bool:
        for p in self.ports:
            if p not in self._meas_open:  return False
            if p not in self._meas_short: return False
            if p not in self._meas_load:  return False
        return True

    @staticmethod
    def _interp(meas_tuple: tuple, f: float) -> complex:
        """Interpolazione lineare su (freqs, values)."""
        freqs, vals = meas_tuple[0], meas_tuple[1]
        if f <= freqs[0]:  return complex(vals[0])
        if f >= freqs[-1]: return complex(vals[-1])
        idx = np.searchsorted(freqs, f)
        f0, f1 = freqs[idx-1], freqs[idx]
        alpha = (f - f0) / (f1 - f0)
        return complex(vals[idx-1]) * (1 - alpha) + complex(vals[idx]) * alpha

    def _interp_point(self, f: float) -> CalPoint:
        """Interpola i CalPoint al valore di frequenza f."""
        if f <= self.cal_points[0].frequency:
            return self.cal_points[0]
        if f >= self.cal_points[-1].frequency:
            return self.cal_points[-1]
        # ricerca binaria
        lo, hi = 0, len(self.cal_points) - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if self.cal_points[mid].frequency <= f:
                lo = mid
            else:
                hi = mid
        cp0, cp1 = self.cal_points[lo], self.cal_points[hi]
        alpha = (f - cp0.frequency) / (cp1.frequency - cp0.frequency)
        return self._lerp_points(cp0, cp1, alpha)

    @staticmethod
    def _lerp(a: complex, b: complex, alpha: float) -> complex:
        return a * (1 - alpha) + b * alpha

    def _lerp_points(self, p0: CalPoint, p1: CalPoint, a: float) -> CalPoint:
        n = self._n
        cp = CalPoint(frequency=p0.frequency * (1-a) + p1.frequency * a)
        cp.D = [self._lerp(p0.D[i], p1.D[i], a) for i in range(n)]
        cp.S = [self._lerp(p0.S[i], p1.S[i], a) for i in range(n)]
        cp.R = [self._lerp(p0.R[i], p1.R[i], a) for i in range(n)]
        cp.L = [[self._lerp(p0.L[i][j], p1.L[i][j], a) for j in range(n)]
                for i in range(n)]
        cp.T = [[self._lerp(p0.T[i][j], p1.T[i][j], a) for j in range(n)]
                for i in range(n)]
        cp.I = [[self._lerp(p0.I[i][j], p1.I[i][j], a) for j in range(n)]
                for i in range(n)]
        return cp
