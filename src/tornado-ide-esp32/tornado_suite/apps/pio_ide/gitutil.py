"""Git minimo per l'IDE (senza GUI): stato, stage, commit, diff, log. Usa il comando ``git`` se presente."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def find_git() -> Optional[str]:
    p = shutil.which("git")
    if p:
        return p
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs")):
            if base:
                c = os.path.join(base, "Git", "cmd", "git.exe")
                if os.path.isfile(c):
                    return c
    return None


@dataclass(frozen=True)
class Change:
    path: str           # relativo alla radice del repository
    index: str          # stato in stage (carattere di git status --porcelain)
    work: str           # stato nell'albero di lavoro

    @property
    def staged(self) -> bool:
        return self.index not in (" ", "?", "!")

    @property
    def untracked(self) -> bool:
        return self.index == "?"

    @property
    def label(self) -> str:
        c = self.work if self.work not in (" ", "") else self.index
        return {"M": "modificato", "A": "aggiunto", "D": "eliminato", "R": "rinominato", "?": "nuovo", "U": "conflitto"}.get(c, c)


@dataclass
class Status:
    branch: str = ""
    ahead: int = 0
    behind: int = 0
    changes: list[Change] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.changes is None:
            self.changes = []


class Git:
    def __init__(self, root: str, exe: Optional[str] = None) -> None:
        self.root = root
        self.exe = exe or find_git()

    @property
    def available(self) -> bool:
        return self.exe is not None

    def run(self, *args: str, check: bool = False, timeout: int = 60) -> subprocess.CompletedProcess:
        if not self.exe:
            raise FileNotFoundError("git non trovato")
        r = subprocess.run([self.exe, *args], cwd=self.root, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=timeout, creationflags=_NOWIN, env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"})
        if check and r.returncode != 0:
            raise RuntimeError((r.stderr or r.stdout).strip() or f"git {args[0]} fallito")
        return r

    def is_repo(self) -> bool:
        if not self.available or not os.path.isdir(self.root):
            return False
        try:
            return self.run("rev-parse", "--is-inside-work-tree").stdout.strip() == "true"
        except (OSError, subprocess.SubprocessError):
            return False

    def toplevel(self) -> str:
        return os.path.normpath(self.run("rev-parse", "--show-toplevel").stdout.strip())

    def init(self) -> None:
        self.run("init", check=True)

    def status(self) -> Status:
        r = self.run("status", "--porcelain=v1", "-b", "-uall")
        st = Status()
        for ln in r.stdout.splitlines():
            if ln.startswith("## "):
                head = ln[3:]
                st.branch = head.split("...")[0].replace("No commits yet on ", "").replace("Initial commit on ", "")
                if "[" in head:
                    info = head[head.index("[") + 1:head.rindex("]")]
                    for part in info.split(","):
                        part = part.strip()
                        if part.startswith("ahead"):
                            st.ahead = int(part.split()[1])
                        elif part.startswith("behind"):
                            st.behind = int(part.split()[1])
                continue
            if len(ln) < 4:
                continue
            path = ln[3:]
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            st.changes.append(Change(path.strip('"'), ln[0], ln[1]))
        return st

    def stage(self, paths: list[str]) -> None:
        if paths:
            self.run("add", "--", *paths, check=True)

    def unstage(self, paths: list[str]) -> None:
        if paths:
            r = self.run("reset", "-q", "HEAD", "--", *paths)
            if r.returncode != 0:                      # repository senza commit: rimuove dall'indice
                self.run("rm", "--cached", "-q", "--", *paths, check=True)

    def commit(self, message: str) -> str:
        if not message.strip():
            raise ValueError("Messaggio di commit vuoto")
        self.run("-c", "user.name=" + (self.config("user.name") or "Tornado"), "-c", "user.email=" + (self.config("user.email") or "tornado@localhost"),
                 "commit", "-m", message, check=True)
        return self.run("rev-parse", "--short", "HEAD").stdout.strip()

    def config(self, key: str) -> str:
        return self.run("config", "--get", key).stdout.strip()

    def diff(self, path: str, staged: bool = False, untracked: bool = False) -> str:
        if untracked:
            full = os.path.join(self.root, path)
            try:
                with open(full, encoding="utf-8", errors="replace") as f:
                    body = f.read()
            except OSError:
                return ""
            return f"nuovo file: {path}\n" + "".join("+" + ln + "\n" for ln in body.splitlines())
        args = ["diff", "--no-color", "-U3"] + (["--cached"] if staged else []) + ["--", path]
        return self.run(*args).stdout

    def discard(self, path: str) -> None:
        self.run("checkout", "--", path, check=True)

    def log(self, n: int = 40) -> list[tuple[str, str, str, str]]:
        r = self.run("log", f"-{n}", "--date=short", "--pretty=format:%h\x1f%ad\x1f%an\x1f%s")
        return [tuple(x.split("\x1f", 3)) for x in r.stdout.splitlines() if x.count("\x1f") == 3]  # type: ignore[misc]

    def status_map(self) -> dict[str, str]:
        """{percorso assoluto normalizzato: etichetta} per colorare l'albero dei file."""
        top = self.toplevel()
        out: dict[str, str] = {}
        for c in self.status().changes:
            out[os.path.normcase(os.path.normpath(os.path.join(top, c.path)))] = "?" if c.untracked else (c.work if c.work != " " else c.index)
        return out
