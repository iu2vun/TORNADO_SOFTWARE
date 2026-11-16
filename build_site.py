#!/usr/bin/env python3
"""Generatore statico di Tornado Laboratori. Solo libreria standard.

Uso:  python build_site.py [--out dist]
Ogni cartella in catalog/<slug>/ con software.json e (opz.) screenshot -> pagina software.
I download puntano alle GitHub Releases: tag = release_tag oppure '<slug>-v<version>'.
"""
import argparse, html, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
import hashlib
CSS_V = hashlib.md5((ROOT / "assets" / "style.css").read_bytes() + (ROOT / "assets" / "crt.js").read_bytes()).hexdigest()[:8]
E = html.escape


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def placeholder_svg(name: str, cat: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500">'
        '<rect width="800" height="500" fill="#050300"/>'
        '<g stroke="#2e1d00" stroke-width="1">'
        + "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="500"/>' for x in range(0, 801, 50))
        + "".join(f'<line x1="0" y1="{y}" x2="800" y2="{y}"/>' for y in range(0, 501, 50))
        + "</g>"
        '<path d="M0 330 C100 330 120 140 220 140 S340 380 440 380 S560 190 660 190 S760 300 800 300" '
        'fill="none" stroke="#ffb000" stroke-width="3"/>'
        f'<text x="40" y="450" fill="#ffe08a" font-family="VT323,monospace" font-size="48">{E(name)}</text>'
        f'<text x="40" y="480" fill="#b87800" font-family="VT323,monospace" font-size="26">{E(cat)}</text>'
        "</svg>"
    )


def dl_url(repo: str, sw: dict, d: dict) -> str:
    if d.get("url"):
        return d["url"]
    if not d.get("file"):
        return ""
    tag = sw.get("release_tag") or f"{sw['slug']}-v{sw.get('version', '')}"
    return f"https://github.com/{repo}/releases/download/{tag}/{d['file']}"


def page(site: dict, title: str, body: str, depth: int, desc: str = "", status_tip: str = "") -> str:
    rel = "../" * depth
    full = f"{title} — {site['title']}" if title else site["title"]
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(full)}</title>
<meta name="description" content="{E(desc or site['tagline'])}">
<meta name="theme-color" content="#0a0908">
<link rel="icon" type="image/png" href="{rel}assets/icon_fulmine.png">
<link rel="preload" href="{rel}assets/fonts/VT323-Regular.ttf" as="font" type="font/ttf" crossorigin>
<link rel="stylesheet" href="{rel}assets/style.css?v={CSS_V}">
</head><body>
<div id="room"><div id="monitor"><div id="bezel"><div id="tube">
<div id="screen">
<header class="top"><a class="brand" href="{rel}index.html">{E(site['title'])}</a>
<nav><a href="{rel}index.html#laboratorio">Laboratorio</a><a href="{rel}index.html#chisono">Chi sono</a><a href="{rel}index.html#software">Software</a><a href="{rel}index.html#contatti">Contatti</a></nav></header>
{body}
<footer><div class="wrap"><span>© {E(site['title'])} — {E(site.get('location',''))}</span>
<span>{E(site.get('callsign',''))}</span></div></footer>
<div id="status"><span id="stmsg" data-def="&gt; SELEZIONA UN PROGRAMMA">&gt; SELEZIONA UN PROGRAMMA</span><span>{E(site.get('callsign',''))} · {E(site.get('location','').upper())}</span></div>
</div>
<div id="crt"></div><div id="vig"></div><div id="glare"></div>
</div></div>
<div id="panel"><div class="ctl">
<div class="kn"><button type="button" data-k="lum" role="slider" aria-label="Luminosità" aria-valuemin="0" aria-valuemax="100"><i></i></button><span>LUM <b>60</b></span></div>
<div class="kn"><button type="button" data-k="con" role="slider" aria-label="Contrasto" aria-valuemin="0" aria-valuemax="100"><i></i></button><span>CONTR <b>60</b></span></div>
<div class="kn"><button type="button" data-k="foc" role="slider" aria-label="Focus" aria-valuemin="0" aria-valuemax="100"><i></i></button><span>FOCUS <b>80</b></span></div>
<button type="button" id="reset">RESET</button><div class="led"></div></div></div>
</div></div>
<div id="lb"><img alt=""></div>
<script>
const lb=document.getElementById('lb'),li=lb.querySelector('img');
document.querySelectorAll('.gallery img').forEach(i=>i.onclick=()=>{{li.src=i.src;lb.style.display='flex'}});
lb.onclick=()=>lb.style.display='none';
document.addEventListener('keydown',e=>{{if(e.key==='Escape')lb.style.display='none'}});
const fb=document.querySelectorAll('.filters button');
fb.forEach(b=>b.onclick=()=>{{fb.forEach(x=>x.classList.remove('on'));b.classList.add('on');
document.querySelectorAll('.sw').forEach(c=>c.style.display=(b.dataset.c==='*'||c.dataset.c===b.dataset.c)?'':'none')}});
</script>
<script src="{rel}assets/crt.js?v={CSS_V}" defer></script>
</body></html>"""


def build(out: Path):
    site = load_json(ROOT / "site.json")
    repo = site["github_repo"]
    if out.exists():
        shutil.rmtree(out)
    (out / "assets").mkdir(parents=True)
    shutil.copy(ROOT / "assets" / "style.css", out / "assets" / "style.css")
    for extra in (ROOT / "assets").glob("*"):
        if extra.name != "style.css" and extra.is_file():
            shutil.copy(extra, out / "assets" / extra.name)
        elif extra.is_dir():
            shutil.copytree(extra, out / "assets" / extra.name)

    sws = []
    for d in sorted((ROOT / "catalog").iterdir()):
        f = d / "software.json"
        if not d.is_dir() or not f.exists():
            continue
        sw = load_json(f)
        sw["slug"] = d.name
        dest = out / "software" / d.name
        (dest / "img").mkdir(parents=True)
        shots = []
        for s in sw.get("screenshots", []):
            src = d / s
            if not src.exists():
                print(f"AVVISO: {d.name}: screenshot mancante {s}", file=sys.stderr)
                continue
            shutil.copy(src, dest / "img" / src.name)
            shots.append(f"img/{src.name}")
        sw["_shots"] = shots
        if shots:
            sw["_thumb"] = f"software/{d.name}/{shots[0]}"
        else:
            (dest / "img" / "placeholder.svg").write_text(
                placeholder_svg(sw["name"], sw.get("category", "")), encoding="utf-8")
            sw["_thumb"] = f"software/{d.name}/img/placeholder.svg"
        sws.append(sw)
    sws.sort(key=lambda s: (s.get("order", 999), s["name"].lower()))

    # ---- home
    acts = "".join(
        f'<div class="card"><span class="icon">{E(a["icon"])}</span><h3>{E(a["title"])}</h3><p>{E(a["text"])}</p></div>'
        for a in site["activities"])
    ab = site.get("about")
    about = ""
    if ab:
        paras = "".join(f"<p>{E(t)}</p>" for t in ab["text"])
        li = f'<p><a class="btn ghost" href="{E(ab["linkedin"])}" target="_blank" rel="noopener">LinkedIn</a></p>' if ab.get("linkedin") else ""
        ph = next((f for f in ("riccardo.jpg", "riccardo.jpeg", "riccardo.png") if (ROOT / "assets" / f).is_file()), None)
        img = f'<img class="photo" src="assets/{ph}" alt="{E(ab["name"])}">' if ph else ""
        about = (f'<section id="chisono"><div class="wrap"><h2>Chi sono</h2>'
                 f'<div class="card about{" withphoto" if ph else ""}">{img}<div><h3>{E(ab["name"])}</h3>'
                 f'<p class="role">{E(ab["role"])} — {E(ab.get("location",""))}</p>{paras}{li}</div></div></div></section>')
    cats = sorted({s.get("category", "Altro") for s in sws})
    filt = '<div class="filters"><button class="on" data-c="*">Tutti</button>' + "".join(
        f'<button data-c="{E(c)}">{E(c)}</button>' for c in cats) + "</div>"
    cards = ""
    for s in sws:
        ver = f'<span class="tag or">v{E(s["version"])}</span>' if s.get("version") else '<span class="tag">Prossimamente</span>'
        cards += (
            f'<div class="card sw" data-c="{E(s.get("category", "Altro"))}">'
            f'<a href="software/{s["slug"]}/index.html"><img src="{s["_thumb"]}" alt="{E(s["name"])}" loading="lazy"></a>'
            f'<div class="body"><h3><a href="software/{s["slug"]}/index.html">{E(s["name"])}</a></h3>'
            f'<p>{E(s.get("tagline", ""))}</p>'
            f'<div class="meta">{ver}<span class="tag">{E(s.get("category", ""))}</span></div></div></div>')
    h = site["hero"]
    mail = site.get("contact_email", "")
    if mail:
        contact = f'<a class="btn" href="mailto:{E(mail)}">{E(mail)}</a>'
    else:
        contact = (f'<a class="btn" href="https://github.com/{E(repo)}/issues">Scrivi su GitHub (Issues)</a>'
                   f' <a class="btn off" href="https://github.com/{E(repo.split("/")[0])}">Profilo GitHub</a>')
    rows = "".join(
        f'<a class="row" href="software/{x["slug"]}/index.html" data-tip="{E(x.get("tagline", ""))}">'
        f'<span>{E(x["name"])}</span><span>{E(x.get("version") or "—")}</span><span>[ SCARICA ]</span></a>'
        for x in sws)
    body = f"""
<div class="hero"><div>
<img class="bolt" src="assets/fulmine.png" alt="Tornado Laboratori">
<div class="l1 type">&gt; BENVENUTO NEL LABORATORIO</div>
<h1 class="type">{E(h['headline'])}</h1><p class="type">{E(h['text'])}</p>
</div>
<div class="dir"><div class="th"><span>DIRECTORY / SOFTWARE</span><span>VERSIONE</span><span>DOWNLOAD</span></div>{rows}</div>
</div>
<section id="laboratorio"><div class="wrap"><h2>Il laboratorio</h2>
<p class="sub">Attività di {E(site['title'])}.</p><div class="grid">{acts}</div></div></section>
{about}
<section id="software"><div class="wrap"><h2>Software</h2>
<p class="sub">Download liberi. Ogni scheda riporta versione, requisiti e checksum.</p>{filt}<div class="grid">{cards}</div></div></section>
<section id="contatti" style="border:0"><div class="wrap"><h2>Contatti</h2>
<p class="sub">Richieste, segnalazioni di bug e sviluppi su misura.</p>
{contact}</div></section>"""
    (out / "index.html").write_text(page(site, "", body, 0), encoding="utf-8")

    # ---- pagine software
    for s in sws:
        dls = ""
        for d in s.get("downloads", []):
            u = dl_url(repo, s, d)
            if not u:
                continue
            sha = f'<small>SHA-256: {E(d["sha256"])}</small>' if d.get("sha256") else ""
            dls += f'<a class="btn" href="{E(u)}">⬇ {E(d["label"])}</a>{sha}'
        if not dls:
            dls = '<span class="btn off">Download non ancora disponibile</span>'
        info = ""
        for k, label in (("version", "Versione"), ("released", "Rilascio"), ("requirements", "Requisiti")):
            if s.get(k):
                info += f"<dt>{label}</dt><dd>{E(s[k])}</dd>"
        if s.get("platforms"):
            info += f"<dt>Piattaforme</dt><dd>{E(', '.join(s['platforms']))}</dd>"
        desc = "".join(f"<p>{E(p)}</p>" for p in s.get("description", []))
        feat = ("<h3>Funzioni</h3><ul class='feat'>" + "".join(f"<li>{E(x)}</li>" for x in s["features"]) + "</ul>") if s.get("features") else ""
        gal = ("<div class='gallery'>" + "".join(f'<img src="{E(x)}" alt="{E(s["name"])}">' for x in s["_shots"]) + "</div>") if s["_shots"] else ""
        cl = ""
        if s.get("changelog"):
            cl = "<h3>Novità</h3>" + "".join(
                f'<div class="cl"><b>v{E(c["version"])}</b><span>{E(c.get("date", ""))}</span><ul class="feat">'
                + "".join(f"<li>{E(n)}</li>" for n in c.get("notes", [])) + "</ul></div>" for c in s["changelog"])
        body = f"""
<div class="wrap"><div class="crumb"><a href="../../index.html#software">← Software</a></div>
<div class="det"><div>
<span class="tag">{E(s.get('category', ''))}</span>
<h1>{E(s['name'])}</h1><p class="lead">{E(s.get('tagline', ''))}</p>
{desc}{gal}{feat}{cl}</div>
<aside class="side"><h4>Download</h4><div class="dl">{dls}</div><h4>Dettagli</h4><dl class="info">{info}</dl></aside>
</div></div>"""
        (out / "software" / s["slug"] / "index.html").write_text(
            page(site, s["name"], body, 2, s.get("tagline", "")), encoding="utf-8")

    (out / ".nojekyll").write_text("", encoding="utf-8")
    print(f"OK: {len(sws)} software -> {out}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="dist")
    a = ap.parse_args()
    build(Path(a.out))
