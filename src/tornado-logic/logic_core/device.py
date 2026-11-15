"""Interfaccia astratta dell'analizzatore logico + configurazione di acquisizione."""
from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass

from .capture import N_CHANNELS, Capture

# Frequenze di campionamento tipiche di fx2lafw (cloni Saleae Logic 8 su FX2LP).
# DA VERIFICARE sull'hardware con: sigrok-cli --driver fx2lafw --show
FX2_SAMPLERATES = (20e3, 25e3, 50e3, 100e3, 200e3, 250e3, 500e3,
                   1e6, 2e6, 3e6, 4e6, 6e6, 8e6, 12e6, 16e6, 24e6)

TRIGGER_MODES = ("rising", "falling", "either", "high", "low")
MAX_SAMPLES = 100_000_000


class DeviceError(Exception):
    """Errore generico del dispositivo."""


class ConfigError(DeviceError):
    """Configurazione di acquisizione non valida."""


class TriggerTimeout(DeviceError):
    """Trigger non verificatosi entro la finestra di acquisizione."""


@dataclass(frozen=True)
class Trigger:
    channel: int
    mode: str = "rising"
    pretrigger: float = 0.1  # frazione della cattura prima del trigger (0..1)

    def __post_init__(self) -> None:
        if not 0 <= self.channel < N_CHANNELS:
            raise ConfigError(f"canale trigger {self.channel} non valido")
        if self.mode not in TRIGGER_MODES:
            raise ConfigError(f"modo trigger '{self.mode}' non valido ({TRIGGER_MODES})")
        if not 0.0 <= self.pretrigger < 1.0:
            raise ConfigError("pretrigger deve essere in [0, 1)")


@dataclass
class AcquisitionConfig:
    samplerate: float
    n_samples: int
    channels: tuple[int, ...] = tuple(range(N_CHANNELS))
    trigger: Trigger | None = None
    timeout_s: float = 10.0
    hw_trigger: bool = False        # trigger gestito da sigrok (attesa illimitata fino al timeout); solo backend hardware

    def channel_mask(self) -> int:
        m = 0
        for ch in self.channels:
            m |= 1 << ch
        return m


class LogicDevice(ABC):
    """Contratto comune a backend simulato, sigrok e USB diretto."""

    name = "logic-device"
    SAMPLERATES: tuple[float, ...] = FX2_SAMPLERATES

    def __init__(self) -> None:
        self._open = False

    # --- ciclo di vita -------------------------------------------------
    def open(self) -> None:
        self._open = True

    def close(self) -> None:
        self._open = False

    @property
    def is_open(self) -> bool:
        return self._open

    def __enter__(self) -> "LogicDevice":
        self.open()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- informazioni --------------------------------------------------
    @abstractmethod
    def info(self) -> dict:
        """Dizionario descrittivo (nome, canali, rate massimo, ...)."""

    def supported_samplerates(self) -> tuple[float, ...]:
        return self.SAMPLERATES

    def nearest_samplerate(self, fs: float) -> float:
        return min(self.SAMPLERATES, key=lambda r: abs(math.log(r / fs)))

    # --- acquisizione --------------------------------------------------
    def validate(self, cfg: AcquisitionConfig) -> None:
        if not self._open:
            raise DeviceError("dispositivo non aperto")
        if not any(math.isclose(cfg.samplerate, r, rel_tol=1e-9) for r in self.SAMPLERATES):
            raise ConfigError(f"samplerate {cfg.samplerate:g} Hz non supportato")
        if not 1 <= cfg.n_samples <= MAX_SAMPLES:
            raise ConfigError(f"n_samples deve essere in 1..{MAX_SAMPLES}")
        if not cfg.channels or any(not 0 <= c < N_CHANNELS for c in cfg.channels):
            raise ConfigError("elenco canali non valido")
        if cfg.trigger is not None and cfg.trigger.channel not in cfg.channels:
            raise ConfigError("il canale di trigger deve essere tra quelli abilitati")

    @abstractmethod
    def acquire(self, cfg: AcquisitionConfig) -> Capture:
        """Acquisizione bloccante di cfg.n_samples campioni (con trigger software se richiesto)."""

    def abort(self) -> None:
        """Interrompe un'acquisizione in corso (no-op di default)."""
