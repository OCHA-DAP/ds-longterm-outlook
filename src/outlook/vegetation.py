"""Vegetation signal for the seasons that have ended or are under way (JRC ASAP biomass).

Rainfall (SEAS5) is used only for what is still forecast. Once a season is observed, the
question is what it did to crops and pasture, so a trimester that has ended or is under way is
judged on biomass: ASAP's cumulative FPAR anomaly since the start of the season (zFPARc), read
at the last dekad of the trimester that has a value (the latest dekad for a trimester still
under way). A unit is dry when that value ranks in the lowest third of the unit's own record at
the same dekad, VEG_FIRST_YEAR to last year -- the same 1-in-3-year rule as the SEAS5 forecast
(Weibull plotting position, rp = 1/p). ASAP's "during growing cycle" series has a value in
most dekads of the year, so it does not by itself keep the dry season out: exposure.unit_flags
judges a trimester only when it is part of the unit's rainy season (the ERA5 climatological
rainy-season test, as before) and is not the season's first rainy trimester. ASAP's cropland
mask includes irrigated land, so irrigated areas (Pakistan, Bangladesh) rarely show poor biomass
in a dry year.

Our admin units are matched to ASAP's units by area -- GAUL2 where ASAP has them (49
countries), GAUL1 elsewhere (data/asap_units.csv, written by
pipeline/fetch_asap_units.py), and both cropland and rangeland are read. A unit is dry when at
least half of the vegetated area that could be judged is dry: each (ASAP unit, land cover) pair
weighs the share of our unit inside that ASAP unit times the share of that ASAP unit's area under
that land cover (ASAP's own km2_crop / km2_range).

Caveat (KB analysis/asap-indicator-trends.md): zFPARc on rangeland has a positive long-term trend
in places (South Sudan +0.5 z/decade), so recent years rank higher in their own record and a dry
signal is harder to reach than it was.
"""

import csv
import logging
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from outlook import config, sources

log = logging.getLogger(__name__)


def load_units() -> dict[str, list[dict]]:
    """pcode -> [{level, asap0, asap, share of our unit, crop / range share of the ASAP unit}]."""
    out: dict[str, list] = {}
    with open(config.ROOT / "data" / "asap_units.csv") as fh:
        for r in csv.DictReader(fh):
            out.setdefault(r["pcode"], []).append(dict(
                level=int(r["level"]), asap0=int(r["asap0_id"]), asap=int(r["asap_id"]),
                share=float(r["share"]), crop=float(r["crop_frac"]),
                range=float(r["range_frac"])))
    return out


def load_series(units: dict, refresh: bool, workers: int = 3) -> dict:
    """(class, level, asap unit id) -> (dates as YYYYMMDD ints, values), sorted by date, for
    every ASAP country and level the matches use."""
    need = sorted({(m["asap0"], m["level"]) for ms in units.values() for m in ms})
    jobs = [(a, lv, cls, cid) for a, lv in need for cls, cid in config.ASAP_CLASSES.items()]

    def one(job):
        a, lv, cls, cid = job
        try:
            return job, sources.asap_biomass(a, cid, lv, refresh)
        except Exception as e:  # one country failing should not stop the build
            log.warning("ASAP biomass country %s level %s %s: %s", a, lv, cls, e)
            return job, None

    out = {}
    with ThreadPoolExecutor(workers) as ex:  # ASAP's export is slow; three at a time
        for (_, lv, cls, _), df in ex.map(one, jobs):
            if df is None:
                continue
            for rid, g in df.groupby("region_id"):
                g = g.sort_values("date")
                out[(cls, lv, int(rid))] = (g["date"].to_numpy(np.int64),
                                            g["value"].to_numpy(float))
    log.info("ASAP biomass: %s (land cover, unit) series from %s country-levels", len(out),
             len(need))
    return out


def _months(slot: dict) -> list[int]:
    """The trimester's three months as YYYYMM ints."""
    y, m = map(int, slot["start"].split("-"))
    out = []
    for k in range(3):
        t = y * 12 + m - 1 + k
        out.append((t // 12) * 100 + t % 12 + 1)
    return out


def judge(series: tuple, slot: dict, this_year: int) -> dict | None:
    """The slot's value (last dekad with one) and its rank against the same dekad in past years."""
    dates, vals = series
    months = set(_months(slot))
    pick = [i for i, d in enumerate(dates) if d // 100 in months]
    if not pick:
        return None
    i = pick[-1]
    d, v = int(dates[i]), float(vals[i])
    md, yr = d % 10000, d // 10000
    hist = vals[(dates % 10000 == md) & (dates // 10000 >= config.VEG_FIRST_YEAR)
                & (dates // 10000 < min(yr, this_year))]
    if len(hist) < config.VEG_N_MIN:
        return None
    p = (np.sum(hist < v) + 1) / (len(hist) + 1)
    return dict(z=round(v, 2), dekad=d, rp=round(1 / p, 1), dry=bool(1 / p >= config.RP_MIN),
                n=int(len(hist)))


def matched(units: dict, series: dict) -> set[str]:
    """Our units with at least one (ASAP unit, land cover) pair that has area and a series."""
    return {pc for pc, ms in units.items()
            if any(m["share"] * m[lc] > 0 and (lc, m["level"], m["asap"]) in series
                   for m in ms for lc in config.ASAP_CLASSES)}


def flags(rows: list[dict], win: list[dict], units: dict, series: dict,
          this_year: int) -> dict:
    """(pcode, slot key) -> {dry, rp, z, dekad, lc, share} for the observed and in-season slots:
    rp, z and dekad of the largest judged (ASAP unit, land cover); share = the dry part of the
    judged area."""
    slots = [s for s in win if s["cat"] in ("observed", "ending")]
    out = {}
    for r in rows:
        pc = r["pcode"]
        matches = units.get(pc)
        if not matches:
            continue
        for s in slots:
            judged = []
            for mt in matches:
                for lc in config.ASAP_CLASSES:
                    wt = mt["share"] * mt[lc]
                    ser = series.get((lc, mt["level"], mt["asap"])) if wt > 0 else None
                    j = judge(ser, s, this_year) if ser else None
                    if j:
                        judged.append((wt, lc, j))
            if not judged:
                continue
            tot = sum(wt for wt, _, _ in judged)
            dry_share = sum(wt for wt, _, j in judged if j["dry"]) / tot
            # what is shown with the result (rank, land cover) comes from the side that decides it
            side = [x for x in judged if x[2]["dry"] == (dry_share >= 0.5)]
            wt, lc, main = max(side, key=lambda x: x[0])
            out[(pc, s["key"])] = dict(dry=dry_share >= 0.5, rp=main["rp"], z=main["z"],
                                       dekad=main["dekad"], lc=lc, n=main["n"],
                                       share=round(dry_share, 2))
    return out
