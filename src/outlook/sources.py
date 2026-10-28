"""Loaders. Every input is read from a product the team already publishes, never re-derived.

- SEAS5 x HNRP / IPC payloads: the ds-seas5-skill site bundle on the dev blob (versioned by
  issuance; `latest.json` names the one the live site shows).
- ERA5 seasonal history per admin unit: ds-seas5-skill's detrended paired series (lead-0 rows).
- HNRP plans + FTS funding: ds-hnrp-mirror site data. IPC/CH national: ds-ipc-mirror site data.
- FEWS NET population estimates (Peak Needs, FAOB): FEWS NET Data Warehouse, public CSV.
- RONI: NOAA CPC relative Nino3.4 index, per 3-month season.

Blob and HTTP reads are cached under cache/; pass refresh=True (build --refresh) to re-read.
"""

import io
import json
import logging
from datetime import date

import ocha_stratus as stratus
import pandas as pd
import pyarrow.parquet as pq
import requests

from outlook.config import (
    BLOB,
    CACHE,
    FDW_POPULATION,
    HNRP_SITE,
    IPC_SITE,
    RONI_URL,
    SEAS5_PROCESSED,
    SEAS5_SITE,
    TRIMESTERS,
)

log = logging.getLogger(__name__)


def _blob_bytes(name: str, cache_name: str, refresh: bool) -> bytes:
    path = CACHE / cache_name
    if path.exists() and not refresh:
        return path.read_bytes()
    log.info("blob dev:projects/%s", name)
    raw = stratus.load_blob_data(name, **BLOB)
    raw = raw if isinstance(raw, bytes) else raw.encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return raw


def _http_text(url: str, cache_name: str, refresh: bool) -> str:
    """Daily cache: a fetch from an earlier day is re-fetched."""
    path = CACHE / f"{date.today():%Y%m%d}_{cache_name}"
    if path.exists() and not refresh:
        return path.read_text(encoding="utf-8-sig")
    log.info("GET %s", url)
    r = requests.get(url, timeout=600)
    r.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return r.content.decode("utf-8-sig")


def seas5_tag(refresh: bool = False) -> str:
    """The issuance the live SEAS5 site shows (YYYY-MM). Never cached: it moves monthly."""
    raw = stratus.load_blob_data(SEAS5_SITE + "latest.json", **BLOB)
    return json.loads(raw)["tag"]


def seas5_payload(name: str, tag: str, refresh: bool = False) -> dict:
    """A Forecast x HNRP payload (hnrp_drought_low.json / hnrp_drought_ipc.json) of one issuance."""
    raw = _blob_bytes(f"{SEAS5_SITE}{tag}/docs/data/{name}", f"seas5/{tag}/{name}", refresh)
    return json.loads(raw)


def monthly_clim(level: int, refresh: bool = False) -> pd.DataFrame:
    """ERA5 full-record monthly mean (mm/day) per pcode: the rainy-season classification input."""
    suffix = "" if level == 0 else f"_adm{level}"
    raw = _blob_bytes(f"{SEAS5_PROCESSED}monthly_clim{suffix}.parquet",
                      f"clim/monthly_clim{suffix}.parquet", refresh)
    return pd.read_parquet(io.BytesIO(raw))


def seasonal_obs(level: int, pcodes: set[str], refresh: bool = False) -> pd.DataFrame:
    """ERA5 seasonal mean per pcode x trimester x season_year (detrended, log space).

    Taken from the lead-0 rows of ds-seas5-skill's paired series (issued in the trimester's
    first month), so each (pcode, trimester, season_year) appears once. season_year follows
    ds-seas5-skill: the year of the trimester's FIRST month (NDJ 2015 = Nov 2015-Jan 2016,
    DJF 2015 = Dec 2015-Feb 2016). The adm2 file is ~560 MB: read in batches, keep only the
    lead-0 rows of the pcodes asked for. All years are returned: callers cut the climatology
    at OBS_LAST_YEAR (config) and read the newest seasons as observations.
    """
    suffix = "" if level == 0 else f"_adm{level}"
    name = f"paired_yearly_detrended{suffix}.parquet"
    path = CACHE / "paired" / name
    if refresh or not path.exists():
        log.info("blob dev:projects/%s%s", SEAS5_PROCESSED, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        cc = stratus.get_container_client(**BLOB)
        with open(path, "wb") as f:
            cc.get_blob_client(SEAS5_PROCESSED + name).download_blob().readinto(f)
    start = {tri: months[0] for tri, months in TRIMESTERS.items()}
    keep = []
    cols = ["pcode", "issued_month", "trimester", "season_year", "obs_mean"]
    for batch in pq.ParquetFile(path).iter_batches(batch_size=1_000_000, columns=cols):
        df = batch.to_pandas()
        df = df[df["pcode"].isin(pcodes)]
        df = df[df["issued_month"] == df["trimester"].map(start)]
        keep.append(df.drop(columns="issued_month"))
    out = pd.concat(keep, ignore_index=True).dropna(subset=["obs_mean"])
    dupes = out.duplicated(["pcode", "trimester", "season_year"]).sum()
    if dupes:
        raise ValueError(f"{name}: {dupes} duplicate (pcode, trimester, season_year) lead-0 rows")
    return out


def hnrp_plans(refresh: bool = False) -> dict:
    return json.loads(_blob_bytes(HNRP_SITE + "plans.json", "hnrp/plans.json", refresh))


def ipc_national(refresh: bool = False) -> pd.DataFrame:
    d = json.loads(_blob_bytes(IPC_SITE + "national.json", "ipc/national.json", refresh))
    return pd.DataFrame(d["rows"], columns=d["columns"]), d.get("generated_at")


def fdw_population(refresh: bool = False) -> pd.DataFrame:
    """Every FEWS NET national population estimate: Peak Needs (PN), FAOB current (CS) and
    most likely (ML). National Phase 3+ ranges only; FEWS NET publishes no area populations."""
    text = _http_text(FDW_POPULATION, "fdw_ipcpopulationsize.csv", refresh)
    return pd.read_csv(io.StringIO(text), low_memory=False)


def roni(refresh: bool = False) -> pd.DataFrame:
    """CPC RONI per 3-month season, re-keyed to ds-seas5-skill's season_year.

    CPC labels a season by the year of its MIDDLE month, so CPC "DJF 2016" = Dec 2015-Feb 2016,
    which ds-seas5-skill calls DJF 2015. Only DJF differs; NDJ's middle month (Dec) is already
    in the first month's year. Checked 2026-10-28 against 3-month means of CPC's monthly
    ERSST v5 Nino3.4: DJF keyed to CPC year - 1 correlates r=0.95 (CPC year: -0.03); NDJ,
    JFM, JAS keyed to the CPC year r=0.91-0.96.
    """
    text = _http_text(RONI_URL, "roni.txt", refresh)
    rows = [ln.split() for ln in text.splitlines()[1:] if ln.strip()]
    df = pd.DataFrame(rows, columns=["trimester", "cpc_year", "roni"])
    df["cpc_year"] = df["cpc_year"].astype(int)
    df["roni"] = df["roni"].astype(float)
    df["season_year"] = df["cpc_year"] - (df["trimester"] == "DJF").astype(int)
    return df[["trimester", "season_year", "roni"]]
