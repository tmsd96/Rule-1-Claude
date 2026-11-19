# Rule #1 Screener

Scans ~2,900 US, European and emerging-market stocks with Phil Town's **Rule #1** method and
shows the results on a free web dashboard. It emails you when a strong business goes on sale or has red-flag news.

- **Moat:** Big 5 numbers (ROIC, sales, EPS, equity and cash-flow growth ≥ 10% over 10/5/3/1 years)
- **Margin of safety:** sticker price, MOS price (your choice of discount), payback time
- **Management:** debt vs free cash flow, CEO/CFO departures, fraud, probes (SEC 8-K + news)
- **Timing:** MACD (8,17,9), Stochastics (14,5), 10-day moving average
- **Filters:** market (US / Europe / emerging markets), country, sector, margin of safety, minimum Big 5 score

Everything runs on free services: **GitHub Actions** (scanner), **Streamlit Community Cloud**
(dashboard), **Gmail** (alerts). The optional AI news summary uses the Anthropic API, which is paid
per use (see step 4).

## How it works

```
GitHub Actions, every day at 17:15 UTC              Streamlit Community Cloud
 ├─ financials of stocks older than 6 days            app.py reads the JSON files
 │  (SEC EDGAR for US, Yahoo for the rest)            from the repo's `data` branch
 └─ prices, tools, news ──► results pushed to the `data` branch
                 └─► email alerts (Gmail)
```

| Data | Source |
|---|---|
| US financials (10+ years, audited) and 8-K event filings | SEC EDGAR |
| Prices, non-US financials (~4 years), analyst growth, FX | Yahoo Finance (`yfinance`) |
| Index members | Wikipedia. US: S&P 500, 400, 600. Europe: EURO STOXX 50, FTSE 100 and 250, DAX, CAC 40, AEX, IBEX 35, PSI, SMI, OMX 30, FTSE MIB. Emerging: Hang Seng, CSI 300, KOSPI 200, NIFTY 50, B3 (Brazil) |
| Taiwan | A fixed list of the ~60 largest companies (`rule1/universe.py`), as Wikipedia has no usable table |
| News | Google News RSS, tagged by keyword rules, optionally summarised by Claude |

## Setup (about 20 minutes, all free)

### 1. Put the code on GitHub
Create a **public** repository on GitHub and push this folder to it. Public repos get unlimited
free GitHub Actions minutes, and the dashboard can read the results without a token. Your secrets
(email password, API keys) stay private either way.

### 2. Add the secrets
In the repo: **Settings → Secrets and variables → Actions → New repository secret**:

| Secret | Value |
|---|---|
| `SEC_USER_AGENT` | `your-name your@email.com` (the SEC asks automated tools to identify themselves) |
| `SMTP_USER` | your Gmail address |
| `SMTP_PASSWORD` | a Gmail **App Password** (Google Account → Security → 2-Step Verification → App passwords). Not your normal password. |
| `ALERT_EMAIL_TO` | where alerts go (defaults to `SMTP_USER`; comma-separate several) |
| `ANTHROPIC_API_KEY` | optional, enables AI news summaries |

### 3. Run the first scan
**Actions → Rule 1 scan → Run workflow.** The first run takes about 2 hours (it downloads
financials for every stock). After that it runs by itself every day at 17:15 UTC; each stock's
financials are refreshed about once a week, so daily runs are short except on that day.

### 4. (Optional) AI news summaries
With `ANTHROPIC_API_KEY` set, stocks with flagged headlines get a short Rule #1 risk read
(management / moat impact) from Claude. It only runs when a stock's flagged headlines change.
It uses `claude-haiku-5-5`, the cheapest model, and makes at most 20 calls per scan
(`news.ai_model` and `news.ai_max_calls_per_run` in `config.yaml`). Stocks over the limit get
their summary on the next scan. For a hard spending ceiling, use prepaid credits with auto-reload
off in the Anthropic Console. Without the key, keyword flags still work.

### 5. Publish the dashboard
1. Sign in at <https://share.streamlit.io> with GitHub → **Create app** → pick this repo, branch
   `main`, file `app.py`.
2. **Advanced settings → Secrets**, paste (with your names filled in):
   ```toml
   DATA_BASE_URL = "https://raw.githubusercontent.com/YOUR_USER/YOUR_REPO/data"
   ```
3. Deploy. Copy the app URL into the repo under **Settings → Secrets and variables → Actions →
   Variables** as `DASHBOARD_URL`, so alert emails link to it.

## Settings
`config.yaml` holds the defaults: 15% minimum return, 50% margin of safety, 15% growth cap,
which indices to scan, and which alerts to send (sectors, regions, minimum Big 5 score). The
dashboard sliders change the valuation live without touching the file.

## Run locally
```bash
pip install -r requirements.txt
python -m rule1.scanner one NVDA                          # analyse one stock
python -m rule1.scanner fundamentals --indices psi --limit 10
python -m rule1.scanner prices --no-alerts
streamlit run app.py
python -m pytest
```

## Known limits
- **Non-US history is short.** Yahoo's free data gives about 4 years, so European and emerging-market
  Big 5 scores use 3- and 1-year windows (the dashboard shows the number of years). US stocks use 10+ years.
- **Index lists can be out of date.** Delisted or renamed tickers in a Wikipedia table are skipped.
- **Yahoo Finance is unofficial.** It can rate-limit or change format. The scanner retries next
  run and keeps the last good data.
- **Keyword news flags are noisy.** A headline mentioning "lawsuit" may not be about the company's
  conduct. The AI summary helps; always read the source.
- **One-off gains.** If one quarter's EPS is more than twice the others (e.g. an investment gain),
  it is replaced by the median quarter and marked "one-off quarter removed".
- **Meaning is up to you.** No screener can tell you whether you understand a business.

This is an educational tool, not investment advice.
