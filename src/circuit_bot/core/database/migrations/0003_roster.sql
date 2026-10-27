CREATE TABLE roster (
    team_id   bigint NOT NULL REFERENCES teams(id),
    player    bigint NOT NULL REFERENCES players(snowflake),
    role      text CHECK (role IN ('owner', 'coach')),
    joined_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, player)
);

-- One owner per team, one team per owner (as owner).
CREATE UNIQUE INDEX roster_one_owner_per_team ON roster (team_id) WHERE role = 'owner';
CREATE UNIQUE INDEX roster_one_team_per_owner ON roster (player) WHERE role = 'owner';
