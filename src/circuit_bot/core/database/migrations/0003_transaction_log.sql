CREATE TABLE transaction_log (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    player       bigint NOT NULL REFERENCES players(snowflake),
    from_team_id bigint REFERENCES teams(id),
    to_team_id   bigint REFERENCES teams(id),
    action       text NOT NULL CHECK (action IN (
                     'offer', 'release', 'demand',
                     'promote', 'demote'
                 )),
    role         text CHECK (role IN ('owner', 'coach')),
    actor        bigint REFERENCES players(snowflake),
    details      jsonb,
    at           timestamptz NOT NULL DEFAULT now(),

    CHECK (
        (action IN ('offer', 'promote', 'demote')
            AND to_team_id IS NOT NULL AND from_team_id IS NULL)
        OR (action IN ('release', 'demand')
            AND from_team_id IS NOT NULL AND to_team_id IS NULL)
    )
);

-- `role` is the role held after promote and demote; release and demand
-- record the role held before leaving.  NULL is a plain player.
-- `details` carries the offer id and status, and the end reason.

-- The team a player is offered to or promoted on.
CREATE INDEX transaction_log_to_team_player_idx
    ON transaction_log (to_team_id, player, at DESC, id DESC)
    WHERE to_team_id IS NOT NULL;

-- The team a player leaves or demands out of.
CREATE INDEX transaction_log_from_team_player_idx
    ON transaction_log (from_team_id, player, at DESC, id DESC)
    WHERE from_team_id IS NOT NULL;

-- Player history: the transactions shown on a profile, and the lineage check.
CREATE INDEX transaction_log_player_idx
    ON transaction_log (player, at DESC, id DESC);
