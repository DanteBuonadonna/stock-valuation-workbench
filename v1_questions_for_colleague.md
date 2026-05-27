# Questions to Tune the Evaluator to the Firm's Methodology

The tool currently uses reasonable industry-default thresholds. Once your colleague walks you through the firm's actual rules, the values below get updated in the `THRESHOLDS` object near the bottom of `stock_evaluator.html`. The structure of the tool stays the same; only numbers change.

## Per-model questions

### Growth Model
1. Which growth rate matters most — trailing 5Y revenue growth, forward 3Y estimate, or a blend?
2. Do we use **forward EPS growth** or **trailing EPS CAGR**?
3. What forward P/E ceiling do we tolerate for a "growth" name? (Tool defaults: ≤30 strong, 30–40 OK, >40 weak.)
4. Is ROE the right quality metric, or do we prefer ROIC or gross margin trend?
5. Is PEG the right reasonableness check, or do we use forward P/E ÷ growth ourselves?
6. Any other required filter — minimum market cap, profitability requirement, sector exclusions?

### Dividend Model
1. Minimum dividend yield to be considered? (Default: 3% strong, 2% OK.)
2. Maximum payout ratio? (Default: 60% strong, 80% OK.) Do we use earnings payout, FCF payout, or both?
3. Required dividend growth track record — years of increases AND/OR CAGR?
4. How do we treat REITs and MLPs where payout ratio doesn't map cleanly?
5. Do we require FCF/dividend coverage, and at what multiple?
6. Any credit quality screen (S&P rating, interest coverage, net debt/EBITDA)?

### Value + Growth Model
1. Trailing P/E or forward P/E for the value lens?
2. Where does the "long-term growth rate" come from — consensus analyst LTG, internal estimate, or historical?
3. FCF yield — based on market cap or enterprise value?
4. Is PEG part of this model or only the Growth model?
5. Debt/Equity — is that the right quality screen, or do we prefer Net Debt/EBITDA or interest coverage?
6. Any rule about combining the two lenses — e.g., must pass BOTH the value AND growth screens, or weighted sum?

## Scoring & weighting

- Currently each metric is 0/1/2 points with equal weight. **Does the firm weight some inputs more heavily?** (e.g., growth rate counts double in the Growth model.)
- Currently 0–4 = Weak, 5–7 = Moderate, 8–10 = Strong. **Should those bands shift** (e.g., 7+ is the bar to add to a portfolio)?
- Should a single hard-fail metric override the score (e.g., negative FCF auto-disqualifies regardless of points)?

## Workflow questions

- Who else needs to use this tool? (Affects whether it should live as a shared file vs. on a firm server later.)
- Do you want an export-to-PDF "evaluation memo" template per stock, suitable for client files? (Easy add — the print button already produces a clean printout.)
- Do you want a saved log of every ticker you've evaluated and its score over time?

## Out of scope for v1 (worth flagging)

- **Batch screening** (paste 50 tickers, get a ranked table). Different workflow than this single-ticker deep dive.
- **Auto-pulling fundamentals** (P/E, payout, FCF) — Yahoo Finance's fundamentals endpoint is CORS-restricted from a static file, so v1 stays manual. If the firm has a Bloomberg/FactSet/Refinitiv terminal, a v2 could plug into that via API key.
- **Historical comparison** — showing how a name's score has drifted over the last four quarters.
