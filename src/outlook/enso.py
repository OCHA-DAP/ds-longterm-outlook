"""ENSO scenarios for the seasons beyond SEAS5's reach.

Deliberately simple: for each admin unit and trimester, how often was the ERA5 season in its
own lower tercile in El Nino, neutral and La Nina seasons (phase from CPC's RONI over that
same trimester)? A scenario "tilts dry" where that happened more often than chance (one-sided
binomial test against 1/3). It is a conditional climatology, not a forecast.
"""

from functools import lru_cache
from math import comb

import numpy as np
import pandas as pd

from outlook.config import ENSO_THRESH


def phase(roni: pd.Series) -> pd.Series:
    return pd.Series(
        np.select([roni >= ENSO_THRESH, roni <= -ENSO_THRESH], ["nino", "nina"], "neutral"),
        index=roni.index,
    )


@lru_cache
def binom_sf(k: int, n: int, p: float = 1 / 3) -> float:
    """P(X >= k) for X ~ Binomial(n, p)."""
    return sum(comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k, n + 1))


def composites(obs: pd.DataFrame, roni: pd.DataFrame) -> pd.DataFrame:
    """Per (pcode, trimester, phase): n years, P(lower tercile), P(upper tercile) and the
    one-sided binomial p-value of the lower-tercile count against chance.

    Terciles are each unit's own (pcode x trimester) ranks over the full ERA5 record, so the
    detrended log values need no back-transform. Seasons without a RONI value (none: RONI
    starts 1950, ERA5 here 1981) would drop out.
    """
    df = obs.merge(roni, on=["trimester", "season_year"], how="inner")
    df["phase"] = phase(df["roni"])
    g = df.groupby(["pcode", "trimester"])["obs_mean"]
    pct = g.rank(pct=True, method="average")
    df["lower"] = pct <= 1 / 3
    df["upper"] = pct > 2 / 3
    out = (
        df.groupby(["pcode", "trimester", "phase"])
        .agg(n=("lower", "size"), k=("lower", "sum"), p_dry=("lower", "mean"),
             p_wet=("upper", "mean"))
        .reset_index()
    )
    out["p_binom"] = [binom_sf(int(k), int(n)) for k, n in zip(out["k"], out["n"], strict=True)]
    years = df.groupby(["pcode", "trimester"]).size().rename("years").reset_index()
    return out.merge(years, on=["pcode", "trimester"])
