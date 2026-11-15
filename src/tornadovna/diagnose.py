#!/usr/bin/env python3
"""
diagnose.py  —  verifica connessione SCPI verso LibreVNA-GUI v1.6.5+
Usage:  python diagnose.py [--host 127.0.0.1] [--port 19542]
"""
import sys, socket, time, argparse, subprocess

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 19542


def check_port_listening(port: int) -> bool:
    try:
        out = subprocess.check_output(
            ["netstat", "-ano"], text=True, stderr=subprocess.DEVNULL, timeout=5)
        for line in out.splitlines():
            if f":{port}" in line and "LISTEN" in line:
                parts = line.split()
                pid = parts[-1] if parts else "?"
                try:
                    pname = subprocess.check_output(
                        ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                        text=True, stderr=subprocess.DEVNULL, timeout=3
                    ).strip().strip('"').split('","')[0]
                except Exception:
                    pname = "?"
                print(f"    → porta in ascolto: PID {pid} ({pname})")
                return True
        return False
    except Exception:
        return False


def try_tcp(host, port) -> bool:
    print(f"\n[1] TCP {host}:{port} … ", end="", flush=True)
    try:
        s = socket.create_connection((host, port), timeout=2.0)
        s.close(); print("APERTA ✓"); return True
    except ConnectionRefusedError:
        print("RIFIUTATA ✗"); _hint_no_server(host, port); return False
    except (TimeoutError, OSError) as e:
        print(f"TIMEOUT/ERRORE ✗  ({e})")
        if not check_port_listening(port):
            _hint_no_server(host, port)
        return False


def _hint_no_server(host, port):
    print(f"""
    Nessun processo in ascolto su {host}:{port}.
    Su localhost il timeout NON è il firewall: il server SCPI non è partito.

    SOLUZIONE:
    1. Apri LibreVNA-GUI.
    2. Edit → Preferences → scheda "SCPI Server"
       → spunta "Enable server" → porta {port} → OK
    3. CHIUDI completamente il GUI e RIAPRILO.
    4. Verifica che il device USB sia connesso nel GUI.
    5. Riesegui:  python diagnose.py
""")


def scpi_session(host, port):
    buf = bytearray()

    def readline(sock):
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk: raise ConnectionError("socket chiuso")
            buf.extend(chunk)
        idx = buf.index(b"\n")
        line = bytes(buf[:idx]).decode(errors="replace").rstrip("\r")
        del buf[:idx + 1]
        return line

    def cmd(sock, c):
        # NON aspetta risposta: Result::Empty = nessun byte inviato (tcpserver.cpp)
        sock.sendall(c.encode() + b"\n")

    def query(sock, q):
        sock.sendall(q.encode() + b"\n")
        return readline(sock)

    print("\n[2] Sessione SCPI … ", end="", flush=True)
    try:
        s = socket.create_connection((host, port), timeout=4.0)
        s.settimeout(4.0); print("ok ✓")
    except Exception as e:
        print(f"ERRORE ✗  ({e})"); return

    try:
        idn = query(s, "*IDN?")
        print(f"    IDN : {idn}")
    except Exception as e:
        print(f"    *IDN? fallita: {e}"); s.close(); return

    print("\n[3] Device USB disponibili … ", end="", flush=True)
    try:
        dev_list = query(s, ":DEVice:LIST?").strip()
        if dev_list and dev_list.lower() != "error":
            serials = [x.strip() for x in dev_list.split(",") if x.strip()]
            print(f"trovati {len(serials)} ✓")
            for sn in serials:
                print(f"    • {sn}")
        else:
            print("NESSUNO ✗")
            print("    → Collega il VNA via USB e riprova.")
            s.close(); return
    except Exception as e:
        print(f"ERRORE ✗  ({e})"); s.close(); return

    print("\n[4] Connessione device … ", end="", flush=True)
    try:
        connected = query(s, ":DEVice:CONNect?").strip()
        if connected.lower() == "not connected":
            # prova a connettersi al primo
            first = dev_list.split(",")[0].strip()
            print(f"non connesso, tento {first} … ", end="", flush=True)
            cmd(s, f":DEVice:CONNect {first}")
            time.sleep(0.5)
            connected = query(s, ":DEVice:CONNect?").strip()
        if connected.lower() == "not connected":
            print("FALLITO ✗")
            print("    → Premi 'Connect' nel GUI o ricollegalo via USB.")
            s.close(); return
        print(f"S/N {connected} ✓")
    except Exception as e:
        print(f"ERRORE ✗  ({e})"); s.close(); return

    print("\n[5] Attivazione modo VNA … ", end="", flush=True)
    try:
        cmd(s, ":DEVice:MODE VNA")
        print("ok ✓")
    except Exception as e:
        print(f"ERRORE ✗  ({e})"); s.close(); return

    print("\n[6] Sweep rapido (S11, 10 pt) … ", end="", flush=True)
    try:
        cmd(s, ":VNA:SWEEP FREQUENCY")
        cmd(s, ":VNA:ACQuisition:POINTS 10")
        cmd(s, ":VNA:FREQuency:START 100000000")
        cmd(s, ":VNA:FREQuency:STOP 1000000000")
        time.sleep(0.8)
        raw = query(s, ":VNA:TRACE:DATA? S11")
        if raw.strip().upper() in ("ERROR", "EMPTY", ""):
            print(f"RISPOSTA VUOTA ✗  ('{raw}')")
            print("    → Il VNA non ha ancora dati; aspetta un ciclo di sweep e riprova.")
        else:
            vals = raw.replace("[","").replace("]","").split(",")
            n = len(vals) // 3
            print(f"OK ✓  ({n} punti)")
            if n:
                print(f"    f={float(vals[0])/1e6:.1f} MHz  re={float(vals[1]):.4f}  im={float(vals[2]):.4f}")
            print("\n    ✓ Tutto ok — avvia:  python main.py")
    except Exception as e:
        print(f"ERRORE ✗  ({e})")

    s.close()
    print()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=DEFAULT_HOST)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    args = ap.parse_args()
    ok = try_tcp(args.host, args.port)
    if ok:
        scpi_session(args.host, args.port)
    sys.exit(0 if ok else 1)
