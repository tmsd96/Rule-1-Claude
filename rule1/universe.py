"""Builds the list of stocks to scan from index constituent tables on Wikipedia."""

from __future__ import annotations

import io
import logging

import pandas as pd
import requests

log = logging.getLogger(__name__)
UA = {"User-Agent": "rule1-screener/1.0 (educational stock screener)"}

# key: (wikipedia page, region, country, yahoo suffix to add, column holding the ticker)
INDICES = {
    "sp500": ("List_of_S%26P_500_companies", "US", "United States", "", "Symbol"),
    "stoxx50": ("EURO_STOXX_50", "Europe", None, "", "Ticker"),
    "ftse100": ("FTSE_100_Index", "Europe", "United Kingdom", ".L", "Ticker"),
    "dax": ("DAX", "Europe", "Germany", "", "Ticker"),
    "cac40": ("CAC_40", "Europe", "France", "", "Ticker"),
    "aex": ("AEX_index", "Europe", "Netherlands", "", "Ticker"),
    "ibex": ("IBEX_35", "Europe", "Spain", "", "Ticker"),
    "psi": ("PSI-20", "Europe", "Portugal", ".LS", "Ticker"),
    "smi": ("Swiss_Market_Index", "Europe", "Switzerland", ".SW", "Ticker"),
    "omx": ("OMX_Stockholm_30", "Europe", "Sweden", "", "Ticker"),
    "ftsemib": ("FTSE_MIB", "Europe", "Italy", "", "Ticker"),
}


def _clean_ticker(raw: str, suffix: str, region: str) -> str | None:
    t = str(raw).strip().split()[0].replace("\xa0", "")
    if not t or t.lower() == "nan":
        return None
    if region == "US":
        return t.replace(".", "-")  # BRK.B -> BRK-B (Yahoo style)
    if suffix and "." not in t:
        return t + suffix
    if suffix == ".L" and not t.endswith(".L"):
        return t.replace(".", "-") + ".L"  # BT.A -> BT-A.L
    return t


def fetch_index(key: str) -> pd.DataFrame:
    page, region, country, suffix, col = INDICES[key]
    r = requests.get(f"https://en.wikipedia.org/wiki/{page}", headers=UA, timeout=30)
    r.raise_for_status()
    for table in pd.read_html(io.StringIO(r.text)):
        cols = {str(c).split("[")[0].strip(): c for c in table.columns}
        if col in cols and len(table) >= 15:
            out = pd.DataFrame({"ticker": table[cols[col]].map(lambda x: _clean_ticker(x, suffix, region))})
            out["region"] = region
            out["country"] = country
            out["index"] = key
            return out.dropna(subset=["ticker"])
    raise ValueError(f"No constituent table found for {key}")


def build_universe(indices: list[str]) -> pd.DataFrame:
    frames = []
    for key in indices:
        try:
            frames.append(fetch_index(key))
            log.info("index %s: %d tickers", key, len(frames[-1]))
        except Exception as e:  # one broken page should not stop the scan
            log.warning("index %s failed: %s", key, e)
    if not frames:
        raise RuntimeError("Could not load any index")
    df = pd.concat(frames)
    # A stock in several indices keeps its first listing; join the index names.
    idx = df.groupby("ticker")["index"].apply(lambda s: ",".join(sorted(set(s))))
    df = df.drop_duplicates("ticker").set_index("ticker")
    df["index"] = idx
    return df.reset_index()
