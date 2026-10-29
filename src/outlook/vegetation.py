"""Vegetation signal for the seasons that have ended or are under way (JRC ASAP biomass).

Rainfall (SEAS5) is used only for what is still forecast. Once a season is observed, the
question is what it did to crops and pasture, so a trimester that has ended or is under way is
judged on biomass: ASAP's cumulative FPAR anomaly since the start of the season (zFPARc), read
at the last dekad of the trimester that has a value (the latest dekad for a trimester still
under way). The value is detrended first: a straight line is fitted to the unit's values at the
same dekad, VEG_FIRST_YEAR to last year, and the current value's departure from that line is
ranked against the past years' departures (both scaled by their leverage, so a departure at the
end of the record is not over-read). A unit is dry when that rank is in the lowest third -- the
same 1-in-3-year rule as the SEAS5 forecast (Weibull plotting position, rp = 1/p) -- AND the
season's biomass is below the unit's long-run average (z < 0): a season with above-average
biomass is never called dry, however far below a rising trend it sits.
ASAP's "during growing cycle" series has a value in most dekads of the year, so it does not by
itself keep the dry season out: exposure.unit_flags
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

Why detrend (KB analysis/asap-indicator-trends.md): zFPARc has a long-term trend in places
(South Sudan rangeland +0.5 z/decade). Ranked against the raw record, recent seasons would look
better than they are and a dry signal would be harder to reach each year.
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
    sel = ((dates % 10000 == md) & (dates // 10000 >= config.VEG_FIRST_YEAR)
           & (dates // 10000 < min(yr, this_year)))
    hist, years = vals[sel], (dates[sel] // 10000).astype(float)
    if len(hist) < config.VEG_N_MIN:
        return None
    # linear trend over the past years at this dekad; rank the departure from it. Past
    # departures are in-sample and this year's is one step beyond the fit, so each is scaled
    # by its leverage (studentized): otherwise the current year's spread is ~13% too wide
    n = len(hist)
    slope, icpt = np.polyfit(years, hist, 1)
    res = hist - (icpt + slope * years)
    xbar = years.mean()
    sxx = ((years - xbar) ** 2).sum()
    sigma = np.sqrt((res ** 2).sum() / (n - 2)) if n > 2 else 0.0
    dep = float(v - (icpt + slope * yr))
    if sigma > 0 and sxx > 0:
        h = 1 / n + (years - xbar) ** 2 / sxx
        t_hist = res / (sigma * np.sqrt(1 - h))
        t_new = dep / (sigma * np.sqrt(1 + 1 / n + (yr - xbar) ** 2 / sxx))
    else:  # a flat record: plain departures
        t_hist, t_new = res, dep
    p = (np.sum(t_hist < t_new) + 1) / (n + 1)
    return dict(z=round(v, 2), dep=round(dep, 2), trend=round(float(slope) * 10, 2), dekad=d,
                rp=round(1 / p, 1), dry=bool(1 / p >= config.RP_MIN and v < 0), n=int(n))


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
                                       dep=main["dep"], trend=main["trend"], dekad=main["dekad"],
                                       lc=lc, n=main["n"], share=round(dry_share, 2))
    return out


# ---- FAO ASI, for reference -------------------------------------------------------------------
# FAO GIEWS's Agricultural Stress Index per admin-1 province (GAUL 2015), the share of cropland
# with mean VHI below 35 so far in the season, since 1984. Shown beside the biomass rank in the
# admin-unit table; not part of the rule (cropland only, admin-1 only, no rangeland).


def load_asi_units() -> dict[str, int]:
    """pcode -> FAO ASI province code (data/asi_units.csv, pipeline/fetch_asap_units.py)."""
    path = config.ROOT / "data" / "asi_units.csv"
    if not path.exists():
        return {}
    with open(path) as fh:
        return {r["pcode"]: int(r["fao_code"]) for r in csv.DictReader(fh)}


def load_asi_series(iso3s: set[str], refresh: bool) -> dict[int, tuple]:
    """FAO province code -> (dates as YYYYMMDD ints, ASI values), sorted by date."""
    out = {}
    for iso3 in sorted(iso3s):
        try:
            df = sources.fao_asi(iso3, refresh)
        except Exception as e:  # reference only: never stops the build
            log.warning("FAO ASI %s: %s", iso3, e)
            continue
        for code, g in df.groupby("code"):
            g = g.sort_values("date")
            out[int(code)] = (g["date"].to_numpy(np.int64), g["value"].to_numpy(float))
    return out


def asi_judge(series: tuple, slot: dict, this_year: int) -> dict | None:
    """ASI at the slot's last dekad with a value, ranked against the same dekad in past years:
    rp = 1 / P(as stressed or worse). Not detrended (a bounded share, and reference only)."""
    dates, vals = series
    months = set(_months(slot))
    pick = [i for i, d in enumerate(dates) if d // 100 in months]
    if not pick:
        return None
    i = pick[-1]
    d, v = int(dates[i]), float(vals[i])
    md, yr = d % 10000, d // 10000
    hist = vals[(dates % 10000 == md) & (dates // 10000 >= config.ASI_FIRST_YEAR)
                & (dates // 10000 < min(yr, this_year))]
    if len(hist) < config.VEG_N_MIN:
        return None
    # ties count as "as stressed" (many years are 0%); values under the floor are not ranked,
    # since FAO's between-season values are tiny and rank each other
    p = (np.sum(hist >= v) + 1) / (len(hist) + 1)
    return dict(asi=round(v, 1), rp=round(1 / p, 1) if v >= config.ASI_RANK_MIN else None,
                dekad=d, n=int(len(hist)))


def asi_flags(rows: list[dict], win: list[dict], units: dict[str, int], series: dict,
              this_year: int) -> dict:
    """(pcode, slot key) -> asi_judge() for the observed and in-season slots."""
    slots = [s for s in win if s["cat"] in ("observed", "ending")]
    out = {}
    for r in rows:
        ser = series.get(units.get(r["pcode"]))
        if not ser:
            continue
        for s in slots:
            j = asi_judge(ser, s, this_year)
            if j:
                out[(r["pcode"], s["key"])] = j
    return out
