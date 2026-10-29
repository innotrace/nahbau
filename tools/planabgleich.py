"""Planabgleich als Build-Schritt (gesteuert über projekt.json -> 'planabgleich').

  raumstempel.json            -> HFW_Raumstempel der Räume (OK FFB, OK RFB, UK RFB, UK RD, UK FD, FB)
  raumlage_aus {Geschoss:Plan} -> Höhen der Trockenbauwände aus den angrenzenden Räumen
  bodenplatte                  -> Stahlbeton-Bodenplatte (IfcSlab BASESLAB) nach Raumzonen
  wandtypen [..]               -> Plan-Wandtypen (TW/VW/IW) aus farbigem Übersichtsplan, Katalog aus Detailblättern
"""
import json, sys
from pathlib import Path

import ifcopenshell.api.root as aroot

sys.path.insert(0, str(Path(__file__).parent))
from viewer_export import grid as grid_aus_ifc
from wandgeometrie import Wand
import rohbau_hoehen as rh
import plan_wandtypen as pw
import detailkatalog as dk
import plan_verstaerkungen as pv


def planabgleich(f, projekt_dir):
    proj = Path(projekt_dir)
    P = json.loads((proj / "projekt.json").read_text())
    cfg = P.get("planabgleich")
    if not cfg:
        return {}
    grid = grid_aus_ifc(f)
    plan = lambda n: proj / "plaene" / n
    prot = {}
    stempel = json.loads((proj / "raumstempel.json").read_text()) if (proj / "raumstempel.json").exists() else {}
    if stempel:
        prot["raumstempel"] = [{"raeume": rh.raumstempel_in_ifc(f, stempel)}]
    lagen = {gs: rh.raumlagen(plan(pl), grid, P["raumnummer_praefix"][gs]) for gs, pl in cfg.get("raumlage_aus", {}).items()}
    umriss = {}
    for gs, lag in lagen.items():
        W = [w for w in (Wand(e) for e in f.by_type("IfcWall")) if w.st.Name == gs]
        bp = cfg.get("bodenplatte", {})
        umriss[gs] = rh.umriss_aus_waenden(W, bp.get("bereich") if bp.get("geschoss") == gs else cfg["umriss_bereich"][gs])
        prot.setdefault("wandhoehen", []).extend(rh.wandhoehen(f, stempel, lag, gs, umriss[gs], None))
    bp = cfg.get("bodenplatte")
    if bp:
        # vorhandene Bodenplatten ersetzen, GUIDs je Name beibehalten
        alt = {s.Name: s.GlobalId for s in f.by_type("IfcSlab") if s.ObjectType == "Bodenplatte"}
        for s in [s for s in f.by_type("IfcSlab") if s.ObjectType == "Bodenplatte"]:
            aroot.remove_product(f, product=s)
        platten, fehler = rh.bodenplatte(f, stempel, lagen[bp["geschoss"]], umriss[bp["geschoss"]], bp["geschoss"])
        for s in f.by_type("IfcSlab"):
            if s.Name in alt:
                s.GlobalId = alt[s.Name]
        prot["bodenplatte"] = platten
        prot["bodenplatte_hinweise"] = fehler
    katalog = {}
    for wt in cfg.get("wandtypen", []):
        L, T, labels, plan_prot, r = pw.lesen(plan(wt["plan"]), grid, wt["legende"], wt["planbereich"])
        katalog.update(dk.katalog(proj, cfg.get("detailblaetter", []), L))
        prot.setdefault("wandtypen_farben", []).extend(plan_prot)
        prot.setdefault("wandtypen", []).extend(pw.zuordnen(f, wt["geschoss"], T, katalog, labels, wt["plan"]))
    # manuelle Zuordnungen aus dem Viewer (Artifact-Datenbank 'wandtypen', vor dem Build exportiert)
    zf = proj / "wandtyp_zuordnung.json"
    if zf.exists():
        if not katalog and (proj / "wandtypen.json").exists():
            katalog = json.loads((proj / "wandtypen.json").read_text())
        nach_guid = {e.GlobalId: e for e in f.by_type("IfcWall")}
        for guid, z in json.loads(zf.read_text()).items():
            el = nach_guid.get(guid)
            if el is None or not z.get("typ") or z["typ"] not in katalog:
                prot.setdefault("wandtypen_manuell", []).append({"guid": guid, "typ": z.get("typ"), "status": "nicht übernommen"})
                continue
            pw.typ_setzen(f, el, z["typ"], katalog, {"Quelle": f"manuell im Viewer ({z.get('datum', '')})",
                                                    "Pruefstatus": "manuell zugeordnet", "Ueberdeckung": None, "Hinweis": None})
            prot.setdefault("wandtypen_manuell", []).append({"wand": el.Name, "typ": z["typ"], "status": "übernommen"})
    for vs in cfg.get("verstaerkungen", []):
        prot.setdefault("verstaerkungen", []).extend(pv.einarbeiten(f, proj, vs, grid))
    if katalog:
        (proj / "wandtypen.json").write_text(json.dumps(katalog, ensure_ascii=False, indent=1))
    return prot
