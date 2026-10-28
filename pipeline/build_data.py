"""Assemble build/outlook.json: one record per country, every source side by side.

    uv run python pipeline/build_data.py [--refresh]

Reads only already-published team products (dev blob) + two public HTTP series; see
src/outlook/sources.py. Prints the hand-check figures at the end — compare them with the
sources before building the page.
"""

import argparse
import json
import logging
import math
import re
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd
import pycountry

from outlook import calendar, config, enso, exposure, sources

log = logging.getLogger("build_data")

HNRP_TYPES = ("Humanitarian needs and response plan", "Humanitarian response plan")
NAME_FIX = {"COD": "DR Congo", "PSE": "Occupied Palestinian Territory", "BOL": "Bolivia",
            "VEN": "Venezuela", "SYR": "Syria", "IRN": "Iran", "TZA": "Tanzania",
            "MMR": "Myanmar", "LAO": "Laos", "PRK": "DPR Korea", "CIV": "Côte d'Ivoire"}


def _num(x):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    return float(x)


def iso3_of(iso2: str) -> str | None:
    c = pycountry.countries.get(alpha_2=iso2)
    return c.alpha_3 if c else None


# ---- other organisations' figures -----------------------------------------------------------


def fews_tables(fdw: pd.DataFrame, today: date) -> tuple[dict, dict, dict]:
    fdw = fdw.copy()
    fdw["iso3"] = fdw["country_code"].map(iso3_of)
    fdw = fdw[fdw["iso3"].notna()]

    def rng(r):
        hi = _num(r["high_value"])
        return dict(low=_num(r["low_value"]), high=None if hi and hi >= 9e9 else hi,
                    label=r["population_range"])

    peak = {}
    pn = fdw[(fdw["scenario"] == "PN")
             & (fdw["source_document"] == "Annual Peak Food Assistance Needs")]
    for iso3, g in pn.groupby("iso3"):
        r = g.sort_values(["projection_start", "created"]).iloc[-1]
        if r["projection_end"] < f"{today:%Y-%m-%d}":
            continue  # that country's latest edition has run out (e.g. Ethiopia's 2024 one)
        peak[iso3] = dict(
            **rng(r), edition=[r["projection_start"][:7], r["projection_end"][:7]],
            pct=[_num(r["pct_population_min"]), _num(r["pct_population_max"])],
            peak=[str(r["peak_start_date"])[:7], str(r["peak_end_date"])[:7]]
            if isinstance(r["peak_start_date"], str) else None,
            vs_last_year=r["comparison_to_one_year_ago"]
            if isinstance(r["comparison_to_one_year_ago"], str) else None,
            vs_5yr=r["comparison_to_five_year_average"]
            if isinstance(r["comparison_to_five_year_average"], str) else None,
            areas=r["areas_of_highest_concern"]
            if isinstance(r["areas_of_highest_concern"], str) else None,
        )
    faob = {}
    fb = fdw[fdw["source_document"] == "Food Assistance Outlook Brief"]
    for iso3, g in fb.groupby("iso3"):
        rep = g["reporting_date"].max()
        g = g[g["reporting_date"] == rep]
        cs = g[g["scenario"] == "CS"].sort_values("projection_start")
        ml = g[g["scenario"] == "ML"].sort_values("projection_start")
        if ml.empty:
            continue
        faob[iso3] = dict(
            brief=rep[:7],
            current=dict(month=cs.iloc[-1]["projection_start"][:7], **rng(cs.iloc[-1]))
            if not cs.empty else None,
            projected=[dict(month=r["projection_start"][:7], **rng(r)) for _, r in ml.iterrows()],
        )
    # FEWS NET's own pages for the two briefs (fews.net/global/<product>/<month>-<year>)
    def brief_url(product: str, ym_: str) -> str:
        y, m = map(int, ym_.split("-"))
        return f"https://fews.net/global/{product}/{MONTHS_EN[m - 1]}-{y}"

    editions = pd.Series([v["edition"][0] for v in peak.values()]).value_counts()
    faob_brief = str(fb["reporting_date"].max())[:7]
    latest = dict(peak_created=str(pn["created"].max())[:10], faob_brief=faob_brief,
                  peak_url=brief_url("peak-food-assistance-needs-outlook-brief",
                                     editions.index[0]) if len(editions) else None,
                  faob_url=brief_url("food-assistance-outlook-brief", faob_brief))
    return peak, faob, latest


IPC_OLDEST = "2022-01"  # older national analyses (ZWE 2020, AGO 2021, PHL 2012...) are not shown


def ipc_table(nat: pd.DataFrame, today: date) -> dict:
    out = {}
    nat = nat[(nat["level"] == "national") & (nat["analysis_date"] >= IPC_OLDEST)]
    for iso3, g in nat.groupby("iso3"):
        a = g["analysis_date"].max()
        g = g[g["analysis_date"] == a]
        periods = []
        for (t, s, e), h in g.groupby(["period_type", "reference_period_start",
                                       "reference_period_end"]):
            ph = h.groupby("phase")["population"].sum()
            periods.append(dict(t=t, s=s[:7], e=e[:7], p3=_num(ph.get("3+")),
                                p4=_num(ph.get("4", 0) + ph.get("5", 0)),
                                p5=_num(ph.get("5")), all=_num(ph.get("all"))))
        periods.sort(key=lambda p: p["s"])
        entries = [dict(a=a[:7], t=p["t"], s=p["s"], e=p["e"], tot=p["all"]) for p in periods]
        shown = exposure.pick_ipc_period(entries, today)
        out[iso3] = dict(analysis=a[:7], periods=periods,
                         shown=dict(t=shown[1], s=shown[2], e=shown[3]) if shown else None)
    return out


def hnrp_table(plans: list[dict], this_year: int) -> dict:
    out = {}
    for p in plans:
        if not p.get("iso3") or p["plan_type"] not in HNRP_TYPES or p["year"] > this_year:
            continue
        cur = out.get(p["iso3"])
        if cur and (cur["year"], cur["in_need"] or 0) >= (p["year"], p["in_need"] or 0):
            continue
        req = p["revised_requirements"] or p["orig_requirements"] or None
        out[p["iso3"]] = dict(
            plan_id=p["plan_id"], code=p["code"], name=p["name"], year=p["year"],
            in_need=p["in_need"],
            requirements=req, funding=p["funding_total"],
            coverage=(p["funding_total"] or 0) / req if req else None,
        )
    return out


# ---- our exposure --------------------------------------------------------------------------


def observed_ranks(obs: pd.DataFrame, wanted: set[tuple[str, int]]) -> dict:
    """(pcode, trimester, season_year) -> {rp, pct} for the observed (ended) window slots:
    the season's ERA5 value against that unit's record up to OBS_LAST_YEAR, Weibull plotting
    position (non-exceedance (k+1)/(n+1), k = record seasons drier than it), dry RP = 1/p."""
    out = {}
    clim = obs[obs["season_year"] <= config.OBS_LAST_YEAR]
    for tri, yr in wanted:
        cur = obs[(obs["trimester"] == tri) & (obs["season_year"] == yr)].set_index("pcode")
        if cur.empty:
            log.warning("no ERA5 value yet for observed slot %s %s", tri, yr)
            continue
        ref = clim[clim["trimester"] == tri].groupby("pcode")["obs_mean"].apply(np.sort)
        for pc, v in cur["obs_mean"].items():
            if pc not in ref.index:
                continue
            r = ref[pc]
            p = (np.searchsorted(r, v, side="left") + 1) / (len(r) + 1)
            out[(pc, tri, yr)] = dict(rp=round(1 / p, 1), pct=round(100 * p, 1))
    return out


def era5_lookups(rows: list[dict], win: list[dict], refresh: bool):
    """Rainy table, ENSO composites and observed-season ranks for every unit the payloads
    hold (and the prefixes admin-3 units resolve through)."""
    wanted = {(s["tri"], s["season_year"]) for s in win if s["cat"] == "observed"}
    want = set()
    for r in rows:
        p = r["pcode"]
        want |= {p[:k] for k in range(2, len(p) + 1)}
    rn = sources.roni(refresh)
    rainy, comp, known, observed = {}, {}, set(), {}
    years = [9999, 0]  # season_year range the composites actually use
    for level in (1, 2):
        clim = sources.monthly_clim(level, refresh)
        clim = clim[clim["pcode"].isin(want)]
        obs_all = sources.seasonal_obs(level, set(clim["pcode"]), refresh)
        observed |= observed_ranks(obs_all, wanted)
        obs = obs_all[obs_all["season_year"] <= config.OBS_LAST_YEAR]
        years = [min(years[0], int(obs["season_year"].min())),
                 max(years[1], int(obs["season_year"].max()))]
        c = enso.composites(obs, rn)
        rainy |= exposure.rainy_table(clim)
        comp |= {(r.pcode, r.trimester, r.phase): dict(n=int(r.n), p_dry=float(r.p_dry),
                                                       p_wet=float(r.p_wet),
                                                       p_binom=float(r.p_binom))
                 for r in c.itertuples()}
        known |= set(obs["pcode"]) & set(clim["pcode"])
        log.info("adm%s: %s units with ERA5 history, %s composites", level,
                 obs["pcode"].nunique(), len(c))
    return rainy, comp, known, rn, years, observed


KEYS = ["w4", "w3", "wall"]
MONTHS_EN = ["january", "february", "march", "april", "may", "june", "july", "august",
             "september", "october", "november", "december"]
MONTH_NAMES = {  # title month words of IPC/CH analyses (EN/FR/ES/PT)
    1: ("january", "janvier", "enero", "janeiro"),
    2: ("february", "février", "febrero", "fevereiro"),
    3: ("march", "mars", "marzo", "março"),
    4: ("april", "avril", "abril"),
    5: ("may", "mai", "mayo", "maio"),
    6: ("june", "juin", "junio", "junho"),
    7: ("july", "juillet", "julio", "julho"),
    8: ("august", "août", "agosto"),
    9: ("september", "septembre", "septiembre", "setembro"),
    10: ("october", "octobre", "octubre", "outubro"),
    11: ("november", "novembre", "noviembre", "novembro"),
    12: ("december", "décembre", "diciembre", "dezembro"),
}


def ipc_link(links: list[dict] | None, nat: dict | None) -> dict | None:
    """The ipcinfo page of the analysis shown: the registry entry whose title names the
    analysis month (whole words, 3-letter abbreviations too) and year; failing that, one month
    either side (CH October analyses are titled "November"); else the country's latest analysis.
    `exact` says which."""
    if not links:
        return None
    if nat:
        y, m = map(int, nat["analysis"].split("-"))

        def names(mm: int) -> set[str]:
            mm = (mm - 1) % 12 + 1
            return set(MONTH_NAMES[mm]) | {MONTHS_EN[mm - 1][:3]}

        def hit(title: str, mm: int, yy: int) -> bool:
            words = set(re.findall(r"[a-zéèûçã]+", title.lower()))
            return str(yy) in title and bool(words & names(mm))

        for lk in links:
            if hit(lk["title"], m, y):
                return dict(lk, exact=True)
        for lk in links:
            for d in (1, -1):
                mm, yy = m + d, y + (1 if m + d > 12 else -1 if m + d < 1 else 0)
                if hit(lk["title"], mm, yy):
                    return dict(lk, exact=True)
    return dict(links[0], exact=False)


def calendar_card(cal: dict | None) -> dict | None:
    """What the page draws of a calendar: regions with their raw month spans + the source."""
    if not cal:
        return None
    keep = ("id", "name", "where", "livelihood", "rainy", "harvest", "lean", "planting", "notes")
    return dict(image_url=cal["image_url"], page_url=cal["page_url"],
                axis_start_month=cal.get("axis_start_month", 1),
                regions=[{k: r.get(k) for k in keep} for r in cal["regions"]],
                assign_method=(cal.get("assign") or {}).get("method"),
                notes=cal.get("transcriber_notes"))


def cpc_label(tri: str, season_year: int) -> str:
    """Back to CPC's labelling (DJF carries its January year)."""
    return f"{tri} {season_year + 1 if tri == 'DJF' else season_year}"


def _r(x):
    return None if x is None else round(x)


def summarise(exp: dict, win: list[dict], keys: list[str]) -> dict:
    totals = {k: _r(sum(w[k] or 0 for _, w, _, _ in exp["units"])) for k in keys}
    return dict(
        totals=totals,
        blind={g: {k: _r(v) for k, v in b.items()} for g, b in exp["blind"].items()},
        top={k: exposure.top_units(exp["units"], win, k) for k in keys},
        cats={c: {k: _r(v[k]) for k in keys} for c, v in exp["cats"].items()},
        slots={key: {k: _r(v) for k, v in s.items()} for key, s in exp["slots"].items()},
        weighted_units=len(exp["units"]),
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="re-read every cached input")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("azure").setLevel(logging.WARNING)
    today = date.today()

    tag = sources.seas5_tag()
    low = sources.seas5_payload("hnrp_drought_low.json", tag, args.refresh)
    ipcp = sources.seas5_payload("hnrp_drought_ipc.json", tag, args.refresh)
    iy, im = low["issued_year"], low["issued_month"]
    assert (iy, im) == (ipcp["issued_year"], ipcp["issued_month"]), "payload issuances differ"
    win = exposure.window(iy, im)
    payload_tris = set(low["trimesters"])
    seas5_slots = {s["tri"] for s in win if s["cat"] in ("ending", "forecast")}
    assert seas5_slots == payload_tris, f"window {seas5_slots} != payload {payload_tris}"

    rainy, comp, known, rn, obs_years, observed = era5_lookups(low["rows"] + ipcp["rows"], win,
                                                               args.refresh)

    hmeta, hw = exposure.hnrp_weights(low["rows"])
    imeta, iw = exposure.ipc_weights(ipcp["rows"], today)
    plans = sources.hnrp_plans(args.refresh)
    nat, ipc_gen = sources.ipc_national(args.refresh)
    fdw = sources.fdw_population(args.refresh)
    peak, faob, fews_latest = fews_tables(fdw, today)
    ipc_nat = ipc_table(nat, today)
    hnrp = hnrp_table(plans["plans"], today.year)

    names = {}
    for r in low["rows"] + ipcp["rows"]:
        names.setdefault(r["iso3"], r.get("country"))
    low_by = {}
    for r in low["rows"]:
        low_by.setdefault(r["iso3"], []).append(r)
    ipc_by = {}
    for r in ipcp["rows"]:
        ipc_by.setdefault(r["iso3"], []).append(r)

    cals = calendar.load(config.ROOT / "data" / "fewsnet_calendars")
    fews_pages = json.loads((config.ROOT / "data" / "fewsnet_pages.json").read_text())
    ipc_links = json.loads((config.ROOT / "data" / "ipc_links.json").read_text())

    recent_ipc = {k for k, v in ipc_nat.items() if v["analysis"] >= "2024-01"}
    recent_hnrp = {k for k, v in hnrp.items() if v["year"] >= today.year - 1}
    universe = recent_hnrp | set(peak) | recent_ipc | set(hmeta) | set(imeta)
    countries = []
    for iso3 in sorted(universe):
        # English names: the payloads carry the boundary file's local-language names
        # ("Tchad", "Moçambique"), so they are only the last resort
        c = pycountry.countries.get(alpha_3=iso3)
        name = NAME_FIX.get(iso3) or (
            (getattr(c, "common_name", None) or c.name) if c else names.get(iso3) or iso3)
        # only a current plan (this year or last) is shown: the mirror holds every plan back
        # to 2004 and e.g. Senegal's latest HRP is from 2017
        plan = hnrp.get(iso3) if iso3 in recent_hnrp else None
        rec = dict(iso3=iso3, name=name, hnrp=plan, ipc=ipc_nat.get(iso3),
                   fews_peak=peak.get(iso3), fews_faob=faob.get(iso3), exposure={},
                   calendar=calendar_card(cals.get(iso3)),
                   links=dict(fews=fews_pages.get(iso3),
                              ipc=ipc_link(ipc_links.get(iso3), ipc_nat.get(iso3))))
        cal = cals.get(iso3)
        if iso3 in hmeta:
            e = exposure.country_exposure(low_by[iso3], hw, win, comp, rainy, known, observed)
            pbs = [0.0] * 5
            for _, w, _, _ in e["units"]:
                if w["pb"]:
                    pbs = [a + b for a, b in zip(pbs, w["pb"], strict=True)]
            rec["exposure"]["hnrp"] = dict(
                **hmeta[iso3], pbs=[_r(x) for x in pbs] if hmeta[iso3]["mode"] == "pbs" else None,
                **summarise(e, win, ["w4", "w3", "wall"]),
                regions=calendar.region_exposure(e["units"], win, cal, KEYS, config.PHASES)
                if cal else None)
        if iso3 in imeta:
            e = exposure.country_exposure(ipc_by[iso3], iw, win, comp, rainy, known, observed)
            # the SEAS5 payload can hold an older analysis than the IPC mirror today (AFG:
            # payload 2025-03, mirror 2026-09): the page says which one the exposure uses
            rec["exposure"]["ipc"] = dict(**imeta[iso3], **summarise(e, win, ["w4", "w3", "wall"]),
                                          regions=calendar.region_exposure(
                                              e["units"], win, cal, KEYS, config.PHASES)
                                          if cal else None,
                                          older_than_national=bool(
                                              rec["ipc"] and imeta[iso3]["analysis"]
                                              < rec["ipc"]["analysis"]))
        rec["era5_caveat"] = iso3 in config.ERA5_DRY_2026
        countries.append(rec)

    lastr = rn.iloc[-1]
    iri_age = (today - date.fromisoformat(config.IRI_PLUME["published"])).days
    if iri_age > 45:
        log.warning("IRI_PLUME in config.py is %s days old: re-check and re-date it", iri_age)
    out = dict(
        meta=dict(
            built=datetime.now(UTC).isoformat(timespec="minutes"),
            issuance=dict(label=low["issued_label"], year=iy, month=im, tag=tag),
            window=win,
            thresholds=dict(r_min=config.R_MIN, rp_min=config.RP_MIN,
                            rainy_share=config.RAINY_SHARE, enso=config.ENSO_THRESH,
                            scen_alpha=config.SCENARIO_ALPHA, scen_n=config.SCENARIO_N_MIN),
            sources=dict(hnrp_generated=plans.get("generated_at"), ipc_generated=ipc_gen,
                         fdw_fetched=f"{today}", **fews_latest),
            enso=dict(latest=dict(season=cpc_label(lastr["trimester"], int(lastr["season_year"])),
                                  roni=float(lastr["roni"])), iri=config.IRI_PLUME,
                      obs_years=obs_years),
        ),
        countries=countries,
    )
    config.BUILD.mkdir(exist_ok=True)
    path = config.BUILD / "outlook.json"
    path.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False))
    log.info("wrote %s (%.1f MB, %s countries)", path, path.stat().st_size / 1e6, len(countries))

    # ---- hand-check figures ----
    by = {c["iso3"]: c for c in countries}
    for iso3 in ("SDN", "AFG", "COL", "MWI"):
        c = by.get(iso3)
        if not c:
            continue
        h = c["exposure"].get("hnrp")
        print(f"\n== {iso3} {c['name']}")
        if c["hnrp"]:
            print(f"  plan {c['hnrp']['code']} PiN {c['hnrp']['in_need']:,} cov "
                  f"{(c['hnrp']['coverage'] or 0):.0%}")
        if h:
            print(f"  PbS ({h['mode']} {h['year']}) {h['pbs']}  4+ exposed: {h['cats']}")
        if c["ipc"]:
            print(f"  IPC {c['ipc']['analysis']} shown={c['ipc']['shown']}: "
                  + "; ".join(f"{p['t']} {p['s']}..{p['e']} 3+={p['p3']}"
                              for p in c["ipc"]["periods"]))
        if c["exposure"].get("ipc"):
            x = c["exposure"]["ipc"]
            print(f"  IPC exposure {x['analysis']} {x['period_type']} {x['label']}: {x['cats']}")
        print(f"  FEWS peak {c['fews_peak']}")


if __name__ == "__main__":
    main()
