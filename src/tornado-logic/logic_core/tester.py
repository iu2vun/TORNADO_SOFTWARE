"""Controllo del tester ESP32 attraverso la sua console seriale (115200 baud). Nessuna dipendenza da Qt.

Il firmware (esp32_tester/src/main.cpp) accetta ``help | status | mode off|mixed|serial|walk | baud <%> | delead <us> |
delag <us> | parity on|off | crc on|off | faults reset`` e risponde con righe di testo; qui si inviano i comandi, si leggono le
righe in un thread e si ricava lo stato (modo, cicli, difetti, canale walk). ``SimTesterTransport`` riproduce la
console del firmware, per provare tutto senza scheda.
"""
from __future__ import annotations

import queue
import re
import threading
import time
from dataclasses import dataclass, field

MODES = ("off", "mixed", "serial", "walk")
BAUD = 115200
SIM_PORT = "Simulato"

# ruolo di ogni uscita (CH1..CH8) in ciascun modo, come in esp32_tester/README.md
ROLES: dict[str, tuple[str, ...]] = {
    "off": ("fermo",) * 8,
    "mixed": ("UART TX", "SPI CLK", "SPI MOSI", "SPI MISO", "SPI CS", "I2C SCL", "I2C SDA", "PWM 1 kHz"),
    "serial": ("RS-232 TX", "RS-232 RX", "RS-485 RO", "RS-485 DE", "RS-485 A", "RS-485 B", "libero (alto)", "PWM 1 kHz"),
    "walk": tuple(f"passo {i} di 8" for i in range(1, 9)),
}
DESCRIPTIONS = {
    "off": "Uscite ferme (tutte basse).",
    "mixed": "Ripete ogni 5 ms: UART 115200 su CH1 (\"Hello\"), SPI 1 MHz su CH2–5, I2C 100 kHz su CH6–7, PWM 1 kHz su CH8.",
    "serial": "Ripete ogni 20 ms: RS-232 su CH1/CH2 (9600), Modbus RTU 19200 8E1 su RS-485 (RO, DE, A, B) su CH3–6, PWM su CH8.",
    "walk": "Un canale alto alla volta, 500 ms ciascuno, da CH1 a CH8: serve a verificare i fili.",
}
CYCLE_S = {"mixed": 5e-3, "serial": 20e-3}      # periodo di ripetizione dei due scenari
FAULT_RANGES = {"baud": (-20.0, 20.0), "delead": (-1000, 5000), "delag": (-1000, 5000)}


class TesterError(Exception):
    __test__ = False


@dataclass
class TesterState:
    __test__ = False
    mode: str | None = None
    cycles: int = 0
    compose_errors: int = 0
    cpu_mhz: int = 0
    baud_err: float = 0.0
    de_lead: int = 0
    de_lag: int = 0
    parity_err: bool = False
    crc_err: bool = False             # CRC errato nelle risposte Modbus dei cicli dispari
    walk_ch: int | None = None        # 0..7, ultimo canale annunciato in modo walk
    rate: float | None = None         # cicli al secondo, misurato tra due "status"
    last_rx: float = 0.0              # istante (monotonic) dell'ultima riga ricevuta
    boots: int = 0                    # quante volte la scheda si è (ri)avviata dopo la connessione


@dataclass
class Line:
    text: str
    kind: str = "other"               # status | faults | mode | walk | help | error | boot | tx | other
    t: float = 0.0
    quiet: bool = False               # risposta a un sondaggio automatico: non va mostrata in console


_STATUS = re.compile(r"^modo=(\w+) cicli=(\d+) errori_compose=(\d+) cpu=(\d+)MHz")
_FAULTS = re.compile(r"^difetti: baud=(-?[\d.]+)% delead=(-?\d+)us delag=(-?\d+)us parity_err=(on|off)(?: crc_err=(on|off))?")
_MODE = re.compile(r"^modo: (\w+)")
_WALK = re.compile(r"^walk: CH([1-8]) alto")
_ERRORS = ("comando sconosciuto", "valore non valido", "modo sconosciuto", "usare:", "config.h:")


def parse_line(state: TesterState, text: str) -> str:
    """Aggiorna lo stato dalla riga ricevuta; restituisce il tipo di riga."""
    if m := _STATUS.match(text):
        state.mode = m[1] if m[1] in MODES else state.mode
        state.cycles, state.compose_errors, state.cpu_mhz = int(m[2]), int(m[3]), int(m[4])
        return "status"
    if m := _FAULTS.match(text):
        state.baud_err, state.de_lead, state.de_lag = float(m[1]), int(m[2]), int(m[3])
        state.parity_err = m[4] == "on"
        state.crc_err = m[5] == "on"        # assente nei firmware vecchi: spento
        return "faults"
    if m := _MODE.match(text):
        if m[1] in MODES:
            state.mode, state.cycles, state.walk_ch, state.rate = m[1], 0, None, None
        return "mode"
    if m := _WALK.match(text):
        state.mode, state.walk_ch = "walk", int(m[1]) - 1
        return "walk"
    if text.startswith("Tester analizzatore logico"):
        state.boots += 1
        return "boot"
    if text.startswith("comandi:") or text.startswith("  "):
        return "help"
    if text.startswith(_ERRORS):
        return "error"
    return "other"


# ================================== trasporti ===================================
def list_ports() -> list[tuple[str, str]]:
    """[(porta, descrizione)] delle porte seriali presenti; vuoto se pyserial non c'è."""
    try:
        from serial.tools import list_ports as lp
    except ImportError:
        return []
    return [(p.device, p.description or "") for p in sorted(lp.comports(), key=lambda p: p.device)]


class SerialTransport:
    """Porta seriale reale (pyserial). DTR/RTS restano bassi: aprire la porta non deve riavviare la scheda."""

    def __init__(self, port: str, baud: int = BAUD) -> None:
        try:
            import serial
        except ImportError as e:
            raise TesterError("manca pyserial (pip install pyserial)") from e
        self._serial = serial
        s = serial.Serial()
        s.port, s.baudrate, s.timeout = port, baud, 0.1
        s.dtr = False
        s.rts = False
        try:
            s.open()
        except (serial.SerialException, OSError) as e:
            raise TesterError(f"impossibile aprire {port}: {e} (è già aperta nel monitor seriale dell'IDE?)") from e
        self._s = s

    def read(self) -> bytes:
        try:
            return self._s.read(self._s.in_waiting or 1)
        except (self._serial.SerialException, OSError) as e:
            raise TesterError(f"porta persa: {e}") from e

    def write(self, data: bytes) -> None:
        try:
            self._s.write(data)
        except (self._serial.SerialException, OSError) as e:
            raise TesterError(f"scrittura non riuscita: {e}") from e

    def close(self) -> None:
        try:
            self._s.close()
        except OSError:
            pass


class SimTesterTransport:
    """ESP32 simulata: stessa console del firmware (stessi testi, stessi limiti), con un thread di lettura vero."""

    def __init__(self, walk_s: float = 0.5, boot_mode: str = "mixed", clock=time.monotonic) -> None:
        self._lock = threading.Lock()
        self._clock = clock
        self._out = bytearray()
        self._in = ""
        self.walk_s = walk_s
        self.mode = "off"
        self.cycle_t0 = 0.0
        self.walk = 0
        self.walk_t = 0.0
        self.baud, self.de_lead, self.de_lag, self.parity, self.crc = 0.0, 100, 60, False, False
        self.closed = False
        self._say("\nTester analizzatore logico")
        self._help()
        self._set_mode(boot_mode)

    # --- uscita del firmware -------------------------------------------------
    def _say(self, text: str) -> None:
        self._out += (text + "\n").encode()

    def _help(self) -> None:
        for line in ("comandi: help | status | mode off|mixed|serial|walk | baud <%> | delead <us> | delag <us> | "
                     "parity on|off | crc on|off | faults reset",
                     "  mixed : CH1 UART 115200, CH2-5 SPI 1 MHz, CH6-7 I2C 100 kHz, CH8 PWM 1 kHz 30 %",
                     "  serial: CH1 RS-232 TX, CH2 RS-232 RX, CH3 RO, CH4 DE, CH5 A, CH6 B, CH8 PWM (Modbus RTU 19200 8E1)",
                     "  walk  : un canale alto alla volta, CH1..CH8 (verifica dei collegamenti)",
                     "  difetti (serial): baud = errore del baud in %, delead = anticipo DE (negativo = in ritardo), "
                     "delag = ritardo DE, parity/crc = errori di parità / di CRC Modbus"):
            self._say(line)

    def _cycles(self) -> int:
        p = CYCLE_S.get(self.mode)
        return 0 if p is None else int((self._clock() - self.cycle_t0) / p)

    def _status(self) -> None:
        self._say(f"modo={self.mode} cicli={self._cycles()} errori_compose=0 cpu=240MHz")
        self._say(f"difetti: baud={self.baud:.2f}% delead={self.de_lead}us delag={self.de_lag}us "
                  f"parity_err={'on' if self.parity else 'off'} crc_err={'on' if self.crc else 'off'}")

    def _set_mode(self, mode: str) -> None:
        self.mode = mode
        self.cycle_t0 = self.walk_t = self._clock()
        self.walk = 0
        self._say(f"modo: {mode}")

    def _num(self, arg: str, lo: float, hi: float) -> float | None:
        try:
            v = float(arg)
        except ValueError:
            v = None
        if v is None or not lo <= v <= hi:
            self._say(f"valore non valido ({lo:.0f}..{hi:.0f})")
            return None
        return v

    def _handle(self, line: str) -> None:
        line = line.strip().lower()
        if not line:
            return
        cmd, _, arg = line.partition(" ")
        arg = arg.strip()
        if cmd in ("help", "?"):
            self._help()
        elif cmd == "status":
            self._status()
        elif cmd == "mode":
            if arg in MODES:
                self._set_mode(arg)
            else:
                self._say("modo sconosciuto: off|mixed|serial|walk")
        elif cmd == "baud":
            if (v := self._num(arg, *FAULT_RANGES["baud"])) is not None:
                self.baud = v
        elif cmd == "delead":
            if (v := self._num(arg, *FAULT_RANGES["delead"])) is not None:
                self.de_lead = int(v)
        elif cmd == "delag":
            if (v := self._num(arg, *FAULT_RANGES["delag"])) is not None:
                self.de_lag = int(v)
        elif cmd == "parity":
            if arg in ("on", "off"):
                self.parity = arg == "on"
            else:
                self._say("usare: parity on|off")
        elif cmd == "crc":
            if arg in ("on", "off"):
                self.crc = arg == "on"
            else:
                self._say("usare: crc on|off")
        elif cmd == "faults" and arg == "reset":
            self.baud, self.de_lead, self.de_lag, self.parity, self.crc = 0.0, 100, 60, False, False
        else:
            self._say("comando sconosciuto (help)")
            return
        if cmd not in ("help", "?", "status", "mode"):
            self._status()

    def _tick(self) -> None:
        if self.mode == "walk":
            now = self._clock()
            while now - self.walk_t >= self.walk_s:
                self.walk_t += self.walk_s
                self.walk = (self.walk + 1) & 7
                self._say(f"walk: CH{self.walk + 1} alto")

    # --- interfaccia di trasporto ---------------------------------------------
    def write(self, data: bytes) -> None:
        with self._lock:
            if self.closed:
                raise TesterError("porta chiusa")
            self._in += data.decode(errors="replace")
            while True:
                i = min((j for j in (self._in.find("\n"), self._in.find("\r")) if j >= 0), default=-1)
                if i < 0:
                    break
                line, self._in = self._in[:i], self._in[i + 1:]
                self._handle(line)

    def read(self) -> bytes:
        with self._lock:
            if self.closed:
                raise TesterError("porta chiusa")
            self._tick()
            out, self._out = bytes(self._out), bytearray()
        if not out:
            time.sleep(0.01)
        return out

    def close(self) -> None:
        with self._lock:
            self.closed = True


# ==================================== collegamento ====================================
class TesterLink:
    """Legge le righe dal tester in un thread; drain() le consegna (nel thread chiamante) aggiornando lo stato."""

    __test__ = False

    def __init__(self, transport, name: str = "") -> None:
        self.transport = transport
        self.name = name
        self.state = TesterState()
        self.error: str | None = None
        self._q: queue.Queue[Line] = queue.Queue()
        self._stop = threading.Event()
        self._quiet_until = 0.0
        self._prev: tuple[int, float] | None = None
        self._thr = threading.Thread(target=self._read_loop, name="tester-rx", daemon=True)
        self.state.last_rx = time.monotonic()
        self._thr.start()

    @classmethod
    def open(cls, port: str, **sim_kw) -> "TesterLink":
        if port == SIM_PORT:
            return cls(SimTesterTransport(**sim_kw), port)
        return cls(SerialTransport(port), port)

    def _read_loop(self) -> None:
        buf = ""
        while not self._stop.is_set():
            try:
                data = self.transport.read()
            except TesterError as e:
                if not self._stop.is_set():
                    self.error = str(e)
                    self._q.put(Line(str(e), "error", time.monotonic()))
                return
            if not data:
                continue
            buf += data.decode(errors="replace")
            while "\n" in buf:
                raw, buf = buf.split("\n", 1)
                text = raw.rstrip("\r")
                if text.strip():
                    self._q.put(Line(text, t=time.monotonic()))
            if len(buf) > 4096:                  # niente a capo: scarta, non accumulare
                buf = buf[-256:]

    @property
    def alive(self) -> bool:
        return self.error is None and self._thr.is_alive()

    def send(self, cmd: str, quiet: bool = False) -> None:
        cmd = cmd.strip()
        if not cmd:
            return
        if not quiet:
            self._quiet_until = 0.0
        else:
            self._quiet_until = time.monotonic() + 0.6
        self.transport.write((cmd + "\n").encode())
        if not quiet:
            self._q.put(Line(cmd, "tx", time.monotonic()))

    def poll(self) -> None:
        """Chiede lo stato senza riempire la console."""
        self.send("status", quiet=True)

    def drain(self) -> list[Line]:
        out: list[Line] = []
        while True:
            try:
                ln = self._q.get_nowait()
            except queue.Empty:
                return out
            if ln.kind != "tx":
                if ln.kind != "error":
                    ln.kind = parse_line(self.state, ln.text)
                    self.state.last_rx = ln.t
                    if ln.kind == "status":
                        self._update_rate(ln.t)
                    if ln.kind in ("status", "faults") and ln.t <= self._quiet_until:
                        ln.quiet = True
            out.append(ln)

    def _update_rate(self, t: float) -> None:
        st = self.state
        if self._prev is not None:
            c0, t0 = self._prev
            if t - t0 >= 0.3 and st.cycles >= c0:
                st.rate = (st.cycles - c0) / (t - t0)
        self._prev = (st.cycles, t)

    def close(self) -> None:
        self._stop.set()
        try:
            self.transport.close()
        except Exception:  # noqa: BLE001 - la chiusura non deve mai sollevare
            pass
        self._thr.join(timeout=1.0)
