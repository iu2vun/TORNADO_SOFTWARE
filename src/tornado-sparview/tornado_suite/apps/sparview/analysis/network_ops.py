"""Operazioni su reti: stabilità, passività, reciprocità, port extension, de-embedding,
cascata, mixed-mode, renormalizzazione."""
from __future__ import annotations

from typing import Optional, Sequence

import numpy as np

from ..core import params
from ..core.network import Network


# ============================================================ stabilità (2 porte)
def stability_2port(net: Network) -> dict[str, np.ndarray]:
    if net.nports != 2:
        raise ValueError("la stabilità richiede una rete a 2 porte")
    s11, s12, s21, s22 = (net.s[:, 0, 0], net.s[:, 0, 1], net.s[:, 1, 0], net.s[:, 1, 1])
    delta = s11 * s22 - s12 * s21
    with np.errstate(all="ignore"):
        den = 2 * np.abs(s12 * s21)
        k = (1 - np.abs(s11) ** 2 - np.abs(s22) ** 2 + np.abs(delta) ** 2) / den
        mu = (1 - np.abs(s11) ** 2) / (np.abs(s22 - delta * np.conj(s11)) + np.abs(s12 * s21))
        mu_p = (1 - np.abs(s22) ** 2) / (np.abs(s11 - delta * np.conj(s22)) + np.abs(s12 * s21))
        msg = np.abs(s21 / s12)
        mag = np.where(k >= 1, msg * (k - np.sqrt(np.maximum(k ** 2 - 1, 0))), np.nan)
        r = s21 / s12
        u = np.abs(r - 1) ** 2 / (2 * k * np.abs(r) - 2 * np.real(r))
        db = lambda x: 10 * np.log10(np.maximum(x, 1e-30))  # noqa: E731  (potenze)
        gmax = np.where(k >= 1, mag, msg)
    stable = ((k > 1) & (np.abs(delta) < 1)) | (mu > 1)
    return {"k": k, "delta": np.abs(delta), "mu": mu, "mu_p": mu_p, "msg_db": db(msg),
            "mag_db": db(mag), "gmax_db": db(gmax), "u_db": db(u), "stable": stable}


# ================================================== passività / reciprocità
def max_singular_value(net: Network) -> np.ndarray:
    return np.linalg.svd(net.s, compute_uv=False)[:, 0]


def passivity_report(net: Network, tol: float = 1e-3) -> dict:
    sv = max_singular_value(net)
    viol = sv > 1 + tol
    worst = int(np.argmax(sv))
    out = {"max_sv": float(sv[worst]), "f_worst": float(net.freq[worst]), "passive": not viol.any(),
           "n_violations": int(viol.sum()), "sv": sv}
    if viol.any():
        out["f_viol_first"] = float(net.freq[np.flatnonzero(viol)[0]])
        out["f_viol_last"] = float(net.freq[np.flatnonzero(viol)[-1]])
    return out


def reciprocity_report(net: Network, tol: float = 1e-3) -> dict:
    d = np.abs(net.s - np.swapaxes(net.s, 1, 2)).reshape(net.nfreq, -1).max(axis=1)
    worst = int(np.argmax(d))
    return {"max_dev": float(d[worst]), "f_worst": float(net.freq[worst]),
            "reciprocal": bool(d.max() <= tol), "dev": d}


def lossless_report(net: Network, tol: float = 1e-3) -> dict:
    n = net.nports
    ident = np.eye(n)[None]
    d = np.abs(np.swapaxes(net.s.conj(), 1, 2) @ net.s - ident).reshape(net.nfreq, -1).max(axis=1)
    return {"max_dev": float(d.max()), "lossless": bool(d.max() <= tol), "dev": d}


def enforce_passivity(net: Network) -> Network:
    u, sv, vh = np.linalg.svd(net.s)
    sv = np.minimum(sv, 1.0)
    s = (u * sv[:, None, :]) @ vh
    return net.copy(s=s, name=net.name + "_pass")


def enforce_reciprocity(net: Network) -> Network:
    return net.copy(s=0.5 * (net.s + np.swapaxes(net.s, 1, 2)), name=net.name + "_recip")


# =============================================================== port extension
def port_extension(net: Network, delays_s: float | Sequence[float],
                   loss_db_1ghz: float | Sequence[float] = 0.0) -> Network:
    """Sposta il piano di riferimento di ciascuna porta.

    delay > 0: rimuove la linea (il piano si sposta verso il DUT, S11 ruota in senso antiorario...).
    S'_ij = S_ij * exp(+j w (t_i + t_j)) * 10^((A_i(f) + A_j(f))/20), con A(f)=loss_1GHz*sqrt(f/1GHz)
    (compensa la perdita di linea, perdita solo se loss_db_1ghz > 0).
    """
    n = net.nports
    tau = np.broadcast_to(np.asarray(delays_s, float), (n,))
    att = np.broadcast_to(np.asarray(loss_db_1ghz, float), (n,))
    w = 2 * np.pi * net.freq
    ph = np.exp(1j * w[:, None, None] * (tau[None, :, None] + tau[None, None, :]))
    a = att[None, :] * np.sqrt(net.freq[:, None] / 1e9)       # (nf, n) dB di tratto singolo
    gain = 10 ** ((a[:, :, None] + a[:, None, :]) / 20)
    return net.copy(s=net.s * ph * gain, name=net.name + "_ext")


def delay_from_length(length_m: float, velocity_factor: float = 1.0, er: Optional[float] = None) -> float:
    c = 299792458.0
    vf = 1 / np.sqrt(er) if er else velocity_factor
    return length_m / (c * vf)


# ========================================================= cascata / de-embedding
def _common_grid(a: Network, b: Network) -> tuple[Network, Network]:
    if a.nfreq == b.nfreq and np.allclose(a.freq, b.freq, rtol=1e-9, atol=0):
        return a, b
    lo, hi = max(a.fmin, b.fmin), min(a.fmax, b.fmax)
    if lo >= hi:
        raise ValueError("le reti non hanno frequenze in comune")
    base = a.cropped(lo, hi)
    return base, b.interpolated(base.freq)


def _as_z0(net: Network, z0) -> Network:
    return net if np.allclose(net.z0, z0) else net.renormalized(z0)


def _star(sa: np.ndarray, sb: np.ndarray) -> np.ndarray:
    """Prodotto stella (cascata) di due 2-porte con z0 coincidente alla giunzione."""
    a11, a12, a21, a22 = sa[:, 0, 0], sa[:, 0, 1], sa[:, 1, 0], sa[:, 1, 1]
    b11, b12, b21, b22 = sb[:, 0, 0], sb[:, 0, 1], sb[:, 1, 0], sb[:, 1, 1]
    with np.errstate(all="ignore"):
        d = 1.0 - a22 * b11
        out = np.empty_like(sa)
        out[:, 0, 0] = a11 + a12 * b11 * a21 / d
        out[:, 0, 1] = a12 * b12 / d
        out[:, 1, 0] = a21 * b21 / d
        out[:, 1, 1] = b22 + b21 * a22 * b12 / d
    return out


def cascade(a: Network, b: Network) -> Network:
    """Collega la porta 2 di `a` alla porta 1 di `b` (2-porte)."""
    if a.nports != 2 or b.nports != 2:
        raise ValueError("la cascata richiede reti a 2 porte")
    a, b = _common_grid(a, b)
    if not np.isclose(a.z0[1], b.z0[0]):
        b = b.renormalized([a.z0[1], b.z0[1]])
    return Network(a.freq, _star(a.s, b.s), [a.z0[0], b.z0[1]], name=f"{a.name}+{b.name}",
                   comments=[f"cascata di {a.name} e {b.name}"])


def invert_2port(net: Network) -> Network:
    """Rete inversa: cascade(inverse, net) = thru."""
    if net.nports != 2:
        raise ValueError("solo 2 porte")
    t_inv = np.linalg.inv(params.s_to_t(net.s))
    return net.copy(s=params.t_to_s(t_inv), z0=net.z0[::-1].copy(), name=net.name + "_inv")


def flip_ports(net: Network) -> Network:
    """Scambia le porte 1 e 2 di una 2-porte."""
    if net.nports != 2:
        raise ValueError("solo 2 porte")
    s = net.s[:, ::-1, ::-1].copy()
    return net.copy(s=s, z0=net.z0[::-1].copy(), name=net.name + "_flip")


def deembed(meas: Network, left: Optional[Network] = None, right: Optional[Network] = None,
            flip_right: bool = False) -> Network:
    """DUT = left^-1 * meas * right^-1.

    `left`: fixture tra il piano VNA (porta 1) e il DUT (porta 2).
    `right`: fixture tra DUT (porta 1) e piano VNA (porta 2); con flip_right=True si assume invece
    misurata dal lato VNA (porta 1 = VNA) e viene girata.
    """
    if meas.nports != 2:
        raise ValueError("de-embedding definito per misure a 2 porte")
    out = meas
    for fix, side in ((left, "L"), (right, "R")):
        if fix is None:
            continue
        if fix.nports != 2:
            raise ValueError("le fixture devono essere a 2 porte")
        fx = flip_ports(fix) if (side == "R" and flip_right) else fix
        fx = _as_z0(fx, meas.z0)
        if fx.nfreq != out.nfreq or not np.allclose(fx.freq, out.freq, rtol=1e-9, atol=0):
            fx = fx.interpolated(out.freq)
        inv = invert_2port(fx)
        # inv ha z0 invertite rispetto alla fixture: riportale ai valori della misura
        inv = inv.copy(z0=meas.z0.copy()) if np.allclose(inv.z0, meas.z0) else inv
        out = cascade(inv, out) if side == "L" else cascade(out, inv)
    return Network(out.freq, out.s, meas.z0, name=meas.name + "_deemb",
                   comments=meas.comments + ["de-embedded"])


def embed(dut: Network, left: Optional[Network] = None, right: Optional[Network] = None) -> Network:
    out = dut
    if left is not None:
        out = cascade(left, out)
    if right is not None:
        out = cascade(out, right)
    out.name = dut.name + "_emb"
    return out


# ======================================================== mixed-mode / z0
def mixed_mode(net: Network, pairs: list[tuple[int, int]]) -> Network:
    n = net.nports
    if 2 * len(pairs) != n:
        raise ValueError("le coppie devono coprire tutte le porte")
    s = params.to_mixed_mode(net.s, pairs)
    npair = len(pairs)
    info = [("d", k + 1) for k in range(npair)] + [("c", k + 1) for k in range(npair)]
    # impedenze modo: differenziale = 2*Z0, comune = Z0/2 (riferite alle porte fisiche della coppia)
    z0 = np.array([2 * net.z0[a] for a, _ in pairs] + [net.z0[a] / 2 for a, _ in pairs])
    return Network(net.freq, s, z0, name=net.name + "_mm", port_info=info,
                   comments=net.comments + [f"mixed-mode coppie {pairs}"], source_format=net.source_format)


def renormalize(net: Network, z0_new) -> Network:
    out = net.renormalized(z0_new)
    out.name = net.name + "_z0"
    return out
