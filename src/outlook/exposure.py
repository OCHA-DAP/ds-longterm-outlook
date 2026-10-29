"""Who sits under a dry signal, season by season, over the outlook window.

One rule for every season, whatever its source:
  dry = rainy trimester (>= 15% of annual rain) AND a lower-tercile signal
- season ended (lead -8..-3) or under way (lead -2/-1): biomass, not rainfall -- JRC ASAP
  zFPARc in the unit's lowest third at that point of the season (vegetation.py); the first
  rainy trimester of a season is not judged (too early in the cycle);
- SEAS5 forecast (lead 0..4): skill r >= 0.30, forecast percentile < 50, drought return period
  >= 3 years;
- ENSO scenario (lead 5..9, beyond SEAS5): more lower-tercile ERA5 seasons in that ENSO phase
  than chance (one-sided binomial p < 0.05 against 1/3), over >= 8 years of the phase.
"Rainy" is a share-of-annual-rain rule, not a crop calendar.

Weights are the people the source counts in each admin unit: HNRP PiN by JIAF severity
(PbS; class 4+ is the headline) or, where a country has no PbS, its PiN; and IPC/CH phase
populations for one analysis period per country.
"""

from datetime import date

import pandas as pd

from outlook.config import (
    OBSERVED_BACK,
    OBSERVED_SHOWN,
    OUTLOOK_MONTHS,
    PHASES,
    R_MIN,
    RAINY_SHARE,
    RP_MIN,
    SCENARIO_ALPHA,
    SCENARIO_N_MIN,
    SEAS5_MAX_LEAD,
    TOP_UNITS,
    TRI_BY_START,
    TRIMESTERS,
)

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def window(issued_year: int, issued_month: int) -> list[dict]:
    """Every trimester from the observed ones (lead -8..-3) to the last one that ends inside
    the outlook window. Trimester codes repeat across the window (MJJ 2026 and MJJ 2027), so
    every slot carries a unique `key` (its start month) that the flags and totals are keyed by."""
    out = []
    for lead in range(-2 - OBSERVED_BACK, OUTLOOK_MONTHS - 2):
        m0 = (issued_month - 1 + lead) % 12 + 1
        y0 = issued_year + (issued_month - 1 + lead) // 12
        tri = TRI_BY_START[m0]
        months = TRIMESTERS[tri]
        y_end = y0 + (1 if months[-1] < months[0] else 0)
        cat = ("observed" if lead < -2 else "ending" if lead < 0
               else "forecast" if lead <= SEAS5_MAX_LEAD else "scenario")
        label = f"{MONTH_ABBR[months[0] - 1]}–{MONTH_ABBR[months[-1] - 1]} {y_end}"
        start = f"{y0}-{months[0]:02d}"
        out.append(dict(key=start, tri=tri, lead=lead, cat=cat, season_year=y0, label=label,
                        start=start, shown=lead >= -2 - OBSERVED_SHOWN))
    return out


def rainy_table(clim: pd.DataFrame) -> dict[tuple[str, str], bool]:
    """(pcode, trimester) -> rainy, from full-record ERA5 monthly means (ds-seas5-skill rule)."""
    piv = clim.pivot_table(index="pcode", columns="month", values="mean_mm_day")
    piv = piv[piv.notna().sum(axis=1) == 12]
    annual = piv.sum(axis=1)
    out = {}
    for tri, months in TRIMESTERS.items():
        share = piv[months].sum(axis=1) / annual
        for pcode, s in share.items():
            out[(pcode, tri)] = bool(s >= RAINY_SHARE)
    return out


def resolve(pcode: str, known: set[str]) -> str | None:
    """The unit itself if we hold ERA5 history for it, else its longest-prefix parent
    (admin-3 health zones / communes inherit their admin-2's season, like their forecast)."""
    if pcode in known:
        return pcode
    for k in range(len(pcode) - 1, 1, -1):
        if pcode[:k] in known:
            return pcode[:k]
    return None


def seas5_dry(t: dict | None) -> bool:
    return bool(
        t and t.get("rainy") and (t.get("r") or 0) >= R_MIN
        and (t.get("pct") if t.get("pct") is not None else 100) < 50
        and (t.get("rp") or 0) >= RP_MIN
    )


def scenario_dry(c: dict | None) -> bool:
    return bool(c and c["n"] >= SCENARIO_N_MIN and c["p_binom"] < SCENARIO_ALPHA)


# ---- weights --------------------------------------------------------------------------------


def hnrp_weights(rows: list[dict]) -> tuple[dict, dict[str, dict]]:
    """Per country: which weight it uses + per pcode weights {w4, w3, wall}.

    PbS (sevc[year].pb, PiN by JIAF class 1-5) is used at ONE year per country, the latest
    any of its units carries, so vintages never mix; units without that year carry nothing
    (counted in `dropped`). Countries with no PbS at all fall back to the units' PiN (`pin`,
    a different source/vintage: HAPI / Global HNO), with no severity split.
    """
    by_iso: dict[str, list[dict]] = {}
    for r in rows:
        by_iso.setdefault(r["iso3"], []).append(r)
    meta, weights = {}, {}
    for iso3, rs in by_iso.items():
        years = {y for r in rs for y, v in (r.get("sevc") or {}).items() if any(v.get("pb") or [])}
        if years:
            yr = max(years)
            dropped = 0
            for r in rs:
                pb = ((r.get("sevc") or {}).get(yr) or {}).get("pb")
                if not pb:
                    dropped += 1
                    continue
                pb = [x or 0 for x in pb]
                weights[r["pcode"]] = dict(w4=pb[3] + pb[4], w3=pb[2] + pb[3] + pb[4],
                                           wall=sum(pb), pb=pb)
            meta[iso3] = dict(mode="pbs", year=int(yr), dropped=dropped, units=len(rs))
        elif any(r.get("pin") for r in rs):
            yrs = {int(r["ref_year"]) for r in rs if r.get("pin") and r.get("ref_year")}
            for r in rs:
                if r.get("pin"):
                    weights[r["pcode"]] = dict(w4=None, w3=None, wall=r["pin"], pb=None)
            meta[iso3] = dict(mode="pin", year=max(yrs) if yrs else None, dropped=0,
                              units=len(rs))
    return meta, weights


def pick_ipc_period(entries: list[dict], today: date) -> tuple[str, str, str, str] | None:
    """(analysis month, period type, start, end) for a country: its latest analysis; within it
    the period covering today, else the next one to start, else the last one to end.

    One analysis month can hold several periods of the same type (UGA 2026-08: a national
    'current' Jul 2026-Jan 2027 and a Karamoja one Aug-Oct 2026), so ties go to the period
    with the largest analysed population (`tot`), then the earliest start: deterministic,
    and the same rule for the national figure and the exposure."""
    if not entries:
        return None
    latest = max(e["a"] for e in entries)
    tot: dict[tuple[str, str, str], float] = {}
    for e in entries:
        if e["a"] == latest:
            k = (e["t"], e["s"], e["e"])
            tot[k] = tot.get(k, 0) + (e.get("tot") or 0)
    now = f"{today:%Y-%m}"
    rank = sorted(tot, key=lambda c: (-tot[c], c[1], c[2], c[0]))
    cover = [c for c in rank if c[1] <= now <= c[2]]
    future = sorted((c for c in rank if c[1] > now), key=lambda c: (c[1], -tot[c], c[2], c[0]))
    past = sorted(rank, key=lambda c: (c[2], tot[c], c[1], c[0]))
    chosen = cover[0] if cover else future[0] if future else past[-1]
    return (latest, *chosen)


def ipc_weights(rows: list[dict], today: date) -> tuple[dict, dict[str, dict]]:
    by_iso: dict[str, list[dict]] = {}
    for r in rows:
        by_iso.setdefault(r["iso3"], []).append(r)
    meta, weights = {}, {}
    for iso3, rs in by_iso.items():
        pick = pick_ipc_period([e for r in rs for e in (r.get("ipc") or [])], today)
        if not pick:
            continue
        a, t, s0, e0 = pick
        label = None
        for r in rs:
            for e in r.get("ipc") or []:
                if (e["a"], e["t"], e["s"], e["e"]) == (a, t, s0, e0) and e.get("p"):
                    p = [x or 0 for x in e["p"]]
                    # a unit with no population in any phase has no figure for the period: its
                    # population is not part of what the IPC/CH figures cover
                    weights[r["pcode"]] = dict(w4=p[3] + p[4], w3=p[2] + p[3] + p[4],
                                               wall=(e.get("tot") or sum(p)) if sum(p) else 0,
                                               pb=p)
                    label = label or dict(s=e["s"], e=e["e"], label=e["label"])
        if label:
            meta[iso3] = dict(analysis=a, period_type=t, **label)
    return meta, weights


# ---- exposure -------------------------------------------------------------------------------


def unit_flags(row: dict, win: list[dict], comp: dict, rainy: dict, known: set[str],
               obs: dict, veg: dict, asi: dict) -> dict:
    """Per window slot (by key): rainy?, dry? — and for scenario slots, dry per ENSO phase.

    Trimesters that have ended or are under way (observed, ending) are judged on biomass
    (vegetation.flags: JRC ASAP, lowest third of the unit's record), not on rainfall; the ERA5
    rank of an observed trimester is kept as a reference only (era5_rp). The fully forecast
    trimesters are SEAS5's; the scenario trimesters are the ENSO composites."""
    tris = row.get("tris") or {}
    ref = resolve(row["pcode"], known)
    flags = {}
    for s in win:
        if s["cat"] in ("observed", "ending"):
            is_rainy = rainy.get((ref, s["tri"]), False) if ref else False
            # a season's first rainy trimester is read a few dekads into the cycle, too early
            # for biomass to say much: judged only once the trimester before is rainy too
            m0 = int(s["start"][5:7])
            prev = TRI_BY_START[(m0 - 2) % 12 + 1]
            settled = bool(ref and rainy.get((ref, prev), False))
            v = veg.get((row["pcode"], s["key"])) if settled else None
            o = obs.get((ref, s["tri"], s["season_year"])) if ref else None
            flags[s["key"]] = dict(rainy=is_rainy, dry=bool(is_rainy and v and v["dry"]),
                                   rp=v["rp"] if v else None, pct=None, r=None, veg=v,
                                   era5_rp=o["rp"] if o else None,
                                   asi=asi.get((row["pcode"], s["key"])))
        elif s["cat"] == "scenario":
            is_rainy = rainy.get((ref, s["tri"]), False) if ref else False
            flags[s["key"]] = dict(
                rainy=is_rainy,
                dry={ph: is_rainy and scenario_dry(comp.get((ref, s["tri"], ph)))
                     for ph in PHASES},
                p={ph: (comp.get((ref, s["tri"], ph)) or {}).get("p_dry") for ph in PHASES},
            )
        else:
            t = tris.get(s["tri"])
            flags[s["key"]] = dict(rainy=bool(t and t.get("rainy")), dry=seas5_dry(t),
                                   rp=(t or {}).get("rp"), pct=(t or {}).get("pct"),
                                   r=(t or {}).get("r"))
    return flags


def country_exposure(rows: list[dict], weights: dict, win: list[dict], comp: dict,
                     rainy: dict, known: set[str], obs: dict, veg: dict,
                     keys=("w4", "w3", "wall")) -> dict:
    """Exposed weight per slot and per category (union over a category's slots: a unit dry
    in two forecast seasons counts once). Observed (ended) seasons count in "ending"."""
    slots = {s["key"]: {k: 0.0 for k in keys} | {"rainy_" + k: 0.0 for k in keys} for s in win}
    for s in win:
        if s["cat"] == "scenario":
            slots[s["key"]].update({f"{ph}_{k}": 0.0 for ph in PHASES for k in keys})
    cats = {c: {k: 0.0 for k in keys} for c in ("ending", "forecast", "known")}
    cats |= {f"scenario_{ph}": {k: 0.0 for k in keys} for ph in PHASES}
    units = []
    # weight in units that can never be flagged: no SEAS5 trimesters in the payload, no ERA5
    # history to resolve to (so no ENSO scenario), or no ASAP unit to read biomass from (so no
    # signal for seasons ended or under way). It stays in the denominators.
    blind = {g: {k: 0.0 for k in keys} for g in ("seas5", "scenario", "veg")}
    for row in rows:
        w = weights.get(row["pcode"])
        if not w:
            continue
        if not row.get("tris"):
            for k in keys:
                blind["seas5"][k] += w[k] or 0
        if resolve(row["pcode"], known) is None:
            for k in keys:
                blind["scenario"][k] += w[k] or 0
        if row["pcode"] not in veg["matched"]:
            for k in keys:
                blind["veg"][k] += w[k] or 0
        f = unit_flags(row, win, comp, rainy, known, obs, veg["flags"], veg.get("asi", {}))
        hit = {c: False for c in cats}
        for s in win:
            fl = f[s["key"]]
            for k in keys:
                if fl["rainy"] and w[k]:
                    slots[s["key"]]["rainy_" + k] += w[k]
            if s["cat"] == "scenario":
                for ph in PHASES:
                    if fl["dry"][ph]:
                        hit[f"scenario_{ph}"] = True
                        for k in keys:
                            slots[s["key"]][f"{ph}_{k}"] += w[k] or 0
            elif fl["dry"]:
                hit["ending" if s["cat"] == "observed" else s["cat"]] = hit["known"] = True
                for k in keys:
                    slots[s["key"]][k] += w[k] or 0
        for c, h in hit.items():
            if h:
                for k in keys:
                    cats[c][k] += w[k] or 0
        units.append((row, w, f, hit))
    return dict(slots=slots, cats=cats, units=units, blind=blind)


def top_units(units: list, win: list[dict], key: str) -> list[dict]:
    """The most-weighted units with any dry flag, for the country page table."""
    flagged = [u for u in units if any(u[3].values()) and (u[1][key] or 0) > 0]
    flagged.sort(key=lambda u: -(u[1][key] or 0))
    out = []
    for row, w, f, _ in flagged[:TOP_UNITS]:
        seasons = []
        for s in win:
            fl = f[s["key"]]
            if s["cat"] == "scenario":
                phs = [ph for ph in PHASES if fl["dry"][ph]]
                if phs:
                    seasons.append(dict(key=s["key"], cat="scenario", phases=phs))
            elif fl["dry"]:
                v, a = fl.get("veg") or {}, fl.get("asi") or {}
                seasons.append(dict(key=s["key"], cat=s["cat"], rp=fl["rp"], r=fl["r"],
                                    veg=s["cat"] in ("observed", "ending"), n=v.get("n"),
                                    lc=v.get("lc"), z=v.get("z"), dep=v.get("dep"),
                                    trend=v.get("trend"),
                                    era5_rp=fl.get("era5_rp"), asi=a.get("asi"),
                                    asi_rp=a.get("rp"), asi_n=a.get("n"), asi_dekad=a.get("dekad")))
        out.append(dict(name=row.get("name"), parent=row.get("parent"),
                        w4=w["w4"], w3=w["w3"], wall=w["wall"], seasons=seasons))
    return out
