"""Loading and preprocessing of historical game JSON files.

The raw files come from gather_data.py: each game has two teams with
string scores and a timestamp. Before the rating model sees them, games
are sorted chronologically, forfeit-convention games are dropped, absurd
scores are winsorized, and team order is normalized.
"""

import hashlib

import msgspec

from tools.params import Params


class RawGame(msgspec.Struct):
    """One game exactly as stored in games/*.json."""

    id: int | str | None  # some files have numeric ids, some null
    team_one_id: str
    team_one_name: str | None
    team_one_score: str
    team_two_id: str
    team_two_name: str | None
    team_two_score: str
    timestamp: str


class ProcessedGame(msgspec.Struct):
    """One game ready for the rating model: oriented, filtered, winsorized."""

    game_id: str
    team_a_id: str
    team_b_id: str
    score_a: int
    score_b: int
    timestamp: str


class PreprocessCounts(msgspec.Struct):
    """What preprocessing did to one file, for the calibration report."""

    total_raw_games: int = 0
    skipped_not_counting: int = 0
    winsorized_scores: int = 0
    kept_games: int = 0


def load_games(file_path: str) -> list[RawGame]:
    with open(file_path, "rb") as file:
        return msgspec.json.decode(file.read(), type=list[RawGame])


def preprocess_games(
    raw_games: list[RawGame],
    params: Params,
    source_name: str,
    randomize_orientation: bool = True,
) -> tuple[list[ProcessedGame], PreprocessCounts]:
    """Turn raw games into model-ready games, oldest first.

    The winner is listed first in 92.7% of raw games, so order carries no
    information and would bias the offense/defense split. Orientation is
    decided by a content hash instead, so the same game always gets the
    same orientation no matter when or how often it is processed.
    """
    counts = PreprocessCounts(total_raw_games=len(raw_games))

    processed_games: list[ProcessedGame] = []

    for game_index, raw_game in enumerate(sorted(raw_games, key=lambda game: game.timestamp)):
        score_one = int(raw_game.team_one_score)
        score_two = int(raw_game.team_two_score)

        # A game counts only if one team reached the team minimum or the
        # combined score reached the combined minimum - otherwise it is a
        # forfeit convention, and the model skips it.
        if max(score_one, score_two) < params.min_team_score and score_one + score_two < params.min_combined_score:
            counts.skipped_not_counting += 1
            continue

        if score_one > params.score_cap or score_two > params.score_cap:
            counts.winsorized_scores += 1

        score_one = min(score_one, int(params.score_cap))
        score_two = min(score_two, int(params.score_cap))

        if randomize_orientation and _should_swap(raw_game):
            team_a_id, team_b_id = raw_game.team_two_id, raw_game.team_one_id
            score_a, score_b = score_two, score_one
        else:
            team_a_id, team_b_id = raw_game.team_one_id, raw_game.team_two_id
            score_a, score_b = score_one, score_two

        processed_games.append(
            ProcessedGame(
                game_id=f"{source_name}:{game_index}",
                team_a_id=team_a_id,
                team_b_id=team_b_id,
                score_a=score_a,
                score_b=score_b,
                timestamp=raw_game.timestamp,
            )
        )

    counts.kept_games = len(processed_games)

    return processed_games, counts


def _should_swap(raw_game: RawGame) -> bool:
    """Hash-parity decision: stable for the same game, random across games."""
    game_identity = f"{raw_game.team_one_id}|{raw_game.team_two_id}|{raw_game.timestamp}"
    identity_hash = hashlib.sha256(game_identity.encode()).digest()
    return identity_hash[0] & 1 == 1
