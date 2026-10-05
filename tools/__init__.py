"""Verification and calibration tooling - not used by the bot at runtime.

- simulate.py   the circuit simulator and the spec's scenario checks
- calibrate.py  parameter tuning against historical Football Fusion games
- gather_data.py fetches raw games from Discord
- games_io.py   loads and preprocesses the raw game files
- games/        the historical season data

Run them from the repository root:

    uv run python -m tools.simulate
    uv run python -m tools.calibrate
"""
