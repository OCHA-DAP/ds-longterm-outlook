"""Snapshot IPC/CH analysis links (title + ipcinfo URL) per country into data/ipc_links.json.

    DSCI_AZ_DB_DEV_HOST=127.0.0.1:15432 uv run python pipeline/fetch_ipc_links.py

Reads the IPC mirror's analysis registry (dev DB, the IPC API's list of analyses). It is the
one input that needs the database, so it is a separate, optional step: build_data.py uses the
committed snapshot and works without it. Re-run it when the page shows a newer IPC analysis
than the link it gives.
"""

import json

import ocha_stratus as stratus
import pandas as pd
import pycountry
from sqlalchemy import text

from outlook.config import ROOT


def main():
    with stratus.get_engine(stage="dev").connect() as c:
        df = pd.read_sql(text("""
            select analysis_id, title, link, country_iso2, created
            from ipc.analyses
            where condition = 'A' and created >= '2023-01-01'
            order by created desc"""), c)
    out = {}
    for r in df.itertuples():
        cc = pycountry.countries.get(alpha_2=r.country_iso2)
        if not cc:
            continue
        out.setdefault(cc.alpha_3, []).append(
            dict(title=r.title, link=r.link, created=str(r.created)[:10]))
    path = ROOT / "data" / "ipc_links.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1))
    print(f"{path}: {sum(len(v) for v in out.values())} analyses, {len(out)} countries")


if __name__ == "__main__":
    main()
