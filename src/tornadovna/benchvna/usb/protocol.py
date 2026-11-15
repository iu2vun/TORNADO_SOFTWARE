"""
LibreVNA USB binary protocol — Python port di Protocol.hpp / Protocol.cpp.

Struttura pacchetto (PacketConstants.h):
  [0x5A][len_lo][len_hi][type][payload...][crc0][crc1][crc2][crc3]
  len = lunghezza totale inclusi header (4 B) e CRC (4 B) = 8 + payload
  CRC32 calcolato su tutto il pacchetto esclusi gli ultimi 4 byte CRC.

VNADatapoint (type=27): CRC fissato a 0x00000000 (ottimizzazione throughput).
Tutti gli altri: CRC normale.

Riferimento sorgente:
  Software/VNA_embedded/Application/Communication/Protocol.hpp
  Software/VNA_embedded/Application/Communication/Protocol.cpp
  Software/VNA_embedded/Application/Communication/PacketConstants.h
"""

import struct
import dataclasses
from enum import IntEnum
from typing import Optional


# ---------------------------------------------------------------------------
# Costanti (PacketConstants.h)
# ---------------------------------------------------------------------------
HEADER_BYTE     = 0x5A
HEADER_LEN      = 1
LENGTH_LEN      = 2
TYPE_LEN        = 1
CRC_LEN         = 4
COMBINED_HEADER = HEADER_LEN + LENGTH_LEN + TYPE_LEN   # 4 byte
EXCL_PAYLOAD    = COMBINED_HEADER + CRC_LEN            # 8 byte

# VNADatapoint payload field sizes
DPNT_FREQ_LEN   = 8
DPNT_POW_LEN    = 2
DPNT_PNT_LEN    = 2
DPNT_REAL_LEN   = 4
DPNT_IMAG_LEN   = 4
DPNT_DESC_LEN   = 1

# descriptor bitmasks
DPNT_STAGE_OFFSET = 5   # bits [7:5] = stage
DPNT_PORT_MASK    = 0x1F  # bits [4:0] = port bitmask (P1=0x01,P2=0x02,Ref=0x10)

CRC32_POLY = 0xEDB88320


# ---------------------------------------------------------------------------
# PacketType enum (Protocol.hpp)
# ---------------------------------------------------------------------------
class PacketType(IntEnum):
    NONE                      = 0
    SWEEP_SETTINGS            = 2
    MANUAL_STATUS             = 3
    MANUAL_CONTROL            = 4
    DEVICE_INFO               = 5
    FIRMWARE_PACKET           = 6
    ACK                       = 7
    CLEAR_FLASH               = 8
    PERFORM_FIRMWARE_UPDATE   = 9
    NACK                      = 10
    REFERENCE                 = 11
    GENERATOR                 = 12
    SA_SETTINGS               = 13
    SA_RESULT                 = 14
    REQUEST_DEVICE_INFO       = 15
    REQUEST_SOURCE_CAL        = 16
    REQUEST_RECEIVER_CAL      = 17
    SOURCE_CAL_POINT          = 18
    RECEIVER_CAL_POINT        = 19
    SET_IDLE                  = 20
    REQUEST_FREQ_CORRECTION   = 21
    FREQ_CORRECTION           = 22
    REQUEST_DEVICE_CONFIG     = 23
    DEVICE_CONFIG             = 24
    DEVICE_STATUS             = 25
    REQUEST_DEVICE_STATUS     = 26
    VNA_DATAPOINT             = 27
    SET_TRIGGER               = 28
    CLEAR_TRIGGER             = 29
    STOP_STATUS_UPDATES       = 30
    START_STATUS_UPDATES      = 31
    INITIATE_SWEEP            = 32
    PERFORM_ACTION            = 33
    RESET_DEVICE_CONFIG       = 34


# ---------------------------------------------------------------------------
# CRC32 (identico a Protocol.cpp CRC32_POLYGON = 0xEDB88320)
# ---------------------------------------------------------------------------
def crc32(data: bytes | bytearray) -> int:
    crc = 0xFFFFFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ CRC32_POLY
            else:
                crc >>= 1
    return crc ^ 0xFFFFFFFF


# ---------------------------------------------------------------------------
# SweepSettings struct (Protocol.hpp, #pragma pack(1))
#
# uint64 f_start, uint64 f_stop, uint16 points, uint32 if_bandwidth,
# int16 cdbm_excitation_start, uint8 flags1, uint16 stages_and_ports,
# int16 cdbm_excitation_stop, uint16 dwell_time
#
# flags1 byte:  standby[0] syncMaster[1] suppressPeaks[2]
#               fixedPowerSetting[3] logSweep[4] syncMode[6:5] unused[7]
# stages_and_ports uint16:
#   stages[2:0] port1Stage[5:3] port2Stage[8:6] port3Stage[11:9]
#   port4Stage[14:12] unused[15]
# ---------------------------------------------------------------------------
SWEEP_SETTINGS_FMT = "<QQHIhBHhH"
SWEEP_SETTINGS_SIZE = struct.calcsize(SWEEP_SETTINGS_FMT)  # should be 27


@dataclasses.dataclass
class SweepSettings:
    f_start:               int   = 100_000_000
    f_stop:                int   = 6_000_000_000
    points:                int   = 501
    if_bandwidth:          int   = 1000
    cdbm_excitation_start: int   = -1000   # -10 dBm in 1/100 dBm
    cdbm_excitation_stop:  int   = -1000
    stages:                int   = 1       # 0=1stage(S11 only), 1=2stages(all)
    port1_stage:           int   = 0
    port2_stage:           int   = 1
    suppress_peaks:        bool  = True
    fixed_power:           bool  = True
    log_sweep:             bool  = False
    dwell_time:            int   = 0       # µs

    def encode(self) -> bytes:
        flags1 = (
            (0)                       |   # standby=0
            (0 << 1)                  |   # syncMaster=0
            (int(self.suppress_peaks) << 2) |
            (int(self.fixed_power)    << 3) |
            (int(self.log_sweep)      << 4)
            # syncMode=0 (bits 6:5), unused=0
        )
        stages_ports = (
            (self.stages       & 0x07)       |
            ((self.port1_stage & 0x07) << 3) |
            ((self.port2_stage & 0x07) << 6)
        )
        return struct.pack(
            SWEEP_SETTINGS_FMT,
            self.f_start, self.f_stop,
            self.points, self.if_bandwidth,
            self.cdbm_excitation_start,
            flags1, stages_ports,
            self.cdbm_excitation_stop,
            self.dwell_time,
        )


# ---------------------------------------------------------------------------
# DeviceInfo struct (Protocol.hpp)
# uint16 ProtocolVersion, uint8 FW_major/minor/patch, uint8 hw_version,
# char HW_Revision, uint64 limits_minFreq, uint64 limits_maxFreq,
# uint32 limits_minIFBW, uint32 limits_maxIFBW, uint16 limits_maxPoints,
# int16 limits_cdbm_min, int16 limits_cdbm_max,
# uint32 limits_minRBW, uint32 limits_maxRBW,
# uint8 limits_maxAmplitudePoints, uint64 limits_maxFreqHarmonic,
# uint8 num_ports, uint16 limits_maxDwellTime
# ---------------------------------------------------------------------------
DEVICE_INFO_FMT  = "<HBBBBcQQIIHhhIIBQBH"
DEVICE_INFO_SIZE = struct.calcsize(DEVICE_INFO_FMT)


@dataclasses.dataclass
class DeviceInfo:
    protocol_version:  int = 0
    fw_major:          int = 0
    fw_minor:          int = 0
    fw_patch:          int = 0
    hw_version:        int = 0
    hw_revision:       str = ""
    min_freq:          int = 0
    max_freq:          int = 0
    min_ifbw:          int = 0
    max_ifbw:          int = 0
    max_points:        int = 0
    min_cdbm:          int = 0
    max_cdbm:          int = 0
    min_rbw:           int = 0
    max_rbw:           int = 0
    max_amp_points:    int = 0
    max_freq_harmonic: int = 0
    num_ports:         int = 2
    max_dwell:         int = 0

    @classmethod
    def decode(cls, payload: bytes) -> "DeviceInfo":
        if len(payload) < DEVICE_INFO_SIZE:
            raise ValueError(f"DeviceInfo payload too short: {len(payload)} < {DEVICE_INFO_SIZE}")
        fields = struct.unpack_from(DEVICE_INFO_FMT, payload)
        return cls(
            protocol_version  = fields[0],
            fw_major          = fields[1],
            fw_minor          = fields[2],
            fw_patch          = fields[3],
            hw_version        = fields[4],
            hw_revision       = fields[5].decode(errors="replace"),
            min_freq          = fields[6],
            max_freq          = fields[7],
            min_ifbw          = fields[8],
            max_ifbw          = fields[9],
            max_points        = fields[10],
            min_cdbm          = fields[11],
            max_cdbm          = fields[12],
            min_rbw           = fields[13],
            max_rbw           = fields[14],
            max_amp_points    = fields[15],
            max_freq_harmonic = fields[16],
            num_ports         = fields[17],
            max_dwell         = fields[18],
        )

    def __str__(self):
        return (f"LibreVNA FW {self.fw_major}.{self.fw_minor}.{self.fw_patch} "
                f"HW v{self.hw_version}{self.hw_revision} "
                f"ports={self.num_ports} "
                f"freq=[{self.min_freq/1e6:.0f}..{self.max_freq/1e6:.0f}] MHz")


# ---------------------------------------------------------------------------
# VNADatapoint decoder
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class VNADatapoint:
    frequency:   int
    point_num:   int
    cdbm:        int                  # power in 1/100 dBm
    num_values:  int
    real_values: list[float]
    imag_values: list[float]
    desc_values: list[int]            # stage[7:5] | port_mask[4:0]

    @classmethod
    def decode(cls, payload: bytes) -> "VNADatapoint":
        offset = 0
        frequency  = struct.unpack_from("<Q", payload, offset)[0]; offset += 8
        cdbm       = struct.unpack_from("<h", payload, offset)[0]; offset += 2
        point_num  = struct.unpack_from("<H", payload, offset)[0]; offset += 2
        remaining  = len(payload) - offset
        # each value = 4 (real) + 4 (imag) + 1 (desc) = 9 bytes
        num_values = remaining // 9
        reals = list(struct.unpack_from(f"<{num_values}f", payload, offset))
        offset += num_values * 4
        imags = list(struct.unpack_from(f"<{num_values}f", payload, offset))
        offset += num_values * 4
        descs = list(struct.unpack_from(f"<{num_values}B", payload, offset))
        return cls(frequency, point_num, cdbm, num_values, reals, imags, descs)

    def get_value(self, stage: int, port_idx: int, is_ref: bool):
        """
        Recupera il valore complesso per (stage, porta, ref).
        port_idx: 0-based (port1=0, port2=1, ...)
        is_ref:   True per il canale di riferimento

        Logica identica al firmware (Protocol.hpp::getValue):
          sourceMask = (0x01 << port); se ref: sourceMask |= 0x10
          match se stage coincide E (descr & sourceMask) == sourceMask

        NB: il descrittore del riferimento include TUTTI i bit porta
        (es. 0x13 = port1|port2|ref). Cercando un valore *measured* con
        mask 0x01, la condizione (0x13 & 0x01)==0x01 matcherebbe ANCHE
        il ref. Il firmware si salva perché il measured precede il ref
        nell'ordine di iterazione. Per robustezza, quando cerchiamo un
        measured (is_ref=False) escludiamo esplicitamente i descrittori
        che hanno il bit reference alzato.

        Ritorna complex o None se non trovato.
        """
        source_mask = (1 << port_idx)
        if is_ref:
            source_mask |= 0x10   # Source.Reference

        for i in range(self.num_values):
            desc  = self.desc_values[i]
            s     = desc >> DPNT_STAGE_OFFSET
            if s != stage:
                continue
            # escludi il ref quando cerchiamo un measured (evita collisione bit)
            if not is_ref and (desc & 0x10):
                continue
            if (desc & source_mask) == source_mask:
                return complex(self.real_values[i], self.imag_values[i])
        return None


# ---------------------------------------------------------------------------
# Packet encode / decode
# ---------------------------------------------------------------------------
def encode_packet(ptype: PacketType, payload: bytes = b"") -> bytes:
    """Costruisce un pacchetto binario completo con header e CRC."""
    is_vna_dp = (ptype == PacketType.VNA_DATAPOINT)
    total_len = EXCL_PAYLOAD + len(payload)
    buf = bytearray(total_len)
    buf[0] = HEADER_BYTE
    struct.pack_into("<H", buf, 1, total_len)
    buf[3] = int(ptype)
    if payload:
        buf[4:4 + len(payload)] = payload
    crc = 0 if is_vna_dp else crc32(buf[:-CRC_LEN])
    struct.pack_into("<I", buf, total_len - CRC_LEN, crc)
    return bytes(buf)


def encode_no_payload(ptype: PacketType) -> bytes:
    return encode_packet(ptype, b"")


class DecodeResult:
    __slots__ = ("ptype", "payload", "consumed")
    def __init__(self, ptype, payload, consumed):
        self.ptype    = ptype
        self.payload  = payload
        self.consumed = consumed


def decode_buffer(buf: bytearray | memoryview) -> Optional[DecodeResult]:
    """
    Tenta di decodificare il primo pacchetto valido nel buffer.
    Ritorna DecodeResult o None se il buffer è incompleto.
    consumed indica quanti byte consumare dal buffer (anche se il pacchetto
    è invalido, per scartare byte spuri).
    """
    data = buf if isinstance(buf, memoryview) else memoryview(buf)
    n = len(data)

    # salta byte spuri prima dell'header
    skip = 0
    while skip < n and data[skip] != HEADER_BYTE:
        skip += 1
    if skip > 0:
        return DecodeResult(PacketType.NONE, b"", skip)

    if n < COMBINED_HEADER:
        return None   # header incompleto, aspetta

    total_len = struct.unpack_from("<H", data, 1)[0]

    if total_len < EXCL_PAYLOAD or total_len > 4096:
        # lunghezza assurda → scarta l'header e riprova
        return DecodeResult(PacketType.NONE, b"", 1)

    if n < total_len:
        return None   # payload incompleto, aspetta

    ptype_byte = data[3]
    payload    = bytes(data[4: total_len - CRC_LEN])
    recv_crc   = struct.unpack_from("<I", data, total_len - CRC_LEN)[0]

    if ptype_byte == PacketType.VNA_DATAPOINT:
        # VNADatapoint: CRC sempre 0
        if recv_crc != 0:
            return DecodeResult(PacketType.NONE, b"", 1)
    else:
        calc_crc = crc32(bytes(data[:total_len - CRC_LEN]))
        if recv_crc != calc_crc:
            return DecodeResult(PacketType.NONE, b"", 1)

    try:
        ptype = PacketType(ptype_byte)
    except ValueError:
        ptype = PacketType.NONE

    return DecodeResult(ptype, payload, total_len)


def decode_at(data, off: int, n: int) -> Optional[DecodeResult]:
    """
    Come decode_buffer ma lavora su un offset dentro `data` (bytes/bytearray),
    da `off` fino a `n`, SENZA cancellare nulla dal buffer.

    `consumed` è il numero di byte consumati a partire da `off`.
    Ritorna None se il buffer da `off` in poi è incompleto (serve più dati).

    Ottimizzato per il flusso VNADatapoint ad alta frequenza: nessuna
    copia/cancellazione O(n) del buffer, solo indicizzazione.
    """
    # salta byte spuri prima dell'header
    i = off
    while i < n and data[i] != HEADER_BYTE:
        i += 1
    if i > off:
        return DecodeResult(PacketType.NONE, None, i - off)

    if n - i < COMBINED_HEADER:
        return None

    total_len = data[i+1] | (data[i+2] << 8)

    if total_len < EXCL_PAYLOAD or total_len > 4096:
        return DecodeResult(PacketType.NONE, None, 1)

    if n - i < total_len:
        return None

    ptype_byte = data[i+3]
    payload_start = i + 4
    payload_end   = i + total_len - CRC_LEN
    crc_off       = payload_end
    recv_crc = (data[crc_off] | (data[crc_off+1] << 8)
                | (data[crc_off+2] << 16) | (data[crc_off+3] << 24))

    if ptype_byte == PacketType.VNA_DATAPOINT:
        if recv_crc != 0:
            return DecodeResult(PacketType.NONE, None, 1)
    else:
        calc_crc = crc32(bytes(data[i:payload_end]))
        if recv_crc != calc_crc:
            return DecodeResult(PacketType.NONE, None, 1)

    try:
        ptype = PacketType(ptype_byte)
    except ValueError:
        ptype = PacketType.NONE

    payload = bytes(data[payload_start:payload_end])
    return DecodeResult(ptype, payload, (i - off) + total_len)


# ---------------------------------------------------------------------------
# DeviceStatus (type 25) — Protocol.hpp: union per variante hardware.
# Il payload e' lungo quanto la variante piu' grande; si decodifica per hardware_version:
#   0x01 (LibreVNA originale): bit0 extRefAvail, 1 extRefInUse, 2 FPGA ok, 3 source locked, 4 LO1 locked,
#                              5 ADC overload, 6 unlevel | temp_source, temp_LO1, temp_MCU (uint8, gradi C)
#   0xD0 (HAR0): stessi flag + temp_MCU, tensione (mV?) e corrente (mA?) uint16
#   altre varianti: bit0 source locked, 1 LO locked, 2 ADC overload, 3 unlevel | temp_MCU
# ---------------------------------------------------------------------------
@dataclasses.dataclass
class DeviceStatus:
    hw_version:     int = 0
    ext_ref_avail:  Optional[bool] = None
    ext_ref_use:    Optional[bool] = None
    fpga_ok:        Optional[bool] = None
    source_locked:  Optional[bool] = None
    lo_locked:      Optional[bool] = None
    adc_overload:   Optional[bool] = None
    unlevel:        Optional[bool] = None
    temp_source:    Optional[float] = None    # [gradi C]
    temp_lo:        Optional[float] = None
    temp_mcu:       Optional[float] = None
    supply_v:       Optional[float] = None    # [V]  (solo alcune varianti)
    supply_i:       Optional[float] = None    # [A]
    raw:            bytes = b""


def decode_status(payload: bytes, hw_version: int = 1) -> DeviceStatus:
    b = bytes(payload)
    st = DeviceStatus(hw_version=hw_version, raw=b)
    if len(b) < 2:
        return st
    f = b[0]
    bit = lambda n: bool((f >> n) & 1)
    if hw_version == 0x01 and len(b) >= 4:
        st.ext_ref_avail, st.ext_ref_use, st.fpga_ok = bit(0), bit(1), bit(2)
        st.source_locked, st.lo_locked, st.adc_overload, st.unlevel = bit(3), bit(4), bit(5), bit(6)
        st.temp_source, st.temp_lo, st.temp_mcu = float(b[1]), float(b[2]), float(b[3])
    elif hw_version == 0xD0 and len(b) >= 6:
        st.ext_ref_avail, st.ext_ref_use, st.fpga_ok = bit(0), bit(1), bit(2)
        st.source_locked, st.lo_locked, st.adc_overload, st.unlevel = bit(3), bit(4), bit(5), bit(6)
        st.temp_mcu = float(b[1])
        st.supply_v = struct.unpack_from("<H", b, 2)[0] / 1000.0
        st.supply_i = struct.unpack_from("<H", b, 4)[0] / 1000.0
    else:
        st.source_locked, st.lo_locked, st.adc_overload, st.unlevel = bit(0), bit(1), bit(2), bit(3)
        st.temp_mcu = float(b[1])
    return st
