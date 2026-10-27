CREATE TABLE transaction_log (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    team_id bigint NOT NULL REFERENCES teams(id),
    player  bigint NOT NULL REFERENCES players(snowflake),
    role    text CHECK (role IN ('owner', 'coach')),
    action  text NOT NULL CHECK (action IN ('join', 'promote', 'demote', 'leave')),
    at      timestamptz NOT NULL DEFAULT now()
);
