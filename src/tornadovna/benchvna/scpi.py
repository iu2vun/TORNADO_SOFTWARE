"""Client SCPI TCP per LibreVNA-GUI v1.6.5+

Protocollo (da tcpserver.cpp):
  - Comando (set): manda linea, NON riceve risposta (Empty = silenzio totale)
  - Query (?):     manda linea, riceve una riga terminata da \\n
  - Errori:        il server manda "ERROR\\n" solo per i comandi query
"""
import socket
import numpy as np


class ScpiError(Exception):
    pass


class ScpiClient:
    def __init__(self, host="127.0.0.1", port=19542, timeout=4.0):
        self.host, self.port, self.timeout = host, port, timeout
        self.sock = None
        self._buf = bytearray()

    def connect(self):
        self.close()
        self.sock = socket.create_connection((self.host, self.port), self.timeout)
        self.sock.settimeout(self.timeout)
        self._buf = bytearray()

    def close(self):
        if self.sock:
            try:
                self.sock.close()
            finally:
                self.sock = None

    @property
    def connected(self):
        return self.sock is not None

    def _readline(self) -> str:
        """Legge una riga terminata da \\n dal socket."""
        while b"\n" not in self._buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("socket chiuso dal server")
            self._buf += chunk
        idx = self._buf.index(b"\n")
        line = bytes(self._buf[:idx])
        del self._buf[: idx + 1]
        return line.decode(errors="replace").rstrip("\r")

    def cmd(self, c: str):
        """Invia un comando SET. NON aspetta risposta (Empty = nessun byte inviato).
        Questo è il comportamento corretto per LibreVNA: Result::Empty → silenzio."""
        self.sock.sendall(c.encode() + b"\n")

    def query(self, q: str) -> str:
        """Invia una query e legge la risposta (sempre seguita da \\n)."""
        self.sock.sendall(q.encode() + b"\n")
        return self._readline()

    @staticmethod
    def parse_trace(raw: str):
        """'[f,re,im],[f,re,im],...' → (freqs ndarray, s complex ndarray)

        Robusto a dati parziali (sweep in transizione): scarta qualsiasi
        terna incompleta alla fine e garantisce len(freqs) == len(s).
        """
        raw = raw.replace("[", "").replace("]", "")
        vals = [v for v in raw.split(",") if v.strip()]
        # scarta i valori in eccesso che non formano terne complete
        n_complete = (len(vals) // 3) * 3
        if n_complete == 0:
            raise ValueError(f"nessuna terna completa in: {raw[:80]!r}")
        vals = vals[:n_complete]
        a = np.fromiter(vals, dtype=float, count=n_complete).reshape(-1, 3)
        freqs = a[:, 0]
        s     = a[:, 1] + 1j * a[:, 2]
        assert len(freqs) == len(s)
        return freqs, s
