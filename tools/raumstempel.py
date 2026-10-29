"""Raumstempel aus Allplan-Grundrissen lesen (Raumnummer, Bezeichnung, Fläche, OK FFB, OK RFB, UK RFB, UK RD, UK FD, FB).

Stempelaufbau (1:50): Raumnummer, Bezeichnung, dann Zeilen 'Label ... Wert' mit rechtsbündigem Wert.
Werte wie '6,700 bis 3,150' (veränderliche Höhe) werden als (max, min) zurückgegeben.
"""
import re, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from planraster import oeffnen

LABELS = {"Fläche": "Flaeche", "OK FFB": "OK_FFB", "OK RFB": "OK_RFB", "UK RFB": "UK_RFB",
          "UK RD": "UK_RD", "UK FD": "UK_FD", "FB": "FB"}
ZAHL = r"[+-]?\d+[,.]\d+"


def _zeilen(page):
    out = []
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t:
                out.append((l["bbox"], t))
    return out


def _wert(t):
    t = t.replace("m²", "").strip()
    z = re.findall(ZAHL, t)
    if not z:
        return t or None
    v = [float(x.replace(",", ".")) for x in z]
    return v[0] if len(v) == 1 else {"max": max(v), "min": min(v), "text": t}


def lesen(pdf, grid, muster=r"FW\.\d\d\.[\w.]+"):
    d, p, raster = oeffnen(pdf, grid)
    Z = _zeilen(p)
    nummern = [z for z in Z if re.fullmatch(muster, z[1])]
    stempel = []
    for (x0, y0, x1, y1), nr in nummern:
        h = y1 - y0
        st = {"Raumnummer": nr}
        # Spalte des Stempels: Labels links bündig mit der Raumnummer, Werte rechts davon in derselben Zeile
        spalte = [z for z in Z if abs(z[0][0] - x0) < 1.5 and y0 < z[0][1] < y0 + 9 * h]
        naechste = min([z[0][1] for z in nummern if abs(z[0][0] - x0) < 1.5 and z[0][1] > y0 + 1] + [1e9])
        spalte = sorted([z for z in spalte if z[0][1] < naechste], key=lambda z: z[0][1])
        for (bx0, by0, bx1, by1), t in spalte:
            if t in LABELS:
                werte = [z for z in Z if abs(z[0][1] - by0) < 2.5 and bx1 + 2 < z[0][0] < x0 + 9 * h * 1.0
                         and z[0][0] > bx1 and z[1] not in LABELS]
                if werte:
                    w = min(werte, key=lambda z: z[0][0] - bx1)
                    v = _wert(w[1])
                    if LABELS[t] in ("Flaeche", "FB") or not isinstance(v, str):
                        st[LABELS[t]] = v
            elif "Raumbezeichnung" not in st and not re.match(ZAHL, t):
                st["Raumbezeichnung"] = t
        st["x"], st["y"] = [round(float(v), 3) for v in raster.m((x0 + x1) / 2, (y0 + y1) / 2)]
        stempel.append(st)
    return stempel, raster


def alle(projekt_dir, grid):
    """Stempel aller Grundrisse aus projekt.json (Eintrag 'stempel_aus': {Geschoss: Plandatei}) -> raumstempel.json."""
    import json
    P = json.loads((Path(projekt_dir) / "projekt.json").read_text())
    out = {}
    for gs, plan in P["stempel_aus"].items():
        S, r = lesen(Path(projekt_dir) / "plaene" / plan, grid)
        praefix = P["raumnummer_praefix"][gs]
        for s in S:
            if s["Raumnummer"].startswith(praefix):  # Lufträume/Nachbargeschoss im Plan ignorieren
                s["Geschoss"], s["Quelle"] = gs, plan
                out[s["Raumnummer"]] = s
    return out


if __name__ == "__main__":
    import json
    proj = Path(sys.argv[1])
    M = json.loads((proj / "viewer" / "model.json").read_text())
    S = alle(proj, M["grid"])
    (proj / "raumstempel.json").write_text(json.dumps(S, ensure_ascii=False, indent=1))
    print(len(S), "Raumstempel")
