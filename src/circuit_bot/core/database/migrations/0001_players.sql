CREATE TABLE players (
    snowflake  bigint PRIMARY KEY,
    roblox_id  bigint UNIQUE,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
