"""Wandverstärkungen, Profilverstärkungen und Unterkonstruktions-Anforderungen aus einem Verstärkungsplan
(z. B. HFW_F_5_039.1_00_UE_12 'FWT EG Trockenbauverstärkung') in das Modell übernehmen.

Die Legende wird in projekt.json -> planabgleich.verstaerkungen beschrieben (Farbe, Strichart, Höhen aus den
Ansichtspiktogrammen des Plans). Ergebnis:
  - Linien  -> IfcMember (PLATE/MEMBER) als dünner Körper auf der zugehörigen Wandseite, Höhe laut Piktogramm
  - Profile -> IfcMember (POST) Stahlhohlprofil, Höhe = Wandhöhe der zugehörigen Wand
  - Flächen -> Wandattribut (z. B. Korrosivitätskategorie C3 der Unterkonstruktion)
  - je Wand: Pset HFW_Verstaerkung mit Zusammenfassung (für das Aufmaß)
Verknüpfung Wand <-> Verstärkung über IfcRelConnectsElements.
"""
import collections, math, sys
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.api.root as aroot
import ifcopenshell.api.pset as apset
import ifcopenshell.api.spatial as aspatial
import ifcopenshell.guid
import ifcopenshell.util.element as ue
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).parent))
from planraster import oeffnen
from wandgeometrie import Wand


def _gleich(a, b, tol=0.02):
    return a is not None and max(abs(x - y) for x, y in zip(a, b)) < tol


def _box(f, ctx, laenge, breite, hoehe, mitte=True):
    """Quader: x = Länge, y = Breite (um 0 zentriert), z = Höhe; x ab 0 (mitte=False) oder zentriert."""
    pos = f.createIfcAxis2Placement2D(f.createIfcCartesianPoint((0.0 if mitte else laenge / 2, 0.0)), None)
    prof = f.createIfcRectangleProfileDef("AREA", None, pos, float(laenge), float(breite))
    solid = f.createIfcExtrudedAreaSolid(prof, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None),
                                         f.createIfcDirection((0.0, 0.0, 1.0)), float(hoehe))
    return f.createIfcProductDefinitionShape(None, None, [f.createIfcShapeRepresentation(ctx, "Body", "SweptSolid", [solid])])


def lesen(f, projekt_dir, cfg, grid):
    proj = Path(projekt_dir)
    d, page, raster = oeffnen(proj / "plaene" / cfg["plan"], grid)
    import fitz
    planbox = fitz.Rect(*cfg["planbereich"])
    linien, flaechen, profile = [], collections.defaultdict(list), []
    for dd in page.get_drawings():
        if not planbox.contains(dd["rect"]):
            continue
        col = dd.get("color")
        if col and dd["type"] == "s":
            for leg in cfg.get("linien", []):
                if _gleich(col, leg["farbe"]) and abs((dd.get("width") or 0) - leg.get("breite", dd.get("width") or 0)) < 0.2:
                    for it in dd["items"]:
                        if it[0] != "l":
                            continue
                        a, b = raster.m(it[1].x, it[1].y), raster.m(it[2].x, it[2].y)
                        L = math.dist(a, b)
                        if leg.get("min_m", 0) <= L <= leg.get("max_m", 1e9):
                            linien.append((leg, LineString([a, b])))
                    break
        fill = dd.get("fill")
        if fill:
            r = dd["rect"]
            for leg in cfg.get("profile", []):
                if _gleich(fill, leg["farbe"]) and r.width < 30 and r.height < 30:
                    profile.append((leg, Point(raster.m((r.x0 + r.x1) / 2, (r.y0 + r.y1) / 2))))
                    break
            else:
                for leg in cfg.get("flaechen", []):
                    if _gleich(fill, leg["farbe"]):
                        x0, y0 = raster.m(r.x0, r.y0)
                        x1, y1 = raster.m(r.x1, r.y1)
                        flaechen[leg["art"]].append(Polygon([(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))
                        break
    # Profile: gleiche Position mehrfach gezeichnet -> zusammenfassen
    eindeutig = []
    for leg, p in profile:
        if not any(l2 is leg and p.distance(p2) < 0.05 for l2, p2 in eindeutig):
            eindeutig.append((leg, p))
    return linien, {k: unary_union(v) for k, v in flaechen.items()}, eindeutig


def einarbeiten(f, projekt_dir, cfg, grid):
    linien, flaechen, profile = lesen(f, projekt_dir, cfg, grid)
    gs = cfg["geschoss"]
    st_el = next(s for s in f.by_type("IfcBuildingStorey") if s.Name == gs)
    st_z = float(st_el.ObjectPlacement.RelativePlacement.Location.Coordinates[2])
    ctx = next(c for c in f.by_type("IfcGeometricRepresentationSubContext") if c.ContextIdentifier == "Body")
    W = [w for w in (Wand(e) for e in f.by_type("IfcWall")) if w.st.Name == gs]
    # vorhandene Verstärkungen dieses Plans ersetzen (GUID je Name beibehalten)
    alt = {}
    for m in f.by_type("IfcMember"):
        if (ue.get_psets(m).get("HFW_Verstaerkung", {}).get("Quelle") == cfg["plan"]):
            alt[m.Name] = m.GlobalId
            aroot.remove_product(f, product=m)
    zaehler = collections.Counter()
    je_wand = collections.defaultdict(lambda: collections.defaultdict(float))
    prot = []

    def neues_member(name, art, pt, wand, props, shape, placement):
        m = aroot.create_entity(f, ifc_class="IfcMember", predefined_type=pt, name=name)
        if name in alt:
            m.GlobalId = alt[name]
        m.ObjectType = art
        aspatial.assign_container(f, products=[m], relating_structure=st_el)
        m.ObjectPlacement = placement
        m.Representation = shape
        apset.edit_pset(f, pset=apset.add_pset(f, product=m, name="HFW_Verstaerkung"),
                        properties={k: v for k, v in props.items() if v is not None})
        if wand is not None:
            f.createIfcRelConnectsElements(ifcopenshell.guid.new(), None, "Verstärkung", None, None, wand.el, m)
        return m

    def platzierung(x, y, z, richtung):
        return f.createIfcLocalPlacement(st_el.ObjectPlacement, f.createIfcAxis2Placement3D(
            f.createIfcCartesianPoint((float(x), float(y), float(z - st_z))), f.createIfcDirection((0.0, 0.0, 1.0)),
            f.createIfcDirection((float(richtung[0]), float(richtung[1]), 0.0))))

    # --- Linien: Wand + Seite suchen, auf Wandlänge begrenzen
    bereits = []
    for leg, ls in linien:
        mitte = np.array(ls.interpolate(0.5, normalized=True).coords[0])
        r = np.array(ls.coords[1]) - np.array(ls.coords[0])
        r = r / (np.linalg.norm(r) or 1)
        best = None
        for w in W:
            if abs(float(np.dot(r, w.x))) < 0.95 or w.kind != "GK":
                continue
            s = float(np.dot(mitte - w.p0, w.n))
            u = float(np.dot(mitte - w.p0, w.x))
            if abs(s) > w.t / 2 + 0.35 or u < -0.2 or u > w.L + 0.2:
                continue
            if best is None or abs(s) < abs(best[1]):
                best = (w, s)
        if not best:
            prot.append({"art": leg["art"], "laenge_m": round(ls.length, 2), "wand": None, "hinweis": "keine Trockenbauwand in 35 cm"})
            continue
        w, s = best
        u0, u1 = sorted(float(np.dot(np.array(c) - w.p0, w.x)) for c in ls.coords)
        u0, u1 = max(0.0, u0), min(w.L, u1)
        if u1 - u0 < 0.1:
            continue
        seite = 1 if s >= 0 else -1
        # Doppellinien-Symbol (z. B. Feuerlöscher): zweite Linie auf derselben Wandseite nicht doppelt zählen
        key = (leg["art"], w.name(), seite)
        if any(k == key and min(u1, b) - max(u0, a) > 0.5 * (u1 - u0) for k, a, b in bereits):
            continue
        bereits.append((key, u0, u1))
        ffb = ue.get_psets(w.el).get("HFW_Erfassung", {}).get("OK_FFB", st_z)
        for i, (h0, h1) in enumerate(leg["hoehen"]):
            zaehler[gs] += 1
            name = f"VS-{gs}-{zaehler[gs]:03d}"
            p = w.p0 + w.x * u0 + w.n * seite * (w.t / 2 + 0.006)
            shape = _box(f, ctx, u1 - u0, 0.012, h1 - h0, mitte=False)
            props = {"Art": leg["art"], "Legende": leg.get("text"), "Wand": w.name(), "Seite": "A" if seite > 0 else "B",
                     "Laenge": round(u1 - u0, 3), "UK_ueber_FFB": h0, "OK_ueber_FFB": h1, "Flaeche": round((u1 - u0) * (h1 - h0), 3),
                     "Quelle": cfg["plan"], "Hoehe_Herkunft": "Ansichtspiktogramm Plan", "Pruefstatus": "ungeprueft"}
            neues_member(name, leg["art"], leg.get("ifc", "MEMBER"), w, props, shape, platzierung(p[0], p[1], ffb + h0, w.x))
            je_wand[w.el][leg["art"] + " [m²]"] += (u1 - u0) * (h1 - h0)
        je_wand[w.el][leg["art"] + " [m]"] += u1 - u0
        prot.append({"art": leg["art"], "wand": w.name(), "seite": "A" if seite > 0 else "B", "laenge_m": round(u1 - u0, 2)})
    # --- Profile: nächste Wand, Höhe = Wandhöhe
    for leg, pt in profile:
        best = min(W, key=lambda w: w.polygon().distance(pt)) if W else None
        dist = best.polygon().distance(pt) if best else 99
        wand = best if dist < 0.3 else None
        z0 = wand.z0 if wand else st_z - 0.18
        z1 = wand.z0 + wand.H if wand else st_z + 3.15
        zaehler[gs + "P"] += 1
        name = f"VP-{gs}-{zaehler[gs + 'P']:03d}"
        props = {"Art": leg["art"], "Legende": leg.get("text"), "Wand": wand.name() if wand else None,
                 "Profil": leg.get("profil"), "Laenge": round(z1 - z0, 3), "Quelle": cfg["plan"],
                 "Hoehe_Herkunft": "Wandhöhe (OK RFB bis UK RD)" if wand else "Geschossstandard", "Pruefstatus": "ungeprueft"}
        richtung = wand.x if wand else (1.0, 0.0)
        shape = _box(f, ctx, leg.get("a", 0.075), leg.get("b", 0.075), z1 - z0)
        neues_member(name, leg["art"], "POST", wand, props, shape, platzierung(pt.x, pt.y, z0, richtung))
        if wand:
            je_wand[wand.el][leg["art"] + " [Stk]"] += 1
        prot.append({"art": leg["art"], "wand": wand.name() if wand else None, "x": round(pt.x, 2), "y": round(pt.y, 2)})
    # --- Flächen: Wandattribute
    for leg in cfg.get("flaechen", []):
        g = flaechen.get(leg["art"])
        if g is None:
            continue
        for w in W:
            if w.kind == "GK" and w.polygon().intersection(g).area > 0.25 * w.polygon().area:
                for k, v in leg["wandattribut"].items():
                    je_wand[w.el][k] = v
                prot.append({"art": leg["art"], "wand": w.name()})
    for el, werte in je_wand.items():
        props = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in werte.items()}
        props["Quelle"] = cfg["plan"]
        p = ue.get_psets(el).get("HFW_Verstaerkung")
        apset.edit_pset(f, pset=f.by_id(p["id"]) if p else apset.add_pset(f, product=el, name="HFW_Verstaerkung"), properties=props)
    return prot
