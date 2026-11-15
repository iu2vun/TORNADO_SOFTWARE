"""Indice dei simboli C/C++ del progetto (senza GUI): completamento, vai-alla-definizione.

Non è un compilatore: riconosce con espressioni regolari funzioni, classi/struct/enum, macro, typedef e variabili globali
nelle cartelle del progetto e nelle cartelle di include indicate da ``compile_commands.json`` (framework e librerie).
Il completamento propone quindi i nomi che il progetto «conosce», non i membri di un oggetto (serve un vero compilatore).
"""
from __future__ import annotations

import json
import os
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

CODE_EXT = (".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".ino", ".tcc")
HEADER_EXT = (".h", ".hh", ".hpp", ".hxx", ".tcc")
SKIP = {".pio", ".git", ".vscode", "__pycache__", ".idea", ".tornado", "build"}
KEYWORDS = (
    "alignas alignof auto bool break case catch char class const constexpr continue default delete do double else enum explicit "
    "extern false float for friend goto if inline int long mutable namespace new noexcept nullptr operator private protected "
    "public register return short signed sizeof static static_assert struct switch template this throw true try typedef "
    "typename union unsigned using virtual void volatile while uint8_t uint16_t uint32_t uint64_t int8_t int16_t int32_t "
    "int64_t size_t String "
    "setup loop pinMode digitalWrite digitalRead analogRead analogWrite delay delayMicroseconds millis micros Serial HIGH LOW "
    "INPUT OUTPUT INPUT_PULLUP LED_BUILTIN attachInterrupt xTaskCreate vTaskDelay"
).split()
_NOT_FUNC = {"if", "for", "while", "switch", "return", "else", "catch", "sizeof", "do", "case", "new", "delete", "defined"}


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str           # funzione | classe | struct | enum | macro | tipo | variabile
    file: str
    line: int
    sig: str = ""

    @property
    def label(self) -> str:
        return f"{self.name}{self.sig}" if self.kind == "funzione" else self.name


_RE_DEFINE = re.compile(r"^\s*#\s*define\s+([A-Za-z_]\w*)(\([^)]*\))?")
_RE_TYPE = re.compile(r"^\s*(?:typedef\s+)?(class|struct|enum(?:\s+class)?|union)\s+([A-Za-z_]\w*)\b(?!\s*;)")
_RE_TYPEDEF = re.compile(r"^\s*typedef\s+.*?\b([A-Za-z_]\w*)\s*;\s*$")
_RE_USING = re.compile(r"^\s*using\s+([A-Za-z_]\w*)\s*=")
# funzione: tipo di ritorno + nome(argomenti) con { oppure ; oppure fine riga; ammette Classe::metodo
_RE_FUNC = re.compile(
    r"^(?P<ind>\s*)(?:template\s*<[^>]*>\s*)?(?:(?:static|inline|virtual|constexpr|extern|explicit|friend|IRAM_ATTR|ICACHE_RAM_ATTR)\s+)*"
    r"(?P<ret>[A-Za-z_][\w:<>,\s\*&~]*?[\s\*&])?(?P<name>~?[A-Za-z_]\w*(?:::~?[A-Za-z_]\w*)*)\s*\((?P<args>[^;{}]*?)\)\s*"
    r"(?:const\s*)?(?:noexcept\s*)?(?:override\s*)?(?:=\s*(?:0|default|delete)\s*)?(?P<end>\{|;|$)"
)
_RE_GLOBAL = re.compile(
    r"^(?:static\s+|extern\s+|const\s+|volatile\s+|constexpr\s+)*(?:unsigned\s+|signed\s+)?[A-Za-z_][\w:<>]*[\s\*&]+([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:=|;)"
)


def index_text(path: str, text: str) -> list[Symbol]:
    """Estrae i simboli da un testo C/C++."""
    out: list[Symbol] = []
    is_header = path.lower().endswith(HEADER_EXT)
    depth = 0
    in_block = False
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw
        if in_block:
            if "*/" in line:
                in_block = False
                line = line.split("*/", 1)[1]
            else:
                continue
        if "/*" in line and "*/" not in line.split("/*", 1)[1]:
            in_block = True
            line = line.split("/*", 1)[0]
        line = re.sub(r"//.*$", "", line)
        s = line.strip()
        if not s:
            continue
        m = _RE_DEFINE.match(line)
        if m:
            out.append(Symbol(m[1], "macro", path, n, m[2] or ""))
            continue
        if s.startswith("#"):
            continue
        m = _RE_TYPE.match(line)
        if m:
            kind = {"class": "classe", "struct": "struct", "union": "struct"}.get(m[1], "enum")
            out.append(Symbol(m[2], kind, path, n))
        m = _RE_USING.match(line)
        if m:
            out.append(Symbol(m[1], "tipo", path, n))
        m = _RE_TYPEDEF.match(line)
        if m and "(" not in s:
            out.append(Symbol(m[1], "tipo", path, n))
        # funzioni: definizioni a livello file/classe (profondità 0, oppure 1 nei header = dichiarazioni nei corpi di classe)
        if depth == 0 or (is_header and depth == 1):
            f = _RE_FUNC.match(line)
            if f and f["name"].split("::")[-1] not in _NOT_FUNC and (f["ret"] or "::" in f["name"] or f["name"].startswith("~")):
                ret = (f["ret"] or "").strip()
                if ret not in _NOT_FUNC and not ret.endswith(("=", "return", "else")):
                    out.append(Symbol(f["name"].split("::")[-1], "funzione", path, n, "(" + " ".join(f["args"].split()) + ")"))
            elif depth == 0:
                g = _RE_GLOBAL.match(line)
                if g and "(" not in s.split("=")[0]:
                    out.append(Symbol(g[1], "variabile", path, n))
        depth = max(0, depth + line.count("{") - line.count("}"))
    return out


def word_at(text: str, pos: int) -> str:
    """Identificatore che contiene (o tocca) la posizione ``pos`` di ``text``."""
    if not text:
        return ""
    pos = max(0, min(pos, len(text)))
    a = pos
    while a > 0 and (text[a - 1].isalnum() or text[a - 1] == "_"):
        a -= 1
    b = pos
    while b < len(text) and (text[b].isalnum() or text[b] == "_"):
        b += 1
    w = text[a:b]
    return w if w and not w[0].isdigit() else ""


def prefix_before(text: str, pos: int) -> str:
    """Parte di identificatore che precede ``pos`` (quello che si sta digitando)."""
    a = max(0, min(pos, len(text)))
    b = a
    while a > 0 and (text[a - 1].isalnum() or text[a - 1] == "_"):
        a -= 1
    return text[a:b]


def include_dirs_from_compiledb(root: str) -> list[str]:
    """Cartelle ``-I`` / ``-isystem`` da ``compile_commands.json`` (generato da ``pio run -t compiledb``)."""
    p = Path(root) / "compile_commands.json"
    if not p.is_file():
        for cand in (Path(root) / ".pio" / "build").glob("*/compile_commands.json"):
            p = cand
            break
        else:
            return []
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return []
    dirs: list[str] = []
    seen: set[str] = set()
    for ent in data if isinstance(data, list) else []:
        cmd = ent.get("command") or " ".join(ent.get("arguments", []))
        toks = cmd.replace('\\"', '"').split()
        i = 0
        while i < len(toks):
            t = toks[i]
            d = ""
            if t in ("-I", "-isystem", "-iquote") and i + 1 < len(toks):
                d, i = toks[i + 1], i + 1
            elif t.startswith("-I") and len(t) > 2:
                d = t[2:]
            elif t.startswith("-isystem") and len(t) > 8:
                d = t[8:]
            d = d.strip('"')
            if d:
                d = os.path.normpath(d)
                if d not in seen and os.path.isdir(d):
                    seen.add(d)
                    dirs.append(d)
            i += 1
    return dirs


class ProjectIndex:
    """Indice con cache per mtime. ``scan()`` si può chiamare da un thread; le letture sono sempre sicure."""

    MAX_FILES = 6000
    MAX_BYTES = 600_000

    def __init__(self, root: str = "") -> None:
        self.root = root
        self._by_file: dict[str, tuple[float, list[Symbol]]] = {}
        self._names: dict[str, list[Symbol]] = {}
        self._lock = threading.Lock()
        self.extra_dirs: list[str] = []

    def set_root(self, root: str) -> None:
        with self._lock:
            self.root = root
            self._by_file.clear()
            self._names = {}

    def _walk(self, folder: str, limit: int) -> Iterable[str]:
        n = 0
        for dp, dns, fns in os.walk(folder):
            dns[:] = [d for d in dns if d not in SKIP and not d.startswith(".")]
            for f in fns:
                if f.lower().endswith(CODE_EXT):
                    yield os.path.join(dp, f)
                    n += 1
                    if n >= limit:
                        return

    def files(self) -> list[str]:
        if not self.root:
            return []
        res: list[str] = []
        for sub in ("src", "include", "lib", "test"):
            d = os.path.join(self.root, sub)
            if os.path.isdir(d):
                res += list(self._walk(d, self.MAX_FILES))
        for f in os.listdir(self.root) if os.path.isdir(self.root) else []:
            p = os.path.join(self.root, f)
            if os.path.isfile(p) and f.lower().endswith(CODE_EXT):
                res.append(p)
        budget = self.MAX_FILES
        for d in self.extra_dirs:
            if budget <= 0:
                break
            for p in self._walk(d, budget):
                res.append(p)
                budget -= 1
        return res

    def scan(self) -> int:
        """(Ri)indicizza i file nuovi o modificati. Ritorna il numero di file elaborati."""
        n = 0
        live: set[str] = set()
        for p in self.files():
            live.add(p)
            try:
                mt = os.path.getmtime(p)
            except OSError:
                continue
            old = self._by_file.get(p)
            if old and old[0] == mt:
                continue
            try:
                if os.path.getsize(p) > self.MAX_BYTES:
                    continue
                text = Path(p).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            syms = index_text(p, text)
            with self._lock:
                self._by_file[p] = (mt, syms)
            n += 1
        with self._lock:
            for p in [k for k in self._by_file if k not in live]:
                del self._by_file[p]
            names: dict[str, list[Symbol]] = {}
            for _mt, syms in self._by_file.values():
                for s in syms:
                    names.setdefault(s.name, []).append(s)
            self._names = names
        return n

    def update_file(self, path: str, text: str) -> None:
        """Aggiorna un file dal testo dell'editor (anche non salvato)."""
        syms = index_text(path, text)
        with self._lock:
            self._by_file[path] = (-1.0, syms)
            names: dict[str, list[Symbol]] = {}
            for _mt, ss in self._by_file.values():
                for s in ss:
                    names.setdefault(s.name, []).append(s)
            self._names = names

    def lookup(self, name: str, prefer_file: str = "") -> list[Symbol]:
        """Definizioni di ``name``: prima le definizioni (.cpp), poi le dichiarazioni (.h); stesso file per primo."""
        with self._lock:
            res = list(self._names.get(name, []))

        def key(s: Symbol) -> tuple:
            return (s.file != prefer_file, s.file.lower().endswith(HEADER_EXT) and s.kind == "funzione", s.file, s.line)

        return sorted(res, key=key)

    def complete(self, prefix: str, extra_words: Iterable[str] = (), limit: int = 40) -> list[tuple[str, str]]:
        """Candidati (testo, tipo) per un prefisso: simboli, parole chiave e parole già presenti nel file corrente."""
        if len(prefix) < 1:
            return []
        low = prefix.lower()
        found: dict[str, str] = {}
        with self._lock:
            names = list(self._names.items())
        for name, syms in names:
            if name.lower().startswith(low) and name != prefix:
                found[name] = syms[0].kind
        for k in KEYWORDS:
            if k.lower().startswith(low) and k != prefix:
                found.setdefault(k, "parola chiave")
        for w in extra_words:
            if w.lower().startswith(low) and w != prefix:
                found.setdefault(w, "testo")
        # locali del progetto prima, poi framework; poi per lunghezza e alfabeto
        order = {"funzione": 0, "variabile": 1, "macro": 2, "classe": 3, "struct": 3, "enum": 3, "tipo": 3, "parola chiave": 4, "testo": 5}
        res = sorted(found.items(), key=lambda kv: (order.get(kv[1], 9), len(kv[0]), kv[0].lower()))
        return res[:limit]

    def count(self) -> int:
        with self._lock:
            return sum(len(s) for _m, s in self._by_file.values())


_WORD = re.compile(r"[A-Za-z_]\w{2,}")


def words_in(text: str) -> set[str]:
    return set(_WORD.findall(text))
