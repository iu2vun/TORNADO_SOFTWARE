"""Conversioni tra parametri di rete (S, Z, Y, ABCD, T, H, G), renormalizzazione,
mixed-mode. Tutte le funzioni sono vettorizzate sull'asse delle frequenze:
le matrici hanno shape (nf, N, N). Le impedenze di riferimento sono reali (N,).
"""
from __future__ import annotations

import numpy as np


def _eye(n: int, nf: int) -> np.ndarray:
    return np.broadcast_to(np.eye(n, dtype=complex), (nf, n, n))


def _solve(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Risolve a x = b per ogni frequenza; punti singolari -> nan (nessuna eccezione)."""
    try:
        return np.linalg.solve(a, b)
    except np.linalg.LinAlgError:
        out = np.full(b.shape, np.nan + 0j)
        for k in range(a.shape[0]):
            try:
                out[k] = np.linalg.solve(a[k], b[k])
            except np.linalg.LinAlgError:
                pass
        return out


def _sqrt_diag(z0: np.ndarray) -> np.ndarray:
    return np.sqrt(np.asarray(z0, float))


def s_to_z(s: np.ndarray, z0) -> np.ndarray:
    nf, n, _ = s.shape
    r = _sqrt_diag(z0)
    x = _solve(_eye(n, nf) - s, _eye(n, nf) + s)
    return r[None, :, None] * x * r[None, None, :]


def z_to_s(z: np.ndarray, z0) -> np.ndarray:
    nf, n, _ = z.shape
    r = _sqrt_diag(z0)
    zn = z / r[None, :, None] / r[None, None, :]
    return _solve(zn + _eye(n, nf), zn - _eye(n, nf))


def s_to_y(s: np.ndarray, z0) -> np.ndarray:
    nf, n, _ = s.shape
    g = 1.0 / _sqrt_diag(z0)
    x = _solve(_eye(n, nf) + s, _eye(n, nf) - s)
    return g[None, :, None] * x * g[None, None, :]


def y_to_s(y: np.ndarray, z0) -> np.ndarray:
    nf, n, _ = y.shape
    r = _sqrt_diag(z0)
    yn = r[None, :, None] * y * r[None, None, :]
    return _solve(_eye(n, nf) + yn, _eye(n, nf) - yn)


def _need_2port(s: np.ndarray):
    if s.shape[1] != 2:
        raise ValueError("operazione definita solo per reti a 2 porte")


def s_to_abcd(s: np.ndarray, z0) -> np.ndarray:
    _need_2port(s)
    z1, z2 = float(z0[0]), float(z0[1])
    s11, s12, s21, s22 = s[:, 0, 0], s[:, 0, 1], s[:, 1, 0], s[:, 1, 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        d = 2 * s21
        a = np.sqrt(z1 / z2) * ((1 + s11) * (1 - s22) + s12 * s21) / d
        b = np.sqrt(z1 * z2) * ((1 + s11) * (1 + s22) - s12 * s21) / d
        c = ((1 - s11) * (1 - s22) - s12 * s21) / d / np.sqrt(z1 * z2)
        dd = np.sqrt(z2 / z1) * ((1 - s11) * (1 + s22) + s12 * s21) / d
    out = np.empty_like(s)
    out[:, 0, 0], out[:, 0, 1], out[:, 1, 0], out[:, 1, 1] = a, b, c, dd
    return out


def abcd_to_s(m: np.ndarray, z0) -> np.ndarray:
    _need_2port(m)
    z1, z2 = float(z0[0]), float(z0[1])
    a = m[:, 0, 0] * np.sqrt(z2 / z1)
    b = m[:, 0, 1] / np.sqrt(z1 * z2)
    c = m[:, 1, 0] * np.sqrt(z1 * z2)
    d = m[:, 1, 1] * np.sqrt(z1 / z2)
    den = a + b + c + d
    out = np.empty_like(m)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[:, 0, 0] = (a + b - c - d) / den
        out[:, 0, 1] = 2 * (a * d - b * c) / den
        out[:, 1, 0] = 2 / den
        out[:, 1, 1] = (-a + b - c + d) / den
    return out


def s_to_t(s: np.ndarray) -> np.ndarray:
    """Matrice di trasferimento: [b1; a1] = T [a2; b2]. Cascata: T = T_A @ T_B."""
    _need_2port(s)
    s11, s12, s21, s22 = s[:, 0, 0], s[:, 0, 1], s[:, 1, 0], s[:, 1, 1]
    out = np.empty_like(s)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[:, 0, 0] = s12 - s11 * s22 / s21
        out[:, 0, 1] = s11 / s21
        out[:, 1, 0] = -s22 / s21
        out[:, 1, 1] = 1.0 / s21
    return out


def t_to_s(t: np.ndarray) -> np.ndarray:
    _need_2port(t)
    out = np.empty_like(t)
    with np.errstate(divide="ignore", invalid="ignore"):
        out[:, 0, 0] = t[:, 0, 1] / t[:, 1, 1]
        out[:, 0, 1] = t[:, 0, 0] - t[:, 0, 1] * t[:, 1, 0] / t[:, 1, 1]
        out[:, 1, 0] = 1.0 / t[:, 1, 1]
        out[:, 1, 1] = -t[:, 1, 0] / t[:, 1, 1]
    return out


def h_to_z(h: np.ndarray) -> np.ndarray:
    _need_2port(h)
    h11, h12, h21, h22 = h[:, 0, 0], h[:, 0, 1], h[:, 1, 0], h[:, 1, 1]
    dh = h11 * h22 - h12 * h21
    out = np.empty_like(h)
    out[:, 0, 0] = dh / h22
    out[:, 0, 1] = h12 / h22
    out[:, 1, 0] = -h21 / h22
    out[:, 1, 1] = 1.0 / h22
    return out


def g_to_z(g: np.ndarray) -> np.ndarray:
    _need_2port(g)
    return h_to_z(np.linalg.inv(g))


def renormalize(s: np.ndarray, z0_old, z0_new) -> np.ndarray:
    """Cambio dell'impedenza di riferimento (reale) per ogni porta.

    S' = K (S - G)(I - G S)^-1 K^-1,  G = diag((Zn - Zo)/(Zn + Zo)),  K = diag(1/sqrt(1-G^2)).
    """
    z0_old = np.asarray(z0_old, float)
    z0_new = np.broadcast_to(np.asarray(z0_new, float), z0_old.shape)
    if np.allclose(z0_old, z0_new):
        return s.copy()
    nf, n, _ = s.shape
    rho = (z0_new - z0_old) / (z0_new + z0_old)
    k = 1.0 / np.sqrt(1.0 - rho ** 2)
    gam = np.diag(rho).astype(complex)[None]
    num = s - gam
    den = _eye(n, nf) - gam @ s
    # num @ den^-1  ->  (den^T)^-1 num^T trasposto
    x = np.swapaxes(_solve(np.swapaxes(den, 1, 2), np.swapaxes(num, 1, 2)), 1, 2)
    return k[None, :, None] * x / k[None, None, :]


def mixed_mode_matrix(n_ports: int, pairs: list[tuple[int, int]]) -> np.ndarray:
    """Matrice M (n_ports x n_ports) tale che S_mm = M S M^-1.

    Righe: prima i modi differenziali delle coppie, poi i modi comuni.
    Per la coppia (a, b): d = (a - b)/sqrt2, c = (a + b)/sqrt2.
    """
    npair = len(pairs)
    if 2 * npair != n_ports:
        raise ValueError("le coppie devono coprire tutte le porte")
    used = sorted(p for pr in pairs for p in pr)
    if used != list(range(n_ports)):
        raise ValueError("ogni porta deve comparire in una sola coppia")
    m = np.zeros((n_ports, n_ports))
    r = 1.0 / np.sqrt(2.0)
    for k, (a, b) in enumerate(pairs):
        m[k, a], m[k, b] = r, -r
        m[npair + k, a], m[npair + k, b] = r, r
    return m


def to_mixed_mode(s: np.ndarray, pairs: list[tuple[int, int]]) -> np.ndarray:
    m = mixed_mode_matrix(s.shape[1], pairs)
    return m[None] @ s @ np.linalg.inv(m)[None]
