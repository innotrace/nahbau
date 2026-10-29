"""IFC -> model.json für den HFW-Viewer (viewer/index.html).

Die IFC-Datei ist die einzige Datenquelle. model.json enthält Attribute, Psets,
Schichtaufbau, Aufmaßwerte (auf) und die triangulierte Geometrie als Base64-Binärblock.

Aufruf:  python tools/viewer_export.py <projektordner> <ifc> <version> [pruefbericht.json]
"""
import base64, json, sys, datetime
from pathlib import Path

import numpy as np
import ifcopenshell
import ifcopenshell.geom
import ifcopenshell.util.element as ue

KLASSEN = ("IfcWall", "IfcDoor", "IfcWindow", "IfcColumn", "IfcStairFlight", "IfcSpace", "IfcGrid")


def storey_of(el):
    s = ue.get_container(el)
    if s is None and el.Decomposes:  # IfcSpace hängt per IfcRelAggregates am Geschoss
        s = el.Decomposes[0].RelatingObject
    return s.Name if s else None


def psets(el):
    out = {}
    for name, vals in ue.get_psets(el).items():
        out[name] = {k: v for k, v in vals.items() if k != "id"}
    return out


def layers(el):
    for rel in el.HasAssociations:
        if not rel.is_a("IfcRelAssociatesMaterial"):
            continue
        m = rel.RelatingMaterial
        if m.is_a("IfcMaterialLayerSetUsage"):
            m = m.ForLayerSet
        if m.is_a("IfcMaterialLayerSet"):
            return [[l.Material.Name, round(l.LayerThickness * 1000, 1)] for l in m.MaterialLayers]
    return None


def openings(wall):
    ops = []
    for rv in wall.HasOpenings:
        o = rv.RelatedOpeningElement
        for rf in o.HasFillings:
            f = rf.RelatedBuildingElement
            ops.append({"typ": "Tür" if f.is_a("IfcDoor") else "Fenster",
                        "b": f.OverallWidth, "h": f.OverallHeight, "tag": f.Tag})
        if not o.HasFillings:  # Durchbruch / Aussparung ohne Füllung
            prof = o.Representation.Representations[0].Items[0]
            ops.append({"typ": o.ObjectType or "Aussparung", "b": prof.SweptArea.XDim,
                        "h": prof.Depth, "tag": o.Name})
    return ops


def auf(el, p, ty, st):
    q = p.get("Qto_WallBaseQuantities", {})
    e = p.get("HFW_Erfassung", {})
    c = p.get("Pset_WallCommon", {})
    return {"guid": el.GlobalId, "id": el.Name, "st": st, "type": ty, "kind": el.ObjectType,
            "L": q.get("Length"), "H": q.get("Height"), "t": q.get("Width"), "ops": openings(el),
            "lage": e.get("Lage"), "rA": e.get("Raum_Seite_A"), "rB": e.get("Raum_Seite_B"),
            "fire": c.get("FireRating"), "ext": c.get("IsExternal", False)}


def grid(f):
    g = f.by_type("IfcGrid")[0]
    def ax(axes, i):
        return {a.AxisTag: round(a.AxisCurve.Points[0].Coordinates[i], 3) for a in axes}
    return {"x": ax(g.UAxes, 0), "y": ax(g.VAxes, 1)}


def export(projekt_dir, ifc_path, version, qa_path=None):
    projekt_dir = Path(projekt_dir)
    P = json.loads((projekt_dir / "projekt.json").read_text())
    f = ifcopenshell.open(ifc_path)

    st = ifcopenshell.geom.settings()
    st.set("use-world-coords", True)
    meshes = {}
    it = ifcopenshell.geom.iterator(st, f, include=[e for c in KLASSEN[:-1] for e in f.by_type(c)])
    if it.initialize():
        while True:
            s = it.get()
            v = np.array(s.geometry.verts, dtype=np.float32).reshape(-1, 3)
            i = np.array(s.geometry.faces, dtype=np.uint32)
            meshes[s.guid] = (v, i)
            if not it.next():
                break

    P_all, I_all, elements = [], [], []
    nv = ni = 0
    for c in KLASSEN:
        for el in f.by_type(c):
            p = psets(el)
            ty = ue.get_type(el)
            ty = ty.Name if ty else None
            s = storey_of(el)
            e = {"g": el.GlobalId, "c": c, "n": el.Name, "d": el.Description,
                 "o": getattr(el, "ObjectType", None), "tag": getattr(el, "Tag", None),
                 "pt": getattr(el, "PredefinedType", None), "ty": ty, "st": s, "p": p, "ml": layers(el)}
            if el.GlobalId in meshes:
                v, i = meshes[el.GlobalId]
                e["geo"] = {"v": [nv, len(v)], "i": [ni, len(i)]}
                P_all.append(v); I_all.append(i); nv += len(v); ni += len(i)
            if c == "IfcWall":
                e["auf"] = auf(el, p, ty, s)
            elements.append(e)

    pos = np.concatenate(P_all).ravel().astype("<f4")
    idx = np.concatenate(I_all).astype("<u4")
    blob = pos.tobytes() + idx.tobytes()

    meta = {"project": P["projekt"], "building": P["gebaeude"], "address": P["adresse"], "nn": P["hoehenbezug"],
            "generated": datetime.datetime.now().strftime("%d.%m.%Y %H:%M"), "ifc": P["ifc_viewer_datei"],
            "schema": f.schema, "version": version, "sources": P["plaene"], "assumptions": P["annahmen"],
            "staende": P.get("modellstaende", [])}
    if qa_path and Path(qa_path).exists():
        meta["qa"] = json.loads(Path(qa_path).read_text())
    storeys = [{"n": s.Name, "z": s.Elevation} for s in sorted(f.by_type("IfcBuildingStorey"), key=lambda s: s.Elevation)]
    return {"meta": meta, "grid": grid(f), "storeys": storeys, "elements": elements,
            "bin": {"positions": int(pos.size), "indices": int(idx.size), "b64": base64.b64encode(blob).decode()}}


if __name__ == "__main__":
    proj, ifc, ver = sys.argv[1], sys.argv[2], int(sys.argv[3])
    M = export(proj, ifc, ver, sys.argv[4] if len(sys.argv) > 4 else None)
    out = Path(proj) / "viewer" / "model.json"
    out.write_text(json.dumps(M, ensure_ascii=False, separators=(",", ":")))
    print(f"{out}: {len(M['elements'])} Elemente, {out.stat().st_size/1e6:.2f} MB")
