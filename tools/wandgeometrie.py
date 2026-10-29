"""Hilfsfunktionen für gerade Wände aus 'HFW Plan2IFC'.

Aufbau einer Wand im Modell:
  - ObjectPlacement relativ zum Geschoss, Ursprung = Wandanfang auf UK Wand, lokale x-Achse = Wandachse
  - 'Axis'-Darstellung: Polyline (0,0) -> (L,0)
  - 'Body'-Darstellung: IfcExtrudedAreaSolid, Rechteck XDim=L, YDim=Dicke, mittig auf der Achse, Höhe=Depth
  - Öffnungen/Türen/Fenster sind relativ zur Wandplatzierung platziert
"""
import numpy as np
import ifcopenshell.util.element as ue
import ifcopenshell.util.placement as up
from shapely.geometry import Polygon


class Wand:
    def __init__(self, el):
        self.el = el
        self.M = up.get_local_placement(el.ObjectPlacement)
        self.o = self.M[:2, 3].copy()
        self.x = self.M[:2, 0] / np.linalg.norm(self.M[:2, 0])
        self.n = np.array([-self.x[1], self.x[0]])
        self.z0 = float(self.M[2, 3])
        self.body = next(r for r in el.Representation.Representations if r.RepresentationIdentifier == "Body").Items[0]
        self.axis = next(r for r in el.Representation.Representations if r.RepresentationIdentifier == "Axis")
        self.L = float(self.body.SweptArea.XDim)
        self.t = float(self.body.SweptArea.YDim)
        self.H = float(self.body.Depth)
        self.kind = el.ObjectType  # GK | MASS | NG
        self.st = ue.get_container(el)

    @property
    def p0(self):
        return self.o

    @property
    def p1(self):
        return self.o + self.x * self.L

    def ende(self, i):
        """Punkt und Richtung (nach außen) des Wandendes i (0=Anfang, 1=Ende)."""
        return (self.p0, -self.x) if i == 0 else (self.p1, self.x)

    def polygon(self):
        h = self.n * self.t / 2
        return Polygon([self.p0 - h, self.p1 - h, self.p1 + h, self.p0 + h])

    def name(self):
        return self.el.Name


def _pt2(f, x, y):
    return f.createIfcCartesianPoint((float(x), float(y)))


def setze_wand(f, w: Wand, ds=0.0, de=0.0, H=None, z0=None):
    """Wand am Anfang um ds und am Ende um de verlängern (negativ = kürzen), optional Höhe/UK ändern.
    Öffnungen bleiben an ihrer Weltposition. Mengen (Qto) werden nachgeführt."""
    el = w.el
    rp = el.ObjectPlacement.RelativePlacement
    loc = np.array(rp.Location.Coordinates, dtype=float)
    loc[:2] -= w.x * ds
    dz = 0.0
    if z0 is not None:
        dz = z0 - w.z0
        loc[2] += dz
    rp.Location = f.createIfcCartesianPoint(tuple(float(v) for v in loc))
    # gehostete Öffnungen und deren Füllungen relativ zur Wand zurückschieben
    for rv in el.HasOpenings:
        o = rv.RelatedOpeningElement
        objs = [o] + [rf.RelatedBuildingElement for rf in o.HasFillings]
        for ob in objs:
            if ob.ObjectPlacement.PlacementRelTo == el.ObjectPlacement:
                r = ob.ObjectPlacement.RelativePlacement
                c = list(r.Location.Coordinates)
                c[0] += ds
                c[2] -= dz
                r.Location = f.createIfcCartesianPoint(tuple(float(v) for v in c))
    L = w.L + ds + de
    H = w.H if H is None else H
    prof = w.body.SweptArea
    prof.XDim = float(L)
    prof.Position = f.createIfcAxis2Placement2D(_pt2(f, L / 2, 0), None)
    w.body.Depth = float(H)
    w.axis.Items = (f.createIfcPolyline((_pt2(f, 0, 0), _pt2(f, L, 0))),)
    w.o, w.L, w.H = w.o - w.x * ds, L, H
    if z0 is not None:
        w.z0 = z0
    mengen(f, w)


def oeffnungsflaeche(el):
    a = 0.0
    for rv in el.HasOpenings:
        for rf in rv.RelatedOpeningElement.HasFillings:
            b = rf.RelatedBuildingElement
            a += (b.OverallWidth or 0) * (b.OverallHeight or 0)
    return a


def mengen(f, w: Wand):
    import ifcopenshell.api.pset as ps
    q = ue.get_psets(w.el).get("Qto_WallBaseQuantities")
    gross = w.L * w.H
    net = gross - oeffnungsflaeche(w.el)
    vals = {"Length": round(w.L, 4), "Height": round(w.H, 4), "GrossSideArea": round(gross, 4),
            "NetSideArea": round(net, 4)}
    if q and q.get("Width"):
        vals["GrossVolume"] = round(gross * q["Width"], 4)
        vals["NetVolume"] = round(net * q["Width"], 4)
    if q:
        ps.edit_qto(f, qto=f.by_id(q["id"]), properties=vals)
