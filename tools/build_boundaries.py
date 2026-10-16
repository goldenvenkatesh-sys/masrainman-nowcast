"""Rebuild assets/boundaries.json (district + state/coast lines used on the
nowcast map) from per-state district GeoJSON files, e.g. those in
https://github.com/udit-001/india-maps-data/tree/main/geojson/states

  python tools/build_boundaries.py tamil-nadu.geojson puducherry.geojson kerala.geojson karnataka.geojson andhra-pradesh.geojson

Lines shared by two districts of one state are drawn thin (district
lines); edges that belong to only one district polygon are the state
border / coastline and are drawn thick. No GIS libraries needed.
"""
import json
import sys
from collections import Counter
from pathlib import Path

BBOX = (6.0, 72.0, 16.5, 82.5)      # south, west, north, east
TOL = 0.004                         # simplification tolerance, degrees (~400 m)


def rings(geom):
    polys = [geom["coordinates"]] if geom["type"] == "Polygon" else geom["coordinates"]
    for poly in polys:
        for ring in poly:
            yield [(round(x, 5), round(y, 5)) for x, y, *_ in ring]


def dp(pts, tol):
    """Douglas-Peucker simplification."""
    if len(pts) < 3:
        return pts
    (x1, y1), (x2, y2) = pts[0], pts[-1]
    dx, dy = x2 - x1, y2 - y1
    L = (dx * dx + dy * dy) ** 0.5 or 1e-12
    dmax, idx = 0, 0
    for i in range(1, len(pts) - 1):
        d = abs(dy * pts[i][0] - dx * pts[i][1] + x2 * y1 - y2 * x1) / L
        if d > dmax:
            dmax, idx = d, i
    if dmax <= tol:
        return [pts[0], pts[-1]]
    return dp(pts[: idx + 1], tol)[:-1] + dp(pts[idx:], tol)


def inside(p):
    s, w, n, e = BBOX
    return s <= p[1] <= n and w <= p[0] <= e


def runs(ring, keep):
    """Split a ring into polylines of consecutive edges where keep(edge)."""
    out, cur = [], []
    for a, b in zip(ring, ring[1:]):
        if keep(a, b) and (inside(a) or inside(b)):
            if not cur:
                cur = [a]
            cur.append(b)
        elif cur:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


def main(files):
    districts, outline = [], []
    for f in files:
        feats = json.loads(Path(f).read_text())["features"]
        all_rings = [r for ft in feats for r in rings(ft["geometry"])]
        cnt = Counter()
        for r in all_rings:
            for a, b in zip(r, r[1:]):
                cnt[frozenset((a, b))] += 1
        for r in all_rings:
            outline += runs(r, lambda a, b: cnt[frozenset((a, b))] == 1)
            districts += runs(r, lambda a, b: cnt[frozenset((a, b))] > 1)
    simp = lambda lines: [[[round(x, 3), round(y, 3)] for x, y in dp(l, TOL)] for l in lines if len(l) > 1]
    doc = {"source": "district polygons from github.com/udit-001/india-maps-data (geojson/states)",
           "districts": simp(districts), "outline": simp(outline)}
    out = Path(__file__).resolve().parent.parent / "assets" / "boundaries.json"
    out.write_text(json.dumps(doc, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size // 1024} KB, {len(doc['districts'])} district lines, "
          f"{len(doc['outline'])} outline lines)")


if __name__ == "__main__":
    main(sys.argv[1:])
