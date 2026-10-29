# nahbau

BIM-Modelle (IFC4) aus PDF-Plänen für das modellbasierte Trockenbau-Aufmaß.

| Projekt | Viewer | Stand |
|---|---|---|
| HFW Trier – Hauptfeuerwache mit Rettungswache | [HFW Trier BIM-Viewer](https://claude.ai/artifact/UT8X1EWjSTAbgvDowtxNgE) | Modellstand 6 |

## Arbeitsweise
1. Neuen Plan in einer Claude-Code-Sitzung mit diesem Repository hochladen.
2. Claude gleicht den Plan mit dem Modell ab, baut einen neuen Modellstand (`tools/build.py`) und prüft ihn.
3. Der bestehende Viewer wird mit den neuen Modelldaten aktualisiert – der Link bleibt gleich,
   Aufmaß-Einstellungen und LV-Zuordnungen bleiben erhalten.

Details zu Aufbau, Regeln und Ablauf: [CLAUDE.md](CLAUDE.md).

## Modellstände HFW Trier
- **Stand 2** (29.10.2026, Chat): Grundriss EG und OG1 automatisch aus Vektor-PDF erfasst.
- **Stand 4** (29.10.2026, Chat): EG Index L, 465 Wanddurchbrüche, Geschossteilung, Wandanschlüsse, Markierungen im Viewer.
- **Stand 5** (29.10.2026, dieses Repository): Bereinigung auf Stand 4 – 2 doppelte Bauteile (doppelte GUID) und
  7 enthaltene Wandstücke entfernt (−18,9 m² Doppelzählung GK), 1 weiterer Anschluss, IFC ohne Validierungsfehler.
  Offene Punkte (66 GK-Überlappungen, 76 offene Wandenden) im Prüfbericht und im Viewer unter „Planstand“:
  [projekte/hfw-trier/berichte/pruefbericht_v5.md](projekte/hfw-trier/berichte/pruefbericht_v5.md)
- **Stand 6** (29.10.2026): Planabgleich mit UE_02 (Trockenbauwände EG), UE_12 (Verstärkungen EG), DE_01 (Wandtypen)
  und den Raumstempeln der Grundrisse – Wandhöhen OK RFB bis UK RD (11 Wände korrigiert, 6 an der Geschossgrenze
  geteilt), Stahlbeton-Bodenplatte EG (9 Zonen, d = 30–76 cm), 59 GK-Wände mit Plan-Wandtyp, Katalog mit
  Detailbildern im Viewer, 122 Verstärkungen (Stahlblech, Stahlhohlprofile, Feuerlöscher, C3).
