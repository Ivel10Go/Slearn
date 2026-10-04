"""Erzeugt die sechs Faultier-Logo-Entwuerfe (SVG, 512x512) nach dem Muster des
ip-as-logo-skill: eine Silhouette, wenige runde Grundformen, drei Farben
(Tinte, Fell, Hintergrund), Figur waechst aus der unteren Ecke."""
from pathlib import Path

INK, FUR, FACE, LIGHT = "#0a0a0a", "#cdcdcd", "#ffffff", "#e9e9e9"
OUT = Path(__file__).parent

def kopf(line, fell=True):
    pelz = (f'<ellipse cx="60" cy="58" rx="31" ry="27" fill="{FUR}" stroke="{line}" stroke-width="2.4"/>' if fell else "")
    return pelz + f'''
<path d="M60 43C76 43 85 52 85 63C85 74 74 81 60 81C46 81 35 74 35 63C35 52 44 43 60 43Z" fill="{FACE}"/>
<ellipse cx="41" cy="71" rx="4" ry="2.5" fill="#ececec"/><ellipse cx="79" cy="71" rx="4" ry="2.5" fill="#ececec"/>
<path d="M56 56.5C52 51.5 42 54.5 37 63C35 67.5 39 70 43.5 68C50.5 65 58 61.5 56 56.5Z" fill="{INK}"/>
<path d="M64 56.5C68 51.5 78 54.5 83 63C85 67.5 81 70 76.5 68C69.5 65 62 61.5 64 56.5Z" fill="{INK}"/>
<circle cx="48.5" cy="60.5" r="3.4" fill="{FACE}"/><circle cx="71.5" cy="60.5" r="3.4" fill="{FACE}"/>
<circle cx="48.9" cy="60.9" r="2" fill="{INK}"/><circle cx="71.9" cy="60.9" r="2" fill="{INK}"/>
<ellipse cx="60" cy="66" rx="4.4" ry="2.9" fill="{INK}"/>
<path d="M54.5 70.5q2.75 3 5.5 0q2.75 3 5.5 0" fill="none" stroke="{INK}" stroke-width="2" stroke-linecap="round"/>'''

def place(cx, cy, s, mirror, body, oy=58):
    return f'<g transform="translate({cx} {cy}) scale({-s if mirror else s} {s}) translate(-60 -{oy})">{body}</g>'

def svg(bg, inner):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512" width="512" height="512"><rect width="512" height="512" fill="{bg}"/>{inner}</svg>\n'

def variants():
    for i, mirror in enumerate((False, True), 1):
        dark = i == 2
        bg = INK if dark else LIGHT
        x = 512 - 175 if mirror else 175
        yield f"A{i}", svg(bg, place(x, 335, 7.6, mirror, kopf(INK)))
        ast = FUR if dark else INK
        rand = bg
        arme = ""
        for sx in (1, -1):
            d = f"M{256 - sx*60} 330C{256 - sx*125} 280 {256 - sx*125} 170 {256 - sx*105} 85"
            arme += f'<path d="{d}" fill="none" stroke="{rand}" stroke-width="62" stroke-linecap="round"/>'
            arme += f'<path d="{d}" fill="none" stroke="{FUR}" stroke-width="44" stroke-linecap="round"/>'
        yield f"B{i}", svg(bg, f'<path d="M20 70H492" stroke="{ast}" stroke-width="30" stroke-linecap="round"/>' + arme + place(256, 345, 5.6, mirror, kopf(INK)))
        yield f"C{i}", svg(INK if dark else LIGHT, place(256, 262, 6.6, False, kopf(INK)))

for name, content in variants():
    (OUT / f"faultier-logo-{name}.svg").write_text(content, encoding="utf-8")
cards = "".join(f'<figure style="width:240px;margin:0">{(OUT / f"faultier-logo-{n}.svg").read_text(encoding="utf-8").replace(chr(34)+"512"+chr(34)+" height="+chr(34)+"512"+chr(34), chr(34)+"240"+chr(34)+" height="+chr(34)+"240"+chr(34), 1)}<figcaption>{n}</figcaption></figure>' for n in ("A1","A2","B1","B2","C1","C2"))
(OUT / "vorschau.html").write_text(f'<!doctype html><meta charset="utf-8"><title>Faultier-Logo</title><body style="font:13px sans-serif;display:flex;flex-wrap:wrap;gap:16px;padding:20px;background:#fff">{cards}', encoding="utf-8")
