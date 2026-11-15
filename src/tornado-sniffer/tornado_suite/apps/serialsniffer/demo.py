"""Genera una registrazione di esempio (.ssn) con Modbus RTU, NMEA e AT, per provare viste e decoder."""
from __future__ import annotations

import random
import struct
from pathlib import Path
from typing import Union

from .chunk import RX, Chunk
from .decoders import append_crc, with_checksum
from .decoders import mavlink as mav
from .decoders import sbus, ubx
from .decoders.modbus import ascii_frame
from .ssnfile import SsnWriter, make_header

MS = 1_000_000


def make_demo(path: Union[str, Path], seconds: int = 20, seed: int = 1) -> Path:
    rnd = random.Random(seed)
    hdr = make_header(
        [{"index": 0, "name": "BUS-RS485", "url": "demo://modbus", "config": "9600 8N1"},
         {"index": 1, "name": "GPS", "url": "demo://nmea", "config": "9600 8N1"},
         {"index": 2, "name": "MODEM", "url": "demo://at", "config": "115200 8N1"},
         {"index": 3, "name": "GNSS-UBX", "url": "demo://ubx", "config": "115200 8N1"},
         {"index": 4, "name": "DRONE-MAV", "url": "demo://mavlink", "config": "57600 8N1"},
         {"index": 5, "name": "CNC-GRBL", "url": "demo://grbl", "config": "115200 8N1"},
         {"index": 6, "name": "RC-SBUS", "url": "demo://sbus", "config": "100000 8E2"},
         {"index": 7, "name": "BUS-ASCII", "url": "demo://modbus-ascii", "config": "9600 7E1"}],
        t0_wall_ns=0, notes="registrazione di esempio generata da `serialsniffer demo`")
    items: list[Chunk] = []
    # Modbus RTU: master legge 2 registri (temperatura float) ogni 500 ms; ogni tanto errore o CRC rotto
    t = 100 * MS
    n = 0
    while t < seconds * 1000 * MS:
        n += 1
        addr = 1 if n % 4 else 2
        items.append(Chunk(t, 0, RX, append_crc(bytes([addr, 3, 0, 0x10, 0, 2]))))
        lat = rnd.randint(4, 15) * MS
        if n % 9 == 0:
            items.append(Chunk(t + lat, 0, RX, append_crc(bytes([addr, 0x83, 2]))))        # eccezione
        else:
            temp = 21.5 + 0.1 * n
            items.append(Chunk(t + lat, 0, RX, append_crc(bytes([addr, 3, 4]) + struct.pack(">f", temp))))
        if n % 7 == 0:
            items.append(Chunk(t + 200 * MS, 0, RX, append_crc(bytes([1, 6, 0, 5, 0, n & 0xFF]))[:-1] + b"\x00"))  # CRC errato
        t += 500 * MS
    # NMEA: GGA + RMC ogni secondo, un checksum errato
    for s in range(seconds):
        hh, mm, ss = 12, 35, s
        lat = f"{4807.038 + s * 0.01:08.3f}"
        gga = with_checksum(f"GPGGA,{hh:02d}{mm:02d}{ss:02d},{lat},N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,")
        rmc = with_checksum(f"GPRMC,{hh:02d}{mm:02d}{ss:02d},A,{lat},N,01131.000,E,022.4,084.4,230394,003.1,W")
        if s == 7:
            gga = gga[:-2] + "00"
        items.append(Chunk(s * 1000 * MS + 50 * MS, 1, RX, (gga + "\r\n").encode()))
        items.append(Chunk(s * 1000 * MS + 80 * MS, 1, RX, (rmc + "\r\n").encode()))
    # AT: modem
    seq = [(b"AT\r", b"AT\r\r\nOK\r\n"), (b"AT+CSQ\r", b"AT+CSQ\r\r\n+CSQ: 21,0\r\n\r\nOK\r\n"),
           (b"AT+CREG?\r", b"AT+CREG?\r\r\n+CREG: 0,1\r\n\r\nOK\r\n"),
           (b'AT+CMGS="+391234567"\r', b'AT+CMGS="+391234567"\r\r\n+CME ERROR: 30\r\n')]
    t = 300 * MS
    for i in range(seconds // 2):
        cmd, resp = seq[i % len(seq)]
        items.append(Chunk(t, 2, RX, cmd))
        items.append(Chunk(t + rnd.randint(8, 60) * MS, 2, RX, resp))
        if i % 3 == 2:
            items.append(Chunk(t + 900 * MS, 2, RX, b"\r\n+CREG: 1,5\r\n"))
        t += 2000 * MS
    # UBX: NAV-PVT a 2 Hz, NAV-DOP a 1 Hz, un ACK-ACK
    for k in range(seconds * 2):
        t = k * 500 * MS + 20 * MS
        pvt = struct.pack("<IHBBBBBBIiBBBBiiiiIIiiiiiIIH", k * 500, 2026, 10, 20, 12, 0, k // 2, 0x37, 25, 0, 3, 0x01, 0, 11 + k % 3,
                          int(11.5167 * 1e7), int(48.1173 * 1e7), 545_000, 500_000, 2300, 3100, 120, -80, 15, 140, 0, 900, 0, 150) + bytes(14)
        items.append(Chunk(t, 3, RX, ubx.build(0x01, 0x07, pvt)))
        if k % 2 == 0:
            items.append(Chunk(t + 2 * MS, 3, RX, ubx.build(0x01, 0x04, struct.pack("<IHHHHHHH", k * 500, 180, 150, 90, 120, 85, 60, 70))))
    items.append(Chunk(4_000 * MS, 3, RX, ubx.build(0x05, 0x01, bytes([0x06, 0x08]))))
    # MAVLink: HEARTBEAT 1 Hz, ATTITUDE 5 Hz (con un pacchetto perso), un STATUSTEXT
    seq = 0
    for k in range(seconds * 5):
        t = k * 200 * MS + 30 * MS
        if k % 5 == 0:
            items.append(Chunk(t, 4, RX, mav.build(0, struct.pack("<IBBBBB", 0, 2, 3, 81, 4, 3), seq=seq)))
            seq += 1
        if k == 12:
            seq += 1  # pacchetto perso
        items.append(Chunk(t + 5 * MS, 4, RX, mav.build(30, struct.pack("<Iffffff", k * 200, 0.05 * (k % 7), -0.02 * (k % 5), 1.5 + k * 0.01, 0, 0, 0), seq=seq)))
        seq += 1
    items.append(Chunk(3_300 * MS, 4, RX, mav.build(253, struct.pack("<B50sHB", 4, b"PreArm: Compass not calibrated", 0, 0), seq=seq)))
    # GRBL: poll di stato "?" e G-code con ok; un comando non supportato
    t = 400 * MS
    for k, line in enumerate(["G21", "G90", "G1 X10 Y10 F300", "G1 X20 Y10", "G99 Z1", "G1 X0 Y0", "M5"]):
        items.append(Chunk(t, 5, RX, b"?"))
        items.append(Chunk(t + 3 * MS, 5, RX, b"<Run|MPos:%.3f,%.3f,0.000|FS:300,0>\r\n" % (k * 2.5, k * 1.5)))
        items.append(Chunk(t + 10 * MS, 5, RX, line.encode() + b"\r\n"))
        resp = b"error:20\r\n" if line.startswith("G99") else b"ok\r\n"
        items.append(Chunk(t + 10 * MS + rnd.randint(3, 40) * MS, 5, RX, resp))
        t += 1500 * MS
    # SBUS: un frame ogni 20 ms con stick in movimento
    import math
    for k in range(seconds * 50):
        chs = [992 + int(600 * math.sin(k / 25 + i)) if i < 4 else 992 for i in range(16)]
        items.append(Chunk(k * 20 * MS + 7 * MS, 6, RX, sbus.build(chs, failsafe=(k == 400))))
    # Modbus ASCII: lettura di 1 registro ogni secondo
    for k in range(seconds):
        t = k * 1000 * MS + 600 * MS
        items.append(Chunk(t, 7, RX, ascii_frame(bytes([3, 3, 0, 1, 0, 1]))))
        items.append(Chunk(t + 20 * MS, 7, RX, ascii_frame(bytes([3, 3, 2, 0, k]))))
    items.sort(key=lambda c: c.ts_ns)
    w = SsnWriter(path, hdr)
    try:
        for c in items:
            w.write(c)
    finally:
        w.close()
    return Path(path)
