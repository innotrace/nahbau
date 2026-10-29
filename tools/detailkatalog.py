"""Wandtypen-Katalog aus Detailblatt (z. B. HFW_X_5_039.1_XX_DE_01 'Innenwände Typen Trockenbau 1') und Legende.

Je Typfeld: Überschrift 'TW 01 | 100mm', Ständerwerk, Kennwerte (Brandschutz, max. Raumhöhe, Wanddicke,
Dämmschicht, Schallschutz) und nummerierte Schichten. Zusätzlich wird jedes Typfeld als PNG ausgeschnitten,
damit der Viewer das Detail anzeigen kann.
"""
import json, re, sys, warnings
from pathlib import Path

warnings.filterwarnings("ignore", category=FutureWarning)
import fitz

KENN = {"Brandschutz": "brandschutz", "max. Raumhöhe (m)": "max_raumhoehe", "Wanddicke (mm)": "wanddicke",
        "Dämmschichtdicke MW (mm)": "daemmung", "Schallschutz": "schallschutz"}


def lesen(pdf, bild_dir=None, prefix=""):
    d = fitz.open(pdf)
    p = d[0]
    B = [b for b in p.get_text("blocks") if b[4].strip()]
    koepfe = [b for b in B if re.match(r"\s*TW\s*\d+\s*\|", b[4])]
    zeichnungen = p.get_drawings()
    # Typfelder: Köpfe in Zeilen/Spalten; Feld reicht bis zum nächsten Kopf rechts bzw. darunter
    xs = sorted({round(b[0]) for b in koepfe})
    ys = sorted({round(b[1]) for b in koepfe})
    from collections import Counter
    # Spalten/Zeilen des Rasters: Kopf-Positionen, die mehrfach vorkommen (zweiter Kopf im selben Feld,
    # z. B. 'TW 21' neben 'TW 07', bildet keine eigene Spalte)
    cx = Counter(round(b[0] / 5) * 5 for b in koepfe)
    cy = Counter(round(b[1] / 5) * 5 for b in koepfe)
    spalten = sorted(x for x, n in cx.items() if n >= 2) or sorted(cx)
    zeilen = sorted(y for y, n in cy.items() if n >= 2) or sorted(cy)
    cw = (spalten[1] - spalten[0]) if len(spalten) > 1 else 280
    ch = (zeilen[1] - zeilen[0]) if len(zeilen) > 1 else 230
    kat = {}
    for k in koepfe:
        # Feld = Spalte/Zeile des Kopfs (zwei Köpfe in einem Feld, z. B. 'TW 07' und 'TW 21', teilen sich das Detail)
        fx = max(s for s in spalten if s <= k[0] + 3)
        fy = max(z for z in zeilen if z <= k[1] + 3)
        feld = fitz.Rect(fx - 40, fy - 6, fx - 40 + cw, fy - 6 + ch - 26)
        inhalt = sorted([b for b in B if feld.contains(fitz.Point(b[0] + 1, b[1] + 1))], key=lambda b: (b[1], b[0]))
        m = re.match(r"\s*TW\s*(\d+)\s*\|\s*(.+)", k[4])
        typ = f"TW{int(m.group(1)):02d}"
        e = {"typ": typ, "titel": " ".join(k[4].split()), "schichten": [], "kennwerte": {}, "quelle": Path(pdf).name}
        for b in inhalt:
            t = b[4].strip()
            if "ständerwerk" in t.lower() and len(t) < 40:
                e["staenderwerk"] = t
            for zeile in t.split("\n"):
                for lab, key in KENN.items():
                    if zeile.strip().startswith(lab):
                        e["kennwerte"][key] = zeile.split(":", 1)[1].strip()
            if re.match(r"^(\d+x\s|\d+\s?mm|1x|2x|\+)", t) and b[0] > fx - 5:
                e["schichten"].append(" ".join(t.split()))
            if re.search(r"RC\d Wand", t) and abs(b[1] - k[1]) < 15:
                e["hinweis"] = t
        # gemeinsames Feld: Schichten aus dem anderen Kopf übernehmen, falls hier leer
        # Farbbalken über dem Typfeld (gleiche Farbe wie in der Legende des Übersichtsplans)
        balken = [dd for dd in zeichnungen if dd.get("fill") and dd["rect"].y1 <= feld.y0 + 8 and dd["rect"].y1 > feld.y0 - 20
                  and dd["rect"].x0 < k[0] + 5 < dd["rect"].x1 and dd["rect"].height < 20]
        if balken:
            e["farbe"] = "#%02x%02x%02x" % tuple(round(c * 255) for c in balken[0]["fill"])
        kat[typ] = e
        if bild_dir:
            Path(bild_dir).mkdir(parents=True, exist_ok=True)
            pix = p.get_pixmap(matrix=fitz.Matrix(2.2, 2.2), clip=feld)
            pix.save(str(Path(bild_dir) / f"{typ}.png"))
            e["bild"] = f"{prefix}{typ}.png"
    return kat


def katalog(projekt_dir, detailblaetter, legende):
    """Detailblätter + Legende (aus plan_wandtypen.legende) -> katalog dict, Bilder nach viewer/details/."""
    proj = Path(projekt_dir)
    kat = {}
    for blatt in detailblaetter:
        kat.update(lesen(proj / "plaene" / blatt, proj / "viewer" / "details", "details/"))
    for typ, v in legende.items():
        e = kat.setdefault(typ, {"typ": typ, "titel": v["text"], "schichten": [], "kennwerte": {},
                                 "quelle": "nur Legende Übersichtsplan (Detailblatt fehlt)"})
        if v.get("farbe") or not e.get("farbe"):
            e["farbe"] = None if not v.get("farbe") else "#%02x%02x%02x" % tuple(round(c * 255) for c in v["farbe"])
        e["art"] = v["art"]
        e["legende"] = v["text"]
        m = re.search(r"(\d+)\s*(mm|cm)", v["text"])
        if m and "wanddicke" not in e["kennwerte"]:
            e["kennwerte"]["wanddicke"] = m.group(0)
    return dict(sorted(kat.items()))
