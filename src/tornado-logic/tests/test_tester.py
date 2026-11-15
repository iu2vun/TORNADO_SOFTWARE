import time

import pytest

from logic_core.tester import (MODES, ROLES, SIM_PORT, Line, SerialTransport, SimTesterTransport, TesterError,
                               TesterLink, TesterState, list_ports, parse_line)


def wait(link, pred, timeout=3.0):
    lines = []
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        lines += link.drain()
        if pred(link.state, lines):
            return lines
        time.sleep(0.01)
    raise AssertionError(f"condizione non raggiunta; stato={link.state}, righe={[l.text for l in lines][-6:]}")


@pytest.fixture
def link():
    lk = TesterLink(SimTesterTransport(walk_s=0.03), SIM_PORT)
    yield lk
    lk.close()


def test_parse_line_all_kinds():
    st = TesterState()
    assert parse_line(st, "modo=serial cicli=123 errori_compose=2 cpu=240MHz") == "status"
    assert (st.mode, st.cycles, st.compose_errors, st.cpu_mhz) == ("serial", 123, 2, 240)
    assert parse_line(st, "difetti: baud=-2.50% delead=-100us delag=150us parity_err=on") == "faults"
    assert (st.baud_err, st.de_lead, st.de_lag, st.parity_err) == (-2.5, -100, 150, True)
    assert parse_line(st, "modo: walk") == "mode" and st.mode == "walk" and st.cycles == 0
    assert parse_line(st, "walk: CH5 alto") == "walk" and st.walk_ch == 4
    assert parse_line(st, "modo: mixed") == "mode" and st.walk_ch is None
    assert parse_line(st, "\nTester analizzatore logico".strip()) == "boot" and st.boots == 1
    assert parse_line(st, "comandi: help | status") == "help" and parse_line(st, "  walk  : un canale") == "help"
    for e in ("comando sconosciuto (help)", "valore non valido (-20..20)", "modo sconosciuto: off|mixed|serial|walk",
              "usare: parity on|off", "config.h: OUT_PINS[0]=40 non valido"):
        assert parse_line(st, e) == "error"
    assert parse_line(st, "qualcosa") == "other"
    assert parse_line(st, "modo: inesistente") == "mode" and st.mode == "mixed"     # modo sconosciuto ignorato


def test_roles_cover_every_mode():
    assert set(ROLES) == set(MODES) and all(len(r) == 8 for r in ROLES.values())


def test_connect_reads_banner_and_boot_mode(link):
    lines = wait(link, lambda s, ls: s.mode == "mixed" and s.boots == 1)
    kinds = [l.kind for l in lines]
    assert kinds[0] == "boot" and "help" in kinds and kinds[-1] == "mode"
    assert link.alive


def test_mode_commands_and_status_with_rate(link):
    wait(link, lambda s, ls: s.mode == "mixed")
    link.send("mode serial")
    wait(link, lambda s, ls: s.mode == "serial")
    link.poll()
    wait(link, lambda s, ls: s.cpu_mhz == 240)
    time.sleep(0.4)
    link.poll()
    wait(link, lambda s, ls: s.rate is not None)
    assert 30 < link.state.rate < 70                       # serial = 50 cicli/s
    link.send("mode off")
    wait(link, lambda s, ls: s.mode == "off" and s.cycles == 0)


def test_polling_is_quiet_but_manual_status_is_not(link):
    wait(link, lambda s, ls: s.mode == "mixed")
    link.poll()
    ls = wait(link, lambda s, l: s.cpu_mhz == 240)
    assert [l.quiet for l in ls if l.kind in ("status", "faults")] == [True, True]
    link.send("status")
    ls = wait(link, lambda s, l: any(x.kind == "faults" for x in l))
    assert any(l.kind == "tx" and l.text == "status" for l in ls)
    assert [l.quiet for l in ls if l.kind in ("status", "faults")] == [False, False]


def test_fault_commands_roundtrip_and_validation(link):
    wait(link, lambda s, ls: s.mode == "mixed")
    link.send("baud 2.5")
    wait(link, lambda s, ls: s.baud_err == 2.5)
    link.send("delead -100")
    wait(link, lambda s, ls: s.de_lead == -100)
    link.send("delag 150")
    link.send("parity on")
    link.send("crc on")
    wait(link, lambda s, ls: s.de_lag == 150 and s.parity_err and s.crc_err)
    ls = None
    link.send("baud 99")
    ls = wait(link, lambda s, l: any(x.kind == "error" for x in l))
    assert any("valore non valido" in x.text for x in ls) and link.state.baud_err == 2.5
    link.send("faults reset")
    wait(link, lambda s, ls: s.baud_err == 0 and not s.parity_err and not s.crc_err and s.de_lead == 100 and s.de_lag == 60)
    link.send("parity boh")
    ls = wait(link, lambda s, l: any(x.kind == "error" for x in l))
    link.send("frobnicate")
    wait(link, lambda s, l: sum(x.kind == "error" for x in l) >= 1)


def test_walk_announces_channels_in_order(link):
    wait(link, lambda s, ls: s.mode == "mixed")
    link.send("mode walk")
    seen = []

    def collect(s, ls):
        seen[:] = [x.text for x in ls if x.kind == "walk"]
        return len(seen) >= 9
    wait(link, collect)
    assert seen[:8] == [f"walk: CH{i} alto" for i in (2, 3, 4, 5, 6, 7, 8, 1)]      # come il firmware: parte da CH1 senza annuncio
    assert link.state.walk_ch is not None


def test_lines_split_across_reads_and_crlf():
    class Chunky:
        def __init__(self):
            self.parts = [b"modo=mix", b"ed cicli=5 errori_compose=0 cpu=240MHz\r", b"\ndifetti: baud=0.00% delead=1us ",
                          b"delag=2us parity_err=off\r\n"]
        def read(self):
            if self.parts:
                return self.parts.pop(0)
            time.sleep(0.01)
            return b""
        def write(self, d): pass
        def close(self): pass
    lk = TesterLink(Chunky())
    try:
        wait(lk, lambda s, ls: s.de_lag == 2 and s.cycles == 5 and s.mode == "mixed")
    finally:
        lk.close()


def test_transport_failure_is_reported():
    class Dead:
        def read(self): raise TesterError("porta persa: scollegata")
        def write(self, d): raise TesterError("scrittura non riuscita")
        def close(self): pass
    lk = TesterLink(Dead())
    try:
        ls = wait(lk, lambda s, l: any(x.kind == "error" for x in l))
        assert "porta persa" in ls[-1].text and not lk.alive and lk.error
        with pytest.raises(TesterError):
            lk.send("status")
    finally:
        lk.close()


def test_serial_transport_without_or_with_pyserial(monkeypatch):
    try:
        import serial  # noqa: F401
    except ImportError:
        with pytest.raises(TesterError, match="pyserial"):
            SerialTransport("COM99")
        assert list_ports() == []
    else:
        with pytest.raises(TesterError, match="impossibile aprire"):
            SerialTransport("/dev/ttyNONESISTE")
        assert isinstance(list_ports(), list)


def test_open_sim_port_and_close_idempotent():
    lk = TesterLink.open(SIM_PORT)
    lk.close()
    lk.close()
    assert not lk.alive


def test_parse_faults_line_with_and_without_crc_field():
    st = TesterState()
    assert parse_line(st, "difetti: baud=0.00% delead=100us delag=60us parity_err=off crc_err=on") == "faults"
    assert st.crc_err is True
    parse_line(st, "difetti: baud=0.00% delead=100us delag=60us parity_err=off")      # firmware senza il comando crc
    assert st.crc_err is False
