# Deploying The Stock Valuation Workbench

This app is not a static HTML-only site. It needs the Python backend because the backend fetches market data and runs the valuation logic.

## Easiest Option: Render

1. Create a GitHub repository for this folder.
2. Upload/push these files to GitHub.
3. Go to https://render.com and create a free account.
4. Click **New +** -> **Blueprint**.
5. Connect the GitHub repo.
6. Render will read `render.yaml` and deploy the app.
7. Send people the deployed URL. The app page is:

   `/stock_evaluator.html`

Example:

`https://your-render-app.onrender.com/stock_evaluator.html`

## What Other People Will See

They do not need a login. They can type a ticker and run the same evaluator in their browser.

## Important Limits

- The current data source is Yahoo Finance through `yfinance`.
- This is useful for screening, but it is not a licensed institutional data feed.
- Free hosting can sleep after inactivity, so the first request may take 30-60 seconds.
- Public websites can be used by anyone with the link. If this gets shared widely, Yahoo/yfinance may rate-limit requests.

## Before Uploading

Do not upload:

- `evaluations.db`
- generated `evaluation_*.html` reports
- API keys
- private firm/client notes

The `.gitignore` file excludes these.

## More Reliable Production Path

For a truly serious public/internal tool, use a paid API such as FMP, Intrinio, CapIQ/S&P API, Polygon, or another approved provider, then add simple login or domain access controls.
