"""Backend simulato: 8 canali con segnali noti, trigger software con pre-trigger."""
from __future__ import annotations

import numpy as np

from . import sim_signals as ss
from .capture import Capture
from .device import AcquisitionConfig, LogicDevice, TriggerTimeout
from .trigger import find_trigger

# Mappa canale -> segnale simulato (indice bit 0..7)
SIM_MAP = {
    0: "UART TX 115200 8N1",
    1: "SPI CLK (modo 0, 1 MHz)",
    2: "SPI MOSI",
    3: "SPI MISO",
    4: "SPI CS (attivo basso)",
    5: "I2C SCL (100 kHz)",
    6: "I2C SDA",
    7: "PWM 1 kHz, duty 30 %",
}

# Scenario "seriale": RS-232 e Modbus RTU su RS-485 (periodo 20 ms)
SIM_MAP_SERIAL = {
    0: "RS-232 TX 9600 8N1 ('GET\\r')",
    1: "RS-232 RX 9600 8N1 ('OK\\r\\n')",
    2: "RS-485 RO: Modbus RTU 19200 8E1 (richiesta + risposta)",
    3: "RS-485 DE (master, attivo alto)",
    4: "RS-485 A",
    5: "RS-485 B (ritardato di ~50 ns, con finestra non pilotata A = B)",
    6: "libero (alto)",
    7: "PWM 1 kHz, duty 30 %",
}

# Scenario "walk": un canale alto alla volta (verifica dei collegamenti), 500 ms per canale
SIM_MAP_WALK = {i: f"alto per 500 ms al passo {i + 1} di 8" for i in range(8)}

SCENARIOS = {"mixed": SIM_MAP, "serial": SIM_MAP_SERIAL, "walk": SIM_MAP_WALK}


class SimLogicDevice(LogicDevice):
    def __init__(self, seed: int | None = None, random_phase: bool = True, scenario: str = "mixed") -> None:
        super().__init__()
        if scenario not in SCENARIOS:
            raise ValueError(f"scenario '{scenario}' sconosciuto ({', '.join(SCENARIOS)})")
        self._rng = np.random.default_rng(seed)
        self._random_phase = random_phase
        self.scenario = scenario
        self.name = "SimLogic 8CH" + {"serial": " (seriale)", "walk": " (walk)"}.get(scenario, "")

    def info(self) -> dict:
        return {"name": self.name, "vendor": "simulato", "channels": 8,
                "max_samplerate": max(self.SAMPLERATES), "signals": dict(SCENARIOS[self.scenario])}

    def _render(self, length: int, fs: float, t0: float) -> np.ndarray:
        """Flusso continuo di `length` campioni a partire dall'istante t0."""
        if self.scenario == "serial":
            return self._render_serial(length, fs, t0)
        if self.scenario == "walk":
            return ss.render_walk(length, fs, t0)
        T = length / fs + t0 + 1e-3
        spi = ss.spi_edges(T)
        i2c = ss.i2c_edges(T)
        sigs = [ss.uart_edges(T), spi["clk"], spi["mosi"], spi["miso"], spi["cs"],
                i2c["scl"], i2c["sda"], ss.pwm_edges(T)]
        out = np.zeros(length, dtype=np.uint8)
        for ch, edges in enumerate(sigs):
            out |= ss.render_edges(edges, fs, length, t0) << ch
        return out

    def _render_serial(self, length: int, fs: float, t0: float) -> np.ndarray:
        T = length / fs + t0 + 1e-3
        sc = ss.serial_scenario(T)
        tx, rx, bus, de = (ss.render_edges(sc[k], fs, length, t0) for k in ("tx", "rx", "bus", "de"))
        a = (1 - bus).astype(np.uint8)          # TIA-485: mark (1) = A < B
        b = bus.copy()
        skew = max(1, int(round(50e-9 * fs)))   # B in ritardo: a ogni fronte A == B per ~1 campione
        if length > skew:
            b[skew:] = bus[:-skew]
            b[:skew] = bus[0]
        for ta, tb in sc["free"]:               # bus non pilotato tra richiesta e risposta
            i0 = max(0, int(round((ta - t0) * fs)))
            i1 = min(length, int(round((tb - t0) * fs)))
            if i1 > i0:
                a[i0:i1] = 0
                b[i0:i1] = 0
        pwm = ss.render_edges(ss.pwm_edges(T), fs, length, t0)
        idle = np.ones(length, dtype=np.uint8)
        out = np.zeros(length, dtype=np.uint8)
        for ch, sig in enumerate((tx, rx, bus, de, a, b, idle, pwm)):
            out |= sig.astype(np.uint8) << ch
        return out

    def acquire(self, cfg: AcquisitionConfig) -> Capture:
        self.validate(cfg)
        n, fs = cfg.n_samples, cfg.samplerate
        span = 8 * ss.WALK_STEP if self.scenario == "walk" else 1e-3
        t0 = float(self._rng.uniform(0.0, span)) if self._random_phase else 0.0

        if cfg.trigger is None:
            stream = self._render(n, fs, t0)
            start, trig_idx = 0, None
        else:
            pre = int(cfg.trigger.pretrigger * n)
            stream = self._render(2 * n, fs, t0)  # finestra di ricerca = n campioni
            hit = find_trigger(stream, cfg.trigger, start=pre, stop=n + pre)
            if hit is None:
                raise TriggerTimeout("trigger non verificatosi nella finestra di acquisizione")
            start, trig_idx = hit - pre, pre

        data = stream[start:start + n] & np.uint8(cfg.channel_mask())
        t = cfg.trigger
        return Capture(fs, data, trigger_index=trig_idx, source=self.name,
                       trigger_channel=t.channel if t is not None else None,
                       trigger_mode=t.mode if t is not None else None)
