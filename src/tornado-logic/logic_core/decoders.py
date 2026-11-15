"""Decoder di protocollo (UART, SPI, I2C) su oggetti Capture.

Tutti gli indici sono indici di campione nella cattura; il tempo si ricava con
Capture.time_of(). I decoder non modificano la cattura.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .capture import Capture


# =============================== UART =======================================
@dataclass
class UartFrame:
    start: int
    end: int
    value: int
    framing_error: bool = False
    parity_error: bool = False

    @property
    def ok(self) -> bool:
        return not (self.framing_error or self.parity_error)


def decode_uart(cap: Capture, ch: int, baud: float, data_bits: int = 8, parity: str = "N",
                stop_bits: int = 1, lsb_first: bool = True, invert: bool = False) -> list[UartFrame]:
    """Decodifica UART asincrona. parity: 'N' nessuna, 'E' pari, 'O' dispari."""
    if parity not in ("N", "E", "O"):
        raise ValueError("parity deve essere 'N', 'E' o 'O'")
    if not 5 <= data_bits <= 9:
        raise ValueError("data_bits deve essere in 5..9")
    if stop_bits not in (1, 2):
        raise ValueError("stop_bits deve essere 1 o 2")
    spb = cap.samplerate / baud
    if spb < 3.0:
        raise ValueError(f"campionamento insufficiente: {spb:.2f} campioni/bit (minimo 3)")

    lv = cap.channel(ch)
    if invert:
        lv = ~lv
    n = len(lv)
    falls = np.flatnonzero(np.diff(lv.astype(np.int8)) < 0) + 1
    has_par = parity != "N"
    nbits = 1 + data_bits + int(has_par) + stop_bits

    frames: list[UartFrame] = []
    pos = 0
    while True:
        k = int(np.searchsorted(falls, pos))
        if k >= len(falls):
            break
        s = int(falls[k])
        last = int(s + (nbits - 0.5) * spb)
        if last >= n:  # frame incompleto a fine cattura
            break
        if lv[int(s + 0.5 * spb)]:  # start bit non confermato a metà bit: disturbo
            pos = s + 1
            continue
        bits = [int(lv[int(s + (j + 0.5) * spb)]) for j in range(1, nbits)]
        data = bits[:data_bits]
        value = 0
        if lsb_first:
            for i, b in enumerate(data):
                value |= b << i
        else:
            for b in data:
                value = (value << 1) | b
        parity_error = False
        if has_par:
            ones = sum(data)
            expected = ones % 2 if parity == "E" else 1 - (ones % 2)
            parity_error = bits[data_bits] != expected
        stops = bits[data_bits + int(has_par):]
        framing_error = any(b != 1 for b in stops)
        frames.append(UartFrame(s, min(int(s + nbits * spb), n - 1), value, framing_error, parity_error))
        pos = last
    return frames


# ================================ SPI =======================================
@dataclass
class SpiWord:
    index: int          # campione del primo fronte di campionamento della parola
    mosi: int | None
    miso: int | None
    end: int = 0        # campione dell'ultimo fronte di campionamento della parola


@dataclass
class SpiTransfer:
    start: int
    end: int
    words: list[SpiWord] = field(default_factory=list)
    partial_bits: int = 0   # bit residui che non completano una parola

    @property
    def mosi(self) -> list[int]:
        return [w.mosi for w in self.words if w.mosi is not None]

    @property
    def miso(self) -> list[int]:
        return [w.miso for w in self.words if w.miso is not None]


def decode_spi(cap: Capture, clk: int, mosi: int | None = None, miso: int | None = None,
               cs: int | None = None, cpol: int = 0, cpha: int = 0, bits: int = 8,
               msb_first: bool = True, cs_active_low: bool = True) -> list[SpiTransfer]:
    """Decodifica SPI. Senza CS ritorna un solo trasferimento con tutta la cattura."""
    if mosi is None and miso is None:
        raise ValueError("serve almeno uno tra mosi e miso")
    if cpol not in (0, 1) or cpha not in (0, 1):
        raise ValueError("cpol e cpha devono essere 0 o 1")
    if not 1 <= bits <= 32:
        raise ValueError("bits deve essere in 1..32")

    n = len(cap)
    sample_on_rising = cpol == cpha
    edges = cap.edges(clk, rising=sample_on_rising, falling=not sample_on_rising)
    lv_mo = cap.channel(mosi) if mosi is not None else None
    lv_mi = cap.channel(miso) if miso is not None else None

    if cs is None:
        intervals = [(0, n)]
    else:
        act = cap.channel(cs)
        if cs_active_low:
            act = ~act
        d = np.diff(act.astype(np.int8))
        starts = (np.flatnonzero(d > 0) + 1).tolist()
        ends = (np.flatnonzero(d < 0) + 1).tolist()
        if act[0]:
            starts.insert(0, 0)
        if act[-1]:
            ends.append(n)
        intervals = list(zip(starts, ends))

    weights = (1 << np.arange(bits)[::-1]) if msb_first else (1 << np.arange(bits))
    weights = weights.astype(np.int64)

    def pack(levels: np.ndarray, sel: np.ndarray, nw: int) -> np.ndarray:
        b = levels[sel[:nw * bits]].reshape(nw, bits).astype(np.int64)
        return b @ weights

    transfers: list[SpiTransfer] = []
    for a, b in intervals:
        lo, hi = np.searchsorted(edges, [a, b])
        sel = edges[lo:hi]
        if sel.size == 0:
            continue
        nw, partial = divmod(sel.size, bits)
        words: list[SpiWord] = []
        if nw:
            vm = pack(lv_mo, sel, nw) if lv_mo is not None else None
            vs = pack(lv_mi, sel, nw) if lv_mi is not None else None
            for i in range(nw):
                words.append(SpiWord(int(sel[i * bits]),
                                     int(vm[i]) if vm is not None else None,
                                     int(vs[i]) if vs is not None else None,
                                     int(sel[(i + 1) * bits - 1])))
        transfers.append(SpiTransfer(a, b, words, partial))
    return transfers


# ================================ I2C =======================================
@dataclass
class I2cByte:
    index: int       # campione del fronte di salita SCL del primo bit
    value: int
    ack: bool        # True = ACK (SDA basso sul 9° clock)
    end: int = 0     # campione del fronte di salita SCL del 9° bit (ACK/NACK)


@dataclass
class I2cTransaction:
    start: int
    stop: int | None                  # None se la cattura finisce prima dello STOP
    address: int | None               # indirizzo a 7 bit
    read: bool
    addr_ack: bool
    data: list[I2cByte] = field(default_factory=list)
    ended_with: str = "stop"          # 'stop' | 'restart' | 'end'
    truncated: bool = False           # byte interrotto a metà
    addr_end: int = 0                 # fronte SCL del 9° bit del byte di indirizzo


_ST_START, _ST_STOP, _ST_BIT = 0, 1, 2


def decode_i2c(cap: Capture, scl: int, sda: int) -> list[I2cTransaction]:
    """Decodifica I2C (indirizzamento a 7 bit)."""
    lv_scl = cap.channel(scl)
    lv_sda = cap.channel(sda)

    sda_chg = np.flatnonzero(np.diff(lv_sda.astype(np.int8)) != 0) + 1
    both_high = lv_scl[sda_chg] & lv_scl[sda_chg - 1]
    cond = sda_chg[both_high]
    cond_kind = np.where(lv_sda[cond], _ST_STOP, _ST_START)  # SDA sale = STOP, scende = START

    rises = cap.edges(scl, falling=False)
    bit_val = lv_sda[rises].astype(np.int64)

    idx = np.concatenate((cond, rises))
    kind = np.concatenate((cond_kind, np.full(rises.size, _ST_BIT)))
    val = np.concatenate((np.zeros(cond.size, np.int64), bit_val))
    order = np.lexsort((kind, idx))

    out: list[I2cTransaction] = []
    cur: I2cTransaction | None = None
    nb = 0
    byte_val = 0
    byte_idx = 0
    n_bytes = 0

    def close(end_idx: int | None, how: str) -> None:
        nonlocal cur
        if cur is None:
            return
        cur.stop = end_idx
        cur.ended_with = how
        if nb > 1:  # 1 bit residuo = fronte SCL che precede START/STOP, non un byte reale
            cur.truncated = True
        out.append(cur)
        cur = None

    for i, k, v in zip(idx[order].tolist(), kind[order].tolist(), val[order].tolist()):
        if k == _ST_START:
            close(i, "restart")
            cur = I2cTransaction(i, None, None, False, False)
            nb = byte_val = n_bytes = 0
        elif k == _ST_STOP:
            close(i, "stop")
            nb = 0
        elif cur is not None:  # _ST_BIT
            if nb == 0:
                byte_idx = i
            if nb < 8:
                byte_val = (byte_val << 1) | v
                nb += 1
            else:  # 9° bit: ACK/NACK
                ack = v == 0
                if n_bytes == 0:
                    cur.address = byte_val >> 1
                    cur.read = bool(byte_val & 1)
                    cur.addr_ack = ack
                    cur.addr_end = i
                else:
                    cur.data.append(I2cByte(byte_idx, byte_val, ack, i))
                n_bytes += 1
                nb = byte_val = 0
    close(None, "end")
    return out
