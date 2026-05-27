"""
Backtest intentionally disabled.

The previous prototype approximated historical fundamentals with current
fundamentals where point-in-time data was unavailable. That violates the
current requirement: no sample, fake, or proxy data.

To re-enable this, connect a data provider with point-in-time historical
fundamentals and adjusted historical prices, then implement the scoring from
those dated records.
"""


def main():
    print("Backtest disabled: no point-in-time historical fundamentals source is configured.")
    print("No calibration.json values were changed.")


if __name__ == "__main__":
    main()
