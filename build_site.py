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
CSS_V = hashlib.md5((ROOT / "assets" / "style.css").read_bytes()).hexdigest()[:8]
E = html.escape


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def placeholder_svg(name: str, cat: str) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 500">'
        '<rect width="800" height="500" fill="#000"/>'
        '<g stroke="#26262a" stroke-width="1">'
        + "".join(f'<line x1="{x}" y1="0" x2="{x}" y2="500"/>' for x in range(0, 801, 50))
        + "".join(f'<line x1="0" y1="{y}" x2="800" y2="{y}"/>' for y in range(0, 501, 50))
        + "</g>"
        '<path d="M0 330 C100 330 120 140 220 140 S340 380 440 380 S560 190 660 190 S760 300 800 300" '
        'fill="none" stroke="#ff8a1f" stroke-width="3"/>'
        f'<text x="40" y="450" fill="#ececec" font-family="Segoe UI,sans-serif" font-size="38" font-weight="700">{E(name)}</text>'
        f'<text x="40" y="480" fill="#9a9aa2" font-family="Segoe UI,sans-serif" font-size="18">{E(cat)}</text>'
        "</svg>"
    )


def dl_url(repo: str, sw: dict, d: dict) -> str:
    if d.get("url"):
        return d["url"]
    if not d.get("file"):
        return ""
    tag = sw.get("release_tag") or f"{sw['slug']}-v{sw.get('version', '')}"
    return f"https://github.com/{repo}/releases/download/{tag}/{d['file']}"


def page(site: dict, title: str, body: str, depth: int, desc: str = "") -> str:
    rel = "../" * depth
    full = f"{title} — {site['title']}" if title else site["title"]
    return f"""<!doctype html>
<html lang="it"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{E(full)}</title>
<meta name="description" content="{E(desc or site['tagline'])}">
<meta name="theme-color" content="#000000">
<link rel="icon" type="image/png" href="{rel}assets/icon_fulmine.png">
<link rel="stylesheet" href="{rel}assets/style.css?v={CSS_V}">
</head><body>
<header class="top"><div class="wrap">
<a class="brand" href="{rel}index.html"><img src="{rel}assets/scritta.png" alt="Tornado Laboratori"></a>
<nav><a href="{rel}index.html#laboratorio">Laboratorio</a><a href="{rel}index.html#software">Software</a><a href="{rel}index.html#contatti">Contatti</a></nav>
</div></header>
{body}
<footer><div class="wrap"><span>© {E(site['title'])} — {E(site.get('location',''))}</span>
<span>{E(site.get('callsign',''))}</span></div></footer>
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
    body = f"""
<div class="hero"><div class="wrap">
<div class="splash"><img class="bolt" src="assets/fulmine.png" alt="Tornado Laboratori">
<div class="line">{E(site['tagline'])} &nbsp;·&nbsp; {E(site.get('callsign',''))} &nbsp;·&nbsp; {E(site.get('location',''))}</div></div>
<h1>{E(h['headline'])}</h1><p>{E(h['text'])}</p>
<a class="btn" href="#software">Scarica i software</a> <a class="btn ghost" href="#laboratorio">Il laboratorio</a>
</div></div>
<section id="laboratorio"><div class="wrap"><h2>Il laboratorio</h2>
<p class="sub">Attività di {E(site['title'])}.</p><div class="grid">{acts}</div></div></section>
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
