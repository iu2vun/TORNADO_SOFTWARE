"""
LibreVNA USB driver — Python puro con pyusb.

Rimpiazza completamente LibreVNA-GUI: scoperta device, handshake,
invio SweepSettings, ricezione stream VNADatapoint, ricostruzione
S11/S21/S12/S22 dai valori raw dei ricevitori.

Nessuna error correction (cal off) — corrisponde alla Fase 1.

Dipendenze:
    pip install pyusb
    Windows: installa Zadig → WinUSB driver per LibreVNA
    Linux:   regola udev (file .rules nel repo LibreVNA)

VID/PID (da librevnausbdriver.cpp):
    0x0483:0x564E  (STM, VNA product name)
    0x0483:0x4121
    0x1209:0x4121

Endpoint bulk:
    OUT  0x01  → comandi host→device
    IN   0x81  → dati device→host (VNADatapoint, DeviceInfo, Ack, ...)
    IN   0x82  → log stringhe (ignorato)
"""

import threading
import queue
import time
import logging
import os
import sys
from typing import Optional, Callable

log = logging.getLogger(__name__)

# usb importato lazy (dentro le funzioni) per evitare errori a livello modulo
# quando pyusb è installato ma il path non è ancora risolto

from .protocol import (
    PacketType, SweepSettings, DeviceInfo, VNADatapoint,
    encode_packet, encode_no_payload, decode_buffer,
)

# USB identifiers
VALID_IDS = [
    (0x0483, 0x564E, "VNA"),
    (0x0483, 0x4121, "VNA"),
    (0x1209, 0x4121, "VNA"),
]
EP_OUT     = 0x01
EP_IN_DATA = 0x81
EP_IN_LOG  = 0x82
READ_SIZE  = 65536
TIMEOUT_MS = 1000


class LibreVNAUSBError(Exception):
    pass


def _get_backend():
    """Carica libusb-1.0.dll: cartella exe/script, _internal (PyInstaller), libusb_package, PATH."""
    import usb.backend.libusb1
    dirs = [os.path.dirname(os.path.abspath(sys.argv[0])),
            os.path.dirname(os.path.abspath(__file__)),
            getattr(sys, "_MEIPASS", "")]
    dirs += [os.path.join(d, "_internal") for d in dirs[:1]]
    for d in dirs:
        dll = os.path.join(d, "libusb-1.0.dll") if d else ""
        if dll and os.path.exists(dll):
            be = usb.backend.libusb1.get_backend(find_library=lambda _, p=dll: p)
            if be:
                return be
    try:
        import libusb_package
        be = libusb_package.get_libusb1_backend()
        if be:
            return be
    except Exception:
        pass
    return usb.backend.libusb1.get_backend()


_BACKEND = None   # inizializzato al primo uso


class LibreVNAUSBDriver:
    """
    Driver USB diretto per LibreVNA senza dipendenza da LibreVNA-GUI.

    Uso:
        drv = LibreVNAUSBDriver()
        drv.connect()                     # o connect(serial="206F...")
        info = drv.device_info            # DeviceInfo dopo handshake
        drv.set_sweep(SweepSettings(...))
        for dp in drv.datapoints():       # generatore infinito
            s11 = dp["S11"]               # complex
            s21 = dp["S21"]
        drv.disconnect()
    """

    def __init__(self):
        self._dev = None          # usb.core.Device | None
        self._serial: str = ""
        self._rx_buf     = bytearray()
        self._rx_lock    = threading.Lock()
        self._pkt_queue: queue.Queue = queue.Queue(maxsize=512)
        self._rx_thread: threading.Thread | None = None
        self._running    = False
        self.device_info: DeviceInfo | None = None

        # sweep pubblicato dal thread RX per la UI (alto throughput)
        self._sweep_lock   = threading.Lock()
        self._latest_sweep = None      # dict {point_num: {Sij: complex}}
        self._sweep_serial = 0
        self._cur_sweep = {}         # sweep in corso (pubblicato quando completo)

        # stato dello strumento (DeviceStatus), aggiornato dal thread RX
        self._status = None
        self._status_serial = 0
        self._status_ts = 0.0
        self._ack_skip = 0           # ACK di comandi 'fire and forget' da non mettere in coda

        # port→stage mapping, impostato da set_sweep
        # {port_number(1-based): stage_index(0-based)}
        self._port_stage: dict[int, int] = {1: 0, 2: 1}
        self._num_ports: int = 2
        self._sweeping = False   # True quando il device sta sweepando

        # Le scritture USB vengono eseguite in un thread demone dedicato con
        # un timeout reale lato Python (vedi _send_raw): alcuni backend
        # pyusb/libusb su Windows (WinUSB) non rispettano sempre il
        # parametro timeout= di dev.write(), bloccando il chiamante a
        # tempo indefinito. Usiamo un thread 'daemon=True' esplicito (non
        # un ThreadPoolExecutor, i cui worker non sono demoni per default)
        # così un'eventuale scrittura bloccata non impedisce la chiusura
        # pulita dell'applicazione.

    # ── scoperta ────────────────────────────────────────────────────────────
    @staticmethod
    def list_devices() -> list[str]:
        """Ritorna i numeri seriali di tutti i LibreVNA connessi."""
        import usb.core, usb.util
        global _BACKEND
        if _BACKEND is None:
            _BACKEND = _get_backend()
        if _BACKEND is None:
            raise LibreVNAUSBError(
                "libusb non trovata.\n"
                "Scarica libusb-1.0.dll da https://github.com/libusb/libusb/releases\n"
                "e copiala nella cartella VNA_Suite\\ accanto a main.py."
            )
        serials = []
        for vid, pid, product in VALID_IDS:
            devs = usb.core.find(idVendor=vid, idProduct=pid,
                                 find_all=True, backend=_BACKEND)
            if devs is None:
                continue
            for dev in devs:
                try:
                    prod_str = usb.util.get_string(dev, dev.iProduct)
                    if prod_str != product:
                        continue
                    sn = usb.util.get_string(dev, dev.iSerialNumber)
                    if sn:
                        serials.append(sn)
                except Exception:
                    pass
        return serials

    # ── connessione ──────────────────────────────────────────────────────────
    def connect(self, serial: str = "") -> "LibreVNAUSBDriver":
        import usb.core, usb.util
        dev = self._find_device(serial)
        if dev is None:
            raise LibreVNAUSBError(
                f"LibreVNA non trovato (serial={serial!r}).\n"
                "Windows: installa WinUSB via Zadig.\n"
                "Linux: controlla le regole udev."
            )

        try:
            dev.set_configuration()
        except Exception:
            pass

        try:
            import usb.util
            usb.util.claim_interface(dev, 0)
        except Exception as e:
            raise LibreVNAUSBError(
                f"Impossibile aprire l'interfaccia USB: {e}\n"
                "Il device è già in uso? Chiudi LibreVNA-GUI se è aperto."
            )

        self._dev     = dev
        self._running = True
        self._rx_buf  = bytearray()

        # thread di ricezione
        self._rx_thread = threading.Thread(
            target=self._rx_loop, daemon=True, name="librevna-rx")
        self._rx_thread.start()

        # handshake: richiedi DeviceInfo — timeout generoso, il firmware può essere lento
        self._send_no_payload(PacketType.REQUEST_DEVICE_INFO)
        self.device_info = self._wait_for_device_info(timeout=8.0)
        if self.device_info is None:
            # secondo tentativo
            self._send_no_payload(PacketType.REQUEST_DEVICE_INFO)
            self.device_info = self._wait_for_device_info(timeout=5.0)
        if self.device_info is None:
            self.disconnect()
            raise LibreVNAUSBError(
                "Timeout handshake: DeviceInfo non ricevuta.\n"
                "Verifica che il firmware LibreVNA sia aggiornato (>= 1.2.0)."
            )

        self._num_ports = self.device_info.num_ports
        self._serial    = serial or ""
        log.info("Connesso: %s", self.device_info)

        # mette il device in idle
        self._send_no_payload(PacketType.SET_IDLE)
        return self

    def disconnect(self):
        self._running = False
        if self._rx_thread:
            self._rx_thread.join(timeout=2.0)
            self._rx_thread = None
        if self._dev:
            try:
                self._send_no_payload(PacketType.SET_IDLE)
            except Exception:
                pass
            try:
                import usb.util
                usb.util.release_interface(self._dev, 0)
                usb.util.dispose_resources(self._dev)
            except Exception:
                pass
            self._dev = None
        log.info("Disconnesso")

    @property
    def connected(self) -> bool:
        return self._dev is not None and self._running

    @property
    def serial(self) -> str:
        return self._serial

    # ── sweep ────────────────────────────────────────────────────────────────
    def set_sweep(self, s: SweepSettings):
        """Configura lo sweep. Manda INITIATE_SWEEP solo se il device non sta
        già sweepando (altrimenti il firmware risponde NACK)."""
        if s.stages == 0:
            self._port_stage = {1: 0}
        else:
            self._port_stage = {1: s.port1_stage, 2: s.port2_stage}

        payload = s.encode()
        pkt     = encode_packet(PacketType.SWEEP_SETTINGS, payload)
        self._send_raw(pkt)
        self._wait_ack(timeout=1.0)

        # avvia l'acquisizione solo se non è già in corso
        if not self._sweeping:
            self._send_no_payload(PacketType.INITIATE_SWEEP)
            self._sweeping = True

    def initiate_sweep(self):
        """Forza un trigger di sweep (usato per riprendere da idle)."""
        self._send_no_payload(PacketType.INITIATE_SWEEP)
        self._sweeping = True

    def set_idle(self):
        self._sweeping = False
        self._send_no_payload(PacketType.SET_IDLE)

    def datapoints(self, timeout: float = 5.0):
        """Generatore: yield dict con S11/S21/S12/S22 complex per ogni punto."""
        while self._running:
            try:
                ptype, payload = self._pkt_queue.get(timeout=timeout)
            except queue.Empty:
                log.warning("Timeout ricezione datapoint")
                continue

            if ptype == PacketType.VNA_DATAPOINT:
                dp = VNADatapoint.decode(payload)
                sparam = self._extract_sparams(dp)
                if sparam:
                    yield sparam

    def read_sweep(self, n_points: int, timeout: float = 30.0) -> list[dict]:
        """
        Legge UNO sweep completo e pulito.

        Il device sweeppa in continuo: per non prendere la coda di uno sweep
        vecchio mischiata all'inizio del nuovo, aspettiamo l'inizio di uno
        sweep pulito (point_num che riparte da 0 / torna indietro) e poi
        raccogliamo tutti i punti indicizzati per point_num. Scartiamo anche
        il primo sweep dopo un cambio config (può essere transitorio) grazie
        a questo allineamento.
        """
        return self._collect_one_sweep(n_points, timeout,
                                       skip_first=True, on_progress=None)

    def read_sweep_progress(self, n_points: int, timeout: float = 30.0,
                            stall_timeout: float = 4.0,
                            on_progress=None, on_idle=None) -> list[dict]:
        """Come read_sweep ma con progress e rilevamento stallo.
        on_idle(): chiamata ogni ~10 ms mentre si attende (aggiorna il display)."""
        return self._collect_one_sweep(n_points, timeout,
                                       skip_first=True,
                                       stall_timeout=stall_timeout,
                                       on_progress=on_progress,
                                       on_idle=on_idle)

    def _collect_one_sweep(self, n_points: int, timeout: float,
                           skip_first: bool = True,
                           stall_timeout: float = 4.0,
                           on_progress=None, on_idle=None) -> list[dict]:
        """
        Attende uno sweep completo fresco tramite il meccanismo di
        pubblicazione del thread RX (get_latest_sweep).

        skip_first: scarta il primo sweep pubblicato (può essere transitorio
        dopo un cambio di configurazione), aspetta il successivo.
        """
        start_serial, _ = self.get_latest_sweep()
        deadline    = time.monotonic() + timeout
        last_seen   = start_serial
        seen_count  = 0

        while True:
            now = time.monotonic()
            if now > deadline:
                raise RuntimeError(
                    f"Timeout raccolta sweep ({seen_count} sweep visti). "
                    f"RX vivo: {self._rx_thread.is_alive() if self._rx_thread else False}."
                )
            serial, sweep = self.get_latest_sweep()
            if serial == last_seen or sweep is None:
                if on_idle:
                    on_idle()
                time.sleep(0.01)
                continue
            last_seen = serial
            seen_count += 1

            # scarta il primo sweep fresco se richiesto
            if skip_first and seen_count < 2:
                if on_progress:
                    on_progress(0, n_points)
                continue

            pts = sorted(sweep.keys())
            data = [sweep[k] for k in pts]
            if on_progress:
                on_progress(len(data), n_points)
            if len(data) >= n_points * 0.9:   # sweep sostanzialmente completo
                return data

        return []


    # ── ricostruzione S-param dal VNADatapoint ───────────────────────────────
    def _extract_sparams(self, dp: VNADatapoint) -> Optional[dict]:
        """
        S_ij = input_j / ref_i   (misurato/riferimento)
        dove i = porta eccitata (stimulus), j = porta ricevente.

        Ricostruzione identica a librevnadriver.cpp righe 777-790:
            ref   = res->getValue(stage, excitedPort-1, isRef=True)
            input = res->getValue(stage, receivingPort-1, isRef=False)
            S_receving_excited = input / ref
        """
        out: dict = {
            "frequency": dp.frequency,
            "point_num": dp.point_num,
            "cdbm":      dp.cdbm,
        }
        for excited_port, stage in self._port_stage.items():
            ref = dp.get_value(stage, excited_port - 1, is_ref=True)
            if ref is None or abs(ref) < 1e-15:
                continue
            for recv_port in range(1, self._num_ports + 1):
                inp = dp.get_value(stage, recv_port - 1, is_ref=False)
                if inp is not None:
                    name = f"S{recv_port}{excited_port}"
                    out[name] = inp / ref
        return out if len(out) > 3 else None   # 3 = frequency + point_num + cdbm

    # ── TX / RX ──────────────────────────────────────────────────────────────
    def _send_raw(self, data: bytes):
        if not self._dev:
            raise LibreVNAUSBError("Non connesso")

        result: dict = {}

        def _do_write():
            try:
                self._dev.write(EP_OUT, data, timeout=TIMEOUT_MS)
                result["ok"] = True
            except Exception as e:
                result["error"] = e

        t = threading.Thread(target=_do_write, daemon=True, name="librevna-tx")
        t.start()
        t.join(timeout=2.0)   # watchdog: bound reale indipendente da pyusb

        if t.is_alive():
            # la scrittura è ancora bloccata dopo 2s: il thread demone verrà
            # abbandonato (non impedisce la chiusura dell'app) ma segnaliamo
            # subito l'errore invece di restare appesi per sempre
            raise LibreVNAUSBError(
                "Scrittura USB bloccata oltre 2s — il device non risponde. "
                "Scollega e ricollega il LibreVNA via USB e riconnetti."
            )
        if "error" in result:
            raise LibreVNAUSBError(f"Errore scrittura USB: {result['error']}")

    def _send_no_payload(self, ptype: PacketType):
        self._send_raw(encode_no_payload(ptype))

    def _rx_loop(self):
        """
        Thread di ricezione ad alto throughput.

        Legge blocchi grandi dall'endpoint bulk, decodifica i pacchetti con
        un parser a offset (nessuna cancellazione O(n) del buffer), e per i
        VNADatapoint li accumula direttamente in uno sweep buffer condiviso
        invece di passare ogni pacchetto grezzo attraverso una queue Qt.

        La UI legge lo sweep completo più recente via get_latest_sweep(),
        con un solo lock per sweep anziché uno per datapoint.
        """
        import usb.core
        from .protocol import decode_at, decode_status, PacketType, VNADatapoint

        rx = bytearray()
        cur_sweep: dict[int, dict] = {}
        with self._sweep_lock:
            self._cur_sweep = cur_sweep
        last_pnum = -1

        while self._running and self._dev:
            try:
                chunk = self._dev.read(EP_IN_DATA, READ_SIZE, timeout=TIMEOUT_MS)
            except usb.core.USBTimeoutError:
                continue
            except usb.core.USBError as e:
                if self._running:
                    log.error("Errore USB RX: %s", e)
                break

            rx += bytes(chunk)
            n = len(rx)
            off = 0

            while True:
                res = decode_at(rx, off, n)
                if res is None:
                    break
                off += res.consumed
                if res.ptype == PacketType.VNA_DATAPOINT and res.payload is not None:
                    dp = VNADatapoint.decode(res.payload)
                    # nuovo sweep: point_num torna indietro
                    if dp.point_num < last_pnum and cur_sweep:
                        cur_sweep = self._publish_sweep(cur_sweep)
                    last_pnum = dp.point_num
                    sp = self._extract_sparams(dp)
                    if sp is not None:
                        cur_sweep[dp.point_num] = sp
                elif res.ptype == PacketType.DEVICE_STATUS and res.payload is not None:
                    try:
                        hw = self.device_info.hw_version if self.device_info else 1
                        st = decode_status(res.payload, hw)
                        with self._sweep_lock:
                            self._status = st
                            self._status_ts = time.time()
                            self._status_serial += 1
                    except Exception:
                        log.exception("DeviceStatus non decodificabile")
                elif res.ptype in (PacketType.ACK, PacketType.NACK) and self._ack_skip > 0:
                    self._ack_skip -= 1
                elif res.ptype not in (PacketType.NONE, PacketType.VNA_DATAPOINT):
                    # pacchetti di controllo (Ack/DeviceInfo/Status...) → queue
                    try:
                        self._pkt_queue.put_nowait((res.ptype, res.payload))
                    except queue.Full:
                        try: self._pkt_queue.get_nowait()
                        except Exception: pass
                        try: self._pkt_queue.put_nowait((res.ptype, res.payload))
                        except Exception: pass
                if res.consumed == 0:
                    break

            # rimuovi in un colpo solo i byte consumati (O(n) una volta per
            # blocco USB, non una volta per pacchetto)
            if off > 0:
                del rx[:off]

    def _publish_sweep(self, sweep: dict) -> dict:
        """Pubblica uno sweep completo per la UI (thread-safe) e ritorna il
        dict vuoto del nuovo sweep in corso (aggiornato atomicamente)."""
        new: dict = {}
        with self._sweep_lock:
            self._latest_sweep = sweep
            self._sweep_serial += 1
            self._cur_sweep = new
        return new

    def get_progress(self):
        """
        Snapshot coerente per la UI: (serial, latest_full, cur_id, cur_items).
        cur_items = [(point_num, dict)] dello sweep in corso, in ordine di arrivo.
        """
        with self._sweep_lock:
            cur = self._cur_sweep
            return (self._sweep_serial, self._latest_sweep,
                    id(cur), list(cur.items()))

    def get_status(self):
        """(serial, DeviceStatus|None, timestamp) dell'ultimo DeviceStatus ricevuto."""
        with self._sweep_lock:
            return self._status_serial, self._status, self._status_ts

    def start_status_updates(self):
        """Il device invia DeviceStatus a fine di ogni sweep (e ogni 1 s in idle).
        Costa una lettura delle temperature PLL per sweep: usare a tratti se serve velocita'."""
        self._ack_skip += 1
        self._send_no_payload(PacketType.START_STATUS_UPDATES)

    def stop_status_updates(self):
        self._ack_skip += 1
        self._send_no_payload(PacketType.STOP_STATUS_UPDATES)

    def request_status(self):
        """Richiesta singola (valori aggiornati solo se il device e' in idle)."""
        self._send_no_payload(PacketType.REQUEST_DEVICE_STATUS)

    def get_latest_sweep(self):
        """
        Ritorna (serial, sweep_dict) dello sweep più recente, o (serial, None).
        serial incrementa ad ogni nuovo sweep: la UI lo usa per non
        rielaborare lo stesso sweep.
        """
        with self._sweep_lock:
            return self._sweep_serial, self._latest_sweep

    def _process_rx_buf(self):
        # mantenuto per compatibilità (non più usato dal loop principale)
        pass

    def _wait_for_device_info(self, timeout: float) -> Optional[DeviceInfo]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ptype, payload = self._pkt_queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if ptype == PacketType.DEVICE_INFO:
                return DeviceInfo.decode(payload)
        return None

    def _wait_ack(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                ptype, _ = self._pkt_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if ptype == PacketType.ACK:
                return True
            if ptype == PacketType.NACK:
                log.warning("NACK ricevuto")
                return False
        return False

    # ── context manager ──────────────────────────────────────────────────────
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.disconnect()

    # ── device search ────────────────────────────────────────────────────────
    @staticmethod
    def _find_device(serial: str) -> Optional[object]:
        import usb.core, usb.util
        global _BACKEND
        for vid, pid, product in VALID_IDS:
            devs = usb.core.find(idVendor=vid, idProduct=pid,
                                 find_all=True, backend=_BACKEND)
            if not devs:
                continue
            for dev in devs:
                try:
                    prod_str = usb.util.get_string(dev, dev.iProduct)
                    if prod_str != product:
                        continue
                    if serial:
                        sn = usb.util.get_string(dev, dev.iSerialNumber)
                        if sn != serial:
                            continue
                    return dev
                except Exception:
                    continue
        return None
