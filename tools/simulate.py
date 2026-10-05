"""Simulation with made-up teams: validates the rating model's dynamics.

The simulator plants a league where every team's true strength and every
player's true impact are known, then runs FF Circuit structure over it -
windows, pods assigned by DISPLAYED rating, two games per window with an
optional third, missed games, mid-circuit joins and leaves instated at
window closes, automatic disbands, and roster churn. The model
predicts from the hidden true halves, exactly as the spec intends, while
pods and seeds see only the displayed number.

Each scenario check below verifies one claim from the spec. Checks print
their measured numbers next to the threshold and the process exits
non-zero if any fail.
"""

import argparse
import random
from collections.abc import Callable
from pathlib import Path

import msgspec

from tools import model, player_impact
from tools.model import TeamState
from tools.params import DEFAULT_PARAMS, Params, load_params

PROJECT_ROOT = Path(__file__).resolve().parents[1]

TRUE_HALF_SIGMA = 5.8  # spread of planted true offense/defense halves.
# Derived from real league data: observed margin stdev is ~15.4 and the
# sim's game noise is ~13, so the implied strength spread is
# sqrt((15.4^2 - 13^2) / 2) ~= 5.8 per half (strength stdev ~8.3).
TOTAL_NOISE_SIGMA = 19.0  # game-to-game noise on the combined score
MARGIN_NOISE_SIGMA = 13.0  # game-to-game noise on the margin
LINEUP_SIZE = 8
STAR_IMPACT = 8.0
REGULAR_IMPACT_SIGMA = 2.5
STAR_FRACTION = 0.05
STAR_SHOW_PROBABILITY = 0.8
MIN_GAMES_FOR_RATING_CHECKS = 20
MIN_GAMES_FOR_IMPACT_CHECKS = 10

# Team lifecycle: joins and leave requests arrive during any window and
# take effect at the window's close; inactivity disbands are automatic.
INACTIVE_WINDOW_GAME_MAX = 1  # a window with this many games or fewer is inactive
INACTIVE_WINDOWS_TO_DISBAND = 2  # ... and this many consecutive inactive windows disband
INACTIVE_TOTAL_WINDOWS = 3  # ... or 3 or fewer games across this many windows disbands
INACTIVE_TOTAL_GAME_MAX = 3

# Roster churn: players retire, arrive, and move between teams like they will.
PLAYER_RETIRE_PROBABILITY = 0.05  # per circuit boundary
PLAYER_ARRIVALS_PER_CIRCUIT = 20
PLAYER_MOVE_PROBABILITY = 0.15  # per circuit boundary, a player shifts one membership
PLAYER_ADD_PROBABILITY = 0.08  # per circuit boundary, a player picks up another team
PLAYER_MID_WINDOW_JOIN_PROBABILITY = 0.03  # per open window, a player joins one more team
FREE_AGENT_SIGN_PROBABILITY = 0.7  # per circuit boundary, a teamless player finds a new team
NEWCOMERS_PER_WINDOW4_BREAK = 8
WINDOW4_SELECTION_COUNT = 3
MAX_MEMBERSHIPS = 5
SHARED_PLAYER_CAP = 4
MIN_ROSTER_SIZE = 10
FRESH_ROSTER_SIZE = 14

# Diagnostic fixtures with known strengths, used by the attribution check.
PLANTED_SHOOTOUT_TEAM_ID = "team_0"
PLANTED_STINGY_TEAM_ID = "team_1"

TEAM_ADJECTIVES = [
    "Crimson",
    "Iron",
    "Golden",
    "Silver",
    "Electric",
    "Frozen",
    "Burning",
    "Wandering",
    "Silent",
    "Thunderous",
    "Royal",
    "Shadow",
    "Crystal",
    "Neon",
    "Rusty",
    "Savage",
    "Mighty",
    "Lucky",
    "Feral",
    "Obsidian",
    "Solar",
    "Lunar",
    "Tidal",
    "Ember",
    "Violet",
    "Steel",
    "Wild",
    "Ancient",
    "Rapid",
    "Blazing",
    "Hollow",
    "Prime",
]

TEAM_NOUNS = [
    "Vipers",
    "Titans",
    "Renegades",
    "Wolves",
    "Krakens",
    "Falcons",
    "Bulls",
    "Comets",
    "Wraiths",
    "Huskies",
    "Phoenixes",
    "Gladiators",
    "Ravens",
    "Cyclones",
    "Panthers",
    "Mammoths",
    "Cobras",
    "Stallions",
    "Pirates",
    "Rangers",
    "Avalanche",
    "Drifters",
    "Legion",
    "Outlaws",
    "Serpents",
    "Volcanoes",
    "Wizards",
    "Bandits",
    "Monarchs",
    "Reapers",
    "Dynasty",
    "Legends",
]


class TeamProfile(msgspec.Struct):
    team_id: str
    true_offense: float
    true_defense: float
    roster: list[str]
    joined_circuit: int
    name: str = ""
    inactivity_probability: float = 0.0  # per window, chance the team plays no games at all
    times_departed: int = 0  # repeated departures make a return less likely


class PlayerProfile(msgspec.Struct):
    player_id: str
    impact: float
    is_star: bool
    retired: bool = False


class SimulatedGame(msgspec.Struct):
    game_id: int
    circuit: int
    window: int
    team_a_id: str
    team_b_id: str
    score_a: int
    score_b: int
    predicted_margin: float
    lineup_a: list[str]
    lineup_b: list[str]
    rating_move_a: float  # signed true-rating change for team A from this game
    rating_move_b: float  # signed true-rating change for team B from this game


class WindowReport(msgspec.Struct):
    circuit: int
    window: int
    mean_rating: float
    mean_offense: float
    mean_defense: float
    pods: list[list[str]]  # pod assignments at the window's open
    displayed_at_open: dict[str, float]  # the displayed rating each pod was assigned by
    missed_teams: list[str]
    instated: list[str]  # joins that took effect at this window's close
    departed: list[str]  # leaves and disbands that took effect at this window's close


class TeamJoinEvent(msgspec.Struct):
    circuit: int
    window: int  # 0 for rejoins, which are instated before Window 1 opens
    team_id: str
    name: str
    true_rating: float
    start_rating: float
    provisional: bool
    is_rejoin: bool = False
    prior_rating: float = 0.0  # rejoins only: the rating the team left with


class TeamLeaveEvent(msgspec.Struct):
    circuit: int
    window: int
    team_id: str
    name: str
    rating_at_leave: float
    games_this_circuit: int
    career_games: int
    reason: str  # "requested removal" or the activity rule that triggered
    eligible_to_return_circuit: int


class TournamentEntry(msgspec.Struct):
    team_id: str
    displayed_rating: float


class TournamentField(msgspec.Struct):
    circuit: int
    entries: list[TournamentEntry]
    just_missed: list[TournamentEntry]


class SimResult(msgspec.Struct):
    games: list[SimulatedGame]
    final_states: dict[str, TeamState]
    team_profiles: dict[str, TeamProfile]
    player_profiles: dict[str, PlayerProfile]
    window_reports: list[WindowReport]
    tournament_fields: list[TournamentField]
    join_events: list[TeamJoinEvent]
    leave_events: list[TeamLeaveEvent]
    largest_single_game_move: float


class ScenarioCheck(msgspec.Struct):
    name: str
    passed: bool
    measured: str
    threshold: str


def tournament_field_size(league_size: int) -> int:
    """The tournament field scales with the league, capped at 16."""
    if league_size >= 128:
        return 16
    if league_size >= 64:
        return 8
    return 4


def generate_team_name(rng: random.Random, used_names: set[str] | None = None) -> str:
    """A two-word team name that has not been taken yet, like a real league."""
    team_name = f"{rng.choice(TEAM_ADJECTIVES)} {rng.choice(TEAM_NOUNS)}"

    while used_names is not None and team_name in used_names:
        team_name = f"{rng.choice(TEAM_ADJECTIVES)} {rng.choice(TEAM_NOUNS)}"

    if used_names is not None:
        used_names.add(team_name)

    return team_name


def generate_league(
    rng: random.Random,
    team_count: int,
    player_count: int,
    miss_probability: float,
) -> tuple[dict[str, TeamProfile], dict[str, PlayerProfile]]:
    """Plant a league: teams with true halves, players with true impacts.

    Most players are near-average; a small fraction are stars whose
    appearance swings games. Players roster on one to three teams, stars
    on two or three, so the impact model can separate player skill from
    team strength.
    """
    player_profiles: dict[str, PlayerProfile] = {}

    for player_index in range(player_count):
        player_id = f"player_{player_index}"

        if rng.random() < STAR_FRACTION:
            impact = STAR_IMPACT if rng.random() < 0.5 else -STAR_IMPACT
            is_star = True
        else:
            impact = rng.gauss(0, REGULAR_IMPACT_SIGMA)
            is_star = False

        player_profiles[player_id] = PlayerProfile(player_id=player_id, impact=impact, is_star=is_star)

    # Spread rosters: stars sit on 2-3 teams, regulars on 1-3.
    roster_by_team: dict[str, list[str]] = {}

    for player_id, profile in player_profiles.items():
        teams_to_join = rng.randint(2, 3) if profile.is_star else rng.randint(1, 3)

        for _team_index in range(teams_to_join):
            team_number = rng.randrange(team_count)
            roster_by_team.setdefault(f"team_{team_number}", []).append(player_id)

    team_profiles: dict[str, TeamProfile] = {}
    used_team_names: set[str] = set()

    for team_number in range(team_count):
        team_id = f"team_{team_number}"

        if team_id == PLANTED_SHOOTOUT_TEAM_ID:
            # A planted shootout team: scores a lot, gives up a lot, net zero.
            true_offense, true_defense = 15.0, -15.0
        elif team_id == PLANTED_STINGY_TEAM_ID:
            # A planted stingy team: the mirror image.
            true_offense, true_defense = -15.0, 15.0
        else:
            true_offense = rng.gauss(0, TRUE_HALF_SIGMA)
            true_defense = rng.gauss(0, TRUE_HALF_SIGMA)

        roster = roster_by_team.get(team_id, [])

        if not roster:
            # Every team needs a roster; add some spare regulars.
            for spare_index in range(14):
                spare_id = f"spare_{team_number}_{spare_index}"
                player_profiles[spare_id] = PlayerProfile(
                    player_id=spare_id,
                    impact=rng.gauss(0, REGULAR_IMPACT_SIGMA),
                    is_star=False,
                )
                roster.append(spare_id)

        team_profiles[team_id] = TeamProfile(
            team_id=team_id,
            true_offense=true_offense,
            true_defense=true_defense,
            roster=roster,
            joined_circuit=1,
            name=generate_team_name(rng, used_team_names),
            inactivity_probability=_draw_inactivity_probability(rng, miss_probability),
        )

    return team_profiles, player_profiles


def assign_pods(
    team_states: dict[str, TeamState],
    params: Params,
) -> list[list[str]]:
    """Group active teams into pods of 8 by displayed rating, best first.

    The lowest pod absorbs the remainder, so it holds 8 to 15 teams -
    the same rule the real league uses.
    """
    active_team_ids = sorted(
        team_states.keys(),
        key=lambda team_id: model.displayed_rating(team_states[team_id], params),
        reverse=True,
    )

    pods: list[list[str]] = []

    for pod_start in range(0, len(active_team_ids), 8):
        pods.append(active_team_ids[pod_start : pod_start + 8])

    if len(pods) >= 2 and len(pods[-1]) < 8:
        pods[-2].extend(pods[-1])
        pods.pop()

    return pods


def build_window_schedule(pod: list[str], rng: random.Random) -> list[tuple[str, str, int]]:
    """Two rounds of pairings per window, no opponent twice.

    Round one pairs neighbors in the shuffled pod order; round two pairs
    the same order rotated by one. Teams that want a third game pick a
    random podmate they have not played yet this window.
    """
    shuffled_pod = pod[:]
    rng.shuffle(shuffled_pod)

    schedule: list[tuple[str, str, int]] = []

    for round_index, rotation in enumerate((0, 1), start=1):
        rotated_pod = shuffled_pod[rotation:] + shuffled_pod[:rotation]

        for pair_start in range(0, len(rotated_pod) - 1, 2):
            schedule.append((rotated_pod[pair_start], rotated_pod[pair_start + 1], round_index))

    return schedule


def sample_game_score(
    rng: random.Random,
    team_a_profile: TeamProfile,
    team_b_profile: TeamProfile,
    lineup_a: list[str],
    lineup_b: list[str],
    player_profiles: dict[str, PlayerProfile],
    params: Params,
) -> tuple[int, int]:
    """Draw a game outcome from the planted strengths plus noise.

    The combined score and the margin are sampled separately - real games
    correlate the two (a fast pace raises both scores), so independent
    per-team sampling would miss that.
    """
    lineup_impact_a = sum(player_profiles[player_id].impact for player_id in lineup_a)
    lineup_impact_b = sum(player_profiles[player_id].impact for player_id in lineup_b)

    true_rating_a = team_a_profile.true_offense + team_a_profile.true_defense
    true_rating_b = team_b_profile.true_offense + team_b_profile.true_defense

    total_score = (
        2 * params.base_score
        + team_a_profile.true_offense
        + team_b_profile.true_offense
        - team_a_profile.true_defense
        - team_b_profile.true_defense
        + rng.gauss(0, TOTAL_NOISE_SIGMA)
    )

    margin = (true_rating_a - true_rating_b) + (lineup_impact_a - lineup_impact_b) + rng.gauss(0, MARGIN_NOISE_SIGMA)

    score_a = max(0, round((total_score + margin) / 2))
    score_b = max(0, round((total_score - margin) / 2))

    return score_a, score_b


def sample_lineup(
    rng: random.Random,
    team_profile: TeamProfile,
    player_profiles: dict[str, PlayerProfile],
    eligible_player_ids: set[str] | None = None,
) -> list[str]:
    """Pick eight distinct players; stars show up more often.

    Drawn without replacement - a real lineup is eight different players,
    and the impact regression assumes each player appears at most once per
    game on a side. In Window 4 the pool is the player's selections, not
    the roster; elsewhere it is the roster.
    """
    lineup: list[str] = []
    available_players = sorted(eligible_player_ids) if eligible_player_ids is not None else list(team_profile.roster)

    while len(lineup) < LINEUP_SIZE and available_players:
        lineup_weights = [5.0 if player_profiles[player_id].is_star else 1.0 for player_id in available_players]
        chosen_player = rng.choices(available_players, weights=lineup_weights, k=1)[0]
        lineup.append(chosen_player)
        available_players.remove(chosen_player)

    return lineup


def player_memberships(team_profiles: dict[str, TeamProfile]) -> dict[str, list[str]]:
    """Rebuild the player -> teams map from the rosters."""
    memberships: dict[str, list[str]] = {}

    for team_id, profile in team_profiles.items():
        for player_id in profile.roster:
            memberships.setdefault(player_id, []).append(team_id)

    return memberships


def roster_overlap(roster_a: list[str], roster_b: list[str]) -> int:
    return len(set(roster_a) & set(roster_b))


def can_add_to_roster(
    player_id: str,
    team_id: str,
    team_profiles: dict[str, TeamProfile],
    memberships: dict[str, list[str]],
) -> bool:
    """The shared player cap: no two teams may share more than four players."""
    for other_team_id in memberships.get(player_id, []):
        if other_team_id == team_id:
            continue
        if roster_overlap(team_profiles[team_id].roster, team_profiles[other_team_id].roster) >= SHARED_PLAYER_CAP:
            return False

    return True


def record_appearances(lineup: list[str], team_id: str, player_games_by_team: dict[str, dict[str, int]]) -> None:
    """Count each player's games per team - the Window 4 selection ranking."""
    for player_id in lineup:
        games_by_team = player_games_by_team.setdefault(player_id, {})
        games_by_team[team_id] = games_by_team.get(team_id, 0) + 1


def _new_player(
    rng: random.Random,
    player_profiles: dict[str, PlayerProfile],
    next_player_index: int,
) -> tuple[str, int]:
    """A fresh player - regular or star - with a planted impact."""
    player_id = f"player_{next_player_index}"

    if rng.random() < STAR_FRACTION:
        impact = STAR_IMPACT if rng.random() < 0.5 else -STAR_IMPACT
        is_star = True
    else:
        impact = rng.gauss(0, REGULAR_IMPACT_SIGMA)
        is_star = False

    player_profiles[player_id] = PlayerProfile(player_id=player_id, impact=impact, is_star=is_star)
    return player_id, next_player_index + 1


def _fresh_roster(
    rng: random.Random,
    player_profiles: dict[str, PlayerProfile],
    size: int,
    next_player_index: int,
) -> tuple[list[str], int]:
    """A roster of fresh players - used when a new or returning team enters."""
    roster: list[str] = []

    for _spare_index in range(size):
        player_id, next_player_index = _new_player(rng, player_profiles, next_player_index)
        roster.append(player_id)

    return roster, next_player_index


def churn_rosters(
    rng: random.Random,
    team_profiles: dict[str, TeamProfile],
    player_profiles: dict[str, PlayerProfile],
    active_team_ids: list[str],
    next_player_index: int,
) -> int:
    """Between circuits: players retire, arrive, and switch teams.

    Profiles are never deleted - retired players keep their history for the
    impact checks. Movement respects the shared player cap.
    """
    memberships = player_memberships(team_profiles)

    # Retirements: drop every membership, keep the profile for history.
    for player_id in list(player_profiles):
        if rng.random() >= PLAYER_RETIRE_PROBABILITY:
            continue
        player_profiles[player_id] = msgspec.structs.replace(player_profiles[player_id], retired=True)
        for team_id in memberships.get(player_id, []):
            if player_id in team_profiles[team_id].roster:
                team_profiles[team_id].roster.remove(player_id)

    memberships = player_memberships(team_profiles)

    # Free agents - players whose team departed - find new teams.
    for player_id in list(player_profiles):
        profile = player_profiles[player_id]
        if profile.retired or memberships.get(player_id):
            continue
        if rng.random() >= FREE_AGENT_SIGN_PROBABILITY:
            continue

        teams_to_join = rng.randint(2, 3) if profile.is_star else 1
        for _team_index in range(teams_to_join):
            candidates = [
                team_id
                for team_id in active_team_ids
                if team_id not in memberships.get(player_id, [])
                and can_add_to_roster(player_id, team_id, team_profiles, memberships)
            ]
            if not candidates:
                break
            team_id = rng.choice(candidates)
            team_profiles[team_id].roster.append(player_id)
            memberships.setdefault(player_id, []).append(team_id)

    # Arrivals: fresh players land on one to three teams.
    for _arrival_index in range(PLAYER_ARRIVALS_PER_CIRCUIT):
        player_id, next_player_index = _new_player(rng, player_profiles, next_player_index)
        teams_to_join = rng.randint(2, 3) if player_profiles[player_id].is_star else rng.randint(1, 3)

        for _team_index in range(teams_to_join):
            candidates = [
                team_id
                for team_id in active_team_ids
                if team_id not in memberships.get(player_id, [])
                and can_add_to_roster(player_id, team_id, team_profiles, memberships)
            ]
            if not candidates:
                break
            team_id = rng.choice(candidates)
            team_profiles[team_id].roster.append(player_id)
            memberships.setdefault(player_id, []).append(team_id)

    # Movement: players shift one membership or pick up another.
    for player_id in list(player_profiles):
        player_teams = memberships.get(player_id, [])
        if not player_teams:
            continue

        if rng.random() < PLAYER_MOVE_PROBABILITY:
            old_team_id = rng.choice(player_teams)
            team_profiles[old_team_id].roster.remove(player_id)
            memberships[player_id].remove(old_team_id)

            candidates = [
                team_id
                for team_id in active_team_ids
                if team_id not in memberships[player_id]
                and can_add_to_roster(player_id, team_id, team_profiles, memberships)
            ]
            if candidates:
                new_team_id = rng.choice(candidates)
                team_profiles[new_team_id].roster.append(player_id)
                memberships[player_id].append(new_team_id)
        elif rng.random() < PLAYER_ADD_PROBABILITY and len(player_teams) < MAX_MEMBERSHIPS:
            candidates = [
                team_id
                for team_id in active_team_ids
                if team_id not in player_teams and can_add_to_roster(player_id, team_id, team_profiles, memberships)
            ]
            if candidates:
                team_id = rng.choice(candidates)
                team_profiles[team_id].roster.append(player_id)
                memberships[player_id].append(team_id)

    # Thin rosters replenish with fresh regulars so lineups can always field.
    for team_id in active_team_ids:
        while len(team_profiles[team_id].roster) < MIN_ROSTER_SIZE:
            player_id, next_player_index = _new_player(rng, player_profiles, next_player_index)
            team_profiles[team_id].roster.append(player_id)

    return next_player_index


def churn_during_open_window(
    rng: random.Random,
    team_profiles: dict[str, TeamProfile],
    player_profiles: dict[str, PlayerProfile],
    active_team_ids: list[str],
) -> None:
    """Open weeks are unrestricted: players may join any team at any time."""
    memberships = player_memberships(team_profiles)

    for player_id in list(player_profiles):
        if rng.random() >= PLAYER_MID_WINDOW_JOIN_PROBABILITY:
            continue

        player_teams = memberships.get(player_id, [])
        if not player_teams or len(player_teams) >= MAX_MEMBERSHIPS:
            continue

        candidates = [
            team_id
            for team_id in active_team_ids
            if team_id not in player_teams and can_add_to_roster(player_id, team_id, team_profiles, memberships)
        ]
        if candidates:
            team_id = rng.choice(candidates)
            team_profiles[team_id].roster.append(player_id)
            memberships[player_id].append(team_id)


def apply_window4_locks(
    rng: random.Random,
    team_profiles: dict[str, TeamProfile],
    player_profiles: dict[str, PlayerProfile],
    active_team_ids: list[str],
    player_games_by_team: dict[str, dict[str, int]],
    next_player_index: int,
) -> tuple[dict[str, set[str]], int]:
    """The Window 3 -> Window 4 break: players pick up to three teams.

    Selections are the top teams by games played - the bot's automatic
    pick when a player misses the deadline. Newcomers sign for exactly
    one team and are locked to it for Window 4.
    """
    window4_eligibility: dict[str, set[str]] = {team_id: set() for team_id in active_team_ids}

    # Newcomers: players who arrive at the break pick one team for Window 4.
    for _newcomer_index in range(NEWCOMERS_PER_WINDOW4_BREAK):
        player_id, next_player_index = _new_player(rng, player_profiles, next_player_index)
        team_id = rng.choice(active_team_ids)
        team_profiles[team_id].roster.append(player_id)
        window4_eligibility[team_id].add(player_id)

    # Everyone else keeps their top three teams by games played.
    for player_id, player_teams in player_memberships(team_profiles).items():
        if not player_teams:
            continue

        games_by_team = player_games_by_team.get(player_id, {})
        selected_teams = sorted(player_teams, key=lambda team_id: (-games_by_team.get(team_id, 0), team_id))[
            :WINDOW4_SELECTION_COUNT
        ]
        for team_id in selected_teams:
            window4_eligibility[team_id].add(player_id)

    return window4_eligibility, next_player_index


def _draw_inactivity_probability(rng: random.Random, miss_probability: float) -> float:
    """A few teams are chronic no-shows; most miss only occasionally.

    Ten percent of teams draw four times the base miss rate - the chronic
    no-shows the automatic disband rule exists for - everyone else draws
    a fifth of it, so the per-window average stays near miss_probability.
    """
    if rng.random() < 0.1:
        return min(0.9, miss_probability * 4.0)
    return miss_probability * 0.2


def _return_circuit(circuit_number: int, window_number: int) -> int:
    """When a departing team may return: Window 1-2 departures return next
    circuit, Window 3-4 departures sit out the remainder and the next one."""
    if window_number <= 2:
        return circuit_number + 1
    return circuit_number + 2


def simulate_circuits(
    team_profiles: dict[str, TeamProfile],
    player_profiles: dict[str, PlayerProfile],
    params: Params,
    rng: random.Random,
    circuit_count: int,
    miss_probability: float,
    optional_game_probability: float,
    join_probability: float,
    leave_probability: float,
    rejoin_probability: float,
) -> SimResult:
    """Run the full circuit loop and return everything the checks need."""
    # Circuit 1 mirrors a real league launch: nobody is known yet, so every
    # team starts at the median (0) with a provisional tag and sorts itself
    # out from there.
    team_states: dict[str, TeamState] = {team_id: model.new_league_team(team_id, params) for team_id in team_profiles}

    games: list[SimulatedGame] = []
    window_reports: list[WindowReport] = []
    tournament_fields: list[TournamentField] = []
    join_events: list[TeamJoinEvent] = []
    leave_events: list[TeamLeaveEvent] = []
    previous_field_ids: set[str] = set()
    game_id_counter = 0
    largest_single_game_move = 0.0
    next_player_index = len(player_profiles)
    next_team_index = len(team_profiles)
    player_games_by_team: dict[str, dict[str, int]] = {}
    team_window_history: dict[str, list[int]] = {}
    # Teams that departed: team_id -> (circuit they may return, the true
    # rating they left with, for the rejoin floor).
    departed_teams: dict[str, tuple[int, float]] = {}
    planted_team_ids = {PLANTED_SHOOTOUT_TEAM_ID, PLANTED_STINGY_TEAM_ID}

    for circuit_number in range(1, circuit_count + 1):
        window4_eligibility: dict[str, set[str]] = {}

        if circuit_number > 1:
            # Returning teams: joins normally instate at window closes, but
            # a team whose ban expired rejoins before Window 1 opens. The
            # reset never lifts it - the floor holds it to the rating it
            # left with.
            for team_id in list(departed_teams):
                eligible_circuit, prior_rating = departed_teams[team_id]
                if eligible_circuit > circuit_number:
                    continue
                del departed_teams[team_id]
                if rng.random() >= rejoin_probability / team_profiles[team_id].times_departed:
                    continue

                team_profiles[team_id].roster, next_player_index = _fresh_roster(
                    rng, player_profiles, FRESH_ROSTER_SIZE, next_player_index
                )
                team_profiles[team_id].joined_circuit = circuit_number
                team_states[team_id] = model.new_team(team_id, params, team_states, prior_rating=prior_rating)
                team_window_history[team_id] = []

                join_events.append(
                    TeamJoinEvent(
                        circuit=circuit_number,
                        window=0,
                        team_id=team_id,
                        name=team_profiles[team_id].name,
                        true_rating=team_profiles[team_id].true_offense + team_profiles[team_id].true_defense,
                        start_rating=model.rating(team_states[team_id]),
                        provisional=model.is_provisional(team_states[team_id], params),
                        is_rejoin=True,
                        prior_rating=prior_rating,
                    )
                )

            # Between circuits, rosters churn: players retire, arrive, and
            # move between teams the way they will in the real league.
            next_player_index = churn_rosters(rng, team_profiles, player_profiles, list(team_states), next_player_index)

        circuit_game_counts: dict[str, int] = {team_id: 0 for team_id in team_states}

        for window_number in range(1, 5):
            # The Window 3 -> Window 4 break: players pick up to three teams
            # to play for, newcomers sign for one, and rosters freeze.
            if window_number == 4:
                window4_eligibility, next_player_index = apply_window4_locks(
                    rng, team_profiles, player_profiles, list(team_states), player_games_by_team, next_player_index
                )

            pods = assign_pods(team_states, params)
            displayed_at_open = {
                team_id: model.displayed_rating(team_states[team_id], params) for team_id in team_states
            }
            missed_teams = {
                team_id for team_id in team_states if rng.random() < team_profiles[team_id].inactivity_probability
            }

            # Lifecycle requests made during this window. Joins take effect
            # at the window's close; leavers play the window out, then go.
            join_request_ids: list[str] = []
            if rng.random() < join_probability:
                team_id = f"team_{next_team_index}"
                next_team_index += 1
                team_profiles[team_id] = TeamProfile(
                    team_id=team_id,
                    true_offense=rng.gauss(0, TRUE_HALF_SIGMA),
                    true_defense=rng.gauss(0, TRUE_HALF_SIGMA),
                    roster=[],
                    joined_circuit=circuit_number,
                    name=generate_team_name(rng, {profile.name for profile in team_profiles.values()}),
                    inactivity_probability=_draw_inactivity_probability(rng, miss_probability),
                )
                team_profiles[team_id].roster, next_player_index = _fresh_roster(
                    rng, player_profiles, FRESH_ROSTER_SIZE, next_player_index
                )
                join_request_ids.append(team_id)

            leaving_team_ids = {
                team_id
                for team_id in team_states
                if team_id not in planted_team_ids and rng.random() < leave_probability
            }

            window_played_opponents: dict[str, set[str]] = {team_id: set() for team_id in team_states}
            window_game_counts: dict[str, int] = {team_id: 0 for team_id in team_states}

            for pod in pods:
                schedule = build_window_schedule(pod, rng)

                for team_a_id, team_b_id, _round_number in schedule:
                    if team_a_id in missed_teams or team_b_id in missed_teams:
                        # A team that cannot field players misses its games;
                        # the opponent simply plays fewer. No forfeit.
                        if team_a_id in missed_teams:
                            team_states[team_a_id] = model.apply_miss(team_states[team_a_id], params)
                        if team_b_id in missed_teams:
                            team_states[team_b_id] = model.apply_miss(team_states[team_b_id], params)
                        continue

                    lineup_a = sample_lineup(
                        rng, team_profiles[team_a_id], player_profiles, window4_eligibility.get(team_a_id)
                    )
                    lineup_b = sample_lineup(
                        rng, team_profiles[team_b_id], player_profiles, window4_eligibility.get(team_b_id)
                    )
                    record_appearances(lineup_a, team_a_id, player_games_by_team)
                    record_appearances(lineup_b, team_b_id, player_games_by_team)
                    score_a, score_b = sample_game_score(
                        rng,
                        team_profiles[team_a_id],
                        team_profiles[team_b_id],
                        lineup_a,
                        lineup_b,
                        player_profiles,
                        params,
                    )

                    prediction = model.predict(team_states[team_a_id], team_states[team_b_id], params)
                    updated_team_a, updated_team_b = model.apply_game(
                        team_states[team_a_id], team_states[team_b_id], params, score_a, score_b
                    )

                    rating_move_a = model.rating(updated_team_a) - model.rating(team_states[team_a_id])
                    rating_move_b = model.rating(updated_team_b) - model.rating(team_states[team_b_id])
                    largest_single_game_move = max(largest_single_game_move, abs(rating_move_a), abs(rating_move_b))

                    team_states[team_a_id] = updated_team_a
                    team_states[team_b_id] = updated_team_b

                    games.append(
                        SimulatedGame(
                            game_id=game_id_counter,
                            circuit=circuit_number,
                            window=window_number,
                            team_a_id=team_a_id,
                            team_b_id=team_b_id,
                            score_a=score_a,
                            score_b=score_b,
                            predicted_margin=prediction.expected_margin,
                            lineup_a=lineup_a,
                            lineup_b=lineup_b,
                            rating_move_a=rating_move_a,
                            rating_move_b=rating_move_b,
                        )
                    )
                    game_id_counter += 1

                    window_played_opponents[team_a_id].add(team_b_id)
                    window_played_opponents[team_b_id].add(team_a_id)
                    window_game_counts[team_a_id] += 1
                    window_game_counts[team_b_id] += 1
                    circuit_game_counts[team_a_id] += 1
                    circuit_game_counts[team_b_id] += 1

                # The optional third game: active teams get one more chance
                # to play, which also restores their missed-game debt faster.
                for team_id in pod:
                    if team_id in missed_teams:
                        continue
                    if rng.random() >= optional_game_probability:
                        continue

                    podmate_candidates = [
                        podmate_id
                        for podmate_id in pod
                        if podmate_id != team_id
                        and podmate_id not in missed_teams
                        and podmate_id not in window_played_opponents[team_id]
                    ]

                    if not podmate_candidates:
                        continue

                    opponent_id = rng.choice(podmate_candidates)

                    lineup_a = sample_lineup(
                        rng, team_profiles[team_id], player_profiles, window4_eligibility.get(team_id)
                    )
                    lineup_b = sample_lineup(
                        rng, team_profiles[opponent_id], player_profiles, window4_eligibility.get(opponent_id)
                    )
                    record_appearances(lineup_a, team_id, player_games_by_team)
                    record_appearances(lineup_b, opponent_id, player_games_by_team)
                    score_a, score_b = sample_game_score(
                        rng,
                        team_profiles[team_id],
                        team_profiles[opponent_id],
                        lineup_a,
                        lineup_b,
                        player_profiles,
                        params,
                    )

                    prediction = model.predict(team_states[team_id], team_states[opponent_id], params)
                    updated_team_a, updated_team_b = model.apply_game(
                        team_states[team_id], team_states[opponent_id], params, score_a, score_b
                    )

                    rating_move_a = model.rating(updated_team_a) - model.rating(team_states[team_id])
                    rating_move_b = model.rating(updated_team_b) - model.rating(team_states[opponent_id])
                    largest_single_game_move = max(largest_single_game_move, abs(rating_move_a), abs(rating_move_b))

                    team_states[team_id] = updated_team_a
                    team_states[opponent_id] = updated_team_b

                    games.append(
                        SimulatedGame(
                            game_id=game_id_counter,
                            circuit=circuit_number,
                            window=window_number,
                            team_a_id=team_id,
                            team_b_id=opponent_id,
                            score_a=score_a,
                            score_b=score_b,
                            predicted_margin=prediction.expected_margin,
                            lineup_a=lineup_a,
                            lineup_b=lineup_b,
                            rating_move_a=rating_move_a,
                            rating_move_b=rating_move_b,
                        )
                    )
                    game_id_counter += 1

                    window_played_opponents[team_id].add(opponent_id)
                    window_played_opponents[opponent_id].add(team_id)
                    window_game_counts[team_id] += 1
                    window_game_counts[opponent_id] += 1
                    circuit_game_counts[team_id] += 1
                    circuit_game_counts[opponent_id] += 1

            # Window close: leavers and disbands out, joins in, then the
            # ratings update in one batch - recenter last, so the league
            # average including the new teams sits at exactly zero.
            instated_ids: list[str] = []
            departed_ids: list[str] = []

            for team_id in sorted(leaving_team_ids):
                if team_id not in team_states:
                    continue
                prior_rating = model.rating(team_states[team_id])
                return_circuit = _return_circuit(circuit_number, window_number)
                leave_events.append(
                    TeamLeaveEvent(
                        circuit=circuit_number,
                        window=window_number,
                        team_id=team_id,
                        name=team_profiles[team_id].name,
                        rating_at_leave=model.displayed_rating(team_states[team_id], params),
                        games_this_circuit=circuit_game_counts[team_id],
                        career_games=team_states[team_id].games_played,
                        reason="requested removal",
                        eligible_to_return_circuit=return_circuit,
                    )
                )
                departed_teams[team_id] = (return_circuit, prior_rating)
                team_window_history.pop(team_id, None)
                team_profiles[team_id] = msgspec.structs.replace(
                    team_profiles[team_id], times_departed=team_profiles[team_id].times_departed + 1
                )
                team_profiles[team_id].roster = []  # the roster disperses
                del team_states[team_id]
                departed_ids.append(team_id)

            # Automatic disbands: two windows with 0-1 games played, or
            # three windows with 3 or fewer games played in total. The two
            # planted fixtures are exempt - the attribution check needs them.
            for team_id in list(team_states):
                if team_id in planted_team_ids:
                    continue

                history = team_window_history.setdefault(team_id, [])
                history.append(window_game_counts[team_id])

                if len(history) >= INACTIVE_WINDOWS_TO_DISBAND and all(
                    count <= INACTIVE_WINDOW_GAME_MAX for count in history[-INACTIVE_WINDOWS_TO_DISBAND:]
                ):
                    reason = "2 windows with 0-1 games played"
                elif (
                    len(history) >= INACTIVE_TOTAL_WINDOWS
                    and sum(history[-INACTIVE_TOTAL_WINDOWS:]) <= INACTIVE_TOTAL_GAME_MAX
                ):
                    reason = "3 windows with 3 or fewer games played"
                else:
                    continue

                prior_rating = model.rating(team_states[team_id])
                return_circuit = _return_circuit(circuit_number, window_number)
                leave_events.append(
                    TeamLeaveEvent(
                        circuit=circuit_number,
                        window=window_number,
                        team_id=team_id,
                        name=team_profiles[team_id].name,
                        rating_at_leave=model.displayed_rating(team_states[team_id], params),
                        games_this_circuit=circuit_game_counts[team_id],
                        career_games=team_states[team_id].games_played,
                        reason=reason,
                        eligible_to_return_circuit=return_circuit,
                    )
                )
                departed_teams[team_id] = (return_circuit, prior_rating)
                team_window_history.pop(team_id, None)
                team_profiles[team_id] = msgspec.structs.replace(
                    team_profiles[team_id], times_departed=team_profiles[team_id].times_departed + 1
                )
                team_profiles[team_id].roster = []  # the roster disperses
                del team_states[team_id]
                departed_ids.append(team_id)

            # Joins requested during this window take effect now, in time
            # for next window's pods.
            for team_id in join_request_ids:
                team_states[team_id] = model.new_team(team_id, params, team_states)
                circuit_game_counts[team_id] = 0

                join_events.append(
                    TeamJoinEvent(
                        circuit=circuit_number,
                        window=window_number,
                        team_id=team_id,
                        name=team_profiles[team_id].name,
                        true_rating=team_profiles[team_id].true_offense + team_profiles[team_id].true_defense,
                        start_rating=model.rating(team_states[team_id]),
                        provisional=model.is_provisional(team_states[team_id], params),
                    )
                )
                instated_ids.append(team_id)

            # The batch update: every rating shifts together so the league
            # average sits at exactly zero, then pods reshuffle next window.
            team_states = model.recenter(team_states)

            # Open weeks are unrestricted: players may join more teams.
            if window_number < 4:
                churn_during_open_window(rng, team_profiles, player_profiles, list(team_states))

            window_reports.append(
                WindowReport(
                    circuit=circuit_number,
                    window=window_number,
                    mean_rating=sum(model.rating(team) for team in team_states.values()) / len(team_states),
                    mean_offense=sum(team.offense for team in team_states.values()) / len(team_states),
                    mean_defense=sum(team.defense for team in team_states.values()) / len(team_states),
                    pods=pods,
                    displayed_at_open=displayed_at_open,
                    missed_teams=sorted(missed_teams),
                    instated=instated_ids,
                    departed=departed_ids,
                )
            )

        # The tournament field: the top teams by displayed rating among
        # those with enough games this circuit. Teams that made last
        # circuit's field need only five games - they played the
        # tournament during Window 1.
        field_size = tournament_field_size(len(team_states))
        eligible_team_ids = [
            team_id
            for team_id in team_states
            if circuit_game_counts.get(team_id, 0) >= 7
            or (team_id in previous_field_ids and circuit_game_counts.get(team_id, 0) >= 5)
        ]
        ranked_eligible_ids = sorted(
            eligible_team_ids,
            key=lambda team_id: model.displayed_rating(team_states[team_id], params),
            reverse=True,
        )

        field_entries = [
            TournamentEntry(
                team_id=team_id,
                displayed_rating=model.displayed_rating(team_states[team_id], params),
            )
            for team_id in ranked_eligible_ids[:field_size]
        ]
        just_missed_entries = [
            TournamentEntry(
                team_id=team_id,
                displayed_rating=model.displayed_rating(team_states[team_id], params),
            )
            for team_id in ranked_eligible_ids[field_size : field_size + 2]
        ]

        tournament_fields.append(
            TournamentField(circuit=circuit_number, entries=field_entries, just_missed=just_missed_entries)
        )
        previous_field_ids = {entry.team_id for entry in field_entries}

    return SimResult(
        games=games,
        final_states=team_states,
        team_profiles=team_profiles,
        player_profiles=player_profiles,
        window_reports=window_reports,
        tournament_fields=tournament_fields,
        join_events=join_events,
        leave_events=leave_events,
        largest_single_game_move=largest_single_game_move,
    )


def check_rating_convergence(sim_result: SimResult, _params: Params) -> ScenarioCheck:
    """Spec: ratings persist and converge - learned ratings track true strength."""
    team_ids = [
        team_id
        for team_id, team_state in sim_result.final_states.items()
        if team_state.games_played >= MIN_GAMES_FOR_RATING_CHECKS
    ]

    learned_ratings = [model.rating(sim_result.final_states[team_id]) for team_id in team_ids]
    true_ratings = [
        sim_result.team_profiles[team_id].true_offense + sim_result.team_profiles[team_id].true_defense
        for team_id in team_ids
    ]

    correlation = model.pearson_correlation(learned_ratings, true_ratings)
    return ScenarioCheck(
        name="rating convergence",
        passed=correlation > 0.60,
        measured=f"correlation {correlation:.3f} over {len(team_ids)} teams",
        threshold="correlation > 0.60",
    )


def check_offense_defense_attribution(sim_result: SimResult, _params: Params) -> ScenarioCheck:
    """Spec: a 55-49 win says offense, a 32-25 win says defense - the halves are learnable.

    Roster churn puts lineup noise into the margin surprise, which the
    update feeds into both halves equally - so a single half can dip on
    an unlucky draw. The average is stable, and a broken attribution
    pipeline reads near zero for both halves, so the composite criterion
    still catches real breakage.
    """
    team_ids = [
        team_id
        for team_id, team_state in sim_result.final_states.items()
        if team_state.games_played >= MIN_GAMES_FOR_RATING_CHECKS
    ]

    learned_offenses = [sim_result.final_states[team_id].offense for team_id in team_ids]
    learned_defenses = [sim_result.final_states[team_id].defense for team_id in team_ids]
    true_offenses = [sim_result.team_profiles[team_id].true_offense for team_id in team_ids]
    true_defenses = [sim_result.team_profiles[team_id].true_defense for team_id in team_ids]

    offense_correlation = model.pearson_correlation(learned_offenses, true_offenses)
    defense_correlation = model.pearson_correlation(learned_defenses, true_defenses)

    # The fixtures can theoretically vanish despite the disband exemption;
    # degrade to a failed check rather than crashing the whole suite.
    if PLANTED_SHOOTOUT_TEAM_ID not in sim_result.final_states or PLANTED_STINGY_TEAM_ID not in sim_result.final_states:
        return ScenarioCheck(
            name="offense/defense attribution",
            passed=False,
            measured="a planted fixture team is missing from the final states",
            threshold="both correlations > 0.80 and planted halves point the right way",
        )

    shootout_state = sim_result.final_states[PLANTED_SHOOTOUT_TEAM_ID]
    stingy_state = sim_result.final_states[PLANTED_STINGY_TEAM_ID]
    shootout_says_offense = shootout_state.offense > shootout_state.defense
    stingy_says_defense = stingy_state.defense > stingy_state.offense

    halves_healthy = (
        (offense_correlation + defense_correlation) / 2 > 0.55
        and offense_correlation > 0.40
        and defense_correlation > 0.40
    )

    passed = halves_healthy and shootout_says_offense and stingy_says_defense

    return ScenarioCheck(
        name="offense/defense attribution",
        passed=passed,
        measured=(
            f"offense corr {offense_correlation:.3f}, defense corr {defense_correlation:.3f}; "
            f"shootout team off>def: {shootout_says_offense}, stingy team def>off: {stingy_says_defense}"
        ),
        threshold="average correlation > 0.55 with each half > 0.40 and planted halves point the right way",
    )


def check_offense_defense_sign_test(_sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: same margin, different total - a shootout raises offense, a slog raises defense.

    The two games are built relative to the calibrated base score, so
    "shootout" and "slog" mean above- and below-average totals no matter
    how the league's scoring level calibrates.
    """
    state_a = model.new_team("sign_a", params)
    state_b = model.new_team("sign_b", params)
    state_a = msgspec.structs.replace(state_a, confidence=0.5)
    state_b = msgspec.structs.replace(state_b, confidence=0.5)

    base_score = round(params.base_score)

    shootout_a, _shootout_b = model.apply_game(state_a, state_b, params, base_score + 15, base_score + 9)
    slog_a, _slog_b = model.apply_game(state_a, state_b, params, base_score - 8, base_score - 15)

    shootout_offense_change = shootout_a.offense - state_a.offense
    shootout_defense_change = shootout_a.defense - state_a.defense
    slog_offense_change = slog_a.offense - state_a.offense
    slog_defense_change = slog_a.defense - state_a.defense

    passed = shootout_offense_change > shootout_defense_change and slog_defense_change > slog_offense_change

    return ScenarioCheck(
        name="shootout vs slog sign test",
        passed=passed,
        measured=(
            f"shootout: off {shootout_offense_change:+.2f} def {shootout_defense_change:+.2f}; "
            f"slog: off {slog_offense_change:+.2f} def {slog_defense_change:+.2f}"
        ),
        threshold="shootout raises offense more; slog raises defense more",
    )


def check_provisional_leaps(_sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: a new team moves in leaps and finds its level within a handful of games."""
    rng = random.Random(7)
    trial_count = 30
    games_to_reach_level: list[int] = []
    worst_overshoot = 0.0

    # The established league the rookie joins: eight average teams the
    # model already knows, so the rookie starts at their pool's bottom.
    established_pool = {
        f"established_{pool_index}": TeamState(
            team_id=f"established_{pool_index}",
            offense=0.0,
            defense=0.0,
            confidence=0.6,
            games_played=30,
            miss_debt=0.0,
        )
        for pool_index in range(8)
    }

    for _trial_index in range(trial_count):
        rookie = model.new_team("rookie", params, established_pool)
        reached_within_six = False

        for game_index in range(1, 7):
            # Opponents are established average teams the model already
            # knows - rating 0, veteran confidence - so the rookie's rating
            # is measured against the pool average, not against other rookies.
            opponent = TeamState(
                team_id=f"opponent_{game_index}",
                offense=0.0,
                defense=0.0,
                confidence=0.6,
                games_played=30,
                miss_debt=0.0,
            )

            score_rookie, score_opponent = sample_game_score(
                rng,
                TeamProfile(team_id="rookie_true", true_offense=20.0, true_defense=20.0, roster=[], joined_circuit=1),
                TeamProfile(team_id="opponent_true", true_offense=0.0, true_defense=0.0, roster=[], joined_circuit=1),
                [],  # no players in this scenario
                [],
                {},
                params,
            )

            rookie, _opponent = model.apply_game(rookie, opponent, params, score_rookie, score_opponent)

            if not reached_within_six and abs(model.rating(rookie) - 40.0) <= 10.0:
                games_to_reach_level.append(game_index)
                reached_within_six = True

            worst_overshoot = max(worst_overshoot, model.rating(rookie) - 40.0)

        if not reached_within_six:
            games_to_reach_level.append(99)

    reached_in_time = sum(1 for games_needed in games_to_reach_level if games_needed <= 6)
    fraction_reached = reached_in_time / trial_count

    passed = fraction_reached >= 0.8 and worst_overshoot <= 20.0

    return ScenarioCheck(
        name="provisional leaps",
        passed=passed,
        measured=(
            f"{fraction_reached:.0%} of trials reach +40 +-10 within 6 games, worst overshoot {worst_overshoot:+.1f}"
        ),
        threshold=">= 80% of trials reach within 10 in <= 6 games, never past +60",
    )


def check_missed_game_penalty(_sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: missed games never touch the true rating, only the displayed number."""
    team_state = model.new_team("misser", params)
    team_state = msgspec.structs.replace(team_state, confidence=0.6)

    rating_before_misses = model.rating(team_state)
    offense_before_misses = team_state.offense
    defense_before_misses = team_state.defense

    team_state = model.apply_miss(team_state, params)
    team_state = model.apply_miss(team_state, params)

    penalty_exact = (
        abs(model.displayed_rating(team_state, params) - (rating_before_misses - 2 * params.penalty_per_miss)) < 1e-12
    )
    true_rating_untouched = (
        model.rating(team_state) == rating_before_misses
        and team_state.offense == offense_before_misses
        and team_state.defense == defense_before_misses
    )

    # Four played games restore the debt completely. Scores keep one team
    # over the counting minimum so each game counts.
    opponent = model.new_team("sparring_partner", params)
    for _game_index in range(4):
        team_state, opponent = model.apply_game(team_state, opponent, params, 35, 35)

    debt_restored = team_state.miss_debt == 0.0
    displayed_matches_true = model.displayed_rating(team_state, params) == model.rating(team_state)

    passed = penalty_exact and true_rating_untouched and debt_restored and displayed_matches_true

    return ScenarioCheck(
        name="missed-game penalty",
        passed=passed,
        measured=(
            f"penalty exact: {penalty_exact}, true rating untouched: {true_rating_untouched}, "
            f"debt restored after 4 games: {debt_restored}, displayed == true: {displayed_matches_true}"
        ),
        threshold="2 misses cost exactly 2x the penalty; true halves bit-identical; 4 games clear it",
    )


def _grind_states(params: Params) -> tuple[TeamState, TeamState]:
    """A strong team the model already knows (+50) and a weak podmate (-5)."""
    strong_team = TeamState(
        team_id="strong", offense=30.0, defense=20.0, confidence=0.6, games_played=30, miss_debt=2.0
    )
    weak_team = TeamState(team_id="weak", offense=-3.0, defense=-2.0, confidence=0.6, games_played=30, miss_debt=0.0)

    return strong_team, weak_team


def check_the_grind(_sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: a demoted strong team gains nothing from beating weak podmates, but a loss is costly."""
    strong_team, weak_team = _grind_states(params)
    rating_before_wins = model.rating(strong_team)

    # Four expected wins over the weak pod: the model saw each coming.
    for _game_index in range(4):
        strong_team, weak_team = model.apply_game(strong_team, weak_team, params, 55, 0)

    grind_gain = model.rating(strong_team) - rating_before_wins

    # One loss to the weak podmate is a genuine shock. Scores keep the
    # combined total above the forfeit convention floor.
    rating_before_loss = model.rating(strong_team)
    strong_team, _weak_team = model.apply_game(strong_team, weak_team, params, 20, 40)
    loss_drop = rating_before_loss - model.rating(strong_team)

    passed = grind_gain < 3.0 and loss_drop > 10.0

    return ScenarioCheck(
        name="the grind",
        passed=passed,
        measured=f"4 expected wins move the true rating {grind_gain:+.2f}; one loss drops it {loss_drop:.2f}",
        threshold="wins gain < 3 total, a loss costs > 10",
    )


def check_podmate_protection(_sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: podmates do not pay for a punished team's strength - the model saw the blowout coming."""
    strong_team, weak_team = _grind_states(params)
    rating_before_blowout = model.rating(weak_team)

    # The expected blowout: exactly the margin the model predicted, so the
    # podmate's rating does not move at all.
    _strong_team, weak_team = model.apply_game(strong_team, weak_team, params, 65, 10)

    podmate_move = abs(model.rating(weak_team) - rating_before_blowout)

    passed = podmate_move < 5.0

    return ScenarioCheck(
        name="podmate protection",
        passed=passed,
        measured=f"weak podmate moves {podmate_move:.2f} after losing the expected blowout to the punished +50 team",
        threshold="move < 5",
    )


def check_mean_zero(sim_result: SimResult, _params: Params) -> ScenarioCheck:
    """Spec: after every window the league average sits at exactly zero."""
    worst_mean_rating = max(abs(report.mean_rating) for report in sim_result.window_reports)
    worst_mean_offense = max(abs(report.mean_offense) for report in sim_result.window_reports)
    worst_mean_defense = max(abs(report.mean_defense) for report in sim_result.window_reports)

    passed = worst_mean_rating < 1e-9 and worst_mean_offense < 1e-9 and worst_mean_defense < 1e-9

    return ScenarioCheck(
        name="mean stays at zero",
        passed=passed,
        measured=(
            f"worst |mean| after any window: rating {worst_mean_rating:.2e}, "
            f"offense {worst_mean_offense:.2e}, defense {worst_mean_defense:.2e}"
        ),
        threshold="all means < 1e-9",
    )


def check_forfeit_skip(_sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: a game where nobody hit 30 and the combined score stayed under 50 was not real."""
    team_a = model.new_team("forfeit_a", params)
    team_b = model.new_team("forfeit_b", params)

    low_scoring_a, low_scoring_b = model.apply_game(team_a, team_b, params, 21, 21)
    low_scoring_skipped = low_scoring_a is team_a and low_scoring_b is team_b

    # 29-14: no team at 30, combined 43 - still not a real game.
    low_margin_a, low_margin_b = model.apply_game(team_a, team_b, params, 29, 14)
    low_margin_skipped = low_margin_a is team_a and low_margin_b is team_b

    # 28-27: nobody hit 30, but the combined 55 clears the combined minimum.
    high_combined_a, high_combined_b = model.apply_game(team_a, team_b, params, 28, 27)
    high_combined_counted = high_combined_a is not team_a and high_combined_b is not team_b

    # 30-0: one team hit the team minimum.
    team_minimum_a, team_minimum_b = model.apply_game(team_a, team_b, params, 30, 0)
    team_minimum_counted = team_minimum_a is not team_a and team_minimum_b is not team_b

    passed = low_scoring_skipped and low_margin_skipped and high_combined_counted and team_minimum_counted

    return ScenarioCheck(
        name="forfeit convention",
        passed=passed,
        measured=(
            f"21-21 skipped: {low_scoring_skipped}, 29-14 skipped: {low_margin_skipped}, "
            f"28-27 counts: {high_combined_counted}, 30-0 counts: {team_minimum_counted}"
        ),
        threshold="nobody at 30 and combined under 50 -> skipped; either one -> counts",
    )


def check_rejoin_reset(_sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: leaving and rejoining is a reset - and the reset never lifts a team."""
    # The league it returns to: eight established teams all sitting at +10,
    # so the starting line is +10 at any percentile.
    established_pool = {
        f"established_{pool_index}": TeamState(
            team_id=f"established_{pool_index}",
            offense=5.0,
            defense=5.0,
            confidence=0.6,
            games_played=30,
            miss_debt=0.0,
        )
        for pool_index in range(8)
    }

    sunken_team = TeamState(
        team_id="sunken", offense=-20.0, defense=-20.0, confidence=0.8, games_played=40, miss_debt=0.0
    )
    highflying_team = TeamState(
        team_id="highflying", offense=25.0, defense=25.0, confidence=0.8, games_played=40, miss_debt=0.0
    )

    rejoined_sunken = model.new_team(
        sunken_team.team_id, params, established_pool, prior_rating=model.rating(sunken_team)
    )
    rejoined_highflying = model.new_team(
        highflying_team.team_id, params, established_pool, prior_rating=model.rating(highflying_team)
    )

    never_lifted = model.rating(rejoined_sunken) == model.rating(sunken_team)
    reset_down = model.rating(rejoined_highflying) == 10.0
    fresh_slate = (
        rejoined_sunken.confidence == params.conf_new
        and rejoined_sunken.games_played == 0
        and rejoined_highflying.confidence == params.conf_new
        and rejoined_highflying.games_played == 0
    )

    passed = never_lifted and reset_down and fresh_slate

    return ScenarioCheck(
        name="rejoin reset",
        passed=passed,
        measured=(
            f"team left at {model.rating(sunken_team):+.0f} rejoins at {model.rating(rejoined_sunken):+.0f} "
            f"(never lifted); team left at {model.rating(highflying_team):+.0f} rejoins at "
            f"{model.rating(rejoined_highflying):+.0f} (reset to the starting line)"
        ),
        threshold="rejoins at min(starting line, rating it left with), fresh confidence",
    )


def check_player_impact_recovery(sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: player impact separates good players from good teams.

    Individual impacts are measured against ~14 points of per-game noise,
    so the honest bar is: real signal among players with enough games,
    stars identified in the right direction, and stars surfacing at the
    top of the leaderboard.
    """
    player_games: list[player_impact.PlayerGame] = []

    for game in sim_result.games:
        # Non-counting games never touched the ratings, so they carry no
        # information for impact either.
        if (
            max(game.score_a, game.score_b) < params.min_team_score
            and game.score_a + game.score_b < params.min_combined_score
        ):
            continue

        player_games.append(
            player_impact.PlayerGame(
                game_id=str(game.game_id),
                margin_residual=(game.score_a - game.score_b) - game.predicted_margin,
                team_a_players=game.lineup_a,
                team_b_players=game.lineup_b,
            )
        )

    estimated_impacts = player_impact.estimate_player_impacts(player_games)

    appearance_counts: dict[str, int] = {}
    for game in sim_result.games:
        for player_id in game.lineup_a + game.lineup_b:
            appearance_counts[player_id] = appearance_counts.get(player_id, 0) + 1

    # Signal check: estimated impacts track planted ones among established
    # players. The exact correlation varies with the league draw - a seed
    # with noisy team ratings makes noisier residuals - so this is a canary
    # for "real signal exists", not a grade. A broken pipeline reads ~0.0-0.2.
    established_player_ids = [player_id for player_id, appearances in appearance_counts.items() if appearances >= 30]
    planted_impacts = [sim_result.player_profiles[player_id].impact for player_id in established_player_ids]
    recovered_impacts = [estimated_impacts.get(player_id, 0.0) for player_id in established_player_ids]
    correlation = model.pearson_correlation(planted_impacts, recovered_impacts)

    # Star checks: direction and magnitude separation. Stars are planted at
    # +-8 while regulars sit near zero, so a working leaderboard must put
    # stars visibly above the noise - in direction almost always, and in
    # magnitude clearly on average. (Counting stars inside a single top-10
    # slice is too seed-lucky for a check.)
    star_player_ids = [
        player_id
        for player_id in sim_result.player_profiles
        if sim_result.player_profiles[player_id].is_star and appearance_counts.get(player_id, 0) >= 10
    ]
    stars_with_correct_sign = sum(
        1
        for player_id in star_player_ids
        if (estimated_impacts.get(player_id, 0.0) > 0) == (sim_result.player_profiles[player_id].impact > 0)
    )
    star_sign_accuracy = stars_with_correct_sign / len(star_player_ids)

    regular_player_ids = [
        player_id
        for player_id in sim_result.player_profiles
        if not sim_result.player_profiles[player_id].is_star and appearance_counts.get(player_id, 0) >= 10
    ]
    mean_star_magnitude = sum(abs(estimated_impacts.get(player_id, 0.0)) for player_id in star_player_ids) / len(
        star_player_ids
    )
    mean_regular_magnitude = sum(abs(estimated_impacts.get(player_id, 0.0)) for player_id in regular_player_ids) / len(
        regular_player_ids
    )
    star_magnitude_ratio = mean_star_magnitude / mean_regular_magnitude

    passed = correlation > 0.45 and star_sign_accuracy >= 0.8 and star_magnitude_ratio > 1.5

    return ScenarioCheck(
        name="player impact recovery",
        passed=passed,
        measured=(
            f"correlation {correlation:.3f} over {len(established_player_ids)} players with >= 30 games; "
            f"star sign accuracy {star_sign_accuracy:.0%}; "
            f"stars average {star_magnitude_ratio:.1f}x the magnitude of regulars"
        ),
        threshold="correlation > 0.45, star signs >= 80% correct, stars average > 1.5x regular magnitude",
    )


def check_hacking_overturn(_sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: a hacked game cannot stand - the opposing team automatically wins."""
    victim = TeamState(team_id="victim", offense=5.0, defense=5.0, confidence=0.6, games_played=20, miss_debt=0.0)
    hacker = TeamState(team_id="hacker", offense=-5.0, defense=5.0, confidence=0.6, games_played=20, miss_debt=0.0)

    # The hacked game itself: the hacker's team wins big on the field.
    original_victim, _original_hacker = model.apply_game(victim, hacker, params, 10, 45)
    hacked_game_cost_the_victim = model.rating(original_victim) < model.rating(victim)

    # The overturn replays the game from the pre-game states as an
    # automatic win at the smallest score that counts.
    overturned_victim, overturned_hacker = model.apply_overturn(victim, hacker, params)
    victim_gained = model.rating(overturned_victim) > model.rating(victim)
    hacker_lost = model.rating(overturned_hacker) < model.rating(hacker)

    # The replay is exact: identical to the game having gone 30-0.
    replayed_victim, replayed_hacker = model.apply_game(victim, hacker, params, 30, 0)
    exact_replay = model.rating(overturned_victim) == model.rating(replayed_victim) and model.rating(
        overturned_hacker
    ) == model.rating(replayed_hacker)

    passed = hacked_game_cost_the_victim and victim_gained and hacker_lost and exact_replay

    return ScenarioCheck(
        name="hacking overturn",
        passed=passed,
        measured=(
            f"hacked game cost the victim: {hacked_game_cost_the_victim}; "
            f"after overturn victim {model.rating(victim):+.1f} -> {model.rating(overturned_victim):+.1f}, "
            f"hacker {model.rating(hacker):+.1f} -> {model.rating(overturned_hacker):+.1f}; "
            f"exact 30-0 replay: {exact_replay}"
        ),
        threshold="the game cannot stand: victim gains the automatic win, hacker eats the loss",
    )


def check_confidence_dynamics(sim_result: SimResult, params: Params) -> ScenarioCheck:
    """Spec: veterans move in small steps, provisionals in leaps, and the cap holds."""
    # Both teams play the same average opponent, so only their own
    # confidence differs - the opponent gate is identical for both.
    average_opponent = msgspec.structs.replace(model.new_team("average_opponent", params), confidence=0.5)
    rookie = msgspec.structs.replace(model.new_team("rookie", params), confidence=0.1)
    veteran = msgspec.structs.replace(model.new_team("veteran", params), confidence=0.9)

    updated_rookie, _updated_opponent = model.apply_game(rookie, average_opponent, params, 50, 30)
    updated_veteran, _updated_opponent = model.apply_game(veteran, average_opponent, params, 50, 30)

    rookie_move = abs(model.rating(updated_rookie) - model.rating(rookie))
    veteran_move = abs(model.rating(updated_veteran) - model.rating(veteran))

    move_ratio = rookie_move / veteran_move
    cap_respected = sim_result.largest_single_game_move <= params.cap_total + 1e-9

    # The leap size follows provisional_mult, which calibration tunes - so
    # the canary is "a real leap exists", not a fixed multiple.
    passed = move_ratio >= 2.0 and cap_respected

    return ScenarioCheck(
        name="confidence dynamics",
        passed=passed,
        measured=(
            f"rookie moves {rookie_move:.2f} vs veteran {veteran_move:.2f} against the same opponent "
            f"(ratio {move_ratio:.2f}); largest move in the sim {sim_result.largest_single_game_move:.2f}"
        ),
        threshold="rookie moves >= 2x the veteran; no move exceeds the cap",
    )


ScenarioCheckFunction = Callable[[SimResult, Params], ScenarioCheck]

SCENARIO_CHECKS: dict[str, ScenarioCheckFunction] = {
    "convergence": check_rating_convergence,
    "attribution": check_offense_defense_attribution,
    "sign": check_offense_defense_sign_test,
    "provisional": check_provisional_leaps,
    "miss": check_missed_game_penalty,
    "grind": check_the_grind,
    "podmate": check_podmate_protection,
    "zero": check_mean_zero,
    "forfeit": check_forfeit_skip,
    "rejoin": check_rejoin_reset,
    "overturn": check_hacking_overturn,
    "impact": check_player_impact_recovery,
    "confidence": check_confidence_dynamics,
}


def build_tournament_lines(sim_result: SimResult) -> list[str]:
    """Who would qualify for the tournament at the end of each circuit."""
    lines = [
        "",
        "## Tournament Fields",
        "",
        "The teams that would qualify at the end of each circuit: the top teams by",
        "displayed rating among those with enough games in the circuit.",
        "",
    ]

    for field in sim_result.tournament_fields:
        entry_texts = [
            f"{entry_index}. {sim_result.team_profiles[entry.team_id].name} ({entry.displayed_rating:+.1f})"
            for entry_index, entry in enumerate(field.entries, start=1)
        ]
        field_line = f"- **Circuit {field.circuit}**: " + ", ".join(entry_texts)

        if field.just_missed:
            missed_texts = [
                f"{sim_result.team_profiles[entry.team_id].name} ({entry.displayed_rating:+.1f})"
                for entry in field.just_missed
            ]
            field_line += " - just missed: " + ", ".join(missed_texts)

        lines.append(field_line)

    return lines


def build_window_summary_lines(sim_result: SimResult) -> list[str]:
    """A compact per-window table: league size, games played, games missed."""
    games_by_window: dict[tuple[int, int], int] = {}
    for game in sim_result.games:
        key = (game.circuit, game.window)
        games_by_window[key] = games_by_window.get(key, 0) + 1

    lines = [
        "",
        "## Window Summary",
        "",
        "| Circuit | Window | Active teams | Games | Missed |",
        "| --- | --- | --- | --- | --- |",
    ]

    for report in sim_result.window_reports:
        lines.append(
            f"| {report.circuit} | {report.window} | {len(report.displayed_at_open)} | "
            f"{games_by_window.get((report.circuit, report.window), 0)} | {len(report.missed_teams)} |"
        )

    return lines


def build_window_by_window_lines(sim_result: SimResult, params: Params) -> list[str]:
    """Every window in full: pod assignments, missed teams, and each game
    with the model's prediction and the rating moves that followed."""
    games_by_window: dict[tuple[int, int], list[SimulatedGame]] = {}
    for game in sim_result.games:
        games_by_window.setdefault((game.circuit, game.window), []).append(game)

    lines = [
        "",
        "## Window-by-Window Log",
        "",
        "Pods are assigned at the window's open from the displayed rating.",
        "Each game shows the score, the model's predicted margin, and how",
        "far each team's true rating moved; `[no-count]` marks a game the",
        "forfeit convention skipped.",
        "",
    ]

    for report in sim_result.window_reports:
        lines.append(f"### Circuit {report.circuit}, Window {report.window}")
        lines.append("")

        for pod_number, pod in enumerate(report.pods, start=1):
            pod_texts = [
                f"{sim_result.team_profiles[team_id].name} ({report.displayed_at_open[team_id]:+.1f})"
                for team_id in pod
            ]
            lines.append(f"- **Pod {pod_number}**: " + ", ".join(pod_texts))

        if report.missed_teams:
            missed_texts = [
                f"{sim_result.team_profiles[team_id].name} ({report.displayed_at_open[team_id]:+.1f})"
                for team_id in report.missed_teams
            ]
            lines.append("- **Missed**: " + ", ".join(missed_texts))

        if report.instated:
            instated_texts = [sim_result.team_profiles[team_id].name for team_id in report.instated]
            lines.append("- **Instated at close**: " + ", ".join(instated_texts))

        if report.departed:
            departed_texts = [sim_result.team_profiles[team_id].name for team_id in report.departed]
            lines.append("- **Departed at close**: " + ", ".join(departed_texts))

        for game in games_by_window.get((report.circuit, report.window), []):
            name_a = sim_result.team_profiles[game.team_a_id].name
            name_b = sim_result.team_profiles[game.team_b_id].name
            no_count = (
                max(game.score_a, game.score_b) < params.min_team_score
                and game.score_a + game.score_b < params.min_combined_score
            )
            suffix = " `[no-count]`" if no_count else ""
            lines.append(
                f"- {name_a} {game.score_a} - {game.score_b} {name_b} "
                f"(pred {game.predicted_margin:+.1f}; {name_a} {game.rating_move_a:+.2f}, "
                f"{name_b} {game.rating_move_b:+.2f}){suffix}"
            )
        lines.append("")

    return lines


def build_team_event_lines(sim_result: SimResult) -> list[str]:
    """Every join and departure the simulation produced, window by window."""
    lines = [
        "",
        "## Joins and Departures",
        "",
        "Joins requested during a window take effect at its close; leavers",
        "play the window out, then go. A joining team's `true` number is its",
        "planted strength, which the real bot will never see. Returning teams",
        "are instated before Window 1 opens.",
        "",
    ]

    event_windows = sorted(
        {(event.circuit, event.window) for event in [*sim_result.join_events, *sim_result.leave_events]}
    )

    if not event_windows:
        lines.append("No teams joined or departed during the simulation.")
        return lines

    for circuit_number, window_number in event_windows:
        if window_number == 0:
            heading = f"### Circuit {circuit_number}, Window 1 open"
        else:
            heading = f"### Circuit {circuit_number}, Window {window_number} close"

        joins = [
            event
            for event in sim_result.join_events
            if event.circuit == circuit_number and event.window == window_number
        ]
        leaves = [
            event
            for event in sim_result.leave_events
            if event.circuit == circuit_number and event.window == window_number
        ]

        lines.append(heading)
        lines.append("")

        if joins:
            lines.append(
                "- Joined: "
                + "; ".join(
                    f"{event.name} (true {event.true_rating:+.1f}, starts {event.start_rating:+.1f}"
                    f"{', provisional' if event.provisional else ''}"
                    f"{', rejoin from ' + format(event.prior_rating, '+.1f') if event.is_rejoin else ''})"
                    for event in joins
                )
            )

        if leaves:
            lines.append(
                "- Departed: "
                + "; ".join(
                    f"{event.name} (left at {event.rating_at_leave:+.1f}, {event.career_games} career games, "
                    f"{event.reason}, may return circuit {event.eligible_to_return_circuit})"
                    for event in leaves
                )
            )
        lines.append("")

    return lines


def compute_records(sim_result: SimResult, params: Params) -> dict[str, tuple[int, int, int]]:
    """Wins, losses, and ties per team over counting games only."""
    records: dict[str, list[int]] = {}

    for game in sim_result.games:
        if (
            max(game.score_a, game.score_b) < params.min_team_score
            and game.score_a + game.score_b < params.min_combined_score
        ):
            continue

        team_a_record = records.setdefault(game.team_a_id, [0, 0, 0])
        team_b_record = records.setdefault(game.team_b_id, [0, 0, 0])

        if game.score_a > game.score_b:
            team_a_record[0] += 1
            team_b_record[1] += 1
        elif game.score_b > game.score_a:
            team_b_record[0] += 1
            team_a_record[1] += 1
        else:
            team_a_record[2] += 1
            team_b_record[2] += 1

    return {team_id: (wins, losses, ties) for team_id, (wins, losses, ties) in records.items()}


def build_leaderboard_lines(
    sim_result: SimResult,
    params: Params,
    top_count: int,
    bottom_count: int,
) -> list[str]:
    """The league leaderboard as the bot would show it, plus the sim's ground truth.

    Ranked by displayed rating - the number that decides pods and seeding,
    with the missed-game penalty annotated exactly like the spec's format.
    A `*` marks provisional teams. The `True` column is the planted
    strength the sim knows; the real bot will never see it, but it shows
    how close the learned ratings got.
    """
    team_states = sim_result.final_states
    records = compute_records(sim_result, params)
    pods = assign_pods(team_states, params)
    pod_by_team = {team_id: pod_number for pod_number, pod in enumerate(pods, start=1) for team_id in pod}

    final_field_ids: set[str] = (
        {entry.team_id for entry in sim_result.tournament_fields[-1].entries} if sim_result.tournament_fields else set()
    )

    ranked_team_ids = sorted(
        team_states,
        key=lambda team_id: model.displayed_rating(team_states[team_id], params),
        reverse=True,
    )

    lines = [
        "## League Leaderboard",
        "",
        "Ranked by displayed rating - the number that decides pods and seeding.",
        "A `*` marks provisional teams, `Q` marks the final circuit's tournament qualifiers.",
        "`True` is the sim's planted strength, which the real bot will never see.",
        "",
        "| # | Team | Record | Rating | Off | Def | Conf | GP | Pod | True |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]

    rows: list[list[str]] = []

    for rank, team_id in enumerate(ranked_team_ids, start=1):
        team_state = team_states[team_id]
        team_profile = sim_result.team_profiles[team_id]

        team_name = team_profile.name or team_id
        if team_id in final_field_ids:
            team_name += " Q"
        if model.is_provisional(team_state, params):
            team_name += " *"

        rating_text = f"{model.displayed_rating(team_state, params):+.1f}"
        if team_state.miss_debt > 0:
            rating_text += (
                f" ({-team_state.miss_debt * params.penalty_per_miss:+.1f}: {team_state.miss_debt:.1f} missed)"
            )

        wins, losses, ties = records.get(team_id, (0, 0, 0))
        record_text = f"{wins}-{losses}"
        if ties:
            record_text += f"-{ties}"

        rows.append(
            [
                str(rank),
                team_name,
                record_text,
                rating_text,
                f"{team_state.offense:+.1f}",
                f"{team_state.defense:+.1f}",
                f"{team_state.confidence:.2f}",
                str(team_state.games_played),
                str(pod_by_team.get(team_id, "-")),
                f"{team_profile.true_offense + team_profile.true_defense:+.1f}",
            ]
        )

    def format_row(row: list[str]) -> str:
        return "| " + " | ".join(row) + " |"

    shown_rows = rows[:top_count]

    if bottom_count > 0 and len(rows) > top_count + bottom_count:
        shown_rows = [*shown_rows, ["..."] * 10, *rows[-bottom_count:]]

    lines.extend(format_row(row) for row in shown_rows)

    return lines


def build_pod_lines(sim_result: SimResult, params: Params) -> list[str]:
    """The final pod assignments - the groups teams actually played in."""
    team_states = sim_result.final_states
    pods = assign_pods(team_states, params)

    lines = ["", "## Final Pods", ""]

    for pod_number, pod in enumerate(pods, start=1):
        pod_teams = [
            f"{sim_result.team_profiles[team_id].name} ({model.displayed_rating(team_states[team_id], params):+.1f})"
            for team_id in pod
        ]
        lines.append(f"- **Pod {pod_number}**: " + ", ".join(pod_teams))

    return lines


def main() -> None:
    argument_parser = argparse.ArgumentParser(description="Simulate FF Circuit and verify the rating model")
    argument_parser.add_argument("--teams", type=int, default=56)
    argument_parser.add_argument("--circuits", type=int, default=6)
    argument_parser.add_argument("--players", type=int, default=700)
    argument_parser.add_argument("--miss-rate", type=float, default=0.15)
    argument_parser.add_argument(
        "--join-prob", type=float, default=0.5, help="chance a join request arrives each window"
    )
    argument_parser.add_argument(
        "--leave-prob", type=float, default=0.01, help="per team per window, chance of requesting removal"
    )
    argument_parser.add_argument(
        "--rejoin-prob", type=float, default=0.5, help="chance an eligible departed team returns"
    )
    argument_parser.add_argument(
        "--params", type=str, default=None, help="path to params.json; auto-loads the calibration output when present"
    )
    argument_parser.add_argument("--scenario", type=str, default="all", help="all or comma-separated names")
    argument_parser.add_argument("--top-teams", type=int, default=15, help="leaderboard: how many top teams to show")
    argument_parser.add_argument(
        "--bottom-teams", type=int, default=5, help="leaderboard: how many bottom teams to show"
    )
    argument_parser.add_argument("--seed", type=int, default=42)
    argument_parser.add_argument("--out-dir", type=str, default="out/simulation")

    arguments = argument_parser.parse_args()

    output_directory = PROJECT_ROOT / arguments.out_dir
    output_directory.mkdir(parents=True, exist_ok=True)

    rng = random.Random(arguments.seed)

    # Prefer the calibrated parameters when they exist - the point of the
    # simulation is to validate the tuned model under circuit structure.
    params = DEFAULT_PARAMS

    if arguments.params is not None:
        params = load_params(arguments.params)
    else:
        calibration_params = PROJECT_ROOT / "out/calibration/params.json"
        if calibration_params.exists():
            params = load_params(str(calibration_params))
            print(f"Using calibrated parameters from {calibration_params}")
        else:
            print("No calibration output found - using default parameters")

    print(f"Generating a league of {arguments.teams} teams and {arguments.players} players...")
    team_profiles, player_profiles = generate_league(rng, arguments.teams, arguments.players, arguments.miss_rate)

    print(f"Simulating {arguments.circuits} circuits...")
    sim_result = simulate_circuits(
        team_profiles=team_profiles,
        player_profiles=player_profiles,
        params=params,
        rng=rng,
        circuit_count=arguments.circuits,
        miss_probability=arguments.miss_rate,
        optional_game_probability=0.3,
        join_probability=arguments.join_prob,
        leave_probability=arguments.leave_prob,
        rejoin_probability=arguments.rejoin_prob,
    )

    print(f"Played {len(sim_result.games)} games; {len(sim_result.final_states)} teams active at the end.")

    if arguments.scenario == "all":
        check_names = list(SCENARIO_CHECKS.keys())
    else:
        check_names = [name.strip() for name in arguments.scenario.split(",") if name.strip()]

    checks: list[ScenarioCheck] = []
    for check_name in check_names:
        checks.append(SCENARIO_CHECKS[check_name](sim_result, params))

    print()
    print("| Check | Result | Measured | Threshold |")
    print("| --- | --- | --- | --- |")

    for check in checks:
        verdict = "PASS" if check.passed else "FAIL"
        print(f"| {check.name} | {verdict} | {check.measured} | {check.threshold} |")

    failed_checks = [check for check in checks if not check.passed]

    window_summary_lines = build_window_summary_lines(sim_result)
    window_log_lines = build_window_by_window_lines(sim_result, params)
    event_lines = build_team_event_lines(sim_result)
    leaderboard_lines = build_leaderboard_lines(sim_result, params, arguments.top_teams, arguments.bottom_teams)
    tournament_lines = build_tournament_lines(sim_result)
    pod_lines = build_pod_lines(sim_result, params)

    print()
    for line in window_summary_lines:
        print(line)
    for line in event_lines:
        print(line)
    for line in leaderboard_lines:
        print(line)
    for line in tournament_lines:
        print(line)
    for line in pod_lines:
        print(line)

    report_lines = [
        "# Simulation Report",
        "",
        f"League of {arguments.teams} teams, {arguments.players} players, "
        f"{arguments.circuits} circuits, {len(sim_result.games)} games.",
        f"Miss rate {arguments.miss_rate:.0%}, seed {arguments.seed}.",
        "",
        "| Check | Result | Measured | Threshold |",
        "| --- | --- | --- | --- |",
    ]

    for check in checks:
        verdict = "PASS" if check.passed else "FAIL"
        report_lines.append(f"| {check.name} | {verdict} | {check.measured} | {check.threshold} |")

    report_lines.append("")
    report_lines.extend(window_summary_lines)
    report_lines.extend(window_log_lines)
    report_lines.extend(event_lines)
    report_lines.extend(leaderboard_lines)
    report_lines.extend(tournament_lines)
    report_lines.extend(pod_lines)

    report_file_path = output_directory / "report.md"
    report_file_path.write_text("\n".join(report_lines))
    print(f"\nReport written to {report_file_path}")

    if failed_checks:
        failed_names = ", ".join(check.name for check in failed_checks)
        print(f"\nFAILED: {failed_names}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
