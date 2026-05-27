"""
Predictor — maps a current evaluation to historical-context expected return.

This is NOT a stock-price oracle. It loads calibration data produced by
backtest.py and reports: "In our calibration data, stocks scoring in this
band went on to return roughly X to Y% over the next year (median Z%)."

That's an honest predictor: a calibrated track record, not a forecast.
"""
from __future__ import annotations
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
CALIBRATION_PATH = os.path.join(HERE, "calibration.json")


def load_calibration() -> dict:
    if not os.path.exists(CALIBRATION_PATH):
        return {"available": False, "is_seed": False, "calibration": {}}
    with open(CALIBRATION_PATH) as f:
        data = json.load(f)
    if data.get("is_seed") or data.get("n_samples", 0) == 0:
        return {
            "available": False,
            "is_seed": False,
            "n_samples": 0,
            "calibration": {},
            "summary": "No real predictor calibration is available yet.",
        }
    data["available"] = True
    return data


def aggregate_band(score: int) -> str:
    if score >= 22:
        return "strong (22-30)"
    if score >= 16:
        return "moderate (16-21)"
    return "weak (0-15)"


def predict(scores: dict, best_model: str, aggregate: int) -> dict:
    """
    Returns a dict with predictor outputs for inclusion in the report.
    """
    cal = load_calibration()
    if not cal.get("available"):
        return {
            "available": False,
            "is_seed": False,
            "n_samples": 0,
            "band": aggregate_band(aggregate),
            "band_stats": {},
            "model_stats": {},
            "generated_at": None,
            "summary": (
                "Predictor disabled: no real historical calibration has been loaded. "
                "I will not show seed or illustrative return expectations."
            ),
        }
    is_seed = cal.get("is_seed", False)
    cal_data = cal.get("calibration", {})

    band = aggregate_band(aggregate)
    by_band = cal_data.get("by_aggregate_band", {}).get(band, {})
    by_model = cal_data.get("by_best_model", {}).get(best_model, {})
    strong_score = scores.get(best_model, 0) >= 8
    by_model_specific = by_model.get("strong_only" if strong_score else "all", {})

    return {
        "is_seed": is_seed,
        "n_samples": cal.get("n_samples", 0),
        "band": band,
        "band_stats": by_band,
        "model_stats": by_model_specific,
        "generated_at": cal.get("generated_at"),
        "summary": _summarize(band, by_band, best_model, by_model_specific, is_seed),
    }


def _summarize(band, band_stats, best_model, model_stats, is_seed) -> str:
    if not band_stats or band_stats.get("n", 0) == 0 and is_seed:
        prefix = "(Seed data — run backtest.py to replace with your real calibration.)"
    elif not band_stats or band_stats.get("n", 0) == 0:
        return "Not enough samples in this band yet. Run backtest.py with more tickers/dates."
    else:
        prefix = f"Based on {band_stats.get('n', 0)} historical calibration samples."

    median = band_stats.get("median")
    p25 = band_stats.get("p25")
    p75 = band_stats.get("p75")
    line1 = f"Stocks in the {band} aggregate band returned a median of {median:+.1f}% " \
            f"over 1 year (IQR {p25:+.1f}% to {p75:+.1f}%)."

    mm = model_stats.get("median")
    mp25 = model_stats.get("p25")
    mp75 = model_stats.get("p75")
    if mm is not None:
        line2 = f"When the best-fit model was {best_model} at this strength, median was " \
                f"{mm:+.1f}% (IQR {mp25:+.1f}% to {mp75:+.1f}%)."
    else:
        line2 = ""

    return f"{prefix} {line1} {line2}".strip()
