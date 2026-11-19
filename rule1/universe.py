"""Builds the list of stocks to scan from index constituent tables on Wikipedia
(plus a fixed list for Taiwan, which has no usable Wikipedia table)."""

from __future__ import annotations

import io
import logging
import re

import pandas as pd
import requests

log = logging.getLogger(__name__)
UA = {"User-Agent": "rule1-screener/1.0 (educational stock screener)"}

US, EUROPE, EMERGING = "US", "Europe", "Emerging markets"

# key: (wikipedia page, region, country, yahoo suffix to add, column holding the ticker)
INDICES = {
    # United States: large, mid and small caps
    "sp500": ("List_of_S%26P_500_companies", US, "United States", "", "Symbol"),
    "sp400": ("List_of_S%26P_400_companies", US, "United States", "", "Symbol"),
    "sp600": ("List_of_S%26P_600_companies", US, "United States", "", "Symbol"),
    # Europe
    "stoxx50": ("EURO_STOXX_50", EUROPE, None, "", "Ticker"),
    "ftse100": ("FTSE_100_Index", EUROPE, "United Kingdom", ".L", "Ticker"),
    "ftse250": ("FTSE_250_Index", EUROPE, "United Kingdom", ".L", "Ticker"),
    "dax": ("DAX", EUROPE, "Germany", "", "Ticker"),
    "cac40": ("CAC_40", EUROPE, "France", "", "Ticker"),
    "aex": ("AEX_index", EUROPE, "Netherlands", "", "Ticker"),
    "ibex": ("IBEX_35", EUROPE, "Spain", "", "Ticker"),
    "psi": ("PSI-20", EUROPE, "Portugal", ".LS", "Ticker"),
    "smi": ("Swiss_Market_Index", EUROPE, "Switzerland", ".SW", "Ticker"),
    "omx": ("OMX_Stockholm_30", EUROPE, "Sweden", "", "Ticker"),
    "ftsemib": ("FTSE_MIB", EUROPE, "Italy", "", "Ticker"),
    # Emerging markets
    "hangseng": ("Hang_Seng_Index", EMERGING, "Hong Kong / China", ".HK", "Ticker"),
    "csi300": ("CSI_300_Index", EMERGING, "China", "", "Ticker"),
    "nifty50": ("NIFTY_50", EMERGING, "India", ".NS", "Symbol"),
    "kospi200": ("KOSPI_200", EMERGING, "South Korea", ".KS", "Symbol"),
    "brazil": ("List_of_companies_listed_on_B3", EMERGING, "Brazil", ".SA", "Ticker"),
}

# Taiwan: the ~60 largest companies on the Taiwan Stock Exchange (each checked on Yahoo, Nov 2026).
# Wikipedia has no usable constituent table; update this list occasionally.
TAIWAN = ("2330 2317 2454 2308 2382 2412 2881 2882 2891 2303 3711 2886 2884 1216 2002 1303 1301 "
          "2357 3231 2345 3008 2379 3034 2395 2892 2880 2885 5880 2887 2883 2603 2609 2615 2207 "
          "1101 2912 4904 3045 6505 1326 2301 4938 2327 3037 6669 3661 2376 2059 3017 2360 2383 "
          "3653 5871 1590 2408 2344 8046 1476 9910").split()
STATIC = {"taiwan": (EMERGING, "Taiwan", [c + ".TW" for c in TAIWAN])}


def _clean_ticker(raw, suffix: str, region: str) -> str | None:
    t = str(raw).strip().replace("\xa0", " ")
    if not t or t.lower() == "nan":
        return None
    # Hong Kong / China tables write "SEHK: 5", "SSE: 600519", "SZSE: 000858"
    m = re.match(r"^(SEHK|SSE|SZSE)\s*:\s*(\d+)", t)
    if m:
        exch, code = m.groups()
        if exch == "SEHK":
            return code.zfill(4) + ".HK"
        return code.zfill(6) + (".SS" if exch == "SSE" else ".SZ")
    t = t.split()[0]
    if region == US:
        return t.replace(".", "-")  # BRK.B -> BRK-B (Yahoo style)
    if suffix == ".KS":
        return t.zfill(6) + ".KS"  # Korean codes keep their leading zeros
    if suffix == ".HK":
        return t.zfill(4) + ".HK"
    if suffix == ".L" and not t.endswith(".L"):
        return t.replace(".", "-") + ".L"  # BT.A -> BT-A.L
    if suffix and "." not in t:
        return t + suffix
    return t


def fetch_index(key: str) -> pd.DataFrame:
    if key in STATIC:
        region, country, tickers = STATIC[key]
        return pd.DataFrame({"ticker": tickers, "region": region, "country": country, "index": key})
    page, region, country, suffix, col = INDICES[key]
    r = requests.get(f"https://en.wikipedia.org/wiki/{page}", headers=UA, timeout=30)
    r.raise_for_status()
    # Read every cell as text so codes like Korea's "005930" keep their leading zeros.
    for table in pd.read_html(io.StringIO(r.text), converters={col: str}):
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
