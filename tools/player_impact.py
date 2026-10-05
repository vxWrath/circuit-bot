"""Player impact ratings - adjusted plus-minus for FF Circuit.

Every game records who stepped onto the field. The residual between the
actual margin and what the team ratings predicted is explained by the
players: a player whose teams consistently beat expectations has a
positive impact, and because players move between teams the model can
tell a good player apart from a good team.

The estimator is ridge regression solved by conjugate gradients in pure
Python - fast enough for a few hundred players over a season. If the
player pool ever grows past a couple thousand, swap this for numpy's
linear algebra, but the equations stay the same.
"""

import msgspec


class PlayerGame(msgspec.Struct):
    """One game for the impact regression: the margin residual and both lineups."""

    game_id: str
    margin_residual: float  # actual margin minus the team-ratings prediction
    team_a_players: list[str]
    team_b_players: list[str]


def estimate_player_impacts(
    player_games: list[PlayerGame],
    regularization: float = 20.0,
    max_iterations: int = 200,
    convergence_tolerance: float = 1e-12,
) -> dict[str, float]:
    """Ridge regression of margin residuals on player appearance.

    Each game is a row: +1 for a player on team a, -1 for team b. The
    regularizer shrinks players with few appearances toward zero and keeps
    roster groups that always play together from producing wild values -
    rosters are collinear by construction, so the penalty has to be a real
    fraction of a season's appearance count, not a token one.
    """
    player_columns: dict[str, int] = {}
    column_index = 0

    for game in player_games:
        for player_id in game.team_a_players + game.team_b_players:
            if player_id not in player_columns:
                player_columns[player_id] = column_index
                column_index += 1

    column_count = len(player_columns)

    if column_count == 0:
        return {}

    # Build X^T X and X^T y directly - never materialize the game x player
    # matrix. Diagonal: games appeared in. Off-diagonal: same-side
    # co-appearances minus opposite-side co-appearances.
    gram_matrix: dict[tuple[int, int], float] = {}
    cross_product: list[float] = [0.0] * column_count

    for game in player_games:
        for player_id in game.team_a_players:
            column = player_columns[player_id]
            gram_matrix[(column, column)] = gram_matrix.get((column, column), 0.0) + 1.0
            cross_product[column] += game.margin_residual

        for player_id in game.team_b_players:
            column = player_columns[player_id]
            gram_matrix[(column, column)] = gram_matrix.get((column, column), 0.0) + 1.0
            cross_product[column] -= game.margin_residual

        for player_id_a in game.team_a_players:
            column_a = player_columns[player_id_a]
            for player_id_b in game.team_a_players:
                column_b = player_columns[player_id_b]
                if column_a < column_b:
                    key = (column_a, column_b)
                    gram_matrix[key] = gram_matrix.get(key, 0.0) + 1.0
            for player_id_b in game.team_b_players:
                column_b = player_columns[player_id_b]
                key = (column_a, column_b) if column_a < column_b else (column_b, column_a)
                gram_matrix[key] = gram_matrix.get(key, 0.0) - 1.0

        for player_id_a in game.team_b_players:
            column_a = player_columns[player_id_a]
            for player_id_b in game.team_b_players:
                column_b = player_columns[player_id_b]
                if column_a < column_b:
                    key = (column_a, column_b)
                    gram_matrix[key] = gram_matrix.get(key, 0.0) + 1.0

    # (X^T X + lambda I) v = gram v + lambda v. The main loop handles the
    # off-diagonal entries; the diagonal is applied once at the end.
    def apply_system_matrix(vector: dict[int, float]) -> dict[int, float]:
        result_vector: dict[int, float] = {}

        for column in range(column_count):
            result_vector[column] = regularization * vector.get(column, 0.0)

        for (column_a, column_b), value in gram_matrix.items():
            if column_a == column_b:
                continue
            result_vector[column_a] = result_vector.get(column_a, 0.0) + value * vector.get(column_b, 0.0)
            result_vector[column_b] = result_vector.get(column_b, 0.0) + value * vector.get(column_a, 0.0)

        for column in range(column_count):
            result_vector[column] = result_vector.get(column, 0.0) + gram_matrix.get(
                (column, column), 0.0
            ) * vector.get(column, 0.0)

        return result_vector

    # Conjugate gradients on (X^T X + lambda I) beta = X^T y.
    solution: dict[int, float] = {column: 0.0 for column in range(column_count)}
    residual = {column: cross_product[column] for column in range(column_count)}
    direction = dict(residual)
    residual_squared_norm = sum(value * value for value in residual.values())

    for _iteration in range(max_iterations):
        if residual_squared_norm < convergence_tolerance:
            break

        matrix_times_direction = apply_system_matrix(direction)
        direction_quadratic = sum(direction[column] * matrix_times_direction.get(column, 0.0) for column in direction)

        if direction_quadratic <= 0:
            break

        step_length = residual_squared_norm / direction_quadratic

        for column in direction:
            solution[column] = solution[column] + step_length * direction[column]
        for column in matrix_times_direction:
            residual[column] = residual.get(column, 0.0) - step_length * matrix_times_direction[column]

        next_residual_squared_norm = sum(value * value for value in residual.values())

        if next_residual_squared_norm < convergence_tolerance:
            break

        direction_scale = next_residual_squared_norm / residual_squared_norm

        for column in residual:
            direction[column] = residual[column] + direction_scale * direction.get(column, 0.0)

        residual_squared_norm = next_residual_squared_norm

    player_id_by_column = {column: player_id for player_id, column in player_columns.items()}

    return {player_id_by_column[column]: solution.get(column, 0.0) for column in range(column_count)}
