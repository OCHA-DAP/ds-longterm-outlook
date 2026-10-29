"""One-off: match our admin units to JRC ASAP's GAUL2 units, written to data/asap_units.csv.

    uv run python pipeline/fetch_asap_units.py

Our units are the SEAS5 site's (hnrp_low / hnrp_ipc GeoJSON of the latest issuance, the units
the payloads hold); ASAP's are gaul2_asap_v06 (44 MB) where it has GAUL2 units for the country
(49 countries) and gaul1_asap_v06 (259 MB) elsewhere, from the ASAP download page, cached under
cache/asap/. For each of our units, every ASAP unit covering at least 5% of it, with that share
and the ASAP unit's cropland and rangeland share of its area (ASAP's km2_crop / km2_range over
km2_tot). Re-run when ASAP publishes a new boundary version or the SEAS5 site changes its units.
"""

import csv
import logging
import zipfile

import requests
import shapefile
from shapely.geometry import shape
from shapely.strtree import STRtree

from outlook import config, sources

log = logging.getLogger("fetch_asap_units")
MIN_SHARE = 0.05


def boundaries(level: int) -> shapefile.Reader:
    name = f"gaul{level}_asap_v06"
    d = config.CACHE / "asap" / name
    if not (d / f"{name}.shp").exists():
        z = d.with_suffix(".zip")
        z.parent.mkdir(parents=True, exist_ok=True)
        url = f"{config.ASAP_BASE}/files/{name}.zip"
        log.info("GET %s (ASAP's server is slow)", url)
        with requests.get(url, stream=True, timeout=3600) as r:
            r.raise_for_status()
            with open(z, "wb") as fh:
                for chunk in r.iter_content(1 << 20):
                    fh.write(chunk)
        zipfile.ZipFile(z).extractall(d)
    return shapefile.Reader(str(d / name))


def load(level: int, wanted: set[int], skip: set[int]) -> tuple[list, list]:
    geoms, recs = [], []
    for sr in boundaries(level).iterShapeRecords():
        rec = sr.record.as_dict()
        if rec["asap0_id"] in wanted and rec["asap0_id"] not in skip and rec["km2_tot"]:
            # ~1 km simplification: ASAP's GAUL1 file is 350 MB of detail that area shares
            # at admin-unit scale do not need
            g = shape(sr.shape.__geo_interface__).simplify(0.01)
            geoms.append(g if g.is_valid else g.buffer(0))
            rec["level"], rec["asap_id"] = level, rec[f"asap{level}_id"]
            recs.append(rec)
    return geoms, recs


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    tag = sources.seas5_tag()
    ours = {}
    for name in ("hnrp_low.geojson", "hnrp_ipc.geojson"):
        for f in sources.seas5_payload(name, tag)["features"]:
            ours.setdefault(f["properties"]["pcode"], f)
    asap0 = sources.asap_countries()
    wanted = {asap0[f["properties"]["iso3"]] for f in ours.values()
              if f["properties"]["iso3"] in asap0
              and f["properties"]["iso3"] not in config.ASAP_SKIP}
    # GAUL2 where ASAP has it for the country, GAUL1 for the rest
    g2, r2 = load(2, wanted, set())
    g1, r1 = load(1, wanted, {r["asap0_id"] for r in r2})
    geoms, recs = g2 + g1, r2 + r1
    log.info("GAUL2 for %s countries, GAUL1 for %s", len({r["asap0_id"] for r in r2}),
             len({r["asap0_id"] for r in r1}))
    tree = STRtree(geoms)
    log.info("%s ASAP units in %s countries; %s of our units", len(geoms), len(wanted), len(ours))
    rows, missed = [], 0
    for pc, f in sorted(ours.items()):
        g = shape(f["geometry"])
        g = g if g.is_valid else g.buffer(0)
        if g.area == 0:
            continue
        hits = []
        for i in tree.query(g):
            share = g.intersection(geoms[int(i)]).area / g.area
            if share >= MIN_SHARE:
                hits.append((share, recs[int(i)]))
        if not hits:
            missed += 1
        for share, rec in hits:
            rows.append(dict(pcode=pc, level=rec["level"], asap0_id=rec["asap0_id"],
                             asap_id=rec["asap_id"],
                             share=round(share, 3),
                             crop_frac=round(rec["km2_crop"] / rec["km2_tot"], 5),
                             range_frac=round(rec["km2_range"] / rec["km2_tot"], 5)))
    out = config.ROOT / "data" / "asap_units.csv"
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    log.info("wrote %s: %s rows; %s of our units match no ASAP unit", out, len(rows), missed)


if __name__ == "__main__":
    main()
