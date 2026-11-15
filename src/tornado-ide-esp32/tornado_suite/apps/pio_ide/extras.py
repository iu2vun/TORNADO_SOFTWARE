"""Modifiche a platformio.ini per OTA, debug, file system e partizioni (senza GUI)."""
from __future__ import annotations

import re
from typing import Optional

from . import pio_core as pc

_ENV_OK = re.compile(r"^[A-Za-z0-9_\-]+$")


def section(env: str) -> str:
    return f"env:{env}"


def has_env(text: str, env: str) -> bool:
    return re.search(rf"^\s*\[env:{re.escape(env)}\]", text, re.M) is not None


def add_ota_env(text: str, env: str, host: str, password: str = "", port: int = 0) -> tuple[str, str]:
    """Aggiunge (o aggiorna) l'ambiente ``<env>-ota`` che estende ``env`` con upload_protocol = espota. Ritorna (testo, nome)."""
    host = host.strip()
    if not host or re.search(r"[\s;=\[\]]", host):
        raise ValueError("Indirizzo non valido: usa un IP o un nome (es. 192.168.1.50 o esp32.local)")
    if not _ENV_OK.match(env):
        raise ValueError("Nome ambiente non valido")
    name = f"{env}-ota"
    if password and re.search(r"[\s]", password):
        raise ValueError("La password OTA non può contenere spazi")
    lines = [f"[env:{name}]", f"extends = env:{env}", "upload_protocol = espota", f"upload_port = {host}"]
    flags = ([f"--auth={password}"] if password else []) + ([f"--port={port}"] if port else [])
    if flags:
        lines.append("upload_flags =")
        lines += [f"    {f}" for f in flags]
    block = "\n".join(lines) + "\n"
    if has_env(text, name):
        out = pc.set_option(text, section(name), "upload_port", host)
        out = pc.set_option(out, section(name), "upload_protocol", "espota")
        if flags:
            out = pc.set_list(out, section(name), "upload_flags", flags)
        return out, name
    return text.rstrip("\n") + "\n\n" + block, name


def add_debug_options(text: str, env: str, board: str) -> tuple[str, str]:
    """Opzioni di debug: USB-JTAG integrato per S3/C3/C6/H2 (esp-builtin), altrimenti sonda esterna (esp-prog)."""
    sec = section(env)
    builtin = bool(re.search(r"esp32-?(s3|c3|c6|h2)|esp32s3|esp32c3", board.lower()))
    tool = "esp-builtin" if builtin else "esp-prog"
    out = pc.set_option(text, sec, "debug_tool", tool)
    out = pc.set_option(out, sec, "debug_init_break", "tbreak setup")
    if builtin:
        out = pc.set_option(out, sec, "upload_protocol", "esp-builtin")
    out = pc.set_option(out, sec, "build_type", "debug")
    return out, tool


def get_option(text: str, env: str, key: str) -> str:
    sp = pc._section_span(pc._split(text), section(env))
    if sp is None:
        return ""
    lines = pc._split(text)
    ks = pc._key_span(lines, *sp, key)
    if ks is None:
        return ""
    return pc._KEY.match(lines[ks[0]])["val"].strip()


def set_partitions(text: str, env: str, csv_name: str = "partitions.csv") -> str:
    out = pc.set_option(text, section(env), "board_build.partitions", csv_name)
    return out


def set_filesystem(text: str, env: str, fs: str = "littlefs") -> str:
    if fs not in ("littlefs", "spiffs", "fatfs"):
        raise ValueError("file system non valido")
    return pc.set_option(text, section(env), "board_build.filesystem", fs)


def has_debug(text: str, env: str) -> bool:
    return bool(get_option(text, env, "debug_tool"))
