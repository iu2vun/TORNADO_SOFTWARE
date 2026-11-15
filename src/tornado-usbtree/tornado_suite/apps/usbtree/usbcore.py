"""Enumerazione dell'albero USB su Windows tramite IOCTL degli hub (ctypes).

Parser e strutture sono indipendenti dalla piattaforma; le chiamate Win32
vengono caricate solo in scan().
"""
from __future__ import annotations

import ctypes
import re
import struct
import uuid
from ctypes import wintypes as wt
from dataclasses import dataclass, field
from typing import Callable, Iterator

# --------------------------------------------------------------------------
# Costanti IOCTL (FILE_DEVICE_USB = 0x22, METHOD_BUFFERED)
# --------------------------------------------------------------------------
IOCTL_ROOT_HUB_NAME = 0x220408  # HCD_GET_ROOT_HUB_NAME = 258 (su handle host controller)
IOCTL_NODE_INFO = 0x220408
IOCTL_DESC_FROM_CONN = 0x220410
IOCTL_CONN_NAME = 0x220414
IOCTL_CONN_DRIVERKEY = 0x220420
IOCTL_CONN_INFO_EX = 0x220448
IOCTL_HUB_INFO_EX = 0x220454
IOCTL_PORT_CONNECTOR = 0x220458
IOCTL_CONN_INFO_EX_V2 = 0x22045C

CONN_BUF = 35 + 32 * 30  # USB_NODE_CONNECTION_INFORMATION_EX + 30 pipe

CLASS_NAMES = {
    0x00: "Definita dall'interfaccia", 0x01: "Audio", 0x02: "CDC Comunicazione",
    0x03: "HID", 0x05: "Physical", 0x06: "Immagini", 0x07: "Stampante",
    0x08: "Mass Storage", 0x09: "Hub", 0x0A: "CDC Data", 0x0B: "Smart Card",
    0x0D: "Content Security", 0x0E: "Video", 0x0F: "Salute personale",
    0x10: "Audio/Video", 0x11: "Billboard", 0x12: "USB-C Bridge",
    0xDC: "Diagnostico", 0xE0: "Wireless Controller", 0xEF: "Miscellanea",
    0xFE: "Specifica applicazione", 0xFF: "Specifica vendor",
}
SPEEDS = {0: "Bassa (1.5 Mbps)", 1: "Piena (12 Mbps)", 2: "Alta (480 Mbps)",
          3: "SuperSpeed (5 Gbps)"}
CONN_STATUS = {
    0: "Nessun dispositivo", 1: "Connesso", 2: "Enumerazione fallita",
    3: "Guasto generale", 4: "Sovracorrente", 5: "Alimentazione insufficiente",
    6: "Banda insufficiente", 7: "Hub annidato troppo in profondita'",
    8: "Dispositivo in hub legacy", 9: "In enumerazione", 10: "Reset in corso",
}
PROBLEMS = {
    1: "dispositivo non configurato correttamente", 10: "avvio del dispositivo non riuscito",
    22: "dispositivo disabilitato", 28: "driver non installato",
    31: "dispositivo non funzionante", 43: "il dispositivo ha segnalato un problema",
    45: "dispositivo non collegato", 52: "firma del driver non verificabile",
}
HUB_TYPES = {1: "Root hub", 2: "Hub USB 2.0", 3: "Hub USB 3.x"}
TRANSFER = ["Control", "Isochronous", "Bulk", "Interrupt"]
ISO_SYNC = ["None", "Asynchronous", "Adaptive", "Synchronous"]
ISO_USAGE = ["Data", "Feedback", "Implicit feedback", "Reserved"]


def fmt_bcd(v: int) -> str:
    return f"{v >> 8:x}.{v & 0xFF:02x}"


def class_name(c: int) -> str:
    return CLASS_NAMES.get(c, "Sconosciuta")


def hexdump(data: bytes) -> str:
    rows = []
    for i in range(0, len(data), 16):
        chunk = data[i:i + 16]
        hx = " ".join(f"{b:02X}" for b in chunk).ljust(47)
        asc = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        rows.append(f"{i:04X}  {hx}  {asc}")
    return "\n".join(rows)


# --------------------------------------------------------------------------
# Modello dati
# --------------------------------------------------------------------------
@dataclass
class Node:
    kind: str                      # host | hub | device | empty
    title: str
    key: str
    level: str = "ok"              # ok | warn | error | empty
    vid: int = 0
    pid: int = 0
    ma: int = 0                    # corrente dichiarata (mA)
    props: list = field(default_factory=list)       # [[campo, valore], ...]
    descriptors: str = ""
    raw: dict = field(default_factory=dict)         # nome -> bytes
    warnings: list = field(default_factory=list)
    children: list = field(default_factory=list)
    search: str = ""

    def add(self, k: str, v) -> None:
        if v is None or v == "":
            return
        self.props.append([k, str(v)])

    def warn(self, msg: str, error: bool = False) -> None:
        self.warnings.append(msg)
        if error:
            self.level = "error"
        elif self.level == "ok":
            self.level = "warn"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind, "title": self.title, "key": self.key,
            "level": self.level, "vid": self.vid, "pid": self.pid, "ma": self.ma,
            "props": self.props, "descriptors": self.descriptors,
            "raw": {k: v.hex() for k, v in self.raw.items()},
            "warnings": self.warnings,
            "children": [c.to_dict() for c in self.children],
        }


def walk(nodes) -> Iterator[Node]:
    for n in nodes:
        yield n
        yield from walk(n.children)


# --------------------------------------------------------------------------
# Parser descrittori
# --------------------------------------------------------------------------
def parse_device(dd: bytes, S: Callable[[int], str]) -> list[str]:
    (_, _, bcd, cls, sub, proto, mps, vid, pid, bcd_dev,
     im, ip, isn, ncfg) = struct.unpack("<BBHBBBBHHHBBBB", dd)
    def s(i):
        t = S(i)
        return f'  "{t}"' if t else ""
    return [
        "Device Descriptor:",
        f"  bcdUSB               {fmt_bcd(bcd)}",
        f"  bDeviceClass         0x{cls:02X}  {class_name(cls)}",
        f"  bDeviceSubClass      0x{sub:02X}",
        f"  bDeviceProtocol      0x{proto:02X}",
        f"  bMaxPacketSize0      {mps}",
        f"  idVendor             0x{vid:04X}",
        f"  idProduct            0x{pid:04X}",
        f"  bcdDevice            {fmt_bcd(bcd_dev)}",
        f"  iManufacturer        {im}{s(im)}",
        f"  iProduct             {ip}{s(ip)}",
        f"  iSerialNumber        {isn}{s(isn)}",
        f"  bNumConfigurations   {ncfg}",
    ]


def parse_config(data: bytes, S: Callable[[int], str],
                 ifsvc: Callable[[int], str], ss: bool):
    """Ritorna (righe, corrente_mA, bConfigurationValue, classi_interfacce)."""
    lines: list[str] = []
    ma = 0
    cfg_val = 0
    classes: list[int] = []
    cur_class = -1
    off = 0

    def s(i):
        t = S(i)
        return f'  "{t}"' if t else ""

    while off + 2 <= len(data):
        ln, typ = data[off], data[off + 1]
        if ln < 2 or off + ln > len(data):
            lines.append(f"  (descrittore malformato @ offset {off})")
            break
        d = data[off:off + ln]
        if typ == 2 and ln >= 9:
            _, _, total, nif, val, istr, attr, pw = struct.unpack_from("<BBHBBBBB", d)
            cfg_val = val
            ma = pw * (8 if ss else 2)
            flags = []
            if attr & 0x40:
                flags.append("self-powered")
            if attr & 0x20:
                flags.append("remote wakeup")
            lines += [
                "Configuration Descriptor:",
                f"  wTotalLength         {total}",
                f"  bNumInterfaces       {nif}",
                f"  bConfigurationValue  {val}",
                f"  iConfiguration       {istr}{s(istr)}",
                f"  bmAttributes         0x{attr:02X}  {', '.join(flags) or 'bus-powered'}",
                f"  bMaxPower            {ma} mA",
            ]
        elif typ == 4 and ln >= 9:
            _, _, num, alt, neps, cls, sub, proto, istr = struct.unpack_from("<BBBBBBBBB", d)
            cur_class = cls
            if alt == 0:
                classes.append(cls)
            drv = ifsvc(num) if alt == 0 else ""
            lines += [
                "",
                f"  Interface {num}, alt {alt}:  classe 0x{cls:02X} ({class_name(cls)}), "
                f"sub 0x{sub:02X}, proto 0x{proto:02X}, {neps} endpoint"
                + (f"  [driver: {drv}]" if drv else ""),
            ]
            if istr:
                lines.append(f"    iInterface         {istr}{s(istr)}")
        elif typ == 5 and ln >= 7:
            _, _, addr, attr, mp, interval = struct.unpack_from("<BBBBHB", d)
            tt = attr & 3
            extra = ""
            if tt == 1:
                extra = f", sync {ISO_SYNC[(attr >> 2) & 3]}, uso {ISO_USAGE[(attr >> 4) & 3]}"
            lines.append(
                f"    Endpoint 0x{addr:02X}  EP{addr & 0xF} {'IN' if addr & 0x80 else 'OUT'}  "
                f"{TRANSFER[tt]}{extra}  maxpkt {mp & 0x7FF}"
                + (f" x{((mp >> 11) & 3) + 1}" if (mp >> 11) & 3 else "")
                + f"  intervallo {interval}"
            )
        elif typ == 0x0B and ln >= 8:
            first, cnt, cls, sub, proto, istr = struct.unpack_from("<BBBBBB", d, 2)
            lines.append(
                f"  Interface Association: interfacce {first}..{first + cnt - 1}, "
                f"classe 0x{cls:02X} ({class_name(cls)}), sub 0x{sub:02X}, proto 0x{proto:02X}"
            )
        elif typ == 0x21 and cur_class == 3 and ln >= 9:
            bcd, country, nd = struct.unpack_from("<HBB", d, 2)
            lines.append(f"    HID {fmt_bcd(bcd)}  paese {country}  {nd} descrittore/i")
            for i in range(nd):
                if 6 + 3 * i + 3 <= ln:
                    t, l2 = struct.unpack_from("<BH", d, 6 + 3 * i)
                    lines.append(f"      tipo 0x{t:02X}  lunghezza {l2}"
                                 + ("  (Report)" if t == 0x22 else ""))
        elif typ == 0x30 and ln >= 6:
            burst, attr, bpi = struct.unpack_from("<BBH", d, 2)
            lines.append(f"      SS Endpoint Companion: maxBurst {burst}, attr 0x{attr:02X}, "
                         f"bytes/intervallo {bpi}")
        elif typ == 0x31 and ln >= 8:
            lines.append(f"      SSP Isoch Companion: {d[2:].hex()}")
        else:
            lines.append(f"    Descrittore 0x{typ:02X} ({ln} byte): {d[2:].hex()}")
        off += ln
    return lines, ma, cfg_val, classes


def parse_bos(data: bytes) -> list[str]:
    if len(data) < 5:
        return []
    total, ncaps = struct.unpack_from("<HB", data, 2)
    lines = ["BOS Descriptor:", f"  wTotalLength  {total}", f"  bNumDeviceCaps {ncaps}"]
    off = 5
    while off + 3 <= len(data):
        ln, typ, cap = data[off], data[off + 1], data[off + 2]
        if ln < 3 or off + ln > len(data):
            break
        d = data[off:off + ln]
        if typ == 0x10:
            if cap == 2 and ln >= 7:
                attr = struct.unpack_from("<I", d, 3)[0]
                lines.append(f"  USB 2.0 Extension: attr 0x{attr:08X}"
                             + ("  LPM supportato" if attr & 2 else ""))
            elif cap == 3 and ln >= 10:
                attr, speeds, func, u1, u2 = struct.unpack_from("<BHBBH", d, 3)
                names = [n for b, n in ((1, "Low"), (2, "Full"), (4, "High"), (8, "5 Gbps"))
                         if speeds & b]
                lines.append(f"  SuperSpeed USB: velocita' {', '.join(names)}; "
                             f"U1 exit {u1} us, U2 exit {u2} us"
                             + ("; LTM" if attr & 2 else ""))
            elif cap == 4 and ln >= 20:
                lines.append(f"  Container ID: {uuid.UUID(bytes_le=bytes(d[4:20]))}")
            elif cap == 0x0A and ln >= 12:
                lines.append(f"  SuperSpeedPlus: {d[3:].hex()}")
            else:
                lines.append(f"  Capability 0x{cap:02X}: {d[3:].hex()}")
        else:
            lines.append(f"  Descrittore 0x{typ:02X}: {d[2:].hex()}")
        off += ln
    return lines


# --------------------------------------------------------------------------
# API Win32
# --------------------------------------------------------------------------
class GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


def make_guid(s: str) -> GUID:
    u = uuid.UUID(s)
    return GUID(u.time_low, u.time_mid, u.time_hi_version, (ctypes.c_ubyte * 8)(*u.bytes[8:]))


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("InterfaceClassGuid", GUID), ("Flags", wt.DWORD),
                ("Reserved", ctypes.c_size_t)]


class SP_DEVINFO_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("ClassGuid", GUID), ("DevInst", wt.DWORD),
                ("Reserved", ctypes.c_size_t)]


DIGCF_PRESENT, DIGCF_ALLCLASSES, DIGCF_DEVICEINTERFACE = 0x2, 0x4, 0x10
SPDRP_DEVICEDESC, SPDRP_HARDWAREID, SPDRP_SERVICE, SPDRP_CLASS = 0, 1, 4, 7
SPDRP_DRIVER, SPDRP_MFG, SPDRP_LOCATION_INFORMATION, SPDRP_LOCATION_PATHS = 9, 0xB, 0xD, 0x23
GUID_HOST_CONTROLLER = "3abf6f2d-71c4-462a-8a92-1e6861e6af27"


class _Api:
    def __init__(self) -> None:
        P, V = ctypes.POINTER, ctypes.c_void_p
        self.invalid = (1 << (8 * ctypes.sizeof(V))) - 1
        k = self.k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        s = self.sa = ctypes.WinDLL("setupapi", use_last_error=True)
        c = self.cm = ctypes.WinDLL("cfgmgr32", use_last_error=True)
        k.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, V, wt.DWORD, wt.DWORD, V]
        k.CreateFileW.restype = V
        k.CloseHandle.argtypes = [V]
        k.DeviceIoControl.argtypes = [V, wt.DWORD, V, wt.DWORD, V, wt.DWORD, P(wt.DWORD), V]
        k.DeviceIoControl.restype = wt.BOOL
        s.SetupDiGetClassDevsW.argtypes = [P(GUID), wt.LPCWSTR, V, wt.DWORD]
        s.SetupDiGetClassDevsW.restype = V
        s.SetupDiEnumDeviceInterfaces.argtypes = [V, V, P(GUID), wt.DWORD, P(SP_DEVICE_INTERFACE_DATA)]
        s.SetupDiGetDeviceInterfaceDetailW.argtypes = [
            V, P(SP_DEVICE_INTERFACE_DATA), V, wt.DWORD, P(wt.DWORD), P(SP_DEVINFO_DATA)]
        s.SetupDiEnumDeviceInfo.argtypes = [V, wt.DWORD, P(SP_DEVINFO_DATA)]
        s.SetupDiGetDeviceRegistryPropertyW.argtypes = [
            V, P(SP_DEVINFO_DATA), wt.DWORD, P(wt.DWORD), V, wt.DWORD, P(wt.DWORD)]
        s.SetupDiGetDeviceInstanceIdW.argtypes = [V, P(SP_DEVINFO_DATA), V, wt.DWORD, P(wt.DWORD)]
        s.SetupDiDestroyDeviceInfoList.argtypes = [V]
        c.CM_Get_DevNode_Status.argtypes = [P(wt.ULONG), P(wt.ULONG), wt.DWORD, wt.ULONG]
        c.CM_Get_Parent.argtypes = [P(wt.DWORD), wt.DWORD, wt.ULONG]

    # -- hub / IOCTL -------------------------------------------------------
    def open(self, path: str):
        h = self.k32.CreateFileW(path, 0x40000000, 2, None, 3, 0, None)
        return None if h in (None, self.invalid) else h

    def close(self, h) -> None:
        self.k32.CloseHandle(h)

    def ioctl(self, h, code: int, inbuf: bytes, outsize: int):
        out = ctypes.create_string_buffer(outsize)
        ret = wt.DWORD(0)
        ok = self.k32.DeviceIoControl(h, code, inbuf or None, len(inbuf or b""),
                                      out, outsize, ctypes.byref(ret), None)
        return out.raw[:ret.value] if ok else None

    # -- SetupAPI ----------------------------------------------------------
    def _prop(self, hdev, di, code):
        buf = ctypes.create_string_buffer(8192)
        rt, need = wt.DWORD(0), wt.DWORD(0)
        if not self.sa.SetupDiGetDeviceRegistryPropertyW(
                hdev, ctypes.byref(di), code, ctypes.byref(rt), buf, 8192, ctypes.byref(need)):
            return None
        raw = buf.raw[:need.value]
        if rt.value == 7:
            return [x for x in raw.decode("utf-16le", "replace").split("\0") if x]
        if rt.value in (1, 2):
            return raw.decode("utf-16le", "replace").split("\0")[0]
        return raw

    def _info(self, hdev, di) -> dict:
        def sprop(code):
            v = self._prop(hdev, di, code)
            return v if isinstance(v, str) else ""
        def lprop(code):
            v = self._prop(hdev, di, code)
            return v if isinstance(v, list) else ([v] if isinstance(v, str) and v else [])
        buf = ctypes.create_unicode_buffer(512)
        self.sa.SetupDiGetDeviceInstanceIdW(hdev, ctypes.byref(di), buf, 512, None)
        st, pr, par = wt.ULONG(0), wt.ULONG(0), wt.DWORD(0)
        problem = 0
        if self.cm.CM_Get_DevNode_Status(ctypes.byref(st), ctypes.byref(pr), di.DevInst, 0) == 0:
            problem = pr.value
        parent = par.value if self.cm.CM_Get_Parent(ctypes.byref(par), di.DevInst, 0) == 0 else 0
        return {
            "desc": sprop(SPDRP_DEVICEDESC), "mfg": sprop(SPDRP_MFG),
            "service": sprop(SPDRP_SERVICE), "cls": sprop(SPDRP_CLASS),
            "driver": sprop(SPDRP_DRIVER), "hwids": lprop(SPDRP_HARDWAREID),
            "locpaths": lprop(SPDRP_LOCATION_PATHS), "loc": sprop(SPDRP_LOCATION_INFORMATION),
            "instance": buf.value, "problem": problem,
            "devinst": di.DevInst, "parent": parent,
        }

    def usb_devices(self) -> list[dict]:
        hdev = self.sa.SetupDiGetClassDevsW(None, "USB", None, DIGCF_PRESENT | DIGCF_ALLCLASSES)
        out: list[dict] = []
        if hdev in (None, self.invalid):
            return out
        try:
            i = 0
            while True:
                di = SP_DEVINFO_DATA()
                di.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
                if not self.sa.SetupDiEnumDeviceInfo(hdev, i, ctypes.byref(di)):
                    break
                i += 1
                out.append(self._info(hdev, di))
        finally:
            self.sa.SetupDiDestroyDeviceInfoList(hdev)
        return out

    def host_controllers(self) -> list[tuple[str, dict]]:
        guid = make_guid(GUID_HOST_CONTROLLER)
        hdev = self.sa.SetupDiGetClassDevsW(ctypes.byref(guid), None, None,
                                            DIGCF_PRESENT | DIGCF_DEVICEINTERFACE)
        out: list[tuple[str, dict]] = []
        if hdev in (None, self.invalid):
            return out
        try:
            i = 0
            while True:
                ifd = SP_DEVICE_INTERFACE_DATA()
                ifd.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
                if not self.sa.SetupDiEnumDeviceInterfaces(hdev, None, ctypes.byref(guid), i,
                                                           ctypes.byref(ifd)):
                    break
                i += 1
                need = wt.DWORD(0)
                self.sa.SetupDiGetDeviceInterfaceDetailW(hdev, ctypes.byref(ifd), None, 0,
                                                         ctypes.byref(need), None)
                buf = ctypes.create_string_buffer(max(need.value, 8))
                struct.pack_into("<I", buf, 0, 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6)
                di = SP_DEVINFO_DATA()
                di.cbSize = ctypes.sizeof(SP_DEVINFO_DATA)
                if not self.sa.SetupDiGetDeviceInterfaceDetailW(
                        hdev, ctypes.byref(ifd), buf, need.value, None, ctypes.byref(di)):
                    continue
                path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                out.append((path, self._info(hdev, di)))
        finally:
            self.sa.SetupDiDestroyDeviceInfoList(hdev)
        return out


# --------------------------------------------------------------------------
# Scanner
# --------------------------------------------------------------------------
def _vidpid(hwids: list[str]):
    for h in hwids:
        m = re.match(r"USB\\VID_([0-9A-F]{4})&PID_([0-9A-F]{4})", h, re.I)
        if m:
            return int(m[1], 16), int(m[2], 16)
    return None


@dataclass
class _HubInfo:
    nports: int
    bus_powered: bool
    htype: int
    chars: int
    ctrl_ma: int
    children: list


class _Scanner:
    def __init__(self, api: _Api, ids) -> None:
        self.api, self.ids = api, ids
        self.devs = api.usb_devices()
        self.bydk = {d["driver"].lower(): d for d in self.devs if d["driver"]}
        self.mi: dict[int, dict[int, dict]] = {}
        self.cands: list[tuple[tuple[int, int], dict]] = []
        for d in self.devs:
            m = re.search(r"&MI_([0-9A-F]{2})", d["instance"], re.I)
            if m:
                self.mi.setdefault(d["parent"], {})[int(m[1], 16)] = d
            else:
                vp = _vidpid(d["hwids"])
                if vp:
                    self.cands.append((vp, d))
        self.used: set[int] = set()

    # -- utilita' ----------------------------------------------------------
    def _name_ioctl(self, h, code, port) -> str:
        out = self.api.ioctl(h, code, struct.pack("<I", port).ljust(1024, b"\0"), 1024)
        if not out or len(out) < 8:
            return ""
        return out[8:].decode("utf-16le", "replace").split("\0")[0]

    def _desc(self, h, port, dtype, index, langid, length):
        req = struct.pack("<IBBHHH", port, 0x80, 6, (dtype << 8) | index, langid, length)
        out = self.api.ioctl(h, IOCTL_DESC_FROM_CONN, req + b"\0" * length, 12 + length)
        return out[12:] if out and len(out) > 12 else None

    def _port_props(self, h, p) -> int:
        out = self.api.ioctl(h, IOCTL_PORT_CONNECTOR, struct.pack("<I", p).ljust(512, b"\0"), 512)
        return struct.unpack_from("<I", out, 8)[0] if out and len(out) >= 12 else 0

    # -- scansione ---------------------------------------------------------
    def host(self, n: int, path: str, info: dict) -> Node:
        node = Node("host", info["desc"] or f"Host controller {n}", f"HC{n}")
        node.add("Nome", info["desc"])
        node.add("Servizio driver", info["service"])
        node.add("Hardware ID", info["hwids"][0] if info["hwids"] else "")
        node.add("Instance ID", info["instance"])
        node.add("Posizione", info["loc"])
        node.add("Percorso interfaccia", path)
        if info["problem"]:
            node.warn(f"Problema driver (codice {info['problem']}): "
                      f"{PROBLEMS.get(info['problem'], 'sconosciuto')}", True)
        h = self.api.open(path)
        hub_path = ""
        if h:
            try:
                out = self.api.ioctl(h, IOCTL_ROOT_HUB_NAME, b"", 1024)
                if out and len(out) > 4:
                    hub_path = out[4:].decode("utf-16le", "replace").split("\0")[0]
            finally:
                self.api.close(h)
        if hub_path:
            node.children.append(self.root_hub(f"\\\\.\\{hub_path}", node.key))
        else:
            node.warn("Root hub non raggiungibile", True)
        self.finish(node)
        return node

    def root_hub(self, path: str, key: str) -> Node:
        node = Node("hub", "Root Hub", key + "/RH")
        hub = self.scan_hub(path, node.key)
        if hub is None:
            node.warn("Impossibile aprire il root hub", True)
            self.finish(node)
            return node
        node.add("Tipo", HUB_TYPES.get(hub.htype, "Root hub"))
        node.add("Porte", hub.nports)
        node.children = hub.children
        self.finish(node)
        return node

    def scan_hub(self, path: str, key: str):
        api = self.api
        h = api.open(path)
        if h is None:
            return None
        try:
            ni = api.ioctl(h, IOCTL_NODE_INFO, struct.pack("<I", 0).ljust(76, b"\0"), 76)
            if not ni or len(ni) < 76:
                return None
            nports = ni[6]
            chars = struct.unpack_from("<H", ni, 7)[0]
            ctrl = ni[10]
            bus = bool(ni[75])
            hx = api.ioctl(h, IOCTL_HUB_INFO_EX, b"", 128)
            htype = struct.unpack_from("<I", hx, 0)[0] if hx and len(hx) >= 4 else 0
            children = [self.port(h, p, key, bus) for p in range(1, nports + 1)]
        finally:
            api.close(h)
        return _HubInfo(nports, bus, htype, chars, ctrl, children)

    def port(self, h, p: int, parent_key: str, parent_bus: bool) -> Node:
        api = self.api
        key = f"{parent_key}/P{p}"
        pc = self._port_props(h, p)
        conn = []
        if pc & 8:
            conn.append("USB Type-C")
        conn.append("esterna" if pc & 1 else "interna / non accessibile")
        if pc & 2:
            conn.append("debug")
        raw = api.ioctl(h, IOCTL_CONN_INFO_EX, struct.pack("<I", p).ljust(CONN_BUF, b"\0"), CONN_BUF)
        if not raw or len(raw) < 35:
            n = Node("empty", f"Porta {p}: (informazioni non disponibili)", key, level="empty")
            n.add("Porta", p)
            self.finish(n)
            return n
        (_, dd, cfg_cur, speed, is_hub, addr, _npipes, status) = struct.unpack_from(
            "<I18sBBBHII", raw, 0)
        if status == 0:
            n = Node("empty", f"Porta {p}: (vuota)", key, level="empty")
            n.add("Porta", p)
            n.add("Connettore", ", ".join(conn))
            self.finish(n)
            return n
        if status != 1:
            n = Node("device", f"Porta {p}: {CONN_STATUS.get(status, status)}", key)
            n.add("Porta", p)
            n.add("Stato connessione", CONN_STATUS.get(status, status))
            n.add("Connettore", ", ".join(conn))
            n.warn(f"Stato porta: {CONN_STATUS.get(status, status)}", True)
            self.finish(n)
            return n

        v2 = api.ioctl(h, IOCTL_CONN_INFO_EX_V2, struct.pack("<IIII", p, 16, 0, 0), 16)
        protocols = flags = 0
        if v2 and len(v2) >= 16:
            _, _, protocols, flags = struct.unpack("<IIII", v2)

        (_, _, bcd, cls, sub, proto, mps, vid, pid, bcd_dev,
         i_mfr, i_prod, i_ser, ncfg) = struct.unpack("<BBHBBBBHHHBBBB", dd)

        # velocita'
        if flags & 4:
            sp = "SuperSpeed+ (>= 10 Gbps)"
        elif flags & 1 or speed >= 3:
            sp = SPEEDS[3]
        else:
            sp = SPEEDS.get(speed, str(speed))
        ss_now = bool(flags & 1) or speed >= 3
        if flags & 8:
            cap = "SuperSpeed+ (>= 10 Gbps)"
        elif flags & 2:
            cap = "SuperSpeed (5 Gbps)"
        elif protocols & 2:
            cap = "Alta (480 Mbps)"
        elif protocols & 1:
            cap = "Piena (12 Mbps)"
        else:
            cap = ""

        # stringhe
        str_cache: dict[int, str] = {}
        lang = [None]

        def S(idx: int) -> str:
            if not idx:
                return ""
            if idx in str_cache:
                return str_cache[idx]
            if lang[0] is None:
                d0 = self._desc(h, p, 3, 0, 0, 255)
                lang[0] = struct.unpack_from("<H", d0, 2)[0] if d0 and len(d0) >= 4 else 0x0409
            d = self._desc(h, p, 3, idx, lang[0], 255)
            t = ""
            if d and len(d) >= 2 and d[1] == 3:
                t = d[2:d[0]].decode("utf-16le", "replace").strip("\0")
            str_cache[idx] = t
            return t

        # driver
        dk = self._name_ioctl(h, IOCTL_CONN_DRIVERKEY, p)
        info = self.bydk.get(dk.lower()) if dk else None
        if info is None:
            same = [d for vp, d in self.cands if vp == (vid, pid) and id(d) not in self.used]
            if len(same) == 1:
                info = same[0]
        if info is not None:
            self.used.add(id(info))
        mi_map = self.mi.get(info["devinst"], {}) if info else {}

        def ifsvc(num: int) -> str:
            d = mi_map.get(num)
            return d["service"] if d else ""

        manuf, prod, serial = S(i_mfr), S(i_prod), S(i_ser)
        vendor_db = self.ids.vendor(vid)
        prod_db = self.ids.product(vid, pid)
        name = prod or prod_db or (info["desc"] if info else "") or "Dispositivo USB"

        n = Node("device", f"Porta {p}: {name}  [{vid:04X}:{pid:04X}]", key, vid=vid, pid=pid)
        n.raw["Device"] = dd
        desc = parse_device(dd, S)

        # configurazioni
        cfg_lines: list[str] = []
        cfg_ma = 0
        iface_classes: list[int] = []
        for ci in range(ncfg):
            hdr = self._desc(h, p, 2, ci, 0, 9)
            if not hdr or len(hdr) < 9:
                continue
            total = struct.unpack_from("<H", hdr, 2)[0]
            full = self._desc(h, p, 2, ci, 0, total)
            if not full:
                continue
            n.raw[f"Config {ci}"] = full
            lines, ma, val, classes = parse_config(full, S, ifsvc, ss_now)
            if not cfg_lines or val == cfg_cur:
                cfg_ma = ma
                iface_classes = classes
            cfg_lines += ([""] if cfg_lines else []) + lines
            if val == cfg_cur:
                cfg_lines.append(f"  (configurazione attiva)")
        bos_lines: list[str] = []
        if bcd >= 0x0201:
            bh = self._desc(h, p, 0x0F, 0, 0, 5)
            if bh and len(bh) >= 5:
                bt = struct.unpack_from("<H", bh, 2)[0]
                bd = self._desc(h, p, 0x0F, 0, 0, bt)
                if bd:
                    n.raw["BOS"] = bd
                    bos_lines = parse_bos(bd)
        n.descriptors = "\n".join(desc + [""] + cfg_lines + ([""] + bos_lines if bos_lines else []))
        n.ma = cfg_ma

        # proprieta'
        n.add("Nome", name)
        n.add("Porta", p)
        n.add("VID:PID", f"{vid:04X}:{pid:04X}")
        n.add("Produttore (descrittore)", manuf)
        n.add("Produttore (usb.ids)", vendor_db)
        n.add("Prodotto (usb.ids)", prod_db)
        n.add("Numero di serie", serial)
        n.add("USB", fmt_bcd(bcd))
        n.add("Classe", f"0x{cls:02X} {class_name(cls)}")
        if iface_classes:
            n.add("Classi interfacce", ", ".join(sorted({class_name(c) for c in iface_classes})))
        n.add("Velocita' negoziata", sp)
        n.add("Velocita' massima supportata", cap)
        n.add("Indirizzo bus", addr)
        n.add("Configurazione attiva", cfg_cur)
        n.add("Corrente dichiarata", f"{cfg_ma} mA" if cfg_ma else "")
        n.add("Connettore", ", ".join(conn))
        n.add("Release dispositivo", fmt_bcd(bcd_dev))
        if info:
            n.add("Servizio driver", info["service"])
            n.add("Classe PnP", info["cls"])
            n.add("Descrizione PnP", info["desc"])
            n.add("Instance ID", info["instance"])
            n.add("Hardware ID", " | ".join(info["hwids"][:3]))
            n.add("Percorso di posizione", info["locpaths"][0] if info["locpaths"] else "")
            if info["problem"]:
                n.warn(f"Problema driver (codice {info['problem']}): "
                       f"{PROBLEMS.get(info['problem'], 'sconosciuto')}", True)
        else:
            n.warn("Nessun nodo PnP associato (driver assente o dispositivo non mappabile)")
        n.add("Driver key", dk)

        # avvisi
        if flags & 2 and not flags & 1:
            n.warn("Dispositivo SuperSpeed collegato a velocita' ridotta "
                   "(cavo, porta o hub non SuperSpeed)")
        if cfg_ma > (900 if ss_now else 500):
            n.warn(f"Corrente dichiarata ({cfg_ma} mA) oltre il limite della porta")
        elif parent_bus and cfg_ma > 100:
            n.warn(f"Dispositivo high-power ({cfg_ma} mA) su hub bus-powered "
                   f"(per specifica solo 100 mA/porta)")

        # sotto-hub
        if is_hub:
            hub_name = self._name_ioctl(h, IOCTL_CONN_NAME, p)
            hub = self.scan_hub(f"\\\\.\\{hub_name}", key) if hub_name else None
            if hub is None:
                n.warn("Hub non apribile: porte non enumerabili")
            else:
                n.add("Tipo hub", HUB_TYPES.get(hub.htype, ""))
                n.add("Porte hub", hub.nports)
                n.add("Alimentazione hub", "bus-powered" if hub.bus_powered else "self-powered")
                n.add("Power switching", ["ganged", "individuale", "nessuno", "nessuno"][hub.chars & 3])
                n.add("Protezione sovracorrente",
                      ["globale", "individuale", "nessuna", "nessuna"][(hub.chars >> 3) & 3])
                n.add("Corrente di controllo hub", f"{hub.ctrl_ma} mA")
                n.children = hub.children
                demand = sum(c.ma for c in hub.children if c.kind == "device")
                n.add("Corrente dichiarata dai dispositivi collegati", f"{demand} mA")
                if hub.bus_powered:
                    budget = (900 if ss_now else 500) - cfg_ma
                    n.add("Budget residuo hub bus-powered", f"{budget} mA")
                    if demand > budget:
                        n.warn(f"Consumo dichiarato dai dispositivi ({demand} mA) superiore "
                               f"al budget dell'hub bus-powered ({budget} mA)")
        self.finish(n)
        return n

    def finish(self, n: Node) -> None:
        parts = [n.title] + [v for _, v in n.props]
        if n.vid or n.pid:
            parts.append(f"{n.vid:04x}:{n.pid:04x}")
        n.search = " ".join(parts).lower()


def scan(ids) -> list[Node]:
    """Scansiona tutti gli host controller USB. Solo Windows."""
    api = _Api()
    sc = _Scanner(api, ids)
    return [sc.host(i, path, info) for i, (path, info) in enumerate(api.host_controllers())]
