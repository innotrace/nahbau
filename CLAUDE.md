# nahbau – BIM-Modelle aus PDF-Plänen für das modellbasierte Aufmaß

Sprache im Projekt: Deutsch. Nutzer: Abduaziz Juraboev (nahbau Hoch- und Tiefbau GmbH).

## Ziel
Aus PDF-Plänen (Allplan-Vektor-PDF) wird je Bauvorhaben ein IFC4-Modell aufgebaut und schrittweise verbessert.
Das Modell dient dem Trockenbau-Aufmaß (VOB/C DIN 18340) mit Export nach Word/PDF.

## Aufbau
```
tools/                   Werkzeuge (für alle Projekte)
  modell_bereinigen.py   Duplikate, enthaltene Wandstücke, Geschossteilung, Wandanschlüsse
  wandgeometrie.py       Wand verlängern/kürzen, Mengen (Qto) nachführen
  pruefung.py            Modellprüfung -> berichte/pruefbericht_v*.json/.md
  viewer_export.py       IFC -> viewer/model.json (IFC ist die einzige Datenquelle)
  build.py               Bereinigung + Planabgleich + Prüfung + Export in einem Lauf
  planraster.py          PDF <-> Modell über die Achsbeschriftungen (1..18 / A..J), mm-genau
  raumstempel.py         Raumstempel (OK FFB, OK RFB, UK RFB, UK RD, UK FD, FB) -> projekte/<p>/raumstempel.json
  planabgleich.py        Build-Schritt, gesteuert über projekt.json -> planabgleich:
  rohbau_hoehen.py         Wandhöhen aus Raumstempeln, Stahlbeton-Bodenplatte nach Raumzonen
  plan_wandtypen.py        Wandtypen TW/VW/IW aus farbigem Trockenbau-Übersichtsplan (UE_02)
  detailkatalog.py         Wandtyp-Katalog + Detailbilder aus Detailblättern (DE_01) -> wandtypen.json, viewer/details/
  plan_verstaerkungen.py   Wandverstärkungen, Stahlhohlprofile, C3-Unterkonstruktion aus Verstärkungsplan (UE_12)
  viewer_test.js         Viewer headless testen (Screenshots, JS-Fehler, Aufmaßsumme)
viewer/index.html        Viewer-Seite (gemeinsam für alle Projekte)
projekte/<projekt>/
  projekt.json           Stammdaten, Geschosse (elevation, ok_rfb), Pläne, Annahmen, Modellstände, Artifact-URL
  plaene/                eingearbeitete PDF-Pläne
  modell/<name>_v<N>.ifc alle Modellstände (nie überschreiben, immer neue Version)
  berichte/              Bereinigungsprotokolle und Prüfberichte je Version
  viewer/                model.json + <name>.ifc.txt für das Artifact
```

## Regeln für das Modell
- **Wände je Geschoss:** keine Wand über OK RFB des nächsten Geschosses. Hallenwände werden geteilt
  (unterer Teil im Geschoss, oberer Teil im nächsten Geschoss, Verweis in `HFW_Erfassung.Geschossteilung`).
- **Wandanschlüsse:** Trockenbauwände müssen an die begrenzenden Bauteile anschließen. T-Stoß bis zur Fläche der
  Nachbarwand, L-Ecke: längere GK-Wand durchlaufend, kürzere stumpf; an Massivwänden läuft GK bis zur Außenkante.
  Massivwände werden von der Automatik nie verändert. Lücken > 60 cm und Lücken in gleicher Flucht nur melden.
- Jede automatische Änderung im Pset `HFW_Erfassung` dokumentieren (`Anschluss_Anfang/_Ende`, `Pruefstatus`).
- GlobalIds eindeutig; IFC muss `python -m ifcopenshell.validate` ohne Fehler bestehen.
- Vor dem Einarbeiten eines Plans **Plankopf prüfen** (Projekt, Bauherr, Höhenbezug ±0,00). Fremde Pläne nicht
  einarbeiten, sondern in `projekt.json -> fremde_plaene` vermerken und den Nutzer informieren.

## Höhen (Raumstempel)
Wandhöhe Trockenbau = OK RFB bis UK RD der angrenzenden Räume (DIN 18340). OK FFB, Fußbodenaufbau (FFB−RFB),
UK RFB und Plattendicke stehen im Pset HFW_Raumstempel. Die Allplan-Grundrisse führen die Stempel als Tabelle
außerhalb der Räume; die Raumlage kommt aus einem Plan mit Raumnummern im Raum (EG: UE_02). Für OG1 fehlt so ein
Plan noch – dort bleiben die Wandhöhen beim Geschossstandard (OK RFB +3,45 bis UK RD +6,70).

## Wandtypen und Verstärkungen
- Plan-Wandtyp je Wand: IfcWallType 'TW 02 | 125mm' + Pset HFW_Wandtyp (Quelle, Überdeckung, Pruefstatus).
  Katalog (Brandschutz, Schallschutz, max. Raumhöhe, Schichten, Detailbild) in wandtypen.json und meta.wandtypen.
- Manuelle Zuordnungen im Viewer liegen in der Artifact-Datenbank, Sammlung `wandtypen` (Dokument-ID = Wand-GUID,
  Feld `typ`). Vor jedem Build mit `ArtifactData list` lesen und als `projekte/<p>/wandtyp_zuordnung.json`
  ({guid: {typ, datum}}) speichern – planabgleich übernimmt sie dann in die IFC.
- Verstärkungen (IfcMember PLATE/MEMBER/POST, mit der Wand über IfcRelConnectsElements verbunden): Legende und
  Höhen aus den Ansichtspiktogrammen stehen in projekt.json -> planabgleich.verstaerkungen (bei neuen Plänen ergänzen).

## Ablauf bei einem neuen Plan
1. PDF nach `projekte/<projekt>/plaene/` legen, in `projekt.json -> plaene` eintragen.
2. Inhalte aus dem PDF mit PyMuPDF auslesen (Vektoren, Texte) und mit dem aktuellen Modell abgleichen;
   neue/fehlende Bauteile per ifcopenshell in eine Kopie des letzten Modellstands schreiben.
   Hinweis: Das ursprüngliche Erfassungsskript „HFW Plan2IFC“ (Modellstand 1/2) ist nicht erhalten;
   Erfassungslogik für neue Pläne als `tools/plan_*.py` hier ablegen.
3. `python tools/build.py projekte/<projekt> --von modell/<name>_v<N>.ifc --version <N+1> --notiz "..."`
4. Prüfen: Prüfbericht lesen, `python -m ifcopenshell.validate`, `node tools/viewer_test.js ...`.
5. Viewer-Artifact aktualisieren: **nur** `model.json` und `<name>.ifc.txt` neu veröffentlichen
   (Artifact-Tool, `url` aus `projekt.json -> viewer_artifact`, `files`-Map). `viewer/index.html` nur
   mitschicken, wenn der Viewer selbst geändert wurde. Aufmaß-Einstellungen/LV-Zuordnungen liegen in der
   Artifact-Datenbank (`aufmass/config`) und bleiben erhalten.
6. Commit mit Modellstand im Betreff (z. B. „HFW Trier Modellstand 4: Trockenbauplan EG eingearbeitet“).

## Umgebung
`pip install -r requirements.txt` (ifcopenshell 0.9, shapely, numpy, pymupdf). Für Plots: matplotlib.
cdn.jsdelivr.net ist in Cloud-Sitzungen evtl. gesperrt – `viewer_test.js` bedient die Bibliotheken lokal.

## Achtung: parallele Bearbeitung
Der Viewer kann auch aus Chat-Sitzungen auf claude.ai aktualisiert werden (so entstand Stand 4). Vor jedem Build
den aktuellen Artifact-Stand lesen (`Artifact read` mit `paths`), IFC als neuen Modellstand übernehmen und
`viewer/index.html` aus dem Artifact übernehmen, falls dort geändert. `tools/viewer_export.py` muss model.json des
Artifacts exakt reproduzieren (Format: auf.p0/p1/z0/z1, Durchbrüche als IfcOpeningElement mit host, Platzhalter als
IfcBuildingElementProxy). Markierungen (`markups`) und Aufmaß-Einstellungen (`aufmass/config`) liegen in der
Artifact-Datenbank und hängen an GUIDs – GUIDs bestehender Bauteile nie ändern.
