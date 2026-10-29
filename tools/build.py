"""Neuen Modellstand bauen: Bereinigung -> Prüfung -> Viewer-Export.

  python tools/build.py projekte/hfw-trier --von modell/hfw_trier_v2.ifc --version 3 --notiz "..."

Ergebnis:
  modell/hfw_trier_v<version>.ifc        neuer Modellstand (bleibt im Repo, alte Stände ebenfalls)
  berichte/bereinigung_v<version>.json   Protokoll aller automatischen Änderungen
  berichte/pruefbericht_v<version>.json/.md
  viewer/model.json, viewer/<ifc_viewer_datei>   Dateien für das Viewer-Artifact
"""
import argparse, datetime, json, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from modell_bereinigen import bereinigen
from pruefung import pruefen, markdown
from viewer_export import export

ap = argparse.ArgumentParser()
ap.add_argument("projekt")
ap.add_argument("--von", required=True, help="Ausgangs-IFC relativ zum Projektordner")
ap.add_argument("--version", type=int, required=True)
ap.add_argument("--notiz", default="")
ap.add_argument("--ohne-bereinigung", action="store_true")
a = ap.parse_args()

proj = Path(a.projekt)
P = json.loads((proj / "projekt.json").read_text())
stem = Path(P["ifc_viewer_datei"]).name.split(".")[0]
ifc_neu = proj / "modell" / f"{stem}_v{a.version}.ifc"
(proj / "berichte").mkdir(exist_ok=True)

if a.ohne_bereinigung:
    shutil.copy(proj / a.von, ifc_neu)
    prot = {}
else:
    prot = bereinigen(proj / a.von, ifc_neu, proj)
    (proj / "berichte" / f"bereinigung_v{a.version}.json").write_text(json.dumps(prot, ensure_ascii=False, indent=1))

qa = pruefen(ifc_neu, proj)
qa_json = proj / "berichte" / f"pruefbericht_v{a.version}.json"
qa_json.write_text(json.dumps(qa, ensure_ascii=False, indent=1))
qa_json.with_suffix(".md").write_text(markdown(qa))

staende = [s for s in P.get("modellstaende", []) if s["version"] != a.version]
staende.append({"version": a.version, "datum": datetime.date.today().strftime("%d.%m.%Y"), "notiz": a.notiz,
                "aenderungen": {k: len(v) for k, v in prot.items() if k != "offene_enden"}})
P["modellstaende"] = staende
(proj / "projekt.json").write_text(json.dumps(P, ensure_ascii=False, indent=2))

M = export(proj, ifc_neu, a.version, qa_json)
(proj / "viewer").mkdir(exist_ok=True)
(proj / "viewer" / "model.json").write_text(json.dumps(M, ensure_ascii=False, separators=(",", ":")))
shutil.copy(ifc_neu, proj / "viewer" / P["ifc_viewer_datei"])

print(f"Modellstand {a.version}: {ifc_neu}")
print("Änderungen:", {k: len(v) for k, v in prot.items()})
for s in qa["zusammenfassung"]:
    print(f"  {s['pruefung']:20s} {s['stufe']:8s} {s['anzahl']}")
