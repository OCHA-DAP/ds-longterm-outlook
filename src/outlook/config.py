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
# ERA5T, and ERA5 read the 2026 Sahel season as record-dry (Chad JAS 2026: 23% of normal against
# CHIRPS's 64-70%). Including it
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
# Fully observed trimesters kept at the front of the window: the six ending in the six
# months before the issuance month (leads -8..-3). Without them a rainy season that ended a month
# ago drops out of the window before its harvest is in (Sudan's Jun-Sep season in the November
# run); six keeps a main season assessed until about when the lean season it feeds begins.
# All six are drawn (they all count in "Season ended / ending").
OBSERVED_BACK = 6
OBSERVED_SHOWN = 6

TOP_UNITS = 12  # admin units listed per country page

# Most months the per-month series can cover, from the first slot of the window. The build
# trims them to the last month with people in an affected lean season (observed, forecast or
# scenario) and warns if a lean season with people runs past this. Long rainy seasons judged on
# an early trimester reach far: southern Nigeria's Feb-Nov rains, covered by the last SEAS5
# trimester, lead to a lean season in May-Aug of the year after next.
AXIS_MONTHS = 36

# Geographic grouping for the all-countries views. Plain geography with Africa split by the
# shape of its seasons (Sahel unimodal, East bimodal, Southern Nov-Mar): not OCHA's regional
# office structure.
REGIONS = {
    "West and Central Africa": "BEN BFA CAF CIV CMR COD CPV GHA GIN GMB GNB LBR MLI MRT NER NGA "
                               "SEN SLE TCD TGO",
    "East Africa": "BDI DJI KEN RWA SDN SOM SSD TZA UGA",
    "Southern Africa": "AGO LSO MDG MOZ MWI NAM SWZ ZMB ZWE",
    "Middle East and North Africa": "IRQ JOR LBN PSE SYR YEM",
    "Asia": "AFG BGD LKA MMR NPL PAK PHL",
    "Latin America and the Caribbean": "BOL COL DOM ECU GTM HND HTI JAM PER SLV VEN",
    "Europe": "UKR",
}
REGION_OF = {iso3: name for name, codes in REGIONS.items() for iso3 in codes.split()}

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


# Public HTTP sources
FDW_POPULATION = "https://fdw.fews.net/api/ipcpopulationsize.csv"
RONI_URL = "https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt"
# JRC ASAP (Anomaly hot Spots of Agricultural Production): the vegetation signal for seasons
# that have ended or are under way. Per admin-2 unit (ASAP's GAUL2 units) and dekad, the
# cumulative FPAR anomaly since the start of the season (zFPARc, MODIS), cropland and rangeland
# "during the growing cycle" -- the biomass indicator ASAP's own warnings use. Endpoint and
# gotchas: KB infrastructure/datasets/jrc-asap.md; client pattern from ds-asap-trends.
ASAP_BASE = "https://agricultural-production-hotspots.ec.europa.eu"
ASAP_BIOMASS = dict(variable_id=240, classesset_id=1, sensor_id=3)  # zFPARc, in season, MODIS
ASAP_CLASSES = {"crop": 1, "range": 2}
VEG_FIRST_YEAR = 2001  # MODIS FPAR starts in 2001
VEG_N_MIN = 10  # a unit needs this many past years at that dekad to be ranked
# Countries whose ASAP units cannot stand for places: no biomass signal, with the reason shown
ASAP_SKIP = {
    "MMR": ("ASAP's admin-1 units for Myanmar are three groupings of the whole country (Region, "
            "State, Union Territory), not places, so they cannot show where biomass is poor"),
}

# national population, latest year per country: the FEWS NET map layer's circle. FEWS NET
# publishes no analysed population; its own percentages use its own population figures, which
# can differ from these (Yemen: about 33M implied, against the World Bank's 42M), so the page shows
# FEWS NET's percentages as published rather than shares of this figure
WB_POPULATION = ("https://api.worldbank.org/v2/country/all/indicator/SP.POP.TOTL"
                 "?format=json&mrnev=1&per_page=400")
