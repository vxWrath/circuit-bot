"""Calibration: backtest the rating model over historical games and tune parameters.

The model runs online - it predicts every game before seeing it and updates
after - exactly as the league will use it, over LFG and MVP as separate rating
pools. A seeded random search over the continuous parameters (plus a small
grid for the discrete ones) picks the configuration that predicts outcomes
best on held-out seasons, then a coordinate pass polishes it.

The missed-game penalty and its related constants are NOT tuned here - the
historical data has no window structure, so they are design constants
validated by simulate.py instead.

Outputs:
- out/calibration/params.json  the tuned parameters, loadable by the bot
- out/calibration/report.md    metrics, filter counts, and ablations
"""

import argparse
import math
import random
from collections import defaultdict
from pathlib import Path

import msgspec

from tools import games_io, model
from tools.games_io import ProcessedGame
from tools.model import TeamState
from tools.params import DEFAULT_PARAMS, Params, save_params

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(__file__).resolve().parent / "games"

POOL_SEASON_FILES: dict[str, dict[int, str]] = {
    "lfg": {season_number: f"games/lfg_season_{season_number}.json" for season_number in range(51, 56)},
    "mvp": {season_number: f"games/mvp_season_{season_number}.json" for season_number in range(9, 12)},
}

SELECTION_SEASONS: dict[str, set[int]] = {"lfg": {51, 52, 53}, "mvp": {9, 10}}
VALIDATION_SEASONS: dict[str, set[int]] = {"lfg": {54, 55}, "mvp": {11}}

MIN_GAMES_FOR_CORRELATION = 8


class GameRecord(msgspec.Struct):
    league: str
    season_number: int
    game_id: str
    team_a_id: str
    team_b_id: str
    predicted_margin: float
    actual_margin: float
    predicted_win_probability_a: float
    team_a_won: bool
    is_tie: bool
    team_a_is_new: bool
    team_a_was_provisional: bool


class SeasonSnapshot(msgspec.Struct):
    league: str
    season_number: int
    team_id: str
    final_rating: float
    mean_margin: float
    games_played: int


class RunResult(msgspec.Struct):
    game_records: list[GameRecord]
    season_snapshots: list[SeasonSnapshot]


class Metrics(msgspec.Struct):
    evaluated_games: int = 0
    baseline_margin_mae: float = 0.0  # mean |actual margin| - the "predict nothing" baseline
    margin_mae: float = 0.0
    margin_mae_excluding_first_season: float = 0.0  # first seasons are cold starts
    win_probability_log_loss: float = 0.0
    win_probability_brier: float = 0.0
    rating_margin_correlation: float = 0.0
    correlation_team_count: int = 0


def load_all_pools(
    params: Params,
    end_season: int | None,
) -> tuple[dict[str, dict[int, list[ProcessedGame]]], dict[str, int]]:
    """Load and preprocess every season file for both leagues.

    Returns the processed games keyed by (league, season_number) plus the
    combined preprocessing counts per league, for the report.
    """
    pool_games: dict[str, dict[int, list[ProcessedGame]]] = {}
    league_skip_counts: dict[str, int] = {}

    for league_name, season_files in POOL_SEASON_FILES.items():
        pool_games[league_name] = {}
        league_skip_counts[league_name] = 0

        for season_number, file_path in season_files.items():
            if end_season is not None and season_number > end_season:
                continue

            raw_games = games_io.load_games(str(DATA_ROOT / file_path))
            processed_games, counts = games_io.preprocess_games(raw_games, params, file_path)
            pool_games[league_name][season_number] = processed_games
            league_skip_counts[league_name] += counts.skipped_not_counting

    return pool_games, league_skip_counts


def run_online(
    pool_games: dict[str, dict[int, list[ProcessedGame]]],
    params: Params,
) -> RunResult:
    """Run the model over every game in chronological order, pool by pool.

    Ratings carry across seasons for teams whose ids persist; ids that
    reappear after an absence decay in confidence at each season boundary
    (historical data cannot distinguish absence from leaving, so it is
    treated as inactivity). Every team starts fresh at the bottom with a
    provisional tag, and the pool is recentered after every game so the
    new-team starting point always means "bottom".
    """
    game_records: list[GameRecord] = []
    season_snapshots: list[SeasonSnapshot] = []
    team_states: dict[str, TeamState] = {}

    for league_name, league_seasons in pool_games.items():
        is_first_season_of_league = True
        seen_season_numbers = sorted(league_seasons)

        for season_number in seen_season_numbers:
            season_games = league_seasons[season_number]

            if not is_first_season_of_league:
                team_states = model.season_boundary(team_states, params)
                team_states = model.recenter(team_states)
            is_first_season_of_league = False

            season_margins: dict[str, list[int]] = defaultdict(list)
            season_games_played: dict[str, int] = defaultdict(int)

            for game in season_games:
                team_a = team_states.get(game.team_a_id)
                team_b = team_states.get(game.team_b_id)

                team_a_is_new = team_a is None
                if team_a is None:
                    team_a = model.new_team(game.team_a_id, params, team_states)
                if team_b is None:
                    team_b = model.new_team(game.team_b_id, params, team_states)

                prediction = model.predict(team_a, team_b, params)
                actual_margin = game.score_a - game.score_b

                game_records.append(
                    GameRecord(
                        league=league_name,
                        season_number=season_number,
                        game_id=game.game_id,
                        team_a_id=game.team_a_id,
                        team_b_id=game.team_b_id,
                        predicted_margin=prediction.expected_margin,
                        actual_margin=actual_margin,
                        predicted_win_probability_a=prediction.win_probability_a,
                        team_a_won=actual_margin > 0,
                        is_tie=actual_margin == 0,
                        team_a_is_new=team_a_is_new,
                        team_a_was_provisional=model.is_provisional(team_a, params),
                    )
                )

                updated_team_a, updated_team_b = model.apply_game(team_a, team_b, params, game.score_a, game.score_b)
                team_states[game.team_a_id] = updated_team_a
                team_states[game.team_b_id] = updated_team_b

                season_margins[game.team_a_id].append(actual_margin)
                season_margins[game.team_b_id].append(-actual_margin)
                season_games_played[game.team_a_id] += 1
                season_games_played[game.team_b_id] += 1

                team_states = model.recenter(team_states)

            for team_id, margins in season_margins.items():
                season_snapshots.append(
                    SeasonSnapshot(
                        league=league_name,
                        season_number=season_number,
                        team_id=team_id,
                        final_rating=model.rating(team_states[team_id]),
                        mean_margin=sum(margins) / len(margins),
                        games_played=season_games_played[team_id],
                    )
                )

    return RunResult(game_records=game_records, season_snapshots=season_snapshots)


def compute_metrics(
    run_result: RunResult,
    selected_leagues: set[str] | None = None,
    selected_seasons: set[int] | None = None,
    exclude_first_season: bool = False,
) -> Metrics:
    """Score a run. All metrics are online: predictions vs what actually happened.

    The first season of each league is a cold start - every team is brand
    new at the same rating - so the warm-up-excluded MAE is the honest
    number and is what the search optimizes.
    """
    if selected_leagues is not None:
        records = [record for record in run_result.game_records if record.league in selected_leagues]
    else:
        records = list(run_result.game_records)

    if selected_seasons is not None:
        records = [record for record in records if record.season_number in selected_seasons]

    metrics = Metrics()
    metrics.evaluated_games = len(records)

    if not records:
        return metrics

    total_margin_error = 0.0
    total_baseline_error = 0.0
    warm_up_margin_error = 0.0
    warm_up_game_count = 0
    total_log_loss = 0.0
    total_brier = 0.0
    decided_game_count = 0

    first_season_per_league = _first_season_per_league(run_result)

    for record in records:
        margin_error = abs(record.predicted_margin - record.actual_margin)
        total_margin_error += margin_error
        total_baseline_error += abs(record.actual_margin)

        if exclude_first_season and record.season_number == first_season_per_league.get(record.league):
            pass  # cold-start games stay out of the warm-up average
        else:
            warm_up_margin_error += margin_error
            warm_up_game_count += 1

        if record.is_tie:
            continue

        decided_game_count += 1
        win_probability = min(max(record.predicted_win_probability_a, 1e-6), 1 - 1e-6)
        outcome = 1.0 if record.team_a_won else 0.0

        total_log_loss += -(outcome * math.log(win_probability) + (1 - outcome) * math.log(1 - win_probability))
        total_brier += (win_probability - outcome) ** 2

    metrics.baseline_margin_mae = total_baseline_error / metrics.evaluated_games
    metrics.margin_mae = total_margin_error / metrics.evaluated_games

    if warm_up_game_count > 0:
        metrics.margin_mae_excluding_first_season = warm_up_margin_error / warm_up_game_count

    if decided_game_count > 0:
        metrics.win_probability_log_loss = total_log_loss / decided_game_count
        metrics.win_probability_brier = total_brier / decided_game_count

    # End-of-season rating vs the team's actual mean margin that season,
    # restricted to teams with enough games for a rating to mean anything.
    ratings: list[float] = []
    mean_margins: list[float] = []
    first_season_cutoff = {league: season for league, season in first_season_per_league.items()}

    for snapshot in run_result.season_snapshots:
        if snapshot.games_played < MIN_GAMES_FOR_CORRELATION:
            continue
        if exclude_first_season and snapshot.season_number == first_season_cutoff.get(snapshot.league):
            continue
        ratings.append(snapshot.final_rating)
        mean_margins.append(snapshot.mean_margin)

    metrics.correlation_team_count = len(ratings)
    metrics.rating_margin_correlation = model.pearson_correlation(ratings, mean_margins)

    return metrics


def calibration_table_lines(run_result: RunResult) -> list[str]:
    """Predicted win probability deciles vs actual win rates, as text lines."""
    lines: list[str] = []
    decided_records = [record for record in run_result.game_records if not record.is_tie]

    lines.append("| Predicted win prob | Games | Actual win rate |")
    lines.append("| --- | --- | --- |")

    for bucket_start in range(0, 100, 10):
        bucket_low = bucket_start / 100
        bucket_high = (bucket_start + 10) / 100
        bucket_records = [
            record for record in decided_records if bucket_low <= record.predicted_win_probability_a < bucket_high
        ]

        if not bucket_records:
            lines.append(f"| {bucket_low:.1f}-{bucket_high:.1f} | 0 | - |")
            continue

        actual_win_rate = sum(1 for record in bucket_records if record.team_a_won) / len(bucket_records)
        lines.append(f"| {bucket_low:.1f}-{bucket_high:.1f} | {len(bucket_records)} | {actual_win_rate:.2f} |")

    return lines


def objective_score(metrics: Metrics) -> float:
    """One number to minimize: log-loss plus a scaled margin-MAE term.

    The MAE term is normalized by the baseline so its weight does not
    depend on the league's scoring level. Warm-up-excluded MAE is used
    because every pool's first season is a cold start by construction.
    """
    margin_term = metrics.margin_mae_excluding_first_season / max(metrics.baseline_margin_mae, 1.0)
    return metrics.win_probability_log_loss + 0.25 * margin_term


CONTINUOUS_SEARCH_SPACE: dict[str, tuple[float, float]] = {
    "base_score": (30.0, 50.0),
    "base_k": (0.03, 0.2),
    "provisional_mult": (3.0, 10.0),
    "cap_total": (15.0, 60.0),
    "unknown_opponent_discount": (0.0, 1.0),
    "conf_growth": (0.03, 0.2),
    "conf_new": (0.05, 0.25),
    "provisional_threshold": (0.2, 0.5),
    "season_gap_decay": (0.05, 0.4),
    # Pinned by design: the start stays near the bottom of the league so
    # disband-and-rejoin is never a ratings gain (docs/rating-system.md).
    "new_team_start_percentile": (2.5, 2.5),
    "prob_scale": (8.0, 20.0),
}

DISCRETE_SEARCH_SPACE: dict[str, list[float]] = {
    "score_cap": [80.0, 100.0, 120.0],
    "w_total": [0.5, 0.75, 1.0],
}


def search_parameters(
    selection_games: dict[str, dict[int, list[ProcessedGame]]],
    random_search_evaluations: int,
    seed: int,
) -> tuple[Params, list[tuple[float, Params]]]:
    """Random search over the continuous space, then coordinate refinement.

    Returns the best parameters found plus every evaluated (score, params)
    pair, so the report can show how flat the optimum is.
    """
    rng = random.Random(seed)
    evaluation_log: list[tuple[float, Params]] = []

    def evaluate(parameters: Params) -> float:
        result = run_online(selection_games, parameters)
        metrics = compute_metrics(result, exclude_first_season=True)
        score = objective_score(metrics)
        evaluation_log.append((score, parameters))
        return score

    best_parameters = DEFAULT_PARAMS
    best_score = evaluate(best_parameters)
    print(f"  defaults: {best_score:.4f}")

    # Random search: sample the full space uniformly.
    for evaluation_index in range(random_search_evaluations):
        trial_parameters = msgspec.structs.replace(
            DEFAULT_PARAMS,
            **{name: rng.uniform(low, high) for name, (low, high) in CONTINUOUS_SEARCH_SPACE.items()},
            **{name: rng.choice(values) for name, values in DISCRETE_SEARCH_SPACE.items()},
        )
        trial_score = evaluate(trial_parameters)
        if trial_score < best_score:
            best_score = trial_score
            best_parameters = trial_parameters
            print(f"  eval {evaluation_index + 1}: {trial_score:.4f}  <- new best")

    # Coordinate refinement: nudge each continuous parameter around the best.
    for refinement_round in range(2):
        for parameter_name, (low, high) in CONTINUOUS_SEARCH_SPACE.items():
            if low == high:
                continue  # pinned by design, not searched
            base_value = getattr(best_parameters, parameter_name)

            for fraction in (0.85, 1.15, 0.7, 1.3):
                candidate_parameters = msgspec.structs.replace(
                    best_parameters, **{parameter_name: base_value * fraction}
                )
                candidate_score = evaluate(candidate_parameters)

                if candidate_score < best_score:
                    best_score = candidate_score
                    best_parameters = candidate_parameters
                    print(f"  refine round {refinement_round + 1} {parameter_name}: {candidate_score:.4f}  <- new best")

    return best_parameters, evaluation_log


def build_report(
    run_result: RunResult,
    metrics_by_pool: dict[str, Metrics],
    evaluation_log: list[tuple[float, Params]],
    best_parameters: Params,
    league_skip_counts: dict[str, int],
    ablation_rows: list[str],
) -> str:
    """Assemble the calibration report markdown."""
    lines: list[str] = []
    lines.append("# Calibration Report")
    lines.append("")
    lines.append("Metrics are online: every game is predicted before it is seen, then the")
    lines.append("model updates. Ties are excluded from win-probability metrics.")
    lines.append("")
    lines.append("The missed-game penalty and its decay constants are design constants set")
    lines.append("from the spec and validated by simulate.py - historical data has no")
    lines.append("window structure to calibrate them against.")
    lines.append("")
    lines.append("## Filtering")
    lines.append("")

    for league_name, skipped in league_skip_counts.items():
        lines.append(
            f"- {league_name}: {skipped} games skipped as forfeit conventions "
            f"(neither team hit {DEFAULT_PARAMS.min_team_score} and combined under {DEFAULT_PARAMS.min_combined_score})"
        )

    lines.append("")
    lines.append("## Metrics")
    lines.append("")
    lines.append("| Pool | Games | MAE margin | Warm-up MAE | Baseline MAE | Log-loss | Brier | Rating corr |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")

    for pool_name, metrics in metrics_by_pool.items():
        lines.append(
            f"| {pool_name} | {metrics.evaluated_games} | {metrics.margin_mae:.2f} | "
            f"{metrics.margin_mae_excluding_first_season:.2f} | {metrics.baseline_margin_mae:.2f} | "
            f"{metrics.win_probability_log_loss:.3f} | {metrics.win_probability_brier:.3f} | "
            f"{metrics.rating_margin_correlation:.3f} ({metrics.correlation_team_count} teams) |"
        )

    lines.append("")
    lines.append("Rating correlation is between each team's end-of-season rating and its mean")
    lines.append(f"margin that season, for teams with at least {MIN_GAMES_FOR_CORRELATION} games.")
    lines.append("")
    lines.append("## Win probability calibration")
    lines.append("")
    lines.extend(calibration_table_lines(run_result))
    lines.append("")
    lines.append("## Parameter search")
    lines.append("")

    if evaluation_log:
        lines.append(f"{len(evaluation_log)} configurations evaluated.")
        lines.append("")

    for parameter_name in sorted(CONTINUOUS_SEARCH_SPACE.keys() | DISCRETE_SEARCH_SPACE.keys()):
        value = getattr(best_parameters, parameter_name)
        lines.append(f"- `{parameter_name}`: {value}")

    lines.append("")
    lines.append("## Ablations")
    lines.append("")

    for ablation_row in ablation_rows:
        lines.append(f"- {ablation_row}")

    lines.append("")
    return "\n".join(lines)


def run_ablation(
    selection_games: dict[str, dict[int, list[ProcessedGame]]],
    best_parameters: Params,
    ablation_name: str,
    ablation_parameters: Params,
) -> str:
    """Run one ablation configuration and summarize it against the tuned one."""
    ablation_result = run_online(selection_games, ablation_parameters)
    ablation_metrics = compute_metrics(ablation_result, exclude_first_season=True)
    best_result = run_online(selection_games, best_parameters)
    best_metrics = compute_metrics(best_result, exclude_first_season=True)

    return (
        f"{ablation_name}: MAE {ablation_metrics.margin_mae_excluding_first_season:.2f} "
        f"(tuned {best_metrics.margin_mae_excluding_first_season:.2f}), "
        f"log-loss {ablation_metrics.win_probability_log_loss:.3f} "
        f"(tuned {best_metrics.win_probability_log_loss:.3f})"
    )


def _first_season_per_league(run_result: RunResult) -> dict[str, int]:
    """The lowest season number seen for each league in this run."""
    first_seasons: dict[str, int] = {}

    for record in run_result.game_records:
        current_first = first_seasons.get(record.league)
        if current_first is None or record.season_number < current_first:
            first_seasons[record.league] = record.season_number

    return first_seasons


def main() -> None:
    argument_parser = argparse.ArgumentParser(description="Calibrate the FF Circuit rating model")
    argument_parser.add_argument("--pools", choices=["lfg", "mvp", "all"], default="all")
    argument_parser.add_argument("--search", choices=["random", "coord", "none"], default="random")
    argument_parser.add_argument("--evals", type=int, default=60, help="random search evaluations")
    argument_parser.add_argument("--end-season", type=int, default=None, help="drop seasons after this one")
    argument_parser.add_argument("--seed", type=int, default=42)
    argument_parser.add_argument("--out-dir", type=str, default="out/calibration")

    arguments = argument_parser.parse_args()

    output_directory = PROJECT_ROOT / arguments.out_dir
    output_directory.mkdir(parents=True, exist_ok=True)

    requested_leagues = {"lfg", "mvp"} if arguments.pools == "all" else {arguments.pools}
    pool_games, league_skip_counts = load_all_pools(DEFAULT_PARAMS, arguments.end_season)

    selection_games: dict[str, dict[int, list[ProcessedGame]]] = {}
    validation_games: dict[str, dict[int, list[ProcessedGame]]] = {}

    for league_name in requested_leagues:
        selection_games[league_name] = {
            season_number: games
            for season_number, games in pool_games[league_name].items()
            if season_number in SELECTION_SEASONS[league_name]
        }
        validation_games[league_name] = {
            season_number: games
            for season_number, games in pool_games[league_name].items()
            if season_number in VALIDATION_SEASONS[league_name]
        }

    # Parameter search on the selection seasons.
    best_parameters = DEFAULT_PARAMS
    evaluation_log: list[tuple[float, Params]] = []

    if arguments.search == "random":
        print("Random search over selection seasons...")
        best_parameters, evaluation_log = search_parameters(selection_games, arguments.evals, arguments.seed)
    elif arguments.search == "coord":
        print("Coordinate refinement only...")
        best_parameters, evaluation_log = search_parameters(selection_games, 0, arguments.seed)
    else:
        print("Using default parameters (no search)")

    print(f"Best parameters: {best_parameters}")

    # Final metrics: selection, validation, and everything together.
    full_games = {league_name: pool_games[league_name] for league_name in requested_leagues}

    selection_result = run_online(selection_games, best_parameters)
    validation_result = run_online(validation_games, best_parameters)
    full_result = run_online(full_games, best_parameters)

    metrics_by_pool = {
        "selection": compute_metrics(selection_result, exclude_first_season=True),
        "validation": compute_metrics(validation_result, exclude_first_season=True),
        "all": compute_metrics(full_result, exclude_first_season=True),
    }

    for pool_name, metrics in metrics_by_pool.items():
        print(
            f"{pool_name}: games={metrics.evaluated_games} MAE={metrics.margin_mae:.2f} "
            f"warm={metrics.margin_mae_excluding_first_season:.2f} "
            f"logloss={metrics.win_probability_log_loss:.3f} corr={metrics.rating_margin_correlation:.3f}"
        )

    # Ablations on the selection seasons: how much each decision mattered.
    ablation_rows: list[str] = []

    ablation_rows.append(run_ablation(selection_games, best_parameters, "default parameters", DEFAULT_PARAMS))

    no_cap_parameters = msgspec.structs.replace(best_parameters, cap_total=1e9)
    ablation_rows.append(run_ablation(selection_games, best_parameters, "no movement cap", no_cap_parameters))

    no_discount_parameters = msgspec.structs.replace(best_parameters, unknown_opponent_discount=0.0)
    ablation_rows.append(
        run_ablation(
            selection_games,
            best_parameters,
            "no unknown-opponent discount",
            no_discount_parameters,
        )
    )

    # The offense/defense split is not ablated here: orientation hashing and
    # w_total only move the halves, and historical games carry no ground
    # truth for them. simulate.py validates the halves on planted data.

    report = build_report(
        run_result=full_result,
        metrics_by_pool=metrics_by_pool,
        evaluation_log=evaluation_log,
        best_parameters=best_parameters,
        league_skip_counts=league_skip_counts,
        ablation_rows=ablation_rows,
    )

    save_params(best_parameters, str(output_directory / "params.json"))

    report_file_path = output_directory / "report.md"
    report_file_path.write_text(report)

    print(f"Report written to {report_file_path}")
    print(f"Params written to {output_directory / 'params.json'}")


if __name__ == "__main__":
    main()
