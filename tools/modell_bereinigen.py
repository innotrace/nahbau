"""Modellbereinigung für das Aufmaß.

Schritte (Reihenfolge wichtig):
  1. duplikate_entfernen   – geometrisch identische Bauteile mit gleicher GUID nur einmal behalten
     enthaltene_entfernen  – Wandstücke, die vollständig in einer gleichartigen Wand liegen
  2. geschosse_teilen      – Wände über die Geschossgrenze (OK RFB des nächsten Geschosses) teilen
  3. wandanschluesse       – offene Enden von Trockenbauwänden an Nachbarwände anschließen

Regeln Wandanschluss (DIN 18340: Länge bis zu den begrenzenden Bauteilen):
  - T-Stoß: offenes Ende wird bis zur Fläche der getroffenen Wand verlängert (max. MAX_LUECKE).
  - L-Ecke GK/GK: die längere Wand läuft bis zur Außenkante durch, die kürzere stößt stumpf an.
  - L-Ecke GK/Massiv: die GK-Wand läuft bis zur Außenkante der Massivwand. Massivwände werden nie verändert.
  - Lücke in gleicher Flucht (Durchgang?) wird nicht geschlossen, sondern als Prüfhinweis gemeldet.
Jede Änderung wird im Pset HFW_Erfassung (Anschluss_Anfang / Anschluss_Ende) dokumentiert und als
IfcRelConnectsPathElements abgelegt.
"""
import json, sys
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.api.root as aroot
import ifcopenshell.api.pset as apset
import ifcopenshell.api.spatial as aspatial
import ifcopenshell.guid
import ifcopenshell.util.element as ue
import ifcopenshell.util.placement as up
from shapely.geometry import LineString, Point, Polygon
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).parent))
from wandgeometrie import Wand, setze_wand, mengen

MAX_LUECKE = 0.60   # m, größere Lücken werden nur gemeldet
TOL = 0.01          # m, Ende gilt als angeschlossen
UEBERSTAND = 0.10   # m, überstehende GK-Enden an L-Ecken werden bis zu diesem Maß gekürzt
cm = lambda v: f"{v*100:.1f}".replace(".", ",") + " cm"


def log_pset(f, el, **props):
    p = ue.get_psets(el).get("HFW_Erfassung")
    pset = f.by_id(p["id"]) if p else apset.add_pset(f, product=el, name="HFW_Erfassung")
    apset.edit_pset(f, pset=pset, properties=props)


# ------------------------------------------------------------------ 1. Duplikate
def duplikate_entfernen(f):
    seen, weg = {}, []
    for el in f.by_type("IfcElement"):
        if el.GlobalId in seen:
            a = seen[el.GlobalId]
            Ma, Mb = up.get_local_placement(a.ObjectPlacement), up.get_local_placement(el.ObjectPlacement)
            if np.allclose(Ma, Mb, atol=1e-4) and a.Name != el.Name:
                weg.append((el.Name, a.Name, el.GlobalId))
                for rv in getattr(el, "HasOpenings", []):
                    aroot.remove_product(f, product=rv.RelatedOpeningElement)
                aroot.remove_product(f, product=el)
            else:
                el.GlobalId = ifcopenshell.guid.new()
        else:
            seen[el.GlobalId] = el
    # doppelte GUIDs in Psets/Relationen neu vergeben
    ids = set()
    for r in f.by_type("IfcRoot"):
        if r.GlobalId in ids:
            r.GlobalId = ifcopenshell.guid.new()
        ids.add(r.GlobalId)
    return [{"entfernt": a, "identisch_mit": b, "guid": g} for a, b, g in weg]


def enthaltene_entfernen(f):
    """Wand, deren Grundriss zu >= 95 % in einer längeren Wand gleicher Art und gleicher Dicke liegt,
    ist ein Erfassungsrest (Doppelzählung im Aufmaß) und wird entfernt. Öffnungen gehen auf die verbleibende Wand über."""
    weg = []
    W = [Wand(el) for el in f.by_type("IfcWall")]
    polys = [w.polygon() for w in W]
    tree = STRtree(polys)
    tot = set()
    for i, w in enumerate(W):
        for j in tree.query(polys[i]):
            v = W[j]
            if j == i or j in tot or i in tot or v.st != w.st or v.kind != w.kind or abs(v.t - w.t) > 0.01 or v.L < w.L:
                continue
            if v.L == w.L and j > i:
                continue
            if polys[i].intersection(polys[j]).area >= 0.95 * polys[i].area:
                if w.el.HasOpenings:
                    continue  # Öffnungen vorhanden: nicht automatisch entscheiden
                weg.append({"entfernt": w.name(), "enthalten_in": v.name(), "guid": w.el.GlobalId})
                tot.add(i)
                aroot.remove_product(f, product=w.el)
                break
    return weg


# ------------------------------------------------------------------ 2. Geschossteilung
def geschosse_teilen(f, geschosse):
    """geschosse: [{'name','elevation','ok_rfb'}] aufsteigend. Teilt Wände an ok_rfb des darüberliegenden Geschosses."""
    storeys = {s.Name: s for s in f.by_type("IfcBuildingStorey")}
    protokoll = []
    nummer = {g["name"]: max([int(w.Name.rsplit("-", 1)[1]) for w in f.by_type("IfcWall")
                               if w.Name.startswith(f"W-{g['name']}-") and w.Name.rsplit("-", 1)[1].isdigit()] or [0])
              for g in geschosse}
    for i, g in enumerate(geschosse[:-1]):
        oben = geschosse[i + 1]
        z_split = oben["ok_rfb"]
        for el in [w for w in f.by_type("IfcWall") if ue.get_container(w) == storeys[g["name"]]]:
            w = Wand(el)
            top = w.z0 + w.H
            if top <= z_split + 0.01:
                continue
            # obere Wand anlegen
            nummer[oben["name"]] += 1
            neu_name = f"W-{oben['name']}-{nummer[oben['name']]:03d}"
            cp = aroot.copy_class(f, product=el)
            for rv in list(cp.HasOpenings):  # kopierte (leere) Öffnungen verwerfen
                aroot.remove_product(f, product=rv.RelatedOpeningElement)
            st_o = storeys[oben["name"]]
            st_z = up.get_local_placement(st_o.ObjectPlacement)[2, 3]
            loc = (float(w.o[0]), float(w.o[1]), float(z_split - st_z))
            cp.ObjectPlacement = f.createIfcLocalPlacement(st_o.ObjectPlacement, f.createIfcAxis2Placement3D(
                f.createIfcCartesianPoint(loc), f.createIfcDirection((0., 0., 1.)),
                f.createIfcDirection((float(w.x[0]), float(w.x[1]), 0.))))
            cp.Representation = ifcopenshell.util.element.copy_deep(f, el.Representation)
            cp.Name = cp.Tag = neu_name
            aspatial.assign_container(f, products=[cp], relating_structure=st_o)
            # Öffnungen nach Lage der Mitte zuordnen
            for rv in list(el.HasOpenings):
                o = rv.RelatedOpeningElement
                oz = w.z0 + o.ObjectPlacement.RelativePlacement.Location.Coordinates[2]
                hoehe = o.Representation.Representations[0].Items[0].Depth
                if oz + hoehe / 2 > z_split:
                    rv.RelatingBuildingElement = cp
                    for ob in [o] + [rf.RelatedBuildingElement for rf in o.HasFillings]:
                        ob.ObjectPlacement.PlacementRelTo = cp.ObjectPlacement
                        c = list(ob.ObjectPlacement.RelativePlacement.Location.Coordinates)
                        c[2] -= (z_split - w.z0)
                        ob.ObjectPlacement.RelativePlacement.Location = f.createIfcCartesianPoint(tuple(c))
                        if ob.is_a("IfcDoor") or ob.is_a("IfcWindow"):
                            aspatial.assign_container(f, products=[ob], relating_structure=st_o)
            wo = Wand(cp)
            setze_wand(f, wo, H=top - z_split)
            setze_wand(f, w, H=z_split - w.z0)
            txt = f"geteilt an OK RFB {oben['name']} {z_split:+.2f} m".replace(".", ",")
            log_pset(f, el, OK_Wand=round(z_split, 3), Geschossteilung=f"Teil 1/2 ({g['name']}), oberer Teil {neu_name}; {txt}")
            log_pset(f, cp, UK_Wand=round(z_split, 3), OK_Wand=round(top, 3),
                     Geschossteilung=f"Teil 2/2 ({oben['name']}), unterer Teil {el.Name}; {txt}")
            protokoll.append({"wand": el.Name, "neu": neu_name, "z_teilung": z_split, "OK": round(top, 3), "art": el.ObjectType})
    return protokoll


# ------------------------------------------------------------------ 3. Wandanschlüsse
def _winkel(a, b):
    return np.degrees(np.arccos(min(1.0, abs(float(np.dot(a, b))))))


def _angeschlossen(W, polys, tree, i, e, ohne=()):
    """Ende e der Wand i berührt eine querlaufende Wand (Endbereich in Wanddicke)
    oder wird von einer Wand in gleicher Flucht fortgesetzt."""
    w = W[i]
    p, d = w.ende(e)
    h = w.n * w.t / 2
    kopf = Polygon([p - d * 0.02 - h, p - h, p + h, p - d * 0.02 + h])
    for j in tree.query(kopf.buffer(0.05)):
        if j == i or j in ohne:
            continue
        if _winkel(W[j].x, w.x) >= 30:
            if polys[j].distance(kopf) < TOL:
                return True
        elif polys[j].distance(Point(p + d * 0.005)) < TOL:
            return True
    return False


def _verbinden(f, a, ia, b, ib, beschreibung):
    pos = {0: "ATSTART", 1: "ATEND", None: "ATPATH"}
    f.createIfcRelConnectsPathElements(ifcopenshell.guid.new(), None, None, beschreibung, None,
                                       a.el, b.el, (), (), pos[ia], pos[ib])


def wandanschluesse(f, aendern=True):
    walls_by_st = {}
    for el in f.by_type("IfcWall"):
        w = Wand(el)
        walls_by_st.setdefault(w.st.Name, []).append(w)
    protokoll = []
    for st, W in walls_by_st.items():
        geaendert = set()
        for _ in range(2):  # zweiter Durchlauf: Enden, die erst nach der ersten Verlängerung erreichbar sind
            polys = [w.polygon() for w in W]
            tree = STRtree(polys)
            for i, w in enumerate(W):
                if w.kind != "GK":
                    continue
                for e in (0, 1):
                    if (i, e) in geaendert:
                        continue
                    p, d = w.ende(e)
                    nah = [j for j in tree.query(Point(p).buffer(MAX_LUECKE + 0.5)) if j != i]
                    if _angeschlossen(W, polys, tree, i, e):
                        continue  # bereits angeschlossen
                    # (a) T-Stoß: Strahl in Wandrichtung
                    # Verlängerungskörper in Wanddicke (trifft auch seitlich versetzte Nachbarwände)
                    h = w.n * w.t / 2 * 0.98
                    ray = Polygon([p - h, p + d * MAX_LUECKE - h, p + d * MAX_LUECKE + h, p + h])
                    best = None
                    for j in nah:
                        if _winkel(W[j].x, w.x) < 30 or not polys[j].intersects(ray):
                            continue
                        hit = polys[j].intersection(ray)
                        s = min(float(np.dot(np.array(c) - p, d)) for c in hit.exterior.coords) if hit.geom_type == "Polygon" else Point(p).distance(hit)
                        if best is None or s < best[0]:
                            best = (s, j)
                    if best and best[0] > TOL:
                        s, j = best
                        if aendern:
                            setze_wand(f, w, ds=s if e == 0 else 0, de=s if e == 1 else 0)
                            log_pset(f, w.el, **{("Anschluss_Anfang" if e == 0 else "Anschluss_Ende"):
                                                 f"T-Stoß an {W[j].name()}, verlängert um {cm(s)}"})
                            _verbinden(f, w, e, W[j], None, "T-Stoß")
                        protokoll.append({"geschoss": st, "wand": w.name(), "ende": e, "art": "T-Stoß",
                                          "an": W[j].name(), "an_art": W[j].kind, "verlaengert_m": round(s, 3)})
                        geaendert.add((i, e)); polys[i] = w.polygon()
                        continue
                    # (b) L-Ecke: Achsen schneiden sich vor beiden Enden
                    done = False
                    for j in nah:
                        v = W[j]
                        if _winkel(v.x, w.x) < 60:
                            continue
                        for ev in (0, 1):
                            q, dq = v.ende(ev)
                            if _angeschlossen(W, polys, tree, j, ev, ohne=(i,)):
                                continue  # Partnerende ist schon anderweitig angeschlossen
                            A = np.array([d, -dq]).T
                            try:
                                sw, sv = np.linalg.solve(A, q - p)
                            except np.linalg.LinAlgError:
                                continue
                            if not (-v.t / 2 - UEBERSTAND <= sw <= MAX_LUECKE + v.t / 2 and -w.t / 2 - UEBERSTAND <= sv <= MAX_LUECKE + w.t / 2):
                                continue
                            X = p + d * sw
                            durch_w = v.kind != "GK" or w.L >= v.L
                            ext_w = sw + (v.t / 2 if durch_w else -v.t / 2)
                            ext_v = sv + (-w.t / 2 if durch_w else w.t / 2)
                            if ext_w > MAX_LUECKE or ext_w < -UEBERSTAND or abs(ext_w) < TOL and abs(ext_v) < TOL:
                                continue
                            if aendern:
                                setze_wand(f, w, ds=ext_w if e == 0 else 0, de=ext_w if e == 1 else 0)
                                log_pset(f, w.el, **{("Anschluss_Anfang" if e == 0 else "Anschluss_Ende"):
                                                     f"L-Ecke mit {v.name()}, {'durchlaufend' if durch_w else 'stumpf'}, {'verlängert' if ext_w > 0 else 'gekürzt'} um {cm(abs(ext_w))}"})
                                if v.kind == "GK" and -UEBERSTAND <= ext_v <= MAX_LUECKE and abs(ext_v) > TOL:
                                    setze_wand(f, v, ds=ext_v if ev == 0 else 0, de=ext_v if ev == 1 else 0)
                                    log_pset(f, v.el, **{("Anschluss_Anfang" if ev == 0 else "Anschluss_Ende"):
                                                         f"L-Ecke mit {w.name()}, {'stumpf' if durch_w else 'durchlaufend'}, {'verlängert' if ext_v > 0 else 'gekürzt'} um {cm(abs(ext_v))}"})
                                _verbinden(f, w, e, v, ev, "L-Ecke")
                            protokoll.append({"geschoss": st, "wand": w.name(), "ende": e, "art": "L-Ecke",
                                              "an": v.name(), "an_art": v.kind, "verlaengert_m": round(ext_w, 3),
                                              "partner_verlaengert_m": round(ext_v, 3) if v.kind == "GK" else 0})
                            geaendert.add((i, e)); geaendert.add((j, ev))
                            polys[i], polys[j] = w.polygon(), v.polygon()
                            done = True
                            break
                        if done:
                            break
    return protokoll, offene_enden(f)


def offene_enden(f):
    """GK-Wandenden ohne Anschluss (für den Prüfbericht)."""
    walls_by_st, offen = {}, []
    for el in f.by_type("IfcWall"):
        w = Wand(el)
        walls_by_st.setdefault(w.st.Name, []).append(w)
    for st, W in walls_by_st.items():
        polys = [w.polygon() for w in W]
        tree = STRtree(polys)
        for i, w in enumerate(W):
            if w.kind != "GK":
                continue
            for e in (0, 1):
                p, d = w.ende(e)
                nah = [j for j in tree.query(Point(p).buffer(2.0)) if j != i]
                if _angeschlossen(W, polys, tree, i, e):
                    continue
                dist = min([polys[j].distance(Point(p)) for j in nah], default=9.9)
                flucht = any(_winkel(W[j].x, w.x) < 10 and abs(float(np.dot(W[j].p0 - p, w.n))) < 0.05 and polys[j].distance(Point(p)) < 2.0 for j in nah)
                offen.append({"geschoss": st, "wand": w.name(), "guid": w.el.GlobalId, "ende": "Anfang" if e == 0 else "Ende",
                              "abstand_m": round(dist, 3), "x": round(float(p[0]), 2), "y": round(float(p[1]), 2),
                              "hinweis": "Lücke in gleicher Flucht – Durchgang/Öffnung prüfen" if flucht else
                                         ("frei endend (z. B. Raumteiler) – prüfen" if dist > MAX_LUECKE else "Anschluss prüfen")})
    return offen


def besitzer_vereinheitlichen(f):
    """Kopierte Bauteile bringen eigene IfcOwnerHistory/IfcApplication-Ketten mit (IFC-Regel IfcApplication.UR1/UR2).
    Alle auf die erste OwnerHistory der Datei umhängen und die Kopien löschen."""
    oh = f.by_type("IfcOwnerHistory")
    haupt = oh[0]
    for h in oh[1:]:
        if h.OwningUser == haupt.OwningUser and h.OwningApplication == haupt.OwningApplication:
            continue
        for inv in f.get_inverse(h):
            for k in range(len(inv)):
                if inv[k] == h:
                    inv[k] = haupt
        kette = [h.OwningApplication, h.OwningUser, h.LastModifyingUser, h.LastModifyingApplication]
        f.remove(h)
        for e in kette:
            if e is not None and e.id() and not f.get_inverse(e):
                unter = [e.ApplicationDeveloper] if e.is_a("IfcApplication") else [e.ThePerson, e.TheOrganization] if e.is_a("IfcPersonAndOrganization") else []
                f.remove(e)
                for u in unter:
                    if u is not None and not f.get_inverse(u):
                        f.remove(u)


def bereinigen(ifc_in, ifc_out, projekt_dir):
    P = json.loads((Path(projekt_dir) / "projekt.json").read_text())
    f = ifcopenshell.open(ifc_in)
    dup = duplikate_entfernen(f)
    enth = enthaltene_entfernen(f)
    from planabgleich import planabgleich
    abgl = planabgleich(f, projekt_dir)  # Höhen, Bodenplatte, Wandtypen aus Plänen (vor der Geschossteilung)
    split = geschosse_teilen(f, P["geschosse"])
    anschl, offen = wandanschluesse(f)
    besitzer_vereinheitlichen(f)
    f.write(ifc_out)
    return {"duplikate": dup, "enthaltene_wände": enth, **abgl, "geschossteilung": split, "anschluesse": anschl, "offene_enden": offen}


if __name__ == "__main__":
    proj, ifc_in, ifc_out, prot = sys.argv[1:5]
    r = bereinigen(ifc_in, ifc_out, proj)
    Path(prot).write_text(json.dumps(r, ensure_ascii=False, indent=1))
    print({k: len(v) for k, v in r.items()})
