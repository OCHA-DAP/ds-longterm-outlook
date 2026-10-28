# ds-longterm-outlook

A year-ahead outlook per country. Our columns count people in areas with a seasonal dry signal: HNRP
PiN by JIAF severity, or IPC/CH phase populations, in admin units where the season now ending,
the SEAS5 forecast or an ENSO scenario shows a dry signal. They are shown alongside what FEWS
NET, IPC/CH and the HNRPs publish, and current plan funding (FTS).

**Site:** https://ocha-dap.github.io/ds-longterm-outlook/ (the outlook itself is at
`/outlook/`, password-protected)

It is an aggregator, not a model. Our figures are exposure, not a projection of how caseloads
will change. They cover meteorological and agricultural signals only: conflict, displacement
and economic drivers are not in them.

## Inputs

Most inputs are products the team already publishes. The FEWS NET seasonal calendars are our
own transcription (see below).

| Input | Where | Used for |
|---|---|---|
| SEAS5 × HNRP / IPC per admin unit (latest issuance) | dev blob `projects/ds-seas5-skill/site/<tag>/docs/data/hnrp_drought_{low,ipc}.json` | forecast per unit and trimester; PiN by severity; IPC/CH phase populations |
| ERA5 seasonal history per admin unit (detrended) | dev blob `projects/ds-seas5-skill/processed/paired_yearly_detrended_adm{1,2}.parquet` (lead-0 rows) | ENSO scenarios |
| ERA5 monthly climatology per admin unit | dev blob `projects/ds-seas5-skill/processed/monthly_clim_adm{1,2}.parquet` | rainy-season test for scenario seasons |
| HNRP plans + FTS funding | dev blob `projects/ds-hnrp-mirror/site-data/plans.json` | PiN, requirements, coverage |
| IPC/CH national | dev blob `projects/ds-ipc-mirror/site-data/national.json` | latest analysis and periods |
| FEWS NET population estimates | `https://fdw.fews.net/api/ipcpopulationsize.csv` (public) | peak needs (latest 12-month edition per country), monthly outlook brief |
| CPC relative Niño3.4 index | `https://www.cpc.ncep.noaa.gov/data/indices/RONI.ascii.txt` | ENSO phase of each past season |
| IRI ENSO probabilities | typed into `src/outlook/config.py` (`IRI_PLUME`) | context beside the scenarios; re-date each refresh |

| FEWS NET seasonal calendars | `data/fewsnet_calendars/<ISO3>.json` (committed; our transcription of the calendar image on each FEWS NET country page, 48 countries) | rainy seasons, harvests, lean seasons per calendar region; admin-1 → region |
| IPC/CH analysis links | `data/ipc_links.json` (committed snapshot of the IPC mirror's analysis registry; refresh with `pipeline/fetch_ipc_links.py`, needs the DB) | link to the analysis shown |
| FEWS NET country pages | `data/fewsnet_pages.json` (committed) | links |

The rules (dry signal, seasons, scenarios, weights) are at the top of
`src/outlook/exposure.py` and `src/outlook/enso.py`, and on the page under "How to read this".

## Seasonal calendars and timing of impact

`src/outlook/calendar.py` links each rainy season to the lean season that follows it, as FEWS
NET's projections do: rains → their harvest → the first lean season after that harvest, and in
pastoral areas the first pastoral lean season after the rains end. When a harvest from another
season falls in between, the impact is marked as likely smaller. The page shows, per calendar
region, FEWS NET's rains, harvest and lean season, our dry signal on each rainy season, and the
lean season affected. The global column "Lean season most affected" is the current or upcoming
lean season with the most people in areas with a dry signal.

The calendars are transcriptions of FEWS NET's images. They were made on 2026-10-28 by reading
each image (zoomed at full resolution), and checked two ways:
- an automatic pixel check of the blue rainy shading, red lean boxes and green harvest bars
  (2024+ template; OCR of the month labels places the columns);
- an ERA5 seasonality check of the admin-1 → region assignments.

Disagreements were reviewed by eye. Months are whole months: a month counts when a bar covers
at least half of it. Region assignments come from FEWS NET's titles and locator maps; each file
says how (`assign.method`) and how sure (`assign.confidence`). When FEWS NET posts a new
calendar image, re-transcribe that country: `data/fewsnet_pages.json` holds the image URL the
transcription was made from.

## Build and publish

```bash
uv sync
uv run python pipeline/build_data.py            # -> build/outlook.json; prints hand-check figures
uv run python pipeline/build_page.py            # -> build/outlook.html (plaintext, local preview)
PAGE_PASSWORD=... uv run python pipeline/build_page.py --publish
                                                # -> docs/outlook/{index.html,page.enc}
```

Needs the dev blob read SAS in the environment, as for any ocha-stratus read. `--refresh` on
`build_data.py` re-reads every cached input (`cache/`, gitignored; the adm2 paired file is
~560 MB). Refresh monthly, once the SEAS5 site has flipped to the new issuance. At the same
time, re-check the IRI numbers and summary sentence in `config.py`; the build warns when they
are over 45 days old.

**Never commit `build/`.** The plaintext page holds the data the gate protects. Commit
`docs/` with explicit pathspecs. The Pages workflow (`.github/workflows/deploy-pages.yml`)
serves `docs/` as is, and it refuses to publish if `docs/` holds unencrypted outlook data.

## Pages site

Workflow-built from `main`: `docs/` is the site. The landing page is at `/` (team convention:
one Pages site per repo, a card per product), and the outlook is at `/outlook/`. The outlook is
a password gate (`site/gate.html`, copied to `docs/outlook/index.html`) that decrypts
`page.enc` in the browser. Same format as the ds-aa-bfa-drought and ds-aa-tcd-drought pages.

## Known limits (v1)

- **ERA5 and the 2026 Sahel season.** The "season ending" trimesters use ERA5 for their
  observed months, and ERA5 overstated the 2026 deficit from Senegal to Sudan. Chad JAS 2026:
  ERA5 23% of normal, CHIRPS 64–70%. Those countries are flagged on the page. A CHIRPS
  cross-check is the next step.
- **"Rainy season" in the dry signal is a share-of-rain rule** (a trimester carrying ≥ 15% of
  annual rain), not a crop calendar. Harvests and lean seasons come from the FEWS NET calendars,
  which cover 48 countries; the others have no lean-season timing.
- **ENSO scenarios are a conditional climatology** (ERA5 seasons up to `OBS_LAST_YEAR` in
  `config.py`, currently 2025, by RONI phase), tested against chance. They are not a
  forecast. 2026 is left out on purpose (ERA5T, and the ERA5 overstatement above): including it
  pushed about 20 Sahel/Sudan units over the El Niño dry test on its own.
- **FEWS NET key drivers** are only in its PDFs; the Data Warehouse fields are empty.
