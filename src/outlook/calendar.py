"""FEWS NET seasonal calendars: from a dry rainy season to the lean season it feeds.

The calendars (data/fewsnet_calendars/<ISO3>.json) are our transcriptions of FEWS NET's
seasonal-calendar images. Each region has rainy seasons, harvests (linked to the rains that
produce them), lean seasons (cropping / pastoral / general) and an admin-1 assignment.

The logic follows how FEWS NET reasons about a poor season:
  dry rainy season -> poor harvest (or poor pasture and water) -> stocks and income run out
  early -> the NEXT lean season comes early and is worse.
So for each occurrence of a rainy season in the outlook window:
  - harvest: the first harvest linked to that season starting on or after the rains begin;
  - impact (crops): the first cropping/general lean season starting after that harvest begins;
  - impact (livestock): where the region has a pastoral lean season, the first one starting
    after the rains end (pasture and water run down through the following dry season).
A region with no harvest for the season uses the first lean season after the rains end.
When another season's harvest starts between that harvest and the lean season, the impact is
kept but marked `diluted_by` (that harvest refills stocks first). Pastoral lean seasons labelled
for a named sub-area are drawn but not followed (they cover a few counties, not the region).

Months are absolute indices (year * 12 + month - 1) so spans can run across years.
"""

import json
from pathlib import Path

MAX_LAG = 12  # months: a harvest or lean season further away than this is not the one fed


def span_len(start: int, end: int) -> int:
    return (end - start) % 12 + 1


def occurrences(start: int, end: int, t0: int, t1: int) -> list[tuple[int, int]]:
    """Every (abs start, abs end) of a yearly span overlapping [t0, t1]."""
    n = span_len(start, end)
    out = []
    for y in range(t0 // 12 - 1, t1 // 12 + 2):
        s = y * 12 + start - 1
        e = s + n - 1
        if e >= t0 and s <= t1:
            out.append((s, e))
    return out


def first_after(spans: list[dict], t: int, horizon: int, key=lambda sp: True):
    """The earliest occurrence of any of `spans` starting in [t, t + horizon]."""
    best = None
    for sp in spans:
        if not key(sp):
            continue
        for s, e in occurrences(sp["start"], sp["end"], t, t + horizon):
            if t <= s <= t + horizon and (best is None or s < best[0]):
                best = (s, e, sp)
    return best


def load(dirpath: Path) -> dict[str, dict]:
    out = {}
    for p in sorted(dirpath.glob("*.json")):
        d = json.loads(p.read_text())
        out[d["iso3"]] = d
    return out


def region_of(pcode: str, assign: dict) -> str | None:
    """adm2 override (longest prefix) first, then the adm1 assignment (longest prefix)."""
    for table in (assign.get("adm2") or {}, assign.get("adm1") or {}):
        hits = [k for k in table if pcode.startswith(k)]
        if hits:
            return table[max(hits, key=len)]
    return None


MINOR_HARVEST = ("minor", "green", "off-season", "off season", "sweet potato", "vegetable")


def main_harvests(linked: list[dict]) -> list[dict]:
    """The harvests that stand for a season's crop: those named "main" if any, else all but
    the minor / green / off-season / vegetable ones, else all. (A season's first harvest bar is
    often a minor crop: Sierra Leone's sweet potatoes before its rice.)"""
    named = [h for h in linked if "main" in h["name"].lower()]
    if named:
        return named
    major = [h for h in linked if not any(w in h["name"].lower() for w in MINOR_HARVEST)]
    return major or linked


def is_subarea(lean: dict) -> bool:
    """A pastoral lean season the calendar labels for a named sub-area ("Pastoral Lean Season:
    Pibor and Greater Kapoeta") applies to a few counties, not the whole calendar region."""
    return lean.get("kind") == "pastoral" and ":" in (lean.get("name") or "")


def season_links(region: dict, t0: int, t1: int) -> list[dict]:
    """Rainy-season occurrences in [t0, t1] with their harvest and the lean season(s) they feed.

    The cropping lean season is the first one starting AFTER the harvest's first month (a lean
    season starting the month the harvest starts is the one that harvest ends)."""
    harvests = region.get("harvest") or []
    leans = region.get("lean") or []
    crop = [lv for lv in leans if lv.get("kind") in ("cropping", "general")]
    past = [lv for lv in leans if lv.get("kind") == "pastoral" and not is_subarea(lv)]
    out = []
    for rs in region.get("rainy") or []:
        if span_len(rs["start"], rs["end"]) >= 11 or rs.get("minor"):
            # "year-round rainfall" (Colombia's Pacific) and minor showers (Somalia's Xagaa) are
            # drawn on the page but not followed as a cropping season
            continue
        linked = main_harvests([h for h in harvests if h.get("rainy") == rs["name"]])
        # harvests of OTHER seasons, which refill stocks before a later lean season
        other = [h for h in harvests if h.get("rainy") and h.get("rainy") != rs["name"]]
        for s, e in occurrences(rs["start"], rs["end"], t0, t1):
            h = first_after(linked, s, MAX_LAG) if linked else None
            impacts = []
            if h and crop:
                lv = first_after(crop, h[0] + 1, MAX_LAG)
                if lv:
                    gap = lv[0] - h[1] - 2  # harvests STARTING strictly between the two
                    between = first_after(other, h[1] + 1, gap) if gap >= 0 else None
                    impacts.append(dict(kind="crops", via="harvest", s=lv[0], e=lv[1],
                                        name=lv[2].get("name") or "Lean season",
                                        diluted_by=between[2]["name"] if between else None))
            elif crop:
                lv = first_after(crop, e, MAX_LAG)
                if lv:
                    impacts.append(dict(kind="crops", via="rains", s=lv[0], e=lv[1],
                                        name=lv[2].get("name") or "Lean season",
                                        diluted_by=None))
            if past:
                lv = first_after(past, e, MAX_LAG)
                if lv:
                    impacts.append(dict(kind="livestock", via="rains", s=lv[0], e=lv[1],
                                        name=lv[2].get("name") or "Pastoral lean season",
                                        diluted_by=None))
            out.append(dict(
                season=rs["name"], minor=bool(rs.get("minor")), s=s, e=e,
                harvest=dict(name=h[2]["name"], s=h[0], e=h[1]) if h else None,
                impacts=impacts,
            ))
    return sorted(out, key=lambda o: o["s"])


def slot_overlap(slot_start: int, s: int, e: int) -> int:
    return max(0, min(slot_start + 2, e) - max(slot_start, s) + 1)


def abs_month(ym: str) -> int:
    y, m = map(int, ym.split("-"))
    return y * 12 + m - 1


def ym(t: int) -> str:
    return f"{t // 12}-{t % 12 + 1:02d}"


def region_exposure(units: list, win: list[dict], cal: dict, keys: list[str],
                    phases: tuple[str, ...], axis: tuple[int, int]) -> tuple[dict, dict]:
    """Per calendar region: people counted, and for each rainy-season occurrence the people in
    units under a dry signal in a trimester that covers it (>= 2 of its months, or all of a
    shorter season), split into observed/forecast ("known") and per ENSO scenario.

    `units` are country_exposure()'s (row, weights, flags, hit) tuples. A unit dry in several
    trimesters of the same season counts once for that season.

    Also returns, for the whole country and each month of `axis` (absolute months, inclusive),
    the people in a lean season affected by a dry rainy season. A unit counts once in a month
    however many affected lean seasons cover it (Somalia's pastoral Aug-Oct and cropping Oct-Dec
    lean seasons share October). `known`: fed by an observed or forecast dry season; `obs`: the
    part fed by an observed or partly observed (ERA5) one; `scen[phase]`: known plus the lean
    seasons fed by that ENSO scenario's dry seasons.
    """
    a0, a1 = axis
    n_ax = a1 - a0 + 1
    t0 = abs_month(win[0]["start"])
    t1 = abs_month(win[-1]["start"]) + 2
    slot_abs = [(s, abs_month(s["start"])) for s in win]
    zero = lambda: {k: 0.0 for k in keys}  # noqa: E731
    out = {}
    for r in cal["regions"]:
        occ = []
        for o in season_links(r, t0, t1):
            need = min(2, o["e"] - o["s"] + 1)
            ov = [s for s, a in slot_abs if slot_overlap(a, o["s"], o["e"]) >= need]
            if not ov:
                continue
            occ.append(dict(o, tris=[s["label"] for s in ov], cats=sorted({s["cat"] for s in ov}),
                            known=zero(), scen={ph: zero() for ph in phases}, _ov=ov,
                            _dry=set()))
        # lean seasons fed by these occurrences: a unit counts once per lean season even when
        # two rainy seasons feed it
        imp = {}
        for i, o in enumerate(occ):
            for im in o["impacts"]:
                key = (im["kind"], im["s"], im["e"])
                imp.setdefault(key, dict(im, feeds=[], known=zero(),
                                         scen={ph: zero() for ph in phases}))["feeds"].append(i)
        out[r["id"]] = dict(totals=zero(), units=0, occ=occ, imp=imp)
    out["unassigned"] = dict(totals=zero(), units=0, occ=[], imp={})
    series = lambda: {k: [0.0] * n_ax for k in keys}  # noqa: E731
    monthly = dict(known=series(), obs=series(), scen={ph: series() for ph in phases})

    def add(target: dict, months: set[int], w: dict) -> None:
        for t in months:
            for k in keys:
                target[k][t - a0] += w[k] or 0

    for row, w, f, _ in units:
        rid = region_of(row["pcode"], cal.get("assign") or {})
        R = out.get(rid) or out["unassigned"]
        R["units"] += 1
        for k in keys:
            R["totals"][k] += w[k] or 0
        dry_known, dry_obs, dry_scen = set(), set(), {ph: set() for ph in phases}
        for i, o in enumerate(R["occ"]):
            if any(f[s["key"]]["dry"] for s in o["_ov"] if s["cat"] in ("observed", "ending")):
                dry_obs.add(i)
            dry_tris = [s["key"] for s in o["_ov"] if s["cat"] != "scenario" and f[s["key"]]["dry"]]
            o["_dry"].update(dry_tris)
            if dry_tris:
                dry_known.add(i)
                for k in keys:
                    o["known"][k] += w[k] or 0
            for ph in phases:
                if any(f[s["key"]]["dry"][ph] for s in o["_ov"] if s["cat"] == "scenario"):
                    dry_scen[ph].add(i)
                    for k in keys:
                        o["scen"][ph][k] += w[k] or 0
        m_known, m_obs, m_scen = set(), set(), {ph: set() for ph in phases}
        for im in R["imp"].values():
            feeds = set(im["feeds"])
            span = set(range(max(im["s"], a0), min(im["e"], a1) + 1))
            if dry_known & feeds:
                m_known |= span
                for k in keys:
                    im["known"][k] += w[k] or 0
            if dry_obs & feeds:
                m_obs |= span
            for ph in phases:
                if dry_scen[ph] & feeds:
                    m_scen[ph] |= span
                    for k in keys:
                        im["scen"][ph][k] += w[k] or 0
        add(monthly["known"], m_known, w)
        add(monthly["obs"], m_obs, w)
        for ph in phases:
            add(monthly["scen"][ph], m_known | m_scen[ph], w)
    rnd = lambda d: {k: round(v) for k, v in d.items()}  # noqa: E731
    for R in out.values():
        R["totals"] = rnd(R["totals"])
        for o in R["occ"]:
            # the observed / forecast trimesters with a dry signal in at least one unit: a long
            # season can be flagged on its first trimester alone, and the page says which
            o["dry_tris"] = [s["label"] for s in o.pop("_ov") if s["key"] in o["_dry"]]
            o.pop("_dry")
            o["known"] = rnd(o["known"])
            o["scen"] = {ph: rnd(v) for ph, v in o["scen"].items()}
            o["s"], o["e"] = ym(o["s"]), ym(o["e"])
            if o["harvest"]:
                o["harvest"]["s"], o["harvest"]["e"] = ym(o["harvest"]["s"]), ym(o["harvest"]["e"])
            for im in o["impacts"]:
                im["s"], im["e"] = ym(im["s"]), ym(im["e"])
        R["imp"] = [dict(im, s=ym(im["s"]), e=ym(im["e"]), known=rnd(im["known"]),
                         scen={ph: rnd(v) for ph, v in im["scen"].items()},
                         feeds=[R["occ"][i]["season"] + " " + R["occ"][i]["s"][:4]
                                for i in im["feeds"]])
                    for im in sorted(R["imp"].values(), key=lambda x: x["s"])]
    if not out["unassigned"]["units"]:
        out.pop("unassigned")
    # all-zero series are left out (most countries have no affected lean season in most keys)
    sparse = lambda d: {k: [round(x) for x in v] for k, v in d.items() if any(v)}  # noqa: E731
    monthly = dict(known=sparse(monthly["known"]), obs=sparse(monthly["obs"]),
                   scen={ph: sparse(v) for ph, v in monthly["scen"].items()})
    return out, monthly
