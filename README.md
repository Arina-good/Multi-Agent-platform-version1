# Multi-Agent LLM Financial Trading System — Upgraded Build

Upgraded version of the group-project TradingAgents demo. This build closes the
gaps found in the earlier review.

## Quick start

```bash
python -m venv trading_env
# Windows: trading_env\Scripts\activate
# macOS/Linux: source trading_env/bin/activate

pip install -r requirements.txt
copy .env.example .env      # then fill in ARK_API_KEY (optional)
streamlit run app.py
```

Without `ARK_API_KEY` the app still runs on a built-in rule-based engine, so
you can demo the UI / PDF flow end-to-end without a paid LLM.

## Scheduled pipeline (requirement 3.4)

```bash
# one-off run
python daily_run.py --tickers AAPL,MSFT,TSLA --risk Moderate

# every weekday after close
python daily_run.py --tickers AAPL,MSFT --schedule daily --at 16:30
```

Reports are written to `./reports/` and appended to `system_audit_log.txt`.

## What was fixed / added

### Bugs from review round 1 & 2
| # | Issue | How it is solved |
|---|---|---|
| 1 | Financial values inflated ~10x | All yfinance raw values converted once in `fetch_market_data()` to USD B / per-share; no ad-hoc `/100` |
| 2 | Web UI ≠ PDF numbers | One `result` dict is built by `run_pipeline()`; **both** the Streamlit render and `generate_pdf_bytes()` read the same dict. No second LLM call when exporting. |
| 3 | Fake `tradingagents.pc.com` URL in PDF | Removed. PDF prints runtime timestamp + data timestamp + risk profile instead. |
| 4 | 5-day forecast had no methodology | Caption under chart + "Methodology" section in PDF (linear fit on last 30 closes). |
| 5 | Dividend quarterly/annual mix | Annualised as sum of last 4 quarterly dividends, labelled `$x.xx / share`; identical in UI and PDF. |
| 6 | No error handling | Ticker validation, try/except around yfinance & LLM, retry on LLM failure, friendly `st.error`, rule-based fallback. |
| + | Market cap shown as $126B for AAPL | Now read from `info["marketCap"]` raw value / 1e9. |

### Missing requirements
- **3.1** Plotly interactive price chart (hover, zoom) + Agent Confidence gauge.
- **3.2** PDF report now contains executive summary, methodology, data-source
  timestamp, and a disclaimer.
- **3.3** Sidebar: ticker, look-back period, risk profile, portfolio size;
  all results are interactive.
- **3.4** `daily_run.py` scheduled pipeline (also easy to wire to Windows Task
  Scheduler / cron / GitHub Actions).
- **3.5** Audit log appended on every run; token & cost tracked; risk profile
  changes position sizing (Conservative 1.5% / Moderate 3% / Aggressive 5%);
  added **Fundamental Analyst** and **News Sentiment** agents with graceful
  fallback when news is unavailable.

## Files
- `app.py` — Streamlit UI + multi-agent pipeline + PDF export
- `daily_run.py` — headless scheduled runner
- `requirements.txt`
- `.env.example`

> （注：部分内容由豆包 AI 生成）