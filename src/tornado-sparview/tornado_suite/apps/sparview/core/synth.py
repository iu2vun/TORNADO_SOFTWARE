"""Generatori di reti sintetiche (esempi e test): filtro, antenna, linea differenziale."""
from __future__ import annotations

import numpy as np

from . import params
from .network import Network


def _abcd_series(z: np.ndarray) -> np.ndarray:
    m = np.zeros((z.size, 2, 2), complex)
    m[:, 0, 0] = m[:, 1, 1] = 1
    m[:, 0, 1] = z
    return m


def _abcd_shunt(y: np.ndarray) -> np.ndarray:
    m = np.zeros((y.size, 2, 2), complex)
    m[:, 0, 0] = m[:, 1, 1] = 1
    m[:, 1, 0] = y
    return m


def _abcd_line(freq, z_line: float, delay: float, loss_db_per_s: float = 0.0) -> np.ndarray:
    gam = (loss_db_per_s / 8.685889638) + 1j * 2 * np.pi * freq
    th = gam * delay
    m = np.zeros((freq.size, 2, 2), complex)
    m[:, 0, 0] = m[:, 1, 1] = np.cosh(th)
    m[:, 0, 1] = z_line * np.sinh(th)
    m[:, 1, 0] = np.sinh(th) / z_line
    return m


def bandpass_filter(f1=0.5e9, f2=1.5e9, npts=801, f0=1.0e9, q_unloaded=120.0, noise_db=-90.0,
                    seed=1) -> Network:
    """Filtro passa-banda a 3 risonatori LC accoppiati con condensatori serie (2 porte, 50 ohm)."""
    rng = np.random.default_rng(seed)
    f = np.linspace(f1, f2, npts)
    w = 2 * np.pi * f
    w0 = 2 * np.pi * f0
    l_res = 8e-9
    c_res = 1 / (w0 ** 2 * l_res) - 0.85e-12
    g_loss = 1.0 / (q_unloaded * w0 * l_res)
    cc = 0.2e-12
    cin = 0.6e-12
    yres = 1j * w * c_res + 1 / (1j * w * l_res) + g_loss
    m = np.tile(np.eye(2, dtype=complex), (f.size, 1, 1))
    for blk in (_abcd_series(1 / (1j * w * cin)), _abcd_shunt(yres),
                _abcd_series(1 / (1j * w * cc)), _abcd_shunt(yres),
                _abcd_series(1 / (1j * w * cc)), _abcd_shunt(yres),
                _abcd_series(1 / (1j * w * cin))):
        m = m @ blk
    s = params.abcd_to_s(m, [50.0, 50.0])
    n = 10 ** (noise_db / 20)
    s = s + n * (rng.standard_normal(s.shape) + 1j * rng.standard_normal(s.shape))
    s[:, 0, 1] = s[:, 1, 0] = 0.5 * (s[:, 0, 1] + s[:, 1, 0])
    return Network(f, s, 50.0, name="filtro_bandpass_1GHz", comments=["Filtro passa-banda sintetico 3 risonatori"],
                   source_format="sintetico")


def antenna_one_port(f1=0.5e9, f2=3.5e9, npts=1001, cable_delay=0.4e-9, noise_db=-80.0, seed=2) -> Network:
    """Antenna sintetica con due risonanze serie (circa 0.9 e 2.4 GHz) dietro un cavo."""
    rng = np.random.default_rng(seed)
    f = np.linspace(f1, f2, npts)
    w = 2 * np.pi * f
    y = np.full(f.size, 1 / 300.0, dtype=complex)
    for f_r, r, q in ((0.9e9, 45.0, 14.0), (2.4e9, 38.0, 25.0)):
        wr = 2 * np.pi * f_r
        l = q * r / wr
        c = 1 / (wr ** 2 * l)
        y += 1.0 / (r + 1j * (w * l - 1 / (w * c)))
    ztot = 1.0 / y + 1j * w * 3e-9
    gam = (ztot - 50) / (ztot + 50)
    gam = gam * np.exp(-1j * 2 * w * cable_delay) * 10 ** (-0.15 * (f / 1e9) / 20)
    n = 10 ** (noise_db / 20)
    gam = gam + n * (rng.standard_normal(f.size) + 1j * rng.standard_normal(f.size))
    s = gam.reshape(-1, 1, 1)
    return Network(f, s, 50.0, name="antenna_dualband", comments=["Antenna sintetica dual-band con cavo"],
                   source_format="sintetico")


def differential_line(f1=10e6, f2=6e9, npts=601, delay_d=1.0e-9, delay_c=1.08e-9) -> Network:
    """4 porte (coppie 1-2 ingresso, 3-4 uscita) costruite da un modello mixed-mode."""
    f = np.linspace(f1, f2, npts)
    w = 2 * np.pi * f
    smm = np.zeros((f.size, 4, 4), complex)
    loss_d = 10 ** (-(0.4 * (f / 1e9) + 0.1) / 20)
    loss_c = 10 ** (-(0.7 * (f / 1e9) + 0.3) / 20)
    sdd21 = loss_d * np.exp(-1j * w * delay_d)
    scc21 = loss_c * np.exp(-1j * w * delay_c)
    scd21 = 0.04 * (f / 6e9) * np.exp(-1j * w * (delay_d + delay_c) / 2)
    ref_d = 0.05 * np.exp(-1j * 2 * w * 0.2e-9)
    ref_c = 0.12 * np.exp(-1j * 2 * w * 0.2e-9)
    # ordine: d1 d2 c1 c2 ; d1=ingresso diff, d2=uscita diff, c1/c2 comuni
    smm[:, 0, 0] = smm[:, 1, 1] = ref_d
    smm[:, 0, 1] = smm[:, 1, 0] = sdd21
    smm[:, 2, 2] = smm[:, 3, 3] = ref_c
    smm[:, 2, 3] = smm[:, 3, 2] = scc21
    smm[:, 0, 3] = smm[:, 3, 0] = scd21
    smm[:, 1, 2] = smm[:, 2, 1] = scd21
    m = params.mixed_mode_matrix(4, [(0, 1), (2, 3)])
    s = np.linalg.inv(m)[None] @ smm @ m[None]
    return Network(f, s, 50.0, name="linea_differenziale_4p",
                   comments=["Linea differenziale sintetica 4 porte (coppie 1-2 e 3-4)"], source_format="sintetico")
