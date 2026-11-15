"""Analisi di una cattura: decoder -> lane di annotazioni; misure e glitch per canale. Nessuna dipendenza da Qt."""
from __future__ import annotations

from dataclasses import dataclass, field, replace

from .annotate import Lane, i2c_annotations, spi_annotations, uart_annotations
from .capture import N_CHANNELS, Capture
from .decoders import decode_i2c, decode_spi, decode_uart
from .measure import ChannelStats, Glitch, find_glitches, measure_channel
from .serialbus import SerialCfg, SerialResult, run_serial

MAX_GLITCHES = 10_000


@dataclass
class UartCfg:
    enabled: bool = True
    ch: int = 0
    baud: float = 115200.0
    data_bits: int = 8
    parity: str = "N"
    stop_bits: int = 1
    lsb_first: bool = True
    invert: bool = False


@dataclass
class SpiCfg:
    enabled: bool = True
    clk: int = 1
    mosi: int | None = 2
    miso: int | None = 3
    cs: int | None = 4
    cpol: int = 0
    cpha: int = 0
    bits: int = 8
    msb_first: bool = True
    cs_active_low: bool = True


@dataclass
class I2cCfg:
    enabled: bool = True
    scl: int = 5
    sda: int = 6


@dataclass
class DecoderConfig:
    uart: UartCfg = field(default_factory=UartCfg)
    spi: SpiCfg = field(default_factory=SpiCfg)
    i2c: I2cCfg = field(default_factory=I2cCfg)
    serial: SerialCfg = field(default_factory=SerialCfg)


@dataclass
class DecodeResult:
    lanes: list[Lane] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    serial: SerialResult | None = None


@dataclass
class MeasureResult:
    stats: list[ChannelStats] = field(default_factory=list)
    glitches: list[tuple[int, Glitch]] = field(default_factory=list)  # (canale, glitch) con indice assoluto
    glitches_truncated: bool = False


def run_decoders(cap: Capture, cfg: DecoderConfig) -> DecodeResult:
    res = DecodeResult()

    u = cfg.uart
    if u.enabled:
        try:
            frames = decode_uart(cap, u.ch, u.baud, u.data_bits, u.parity, u.stop_bits, u.lsb_first, u.invert)
            res.lanes.append(Lane("UART", uart_annotations(frames)))
        except ValueError as e:
            res.errors.append(f"UART: {e}")

    s = cfg.spi
    if s.enabled:
        try:
            transfers = decode_spi(cap, s.clk, s.mosi, s.miso, s.cs, s.cpol, s.cpha, s.bits,
                                   s.msb_first, s.cs_active_low)
            if s.mosi is not None:
                res.lanes.append(Lane("SPI MOSI", spi_annotations(transfers, "mosi", s.bits)))
            if s.miso is not None:
                res.lanes.append(Lane("SPI MISO", spi_annotations(transfers, "miso", s.bits)))
        except ValueError as e:
            res.errors.append(f"SPI: {e}")

    i = cfg.i2c
    if i.enabled:
        try:
            res.lanes.append(Lane("I2C", i2c_annotations(decode_i2c(cap, i.scl, i.sda))))
        except ValueError as e:
            res.errors.append(f"I2C: {e}")

    if cfg.serial.enabled:
        try:
            res.serial = run_serial(cap, cfg.serial)
            res.lanes.extend(res.serial.lanes)
        except ValueError as e:
            res.errors.append(f"Seriale: {e}")
    return res


def run_measures(cap: Capture, channels: tuple[int, ...] | list[int] = tuple(range(N_CHANNELS)),
                 glitch_s: float = 100e-9, sample_range: tuple[int, int] | None = None) -> MeasureResult:
    """Misure e glitch sui canali indicati; con sample_range solo sull'intervallo [a, b) di campioni."""
    work, offset = cap, 0
    if sample_range is not None:
        a, b = max(0, int(sample_range[0])), min(len(cap), int(sample_range[1]))
        if b - a >= 2:
            work, offset = cap.slice(a, b), a
    res = MeasureResult()
    for ch in channels:
        res.stats.append(measure_channel(work, ch))
        for g in find_glitches(work, ch, glitch_s):
            if len(res.glitches) >= MAX_GLITCHES:
                res.glitches_truncated = True
                break
            res.glitches.append((ch, replace(g, index=g.index + offset)))
    return res
