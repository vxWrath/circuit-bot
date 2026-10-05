"""The FF Circuit rating model.

Every team carries a rating split into two halves - offense and defense -
which add up to the overall rating. A rating is expressed in expected
margin points: a team at +15 is expected to beat an average team by 15.

Behind the displayed rating sits the same number adjusted for missed
games; the true halves never move for a game that did not happen.

Key properties of the update rule:

- The total rating move is the confidence-scaled margin surprise only:
  offense_change + defense_change = K * margin_surprise. The total-score
  surprise cancels out of the sum, so expected results are cheap and
  upsets are expensive with no extra machinery.
- With w_total = 1 the split is exactly "scored more than expected ->
  offense up; conceded less than expected -> defense up".
- At equilibrium with centered halves and balanced schedules,
  offense = mean(own scores) - base_score and
  defense = base_score - mean(opponent scores), so the game network
  separates the halves even though no single game can.
"""

import math

import msgspec

from tools.params import Params


class TeamState(msgspec.Struct):
    team_id: str
    offense: float  # offense half of the true rating
    defense: float  # defense half of the true rating
    confidence: float  # how sure the model is, between conf_min and 1
    games_played: int
    miss_debt: float  # fractional missed games still owed, >= 0
    status: str = "active"  # "active" | "hiatus" | "left"
    games_missed: int = 0


class Prediction(msgspec.Struct):
    expected_points_a: float
    expected_points_b: float
    expected_margin: float
    win_probability_a: float


def rating(team: TeamState) -> float:
    """The team's true rating - always computed, never stored."""
    return team.offense + team.defense


def displayed_rating(team: TeamState, params: Params) -> float:
    """The number the league shows: the true rating minus missed-game penalties."""
    return rating(team) - team.miss_debt * params.penalty_per_miss


def is_provisional(team: TeamState, params: Params) -> bool:
    return team.confidence < params.provisional_threshold


def new_team(
    team_id: str,
    params: Params,
    team_states: dict[str, TeamState] | None = None,
    prior_rating: float | None = None,
) -> TeamState:
    """A brand-new team starts near the bottom of the current league.

    The start is measured, not assumed: a percentile of the established
    teams' ratings, so it moves as the league changes. With no established
    pool yet - the first teams of a brand-new league - the configured
    fallback is used.

    A returning team passes the rating it left with as prior_rating: the
    reset never lifts it, so it starts at the lower of the starting line
    and that rating.
    """
    start_rating = _new_team_start_rating(params, team_states)
    if prior_rating is not None:
        start_rating = min(start_rating, prior_rating)

    return TeamState(
        team_id=team_id,
        offense=start_rating / 2,
        defense=start_rating / 2,
        confidence=params.conf_new,
        games_played=0,
        miss_debt=0.0,
    )


def new_league_team(team_id: str, params: Params) -> TeamState:
    """The launch case: an all-new league starts every team at 0, provisional.

    Nobody is better than anyone else on day one, so everyone begins at
    the average and the ratings sort themselves out from there.
    """
    return TeamState(
        team_id=team_id,
        offense=0.0,
        defense=0.0,
        confidence=params.conf_new,
        games_played=0,
        miss_debt=0.0,
    )


def win_probability(expected_margin: float, params: Params) -> float:
    """Logistic link from expected margin in points to win probability."""
    return 1 / (1 + math.exp(-expected_margin / params.prob_scale))


def predict(team_a: TeamState, team_b: TeamState, params: Params) -> Prediction:
    """Expected points for each side given both teams' halves.

    A's offense plays B's defense and vice versa, so the expected margin
    simplifies to the difference of the full ratings.
    """
    expected_points_a = params.base_score + team_a.offense - team_b.defense
    expected_points_b = params.base_score + team_b.offense - team_a.defense
    expected_margin = expected_points_a - expected_points_b

    return Prediction(
        expected_points_a=expected_points_a,
        expected_points_b=expected_points_b,
        expected_margin=expected_margin,
        win_probability_a=win_probability(expected_margin, params),
    )


def apply_game(
    team_a: TeamState,
    team_b: TeamState,
    params: Params,
    score_a: int,
    score_b: int,
) -> tuple[TeamState, TeamState]:
    """Move both teams' ratings after a game with these scores.

    A game where neither team reached the team minimum and the combined
    score fell short of the combined minimum was not a real game - it is
    a forfeit convention. The model skips it entirely: the returned teams
    are the exact same objects, so nobody gains and nobody loses.
    """
    if max(score_a, score_b) < params.min_team_score and score_a + score_b < params.min_combined_score:
        return team_a, team_b

    prediction = predict(team_a, team_b, params)

    total_score_surprise = (score_a + score_b) - (prediction.expected_points_a + prediction.expected_points_b)
    margin_surprise = (score_a - score_b) - prediction.expected_margin

    step_size_a = _step_size(team_a, team_b.confidence, params)
    step_size_b = _step_size(team_b, team_a.confidence, params)

    updated_team_a = _apply_game_to_team(
        team=team_a,
        params=params,
        step_size=step_size_a,
        total_surprise=_clamp_surprise(total_score_surprise, step_size_a, params),
        margin_surprise=_clamp_surprise(margin_surprise, step_size_a, params),
    )

    # Team b's margin surprise is the mirror image of team a's.
    updated_team_b = _apply_game_to_team(
        team=team_b,
        params=params,
        step_size=step_size_b,
        total_surprise=_clamp_surprise(total_score_surprise, step_size_b, params),
        margin_surprise=_clamp_surprise(-margin_surprise, step_size_b, params),
    )

    return updated_team_a, updated_team_b


def apply_miss(team: TeamState, params: Params) -> TeamState:
    """A missed game: confidence shrinks and the penalty debt grows.

    The true halves never move - the model does not punish a rating for
    a game that never happened. Only the displayed number drops.
    """
    return msgspec.structs.replace(
        team,
        confidence=max(params.conf_min, team.confidence * (1 - params.miss_decay)),
        miss_debt=min(team.miss_debt + 1.0, params.miss_debt_max),
        games_missed=team.games_missed + 1,
    )


def apply_overturn(
    victim_team: TeamState,
    hacking_team: TeamState,
    params: Params,
) -> tuple[TeamState, TeamState]:
    """An overturned game: a player on the hacking team was caught cheating.

    The game cannot stand, so it is replayed as an automatic win for the
    victim at the smallest score that counts - the victim gets the win,
    the hacker's side eats the loss. Call this with both teams' PRE-GAME
    states: the original game's effect is discarded entirely, as if the
    game had gone the automatic score instead. The bot keeps the pre-game
    snapshot for any game a moderator can overturn.
    """
    return apply_game(victim_team, hacking_team, params, params.min_team_score, 0)


def _new_team_start_rating(
    params: Params,
    team_states: dict[str, TeamState] | None,
) -> float:
    """Where a new team starts: a percentile of the established pool's ratings.

    Only teams with enough games to be meaningful count toward the pool,
    so one-game wonders cannot drag the starting line around. With fewer
    than eight established teams there is no pool to measure, so the
    configured fallback is used.
    """
    if team_states is None:
        return params.new_team_start_fallback

    established_ratings = [
        displayed_rating(team, params)
        for team in team_states.values()
        if team.games_played >= params.new_team_start_min_games
    ]

    if len(established_ratings) < 8:
        return params.new_team_start_fallback

    sorted_ratings = sorted(established_ratings)
    percentile_position = params.new_team_start_percentile / 100 * (len(sorted_ratings) - 1)
    lower_index = int(percentile_position)
    upper_index = min(lower_index + 1, len(sorted_ratings) - 1)
    interpolation_fraction = percentile_position - lower_index

    return (
        sorted_ratings[lower_index] * (1 - interpolation_fraction)
        + sorted_ratings[upper_index] * interpolation_fraction
    )


def recenter(team_states: dict[str, TeamState]) -> dict[str, TeamState]:
    """Shift every team so the league averages sit at exactly zero.

    Offense and defense are centered separately - centering only the sum
    would leave a slow drift where the whole league's offense creeps up
    and defense creeps down without changing any ratings.
    """
    if not team_states:
        return team_states

    average_offense = sum(team.offense for team in team_states.values()) / len(team_states)
    average_defense = sum(team.defense for team in team_states.values()) / len(team_states)

    return {
        team_id: msgspec.structs.replace(
            team,
            offense=team.offense - average_offense,
            defense=team.defense - average_defense,
        )
        for team_id, team in team_states.items()
    }


def season_boundary(team_states: dict[str, TeamState], params: Params) -> dict[str, TeamState]:
    """Between seasons every team's confidence decays - time away erodes certainty.

    Calibration only: the circuit structure has no season gaps, missed games
    are the decay channel there.
    """
    return {
        team_id: msgspec.structs.replace(
            team,
            confidence=max(params.conf_min, team.confidence * (1 - params.season_gap_decay)),
        )
        for team_id, team in team_states.items()
    }


def pearson_correlation(values_x: list[float], values_y: list[float]) -> float:
    """Pearson correlation between two equal-length lists of numbers."""
    if len(values_x) < 2:
        return 0.0

    mean_x = sum(values_x) / len(values_x)
    mean_y = sum(values_y) / len(values_y)

    covariance = sum((x - mean_x) * (y - mean_y) for x, y in zip(values_x, values_y, strict=True))
    variance_x = sum((x - mean_x) ** 2 for x in values_x)
    variance_y = sum((y - mean_y) ** 2 for y in values_y)

    if variance_x == 0 or variance_y == 0:
        return 0.0

    return covariance / math.sqrt(variance_x * variance_y)


def _step_size(team: TeamState, opponent_confidence: float, params: Params) -> float:
    """How far this team's rating moves per point of surprise.

    Confidence scales the step linearly: a brand-new team (confidence at
    conf_new) moves provisional_mult times as far as a veteran (confidence 1).
    The opponent's confidence gates it too - a game against an unproven
    team teaches less, so veterans do not farm rating off unknowns.
    """
    own_step = params.base_k * (1 + (params.provisional_mult - 1) * (1 - team.confidence))
    opponent_gate = 1 - (1 - opponent_confidence) * params.unknown_opponent_discount

    return own_step * opponent_gate


def _clamp_surprise(surprise: float, step_size: float, params: Params) -> float:
    """Clamp a surprise so the resulting rating move respects cap_total.

    The surprise is clamped before multiplying by the step size, so no
    single game can move a rating past the cap no matter how wild.
    """
    surprise_limit = params.cap_total / step_size
    return max(-surprise_limit, min(surprise_limit, surprise))


def _apply_game_to_team(
    team: TeamState,
    params: Params,
    step_size: float,
    total_surprise: float,
    margin_surprise: float,
) -> TeamState:
    """Move one team's halves, confidence, and debt after a game.

    The two surprises are mixed into the halves: the total-score surprise
    carries the offense/defense split information (a 55-49 win says
    offense, a 28-21 win says defense), the margin surprise carries
    overall strength. Their mix keeps the full rating move equal to
    step_size * margin_surprise.
    """
    offense_change = step_size * (params.w_total * total_surprise + margin_surprise) / 2
    defense_change = step_size * (margin_surprise - params.w_total * total_surprise) / 2

    return msgspec.structs.replace(
        team,
        offense=team.offense + offense_change,
        defense=team.defense + defense_change,
        confidence=team.confidence + (1 - team.confidence) * params.conf_growth,
        games_played=team.games_played + 1,
        miss_debt=max(0.0, team.miss_debt - 0.5),  # every game restores half a missed game
    )
