"""Höhen aus Raumstempeln (OK FFB, OK RFB, UK RFB, UK RD, UK FD, FB) auf Räume, Trockenbauwände und Bodenplatte.

Raumlage: Die Allplan-Grundrisse führen die Raumstempel als Tabelle außerhalb der Räume. Die Lage der Räume
kommt deshalb aus einem Plan mit Raumnummern im Raum (z. B. Trockenbau-Übersicht UE_02).

Regeln (DIN 18340: Wandhöhe von OK Rohfußboden bis UK Rohdecke):
  UK Wand = niedrigste OK RFB der angrenzenden Räume (bei Gefälle der tiefste Wert)
  OK Wand = UK RD der angrenzenden Räume; bei 'x bis y' (veränderliche Rohdecke) gilt der feste Wert der
            Nachbarseite, sonst der größte Wert (Prüfhinweis). Über OK RFB des nächsten Geschosses wird später
            geteilt (modell_bereinigen.geschosse_teilen).
  Bodenplatte: Oberkante = OK RFB, Unterkante = UK RFB je Raumzone; Zonen = Voronoi-Zellen der Raumnummern,
            begrenzt durch den Gebäudeumriss (Außenkante Außenwände).
"""
import json, re, sys
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.api.root as aroot
import ifcopenshell.api.pset as apset
import ifcopenshell.api.spatial as aspatial
import ifcopenshell.api.geometry as ageo
import ifcopenshell.api.material as amat
import ifcopenshell.guid
import ifcopenshell.util.element as ue
from shapely.geometry import Point, Polygon, box, MultiPoint
from shapely.ops import unary_union, voronoi_diagram

sys.path.insert(0, str(Path(__file__).parent))
from planraster import oeffnen
from wandgeometrie import Wand, setze_wand


def wert(v, art="max"):
    if isinstance(v, dict):
        return v[art]
    return v


def raumlagen(pdf, grid, praefix):
    d, p, r = oeffnen(pdf, grid)
    out = {}
    for w in p.get_text("words"):
        if re.fullmatch(re.escape(praefix) + r"[\w.]+", w[4]):
            x, y = r.m((w[0] + w[2]) / 2, (w[1] + w[3]) / 2)
            out[w[4]] = (float(x), float(y))
    return out


class Raumsuche:
    """Punkt -> Raumnummer: Region zwischen Wänden, darin nächstgelegene Raumnummer."""

    def __init__(self, lagen, walls, umriss):
        self.lagen = lagen
        u = unary_union([w.polygon().buffer(0.01, join_style=2) for w in walls])
        R = umriss.difference(u)
        self.regionen = list(R.geoms) if hasattr(R, "geoms") else [R]
        self.inhalt = [[n for n, p in lagen.items() if g.contains(Point(p))] for g in self.regionen]

    def raum(self, p, max_dist=8.0):
        pt = Point(p)
        for g, nn in zip(self.regionen, self.inhalt):
            if g.contains(pt):
                kand = nn
                break
        else:
            return None, "außerhalb"
        if not kand:
            return None, "Region ohne Raumnummer"
        n = min(kand, key=lambda n: pt.distance(Point(self.lagen[n])))
        d = pt.distance(Point(self.lagen[n]))
        if d > max_dist and len(kand) > 1:
            return n, f"nächste Raumnummer {d:.1f} m entfernt"
        return n, "eindeutig" if len(kand) == 1 else "nächste Raumnummer in Region"


def umriss_aus_waenden(walls, bereich):
    """Rechteckiger Gebäudeumriss = Außenkante der Wände im Bereich (xmin, ymin, xmax, ymax)."""
    B = box(*bereich)
    ws = [w.polygon() for w in walls if B.contains(w.polygon().centroid)]
    return box(*unary_union(ws).bounds)


def raumstempel_in_ifc(f, stempel):
    """HFW_Raumstempel der IfcSpaces mit den Planwerten aktualisieren (Bereiche als *_min/*_max + Text)."""
    n = 0
    for sp in f.by_type("IfcSpace"):
        s = stempel.get(sp.Name)
        if not s:
            continue
        props = {}
        for k in ("OK_FFB", "OK_RFB", "UK_RFB", "UK_RD", "UK_FD"):
            v = s.get(k)
            if isinstance(v, dict):
                props[k] = v["max"]
                props[k + "_min"] = v["min"]
                props[k + "_Text"] = v["text"]
            elif v is not None:
                props[k] = v
        if s.get("FB"):
            props["Fussbodentyp"] = str(s["FB"])
        ffb, rfb = wert(s.get("OK_FFB"), "min"), wert(s.get("OK_RFB"), "min")
        if ffb is not None and rfb is not None:
            props["Fussbodenaufbau_m"] = round(ffb - rfb, 3)
        if rfb is not None and wert(s.get("UK_RFB"), "min") is not None:
            props["Bodenplatte_Decke_d_m"] = round(wert(s.get("OK_RFB"), "max") - wert(s.get("UK_RFB"), "min"), 3)
        p = ue.get_psets(sp).get("HFW_Raumstempel")
        pset = f.by_id(p["id"]) if p else apset.add_pset(f, product=sp, name="HFW_Raumstempel")
        apset.edit_pset(f, pset=pset, properties=props)
        n += 1
    return n


def wandhoehen(f, stempel, lagen, geschoss, umriss, ok_rfb_oben, nur_gk=True):
    """Trockenbauwände eines Geschosses auf OK RFB .. UK RD der angrenzenden Räume setzen."""
    W = [Wand(el) for el in f.by_type("IfcWall")]
    W = [w for w in W if w.st.Name == geschoss]
    such = Raumsuche(lagen, W, umriss)
    prot = []
    for w in W:
        if nur_gk and w.kind != "GK":
            continue
        e = ue.get_psets(w.el).get("HFW_Erfassung", {})
        if str(e.get("Geschossteilung", "")).startswith("Teil 2/2"):
            continue
        seiten = []
        for sgn in (1, -1):
            treffer = {}
            for t in (0.25, 0.5, 0.75):
                n, g = such.raum(w.p0 + w.x * w.L * t + w.n * sgn * (w.t / 2 + 0.15))
                if n and n in stempel:
                    treffer[n] = g
            seiten.append(treffer)
        raeume = [n for s in seiten for n in s]
        st = [stempel[n] for n in dict.fromkeys(raeume)]
        rfb = [wert(s.get("OK_RFB"), "min") for s in st if s.get("OK_RFB") is not None]
        fest = [s["UK_RD"] for s in st if isinstance(s.get("UK_RD"), (int, float))]
        var = [s["UK_RD"] for s in st if isinstance(s.get("UK_RD"), dict)]
        if not rfb or not (fest or var):
            prot.append({"wand": w.name(), "raeume": raeume, "status": "kein Raumstempel – Höhe unverändert",
                         "z0": round(w.z0, 3), "z1": round(w.z0 + w.H, 3)})
            continue
        z0 = min(rfb)
        z1 = max(fest) if fest else max(v["max"] for v in var)
        hinweis = []
        if len(set(fest)) > 1:
            hinweis.append("angrenzende Räume mit unterschiedlicher UK RD (" + ", ".join(f"{v:.2f}".replace(".", ",") for v in sorted(set(fest))) + "), höchster Wert verwendet – prüfen")
        if var:
            hinweis.append("UK RD veränderlich (" + "; ".join(v["text"] for v in var) + ")" +
                           (", fester Wert der Nachbarseite verwendet" if fest else ", größter Wert verwendet"))
        if any(isinstance(s.get("OK_RFB"), dict) for s in st):
            hinweis.append("OK RFB mit Gefälle, tiefster Wert verwendet")
        alt = (round(w.z0, 3), round(w.z0 + w.H, 3))
        if abs(z0 - w.z0) > 0.004 or abs(z1 - (w.z0 + w.H)) > 0.004:
            setze_wand(f, w, H=z1 - z0, z0=z0)
        props = {"UK_Wand": round(z0, 3), "OK_Wand": round(z1, 3),
                 "Hoehe_Herkunft": "Raumstempel " + ", ".join(dict.fromkeys(raeume)) + " (OK RFB bis UK RD)",
                 "Raum_Seite_A": ", ".join(seiten[0]) or None, "Raum_Seite_B": ", ".join(seiten[1]) or None,
                 "Hoehe_Hinweis": "; ".join(hinweis) or None}
        ffb = [wert(s.get("OK_FFB"), "min") for s in st if s.get("OK_FFB") is not None]
        if ffb:
            props["OK_FFB"] = min(ffb)
        p = ue.get_psets(w.el)["HFW_Erfassung"]
        apset.edit_pset(f, pset=f.by_id(p["id"]), properties={k: v for k, v in props.items() if v is not None})
        prot.append({"wand": w.name(), "raeume": raeume, "alt": alt, "neu": (round(z0, 3), round(z1, 3)),
                     "status": "geändert" if alt != (round(z0, 3), round(z1, 3)) else "bestätigt",
                     "hinweis": "; ".join(hinweis)})
    return prot


def bodenplatte(f, stempel, lagen, umriss, geschoss):
    """Stahlbeton-Bodenplatte als IfcSlab (BASESLAB) je Zone gleicher OK RFB/UK RFB."""
    st_el = next(s for s in f.by_type("IfcBuildingStorey") if s.Name == geschoss)
    pts = {n: p for n, p in lagen.items() if n in stempel and stempel[n].get("OK_RFB") is not None
           and stempel[n].get("UK_RFB") is not None and umriss.contains(Point(p))}
    fehler = []
    # unplausible Stempel vorab entfernen, damit ihre Fläche den Nachbarzonen zufällt
    def _ok(s):
        o = s["OK_RFB"]
        o = (o["min"] if o["max"] - o["min"] > 0.3 else o["max"]) if isinstance(o, dict) else o
        return 0.15 <= o - wert(s["UK_RFB"], "min") <= 1.0
    for n in [n for n in pts if not _ok(stempel[n])]:
        fehler.append({"raum": n, "OK_RFB": stempel[n]["OK_RFB"], "UK_RFB": stempel[n]["UK_RFB"],
                       "grund": "Plattendicke aus Stempel unplausibel – Fläche den Nachbarzonen zugeschlagen"})
        del pts[n]
    vor = voronoi_diagram(MultiPoint([Point(p) for p in pts.values()]), envelope=umriss.buffer(50))
    zonen = {}
    for zelle in vor.geoms:
        n = next(n for n, p in pts.items() if zelle.contains(Point(p)))
        s = stempel[n]
        ok_r = s["OK_RFB"]
        # Gefälle (kleine Spanne): höchster Punkt ist OK Platte; Grube/Versprung (> 30 cm): tiefster Wert
        ok = (ok_r["min"] if ok_r["max"] - ok_r["min"] > 0.3 else ok_r["max"]) if isinstance(ok_r, dict) else ok_r
        uk = wert(s["UK_RFB"], "min")
        if not (0.15 <= ok - uk <= 1.0):
            fehler.append({"raum": n, "OK_RFB": s["OK_RFB"], "UK_RFB": s["UK_RFB"], "grund": "Plattendicke unplausibel – Zone der Nachbarräume"})
            continue
        zonen.setdefault((ok, uk), []).append((zelle.intersection(umriss), n))
    ctx = next(c for c in f.by_type("IfcGeometricRepresentationSubContext") if c.ContextIdentifier == "Body")
    beton = next((m for m in f.by_type("IfcMaterial") if m.Name == "Stahlbeton"), None) or f.createIfcMaterial("Stahlbeton")
    st_z = st_el.ObjectPlacement.RelativePlacement.Location.Coordinates[2]
    prot = []
    for i, ((ok, uk), teile) in enumerate(sorted(zonen.items(), key=lambda z: -sum(t[0].area for t in z[1])), 1):
        g = unary_union([t[0] for t in teile]).buffer(0.001, join_style=2).buffer(-0.001, join_style=2)
        geoms = list(g.geoms) if hasattr(g, "geoms") else [g]
        d = round(ok - uk, 3)
        for k, poly in enumerate(geoms, 1):
            if poly.area < 0.5:
                continue
            slab = aroot.create_entity(f, ifc_class="IfcSlab", predefined_type="BASESLAB",
                                       name=f"BP-{geschoss}-{i:02d}" + (f".{k}" if len(geoms) > 1 else ""))
            slab.Description = f"Stahlbeton-Bodenplatte d={d*100:.0f} cm (OK RFB {ok:+.3f} / UK RFB {uk:+.3f})".replace(".", ",")
            slab.ObjectType = "Bodenplatte"
            aspatial.assign_container(f, products=[slab], relating_structure=st_el)
            slab.ObjectPlacement = f.createIfcLocalPlacement(st_el.ObjectPlacement, f.createIfcAxis2Placement3D(
                f.createIfcCartesianPoint((0.0, 0.0, float(uk - st_z))), None, None))
            ring = [f.createIfcCartesianPoint((float(x), float(y))) for x, y in list(poly.exterior.coords)]
            prof = f.createIfcArbitraryClosedProfileDef("AREA", None, f.createIfcPolyline(ring))
            solid = f.createIfcExtrudedAreaSolid(prof, f.createIfcAxis2Placement3D(f.createIfcCartesianPoint((0.0, 0.0, 0.0)), None, None),
                                                 f.createIfcDirection((0.0, 0.0, 1.0)), float(d))
            rep = f.createIfcShapeRepresentation(ctx, "Body", "SweptSolid", [solid])
            slab.Representation = f.createIfcProductDefinitionShape(None, None, [rep])
            f.createIfcRelAssociatesMaterial(ifcopenshell.guid.new(), None, None, None, [slab], beton)
            raeume = sorted(t[1] for t in teile if t[0].intersects(poly))
            apset.edit_pset(f, pset=apset.add_pset(f, product=slab, name="Pset_SlabCommon"),
                            properties={"IsExternal": False, "LoadBearing": True, "Reference": f"Bodenplatte d={d*100:.0f} cm"})
            apset.edit_pset(f, pset=apset.add_pset(f, product=slab, name="HFW_Erfassung"), properties={
                "Quelle": "Raumstempel Grundriss EG (OK RFB / UK RFB), Raumlage aus UE_02",
                "Erfassung": "automatisch, Zonen nach Raumstempel", "Pruefstatus": "ungeprueft",
                "OK_RFB": ok, "UK_RFB": uk, "Dicke": d, "Raeume": ", ".join(raeume)[:250],
                "Hinweis": "Umriss = Außenkante Außenwände (Rechteck); Zonengrenzen angenähert (Voronoi der Raumnummern); "
                           "Gruben, Rinnen, Fundamente und Gefälle nicht modelliert"})
            apset.edit_qto(f, qto=apset.add_qto(f, product=slab, name="Qto_SlabBaseQuantities"), properties={
                "Width": d, "GrossArea": round(poly.area, 3), "NetArea": round(poly.area, 3),
                "GrossVolume": round(poly.area * d, 3), "NetVolume": round(poly.area * d, 3), "Perimeter": round(poly.length, 3)})
            prot.append({"name": slab.Name, "ok_rfb": ok, "uk_rfb": uk, "d": d, "flaeche_m2": round(poly.area, 2), "raeume": raeume})
    return prot, fehler
