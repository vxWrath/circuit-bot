"""Tunable parameters for the FF Circuit rating model.

Every knob the model has lives here, with sensible starting values. The
exact values are set by calibration (see calibrate.py) and written to
out/calibration/params.json, which the bot loads at startup.
"""

import msgspec


class Params(msgspec.Struct):
    # Scoring
    base_score: float = 40.0  # per-league average points a team scores in a game

    # Step size
    base_k: float = 0.15  # rating movement per point of surprise for a full-confidence team
    provisional_mult: float = 8.0  # K multiplier for a brand-new, zero-confidence team
    cap_total: float = 30.0  # the most rating points any single game can move a team
    unknown_opponent_discount: float = 0.5  # how much less a game against an unproven team moves you

    # Confidence
    conf_growth: float = 0.08  # fraction of remaining confidence gained per game played
    conf_new: float = 0.10  # confidence a new team starts with
    conf_min: float = 0.05  # confidence floor, so a team is never completely static
    provisional_threshold: float = 0.35  # below this confidence a team is provisional
    miss_decay: float = 0.08  # fraction of confidence lost per missed game
    season_gap_decay: float = 0.15  # confidence decay at a season boundary (calibration only)

    # Placement
    new_team_start_percentile: float = (
        2.5  # new teams start at this percentile of established ratings; kept low so a rejoin never lifts a team
    )
    new_team_start_min_games: int = 4  # a team needs this many games to count toward the percentile pool
    new_team_start_fallback: float = -25.0  # start rating when no established pool exists yet

    # Displayed rating penalty
    penalty_per_miss: float = 2.5  # displayed rating penalty per missed game
    miss_debt_max: float = 4.0  # cap on accumulated missed-game debt

    # Win probability
    prob_scale: float = 8.5  # logistic scale - margin points per unit of log-odds

    # Offense/defense split
    w_total: float = 1.0  # weight on the total-score dimension when splitting surprise into halves

    # Data hygiene
    score_cap: float = 100.0  # winsorize ceiling for individual scores, blunts troll games
    min_team_score: int = 30  # a game counts if one team reaches this many points
    min_combined_score: int = 50  # ... or if the combined score reaches this much


DEFAULT_PARAMS = Params()


def load_params(file_path: str) -> Params:
    """Load parameters from a JSON file written by save_params."""
    with open(file_path, "rb") as file:
        return msgspec.json.decode(file.read(), type=Params)


def save_params(parameters: Params, file_path: str) -> None:
    """Write parameters to a JSON file the bot can load."""
    with open(file_path, "wb") as file:
        file.write(msgspec.json.encode(parameters))
