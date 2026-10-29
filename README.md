# nahbau

BIM-Modelle (IFC4) aus PDF-Plänen für das modellbasierte Trockenbau-Aufmaß.

| Projekt | Viewer | Stand |
|---|---|---|
| HFW Trier – Hauptfeuerwache mit Rettungswache | [HFW Trier BIM-Viewer](https://claude.ai/artifact/UT8X1EWjSTAbgvDowtxNgE) | Modellstand 3 |

## Arbeitsweise
1. Neuen Plan in einer Claude-Code-Sitzung mit diesem Repository hochladen.
2. Claude gleicht den Plan mit dem Modell ab, baut einen neuen Modellstand (`tools/build.py`) und prüft ihn.
3. Der bestehende Viewer wird mit den neuen Modelldaten aktualisiert – der Link bleibt gleich,
   Aufmaß-Einstellungen und LV-Zuordnungen bleiben erhalten.

Details zu Aufbau, Regeln und Ablauf: [CLAUDE.md](CLAUDE.md).

## Modellstände HFW Trier
- **Stand 2** (29.10.2026): Grundriss EG und OG1 automatisch aus Vektor-PDF erfasst.
- **Stand 3** (29.10.2026): Bereinigung – 5 doppelte Bauteile und 12 enthaltene Wandstücke entfernt,
  8 Hallenwände an der Geschossgrenze geteilt, 34 Trockenbau-Wandanschlüsse geschlossen.
  Prüfbericht: [projekte/hfw-trier/berichte/pruefbericht_v3.md](projekte/hfw-trier/berichte/pruefbericht_v3.md)
