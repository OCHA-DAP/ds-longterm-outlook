"""Small locator maps of the FEWS NET calendar regions, drawn on each country page.

For a country whose calendar has more than one region, the page shows where each region is: its
admin units filled, the rest of the country grey. The boundaries are the SEAS5 site's own (the
hnrp_adm1 / hnrp_adm2 / hnrp_ipc GeoJSON of the same issuance), so the p-codes are the ones the
calendar assignments use. Admin-2 is used where the calendar assigns some admin-2 units on their
own (Afghanistan, Colombia, Guatemala, Somalia, South Sudan, Syria, Yemen); IPC/CH areas where
there are no admin-1 boundaries (Tanzania).

Rings are projected (longitude scaled by the cosine of the middle latitude), simplified
(Ramer-Douglas-Peucker) and written as SVG path strings in a box whose longer side is 100, one
path per region, so the page needs no mapping library to draw them.
"""

import math

from outlook.calendar import region_of

SIDE = 100.0
EPS = 0.3  # simplification tolerance, in box units (the page draws the box 22-150 px tall)
MIN_RING = 0.8  # rings whose longer side is under this (box units) are dropped: small islands
MAX_NAMES = 16  # region tooltips list admin-1 names up to this many


def _rdp(pts: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Ramer-Douglas-Peucker on an open polyline (iterative)."""
    if len(pts) < 3:
        return pts
    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        a, b = stack.pop()
        (x1, y1), (x2, y2) = pts[a], pts[b]
        dx, dy = x2 - x1, y2 - y1
        n = math.hypot(dx, dy) or 1e-12
        far, dmax = None, eps
        for i in range(a + 1, b):
            x0, y0 = pts[i]
            d = abs(dy * x0 - dx * y0 + x2 * y1 - y2 * x1) / n
            if d > dmax:
                far, dmax = i, d
        if far is not None:
            keep[far] = True
            stack += [(a, far), (far, b)]
    return [p for p, k in zip(pts, keep, strict=True) if k]


def _rings(geom: dict) -> list[list[list[float]]]:
    if geom["type"] == "Polygon":
        return geom["coordinates"]
    if geom["type"] == "MultiPolygon":
        return [r for poly in geom["coordinates"] for r in poly]
    return []


def _parent(pcode: str, adm1: dict[str, str]) -> str | None:
    hits = [p for p in adm1 if pcode.startswith(p)]
    return max(hits, key=len) if hits else None


def country_zones(cal: dict, feats: list[dict], adm1_names: dict[str, str]) -> dict | None:
    """Locator-map paths per calendar region (key "" = units no region claims), and the admin-1
    names in each region ("part of X" where only some of its admin-2 units are)."""
    if len(cal["regions"]) < 2 or not feats:
        return None
    assign = cal.get("assign") or {}
    pts = [(x, y) for f in feats for ring in _rings(f["geometry"]) for x, y in ring]
    lon0, lon1 = min(p[0] for p in pts), max(p[0] for p in pts)
    lat0, lat1 = min(p[1] for p in pts), max(p[1] for p in pts)
    kx = math.cos(math.radians((lat0 + lat1) / 2))
    w, h = (lon1 - lon0) * kx, lat1 - lat0
    s = SIDE / max(w, h)

    paths: dict[str, list[str]] = {}
    members: dict[str, set[str]] = {}
    for f in feats:
        pcode = f["properties"]["pcode"]
        rid = region_of(pcode, assign) or ""
        par = _parent(pcode, adm1_names) or pcode
        members.setdefault(par, set()).add(rid)
        for ring in _rings(f["geometry"]):
            xy = [((x - lon0) * kx * s, (lat1 - y) * s) for x, y in ring]
            xs, ys = [p[0] for p in xy], [p[1] for p in xy]
            if max(max(xs) - min(xs), max(ys) - min(ys)) < MIN_RING:
                continue
            # a closed ring starts and ends on the same point, which leaves RDP no baseline:
            # split it at the point farthest from the start and simplify the two halves
            k = max(range(len(xy)), key=lambda i: math.dist(xy[0], xy[i]))
            xy = _rdp(xy[: k + 1], EPS) + _rdp(xy[k:], EPS)[1:]
            if len(xy) < 4:
                continue
            paths.setdefault(rid, []).append(
                "M" + "L".join(f"{x:.1f} {y:.1f}" for x, y in xy[:-1]) + "Z")

    names: dict[str, list[str]] = {}
    for par, rids in sorted(members.items(), key=lambda kv: adm1_names.get(kv[0], kv[0])):
        nm = adm1_names.get(par)
        if not nm:
            continue
        for rid in rids - {""}:
            names.setdefault(rid, []).append(nm if len(rids) == 1 else f"part of {nm}")
    return dict(
        w=round(w * s, 1), h=round(h * s, 1),
        paths={rid: "".join(p) for rid, p in paths.items()},
        names={rid: (v if len(v) <= MAX_NAMES else None) for rid, v in names.items()},
        units={rid: sum(1 for f in feats if (region_of(f["properties"]["pcode"], assign) or "")
                        == rid) for rid in paths},
    )


def zones_by_country(cals: dict, adm1: list, adm2: list, ipc: list) -> dict:
    """iso3 -> {layer: country_zones()}: admin 1, plus admin 2 where the calendar assigns some
    admin-2 units on their own, or IPC/CH areas where there is no admin 1. The page draws the
    one at the level of the count's units (a count at admin 1 follows the admin-1 assignment)."""
    by = {}
    for layer, fs in (("adm1", adm1), ("adm2", adm2), ("ipc", ipc)):
        for f in fs:
            by.setdefault((layer, f["properties"]["iso3"]), []).append(f)
    out = {}
    for iso3, cal in cals.items():
        if len(cal["regions"]) < 2:
            continue
        names = {f["properties"]["pcode"]: f["properties"]["name"]
                 for f in by.get(("adm1", iso3), [])}
        wants_adm2 = bool((cal.get("assign") or {}).get("adm2"))
        layers = (["adm2"] if wants_adm2 else []) + ["adm1"]
        if not by.get(("adm1", iso3)):
            layers.append("ipc")
        maps = {}
        for layer in layers:
            z = country_zones(cal, by.get((layer, iso3), []), names)
            if z:
                z["layer"] = layer
                maps[layer] = z
        if maps:
            out[iso3] = maps
    return out


def unit_level(rows: list[dict], adm1: list) -> str:
    """'adm1' when most of a payload's units are admin-1 units, else 'adm2'."""
    codes = {f["properties"]["pcode"] for f in adm1}
    hits = sum(r["pcode"] in codes for r in rows)
    return "adm1" if rows and hits >= len(rows) / 2 else "adm2"
