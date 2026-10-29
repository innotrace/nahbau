"""Wandtypen (TW/VW/IW) aus einem farbigen Trockenbau-Übersichtsplan (z. B. HFW_F_5_039.1_00_UE_02) lesen.

1. Legende: Farbfeld über jeder Beschriftung 'TW 01 | 100mm' usw. -> Farbe je Typ.
2. Plan: gefüllte Flächen in Legendenfarben -> Polygone in Modellkoordinaten (über das Achsraster).
   Farben, die im Plan leicht von der Legende abweichen oder mehreren Typen gehören, werden über die
   Typbeschriftungen ('TW15', 'VW08') in der Nähe der Flächen entschieden.
3. Zuordnung: Anteil der Wandgrundfläche, der von Flächen eines Typs überdeckt wird.
"""
import collections, re, sys
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon, Point, box
from shapely.ops import unary_union
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).parent))
from planraster import oeffnen

GRAU = lambda c: max(c) - min(c) < 0.03


def _hex(c):
    return "#%02x%02x%02x" % tuple(round(v * 255) for v in c)


def legende(page, bereich):
    """bereich: fitz.Rect der Legende. Liefert {typ: {'farbe':(r,g,b), 'text': 'TW 01 | 100mm', 'art': 'TW'}}."""
    W = page.get_text("words")
    D = [d for d in page.get_drawings() if d["rect"].intersects(bereich)]
    out = {}
    for w in W:
        if w[4] not in ("TW", "VW", "IW") or not bereich.contains(fitz_point(w[0], w[1])):
            continue
        zeile = sorted([v for v in W if abs(v[1] - w[1]) < 2 and 0 <= v[0] - w[0] < 120], key=lambda v: v[0])
        # nur bis zur nächsten Typbezeichnung (Nachbarspalte der Legende)
        bis = next((i for i, v in enumerate(zeile) if i > 0 and v[4] in ("TW", "VW", "IW")), len(zeile))
        zeile = zeile[:bis]
        text = " ".join(v[4] for v in zeile)
        if text.rstrip().endswith("|"):  # Dicke in der Folgezeile (z. B. 'IW 1 - 15 |' / '245 - 385mm')
            folge = sorted([v for v in W if 4 < v[1] - w[1] < 16 and -2 <= v[0] - w[0] < 120], key=lambda v: v[0])
            text += " " + " ".join(v[4] for v in folge if v[4] not in ("TW", "VW", "IW"))
        m = re.match(r"(TW|VW|IW)\s*(\d+)", text)
        if not m:
            continue
        typ = f"{m.group(1)}{int(m.group(2)):02d}" if m.group(1) != "IW" else "IW"
        feld = [d for d in D if d.get("fill") and d["rect"].x0 < w[0] + 5 < d["rect"].x1 and 0 < w[1] - d["rect"].y1 < 12]
        if feld:
            out[typ] = {"farbe": tuple(round(v, 3) for v in feld[0]["fill"]), "text": text, "art": m.group(1),
                        "legende_hoehe": round(feld[0]["rect"].height, 1)}
        elif typ not in out:
            out[typ] = {"farbe": None, "text": text, "art": m.group(1)}
    return out


def fitz_point(x, y):
    import fitz
    return fitz.Point(x, y)


def _polygone(d, raster):
    """Alle geschlossenen Teilflächen eines Pfads als Shapely-Polygone (Modellkoordinaten)."""
    polys, akt = [], []
    for it in d["items"]:
        if it[0] == "re":
            r = it[1]
            pts = [raster.m(r.x0, r.y0), raster.m(r.x1, r.y0), raster.m(r.x1, r.y1), raster.m(r.x0, r.y1)]
            polys.append(Polygon(pts))
        elif it[0] == "qu":
            q = it[1]
            polys.append(Polygon([raster.m(p.x, p.y) for p in (q.ul, q.ur, q.lr, q.ll)]))
        elif it[0] == "l":
            a, b = it[1], it[2]
            if akt and (abs(akt[-1][0] - a.x) > 0.01 or abs(akt[-1][1] - a.y) > 0.01):
                if len(akt) >= 3:
                    polys.append(Polygon([raster.m(*p) for p in akt]))
                akt = []
            if not akt:
                akt.append((a.x, a.y))
            akt.append((b.x, b.y))
    if len(akt) >= 3:
        polys.append(Polygon([raster.m(*p) for p in akt]))
    return [p.buffer(0) for p in polys if p.is_valid or p.buffer(0).area > 0]


def lesen(pdf, grid, legende_rect, plan_rect):
    import fitz
    d, page, raster = oeffnen(pdf, grid)
    L = legende(page, fitz.Rect(*legende_rect))
    planbox = fitz.Rect(*plan_rect)
    # Typbeschriftungen im Plan
    labels = []
    for w in page.get_text("words"):
        m = re.fullmatch(r"(TW|VW)\s?(\d{1,2})", w[4])
        if m and planbox.contains(fitz_point(w[0], w[1])):
            x, y = raster.m((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
            labels.append((f"{m.group(1)}{int(m.group(2)):02d}", Point(float(x), float(y))))
    # farbige Flächen im Plan
    flaechen = collections.defaultdict(list)
    for dd in page.get_drawings():
        f = dd.get("fill")
        if not f or not dd["rect"].intersects(planbox) or GRAU(f):
            continue
        flaechen[tuple(round(v, 3) for v in f)] += _polygone(dd, raster)
    # Farbe -> Typ: exakt aus Legende (eindeutig), sonst über Beschriftungen in <= 2,5 m
    leg_farben = collections.defaultdict(list)
    for t, v in L.items():
        if v["farbe"]:
            leg_farben[v["farbe"]].append(t)
    zuordnung, protokoll = {}, []
    for farbe, polys in flaechen.items():
        u = unary_union(polys)
        kand = [t for c, ts in leg_farben.items() if max(abs(a - b) for a, b in zip(c, farbe)) < 0.02 for t in ts]
        stimmen = collections.Counter(t for t, p in labels if u.distance(p) < 2.5)
        if len(kand) == 1:
            typ, grund = kand[0], "Legendenfarbe"
        elif kand and "IW" in kand:
            # IW (Installationswand) ist in der Legende als Doppellinie dargestellt -> schmale Streifen
            breite = 2 * u.area / max(u.length, 1e-6)
            typ = "IW" if breite < 0.05 else [t for t in kand if t != "IW"][0]
            grund = f"Legendenfarbe mehrdeutig {kand}, Streifenbreite {breite*100:.1f} cm"
        elif kand:
            k2 = [t for t in kand if stimmen[t]]
            typ = max(k2, key=lambda t: stimmen[t]) if k2 else kand[0]
            grund = f"Legendenfarbe mehrdeutig {kand}, Beschriftungen {dict(stimmen)}"
        elif stimmen:
            typ, grund = stimmen.most_common(1)[0][0], f"Farbe nicht in Legende, Beschriftungen {dict(stimmen)}"
        else:
            typ, grund = None, "Farbe nicht zuordenbar"
        zuordnung[farbe] = typ
        protokoll.append({"farbe": _hex(farbe), "flaechen": len(polys), "flaeche_m2": round(u.area, 2), "typ": typ, "grund": grund})
    typflaechen = collections.defaultdict(list)
    for farbe, polys in flaechen.items():
        if zuordnung[farbe]:
            typflaechen[zuordnung[farbe]] += polys
    return L, {t: unary_union(p) for t, p in typflaechen.items()}, labels, protokoll, raster


def wand_typ(wandpoly, typflaechen, achse_len):
    """Typ mit größter Überdeckung der Wandgrundfläche; liefert (typ, anteil, alle Anteile)."""
    a = wandpoly.area
    ant = {t: wandpoly.intersection(g).area / a for t, g in typflaechen.items() if wandpoly.intersects(g)}
    ant = {t: v for t, v in ant.items() if v > 0.02}
    if not ant:
        return None, 0.0, {}
    t = max(ant, key=ant.get)
    return t, ant[t], ant


def zuordnen(f, geschoss, typflaechen, katalog, labels, quelle, min_anteil=0.15, min_anteil_iw=0.08):
    """Trockenbauwände eines Geschosses den Plan-Wandtypen zuordnen.

    - IfcWallType je Plantyp (Name 'TW02 | 125mm'), Pset HFW_Wandtyp_Detail mit Katalogwerten
    - Wand: Pset HFW_Wandtyp (Wandtyp, Quelle, Ueberdeckung, Pruefstatus, Alternativen), bisheriger Typ in
      HFW_Wandtyp.Typ_Erfassung; Pset_WallCommon FireRating/AcousticRating aus dem Katalog
    """
    import ifcopenshell.api.pset as apset
    import ifcopenshell.guid
    import ifcopenshell.util.element as ue
    from wandgeometrie import Wand

    prot = []
    for el in f.by_type("IfcWall"):
        w = Wand(el)
        if w.st.Name != geschoss or w.kind != "GK":
            continue
        poly = w.polygon().buffer(0.02)
        typ, anteil, alle = wand_typ(poly, typflaechen, w.L)
        grenze = min_anteil_iw if typ == "IW" else min_anteil
        if not typ or anteil < grenze:
            prot.append({"wand": el.Name, "typ": None, "anteil": round(anteil, 2)})
            continue
        zweit = sorted(alle.values(), reverse=True)[1] if len(alle) > 1 else 0
        nah = sorted({t for t, p in labels if p.distance(poly) < 0.8})
        status = "automatisch zugeordnet"
        hinweis = []
        if zweit >= 0.6 * anteil:
            status = "prüfen"
            hinweis.append("mehrere Typfarben: " + ", ".join(f"{t} {v:.0%}" for t, v in sorted(alle.items(), key=lambda x: -x[1])))
        if nah and typ not in nah:
            hinweis.append("Planbeschriftung in der Nähe: " + ", ".join(nah))
        typ_setzen(f, el, typ, katalog, {"Quelle": quelle, "Ueberdeckung": round(anteil, 2), "Pruefstatus": status,
                                          "Hinweis": "; ".join(hinweis) or None})
        prot.append({"wand": el.Name, "typ": typ, "anteil": round(anteil, 2), "status": status, "hinweis": "; ".join(hinweis)})
    return prot


def _wandtyp_entity(f, typ, katalog):
    import ifcopenshell.api.pset as apset
    import ifcopenshell.guid
    k = katalog.get(typ, {})
    name = k.get("legende") or k.get("titel") or typ
    for t in f.by_type("IfcWallType"):
        if t.Name == name:
            return t
    t = f.createIfcWallType(ifcopenshell.guid.new(), f.by_type("IfcOwnerHistory")[0], name, k.get("titel"), None, None, None,
                            typ, None, "PARTITIONING" if typ.startswith("TW") else "NOTDEFINED")
    kw = k.get("kennwerte", {})
    props = {"Wandtyp": typ, "Bezeichnung": name, "Art": {"TW": "Trockenbau-Trennwand", "VW": "Vorsatzwand/Schachtwand/Bekleidung",
                                                            "IW": "Installationswand"}.get(k.get("art", typ[:2]), ""),
             "Farbe_Plan": k.get("farbe"), "Staenderwerk": k.get("staenderwerk"),
             "Brandschutz": kw.get("brandschutz"), "Schallschutz": kw.get("schallschutz"),
             "Max_Raumhoehe": kw.get("max_raumhoehe"), "Wanddicke": kw.get("wanddicke"), "Daemmung": kw.get("daemmung"),
             "Schichten": " | ".join(k.get("schichten", [])) or None, "Detailblatt": k.get("quelle")}
    apset.edit_pset(f, pset=apset.add_pset(f, product=t, name="HFW_Wandtyp_Detail"), properties={a: b for a, b in props.items() if b})
    return t


def typ_setzen(f, el, typ, katalog, props):
    """Wand el dem Plan-Wandtyp typ zuordnen (IfcWallType + Pset HFW_Wandtyp + Brand-/Schallschutz aus Katalog)."""
    import ifcopenshell.api.pset as apset
    import ifcopenshell.guid
    import ifcopenshell.util.element as ue
    alt_typ = ue.get_type(el)
    nt = _wandtyp_entity(f, typ, katalog)
    for rel in list(el.IsTypedBy):
        ro = [o for o in rel.RelatedObjects if o != el]
        if ro:
            rel.RelatedObjects = ro
        else:
            f.remove(rel)
    rel = next((r for r in f.by_type("IfcRelDefinesByType") if r.RelatingType == nt), None)
    if rel:
        rel.RelatedObjects = list(rel.RelatedObjects) + [el]
    else:
        f.createIfcRelDefinesByType(ifcopenshell.guid.new(), f.by_type("IfcOwnerHistory")[0], None, None, [el], nt)
    props = {"Wandtyp": typ, "Typ_Erfassung": (ue.get_psets(el).get("HFW_Wandtyp", {}).get("Typ_Erfassung")
                                               or (alt_typ.Name if alt_typ else None)), **props}
    p = ue.get_psets(el).get("HFW_Wandtyp")
    pset = f.by_id(p["id"]) if p else apset.add_pset(f, product=el, name="HFW_Wandtyp")
    apset.edit_pset(f, pset=pset, properties={a: b for a, b in props.items() if b is not None})
    kw = katalog.get(typ, {}).get("kennwerte", {})
    common = {}
    if re.match(r"F[1-9]\d+", kw.get("brandschutz", "")):
        alt_fr = ue.get_psets(el).get("Pset_WallCommon", {}).get("FireRating")
        if alt_fr and alt_fr != kw["brandschutz"]:
            apset.edit_pset(f, pset=pset, properties={"FireRating_Erfassung": alt_fr})
        common["FireRating"] = kw["brandschutz"]
    if kw.get("schallschutz", "").startswith("RW"):
        common["AcousticRating"] = kw["schallschutz"].replace("  ", " ")
    if common:
        pc = ue.get_psets(el).get("Pset_WallCommon")
        apset.edit_pset(f, pset=f.by_id(pc["id"]) if pc else apset.add_pset(f, product=el, name="Pset_WallCommon"), properties=common)
