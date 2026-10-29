"""Plan (PDF-Koordinaten) <-> Modell (m) über das Achsraster.

Achsbeschriftungen 1..18 (x) und A..J (y) stehen am Planrand doppelt (oben/unten bzw. links/rechts).
Aus ihnen wird je Richtung eine lineare Abbildung model = a * pdf + b per Ausgleichsrechnung bestimmt.
"""
import re, warnings
import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning)
import fitz  # PyMuPDF


class Planraster:
    def __init__(self, page, grid, rand=0.12):
        """page: fitz-Seite, grid: {'x': {'1': 0.0, ...}, 'y': {'A': 66.375, ...}} aus model.json."""
        W = page.get_text("words")
        R = page.rect
        xs, ys = [], []
        for w in W:
            t = w[4]
            cx, cy = (w[0] + w[2]) / 2, (w[1] + w[3]) / 2
            # nur Beschriftungen in Achskreisen am Planrand (oberes/unteres bzw. linkes/rechtes Band)
            if t in grid["x"] and re.fullmatch(r"\d+", t):
                xs.append((t, cx, cy))
            elif t in grid["y"] and re.fullmatch(r"[A-Z]", t):
                ys.append((t, cx, cy))
        self.ax = self._fit(xs, grid["x"], 1, 2)   # x: Pdf-x, Gruppierung über gleiche Pdf-y-Zeile
        self.ay = self._fit(ys, grid["y"], 2, 1)

    @staticmethod
    def _fit(kand, soll, wert_idx, zeilen_idx):
        # Achsbeschriftungen liegen in einer gemeinsamen Zeile (bzw. Spalte): häufigste Zeile suchen
        from collections import defaultdict
        zeilen = defaultdict(set)
        for k in kand:
            zeilen[round(k[zeilen_idx] / 4)].add(k[0])
        # Zeilen mit den meisten *verschiedenen* Achsbezeichnungen (Maßketten wiederholen dieselbe Zahl)
        beste = sorted(zeilen, key=lambda z: -len(zeilen[z]))[:2]
        beste = [z for z in beste if len(zeilen[z]) >= 4]
        pts = [(k[wert_idx], soll[k[0]]) for k in kand if round(k[zeilen_idx] / 4) in beste]
        # Ausreißer (gleiche Bezeichnung in Raumstempeln o. ä.) über robuste Mehrfachanpassung entfernen
        p = np.array(pts, dtype=float)
        for _ in range(3):
            a, b = np.polyfit(p[:, 0], p[:, 1], 1)
            r = np.abs(a * p[:, 0] + b - p[:, 1])
            if r.max() < 0.05:
                break
            p = p[r < max(0.05, np.median(r) * 3)]
        a, b = np.polyfit(p[:, 0], p[:, 1], 1)
        res = np.abs(a * p[:, 0] + b - p[:, 1]).max()
        return a, b, len(p), res

    def m(self, x, y):
        """PDF-Punkt -> Modellkoordinate (m)."""
        return (self.ax[0] * x + self.ax[1], self.ay[0] * y + self.ay[1])

    def massstab(self):
        """m je PDF-Punkt."""
        return abs(self.ax[0])

    def info(self):
        return (f"x: {self.ax[2]} Achsen, max. Abweichung {self.ax[3]*100:.1f} cm | "
                f"y: {self.ay[2]} Achsen, max. Abweichung {self.ay[3]*100:.1f} cm | "
                f"Maßstab 1:{round(self.massstab() / 0.0254 * 72)}")


def oeffnen(pfad, grid):
    d = fitz.open(pfad)
    p = d[0]
    return d, p, Planraster(p, grid)
