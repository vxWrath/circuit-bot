-- Current membership: the latest membership event per player per team.
-- The owner's creation promote attaches them to `to_team_id`; an accepted
-- offer attaches a player; a promotion or demotion updates the role on it;
-- a release or demand detaches them from `from_team_id`.
CREATE VIEW roster AS
WITH events AS (
    SELECT player, to_team_id AS team_id, role, action, at, id
    FROM transaction_log
    WHERE action = 'offer'
      AND to_team_id IS NOT NULL
      AND details->>'status' = 'accepted'

    UNION ALL

    SELECT player, to_team_id AS team_id, role, action, at, id
    FROM transaction_log
    WHERE action IN ('promote', 'demote') AND to_team_id IS NOT NULL

    UNION ALL

    SELECT player, from_team_id AS team_id, role, action, at, id
    FROM transaction_log
    WHERE action IN ('release', 'demand') AND from_team_id IS NOT NULL
),
latest AS (
    SELECT DISTINCT ON (team_id, player)
        team_id, player, role, action, at, id
    FROM events
    ORDER BY team_id, player, at DESC, id DESC
)
SELECT team_id, player, role
FROM latest
WHERE action NOT IN ('release', 'demand');
