-- Current membership: the latest membership event per player per team.
-- The owner comes from the team_ownership row still open; an accepted
-- offer attaches a player, a promotion or demotion updates a coach's role,
-- and a release or demand detaches them.
CREATE VIEW roster AS
WITH events AS (
    SELECT player, team_id, 'owner'::text AS role, 'appoint' AS action, appointed_at AS at, id
    FROM team_ownership
    WHERE ended_at IS NULL

    UNION ALL

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
