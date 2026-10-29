"""Modellprüfung für das Aufmaß. Erzeugt pruefbericht.json (für den Viewer) und pruefbericht.md.

Prüfungen:
  guid_doppelt        – GlobalId mehrfach vergeben
  geschossgrenze      – Wand reicht über OK RFB des nächsten Geschosses (muss geteilt werden)
  ueberlappung        – parallele Wände im selben Geschoss überdecken sich (Doppelzählung im Aufmaß)
  offene_enden        – Trockenbauwand-Enden ohne Anschluss
  raum_ohne_geometrie – Raum nur mit Stempeldaten
"""
import collections, json, sys
from pathlib import Path

import ifcopenshell
import ifcopenshell.util.element as ue
from shapely.strtree import STRtree

sys.path.insert(0, str(Path(__file__).parent))
from wandgeometrie import Wand
from modell_bereinigen import offene_enden, _winkel


def pruefen(ifc_path, projekt_dir):
    P = json.loads((Path(projekt_dir) / "projekt.json").read_text())
    f = ifcopenshell.open(ifc_path)
    befunde = []

    cnt = collections.Counter(e.GlobalId for e in f.by_type("IfcRoot"))
    for el in f.by_type("IfcElement"):
        if cnt[el.GlobalId] > 1:
            befunde.append({"pruefung": "guid_doppelt", "stufe": "Fehler", "element": el.Name, "guid": el.GlobalId,
                            "text": f"GlobalId {cnt[el.GlobalId]}-fach vergeben"})

    rfb = {g["name"]: g["ok_rfb"] for g in P["geschosse"]}
    namen = [g["name"] for g in P["geschosse"]]
    walls = [Wand(el) for el in f.by_type("IfcWall")]
    for w in walls:
        i = namen.index(w.st.Name)
        if i + 1 < len(namen) and w.z0 + w.H > rfb[namen[i + 1]] + 0.01:
            befunde.append({"pruefung": "geschossgrenze", "stufe": "Fehler", "element": w.name(), "guid": w.el.GlobalId,
                            "text": f"OK Wand {w.z0 + w.H:+.2f} über OK RFB {namen[i + 1]} {rfb[namen[i + 1]]:+.2f}".replace(".", ",")})

    for st in namen:
        W = [w for w in walls if w.st.Name == st]
        polys = [w.polygon() for w in W]
        tree = STRtree(polys)
        for i, w in enumerate(W):
            for j in tree.query(polys[i]):
                if j <= i or _winkel(W[j].x, w.x) > 10:
                    continue
                a = polys[i].intersection(polys[j]).area
                if a > 0.02:
                    gk = "GK" in (w.kind, W[j].kind)
                    befunde.append({"pruefung": "ueberlappung", "stufe": "Fehler" if gk else "Hinweis",
                                    "element": w.name(), "guid": w.el.GlobalId, "partner": W[j].name(),
                                    "partner_guid": W[j].el.GlobalId,
                                    "text": f"überlappt mit {W[j].name()} ({a:.2f} m² Grundfläche) – Doppelzählung prüfen".replace(".", ",")})

    for o in offene_enden(f):
        befunde.append({"pruefung": "offene_enden", "stufe": "Hinweis", "element": o["wand"], "guid": o["guid"],
                        "text": f"{o['ende']}: {o['hinweis']} (Abstand {o['abstand_m']:.2f} m)".replace(".", ","),
                        "x": o["x"], "y": o["y"]})

    for sp in f.by_type("IfcSpace"):
        if not sp.Representation:
            befunde.append({"pruefung": "raum_ohne_geometrie", "stufe": "Hinweis", "element": sp.Name, "guid": sp.GlobalId,
                            "text": f"{sp.LongName or ''}: nur Stempeldaten, Raumkontur fehlt"})

    zus = collections.Counter((b["pruefung"], b["stufe"]) for b in befunde)
    return {"datei": Path(ifc_path).name,
            "zusammenfassung": [{"pruefung": k[0], "stufe": k[1], "anzahl": v} for k, v in sorted(zus.items())],
            "befunde": befunde}


TITEL = {"guid_doppelt": "Doppelte GlobalId", "geschossgrenze": "Wand über Geschossgrenze",
         "ueberlappung": "Überlappende Wände", "offene_enden": "Offene Trockenbau-Wandenden",
         "raum_ohne_geometrie": "Räume ohne Geometrie"}


def markdown(r):
    z = ["# Prüfbericht " + r["datei"], "", "| Prüfung | Stufe | Anzahl |", "|---|---|---:|"]
    z += [f"| {TITEL[s['pruefung']]} | {s['stufe']} | {s['anzahl']} |" for s in r["zusammenfassung"]]
    for k, t in TITEL.items():
        b = [x for x in r["befunde"] if x["pruefung"] == k]
        if b:
            z += ["", f"## {t} ({len(b)})", ""] + [f"- **{x['element']}** – {x['text']}" for x in b]
    return "\n".join(z) + "\n"


if __name__ == "__main__":
    ifc, proj, out = sys.argv[1:4]
    r = pruefen(ifc, proj)
    Path(out).write_text(json.dumps(r, ensure_ascii=False, indent=1))
    Path(out).with_suffix(".md").write_text(markdown(r))
    for s in r["zusammenfassung"]:
        print(f"{TITEL[s['pruefung']]:32s} {s['stufe']:8s} {s['anzahl']}")
