CREATE TABLE players (
    snowflake            bigint PRIMARY KEY,
    roblox_id            bigint UNIQUE,
    -- The last time the startup sweep asked Bloxlink about this player.  NULL
    -- means the sweep hasn't, so it asks; a marked player is skipped on later
    -- startups.
    verified_checked_at  timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
