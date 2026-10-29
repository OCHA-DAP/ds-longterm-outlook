"""One-off: the map's basemap and each country's bubble position, committed under data/.

    uv run python pipeline/fetch_geo.py

- data/world-110m.json: world-atlas 2.0.2 countries-110m TopoJSON (Natural Earth 1:110m,
  public domain), drawn as the map's background.
- data/country_points.csv: Natural Earth's label point (LABEL_X / LABEL_Y, a point inside the
  country chosen for labels) for every country in config.REGIONS; a bubble starts there before
  overlaps are pushed apart. Cabo Verde is not in the 1:110m set: its point is typed in.
"""

import csv

import requests

from outlook import config

WORLD = "https://cdn.jsdelivr.net/npm/world-atlas@2.0.2/countries-110m.json"
NE = ("https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/"
      "ne_110m_admin_0_countries.geojson")
TYPED = {"CPV": (-24.0, 16.0)}  # centre of the archipelago


def main():
    data = config.ROOT / "data"
    world = requests.get(WORLD, timeout=60)
    world.raise_for_status()
    (data / "world-110m.json").write_text(world.text)

    ne = requests.get(NE, timeout=120)
    ne.raise_for_status()
    pts = dict(TYPED)
    for f in ne.json()["features"]:
        p = f["properties"]
        for k in ("ISO_A3", "ADM0_A3", "ISO_A3_EH"):
            if p.get(k) in config.REGION_OF and p[k] not in pts:
                pts[p[k]] = (round(p["LABEL_X"], 3), round(p["LABEL_Y"], 3))
    missing = sorted(set(config.REGION_OF) - set(pts))
    if missing:
        raise SystemExit(f"no point for {missing}: add them to TYPED")
    with open(data / "country_points.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["iso3", "lon", "lat"])
        for iso3 in sorted(pts):
            if iso3 in config.REGION_OF:
                w.writerow([iso3, *pts[iso3]])
    print(f"{len(pts)} points; world-110m {len(world.text) / 1e3:.0f} kB")


if __name__ == "__main__":
    main()
