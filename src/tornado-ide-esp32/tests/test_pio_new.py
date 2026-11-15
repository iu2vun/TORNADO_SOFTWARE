"""Test delle funzioni aggiunte all'IDE ESP32: indice simboli, ricerca, git, memoria, partizioni, OTA/debug, modelli, grafico, installazione."""
import os
import stat
import subprocess
import sys
import textwrap

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402

from tornado_suite.apps.pio_ide import (debugsession as ds, extras, gitutil, memstats as ms, partitions as pt, pio_core as pc,  # noqa: E402
                                        pio_install as pi, plotter as pl, projsearch as ps, symbols as sy, templates as tp)


@pytest.fixture(autouse=True)
def _niente_dialoghi(monkeypatch):
    from PySide6.QtWidgets import QDialog, QMessageBox
    monkeypatch.setattr(QDialog, "exec", lambda self: 0)
    monkeypatch.setattr(QMessageBox, "question", staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: None))


@pytest.fixture
def qapp(tmp_path, monkeypatch):
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    from PySide6.QtWidgets import QApplication
    from tornado_suite.core.theme import apply_theme
    app = QApplication.instance() or QApplication([])
    apply_theme(app)
    return app


SRC = '''#include <Arduino.h>
#define LED_PIN 2
int contatore = 0;
class Sensore { public: void begin(int pin); };
static int somma(int a, int b)
{
  return a + b;
}
void Sensore::begin(int pin) { pinMode(pin, INPUT); }
void setup() { Serial.begin(115200); }
void loop() { contatore = somma(contatore, 1); }
'''


# ------------------------------------------------------------- simboli
def test_symbols_index_and_complete():
    names = {(s.kind, s.name) for s in sy.index_text("a.cpp", SRC)}
    assert ("funzione", "somma") in names and ("macro", "LED_PIN") in names and ("classe", "Sensore") in names
    assert ("funzione", "setup") in names and ("variabile", "contatore") in names
    ix = sy.ProjectIndex()
    ix.update_file("a.cpp", SRC)
    assert ix.complete("som")[0][0] == "somma"
    assert ix.lookup("somma")[0].line == 5
    assert sy.word_at("x = somma(1)", 6) == "somma"


def test_symbols_scan_project_and_compiledb(tmp_path):
    root = tmp_path / "p"
    (root / "src").mkdir(parents=True)
    (root / "src" / "main.cpp").write_text(SRC)
    inc = tmp_path / "fw" / "inc"
    inc.mkdir(parents=True)
    (inc / "Lib.h").write_text("void libFunc(int x);\n")
    import json
    (root / "compile_commands.json").write_text(json.dumps([{"directory": str(root), "command": f"g++ -I{inc} -isystem {inc} -c a.cpp", "file": "a.cpp"}]))
    dirs = sy.include_dirs_from_compiledb(str(root))
    assert dirs == [os.path.normpath(str(inc))]
    ix = sy.ProjectIndex(str(root))
    ix.extra_dirs = dirs
    ix.scan()
    assert ix.lookup("libFunc") and ix.lookup("somma")


# ------------------------------------------------------------- ricerca
def test_search_and_replace(tmp_path):
    (tmp_path / "src").mkdir()
    f = tmp_path / "src" / "a.cpp"
    f.write_bytes(b"int x = 1;\r\nint y = x + x;\r\n")
    (tmp_path / ".pio").mkdir()
    (tmp_path / ".pio" / "b.cpp").write_text("x x x")
    hits = ps.search(str(tmp_path), "x", whole=True, case=True)
    assert len(hits) == 3 and all(".pio" not in h.file for h in hits)
    assert ps.search(str(tmp_path), "X", case=True) == []
    over = ps.search(str(tmp_path), "zzz", overrides={str(f): "zzz"})
    assert len(over) == 1
    res = ps.replace_in_files([str(f)], "x", "val", whole=True)
    assert res[str(f)] == 3
    assert f.read_bytes() == b"int val = 1;\r\nint y = val + val;\r\n"          # fine riga CRLF mantenuto
    with pytest.raises(ValueError):
        ps.compile_pattern("")


# ------------------------------------------------------------- git
@pytest.mark.skipif(gitutil.find_git() is None, reason="git assente")
def test_git_flow(tmp_path):
    g = gitutil.Git(str(tmp_path))
    assert not g.is_repo()
    g.init()
    (tmp_path / "a.txt").write_text("uno\n")
    st = g.status()
    assert [c.path for c in st.changes] == ["a.txt"] and st.changes[0].untracked
    g.stage(["a.txt"])
    assert g.status().changes[0].staged
    h = g.commit("primo")
    assert h and g.log(5)[0][3] == "primo"
    (tmp_path / "a.txt").write_text("due\n")
    assert "+due" in g.diff("a.txt")
    assert list(g.status_map().values()) == ["M"]
    g.discard("a.txt")
    assert (tmp_path / "a.txt").read_text() == "uno\n"
    with pytest.raises(ValueError):
        g.commit("  ")


# ------------------------------------------------------------- memoria
def test_memstats(tmp_path):
    h = ms.History(str(tmp_path))
    h.add(ms.Sample(1.0, "e", 100, 1000, 500, 4000, 3.0, 12))
    h.add(ms.Sample(2.0, "e", 120, 1000, 600, 4000, 3.0, 12))
    h2 = ms.History(str(tmp_path))
    assert len(h2.items) == 2 and h2.last_compiled("e") == 12
    assert ms.delta(h2.items[0], h2.items[1]) == (20, 100)
    assert abs(h2.items[1].flash_pct - 15.0) < 1e-9
    nm = "00000010 00000020 T loop()\t/x/.pio/libdeps/e/WiFi/src/a.cpp:10\n00001000 00000008 B counter\t/x/src/main.cpp:3\n"
    assert ms.group_by_origin(ms.parse_nm(nm)) == [("lib: WiFi", 32), ("progetto: main.cpp", 8)]
    p = ms.BuildProgress(10)
    for _ in range(5):
        p.feed("Compiling .pio/build/e/a.o")
    assert p.percent == 45
    p.feed("Linking .pio/build/e/firmware.elf")
    assert p.percent == 95


@pytest.mark.skipif(not (ms.find_binutils("size") and ms.find_binutils("nm") and subprocess.run(["which", "gcc"], capture_output=True).returncode == 0), reason="binutils/gcc assenti")
def test_elf_analysis(tmp_path):
    c = tmp_path / "a.c"
    c.write_text("int dati[100]={1}; int bss[200]; const char s[]=\"ciao\"; int main(void){return dati[0]+bss[0];}\n")
    elf = tmp_path / "a.elf"
    subprocess.run(["gcc", "-g", "-o", str(elf), str(c)], check=True)
    secs = ms.section_sizes(ms.find_binutils("size"), str(elf))
    assert any(s.name == ".text" for s in secs)
    syms = ms.top_symbols(ms.find_binutils("nm"), str(elf), 50)
    assert any(s.name == "bss" and s.size == 800 for s in syms)


# ------------------------------------------------------------- partizioni
def test_partitions():
    assert pt.PRESETS
    for name, (text, flash) in pt.PRESETS.items():
        assert pt.validate(pt.parse_csv(text), flash) == [], name
    bad = pt.parse_csv("a,app,ota_0,0x10000,1M\nb,app,ota_1,0x18000,1M\n")
    errs = pt.validate(bad, 0x400000)
    assert any("allineato" in e for e in errs) and any("sovrappongono" in e for e in errs) and any("otadata" in e for e in errs)
    assert pt.parse_size("1M") == 1 << 20 and pt.parse_size("0x1000") == 4096 and pt.fmt_size(0x140000) == "1280K"
    again = pt.parse_csv(pt.to_csv(pt.parse_csv(pt.PRESETS[next(iter(pt.PRESETS))][0])))
    assert again[2].name == "app0"
    assert pt.validate(pt.parse_csv("a,app,factory,0x10000,3M\n"), 0x200000)[0].startswith("La tabella occupa")


# ------------------------------------------------------------- ini: OTA, debug
def test_extras():
    ini = pc.render_ini(pc.PRESETS[0])
    new, name = extras.add_ota_env(ini, "esp32dev", "192.168.1.50", "pw", 3232)
    assert name == "esp32dev-ota" and "extends = env:esp32dev" in new and "--auth=pw" in new and "--port=3232" in new
    again, _ = extras.add_ota_env(new, "esp32dev", "10.0.0.2")
    assert again.count("[env:esp32dev-ota]") == 1 and "upload_port = 10.0.0.2" in again
    with pytest.raises(ValueError):
        extras.add_ota_env(ini, "esp32dev", "bad host")
    dbg, tool = extras.add_debug_options(ini, "esp32dev", "esp32-s3-devkitc-1")
    assert tool == "esp-builtin" and "build_type = debug" in dbg and extras.has_debug(dbg, "esp32dev")
    assert extras.add_debug_options(ini, "esp32dev", "esp32dev")[1] == "esp-prog"
    assert not extras.has_debug(ini, "esp32dev")
    info = pc.read_project  # il file ini generato resta leggibile
    assert info


def test_check_output_parse():
    d = pc.parse_diag("src/main.cpp:12: [medium:warning] Variable 'x' is never used. [unreadVariable]")
    assert d and d.severity == "warning" and d.line == 12
    assert pc.parse_diag("src/a.cpp:3: [high:error] oops").severity == "error"


# ------------------------------------------------------------- modelli
def test_templates(tmp_path):
    for t in tp.TEMPLATES:
        root = pc.create_project(str(tmp_path), f"p_{t.id}", pc.PRESETS[0])
        tp.apply_template(root, t.id, "esp32dev")
        ini = open(os.path.join(root, "platformio.ini")).read()
        for dep in t.lib_deps:
            assert dep in ini
        main = open(os.path.join(root, "src", "main.cpp")).read()
        assert "void setup()" in main and main.count("{") == main.count("}")
    with pytest.raises(KeyError):
        tp.by_id("non_esiste")


# ------------------------------------------------------------- grafico, debug (parsing)
def test_plot_parse():
    assert pl.parse_values("temp:21.5 umid=40") == [("temp", 21.5), ("umid", 40.0)]
    assert pl.parse_values("1,2,3") == [("v1", 1.0), ("v2", 2.0), ("v3", 3.0)]
    assert pl.parse_values("IP: 192.168.1.10") == [] and pl.parse_values("Ciao") == []


def test_plot_widget(qapp, tmp_path):
    w = pl.PlotterPanel()
    w.resize(500, 300)
    w.feed_bytes(b"a:1 b:2\na:2 b:1\na:3")
    w.feed_bytes(b" b:5\n")
    w.show()
    w.grab()
    assert list(w.canvas.series["a"]) == [1.0, 2.0, 3.0]
    out = tmp_path / "o.csv"
    w.write_csv(str(out))
    assert out.read_text().splitlines()[0] == "campione;a;b"


def test_debug_parse():
    assert ds.parse_stop("Breakpoint 1, loop () at src/main.cpp:12") == ("src/main.cpp", 12)
    assert ds.parse_stop(r"Temporary breakpoint 1, setup () at C:\p\src\main.cpp:7") == (r"C:\p\src\main.cpp", 7)
    assert ds.parse_stop("#1  0x400d in main () at x.cpp:3") is None


def test_debug_session_fake_gdb(qapp, tmp_path):
    """Sessione con un finto «gdb» (script Python): comandi in coda, arresto riconosciuto."""
    from PySide6.QtCore import QEventLoop, QTimer
    script = tmp_path / "fakegdb.py"
    script.write_text(textwrap.dedent('''
        import sys
        print("Remote debugging using :3333", flush=True)
        for ln in sys.stdin:
            c = ln.strip()
            if c.startswith("break"):
                print("Breakpoint 1 at 0x400d: file src/main.cpp, line 12.", flush=True)
            elif c == "continue":
                print("Breakpoint 1, loop () at src/main.cpp:12", flush=True)
            elif c.startswith("quit"):
                break
    '''))
    s = ds.DebugSession()
    got, lines = [], []
    s.stopped.connect(lambda f, l: got.append((f, l)))
    s.output.connect(lines.append)
    s.start([sys.executable, str(script)], str(tmp_path))
    s.send("break src/main.cpp:12")
    s.send("continue")
    loop = QEventLoop()
    t = QTimer()
    t.timeout.connect(loop.quit)
    t.start(2500)
    loop.exec()
    s.stop()
    assert ("src/main.cpp", 12) in got
    assert any("Breakpoint 1 at" in x for x in lines)


# ------------------------------------------------------------- installazione
def test_pio_install_selection():
    c = lambda v: pi.PyCand("/x/py", v)  # noqa: E731
    best = pi.best_python([c((3, 14)), c((3, 13)), c((3, 12)), c((3, 9))])
    assert best.version == (3, 12)
    assert pi.best_python([c((3, 14))]).version == (3, 14)       # unica scelta: la propone ma l'IDE avvisa
    assert pi.best_python([]) is None
    steps = pi.install_steps("/usr/bin/python3.12", pi.venv_dir())
    assert steps[0][1][:3] == ["/usr/bin/python3.12", "-m", "venv"] and "platformio" in steps[2][1]


def test_pio_install_runs_with_fake(tmp_path):
    calls = []

    def fake_run(argv, **k):
        calls.append(argv)
        if argv[1:3] == ["-m", "venv"]:
            (tmp_path / "v" / "bin").mkdir(parents=True)
            (tmp_path / "v" / "bin" / "python").write_text("")
            (tmp_path / "v" / "bin" / "pio").write_text("")
        return subprocess.CompletedProcess(argv, 0, "ok\n", "")

    msgs = []
    assert pi.run_install("/usr/bin/python3", msgs.append, tmp_path / "v", fake_run)
    assert len(calls) == 4 and any("PlatformIO" in m for m in msgs)
    assert pi.venv_pio(tmp_path / "v").name == "pio"

    def failing(argv, **k):
        return subprocess.CompletedProcess(argv, 1, "", "boom")

    msgs.clear()
    assert not pi.run_install("/usr/bin/python3", msgs.append, tmp_path / "w", failing)
    assert any("non riuscito" in m for m in msgs)


# ------------------------------------------------------------- integrazione con un finto pio
FAKE_PIO = '''#!%(py)s
import sys, time
a = sys.argv[1:]
if a[:1] == ["--version"]:
    print("PlatformIO Core, version 0.0-fake"); sys.exit(0)
if a[:1] == ["check"]:
    print("src/main.cpp:3: [medium:warning] variabile non usata [unusedVariable]")
    sys.exit(1)
if a[:1] == ["run"]:
    for n in range(4):
        print("Compiling .pio/build/e/src/f%%d.o" %% n, flush=True)
    print("Linking .pio/build/e/firmware.elf")
    print("RAM:   [=         ]   6.3%% (used 20692 bytes from 327680 bytes)")
    print("Flash: [==        ]  21.0%% (used 275301 bytes from 1310720 bytes)")
    print("========================= [SUCCESS] Took 1.23 seconds =========================")
    sys.exit(0)
sys.exit(0)
'''


def _wait(app, cond, ms_=8000):
    from PySide6.QtCore import QElapsedTimer
    t = QElapsedTimer()
    t.start()
    while t.elapsed() < ms_ and not cond():
        app.processEvents()
    return cond()


def test_ide_integration(qapp, tmp_path):
    from tornado_suite.apps.pio_ide.window import EspIde
    fake = tmp_path / "fakepio"
    fake.write_text(FAKE_PIO % {"py": sys.executable})
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    root = pc.create_project(str(tmp_path), "prog", pc.PRESETS[0])
    (tmp_path / "prog" / "src" / "main.cpp").write_text(SRC)
    w = EspIde()
    w.s.setValue("espide/pio_exe", str(fake))
    w.resize(1300, 800)
    w.show()
    w.apri_file(root)
    w.apri_file(os.path.join(root, "src", "main.cpp"))
    assert _wait(qapp, lambda: w.index.lookup("somma"))
    ed = w.current()
    # completamento + vai alla definizione
    assert ed.index is w.index
    ed.setPlainText(SRC + "som")
    c = ed.textCursor()
    c.movePosition(c.MoveOperation.End)
    ed.setTextCursor(c)
    ed.complete_now(force=True)
    assert "somma" in ed._model.stringList()
    ed.setPlainText(SRC)
    w._goto_def("somma", 0)
    assert w.current().textCursor().blockNumber() + 1 == 5
    # breakpoint
    w._toggle_bp(w.current(), 7)
    assert w.dbg.lines_for(w.current().path) == {7} and w.current().breakpoints == {7}
    w._toggle_bp(w.current(), 7)
    assert not w.dbg.lines_for(w.current().path)
    w._dbg_location("src/main.cpp", 6)
    assert w.current().exec_line == 6
    w._dbg_location("", 0)
    assert w.current().exec_line == 0
    # build con progresso e storico memoria
    w.compila()
    assert _wait(qapp, lambda: not w.runner.running)
    assert "riuscita" in w.lbl_state.text()
    assert len(w.hist.items) == 1 and w.hist.items[0].compiled == 4 and w.hist.items[0].flash_used == 275301
    assert w.mem.g_flash.pct > 20
    w.compila()
    assert _wait(qapp, lambda: not w.runner.running)
    assert w._bp.known and w._bp.expected == 4                      # la seconda build conosce già il totale
    # analisi statica -> problemi
    w.analisi_statica()
    assert _wait(qapp, lambda: not w.runner.running)
    assert w.problems.count() == 1 and "Analisi statica" in w.lbl_state.text()
    # cerca nel progetto
    w.search.ed_q.setText("somma")
    w.search.run()
    assert "risultati" in w.search.lbl.text() and w.search.tree.topLevelItemCount() >= 1
    # trova nel file
    w.find.ed_find.setText("contatore")
    w.find.next()
    assert w.current().textCursor().selectedText() == "contatore"
    # Git
    if gitutil.find_git():
        w.git._init()
        assert w.git.lbl_branch.text().startswith("⎇")
        assert w.proxy.git
    # ota / partizioni / modelli: ini
    from tornado_suite.apps.pio_ide.panels import OtaDialog
    w.write_ini(extras.add_ota_env(w.ini_text(), "esp32dev", "192.168.1.9")[0])
    assert "esp32dev-ota" in [e.name for e in w.info.envs]
    # recenti
    w._fill_recenti()
    assert any(root in a.text() for a in w.menu_recenti.actions())
    w.diagnostica_extra()
    w.close()


def test_ide_formatta_senza_clangformat(qapp, tmp_path, monkeypatch):
    from tornado_suite.apps.pio_ide.window import EspIde
    root = pc.create_project(str(tmp_path), "p2", pc.PRESETS[0])
    w = EspIde()
    w.apri_file(os.path.join(root, "src", "main.cpp"))
    monkeypatch.setattr(EspIde, "_find_clang_format", staticmethod(lambda: None))
    w.current().setPlainText("int a;   \nint b;\t\n\n\n")
    w.formatta()
    assert w.current().toPlainText() == "int a;\nint b;\n"
    w.current().document().setModified(False)
    w.close()
