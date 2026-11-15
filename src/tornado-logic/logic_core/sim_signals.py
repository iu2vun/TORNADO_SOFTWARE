"""Generatori di segnali digitali sintetici (UART, SPI, I2C, PWM) per il backend simulato.

Ogni generatore costruisce un pattern a "slot" di durata fissa e lo converte in una
lista di fronti (livello iniziale, istanti, nuovi livelli); render_edges() li trasforma
in campioni a una data frequenza di campionamento.
"""
from __future__ import annotations

import math

import numpy as np

Edges = tuple[int, np.ndarray, np.ndarray]  # (livello iniziale, tempi [s], livelli dopo il fronte)


def bits_to_edges(bits, slot_t: float) -> Edges:
    bits = np.asarray(bits, dtype=np.uint8)
    idx = np.flatnonzero(np.diff(bits.astype(np.int8))) + 1
    return int(bits[0]), idx * slot_t, bits[idx]


def _tile_to(pattern, n: int) -> np.ndarray:
    pattern = np.asarray(pattern, dtype=np.uint8)
    return np.tile(pattern, -(-n // pattern.size))[:n]


def _n_slots(T: float, slot_t: float) -> int:
    return int(math.ceil(T / slot_t)) + 1


def render_edges(edges: Edges, fs: float, n: int, t0: float = 0.0) -> np.ndarray:
    """Campiona i fronti: n campioni a fs, il primo all'istante t0. Ritorna uint8 0/1."""
    init, t, lv = edges
    idx = np.rint((t - t0) * fs).astype(np.int64)
    before = idx <= 0
    level0 = int(lv[before][-1]) if before.any() else init
    keep = (~before) & (idx < n)
    idx_k = idx[keep]
    lv_k = lv[keep].astype(np.uint8)
    bounds = np.concatenate(([0], idx_k, [n]))
    levels = np.concatenate(([level0], lv_k)).astype(np.uint8)
    return np.repeat(levels, np.diff(bounds))


# --- UART 8N1 ---------------------------------------------------------------
def uart_edges(T: float, baud: float = 115200.0, message: bytes = b"Hello\r\n", idle_bits: int = 20) -> Edges:
    bits: list[int] = [1] * idle_bits
    for byte in message:
        bits += [0] + [(byte >> i) & 1 for i in range(8)] + [1]  # start, LSB first, stop
    slot = 1.0 / baud
    return bits_to_edges(_tile_to(bits, _n_slots(T, slot)), slot)


# --- SPI modo 0, MSB first ----------------------------------------------------
def _msb(byte: int) -> list[int]:
    return [(byte >> i) & 1 for i in range(7, -1, -1)]


def spi_edges(T: float, fclk: float = 1e6, mosi: tuple[int, ...] = (0x9F, 0x00, 0x00),
              miso: tuple[int, ...] = (0x00, 0xEF, 0x40), idle_slots: int = 20) -> dict[str, Edges]:
    if len(mosi) != len(miso):
        raise ValueError("mosi e miso devono avere la stessa lunghezza")
    clk = [0] * idle_slots
    cs = [1] * idle_slots
    mo = [0] * idle_slots
    mi = [0] * idle_slots
    clk += [0, 0]; cs += [0, 0]; mo += [0, 0]; mi += [0, 0]  # CS scende prima del primo clock
    for bm, bs in zip(mosi, miso):
        for a, b in zip(_msb(bm), _msb(bs)):
            clk += [0, 1]; cs += [0, 0]; mo += [a, a]; mi += [b, b]  # dato stabile prima del fronte di salita
    clk += [0, 0]; cs += [0, 0]; mo += [0, 0]; mi += [0, 0]  # coda prima di CS alto
    slot = 0.5 / fclk
    n = _n_slots(T, slot)
    return {name: bits_to_edges(_tile_to(p, n), slot)
            for name, p in (("clk", clk), ("mosi", mo), ("miso", mi), ("cs", cs))}


# --- I2C (scrittura: indirizzo + dati) ---------------------------------------
def i2c_edges(T: float, fscl: float = 100e3, addr: int = 0x50,
              data: tuple[int, ...] = (0x10, 0x3C), read: bool = False,
              acks: tuple[int, ...] | None = None, idle_slots: int = 20) -> dict[str, Edges]:
    """acks: livello del 9° bit per ogni byte (0 = ACK, 1 = NACK); default tutti ACK."""
    frame = [((addr << 1) & 0xFE) | int(read), *data]
    acks = (0,) * len(frame) if acks is None else tuple(acks)
    if len(acks) != len(frame):
        raise ValueError("acks deve avere un elemento per byte (indirizzo incluso)")
    scl = [1] * idle_slots
    sda = [1] * idle_slots
    scl += [1, 1, 0]; sda += [1, 0, 0]  # START: SDA scende con SCL alto
    for byte, ack in zip(frame, acks):
        for b in _msb(byte) + [ack]:  # 8 bit + ACK
            scl += [0, 1, 1, 0]; sda += [b] * 4
    scl += [0, 1, 1]; sda += [0, 0, 1]  # STOP: SDA sale con SCL alto
    slot = 1.0 / (4.0 * fscl)
    n = _n_slots(T, slot)
    return {"scl": bits_to_edges(_tile_to(scl, n), slot),
            "sda": bits_to_edges(_tile_to(sda, n), slot)}


# --- Seriale asincrona (RS-232 / RS-485) -----------------------------------------
def crc16_modbus(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            crc = (crc >> 1) ^ 0xA001 if crc & 1 else crc >> 1
    return crc


def modbus_frame(payload: bytes) -> bytes:
    crc = crc16_modbus(payload)
    return payload + bytes((crc & 0xFF, crc >> 8))  # CRC con il byte basso per primo


def uart_frame_bits(byte: int, data_bits: int = 8, parity: str = "N", stop: int = 1) -> list[int]:
    d = [(byte >> i) & 1 for i in range(data_bits)]  # LSB first
    bits = [0] + d
    if parity != "N":
        ones = sum(d)
        bits.append(ones % 2 if parity == "E" else 1 - ones % 2)
    return bits + [1] * stop


def uart_stream_edges(items: list[tuple[float, bytes]], baud: float, data_bits: int = 8, parity: str = "N",
                      stop: int = 1) -> Edges:
    """Fronti di una linea UART con messaggi (istante di inizio, byte) a frame contigui. Riposo = 1."""
    slot = 1.0 / baud
    ts: list[np.ndarray] = []
    lvs: list[np.ndarray] = []
    for t0, payload in items:
        bits: list[int] = []
        for b in payload:
            bits += uart_frame_bits(b, data_bits, parity, stop)
        ext = np.array([1] + bits + [1], dtype=np.uint8)
        idx = np.flatnonzero(np.diff(ext.astype(np.int8))) + 1
        ts.append(t0 + (idx - 1) * slot)
        lvs.append(ext[idx])
    if not ts:
        return 1, np.empty(0), np.empty(0, dtype=np.uint8)
    return 1, np.concatenate(ts), np.concatenate(lvs)


SERIAL_CYCLE = 20e-3        # periodo di ripetizione dello scenario seriale
SERIAL_DE_LEAD = 100e-6     # DE sale prima dello start bit
SERIAL_DE_LAG = 60e-6       # DE scende dopo l'ultimo stop bit
SERIAL_TURNAROUND = 1.5e-3  # pausa richiesta -> risposta sul bus RS-485
SERIAL232_TURNAROUND = 1.8e-3


def serial_scenario(T: float) -> dict:
    """RS-232 9600 8N1 (TX 'GET\\r', RX 'OK\\r\\n') + Modbus RTU 19200 8E1 su RS-485 con DE.

    Ritorna le liste di fronti per linea e le finestre di bus non pilotato [(t0, t1)].
    """
    tx, rx, bus = [], [], []
    de_t: list[float] = []
    de_lv: list[int] = []
    free: list[tuple[float, float]] = []
    char485 = 11 / 19200.0
    char232 = 10 / 9600.0
    k = 0
    while k * SERIAL_CYCLE < T:
        base = k * SERIAL_CYCLE
        t_tx = base + 0.2e-3
        tx.append((t_tx, b"GET\r"))
        rx.append((t_tx + 4 * char232 + SERIAL232_TURNAROUND, b"OK\r\n"))

        req = modbus_frame(bytes([1, 3, 0, 0, 0, 2]))
        rsp = modbus_frame(bytes([1, 3, 4]) + (0x000A + k).to_bytes(2, "big") + (0x000B).to_bytes(2, "big"))
        t_req = base + 1.0e-3
        t_req_end = t_req + len(req) * char485
        t_rsp = t_req_end + SERIAL_TURNAROUND
        bus += [(t_req, req), (t_rsp, rsp)]
        de_t += [t_req - SERIAL_DE_LEAD, t_req_end + SERIAL_DE_LAG]
        de_lv += [1, 0]
        free.append((t_req_end + 0.15e-3, t_rsp - 0.1e-3))
        k += 1
    de_init = 0
    return {
        "tx": uart_stream_edges(tx, 9600, 8, "N", 1),
        "rx": uart_stream_edges(rx, 9600, 8, "N", 1),
        "bus": uart_stream_edges(bus, 19200, 8, "E", 1),
        "de": (de_init, np.array(de_t), np.array(de_lv, dtype=np.uint8)),
        "free": free,
    }


# --- PWM ---------------------------------------------------------------------
def pwm_edges(T: float, freq: float = 1e3, duty: float = 0.3) -> Edges:
    if not 0.0 < duty < 1.0:
        raise ValueError("duty deve essere in (0, 1)")
    per = 100
    hi = max(1, min(per - 11, int(round(duty * per))))
    pattern = [0] * 10 + [1] * hi + [0] * (per - 10 - hi)
    slot = 1.0 / (freq * per)
    return bits_to_edges(_tile_to(pattern, _n_slots(T, slot)), slot)


# --- modo "walk" del tester: un canale alto alla volta, CH1 -> CH8, un passo ogni WALK_STEP ---------------
WALK_STEP = 0.5


def render_walk(length: int, fs: float, phase: float = 0.0, step: float = WALK_STEP) -> np.ndarray:
    """Campioni uint8 del modo walk: al passo k (da 0) è alto solo il canale k % 8. phase = istante (s) nel giro."""
    t = (np.arange(length, dtype=np.float64) / fs + phase) % (8 * step)
    slot = np.minimum((t / step).astype(np.int64), 7)
    return (np.uint8(1) << slot.astype(np.uint8)).astype(np.uint8)
