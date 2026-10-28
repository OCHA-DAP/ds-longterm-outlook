"""Paths, thresholds and source locations shared by the build."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "cache"
BUILD = ROOT / "build"

# Dry-signal rule. Same thresholds as the ds-seas5-skill Forecast x HNRP tab, so a unit
# flagged here is a unit flagged there.
R_MIN = 0.30  # Pearson r of SEAS5 vs ERA5 for that slot: moderate skill or better
RP_MIN = 3.0  # drought return period: 1-in-3-year dry or worse (the lower tercile)
RAINY_SHARE = 0.15  # trimester carries >= 15% of annual rain (ds-seas5-skill APP_TRIMESTER_SHARE)

# ENSO scenarios for the part of the window SEAS5 does not reach
ENSO_THRESH = 0.5  # RONI >= +0.5 El Nino, <= -0.5 La Nina
# Last season_year of ERA5 the composites use. 2026 is excluded: its seasons are incomplete or
# ERA5T, and ERA5 read the 2026 Sahel season as record-dry (see ERA5_DRY_2026). Including it
# lifted ~20 Sahel/Sudan units over the El Nino dry test on its own. Raise it once a year,
# when the year's ERA5 is final and has been checked against CHIRPS.
OBS_LAST_YEAR = 2025
SCENARIO_N_MIN = 8  # a phase needs at least this many ERA5 years for that unit and season
SCENARIO_ALPHA = 0.05  # dry tilt = more lower-tercile years than chance (one-sided binomial
# test against 1/3). A bare "at least half the years" rule flags about half of all units in
# some scenario season by chance alone with ~12 years per phase.
PHASES = ("nino", "neutral", "nina")

OUTLOOK_MONTHS = 12  # the window runs from the issuance month for this many months
SEAS5_MAX_LEAD = 4  # leads 0..4 are fully forecast; -2/-1 are in-season (partly observed)
# Fully observed trimesters (ERA5) kept at the front of the window: the six ending in the six
# months before the issuance month (leads -8..-3). Without them a rainy season that ended a month
# ago drops out of the window before its harvest is in (Sudan's Jun-Sep season in the November
# run); six keeps a main season assessed until about when the lean season it feeds begins.
# All six are drawn (they all count in "Season ended / ending").
OBSERVED_BACK = 6
OBSERVED_SHOWN = 6

TOP_UNITS = 12  # admin units listed per country page

TRIMESTERS: dict[str, list[int]] = {
    "JFM": [1, 2, 3],
    "FMA": [2, 3, 4],
    "MAM": [3, 4, 5],
    "AMJ": [4, 5, 6],
    "MJJ": [5, 6, 7],
    "JJA": [6, 7, 8],
    "JAS": [7, 8, 9],
    "ASO": [8, 9, 10],
    "SON": [9, 10, 11],
    "OND": [10, 11, 12],
    "NDJ": [11, 12, 1],
    "DJF": [12, 1, 2],
}
TRI_BY_START = {months[0]: tri for tri, months in TRIMESTERS.items()}

# Blob locations (dev stage, container "projects")
BLOB = dict(stage="dev", container_name="projects")
SEAS5_SITE = "ds-seas5-skill/site/"
SEAS5_PROCESSED = "ds-seas5-skill/processed/"
HNRP_SITE = "ds-hnrp-mirror/site-data/"
IPC_SITE = "ds-ipc-mirror/site-data/"

# ENSO context shown beside the scenarios. Typed in by hand from the source: re-check and
# re-date at every monthly refresh (the build warns when it is older than 45 days).
IRI_PLUME = dict(
    source="IRI ENSO quick look (model-based plume)",
    url="https://iri.columbia.edu/our-expertise/climate/forecasts/enso/current/",
    published="2026-10-19",
    note=("El Niño 100% through MAM 2027; La Niña and neutral given no probability before "
          "AMJ. The page states no La Niña figure for AMJ–JJA; for JJA it is roughly 12% by "
          "subtraction."),
    summary=("The current El Niño is very strong; models expect it to fade by mid-2027, and "
             "none yet gives a probability for La Niña in the second half of 2027."),
    # season label as IRI/CPC write it (DJF carries its January year), P(El Niño),
    # P(neutral), P(La Niña); None = not stated
    seasons=[
        ("MAM 2027", 100, None, None),
        ("AMJ 2027", 92, None, None),
        ("MJJ 2027", 60, None, None),
        ("JJA 2027", 37, 51, None),
    ],
)

# Countries where ERA5 is known to overstate the 2026 season's deficit. The "season ending"
# slots blend ERA5 (ERA5T at the time) for the observed months. Checked for Chad on
# 2026-10-19: JAS 2026 ERA5 23% of normal (driest on record) vs CHIRPS v2/v3 64-70%. ERA5
# dried the whole belt from Senegal to Sudan that season. Flag, don't drop.
ERA5_DRY_2026 = {"SEN", "GMB", "MRT", "MLI", "BFA", "NER", "NGA", "TCD", "CMR", "SDN"}

# Public HTTP sources
FDW_POPULATION = "https://fdw.fews.net/api/ipcpopulationsize.csv"
RONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt"
