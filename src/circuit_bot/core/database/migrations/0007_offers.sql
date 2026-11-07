-- An offer is a team's invitation to a player.  It expires and is accepted
-- or declined; a player may hold offers from several teams.
CREATE TABLE offers (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id      bigint NOT NULL REFERENCES teams(id),
    player       bigint NOT NULL REFERENCES players(snowflake),
    offered_by   bigint NOT NULL REFERENCES players(snowflake),
    status       text NOT NULL DEFAULT 'pending'
                 CHECK (status IN ('pending', 'accepted', 'declined', 'expired')),
    terms        jsonb NOT NULL DEFAULT '{}'::jsonb,
    expires_at   timestamptz NOT NULL,
    responded_at timestamptz,
    created_at   timestamptz NOT NULL DEFAULT now(),
    updated_at   timestamptz NOT NULL DEFAULT now()
);

-- A team has one pending offer to a player; a player may hold pending
-- offers from several teams.
CREATE UNIQUE INDEX offers_one_pending_per_team_player
    ON offers (team_id, player)
    WHERE status = 'pending';

CREATE INDEX offers_player_pending_idx
    ON offers (player)
    WHERE status = 'pending';
