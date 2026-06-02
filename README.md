# Three-Model Stock Evaluator

A wealth-management evaluator that scores any stock against three internal models — Growth, Dividend, Value+Growth — plus supplementary valuations (Graham Number, Lynch PEG fair value, Dividend Discount Model, FCF yield). The main experience is now an interactive web app with peer/industry comparison charts, predictor context, and a local learning log.

## Quick start

1. **Double-click `start_stock_app.command`** in Finder.
2. A browser opens to `http://127.0.0.1:8765/stock_evaluator.html`.
3. Type any ticker (e.g. `NVDA`, `KO`, `MSFT`) and click **Evaluate**.

Keep the Terminal window open while using the app. First run installs `yfinance` and `pandas` automatically — takes about 30 seconds. After that, evaluations usually take a few seconds, depending on peer fetch speed.

`evaluate_stock.command` still exists if you want the older one-ticker HTML report generator.

## The Web App

The interactive app shows:

- **Overall recommendation** (BUY / HOLD / PASS) and the best-fit model
- **Three model scorecards** — Growth, Dividend, Value+Growth — with per-metric breakdown
- **Supplementary valuations** — Graham, Lynch PEG, DDM, FCF yield, with implied upside vs. price
- **Peer and benchmark comparisons** — inferred industry competitors, sector ETF, SPY, and averages
- **Graphs** — 2-year price trend, peer comparison chart, and model score radar
- **Predictor status** — disabled unless real historical calibration data is available
- **Your track record** — every past evaluation you've run, with realized returns once they mature
- **Strengths and red flags** — automatically surfaced
- **Watchlist + Alerts** — add any ticker with custom thresholds (daily %, weekly %, price targets, 52w high/low touches, score change). Alerts auto-check on page load and on demand
- **Macro context** — live 10Y Treasury, 13-week T-bill, 30Y Treasury, VIX, DXY, Gold, Oil, SPY — with spread comparisons (FCF yield, dividend yield, earnings yield vs the 10Y)
- **Events & catalysts** — next earnings date, ex-dividend date, dividend payment date with countdown badges
- **Tax tags** — auto-detected REIT / MLP / ADR classifications with treatment notes
- **Income projection** — 5-year cash-collection and DRIP scenarios for dividend payers, configurable position size + growth override
- **Per-ticker notes** — freeform research notes and meeting talking points, persisted locally
- **Portfolio fit check** — paste a client's holdings as CSV; the app reports correlation of the candidate to each holding, current and projected sector concentration, and a suggested starter position size
- **Client memo PDF** — one-click 2-page PDF with the recommendation, key metrics, model breakdown, upcoming catalysts, tax notes, suitability checkboxes, and disclaimer. Firm branding lives in `firm_branding.json`

## Files

| File | What it does |
|------|--------------|
| `start_stock_app.command` | Double-click launcher for the interactive web app |
| `stock_evaluator.html` | Browser UI for ticker input, valuation tabs, charts, peers, predictor |
| `app_server.py` | Local API server that fetches data and powers the web app |
| `evaluate_stock.command` | Older double-click launcher for generating a static report |
| `run_backtest.command` | Disabled until a real point-in-time historical fundamentals source is added |
| `evaluate.py` | CLI: `python3 evaluate.py NVDA` |
| `data_fetcher.py` | Pulls fundamentals from Yahoo Finance |
| `evaluator_engine.py` | Scoring rules, thresholds, HTML report generator |
| `predictor.py` | Maps current score to historical return distribution |
| `backtest.py` | Generates calibration data |
| `calibration.json` | Empty calibration table; no seed/mock predictor data |
| `evaluations.db` | SQLite log of every evaluation, watchlist, alerts, and notes |
| `watchlist.py` | Watchlist + alert rule engine |
| `macro.py` | Live macro indicators and spread comparisons vs the 10Y |
| `events.py` | Earnings calendar and dividend dates |
| `tax_tags.py` | REIT / MLP / ADR / qualified-dividend tagging |
| `income_proj.py` | Dividend income projection (cash + DRIP) |
| `notes_store.py` | Per-ticker research notes and meeting prep |
| `portfolio_fit.py` | CSV holdings parser, correlation calc, sector concentration, position-size rule |
| `memo_generator.py` | 2-page client memo PDF generator (uses reportlab) |
| `firm_branding.json` | Edit this to put your real firm name, advisor name, and disclaimer on the memo |

## Tuning to your firm's methodology

The current scoring thresholds are sensible industry defaults — see `v1_questions_for_colleague.md` for the list of specifics to confirm. Once those are settled, edit the `THRESHOLDS` dict near the top of `evaluator_engine.py` and every future report uses the new logic.

## The predictor — current status

The predictor is currently **disabled**. The old seed calibration has been removed because it was illustrative, not factual.

To enable a real predictor, the app needs point-in-time historical fundamentals plus adjusted future returns from an approved data source. Using today's fundamentals as a proxy for the past is not acceptable for this version.

## Learning over time

Every evaluation is logged to `evaluations.db` with the price at evaluation time. Each subsequent run pulls the current price for past tickers and computes the realized return. Over time, this gives you a real track record of your own calls (separate from the synthetic backtest basket) — which the report surfaces in the Track Record section.
