"""One-off: match our admin units to JRC ASAP's GAUL2 units, written to data/asap_units.csv.

    uv run python pipeline/fetch_asap_units.py

Our units are the SEAS5 site's (hnrp_low / hnrp_ipc GeoJSON of the latest issuance, the units
the payloads hold); ASAP's are gaul2_asap_v06 (44 MB) where it has GAUL2 units for the country
(49 countries) and gaul1_asap_v06 (259 MB) elsewhere, from the ASAP download page, cached under
cache/asap/. For each of our units, every ASAP unit covering at least 5% of it, with that share
and the ASAP unit's cropland and rangeland share of its area (ASAP's km2_crop / km2_range over
km2_tot). Re-run when ASAP publishes a new boundary version or the SEAS5 site changes its units.

Also writes data/asi_units.csv: the FAO ASI province (GAUL 2015 ADM1_CODE) each of our units
sits in, for the ASI reference shown beside the biomass rank. FAO's provinces are matched by
name -- to our admin-1 units, then our admin-2 units (where FAO's provinces are our admin-2:
Chad, Tanzania, DR Congo's old provinces, Venezuela), then ASAP's GAUL1 units (whose polygons
are then overlapped with ours). Names are compared without accents, case, spaces, punctuation
or Arabic articles, with a close-match fallback ("Northern Darfur" / "North Darfur",
"Gadaref" / "Gedaref", "Dayr Az Zor" / "Deir-ez-Zor").
"""

import csv
import difflib
import logging
import re
import unicodedata
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


def norm(name: str) -> str:
    plain = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    plain = re.sub(r"\b(al|aj|el|ar|az|ad|ash|the|de|du|des|province|region|governorate)\b", " ",
                   plain)
    plain = re.sub(r"\bnorthern\b", "north", plain)
    plain = re.sub(r"\bsouthern\b", "south", plain)
    plain = re.sub(r"\beastern\b", "east", plain)
    plain = re.sub(r"\bwestern\b", "west", plain)
    return re.sub(r"[^a-z0-9]", "", plain)


def lookup(by_name: dict, n: str):
    """Exact normalised name, else the single close match (ratio >= 0.85)."""
    if n in by_name:
        return by_name[n]
    close = difflib.get_close_matches(n, list(by_name), n=2, cutoff=0.85)
    return by_name[close[0]] if len(close) == 1 else None


def asi_units(ours: dict, tag: str, asap0: dict) -> list[dict]:
    """Our unit -> FAO ASI province code, by name (our admin-1, our admin-2, ASAP GAUL1 + area)."""
    names = {lv: {} for lv in ("adm1", "adm2")}
    for lv in names:
        for f in sources.seas5_payload(f"hnrp_{lv}.geojson", tag)["features"]:
            p = f["properties"]
            names[lv].setdefault(p["iso3"], []).append((p["pcode"], norm(p["name"])))
    gaul1 = {}
    for sr in boundaries(1).iterShapeRecords():
        rec = sr.record.as_dict()
        gaul1.setdefault(rec["asap0_id"], []).append((norm(rec["name1"]), sr))
    rows = []
    for iso3 in sorted({f["properties"]["iso3"] for f in ours.values()}):
        fao = sources.fao_asi(iso3)
        if fao.empty:
            continue
        by_name = {norm(n): int(c) for c, n in fao[["code", "province"]].drop_duplicates().values}
        prefix, used = {}, set()
        for lv in ("adm1", "adm2"):  # name matches, admin-1 first
            for pc, n in names[lv].get(iso3, []):
                code = lookup(by_name, n)
                if code is not None and (lv == "adm1" or code not in used):
                    prefix[pc] = (code, lv)
                    if lv == "adm1":
                        used.add(code)
        polys = [(lookup(by_name, n), shape(sr.shape.__geo_interface__).simplify(0.01))
                 for n, sr in gaul1.get(asap0.get(iso3), []) if lookup(by_name, n) is not None]
        polys = [(c, g if g.is_valid else g.buffer(0)) for c, g in polys]
        for pc, f in ours.items():
            if f["properties"]["iso3"] != iso3:
                continue
            hits = [k for k in prefix if pc.startswith(k)]
            if hits:
                code, how = prefix[max(hits, key=len)]
                rows.append(dict(pcode=pc, fao_code=code, how=how))
                continue
            g = shape(f["geometry"])
            g = g if g.is_valid else g.buffer(0)
            best = max(((g.intersection(pg).area / g.area, c) for c, pg in polys if g.area),
                       default=(0, None))
            if best[0] >= 0.5:
                rows.append(dict(pcode=pc, fao_code=best[1], how="asap-gaul1"))
    return rows


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

    asi = asi_units(ours, tag, asap0)
    out = config.ROOT / "data" / "asi_units.csv"
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=["pcode", "fao_code", "how"])
        w.writeheader()
        w.writerows(asi)
    log.info("wrote %s: %s of our %s units placed in an FAO ASI province", out, len(asi),
             len(ours))


if __name__ == "__main__":
    main()
