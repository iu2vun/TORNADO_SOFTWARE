"""Export Touchstone (.s1p/.s2p) e CSV."""
import numpy as np
from .const import Z0, ALL_TRACES


def write_touchstone(path: str, freqs, traces: dict):
    has2 = any(t in traces for t in ("S21", "S12", "S22"))
    with open(path, "w") as f:
        f.write("! TornadoVNA export\n")
        f.write(f"# HZ S RI R {int(Z0)}\n")
        if not has2:
            s11 = traces.get("S11", np.zeros(len(freqs), complex))
            for i, fr in enumerate(freqs):
                f.write(f"{fr:.0f} {s11[i].real:.9e} {s11[i].imag:.9e}\n")
        else:
            z = np.zeros(len(freqs), dtype=complex)
            s11 = traces.get("S11", z); s21 = traces.get("S21", z)
            s12 = traces.get("S12", z); s22 = traces.get("S22", z)
            for i, fr in enumerate(freqs):
                f.write(
                    f"{fr:.0f}"
                    f" {s11[i].real:.9e} {s11[i].imag:.9e}"
                    f" {s21[i].real:.9e} {s21[i].imag:.9e}"
                    f" {s12[i].real:.9e} {s12[i].imag:.9e}"
                    f" {s22[i].real:.9e} {s22[i].imag:.9e}\n"
                )


def write_csv(path: str, freqs, traces: dict):
    names = [t for t in ALL_TRACES if t in traces]
    with open(path, "w") as f:
        hdr = ["freq_Hz"] + [f"{n}_re,{n}_im,{n}_dB" for n in names]
        f.write(",".join(hdr) + "\n")
        for i, fr in enumerate(freqs):
            row = [f"{fr:.0f}"]
            for n in names:
                s = traces[n][i]
                row.append(f"{s.real:.9e},{s.imag:.9e},{20*np.log10(abs(s)+1e-15):.4f}")
            f.write(",".join(row) + "\n")
