-- Who owns a team, over time.  The row with ended_at IS NULL is the current owner.
CREATE TABLE team_ownership (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id      bigint NOT NULL REFERENCES teams(id),
    player       bigint NOT NULL REFERENCES players(snowflake),
    appointed_at timestamptz NOT NULL DEFAULT now(),
    ended_at     timestamptz,
    end_reason   text CHECK (end_reason IN ('transferred', 'removed'))
);

-- One current owner per team.
CREATE UNIQUE INDEX team_ownership_one_owner_per_team
    ON team_ownership (team_id)
    WHERE ended_at IS NULL;

-- One live ownership per player - nobody owns two teams at once.
CREATE UNIQUE INDEX team_ownership_one_live_per_player
    ON team_ownership (player)
    WHERE ended_at IS NULL;

-- Owner history: the appointments shown on a profile, and the lineage check.
CREATE INDEX team_ownership_player_idx
    ON team_ownership (player, appointed_at DESC, id DESC);
