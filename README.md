# Rule #1 Screener

Scans ~900 US and European stocks with Phil Town's **Rule #1** method and shows the results on
a free web dashboard. It emails you when a strong business goes on sale or has red-flag news.

- **Moat:** Big 5 numbers (ROIC, sales, EPS, equity and cash-flow growth ≥ 10% over 10/5/3/1 years)
- **Margin of safety:** sticker price, MOS price (your choice of discount), payback time
- **Management:** debt vs free cash flow, CEO/CFO departures, fraud, probes (SEC 8-K + news)
- **Timing:** MACD (8,17,9), Stochastics (14,5), 10-day moving average
- **Filters:** market (US / Europe), sector, margin of safety, minimum Big 5 score

Everything runs on free services: **GitHub Actions** (scanner), **Streamlit Community Cloud**
(dashboard), **Gmail** (alerts). The optional AI news summary uses the Anthropic API, which is paid
per use (see step 4).

## How it works

```
GitHub Actions (on a schedule)                       Streamlit Community Cloud
 ├─ Sunday:   10-year financials for every stock      app.py reads the JSON files
 │            (SEC EDGAR for US, Yahoo for Europe)    from the repo's `data` branch
 └─ Weekdays every 2 h: prices, tools, news ──► results pushed to the `data` branch
                          └─► email alerts (Gmail)
```

| Data | Source |
|---|---|
| US financials (10+ years, audited) and 8-K event filings | SEC EDGAR |
| Prices, European financials (~4 years), analyst growth, FX | Yahoo Finance (`yfinance`) |
| Index members | Wikipedia: S&P 500, EURO STOXX 50, FTSE 100, DAX, CAC 40, AEX, IBEX 35, PSI, SMI, OMX 30, FTSE MIB |
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
**Actions → Rule 1 scan → Run workflow → mode: fundamentals.** The first run takes about 45
minutes. After that it runs by itself: financials every Sunday, prices/news/alerts every 2 hours
on weekdays.

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
- **European history is short.** Yahoo's free data gives about 4 years, so European Big 5 scores
  use 3- and 1-year windows (the dashboard shows the number of years). US stocks use 10+ years.
- **Yahoo Finance is unofficial.** It can rate-limit or change format. The scanner retries next
  run and keeps the last good data.
- **Keyword news flags are noisy.** A headline mentioning "lawsuit" may not be about the company's
  conduct. The AI summary helps; always read the source.
- **One-off gains.** If one quarter's EPS is more than twice the others (e.g. an investment gain),
  it is replaced by the median quarter and marked "one-off quarter removed".
- **Meaning is up to you.** No screener can tell you whether you understand a business.

This is an educational tool, not investment advice.
